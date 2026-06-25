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
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import __version__
from ..core import registry
from ..core.pipeline import Pipeline
from ..storage.live import hub, store

log = logging.getLogger("proinfo.api")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
UPLOAD_DIR = Path("uploads")

# Running jobs: serializable info in _jobs, the asyncio.Task in _tasks.
_jobs: dict[str, dict] = {}
_tasks: dict[str, asyncio.Task] = {}
_job_seq = 0


@asynccontextmanager
async def lifespan(_: FastAPI):
    registry.load_plugins()
    UPLOAD_DIR.mkdir(exist_ok=True)
    log.info("ProInfo API ready (codecs: %s)", sorted(registry.CODECS))
    yield
    for task in _tasks.values():
        task.cancel()


app = FastAPI(title="ProInfo", version=__version__, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


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
    await hub.publish({"type": "job", "data": dict(job)})
    try:
        count = await pipeline.run()
        job.update(status="done", records=count, bytes=pipeline.bytes_in)
    except asyncio.CancelledError:
        job["status"] = "stopped"
        raise
    except Exception as exc:  # noqa: BLE001
        job.update(status="error", error=str(exc))
        log.exception("job %s failed", jid)
    finally:
        await hub.publish({"type": "job", "data": dict(job)})
        _tasks.pop(jid, None)


def _spawn(jid: str, pipeline: Pipeline) -> None:
    _tasks[jid] = asyncio.create_task(_run(jid, pipeline))


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
    return list(_jobs.values())


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


@app.post("/api/listen")
async def listen(body: ListenBody):
    from ..sources.tcp_server import TcpServerSource

    source = TcpServerSource(host=body.host, port=body.port)
    jid = _new_job("listen", codec=body.codec, host=body.host, port=body.port)
    _spawn(jid, Pipeline(source, body.codec, _default_sinks()))
    return {"job": _jobs[jid]}


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
