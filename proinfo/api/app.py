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
import json
import logging
import re
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from logging.handlers import RotatingFileHandler
from pydantic import BaseModel

from .. import __version__
from ..core import registry
from ..core.interfaces import Source
from ..core.models import RawFrame
from ..core.pipeline import Pipeline
from ..storage.db import DATA_DIR
from ..storage.live import config, db, hub, store

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


@asynccontextmanager
async def lifespan(_: FastAPI):
    registry.load_plugins()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
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
    signature: dict = {}
    status: str = "draft"


# --------------------------------------------------------------------------- #
# Job plumbing
# --------------------------------------------------------------------------- #
def _default_sinks():
    return [registry.get_sink("memory_store"), registry.get_sink("ws_broadcast")]


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
        if src is not None and hasattr(src, "peers"):
            job["peers"] = src.peers()
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
    return [_proto_meta(p) for p in sorted(PROTOCOLS_DIR.iterdir())
            if p.is_file() and p.suffix.lower() in PROTO_EXTS]


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


@app.post("/api/listen")
async def listen(body: ListenBody):
    from ..sources.tcp_server import TcpServerSource

    registry.load_plugins()
    if body.codec != "auto" and body.codec not in registry.CODECS:
        raise HTTPException(400, f"unknown codec '{body.codec}'")

    jid = _new_job("listen", codec=body.codec, host=body.host, port=body.port,
                   started=time.time(), peers=[])

    async def _on_conn(kind: str, peer: str) -> None:
        job = _jobs.get(jid)
        peers = source.peers()
        if job is not None:
            job["peers"] = peers
        await hub.publish({"type": "conn", "data": {"job": jid, "peer": peer,
                                                    "state": kind, "peers": peers,
                                                    "port": body.port}})

    source = TcpServerSource(host=body.host, port=body.port, on_event=_on_conn)
    try:
        _spawn(jid, Pipeline(source, body.codec, _default_sinks()))
    except Exception as exc:
        _jobs[jid].update(status="error", error=str(exc))
        raise HTTPException(500, f"could not start listener: {exc}")

    # Remember settings for next time.
    config.set("default_listen_host", body.host)
    config.set("default_listen_port", body.port)
    config.set("default_codec", body.codec)
    return {"job": _job_view(jid)}


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
    task.cancel()
    return {"stopped": job_id}


@app.delete("/api/data")
async def clear_data():
    store.clear()
    return {"cleared": True}


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
