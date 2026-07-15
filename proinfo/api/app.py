"""FastAPI application: REST + WebSocket + dashboard host.

Endpoints
---------
GET  /                       dashboard (static)
GET  /api/health             status + version
GET  /api/codecs             available protocol codecs
GET  /api/devices            per-device summaries
GET  /api/devices/{id}/records
GET  /api/packets            recent records (all devices)
GET  /api/snapshot           devices + recent (one shot)
GET  /api/jobs               replay/listen jobs
POST /api/replay             upload a log file and replay it
POST /api/replay-path        replay a file already on the server/local disk
POST /api/listen             start a live TCP listener
POST /api/stop/{job_id}      stop a running job
DELETE /api/data             clear the live store
WS   /ws                     real-time record stream (snapshot then live)
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import sys
import time
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from logging.handlers import RotatingFileHandler
from pydantic import BaseModel

from .. import __version__
from ..core import registry
from ..core.interfaces import Source
from ..core.models import RawFrame
from ..core.pipeline import Pipeline
from ..qc import engine as qc_engine
from ..qc.session import UNKNOWN_DEVICE_IDS, QcCaptureSink
from ..storage.db import DATA_DIR
from ..storage.live import config, db, hub, packet_log, store

log = logging.getLogger("proinfo.api")

# Static UI. When frozen (PyInstaller), it's bundled under _MEIPASS/proinfo/web.
if getattr(sys, "frozen", False):
    WEB_DIR = Path(getattr(sys, "_MEIPASS", ".")) / "proinfo" / "web"
else:
    WEB_DIR = Path(__file__).resolve().parent.parent / "web"
UPLOAD_DIR = Path(DATA_DIR) / "uploads"
SIGNATURES_DIR = Path(DATA_DIR) / "signatures"     # local repository of signature files
SIGNATURES_DIR.mkdir(parents=True, exist_ok=True)
PROTOCOLS_DIR = Path(DATA_DIR) / "protocols"       # local repository of protocol definitions
PROTOCOLS_DIR.mkdir(parents=True, exist_ok=True)
QC_PROFILES_DIR = Path(DATA_DIR) / "qc" / "profiles"   # per-protocol QC coverage profiles (user overrides)
# Defaults ship inside the package (like the web assets); data/qc/profiles overrides them.
if getattr(sys, "frozen", False):
    QC_DEFAULTS_DIR = Path(getattr(sys, "_MEIPASS", ".")) / "proinfo" / "qc" / "profiles"
else:
    QC_DEFAULTS_DIR = Path(__file__).resolve().parent.parent / "qc" / "profiles"
_SIG_NAME_RE = re.compile(r"^[A-Za-z0-9._ -]+$")

# Two runtime logs (Diagnostics → Logs):
#   runway.log         — all activity (INFO+), small + frequently rotated (churns).
#   runway.errors.log  — WARNING+ERROR only, large + many backups (KEPT for later analysis).
LOG_FILE = Path(DATA_DIR) / "runway.log"
LOG_ERR_FILE = Path(DATA_DIR) / "runway.errors.log"


def _log_path(source: str) -> Path:
    return LOG_ERR_FILE if source == "errors" else LOG_FILE


def _setup_file_logging() -> None:
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-5s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    activity = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    activity.setFormatter(fmt); activity.setLevel(logging.INFO); activity._runway = "activity"
    errors = RotatingFileHandler(LOG_ERR_FILE, maxBytes=5_000_000, backupCount=10, encoding="utf-8")
    errors.setFormatter(fmt); errors.setLevel(logging.WARNING); errors._runway = "errors"
    for name in ("", "uvicorn", "uvicorn.error"):   # root + uvicorn so crashes are captured
        lg = logging.getLogger(name)
        have = {getattr(h, "_runway", None) for h in lg.handlers}
        if "activity" not in have:
            lg.addHandler(activity)
        if "errors" not in have:
            lg.addHandler(errors)
    logging.getLogger().setLevel(logging.INFO)


_setup_file_logging()
log.info("Runway %s — logging to %s", __version__, LOG_FILE)

# Running jobs: serializable info in _jobs, the asyncio.Task in _tasks, the live
# Pipeline in _pipelines (for live records/bytes + the source's connected peers).
_jobs: dict[str, dict] = {}
_tasks: dict[str, asyncio.Task] = {}
_pipelines: dict[str, "Pipeline"] = {}
_job_seq = 0

# Device QC sessions: each backs onto a "qc" job (above) plus a QcCaptureSink (keyed by
# qc_sessions.id) that learns the device's protocol/id and writes its capture file.
_qc_sinks: dict[int, QcCaptureSink] = {}


def _qc_reconcile_startup() -> None:
    """Reconcile persisted QC sessions against a fresh process.

    Job ids are per-process (job-1, job-2, ...): after a restart every qc_sessions row's
    job_id points at a dead job — or worse, COLLIDES with a new job that happens to reuse
    the same id, making an old tab show the new job's devices/live feed. And rows still
    marked "running" belong to the previous process; nothing is listening for them now.
    So on boot: clear every row's job_id and flip running -> stopped."""
    now = time.time()
    for row in db.qc_sessions():
        updates: dict = {}
        if row.get("job_id"):
            updates["job_id"] = ""
        if row.get("status") == "running":
            updates.update(status="stopped", ended_at=row.get("ended_at") or now)
        if updates:
            db.qc_update(row["id"], **updates)


@asynccontextmanager
async def lifespan(_: FastAPI):
    registry.load_plugins()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    _qc_reconcile_startup()
    # Fresh start: the live view begins empty each run. Devices appear only from data fed
    # this session (Parser ingest or live feeds) — we do NOT replay the persisted registry
    # into the live store, so a restart is a clean slate.
    log.info("Runway API ready (codecs: %s) — live view starts empty", sorted(registry.CODECS))
    yield
    for task in _tasks.values():
        task.cancel()


app = FastAPI(title="Runway", version=__version__, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


@app.middleware("http")
async def _revalidate_static(request, call_next):
    """Force the browser to revalidate static assets so UI edits show on a normal reload
    (no more stale cached HTML/JS/CSS). API/WS responses are left untouched."""
    response = await call_next(request)
    path = request.url.path
    if not path.startswith("/api") and not path.startswith("/ws"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.exception_handler(Exception)
async def _log_unhandled(request, exc):
    """Last-resort handler: log the full traceback to runway.log, return a clean 500."""
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500,
                        content={"ok": False, "error": "internal error — see Diagnostics → Logs"})


# --------------------------------------------------------------------------- #
# Request bodies
# --------------------------------------------------------------------------- #
class ReplayPathBody(BaseModel):
    path: str
    codec: str = "auto"
    fmt: str = "auto"
    delay: float = 0.0


class ListenBody(BaseModel):
    codec: str = "auto"
    host: str = "0.0.0.0"
    port: int = 5050


class ParseBody(BaseModel):
    hex: str
    transport: str = "tcp"          # tcp | udp


class IngestBody(BaseModel):
    hex: str
    codec: str = "teltonika"
    peer: str = "parser"            # shows as the device's source/peer on the dashboard


class _BytesSource(Source):
    """One-shot in-memory Source: yields the given bytes as a single frame.

    Lets the Parser push decoded data through the normal pipeline into the live store
    (so it appears on the Dashboard) without writing a temp file.
    """

    name = "bytes"

    def __init__(self, data: bytes, peer: str):
        self._data = data
        self._peer = peer

    async def frames(self):
        yield RawFrame(data=self._data, transport="parser", peer=self._peer, source=self.name)


class SignatureBody(BaseModel):
    name: str
    content: Any                       # JSON object, or a JSON string to be parsed


class ProfileBody(BaseModel):
    name: str
    vendor: str = ""
    model: str = ""
    transport: str = ""
    codec: str = ""
    framing: dict = {}


class ConvertParseBody(BaseModel):
    fmt: str = "canplus"               # source format of the uploaded signature
    text: str                          # raw file text (e.g. CanPlus XML)


class ConvertExportBody(BaseModel):
    fmt: str = "json"                  # "json" | "dbc"
    vehicle: dict = {}
    signals: list = []


class SendBody(BaseModel):
    peer: str                          # "ip:port" of a connected device
    mode: str = "text"                 # "text" (built via codec) | "hex" (raw bytes)
    value: str = ""


class LogStartBody(BaseModel):
    fmt: str = "txt"                   # txt | csv
    raw: str = "ascii"                 # ascii | hex
    name: str = ""                     # file name (blank = auto, timestamped)
    directory: str = ""                # folder (blank = server data/logs)


class QcStartBody(BaseModel):
    port: int
    host: str = "0.0.0.0"
    protocol_mode: str = "auto"         # "auto" | "manual"
    protocol: str = "auto"              # codec name when manual; ignored (-> "auto") otherwise
    operator: str = ""
    notes: str = ""


class QcSkipBody(BaseModel):
    device_id: str = ""                 # which device's coverage to skip (""=session-level)
    keys: list[str] = []                # coverage keys the operator marks optional


class QcFinalizeBody(BaseModel):
    device_id: str = ""                 # which device to finalize (""=session-level)
    verdict: str = ""                   # "" clears; else pass | conditional_pass | fail
    note: str = ""


# --------------------------------------------------------------------------- #
# Job plumbing
# --------------------------------------------------------------------------- #
def _default_sinks():
    return [registry.get_sink("memory_store"), registry.get_sink("ws_broadcast")]


def _activity_mode() -> str:
    """Current activity for the topbar badge (auto, not user-set):

    - ``live``  a live TCP listener / QC session is running (real devices dial in),
    - ``demo``  a scripted file replay is running,
    - ``idle``  nothing running.

    Live wins when a listener and a replay are active at once. Only jobs with a live
    asyncio task (``_tasks``) count — finished/stopped jobs linger in ``_jobs`` but are
    not activity.
    """
    kinds = {_jobs[jid].get("kind") for jid in _tasks if jid in _jobs}
    if kinds & {"listen", "qc"}:
        return "live"
    if "replay" in kinds:
        return "demo"
    return "idle"


def _new_job(kind: str, **meta) -> str:
    global _job_seq
    _job_seq += 1
    jid = f"job-{_job_seq}"
    _jobs[jid] = {"id": jid, "kind": kind, "status": "pending", **meta}
    return jid


async def _run(jid: str, pipeline: Pipeline) -> None:
    job = _jobs[jid]
    job["status"] = "running"
    await hub.publish({"type": "job", "data": _job_view(jid)})
    try:
        count = await pipeline.run()
        job.update(status="done", records=count, bytes=pipeline.bytes_in)
        _record_capture(job, count, pipeline.bytes_in)
    except asyncio.CancelledError:
        job["status"] = "stopped"
        raise
    except Exception as exc:  # noqa: BLE001
        job.update(status="error", error=str(exc))
        log.exception("job %s failed", jid)
    finally:
        job["ended"] = time.time()                        # freeze uptime once it stops
        await hub.publish({"type": "job", "data": _job_view(jid)})
        _tasks.pop(jid, None)
        _pipelines.pop(jid, None)


def _spawn(jid: str, pipeline: Pipeline) -> None:
    _pipelines[jid] = pipeline
    _tasks[jid] = asyncio.create_task(_run(jid, pipeline))


def _job_view(jid: str) -> dict:
    """Job metadata merged with live counters from its running pipeline (if any)."""
    job = dict(_jobs.get(jid, {}))
    pipe = _pipelines.get(jid)
    if pipe is not None:
        job["records"] = pipe.records_emitted
        job["bytes"] = pipe.bytes_in
        src = getattr(pipe, "source", None)
        connected = set(src.peers()) if (src is not None and hasattr(src, "peers")) else set()
        job["peers"] = sorted(connected)
        per = getattr(pipe, "per_peer", {}) or {}
        job["clients"] = [
            {"peer": p, "records": per.get(p, {}).get("records", 0),
             "bytes": per.get(p, {}).get("bytes", 0), "last": per.get(p, {}).get("last", 0),
             "connected": p in connected}
            for p in sorted(set(per) | connected) if p and p != "?"
        ]
    return job


def _record_capture(job: dict, records: int, nbytes: int) -> None:
    """Index a finished replay/listen job in the durable ``captures`` table."""
    try:
        db.insert(
            "captures",
            name=job.get("file") or f"{job['kind']}-{job['id']}",
            path=job.get("file", ""),
            fmt=job.get("fmt", ""),
            codec=job.get("codec", ""),
            records=records,
            bytes=nbytes,
            meta=json.dumps({"kind": job["kind"]}),
        )
    except Exception:
        log.exception("failed to record capture for job %s", job.get("id"))


# --------------------------------------------------------------------------- #
# REST
# --------------------------------------------------------------------------- #
@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "version": __version__,
        "codecs": len(registry.CODECS),
        "devices": len(store.devices),
        "subscribers": hub.subscribers,
        "mode": _activity_mode(),
    }


@app.get("/api/codecs")
async def codecs():
    return registry.list_codecs()


@app.get("/api/params")
async def params():
    """AVL id -> parameter name/unit list, so the Dashboard's column picker offers the same
    signal names as the Parser view."""
    from ..codecs.teltonika_ids import AVL_IDS

    return {"avl": [{"id": k, "name": n, "unit": u} for k, (n, u) in sorted(AVL_IDS.items())]}


@app.post("/api/parse")
async def parse(body: ParseBody):
    """Stateless Teltonika hex -> field-by-field breakdown (for the parser UI)."""
    from ..codecs.teltonika_explain import explain

    cleaned = "".join(ch for ch in body.hex if ch in "0123456789abcdefABCDEF")
    if len(cleaned) % 2:
        raise HTTPException(400, "hex has an odd number of nibbles")
    try:
        raw = bytes.fromhex(cleaned)
    except ValueError as exc:
        raise HTTPException(400, f"invalid hex: {exc}")
    if not raw:
        raise HTTPException(400, "no bytes to parse")
    transport = body.transport if body.transport in ("tcp", "udp") else "tcp"
    result = explain(raw, transport)
    _log_skips("parse", result.get("skips", []), result.get("skipped", 0))
    if result.get("error"):
        log.warning("parse: error=%s (%d bytes)", result["error"], len(raw))
    return result


def _log_skips(tag: str, skips: list, total: int) -> None:
    """Log each skipped frame (offset + reason) so it can be drilled into in Logs."""
    for s in skips[:50]:
        log.warning("%s: skipped %d byte(s) at offset %d — %s", tag, s["bytes"], s["at"], s["reason"])
    if total > 50:
        log.warning("%s: …and %d more skipped frame(s)", tag, total - 50)


@app.post("/api/parse-stream")
async def parse_stream(body: ParseBody):
    """Like /api/parse but streams NDJSON progress events while decoding, then a final
    `{"type":"result", …}` line — so the UI can show live decode progress on big dumps."""
    from ..codecs.teltonika_explain import walk

    cleaned = "".join(ch for ch in body.hex if ch in "0123456789abcdefABCDEF")
    if len(cleaned) % 2:
        cleaned = cleaned[:-1]
    raw = bytes.fromhex(cleaned)
    if not raw:
        raise HTTPException(400, "no bytes to parse")
    transport = body.transport if body.transport in ("tcp", "udp") else "tcp"
    total_bytes = len(raw)

    def gen():
        out = {"ok": True, "transport": transport, "packets": [], "records": [],
               "skipped": 0, "skips": [], "error": None}
        last, logged = 0, 0
        for kind, payload, off in walk(raw, transport):
            if kind == "packet":
                out["packets"].append(payload)
                out["records"].extend(payload.get("records", []))
            elif kind == "skip":
                out["skipped"] += 1
                if len(out["skips"]) < 200:
                    out["skips"].append(payload)
                if logged < 50:                      # log each skip reason for drill-down
                    log.warning("parse-stream: skipped %d byte(s) at offset %d — %s",
                                payload["bytes"], payload["at"], payload["reason"])
                    logged += 1
            elif kind == "error":
                out["error"] = payload
            if off - last >= 16384:                  # ~16 KB between progress ticks
                last = off
                yield json.dumps({"type": "progress", "bytes": off, "total_bytes": total_bytes,
                                  "packets": len(out["packets"]), "records": len(out["records"]),
                                  "skipped": out["skipped"]}) + "\n"
        if out["skipped"] > 50:
            log.warning("parse-stream: …and %d more skipped frame(s)", out["skipped"] - 50)
        out["ok"] = out["error"] is None
        out["consumed"] = total_bytes
        out["total"] = len(out["packets"]) + out["skipped"]
        yield json.dumps({"type": "result", **out}) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.post("/api/ingest")
async def ingest(body: IngestBody):
    """Decode hex through the real codec into the live store, so parsed devices show on
    the Dashboard (the Parser calls this alongside /api/parse)."""
    cleaned = "".join(ch for ch in body.hex if ch in "0123456789abcdefABCDEF")
    if len(cleaned) % 2:
        cleaned = cleaned[:-1]
    raw = bytes.fromhex(cleaned)
    if not raw:
        raise HTTPException(400, "no bytes to ingest")
    pipeline = Pipeline(_BytesSource(raw, body.peer), body.codec, _default_sinks())
    count = await pipeline.run()
    return {"records": count, "bytes": pipeline.bytes_in}


@app.get("/api/devices")
async def devices():
    return list(store.devices.values())


@app.get("/api/devices/{device_id}/records")
async def device_records(device_id: str, limit: int = 200):
    return store.device_records(device_id, limit)


@app.get("/api/packets")
async def packets(limit: int = 200):
    return store.recent(limit)


@app.get("/api/snapshot")
async def snapshot():
    return store.snapshot()


@app.get("/api/jobs")
async def jobs():
    return [_job_view(jid) for jid in _jobs]


# --------------------------------------------------------------------------- #
# Persistence rail: config, profiles, captures
# --------------------------------------------------------------------------- #
@app.get("/api/config")
async def get_config():
    return config.all()


@app.put("/api/config")
async def put_config(values: dict):
    for key, value in values.items():
        config.set(key, value)
    return config.all()


@app.get("/api/profiles")
async def list_profiles():
    return db.list("profiles", order="id DESC")


@app.post("/api/profiles")
async def create_profile(body: ProfileBody):
    pid = db.insert(
        "profiles",
        name=body.name, vendor=body.vendor, model=body.model,
        transport=body.transport, codec=body.codec,
        framing=json.dumps(body.framing), signature=json.dumps(body.signature),
        status=body.status,
    )
    return db.get("profiles", pid)


@app.get("/api/profiles/{profile_id}")
async def get_profile(profile_id: int):
    row = db.get("profiles", profile_id)
    if row is None:
        raise HTTPException(404, "profile not found")
    return row


@app.delete("/api/profiles/{profile_id}")
async def delete_profile(profile_id: int):
    db.delete("profiles", profile_id)
    return {"deleted": profile_id}


@app.get("/api/captures")
async def list_captures(limit: int = 100):
    return db.query("SELECT * FROM captures ORDER BY id DESC LIMIT ?", (limit,))


# --------------------------------------------------------------------------- #
# Signatures repository (decode specs: parameter key -> sub-field definitions)
# --------------------------------------------------------------------------- #
def _sig_file(name: str) -> Path:
    raw = name or ""
    if "/" in raw or "\\" in raw or ".." in raw:    # reject path traversal outright
        raise HTTPException(400, "invalid signature name (no path separators)")
    base = raw.strip()
    if not base:
        raise HTTPException(400, "signature name required")
    if not base.lower().endswith(".json"):
        base += ".json"
    if not _SIG_NAME_RE.match(base):
        raise HTTPException(400, "invalid signature name (use letters, digits, . _ - space)")
    return SIGNATURES_DIR / base


@app.get("/api/signatures")
async def list_signatures():
    out = []
    for p in sorted(SIGNATURES_DIR.glob("*.json")):
        info = {"name": p.name, "size": p.stat().st_size, "modified": p.stat().st_mtime}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            info["keys"] = len(data) if isinstance(data, dict) else 0
            info["fields"] = (sum(len(v) for v in data.values() if isinstance(v, list))
                              if isinstance(data, dict) else 0)
            info["valid"] = True
        except Exception:
            info.update(keys=0, fields=0, valid=False)
        out.append(info)
    return out


@app.get("/api/signatures/{name}")
async def get_signature(name: str):
    p = _sig_file(name)
    if not p.exists():
        raise HTTPException(404, "signature not found")
    try:
        return {"name": p.name, "content": json.loads(p.read_text(encoding="utf-8"))}
    except Exception as exc:
        raise HTTPException(400, f"invalid JSON in {p.name}: {exc}")


@app.post("/api/signatures")
async def save_signature(body: SignatureBody):
    p = _sig_file(body.name)
    content = body.content
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except Exception as exc:
            raise HTTPException(400, f"content is not valid JSON: {exc}")
    if not isinstance(content, dict):
        raise HTTPException(400, "signature content must be a JSON object (key -> [fields])")
    p.write_text(json.dumps(content, indent=2), encoding="utf-8")
    return {"name": p.name, "keys": len(content), "saved": True}


@app.delete("/api/signatures/{name}")
async def delete_signature(name: str):
    p = _sig_file(name)
    if p.exists():
        p.unlink()
    return {"deleted": p.name}


# --------------------------------------------------------------------------- #
# Protocols repository — format-agnostic: a transferable parser file can be a .py,
# .json, .yaml, … Any other utility can take the file and parse with it. Files are
# stored as-is (text); .json files are additionally validated + pretty-printed.
# --------------------------------------------------------------------------- #
PROTO_EXTS = {".json", ".py", ".yaml", ".yml", ".toml", ".txt", ".md", ".csv", ".xml", ".cfg", ".ini"}


def _proto_file(name: str) -> Path:
    raw = name or ""
    if "/" in raw or "\\" in raw or ".." in raw:
        raise HTTPException(400, "invalid protocol name (no path separators)")
    base = raw.strip()
    if not base:
        raise HTTPException(400, "protocol name required")
    if "." not in base:
        base += ".json"                                   # default format
    if not _SIG_NAME_RE.match(base):
        raise HTTPException(400, "invalid protocol name (use letters, digits, . _ - space)")
    if Path(base).suffix.lower() not in PROTO_EXTS:
        raise HTTPException(400, f"unsupported file type (allowed: {', '.join(sorted(PROTO_EXTS))})")
    return PROTOCOLS_DIR / base


def _proto_meta(p: Path) -> dict:
    st = p.stat()
    info = {"name": p.name, "size": st.st_size, "modified": st.st_mtime,
            "kind": p.suffix.lower().lstrip(".") or "file", "valid": True,
            "keys": 0, "fields": 0}
    if p.suffix.lower() == ".json":
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            info["keys"] = len(data) if isinstance(data, dict) else 0
            info["fields"] = (sum(len(v) for v in data.values() if isinstance(v, list))
                              if isinstance(data, dict) else 0)
        except Exception:
            info["valid"] = False
    return info


@app.get("/api/protocols")
async def list_protocols():
    return [_proto_meta(p) for p in _proto_files()]


@app.get("/api/protocols/{name}")
async def get_protocol(name: str):
    p = _proto_file(name)
    if not p.exists():
        raise HTTPException(404, "protocol not found")
    text = p.read_text(encoding="utf-8", errors="replace")
    content = None
    if p.suffix.lower() == ".json":
        try:
            content = json.loads(text)
        except Exception:
            content = None                                # still return raw text to view/edit
    return {"name": p.name, "kind": p.suffix.lower().lstrip(".") or "file",
            "text": text, "content": content}


@app.post("/api/protocols")
async def save_protocol(body: SignatureBody):
    p = _proto_file(body.name)
    content = body.content
    if p.suffix.lower() == ".json":
        # JSON files are validated and stored pretty-printed.
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except Exception as exc:
                raise HTTPException(400, f"content is not valid JSON: {exc}")
        if not isinstance(content, dict):
            raise HTTPException(400, "a .json protocol's content must be a JSON object")
        p.write_text(json.dumps(content, indent=2), encoding="utf-8")
        return {"name": p.name, "kind": "json", "keys": len(content), "saved": True}
    # Any other format is stored verbatim — content must be raw text.
    if not isinstance(content, str):
        raise HTTPException(400, "non-JSON protocol content must be a string")
    p.write_text(content, encoding="utf-8")
    return {"name": p.name, "kind": p.suffix.lower().lstrip("."), "bytes": len(content), "saved": True}


@app.delete("/api/protocols/{name}")
async def delete_protocol(name: str):
    p = _proto_file(name)
    if p.exists():
        p.unlink()
    return {"deleted": p.name}


def _proto_files() -> list[Path]:
    return sorted(p for p in PROTOCOLS_DIR.iterdir() if p.is_file() and p.suffix.lower() in PROTO_EXTS)


@app.get("/api/protocols/export")
async def export_protocols(name: str | None = None):
    """Zip protocol files for instant transfer to another system.

    Without ``name``: the whole repository. With ``name``: every file sharing that file's stem
    (e.g. ``ais140.json`` + ``ais140.py`` travel together as one protocol package).
    """
    files = _proto_files()
    if name:
        stem = Path(name).stem
        files = [p for p in files if p.stem == stem]
        if not files:
            raise HTTPException(404, "protocol not found")
        zip_name = f"{stem}.zip"
    else:
        if not files:
            raise HTTPException(404, "no protocols to export")
        zip_name = "protocols-export.zip"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            zf.write(p, arcname=p.name)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                              headers={"Content-Disposition": f'attachment; filename="{zip_name}"'})


@app.post("/api/protocols/import")
async def import_protocols(file: UploadFile = File(...)):
    """Unpack a protocol .zip (as produced by /api/protocols/export) into the repository.

    Entries are validated the same way as a manual save (name pattern, allowed extension, no
    path separators) — anything a zip-slip or unsupported file is skipped, not written.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(await file.read()))
    except zipfile.BadZipFile:
        raise HTTPException(400, "not a valid zip file")

    imported: list[str] = []
    skipped: list[dict[str, str]] = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        entry_name = Path(info.filename).name          # strip any directory component (zip-slip guard)
        try:
            dest = _proto_file(entry_name)
        except HTTPException as exc:
            skipped.append({"name": info.filename, "reason": str(exc.detail)})
            continue
        content = zf.read(info)
        if dest.suffix.lower() == ".json":
            try:
                parsed = json.loads(content.decode("utf-8"))
            except Exception as exc:
                skipped.append({"name": info.filename, "reason": f"invalid JSON: {exc}"})
                continue
            dest.write_text(json.dumps(parsed, indent=2), encoding="utf-8")
        else:
            dest.write_bytes(content)
        imported.append(dest.name)
    return {"imported": imported, "skipped": skipped}


# --------------------------------------------------------------------------- #
# Converter — upload a vehicle signature (CanPlus XML), view/edit, export DBC/JSON.
# --------------------------------------------------------------------------- #
@app.post("/api/convert/parse")
async def convert_parse(body: ConvertParseBody):
    """Parse an uploaded signature file into the editable signal model."""
    if body.fmt != "canplus":
        raise HTTPException(400, f"unsupported source format '{body.fmt}'")
    from ..convert import canplus
    try:
        sig = canplus.parse(body.text)
    except Exception as exc:
        raise HTTPException(400, f"could not parse signature: {exc}")
    return canplus.to_model(sig)


@app.post("/api/convert/export")
async def convert_export(body: ConvertExportBody):
    """Render the (possibly edited) model to a downloadable JSON or DBC artifact."""
    from ..convert import canplus
    model = {"vehicle": body.vehicle, "signals": body.signals}
    brand = re.sub(r"[^0-9A-Za-z]+", "_", f"{body.vehicle.get('brand','')}_"
                   f"{body.vehicle.get('model','')}").strip("_") or "signals"
    if body.fmt == "dbc":
        return {"filename": f"{brand}.dbc", "mime": "text/plain",
                "content": canplus.model_to_dbc(model)}
    if body.fmt == "json":
        return {"filename": f"{brand}.json", "mime": "application/json",
                "content": json.dumps({"format": "canplus", **model}, indent=2)}
    raise HTTPException(400, f"unsupported export format '{body.fmt}'")


# --------------------------------------------------------------------------- #
# Diagnostics: runtime log (errors + activity)
# --------------------------------------------------------------------------- #
@app.get("/api/logs")
async def get_logs(limit: int = 500, source: str = "activity"):
    p = _log_path(source)
    if not p.exists():
        return {"path": str(p), "size": 0, "lines": [], "source": source}
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    return {"path": str(p), "size": p.stat().st_size, "lines": lines[-limit:], "source": source}


@app.get("/api/logs/download")
async def download_logs(source: str = "activity"):
    p = _log_path(source)
    if not p.exists():
        raise HTTPException(404, "no log file yet")
    return FileResponse(str(p), filename=p.name, media_type="text/plain")


@app.delete("/api/logs")
async def clear_logs(source: str = "activity"):
    p = _log_path(source)
    if p.exists():
        p.write_text("", encoding="utf-8")
    log.info("log cleared from Diagnostics (%s)", source)
    return {"cleared": True, "source": source}


@app.post("/api/replay")
async def replay(
    file: UploadFile = File(...),
    codec: str = Form("auto"),
    fmt: str = Form("auto"),
    delay: float = Form(0.0),
):
    UPLOAD_DIR.mkdir(exist_ok=True)
    dest = UPLOAD_DIR / (file.filename or "upload.bin")
    dest.write_bytes(await file.read())
    from ..sources.file_replay import FileReplaySource

    source = FileReplaySource(str(dest), fmt=fmt, delay=delay)
    jid = _new_job("replay", file=dest.name, codec=codec)
    _spawn(jid, Pipeline(source, codec, _default_sinks()))
    return {"job": _jobs[jid]}


@app.post("/api/replay-path")
async def replay_path(body: ReplayPathBody):
    if not Path(body.path).exists():
        raise HTTPException(404, f"file not found: {body.path}")
    from ..sources.file_replay import FileReplaySource

    source = FileReplaySource(body.path, fmt=body.fmt, delay=body.delay)
    jid = _new_job("replay", file=body.path, codec=body.codec)
    _spawn(jid, Pipeline(source, body.codec, _default_sinks()))
    return {"job": _jobs[jid]}


def _local_ip() -> str:
    """Best-effort primary LAN IP of this machine (the address devices dial into).

    Opens a UDP socket toward a public address to learn the outbound interface — no
    packets are actually sent. Falls back to the hostname lookup, then localhost.
    """
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


@app.get("/api/listen/defaults")
async def listen_defaults():
    """Last-used listener settings + the auto-detected local IP (for the Start form)."""
    import socket
    return {"host": config.get("default_listen_host", "0.0.0.0"),
            "port": config.get("default_listen_port", 5050),
            "codec": config.get("default_codec", "auto"),
            "local_ip": _local_ip(),
            "hostname": socket.gethostname()}


def _spawn_listener(jid: str) -> None:
    """(Re)start the TCP listener for an existing listen job using its stored config."""
    from ..sources.tcp_server import TcpServerSource

    job = _jobs[jid]

    async def _on_conn(kind: str, peer: str) -> None:
        peers = source.peers()
        j = _jobs.get(jid)
        if j is not None:
            j["peers"] = peers
        await hub.publish({"type": "conn", "data": {"job": jid, "peer": peer,
                                                    "state": kind, "peers": peers,
                                                    "port": job["port"]}})

    source = TcpServerSource(host=job["host"], port=job["port"], on_event=_on_conn)
    _spawn(jid, Pipeline(source, job["codec"], _default_sinks()))


@app.post("/api/listen")
async def listen(body: ListenBody):
    registry.load_plugins()
    if body.codec != "auto" and body.codec not in registry.CODECS:
        raise HTTPException(400, f"unknown codec '{body.codec}'")

    jid = _new_job("listen", codec=body.codec, host=body.host, port=body.port,
                   started=time.time(), peers=[])
    try:
        _spawn_listener(jid)
    except Exception as exc:
        _jobs[jid].update(status="error", error=str(exc))
        raise HTTPException(500, f"could not start listener: {exc}")

    # Remember settings for next time.
    config.set("default_listen_host", body.host)
    config.set("default_listen_port", body.port)
    config.set("default_codec", body.codec)
    return {"job": _job_view(jid)}


@app.post("/api/resume/{job_id}")
async def resume(job_id: str):
    """Resume a stopped/finished listener under the SAME job id (no new card)."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.get("kind") != "listen":
        raise HTTPException(400, "only listener jobs can be resumed")
    if job_id in _tasks:
        return {"job": _job_view(job_id)}                 # already running
    job.update(status="pending", started=time.time(), ended=None, peers=[],
               records=0, bytes=0, error=None)
    try:
        _spawn_listener(job_id)
    except Exception as exc:
        job.update(status="error", error=str(exc))
        raise HTTPException(500, f"could not resume listener: {exc}")
    return {"job": _job_view(job_id)}


@app.post("/api/send/{job_id}")
async def send_command(job_id: str, body: SendBody):
    """Send a downlink command/bytes back to a connected device on a listener."""
    pipe = _pipelines.get(job_id)
    if pipe is None:
        raise HTTPException(404, "job not running")
    source = getattr(pipe, "source", None)
    if source is None or not hasattr(source, "send"):
        raise HTTPException(400, "this job does not support sending")

    if body.mode == "hex":
        cleaned = "".join(c for c in body.value if c in "0123456789abcdefABCDEF")
        if len(cleaned) % 2 or not cleaned:
            raise HTTPException(400, "invalid hex")
        data = bytes.fromhex(cleaned)
    else:  # build via the job's codec when it supports it, else raw ASCII
        codec_name = _jobs.get(job_id, {}).get("codec", "")
        cls = registry.CODECS.get(codec_name)
        builder = getattr(cls, "build_command", None) if cls else None
        data = builder(body.value) if builder else body.value.encode("ascii", "replace")

    ok = await source.send(body.peer, data)
    if not ok:
        raise HTTPException(404, f"peer {body.peer} not connected")
    return {"sent": len(data), "peer": body.peer, "hex": data.hex()}


@app.post("/api/stop/{job_id}")
async def stop(job_id: str):
    task = _tasks.get(job_id)
    if task is None:
        raise HTTPException(404, "job not running")
    job = _jobs.get(job_id)
    if job is not None:                       # reflect immediately so a poll mid-cancel
        job["status"] = "stopped"             # never sees it back as "running"
        job["ended"] = time.time()
    task.cancel()
    return {"stopped": job_id}


@app.delete("/api/jobs/{job_id}")
async def remove_job(job_id: str):
    """Drop a finished/stopped job from the list (must not be running)."""
    if job_id in _tasks:
        raise HTTPException(400, "stop the job before removing it")
    _jobs.pop(job_id, None)
    _pipelines.pop(job_id, None)
    return {"removed": job_id}


# --------------------------------------------------------------------------- #
# Device QC — Target 1: intake + identify + record.
#
# A QC session is a "qc"-kind job (reusing the listen/job plumbing above) plus a
# QcCaptureSink that learns the connecting device's protocol/id and writes its own
# capture file. The qc_sessions DB row is the durable record; _qc_sinks holds the live
# counters while the session's job is running.
# --------------------------------------------------------------------------- #
def _qc_capture_path(qc_id: int) -> Path:
    d = Path(DATA_DIR) / "qc"
    d.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%d-%b-%Y_%H-%M-%S")
    return d / f"qc-{qc_id}-{stamp}.txt"


def _qc_profile(protocol: str) -> Optional[dict]:
    """The per-protocol QC coverage profile, or None. A user copy in data/qc/profiles wins;
    otherwise the default shipped inside the package is used. Read each call so an edited
    profile is picked up without a restart (files are small)."""
    if not protocol:
        return None
    for base in (QC_PROFILES_DIR, QC_DEFAULTS_DIR):
        p = base / f"{protocol}.json"
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
    return None


def _qc_last_recv_epoch(records) -> Optional[float]:
    """Most-recent RECEIVE time across a session's records (drives idle/timeout — uses
    received_at, not the device's reported event_time which history packets backdate)."""
    best = None
    for r in records:
        t = getattr(r, "received_at", None)
        if t is not None:
            e = t.timestamp()
            if best is None or e > best:
                best = e
    return best


# Worst-first ranking so a lot's verdict = its worst unit (any fail -> lot fails).
_VERDICT_RANK = {"": 0, "pass": 1, "conditional_pass": 2, "in_progress": 3, "timeout": 4, "fail": 5}


def _jloads_dict(s: str) -> dict:
    try:
        v = json.loads(s or "{}")
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def _device_verdict(cov: list, checks_verdict: str, meta: dict) -> str:
    """Verdict for one device (or an unidentified session). Coverage-driven when a catalog
    is present; otherwise the legacy checks pass/fail — but an abandoned session (idle past
    the timeout) flips to 'timeout' regardless (Change 1)."""
    if meta.get("finalized_verdict"):
        return meta["finalized_verdict"]
    if not meta.get("packet_count"):
        return ""
    if cov:
        return qc_engine.overall_verdict(cov, meta)
    now, last = meta.get("now"), meta.get("last_packet_epoch")
    stale = meta.get("stale_timeout_s") or qc_engine.DEFAULT_CONFIG["stale_timeout_s"]
    idle = (now - last) if (now and last) else None
    if idle is not None and idle >= stale:
        return "timeout"
    return checks_verdict


def _qc_devices(records, catalog, profile, skipped_map, finalized_map, started_at, now) -> list:
    """Group a session's records by device id (IMEI) and compute per-device coverage +
    verdict + identity. One entry per physical device — a reconnect (new peer) merges into
    the same device rather than adding a row."""
    from collections import OrderedDict
    groups: "OrderedDict[str, list]" = OrderedDict()
    for r in records:
        did = r.device_id
        if did and did not in UNKNOWN_DEVICE_IDS:
            groups.setdefault(did, []).append(r)
    stale = (profile or {}).get("stale_timeout_s")
    out = []
    for did, recs in groups.items():
        cov = qc_engine.coverage(recs, catalog) if catalog else []
        last = _qc_last_recv_epoch(recs)
        checks, checks_verdict = qc_engine.evaluate(recs, {"device_id": did, "started_at": started_at})
        meta = {"packet_count": len(recs), "now": now, "last_packet_epoch": last,
                "stale_timeout_s": stale, "skipped": skipped_map.get(did) or [],
                "finalized_verdict": finalized_map.get(did) or ""}
        fw = vend = ""
        for r in recs:
            io = r.io or {}
            fw = io.get("firmware_version") or fw
            vend = io.get("vendor_id") or vend
        out.append({
            "device_id": did, "packet_count": len(recs),
            "coverage": cov, "checks": checks,
            "verdict": _device_verdict(cov, checks_verdict, meta),
            "idle_s": (now - last) if last else None, "last_seen": last,
            "peers": sorted({r.peer for r in recs if r.peer}),
            "firmware_version": fw, "vendor_id": vend,
            "skipped": skipped_map.get(did) or [],
            "finalized_verdict": finalized_map.get(did) or "",
        })
    return out


def _aggregate_verdict(devices: list, fallback: str) -> str:
    """Session (tab) verdict: the worst among its devices; if none identified yet, the
    session-level fallback (which covers the abandoned/unidentified case)."""
    if not devices:
        return fallback
    return max((d["verdict"] for d in devices), key=lambda v: _VERDICT_RANK.get(v, 0))


def _qc_compute(row: dict, sink, now: Optional[float] = None) -> dict:
    """Everything the view/report/stop need from a QC session's records: per-device list,
    session-level checks/coverage, and the aggregate verdict. Works off the live sink while
    running; off the persisted snapshots once stopped."""
    now = now or time.time()
    skipped_map = _jloads_dict(row.get("skipped_json"))
    finalized_map = _jloads_dict(row.get("finalized_json"))
    if sink is not None:
        profile = _qc_profile(row.get("identified_as") or "")
        catalog = (profile or {}).get("coverage") or []
        devices = _qc_devices(sink.records, catalog, profile, skipped_map, finalized_map,
                              row.get("started_at"), now)
        checks, checks_verdict = sink.checks()
        sess_cov = qc_engine.coverage(sink.records, catalog) if catalog else []
        sess_last = _qc_last_recv_epoch(sink.records)
        sess_meta = {"packet_count": sink.count, "now": now, "last_packet_epoch": sess_last,
                     "stale_timeout_s": (profile or {}).get("stale_timeout_s"),
                     "skipped": skipped_map.get("") or [],
                     "finalized_verdict": row.get("finalized_verdict") or ""}
        fallback = _device_verdict(sess_cov, checks_verdict, sess_meta)
        return {"devices": devices, "checks": checks, "coverage": sess_cov,
                "verdict": _aggregate_verdict(devices, fallback),
                "idle_s": (now - sess_last) if sess_last else None}
    # stopped: persisted snapshots
    return {"devices": json.loads(row.get("devices_json") or "[]"),
            "checks": json.loads(row.get("checks_json") or "[]"),
            "coverage": json.loads(row.get("coverage_json") or "[]"),
            "verdict": row.get("finalized_verdict") or row.get("verdict", ""),
            "idle_s": None}


def _qc_view(qc_id: int) -> dict:
    row = db.qc_session(qc_id)
    if row is None:
        raise HTTPException(404, "QC session not found")
    row = dict(row)
    sink = _qc_sinks.get(qc_id)
    if sink is not None:                          # live identity/counters while running
        row["packet_count"] = sink.count
        row["identified_as"] = sink.identified_as or row.get("identified_as", "")
        row["device_id"] = sink.device_id or row.get("device_id", "")
    comp = _qc_compute(row, sink)
    row["devices"] = comp["devices"]
    row["checks"] = comp["checks"]
    row["coverage"] = comp["coverage"]
    row["verdict"] = comp["verdict"]
    row["idle_s"] = comp["idle_s"]
    row["skipped"] = _jloads_dict(row.get("skipped_json"))
    row["capture_mode"] = row.get("capture_mode") or "full"
    jid = row.get("job_id")
    jv = _job_view(jid) if jid else {}
    row["connected"] = bool(jv.get("peers"))
    job_status = jv.get("status", row.get("status"))
    row["job_status"] = job_status
    # Self-heal: the underlying listener can die on its own (e.g. the port is already in
    # use elsewhere) — that failure surfaces asynchronously on the job, not synchronously
    # from /api/qc/start. Without this, qc_sessions.status would show "running" forever
    # even though nothing is actually listening. Reconcile it the next time this session
    # is viewed (list/get/poll all go through here). Also covers the job having VANISHED
    # (unknown to this process, e.g. after a restart): nothing is listening then either.
    job_gone = not jid or jid not in _jobs
    if row["status"] == "running" and (job_gone or job_status in ("error", "stopped", "done")):
        new_status = "error" if job_status == "error" else "stopped"
        ended = time.time()
        db.qc_update(qc_id, status=new_status, ended_at=ended,
                     checks_json=json.dumps(row["checks"]),
                     coverage_json=json.dumps(row["coverage"]),
                     devices_json=json.dumps(row["devices"]), verdict=row["verdict"])
        _qc_sinks.pop(qc_id, None)
        row["status"] = new_status
        row["ended_at"] = ended
        row["error"] = jv.get("error", "")

    # The device id (IMEI) is the device's real, durable identity — a session id/port is
    # just this run's plumbing. Once it's known, surface every OTHER QC session ever run
    # for that same device id, so a returning device is recognized regardless of which
    # port/session it lands on this time.
    row["prior_sessions"] = []
    if row.get("device_id"):
        row["prior_sessions"] = [
            {"id": h["id"], "status": h["status"], "started_at": h["started_at"],
             "ended_at": h["ended_at"], "packet_count": h["packet_count"], "port": h["port"]}
            for h in db.qc_sessions_by_device(row["device_id"]) if h["id"] != qc_id
        ]
    return row


async def _qc_publish(qc_id: int) -> None:
    try:
        await hub.publish({"type": "qc", "data": _qc_view(qc_id)})
    except Exception:
        log.exception("qc %s: failed to publish update", qc_id)


@app.get("/api/qc")
async def qc_list():
    return [_qc_view(r["id"]) for r in db.qc_sessions()]


@app.get("/api/qc/{qc_id}")
async def qc_get(qc_id: int):
    return _qc_view(qc_id)


def _qc_spawn(qc_id: int, host: str, port: int, codec: str, initial_count: int = 0) -> None:
    """(Re)start the listener + capture for an existing QC session row (start or resume)."""
    from ..sources.tcp_server import TcpServerSource

    row = db.qc_session(qc_id)
    loop = asyncio.get_event_loop()

    def _on_update(_snap: dict) -> None:
        loop.create_task(_qc_publish(qc_id))

    qc_sink = QcCaptureSink(row["capture_path"], started_at=row["started_at"], on_update=_on_update)
    qc_sink.count = initial_count               # keep the packet count cumulative across resumes
    _qc_sinks[qc_id] = qc_sink

    async def _on_conn(kind: str, peer: str) -> None:
        db.qc_update(qc_id, peer=peer if kind == "up" else "")
        await _qc_publish(qc_id)

    source = TcpServerSource(host=host, port=port, on_event=_on_conn)
    jid = _new_job("qc", codec=codec, host=host, port=port, started=time.time(), peers=[])
    db.qc_update(qc_id, job_id=jid)
    _spawn(jid, Pipeline(source, codec, [*_default_sinks(), qc_sink]))


@app.post("/api/qc/start")
async def qc_start(body: QcStartBody):
    registry.load_plugins()
    codec = body.protocol if body.protocol_mode == "manual" else "auto"
    if body.protocol_mode == "manual" and codec not in registry.CODECS:
        raise HTTPException(400, f"unknown codec '{codec}'")

    started = time.time()
    qc_id = db.insert(
        "qc_sessions", port=body.port, host=body.host, protocol_mode=body.protocol_mode,
        protocol=codec, identified_as="", device_id="", peer="", operator=body.operator,
        notes=body.notes, status="running", packet_count=0, capture_path="",
        capture_fmt="txt", started_at=started, ended_at=None,
    )
    db.qc_update(qc_id, capture_path=str(_qc_capture_path(qc_id)))
    try:
        _qc_spawn(qc_id, body.host, body.port, codec)
    except Exception as exc:
        db.qc_update(qc_id, status="error", ended_at=time.time())
        _qc_sinks.pop(qc_id, None)
        raise HTTPException(500, f"could not start QC session: {exc}")
    return _qc_view(qc_id)


@app.post("/api/qc/{qc_id}/resume")
async def qc_resume(qc_id: int):
    """Resume a stopped/errored QC session under the SAME id (no new card) — the packet
    count and capture file continue where they left off (capture file is append-only)."""
    row = db.qc_session(qc_id)
    if row is None:
        raise HTTPException(404, "QC session not found")
    jid = row.get("job_id")
    if jid and jid in _tasks:
        return _qc_view(qc_id)                    # already running
    db.qc_update(qc_id, status="running", ended_at=None, peer="")
    try:
        _qc_spawn(qc_id, row["host"], row["port"], row["protocol"],
                 initial_count=row.get("packet_count", 0))
    except Exception as exc:
        db.qc_update(qc_id, status="error", ended_at=time.time())
        _qc_sinks.pop(qc_id, None)
        raise HTTPException(500, f"could not resume QC session: {exc}")
    return _qc_view(qc_id)


@app.post("/api/qc/{qc_id}/stop")
async def qc_stop(qc_id: int):
    row = db.qc_session(qc_id)
    if row is None:
        raise HTTPException(404, "QC session not found")
    jid = row.get("job_id")
    if jid and jid in _tasks:
        j = _jobs.get(jid)
        if j is not None:                  # reflect immediately, same as /api/stop/{job_id}
            j["status"] = "stopped"
            j["ended"] = time.time()
        _tasks[jid].cancel()
    sink = _qc_sinks.get(qc_id)
    # Evaluate with the sink's records still in hand so the persisted per-device coverage/
    # verdict are the final ones (the sink is dropped right after).
    row2 = dict(row)
    if sink is not None:
        row2["identified_as"] = sink.identified_as or row2.get("identified_as", "")
        row2["device_id"] = sink.device_id or row2.get("device_id", "")
    comp = _qc_compute(row2, sink)
    _qc_sinks.pop(qc_id, None)
    db.qc_update(qc_id, status="stopped", ended_at=time.time(),
                 packet_count=(sink.count if sink else row.get("packet_count", 0)),
                 identified_as=(sink.identified_as if sink else row.get("identified_as", "")),
                 device_id=(sink.device_id if sink else row.get("device_id", "")),
                 checks_json=json.dumps(comp["checks"]), coverage_json=json.dumps(comp["coverage"]),
                 devices_json=json.dumps(comp["devices"]), verdict=comp["verdict"])
    await _qc_publish(qc_id)
    return _qc_view(qc_id)


@app.post("/api/qc/{qc_id}/skip")
async def qc_skip(qc_id: int, body: QcSkipBody):
    """Mark coverage items optional for a device on this session (bench can't always trigger
    every alarm). A required item that's skipped no longer blocks that device — it settles
    as Conditional Pass instead of staying in-progress. Keyed per device_id."""
    row = db.qc_session(qc_id)
    if row is None:
        raise HTTPException(404, "QC session not found")
    m = _jloads_dict(row.get("skipped_json"))
    m[body.device_id or ""] = sorted(set(body.keys))
    db.qc_update(qc_id, skipped_json=json.dumps(m))
    await _qc_publish(qc_id)
    return _qc_view(qc_id)


@app.post("/api/qc/{qc_id}/finalize")
async def qc_finalize(qc_id: int, body: QcFinalizeBody):
    """Operator's explicit verdict for a device — overrides the computed state ('Pass with
    conditions' -> conditional_pass, or an outright fail). Pass verdict='' to clear it.
    device_id="" targets the session-level fallback (unidentified sessions)."""
    row = db.qc_session(qc_id)
    if row is None:
        raise HTTPException(404, "QC session not found")
    v = body.verdict or ""
    if v and v not in ("pass", "conditional_pass", "fail"):
        raise HTTPException(400, "verdict must be pass, conditional_pass, fail, or empty")
    if body.device_id:
        m = _jloads_dict(row.get("finalized_json"))
        if v:
            m[body.device_id] = v
        else:
            m.pop(body.device_id, None)
        db.qc_update(qc_id, finalized_json=json.dumps(m))
    else:
        db.qc_update(qc_id, finalized_verdict=v)
    await _qc_publish(qc_id)
    return _qc_view(qc_id)


@app.get("/api/qc/{qc_id}/capture")
async def qc_capture_file(qc_id: int):
    row = db.qc_session(qc_id)
    if row is None or not row.get("capture_path") or not Path(row["capture_path"]).exists():
        raise HTTPException(404, "no capture file for this QC session")
    return FileResponse(row["capture_path"], filename=Path(row["capture_path"]).name,
                        media_type="text/plain")


@app.get("/api/qc/{qc_id}/report")
async def qc_report(qc_id: int, fmt: str = "json"):
    """QC report: verdict + every check's result/detail + device + session summary.

    fmt=json (default) -> the report as a JSON object (for the in-page report view).
    fmt=txt/csv         -> a downloadable file of the same data.
    """
    v = _qc_view(qc_id)
    report = {
        "qc_id": v["id"], "device_id": v.get("device_id") or "", "identified_as": v.get("identified_as") or "",
        "protocol_mode": v["protocol_mode"], "port": v["port"], "host": v["host"],
        "operator": v.get("operator") or "", "notes": v.get("notes") or "",
        "status": v["status"], "verdict": v.get("verdict") or "",
        "packet_count": v.get("packet_count", 0),
        "started_at": v["started_at"], "ended_at": v.get("ended_at"),
        "checks": v.get("checks") or [],
        "coverage": v.get("coverage") or [],
        "devices": v.get("devices") or [],
        "skipped": v.get("skipped") or {},
        "prior_session_count": len(v.get("prior_sessions") or []),
    }
    if fmt not in ("csv", "txt"):
        return report

    def _ts(t):
        return time.strftime("%d-%b-%Y %H:%M:%S", time.localtime(t)) if t else ""

    lines = [
        f"QC Report — session #{report['qc_id']}",
        f"Device id (IMEI): {report['device_id'] or '(unidentified)'}",
        f"Protocol: {report['identified_as'] or report['protocol_mode']}",
        f"Port: {report['port']}    Operator: {report['operator'] or '-'}",
        f"Started: {_ts(report['started_at'])}    Ended: {_ts(report['ended_at'])}",
        f"Packets captured: {report['packet_count']}",
        f"Verdict: {report['verdict'].upper() or 'N/A'}",
        f"Prior sessions for this device: {report['prior_session_count']}",
        "", "Checks:",
    ]
    for c in report["checks"]:
        lines.append(f"  [{c['result'].upper():4}] {c['label']} — {c['detail']}")
    # Per-device packet coverage (one block per device — mirrors the Raw Data sheets).
    for d in report["devices"]:
        skipped = set(d.get("skipped") or [])
        lines += ["", f"Device {d['device_id']} — {str(d.get('verdict') or 'n/a').upper()}"
                      f"  (fw {d.get('firmware_version') or '-'}, vendor {d.get('vendor_id') or '-'},"
                      f" {d.get('packet_count', 0)} packets)"]
        for c in d.get("coverage") or []:
            req = "required" if c.get("required") else "optional"
            state = ("skipped" if c["key"] in skipped else "seen" if c.get("seen") else "MISSING")
            lines.append(f"    [{state.upper():7}] {c['label']} ({req}) — {c.get('count', 0)}×")
    if report["notes"]:
        lines += ["", f"Notes: {report['notes']}"]
    content = "\n".join(lines) + "\n"
    fname = f"qc-report-{report['qc_id']}.{fmt}"
    return PlainTextResponse(content, headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.delete("/api/qc/{qc_id}")
async def qc_delete(qc_id: int):
    """Clear a finished QC session from the list (must be stopped/error first, same rule
    as removing a Live TCP job) — deletes the DB row; the capture file on disk is left
    untouched (a separate, explicit cleanup, not implied by clearing the session list)."""
    row = db.qc_session(qc_id)
    if row is None:
        raise HTTPException(404, "QC session not found")
    jid = row.get("job_id")
    if jid and jid in _tasks:
        raise HTTPException(400, "stop the session before clearing it")
    db.delete("qc_sessions", qc_id)
    _qc_sinks.pop(qc_id, None)
    return {"deleted": qc_id}


@app.get("/api/qc/device/{device_id}")
async def qc_device_summary(device_id: str):
    """Cross-session device profile for the QC "click the IMEI" drill-down: firmware/
    vendor info plus an alarm-coverage checklist, built from every record the live store
    has retained for this device_id (proinfo/storage/live.py's LiveStore is a process-wide
    ring buffer keyed by device_id -- NOT scoped to one QC session -- so this reflects the
    device's whole history across every session it has ever run, same identity model as
    qc_sessions_by_device / prior_sessions)."""
    records = store.device_records(device_id, limit=1000)
    if not records:
        raise HTTPException(404, "no records seen for this device id")

    protocol = records[-1].get("protocol", "")
    device_info: dict = {}
    for r in records:
        io = r.get("io") or {}
        for key in ("firmware_version", "vendor_id"):
            if io.get(key):
                device_info[key] = io[key]     # last (most recent) non-empty value wins

    tally: dict[str, dict] = {}
    for r in records:
        ts = r.get("event_time") or r.get("received_at")
        for a in r.get("alarms") or []:
            entry = tally.setdefault(a, {"count": 0, "last_seen": None})
            entry["count"] += 1
            if ts and (entry["last_seen"] is None or ts > entry["last_seen"]):
                entry["last_seen"] = ts

    catalog = getattr(registry.CODECS.get(protocol), "alarm_catalog", {}) if protocol else {}
    alarms = [
        {"key": key, "label": label, "seen": key in tally,
         "count": tally.get(key, {}).get("count", 0), "last_seen": tally.get(key, {}).get("last_seen")}
        for key, label in catalog.items()
    ]
    # An alarm actually observed but outside the known catalog (a different protocol with
    # no fixed catalog, or a gap in it) still shows up -- just without a curated label.
    alarms += [
        {"key": key, "label": key, "seen": True, "count": v["count"], "last_seen": v["last_seen"]}
        for key, v in tally.items() if key not in catalog
    ]

    return {"device_id": device_id, "protocol": protocol, "records_seen": len(records),
            "device_info": device_info, "alarms": alarms}


@app.delete("/api/data")
async def clear_data():
    store.clear()
    return {"cleared": True}


# --------------------------------------------------------------------------- #
# Continuous packet logging (server-side; start/stop, then download the file).
# --------------------------------------------------------------------------- #
@app.get("/api/log/status")
async def log_status():
    return packet_log.status()


@app.post("/api/log/start")
async def log_start(body: LogStartBody):
    try:
        return packet_log.start(body.fmt, body.raw, body.name, body.directory)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except OSError as exc:
        raise HTTPException(400, f"could not open log file: {exc}")


@app.post("/api/log/stop")
async def log_stop():
    return packet_log.stop()


@app.get("/api/log/file")
async def log_file():
    p = packet_log.path
    if not p or not Path(p).exists():
        raise HTTPException(404, "no log file yet")
    return FileResponse(p, filename=Path(p).name, media_type="application/octet-stream")


# --------------------------------------------------------------------------- #
# WebSocket
# --------------------------------------------------------------------------- #
@app.websocket("/ws")
async def ws(socket: WebSocket):
    await socket.accept()
    q = hub.subscribe()
    try:
        await socket.send_json({"type": "snapshot", "data": store.snapshot()})
        while True:
            msg = await q.get()
            await socket.send_json(msg)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.unsubscribe(q)


# Dashboard (mounted last so /api and /ws win). html=True serves index.html at /.
if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
