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
import threading
import time
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from logging.handlers import RotatingFileHandler
from pydantic import BaseModel

from .. import __version__
from ..core import registry
from ..core.interfaces import Source
from ..core.models import RawFrame
from ..convert import log_export
from ..core.pipeline import Pipeline
from ..qc import engine as qc_engine
from ..qc import report as qc_report_builder
from ..qc.session import UNKNOWN_DEVICE_IDS, QcCaptureSink
from ..storage.db import DATA_DIR
from ..storage.live import config, db, hub, packet_log, store

log = logging.getLogger("runway.api")

# Static UI. When frozen (PyInstaller), it's bundled under _MEIPASS/runway/web.
if getattr(sys, "frozen", False):
    WEB_DIR = Path(getattr(sys, "_MEIPASS", ".")) / "runway" / "web"
else:
    WEB_DIR = Path(__file__).resolve().parent.parent / "web"
UPLOAD_DIR = Path(DATA_DIR) / "uploads"

# The static UI is re-read from disk on every page load, but __version__ is fixed when the
# process imports it. /api/health reports both, so the page can tell "server not restarted
# after an update" from "stale browser tab" without a version literal of its own. A frozen
# build cannot change under a running process, so there the two are the same.
_INIT_PY = Path(__file__).resolve().parent.parent / "__init__.py"
_VERSION_RE = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']', re.M)


def _code_version() -> str:
    if getattr(sys, "frozen", False):
        return __version__
    try:
        m = _VERSION_RE.search(_INIT_PY.read_text(encoding="utf-8"))
    except OSError:
        return __version__
    return m.group(1) if m else __version__
SIGNATURES_DIR = Path(DATA_DIR) / "signatures"     # local repository of signature files
SIGNATURES_DIR.mkdir(parents=True, exist_ok=True)
PROTOCOLS_DIR = Path(DATA_DIR) / "protocols"       # local repository of protocol definitions
PROTOCOLS_DIR.mkdir(parents=True, exist_ok=True)
QC_PROFILES_DIR = Path(DATA_DIR) / "qc" / "profiles"   # per-protocol QC coverage profiles (user overrides)
# Defaults ship inside the package (like the web assets); data/qc/profiles overrides them.
if getattr(sys, "frozen", False):
    QC_DEFAULTS_DIR = Path(getattr(sys, "_MEIPASS", ".")) / "runway" / "qc" / "profiles"
    # Protocol-definition seed shipped in the exe (see packaging/Runway.spec). Copied into
    # the empty data/protocols repository on first run so the send-command catalogue
    # (ota_commands, e.g. AIS-140 SET:EIP) works from a bare exe — the QC coverage profiles
    # are read straight from QC_DEFAULTS_DIR, but the protocol repo is user-writable so it
    # is SEEDED rather than read-through.
    PROTOCOLS_SEED_DIR = Path(getattr(sys, "_MEIPASS", ".")) / "_defaults" / "protocols"
else:
    QC_DEFAULTS_DIR = Path(__file__).resolve().parent.parent / "qc" / "profiles"
    # Dev: the shipped definitions live in the checkout's own data/protocols. Resolved from the
    # package, NOT from DATA_DIR — with RUNWAY_DATA_DIR pointed elsewhere (or data/ cleared),
    # DATA_DIR/protocols is empty, and the grammars behind the Parser's field names and the
    # send-command catalogue would silently vanish.
    PROTOCOLS_SEED_DIR = Path(__file__).resolve().parents[2] / "data" / "protocols"
_SIG_NAME_RE = re.compile(r"^[A-Za-z0-9._ -]+$")


def _seed_protocols() -> None:
    """First-run seed: if the protocol repository has no definitions yet, copy the ones
    bundled in the exe into it. Keeps a shared exe self-contained (the send-command
    catalogue needs these) and survives a user deleting data/ to reset. A no-op in a dev
    checkout (the seed dir IS the repo) and whenever the repo already has files."""
    import shutil
    try:
        src = PROTOCOLS_SEED_DIR
        if not src.is_dir() or src.resolve() == PROTOCOLS_DIR.resolve():
            return                              # dev checkout: the seed dir IS the repository
        if any(PROTOCOLS_DIR.iterdir()):        # already populated — never overwrite
            return
        for f in src.iterdir():
            if f.is_file():
                shutil.copy2(f, PROTOCOLS_DIR / f.name)
        log.info("seeded protocol repository from %s", src)
    except Exception:
        log.exception("could not seed protocol repository")

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


def _infer_protocol_from_devices(devices_json: str) -> tuple[str, int]:
    """Recover a session's lost protocol identity from its persisted per-device coverage:
    the QC profile whose catalogue contains every coverage key the devices carry is the
    profile that generated them. Heals rows damaged by the pre-1.1.22 self-heal path,
    which saved devices/coverage but never identified_as/packet_count. Returns
    (protocol, total_packets) — ("", 0) when nothing can be inferred."""
    try:
        devices = json.loads(devices_json or "[]")
        keys = {c.get("key") for d in devices for c in (d.get("coverage") or [])}
        packets = sum(d.get("packet_count") or 0 for d in devices)
    except Exception:
        return "", 0
    if not keys:
        return "", 0
    for base in (QC_PROFILES_DIR, QC_DEFAULTS_DIR):
        try:
            profiles = sorted(base.glob("*.json"))
        except Exception:
            continue
        for p in profiles:
            try:
                prof = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            catalog = {i.get("key") for i in prof.get("coverage") or []}
            if catalog and keys <= catalog:
                return prof.get("protocol") or p.stem, packets
    return "", 0


def _qc_reconcile_startup() -> None:
    """Reconcile persisted QC sessions against a fresh process.

    Job ids are per-process (job-1, job-2, ...): after a restart every qc_sessions row's
    job_id points at a dead job — or worse, COLLIDES with a new job that happens to reuse
    the same id, making an old tab show the new job's devices/live feed. And rows still
    marked "running" belong to the previous process; nothing is listening for them now.
    So on boot: clear every row's job_id and flip running -> stopped. Rows that lost
    their protocol identity to the old self-heal bug get it back from their coverage."""
    now = time.time()
    for row in db.qc_sessions():
        updates: dict = {}
        if row.get("job_id"):
            updates["job_id"] = ""
        if row.get("status") == "running":
            updates.update(status="stopped", ended_at=row.get("ended_at") or now)
        if not row.get("identified_as") and row.get("devices_json"):
            proto, packets = _infer_protocol_from_devices(row["devices_json"])
            if proto:
                updates["identified_as"] = proto
                if not row.get("packet_count") and packets:
                    updates["packet_count"] = packets
        if updates:
            db.qc_update(row["id"], **updates)


def _backup_db_daily() -> None:
    """One consistent DB copy per day under data/backups/ (kept 14 days). The DB now
    carries QC business records — device serials, verdicts, lot details, audit — so a
    corrupted or fat-fingered file must never be the only copy."""
    try:
        bdir = Path(DATA_DIR) / "backups"
        dest = bdir / f"runway-{time.strftime('%Y-%m-%d')}.db"
        if not dest.exists():
            db.backup(str(dest))
            log.info("daily DB backup written: %s", dest)
        for old in sorted(bdir.glob("runway-*.db"))[:-14]:
            old.unlink(missing_ok=True)
    except Exception:
        log.exception("daily DB backup failed")


@asynccontextmanager
async def lifespan(_: FastAPI):
    registry.load_plugins()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    _seed_protocols()
    _qc_reconcile_startup()
    _backup_db_daily()
    _autostart_listeners()
    housekeeping = asyncio.create_task(_housekeeping())
    try:
        import openpyxl  # noqa: F401
    except ImportError:                 # surface at boot, not when the operator clicks Download
        log.warning("openpyxl is NOT installed in this Python (%s) — the XLS QC report "
                    "export will fail; run: %s -m pip install openpyxl",
                    sys.executable, sys.executable)
    # Fresh start: the live view begins empty each run. Devices appear only from data fed
    # this session (Parser ingest or live feeds) — we do NOT replay the persisted registry
    # into the live store, so a restart is a clean slate.
    log.info("Runway API ready (codecs: %s) — live view starts empty", sorted(registry.CODECS))
    yield
    housekeeping.cancel()
    for task in [*_tasks.values(), *_replay_tasks.values()]:
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
    relay: str = ""                    # "host:port" of the next server — raw bytes forwarded
    store: bool = False                # keep packets + IMU samples in data/telemetry.db


class ParseBody(BaseModel):
    hex: str                        # hex dump OR raw device text ($-sentences) — auto-detected
    transport: str = "tcp"          # tcp | udp
    protocol: str = "auto"          # registered codec name, or "auto" to detect from the bytes
    signature: str = ""             # signature file to decode CAN with; "" = the device's binding


class IngestBody(BaseModel):
    hex: str
    codec: str = "auto"             # never assume a protocol — detect unless told otherwise
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
    reason: str = ""                    # why (goes on the report's Deviations + audit)


class QcFinalizeBody(BaseModel):
    device_id: str = ""                 # which device to finalize (""=session-level)
    verdict: str = ""                   # "" clears; else pass | conditional_pass | fail
    note: str = ""


# The bench-only report fields, typed into the app instead of into Excel afterwards.
# Whitelisted so the report merge stays predictable (unknown keys are dropped).
QC_LOT_FIELDS = {"lot_id", "lot_size", "pcb_number", "observation",
                 "corrective_action", "approved_by"}
QC_MANUAL_FIELDS = {"mobile_no", "data_on_intouch", "all_io_static", "harness",
                    "battery_charging", "ignition_on_intouch", "ignition_off_intouch",
                    "plugged_alarm_intouch", "cota", "fota", "sms_command",
                    "gprs_command", "serial_command", "power_ma_without_ign",
                    "power_ma_with_ign", "field_testing", "remarks"}


class QcLotBody(BaseModel):
    lot: dict = {}                      # session-level lot fields (QC_LOT_FIELDS)
    device_id: str = ""                 # optional: per-device manual values follow
    fields: dict = {}                   # per-device manual fields (QC_MANUAL_FIELDS)


# --------------------------------------------------------------------------- #
# Job plumbing
# --------------------------------------------------------------------------- #
def _default_sinks():
    return [registry.get_sink("memory_store"), registry.get_sink("ws_broadcast")]


# --------------------------------------------------------------------------- #
# Signature bindings — a vehicle signature bound to a device decodes that device's CAN
# frames into named engineering values, on EVERY path (live listener, QC session, replay,
# pasted data), because the enrichment runs in the pipeline before any sink sees the record.
#
# Stored as {device_id -> signature name} in settings, with "*" as the any-device default, so
# a bench with one vehicle needs one binding and a mixed bench binds per IMEI.
# --------------------------------------------------------------------------- #
BINDINGS_KEY = "signature_bindings"


def _bindings() -> dict:
    b = db.get_setting(BINDINGS_KEY, {}) or {}
    return b if isinstance(b, dict) else {}


def _signature_for(device_id: str | None) -> tuple[str, dict] | None:
    """The signature bound to this device (its own binding wins over the "*" default)."""
    binds = _bindings()
    name = binds.get(device_id or "") or binds.get("*")
    if not name:
        return None
    p = SIGNATURES_DIR / name
    if not p.is_file():
        return None
    try:
        return name, json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        log.warning("signature %s is not readable/valid JSON — no CAN signals decoded", name)
        return None


def _decode_can_signals(rec, signature: dict | None, sig_name: str = "") -> None:
    """Attach decoded CAN signals to a record, in place. No-op unless the record carries CAN
    frames and the signature is CAN-id keyed (an AVL-keyed one belongs to the Parser's AVL
    view, not to a bus frame)."""
    from ..convert import can_signals

    frames = (rec.io or {}).get("can")
    if not isinstance(frames, list) or not frames:
        return
    signals = can_signals.decode_frames(frames, signature)
    if not signals:
        return
    rec.io["can_signals"] = signals
    rec.io["can_signal_count"] = len(signals)
    if sig_name:
        rec.io["can_signature"] = sig_name


def _enricher():
    """The pipeline's per-record hook: decode CAN with whatever signature that device is bound
    to. Looked up per record (not once at start) so binding a signature takes effect on the
    next packet — no restart, no relisten."""
    def enrich(rec) -> None:
        bound = _signature_for(rec.device_id)
        if bound:
            _decode_can_signals(rec, bound[1], bound[0])
    return enrich


@app.get("/api/signature-bindings")
async def get_signature_bindings():
    """{device_id -> signature name}; "*" is the any-device default."""
    return {"bindings": _bindings(),
            "signatures": [p.name for p in sorted(SIGNATURES_DIR.glob("*.json"))]}


@app.put("/api/signature-bindings")
async def put_signature_bindings(body: dict):
    """Replace the binding map. A device mapped to "" (or absent) is unbound."""
    raw = body.get("bindings") if isinstance(body, dict) else None
    if not isinstance(raw, dict):
        raise HTTPException(400, "bindings must be an object of device id -> signature name")
    names = {p.name for p in SIGNATURES_DIR.glob("*.json")}
    out = {}
    for dev, name in raw.items():
        dev, name = str(dev).strip(), str(name or "").strip()
        if not dev or not name:
            continue
        if name not in names:
            raise HTTPException(400, f"unknown signature '{name}'")
        out[dev] = name
    db.set_setting(BINDINGS_KEY, out)
    log.info("signature bindings updated: %s", out or "(none)")
    return {"bindings": out, "saved": True}


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
        links = src.relay_status() if hasattr(src, "relay_status") else {}
        job["clients"] = [
            {"peer": p, "records": per.get(p, {}).get("records", 0),
             "bytes": per.get(p, {}).get("bytes", 0), "first": per.get(p, {}).get("first", 0),
             "last": per.get(p, {}).get("last", 0),
             "connected": p in connected, "relay": links.get(p)}
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
# When a Runway page last checked in. Every page polls /api/health every 4 s (shell.js), so after
# a restart an open tab shows up here within seconds — the launcher reads it (?probe=1, which
# does not count as a page) and opens a browser tab only when none is open, instead of one more
# tab per restart.
_page_seen_at = 0.0


@app.get("/api/health")
async def health(probe: bool = False):
    global _page_seen_at
    if not probe:
        _page_seen_at = time.time()
    # deps: optional-at-import packages the UI warns about BEFORE they fail at use time
    # (e.g. openpyxl missing meant the XLS report 500'd only when the operator clicked
    # Download — on the wrong Python env that's the worst possible moment to find out)
    try:
        import openpyxl  # noqa: F401
        has_openpyxl = True
    except ImportError:
        has_openpyxl = False
    return {
        "status": "ok",
        "version": __version__,
        "code_version": _code_version(),
        "codecs": len(registry.CODECS),
        "devices": len(store.devices),
        "subscribers": hub.subscribers,
        "mode": _activity_mode(),
        "deps": {"openpyxl": has_openpyxl},
        "page_seen_s": round(time.time() - _page_seen_at, 1) if _page_seen_at else None,
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


_HEX_CHARS = set("0123456789abcdefABCDEF")
_HEX_SEPARATORS = set(" \t\r\n:-")      # spaced / colon / dash separated dumps are still hex


# A codec that genuinely recognises a stream returns real confidence from detect(); the
# generic-hex fallback deliberately returns a floor value so it never outranks a real match.
# Below this, nothing actually claimed the data.
_MIN_CONFIDENCE = 0.05


def _looks_hex(text: str) -> bool:
    """Is this input a hex dump rather than device text? (whitespace/colon/dash separated)"""
    body = "".join(ch for ch in (text or "") if ch not in _HEX_SEPARATORS)
    return bool(body) and all(ch in _HEX_CHARS for ch in body)


def _input_bytes(text: str) -> bytes:
    """The Parser input accepts a hex dump, the device's own text, OR a platform message-log
    export (``<time> | <imei> | ,"MMI,...*37 | null"`` rows).

    Which one it is, is decided from the content, not from a setting: an export is unwrapped
    back into the stream the server received (``convert/log_export.py``); if everything left
    after dropping the usual separators is a hex digit, it's a dump; otherwise it is text off the
    wire (``$MMI,...``, ``$GPRMC,...``) and is taken as ASCII bytes verbatim. Filtering text down
    to its hex-looking characters — the old behaviour — silently shredded every ASCII protocol.
    """
    if not text or not text.strip():
        raise HTTPException(400, "nothing to parse")
    exported = log_export.unwrap(text)
    if exported is not None:
        return exported
    if _looks_hex(text):
        body = "".join(ch for ch in text if ch not in _HEX_SEPARATORS)
        if len(body) % 2:
            raise HTTPException(400, "hex has an odd number of nibbles")
        return bytes.fromhex(body)
    # Line-oriented protocols (NMEA) frame on the line terminator, which a paste usually
    # loses — restore it so the last sentence isn't left looking like a partial message.
    if not text.endswith(("\n", "\r")):
        text += "\r\n"
    return text.encode("utf-8", "surrogateescape")


def _explain_enricher(signature: str):
    """The record hook for a breakdown: an explicitly chosen signature applies to every record;
    otherwise each record gets whatever signature its own device is bound to."""
    name = (signature or "").strip()
    if not name:
        return _enricher()
    p = SIGNATURES_DIR / name
    if not p.is_file():
        raise HTTPException(400, f"unknown signature '{name}'")
    try:
        content = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(400, f"signature '{name}' is not valid JSON: {exc}")
    return lambda rec: _decode_can_signals(rec, content, name)


def _explain_walk(raw: bytes, transport: str, protocol: str, from_hex: bool = True,
                  signature: str = ""):
    """Resolve the codec (explicit or auto-detected) and return ``(name, walk)``.

    The only decision made here is *which codec*; how a protocol is broken down is the codec's
    business — its own :meth:`Codec.explain` if it has something bit-level to add, otherwise the
    grammar-driven generic walk. No protocol names appear in this function on purpose.

    ``from_hex`` says how the input arrived. A hex dump nothing recognises is still worth
    showing as bytes (that is what the generic-hex codec is for), but *text* nothing recognises
    is almost always a mistake — a typo'd dump, or a protocol with no codec yet — so it is
    reported instead of being passed off as a decode. Forcing a protocol always wins over both.
    """
    from ..codecs import generic_explain
    from ..core import registry

    registry.load_plugins()
    name = (protocol or "auto").strip()
    if name and name != "auto":
        if name not in registry.CODECS:
            raise HTTPException(400, f"unknown protocol '{name}'")
        codec = registry.CODECS[name]()
    else:
        best, conf = None, 0.0
        for cls in registry.CODECS.values():
            try:
                c = cls().detect(raw)
            except Exception:
                c = 0.0
            if c > conf:
                best, conf = cls, c
        if conf < _MIN_CONFIDENCE and not from_hex:
            raise HTTPException(400, "no registered protocol recognised this text — check the "
                                     "paste, or pick a protocol to force one")
        codec = (best or registry.CODECS["generic_hex"])()
        log.info("parse: auto-detected codec '%s' (confidence %.2f)", codec.name, conf)
    walk = codec.explain(raw, transport)
    if walk is None:
        walk = generic_explain.walk(raw, codec, _protocol_grammar(codec.name), transport,
                                    _explain_enricher(signature))
    return codec.name, walk


def _protocol_grammar(name: str) -> dict | None:
    """The codec's declarative grammar, if one is published for it.

    The user's repository wins (they can edit or extend a grammar in Configuration → Protocols);
    the shipped default is the fallback, so field names work on a fresh install, in a checkout
    with a custom data dir, and after someone clears data/ — with or without the first-run seed.
    """
    for d in (PROTOCOLS_DIR, PROTOCOLS_SEED_DIR):
        p = d / f"{name}.json"
        if not p.is_file():
            continue
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            log.warning("parse: %s is not readable/valid JSON — breakdown falls back to "
                        "decoded fields", p)
    return None


def _collect(walk, total_bytes: int, transport: str, protocol: str) -> dict:
    """Drain an explain walk into the Parser's result shape."""
    out = {"ok": True, "transport": transport, "protocol": protocol, "packets": [],
           "records": [], "skipped": 0, "skips": [], "error": None}
    for kind, payload, _off in walk:
        if kind == "packet":
            out["packets"].append(payload)
            out["records"].extend(payload.get("records", []))
        elif kind == "skip":
            out["skipped"] += 1
            if len(out["skips"]) < 200:
                out["skips"].append(payload)
        elif kind == "error":
            out["error"] = payload
    out["ok"] = out["error"] is None
    out["consumed"] = total_bytes
    out["total"] = len(out["packets"]) + out["skipped"]
    return out


@app.post("/api/parse")
async def parse(body: ParseBody):
    """Stateless bytes -> field-by-field breakdown for the Parser UI, for ANY registered
    protocol: the input may be a hex dump or the device's own text, and the codec is the one
    asked for or the one auto-detected from the bytes."""
    raw = _input_bytes(body.hex)
    if not raw:
        raise HTTPException(400, "no bytes to parse")
    transport = body.transport if body.transport in ("tcp", "udp") else "tcp"
    name, walk = _explain_walk(raw, transport, body.protocol, _looks_hex(body.hex),
                               body.signature)
    result = _collect(walk, len(raw), transport, name)
    _log_skips("parse", result.get("skips", []), result.get("skipped", 0))
    if result.get("error"):
        log.warning("parse: error=%s (%d bytes, %s)", result["error"], len(raw), name)
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
    raw = _input_bytes(body.hex)
    if not raw:
        raise HTTPException(400, "no bytes to parse")
    transport = body.transport if body.transport in ("tcp", "udp") else "tcp"
    protocol, walk = _explain_walk(raw, transport, body.protocol, _looks_hex(body.hex),
                                   body.signature)
    total_bytes = len(raw)

    def gen():
        out = {"ok": True, "transport": transport, "protocol": protocol, "packets": [],
               "records": [], "skipped": 0, "skips": [], "error": None}
        last, logged = 0, 0
        for kind, payload, off in walk:
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
    """Decode pasted data through the real codec into the live store, so parsed devices show
    on the Dashboard (the Parser calls this alongside /api/parse). Same hex-or-text input rule
    as /api/parse, and the codec defaults to auto-detection rather than to any one protocol."""
    raw = _input_bytes(body.hex)
    if not raw:
        raise HTTPException(400, "no bytes to ingest")
    pipeline = Pipeline(_BytesSource(raw, body.peer), body.codec, _default_sinks(), _enricher())
    count = await pipeline.run()
    return {"records": count, "bytes": pipeline.bytes_in}


@app.get("/api/devices")
async def devices():
    return list(store.devices.values())


# --------------------------------------------------------------------------- #
# IMU — read back what a storing listener kept in data/telemetry.db. A sample's time comes
# from its packet's time and the packet interval for its ignition state (the device spreads 20
# samples over that interval — see storage/telemetry.py): measured from the packets, else the
# device's IMUPKT values set here. Changing a setting re-times every stored sample.
# --------------------------------------------------------------------------- #
IMU_TIMING_KEY = "imu_timing"


class ImuTimingBody(BaseModel):
    ign_on_s: float = 10.0             # SET IMUPKT:1,<ignOnSec>,<ignOffSec> — default 1,10,2
    ign_off_s: float = 2.0
    measure: bool = True               # use the packets' own spacing when there are enough
    anchor: str = "last"               # which sample the packet time marks


def _imu_timing() -> dict:
    from ..storage.telemetry import TIMING_DEFAULT

    saved = db.get_setting(IMU_TIMING_KEY, {}) or {}
    return {k: saved.get(k, v) for k, v in TIMING_DEFAULT.items()}


@app.get("/api/imu/devices")
async def imu_devices():
    from ..storage.telemetry import telemetry
    return await asyncio.get_running_loop().run_in_executor(None, telemetry.devices)


@app.get("/api/imu/series")
async def imu_series(device: str, start: Optional[float] = None, end: Optional[float] = None,
                     points: int = 1200):
    """Samples of one device in ``[start, end]`` (epoch seconds) — the last 15 minutes of
    its data when no range is given."""
    from ..storage.telemetry import telemetry

    loop = asyncio.get_running_loop()
    if start is None or end is None:
        known = {d["device_id"]: d for d in await loop.run_in_executor(None, telemetry.devices)}
        if device not in known:
            raise HTTPException(404, f"no stored data for {device}")
        end = known[device]["last"] + 1
        start = end - 900
    if end <= start:
        raise HTTPException(400, "end must be after start")
    timing = _imu_timing()
    return await loop.run_in_executor(None, lambda: telemetry.imu_series(
        device, start, end, points, timing))


# --------------------------------------------------------------------------- #
# Packet archive — every stored packet, one file per device per day (storage/archive.py):
# list the device-days, download one, or replay one to a platform.
# --------------------------------------------------------------------------- #
STORE_SINKS = ("telemetry_store", "archive_store")     # what a listener's Store switch adds
_replays: dict[str, dict] = {}
_replay_tasks: dict[str, asyncio.Task] = {}
_replay_seq = 0


class ArchiveReplayBody(BaseModel):
    device: str
    day: str                           # YYYY-MM-DD
    target: str                        # host:port of the platform
    rate: float = 50.0                 # packets per second; 0 = as fast as the link takes them


# Retention — what a 24x7 listener keeps. IMU samples and decoded packets are the bulk (~3.6 KB
# an IMU packet: 0.6-3 GB a day for 20 devices); their per-minute summaries are ~1,000x smaller
# and keep long ranges drawable after the samples go; the archive is the packets themselves,
# gzipped, kept until a limit is set (0 = forever).
RETENTION_KEY = "storage_retention"
RETENTION_DEFAULT = {"samples_days": 7, "minute_days": 365, "archive_days": 0}
_housekeeping_last: dict[str, Any] = {}


class RetentionBody(BaseModel):
    samples_days: int = 7
    minute_days: int = 365
    archive_days: int = 0


def _retention() -> dict:
    saved = db.get_setting(RETENTION_KEY, {}) or {}
    return {k: int(saved.get(k, v)) for k, v in RETENTION_DEFAULT.items()}


_housekeeping_busy = threading.Lock()


def _housekeep_once() -> dict | None:
    """Gzip finished archive days, then drop what is past retention. Blocking: run it in an
    executor. None when a run is already going (the hourly one and a settings change)."""
    import os
    from ..storage.archive import archive
    from ..storage.telemetry import telemetry

    if not _housekeeping_busy.acquire(blocking=False):
        return None
    try:
        keep = _retention()
        out: dict[str, Any] = {"at": time.time(), "compressed": archive.compress_finished()}
        if os.path.exists(telemetry.path):       # never create the database just to empty it
            out["telemetry"] = telemetry.purge(keep["samples_days"], keep["minute_days"])
        out["archive_files"] = archive.purge(keep["archive_days"]) if keep["archive_days"] else 0
        out["took_s"] = round(time.time() - out["at"], 2)
        _housekeeping_last.clear()
        _housekeeping_last.update(out)
        return out
    finally:
        _housekeeping_busy.release()


async def _housekeeping() -> None:
    """At start-up and every hour after."""
    while True:
        try:
            out = await asyncio.get_running_loop().run_in_executor(None, _housekeep_once)
            gone = out and sum((out.get("telemetry") or {}).values()) + out["archive_files"]
            if out and (out["compressed"] or gone):
                log.info("housekeeping: %s", out)
        except Exception:
            log.exception("housekeeping failed")
        await asyncio.sleep(3600)


def _storage_view() -> dict:
    import os
    import shutil
    from ..storage.archive import archive
    from ..storage.outbox import outbox
    from ..storage.telemetry import telemetry

    return {"retention": _retention(), "data_dir": DATA_DIR,
            "free": shutil.disk_usage(DATA_DIR).free if os.path.isdir(DATA_DIR) else None,
            "sizes": {"telemetry": telemetry.size(), "archive": archive.size(),
                      "outbox": outbox.size(), "outbox_queued": outbox.total},
            "last_housekeeping": dict(_housekeeping_last) or None}


@app.get("/api/storage")
async def storage_view():
    return await asyncio.get_running_loop().run_in_executor(None, _storage_view)


@app.put("/api/storage/retention")
async def storage_retention(body: RetentionBody):
    if not 1 <= body.samples_days <= 365:
        raise HTTPException(400, "samples_days must be 1-365")
    if not body.samples_days <= body.minute_days <= 3650:
        raise HTTPException(400, "minute_days must be at least samples_days and at most 3650")
    if not 0 <= body.archive_days <= 3650:
        raise HTTPException(400, "archive_days must be 0 (keep forever) to 3650")
    db.set_setting(RETENTION_KEY, body.model_dump())
    loop = asyncio.get_running_loop()
    # A shorter limit applies now, not in an hour — in the background: cutting a month of IMU
    # samples to a week deletes hundreds of millions of rows.
    loop.run_in_executor(None, _housekeep_once)
    return await loop.run_in_executor(None, _storage_view)


@app.get("/api/archive")
async def archive_days():
    from ..storage.archive import archive
    return await asyncio.get_running_loop().run_in_executor(None, archive.days)


@app.get("/api/archive/replays")
async def archive_replays():
    return sorted(_replays.values(), key=lambda r: r["started"], reverse=True)


@app.get("/api/archive/{device}/{day}")
async def archive_download(device: str, day: str):
    from ..storage.archive import archive

    path = archive.file_for(device, day)
    if path is None:
        raise HTTPException(404, f"no archive for {device} on {day}")
    return FileResponse(path, filename=path.name)


@app.post("/api/archive/replay")
async def archive_replay(body: ArchiveReplayBody):
    """Send one archived device-day to a platform: one connection, the device's login first,
    every packet in its original order, exactly as received, at ``rate`` packets per second."""
    global _replay_seq
    from ..sources.tcp_server import parse_hostport
    from ..storage.archive import archive

    try:
        host, port = parse_hostport(body.target)
    except ValueError as exc:
        raise HTTPException(400, f"target: {exc}")
    if archive.file_for(body.device, body.day) is None:
        raise HTTPException(404, f"no archive for {body.device} on {body.day}")
    if body.rate < 0:
        raise HTTPException(400, "rate must be 0 (as fast as possible) or more")
    _replay_seq += 1
    rid = f"replay-{_replay_seq}"
    _replays[rid] = {"id": rid, "device": body.device, "day": body.day, "target": f"{host}:{port}",
                     "rate": body.rate, "status": "running", "started": time.time(), "ended": None,
                     "total": None, "sent": 0, "bytes_up": 0, "bytes_down": 0, "error": None}
    _replay_tasks[rid] = asyncio.create_task(_run_replay(rid, host, port))
    log.info("archive replay %s: %s %s -> %s:%d at %s/s", rid, body.device, body.day, host, port,
             body.rate or "max")
    return _replays[rid]


@app.post("/api/archive/replays/{rid}/stop")
async def archive_replay_stop(rid: str):
    task = _replay_tasks.get(rid)
    if task is None:
        raise HTTPException(404, "replay not running")
    task.cancel()
    return {"stopped": rid}


async def _run_replay(rid: str, host: str, port: int) -> None:
    from ..storage.archive import archive, replay_lines

    st = _replays[rid]
    loop = asyncio.get_running_loop()
    writer, rx = None, None
    try:
        lines = await loop.run_in_executor(None, replay_lines, archive, st["device"], st["day"])
        st["total"] = len(lines)
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 10)

        async def replies() -> None:              # the platform's ACKs/commands: counted, not used
            while data := await reader.read(4096):
                st["bytes_down"] += len(data)
        rx = asyncio.create_task(replies())
        t0 = loop.time()
        for line in lines:
            writer.write(line.raw)
            await writer.drain()
            st["sent"] += 1
            st["bytes_up"] += len(line.raw)
            if st["rate"]:                         # on schedule, not sleep-per-packet: Windows
                ahead = t0 + st["sent"] / st["rate"] - loop.time()   # timers are ~15 ms coarse
                if ahead > 0.02:
                    await asyncio.sleep(ahead)
        await asyncio.sleep(1.0)                   # let the last replies land
        st["status"] = "done"
    except asyncio.CancelledError:
        st["status"] = "stopped"
    except Exception as exc:  # noqa: BLE001
        st.update(status="error", error=f"{type(exc).__name__}: {exc}")
        log.warning("archive replay %s failed: %s", rid, st["error"])
    finally:
        if rx is not None:
            rx.cancel()
        if writer is not None:
            writer.close()
        st["ended"] = time.time()
        _replay_tasks.pop(rid, None)


@app.get("/api/imu/timing")
async def imu_timing():
    return _imu_timing()


@app.put("/api/imu/timing")
async def set_imu_timing(body: ImuTimingBody):
    for name, v in (("ignition ON interval", body.ign_on_s), ("ignition OFF interval", body.ign_off_s)):
        if not 2 <= v <= 3600:          # the device allows 2 s at the least (20 x 100 ms)
            raise HTTPException(400, f"{name} must be between 2 and 3600 s")
    if body.anchor not in ("first", "last"):
        raise HTTPException(400, "anchor must be 'first' or 'last'")
    db.set_setting(IMU_TIMING_KEY, body.model_dump())
    return _imu_timing()


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
    _spawn(jid, Pipeline(source, codec, _default_sinks(), _enricher()))
    return {"job": _jobs[jid]}


@app.post("/api/replay-path")
async def replay_path(body: ReplayPathBody):
    if not Path(body.path).exists():
        raise HTTPException(404, f"file not found: {body.path}")
    from ..sources.file_replay import FileReplaySource

    source = FileReplaySource(body.path, fmt=body.fmt, delay=body.delay)
    jid = _new_job("replay", file=body.path, codec=body.codec)
    _spawn(jid, Pipeline(source, body.codec, _default_sinks(), _enricher()))
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
            "relay": config.get("default_relay", ""),
            "store": bool(config.get("default_store", False)),
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

    source = TcpServerSource(host=job["host"], port=job["port"], on_event=_on_conn,
                             routes=_routes_of(job))
    sinks = _default_sinks()
    if job.get("store"):
        sinks.extend(registry.get_sink(n) for n in STORE_SINKS)
    _spawn(jid, Pipeline(source, job["codec"], sinks, _enricher()))


# A listener that relays or stores is a service, not a bench session: devices keep dialling in
# and the next server depends on it, so it must come back by itself after a server restart.
# Kept as {port: config} in settings; stopping or removing the session forgets it.
AUTOSTART_KEY = "autostart_listeners"


def _routes_of(job: dict) -> dict:
    """A listener's relay rules: where all devices go, and each device's own target / "off"."""
    return job.get("routes") or {"all": job.get("relay") or "", "devices": {}}


def _autostart_save(job: dict) -> None:
    saved = db.get_setting(AUTOSTART_KEY, {}) or {}
    routes = _routes_of(job)
    if routes["all"] or routes["devices"] or job.get("store"):
        saved[str(job["port"])] = {**{k: job.get(k) for k in ("codec", "host", "port", "store")},
                                   "relay": routes["all"], "routes": routes}
    else:
        saved.pop(str(job["port"]), None)
    db.set_setting(AUTOSTART_KEY, saved)


def _autostart_forget(job: dict | None) -> None:
    if not job or job.get("kind") != "listen":
        return
    saved = db.get_setting(AUTOSTART_KEY, {}) or {}
    if saved.pop(str(job.get("port")), None) is not None:
        db.set_setting(AUTOSTART_KEY, saved)


def _autostart_listeners() -> None:
    """Server start: bring back every relaying / storing listener that was running."""
    for cfg in (db.get_setting(AUTOSTART_KEY, {}) or {}).values():
        jid = _new_job("listen", started=time.time(), peers=[], autostarted=True, **cfg)
        try:
            _spawn_listener(jid)
            log.info("listener on :%s restarted (relay %s, store %s)", cfg.get("port"),
                     cfg.get("relay") or "off", "on" if cfg.get("store") else "off")
        except Exception as exc:  # noqa: BLE001 — one bad entry must not stop the server
            _jobs[jid].update(status="error", error=str(exc))
            log.exception("could not restart listener on :%s", cfg.get("port"))


def _check_target(value: str, listen_port: int, label: str = "relay") -> str:
    """Validate one relay target; refuse one that points back at this very listener."""
    from ..sources.tcp_server import parse_hostport

    value = (value or "").strip()
    if not value:
        return ""
    try:
        host, port = parse_hostport(value)
    except ValueError as exc:
        raise HTTPException(400, f"{label}: {exc}")
    if port == listen_port and host in ("127.0.0.1", "localhost", "0.0.0.0", "::1", _local_ip()):
        raise HTTPException(400, f"{label} points back at this listener — that would loop forever")
    return f"{host}:{port}"


def _check_relay(body: ListenBody) -> str:
    return _check_target(body.relay, body.port)


class RoutesBody(BaseModel):
    all: str = ""                      # "host:port" every device goes to; "" = none
    devices: dict[str, str] = {}       # device id -> "host:port" | "off"; absent = follows "all"


class StoreBody(BaseModel):
    store: bool


def _listen_job(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.get("kind") != "listen":
        raise HTTPException(400, "only listener sessions relay or store")
    return job


@app.put("/api/listen/{job_id}/routes")
async def set_listen_routes(job_id: str, body: RoutesBody):
    """Change where devices are relayed while the session runs: connected devices follow at
    once (a moved device's login is replayed to its new server first)."""
    job = _listen_job(job_id)
    routes = {"all": _check_target(body.all, job["port"], "all devices"), "devices": {}}
    for dev, target in body.devices.items():
        dev, target = dev.strip(), (target or "").strip()
        if not dev or not target:
            continue
        routes["devices"][dev] = ("off" if target.lower() == "off"
                                  else _check_target(target, job["port"], dev))
    job["routes"], job["relay"] = routes, routes["all"]
    pipe = _pipelines.get(job_id)
    if pipe is not None and hasattr(pipe.source, "set_routes"):
        await pipe.source.set_routes(routes)
    if job_id in _tasks:
        _autostart_save(job)
    log.info("listener :%s relay rules: all -> %s, %d device rule(s)", job["port"],
             routes["all"] or "none", len(routes["devices"]))
    return {"job": _job_view(job_id)}


@app.put("/api/listen/{job_id}/store")
async def set_listen_store(job_id: str, body: StoreBody):
    """Start or stop keeping this session's packets + IMU samples, without restarting it."""
    job = _listen_job(job_id)
    job["store"] = body.store
    pipe = _pipelines.get(job_id)
    if pipe is not None:
        # Swap the list, never edit it in place: the pipeline may be mid-way through it.
        have = [s for s in pipe.sinks if s.name in STORE_SINKS]
        if body.store and not have:
            added = [registry.get_sink(n) for n in STORE_SINKS]
            for sink in added:
                await sink.start()
            pipe.sinks = [*pipe.sinks, *added]
        elif not body.store and have:
            pipe.sinks = [s for s in pipe.sinks if s.name not in STORE_SINKS]
            for s in have:
                await s.close()
    if job_id in _tasks:
        _autostart_save(job)
    return {"job": _job_view(job_id)}


@app.post("/api/listen")
async def listen(body: ListenBody):
    registry.load_plugins()
    if body.codec != "auto" and body.codec not in registry.CODECS:
        raise HTTPException(400, f"unknown codec '{body.codec}'")
    relay = _check_relay(body)

    jid = _new_job("listen", codec=body.codec, host=body.host, port=body.port,
                   relay=relay, routes={"all": relay, "devices": {}}, store=body.store,
                   started=time.time(), peers=[])
    try:
        _spawn_listener(jid)
    except Exception as exc:
        _jobs[jid].update(status="error", error=str(exc))
        raise HTTPException(500, f"could not start listener: {exc}")
    _autostart_save(_jobs[jid])

    # Remember settings for next time.
    config.set("default_listen_host", body.host)
    config.set("default_listen_port", body.port)
    config.set("default_codec", body.codec)
    config.set("default_relay", relay)
    config.set("default_store", body.store)
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
    _autostart_save(job)
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
    _autostart_forget(job)                    # stopped by hand: don't bring it back on restart
    task.cancel()
    return {"stopped": job_id}


@app.delete("/api/jobs/{job_id}")
async def remove_job(job_id: str):
    """Drop a finished/stopped job from the list (must not be running)."""
    if job_id in _tasks:
        raise HTTPException(400, "stop the job before removing it")
    _autostart_forget(_jobs.get(job_id))
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
# Only these verdicts are worth remembering in the durable qc_devices registry — writing
# "in_progress" there would clobber a returning device's actual last result while its new
# session is still running (see qc_device_touch's docstring).
_FINAL_VERDICTS = {"pass", "conditional_pass", "fail", "timeout"}


def _jloads_dict(s: str) -> dict:
    try:
        v = json.loads(s or "{}")
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def _jloads_list(s: str) -> list:
    try:
        v = json.loads(s or "[]")
        return v if isinstance(v, list) else []
    except Exception:
        return []


def _qc_audit(qc_id: int, row: dict, action: str, **fields) -> None:
    """Append one operator-action entry to the session's audit trail (who skipped or
    finalized what, when, and why) — the report's Deviations section reads it, so a
    Conditional Pass is always accountable."""
    entries = _jloads_list(row.get("audit_json"))
    entries.append({"at": time.time(), "action": action,
                    "operator": row.get("operator") or "", **fields})
    db.qc_update(qc_id, audit_json=json.dumps(entries))


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


def _qc_last_packet(recs) -> dict:
    """Snapshot of a device's most recent traffic, for the Summary "Device details":
    the newest record's type/alert + when it LANDED on the utility, plus the newest
    position (lat/lon, satellites, fix) and battery voltages. Position and battery each
    fall back to the newest record that actually carries them — e.g. a health packet has
    no GPS fix, and a login packet has no voltages, so "the latest packet" alone would
    blank those groups out whenever housekeeping traffic arrives last."""
    last = recs[-1]
    io = last.io or {}
    out: dict = {
        "message_type": last.message_type or "",
        "alert_id": io.get("alert_id"),
        "alarms": list(last.alarms or []),
        "packet_status": io.get("packet_status") or "",
        "received_at": last.received_at.isoformat() if last.received_at else None,
    }
    for r in reversed(recs):
        if r.position is not None and r.position.latitude is not None:
            at = r.event_time or r.received_at
            out["position"] = {"lat": r.position.latitude, "lon": r.position.longitude,
                               "satellites": r.satellites, "gps_valid": r.gps_valid,
                               "at": at.isoformat() if at else None}
            break
    for r in reversed(recs):
        if r.external_v is not None or r.battery_v is not None:
            out["battery"] = {"external_v": r.external_v, "internal_v": r.battery_v}
            break
    return out


def _qc_devices(records, catalog, profile, skipped_map, finalized_map, started_at, now,
                qc_id: Optional[int] = None, ledger=None) -> list:
    """One entry per physical device (keyed by IMEI) with coverage + verdict + identity —
    a reconnect (new peer) merges into the same device rather than adding a row.

    Coverage/vitals come from the sink's incremental **ledger** when one is supplied —
    the records list is a bounded window and must never be the source of truth for the
    verdict (a long session would forget its own history). The window-based fallback
    remains for callers without a ledger (rebuilds, legacy tests).

    Also touches the durable qc_devices registry for every device seen: assigns/looks up
    its stable serial number (#6 — same IMEI always gets the same number, a genuinely new
    IMEI gets the next one) and, once this device's verdict is FINAL, records it there so a
    future connection of the same IMEI (in any session, even after a restart) immediately
    shows its last known QC status (#7) instead of requiring a manual search."""
    from collections import OrderedDict
    groups: "OrderedDict[str, list]" = OrderedDict()
    for r in records:
        did = r.device_id
        if did and did not in UNKNOWN_DEVICE_IDS:
            groups.setdefault(did, []).append(r)
    device_ids = list(groups)
    if ledger is not None:
        device_ids = ledger.device_ids() or device_ids
    stale = (profile or {}).get("stale_timeout_s")
    out = []
    for did in device_ids:
        recs = groups.get(did) or []
        vit = ledger.vitals(did) if ledger is not None else {}
        if ledger is not None and vit:
            cov = ledger.coverage(did, catalog) if catalog else []
            last = vit["last_recv_epoch"]
            n_packets = vit["packet_count"]
            peers = vit["peers"]
            fw, vend = vit["firmware_version"], vit["vendor_id"]
            last_packet = vit["last_packet"]
        else:                                    # window fallback (no ledger supplied)
            cov = qc_engine.coverage(recs, catalog) if catalog else []
            last = _qc_last_recv_epoch(recs)
            n_packets = len(recs)
            peers = sorted({r.peer for r in recs if r.peer})
            fw = vend = ""
            for r in recs:
                io = r.io or {}
                fw = io.get("firmware_version") or fw
                vend = io.get("vendor_id") or vend
            last_packet = _qc_last_packet(recs) if recs else {}
        checks, checks_verdict = qc_engine.evaluate(recs, {"device_id": did, "started_at": started_at})
        meta = {"packet_count": n_packets, "now": now, "last_packet_epoch": last,
                "stale_timeout_s": stale, "skipped": skipped_map.get(did) or [],
                "finalized_verdict": finalized_map.get(did) or ""}
        verdict = _device_verdict(cov, checks_verdict, meta)

        # (#6/#7) get-or-create the serial + only persist FINAL verdicts into the registry
        is_final = verdict in _FINAL_VERDICTS
        reg = db.qc_device_touch(did, protocol=(profile or {}).get("protocol") or "",
                                 qc_id=(qc_id if is_final else None), verdict=(verdict if is_final else None))
        # "prior" is only meaningful while it's from a DIFFERENT (older) session than this
        # one — once this session's own final verdict lands in the registry, there's
        # nothing older left to show.
        has_prior = bool(reg["last_qc_id"]) and reg["last_qc_id"] != qc_id
        out.append({
            "device_id": did, "packet_count": n_packets,
            "coverage": cov, "checks": checks,
            "verdict": verdict,
            "idle_s": (now - last) if last else None, "last_seen": last,
            "peers": peers,
            "firmware_version": fw, "vendor_id": vend,
            "skipped": skipped_map.get(did) or [],
            "finalized_verdict": finalized_map.get(did) or "",
            "serial": reg["serial"], "first_seen_ever": reg["first_seen"],
            "prior_qc_id": reg["last_qc_id"] if has_prior else None,
            "prior_verdict": reg["last_verdict"] if has_prior else "",
            "last_packet": last_packet,
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
                              row.get("started_at"), now, qc_id=row.get("id"),
                              ledger=getattr(sink, "ledger", None))
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
    # how this protocol's raw bytes should be shown (from the QC profile) — the UI uses
    # this instead of hardcoding protocol names; "" = unknown (UI falls back to sniffing)
    row["raw_view"] = (_qc_profile(row.get("identified_as") or "") or {}).get("raw_view") or ""
    row["lot"] = _jloads_dict(row.get("lot_json"))            # lot details typed in-app
    row["manual"] = _jloads_dict(row.get("manual_json"))      # {device_id: manual columns}
    row["audit"] = _jloads_list(row.get("audit_json"))        # skip/finalize history
    row["reports"] = _jloads_list(row.get("reports_json"))    # printed-report log
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
        # Persist identity + counters too (row carries the live sink's values when the
        # sink is still around) — without them a self-healed session came back with
        # identified_as=""/packet_count=0, losing its protocol (ASCII/HEX raw view, the
        # report's QC profile) even though its devices/coverage were saved.
        db.qc_update(qc_id, status=new_status, ended_at=ended,
                     identified_as=row.get("identified_as") or "",
                     device_id=row.get("device_id") or "",
                     packet_count=row.get("packet_count") or 0,
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
    # Identity already persisted for this session (protocol + device id). Kept so the
    # identity is written to the DB the MOMENT it's learned from the wire — not only on a
    # clean stop. A session that ends without one (crash, restart, dead listener) must not
    # come back as identified_as="" — that would make the Summary/report lose the
    # protocol's raw view (ASCII vs HEX) and the report its QC profile.
    persisted = {"identified_as": row.get("identified_as") or "",
                 "device_id": row.get("device_id") or ""}
    persist_last = {"t": 0.0}

    def _on_update(snap: dict) -> None:
        ident = snap.get("identified_as") or ""
        did = snap.get("device_id") or ""
        if (ident and ident != persisted["identified_as"]) or (did and did != persisted["device_id"]):
            persisted["identified_as"] = ident or persisted["identified_as"]
            persisted["device_id"] = did or persisted["device_id"]
            db.qc_update(qc_id, identified_as=persisted["identified_as"],
                         device_id=persisted["device_id"])
        # Durable summary while RUNNING (throttled): devices/coverage/count used to be
        # persisted only on a clean Stop, so a crash or server restart blanked the
        # stopped card until the session was resumed. Now the snapshot lands every few
        # seconds, so the summary survives any kind of interruption.
        now_m = time.monotonic()
        if now_m - persist_last["t"] >= 5.0:
            persist_last["t"] = now_m
            try:
                row2 = dict(db.qc_session(qc_id) or {})
                row2["identified_as"] = persisted["identified_as"] or row2.get("identified_as", "")
                comp = _qc_compute(row2, qc_sink)
                db.qc_update(qc_id, packet_count=qc_sink.count,
                             checks_json=json.dumps(comp["checks"]),
                             coverage_json=json.dumps(comp["coverage"]),
                             devices_json=json.dumps(comp["devices"]), verdict=comp["verdict"])
            except Exception:
                log.exception("qc %s: periodic summary persist failed", qc_id)
        loop.create_task(_qc_publish(qc_id))

    qc_sink = QcCaptureSink(row["capture_path"], started_at=row["started_at"], on_update=_on_update,
                            catalog_loader=lambda proto: (_qc_profile(proto) or {}).get("coverage"))
    # Same-session retention: re-decode this session's own capture file (append-only
    # across resumes) so a resume/restart carries on with the full Summary — packets,
    # coverage, device details — instead of starting blank. New session => no file yet.
    try:
        qc_sink.rehydrate(row.get("identified_as") or codec)
    except Exception:
        log.exception("qc %s: could not rehydrate capture %s", qc_id, row.get("capture_path"))
    qc_sink.count = max(qc_sink.count, initial_count)   # cumulative across resumes
    _qc_sinks[qc_id] = qc_sink

    async def _on_conn(kind: str, peer: str) -> None:
        db.qc_update(qc_id, peer=peer if kind == "up" else "")
        await _qc_publish(qc_id)

    source = TcpServerSource(host=host, port=port, on_event=_on_conn)
    jid = _new_job("qc", codec=codec, host=host, port=port, started=time.time(), peers=[])
    db.qc_update(qc_id, job_id=jid)
    _spawn(jid, Pipeline(source, codec, [*_default_sinks(), qc_sink], _enricher()))


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
    before = set(m.get(body.device_id or "") or [])
    after = set(body.keys)
    # Only items the profile marks skippable may be waived — mandatory items cannot be
    # skipped (enforced here, not just hidden in the UI). Validate only NEWLY-ADDED keys:
    # skips recorded before this rule existed are grandfathered so they stay removable
    # (rejecting the whole list would lock the operator out of unticking anything).
    # No catalog (unknown protocol) -> no restriction, so a missing profile never blocks.
    catalog = (_qc_profile(row.get("identified_as") or "") or {}).get("coverage") or []
    if catalog:
        skippable = {c["key"] for c in catalog if c.get("skippable")}
        bad = sorted(k for k in (after - before) if k not in skippable)
        if bad:
            raise HTTPException(400, f"these items are mandatory and cannot be skipped: {', '.join(bad)}")
    m[body.device_id or ""] = sorted(after)
    db.qc_update(qc_id, skipped_json=json.dumps(m))
    if after != before:                              # audit the change, with the why
        _qc_audit(qc_id, row, "skip", device_id=body.device_id,
                  added=sorted(after - before), removed=sorted(before - after),
                  reason=body.reason or "")
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
    _qc_audit(qc_id, row, "finalize", device_id=body.device_id,
              verdict=v, reason=body.note or "")
    await _qc_publish(qc_id)
    return _qc_view(qc_id)


@app.post("/api/qc/{qc_id}/lot")
async def qc_lot(qc_id: int, body: QcLotBody):
    """Save the bench-only report fields typed into the app (lot details + per-device
    manual columns) so the exported workbook is the COMPLETE deliverable — no editing
    the Excel by hand afterwards. Unknown keys are dropped (whitelisted merge)."""
    row = db.qc_session(qc_id)
    if row is None:
        raise HTTPException(404, "QC session not found")
    updates: dict = {}
    if body.lot:
        lot = _jloads_dict(row.get("lot_json"))
        lot.update({k: str(v) for k, v in body.lot.items() if k in QC_LOT_FIELDS})
        updates["lot_json"] = json.dumps(lot)
    if body.device_id and body.fields:
        manual = _jloads_dict(row.get("manual_json"))
        dev = manual.setdefault(body.device_id, {})
        dev.update({k: str(v) for k, v in body.fields.items() if k in QC_MANUAL_FIELDS})
        updates["manual_json"] = json.dumps(manual)
    if updates:
        db.qc_update(qc_id, **updates)
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


@app.get("/api/qc/{qc_id}/export")
async def qc_export(qc_id: int, fmt: str = "xlsx", devices: str = ""):
    """The per-lot QC report workbook: Sheet 1 "Summary" (one row per device) + one sheet
    per selected device, titled with its IMEI, carrying the Summary view's coverage detail.

    fmt=xlsx (default) -> Excel workbook.
    fmt=csv            -> ZIP of CSVs, one per would-be sheet (CSV has no sheets).
    devices            -> comma-separated device ids (IMEIs) to include; empty = all.
    """
    if fmt not in ("xlsx", "csv"):
        raise HTTPException(400, "fmt must be xlsx or csv")
    view = _qc_view(qc_id)
    all_devs = view.get("devices") or []
    if not all_devs:
        raise HTTPException(400, "no identified devices on this QC session yet")
    wanted = {d for d in devices.split(",") if d}
    devs = [d for d in all_devs if d.get("device_id") in wanted] if wanted else all_devs
    if not devs:
        raise HTTPException(400, "none of the requested devices exist on this session")
    # The report is the lot's FINAL deliverable — an in-progress device can't be printed.
    # The operator marks its missing required items as Skip (-> Conditional Pass), waits
    # for the test to settle, or unticks the device to leave it out.
    incomplete = qc_report_builder.incomplete_devices(devs)
    if incomplete:
        names = ", ".join(d.get("device_id") or "?" for d in incomplete)
        raise HTTPException(409, f"QC is not completed for {names} — mark the missing "
                                 "required items as Skip (device closes as Conditional "
                                 "Pass) or untick the device; only completed devices can "
                                 "be printed")
    profile = _qc_profile(view.get("identified_as") or "")
    if fmt == "xlsx":
        try:
            data = qc_report_builder.build_xlsx(view, devs, profile)
        except ImportError:
            raise HTTPException(500, "openpyxl is not installed on the server — "
                                     "pip install openpyxl (or use fmt=csv)")
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        fname = qc_report_builder.report_filename(view, profile, "xlsx")
    else:
        data = qc_report_builder.build_csv_zip(view, devs, profile)
        mime = "application/zip"
        fname = qc_report_builder.report_filename(view, profile, "zip")
    # Archive the exact printed bytes: the report is a QC record — a regeneration after
    # later edits must never silently replace what was actually handed over. Every print
    # lands in data/reports/ and in the session's printed-report log (shown in the UI).
    try:
        import hashlib
        rdir = Path(DATA_DIR) / "reports"
        rdir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        apath = rdir / f"qc{qc_id}-{stamp}-{len(devs)}dev.{'xlsx' if fmt == 'xlsx' else 'zip'}"
        apath.write_bytes(data)
        row = db.qc_session(qc_id) or {}
        entries = _jloads_list(row.get("reports_json"))
        entries.append({"at": time.time(), "fmt": fmt, "file": str(apath),
                        "devices": [d.get("device_id") for d in devs],
                        "sha256": hashlib.sha256(data).hexdigest()})
        db.qc_update(qc_id, reports_json=json.dumps(entries))
    except Exception:
        log.exception("qc %s: could not archive the printed report", qc_id)
    return Response(content=data, media_type=mime,
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


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
    has retained for this device_id (runway/storage/live.py's LiveStore is a process-wide
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

    reg = db.qc_device_registry(device_id) or {}
    return {"device_id": device_id, "protocol": protocol, "records_seen": len(records),
            "device_info": device_info, "alarms": alarms,
            "serial": reg.get("serial"), "first_seen_ever": reg.get("first_seen"),
            "last_verdict": reg.get("last_verdict") or "", "last_qc_id": reg.get("last_qc_id")}


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
