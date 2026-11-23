"""Log analysis — the routes the Log analysis page uses. The work is in ``runway/analysis/``.

    GET    /api/analysis                 the analysed logs (newest first) and the current settings
    POST   /api/analysis                 upload a log (multipart "file"): read, analyse, keep
    GET    /api/analysis/settings        the settings and limits the checks use
    PUT    /api/analysis/settings        change the settings new analyses use
    PUT    /api/analysis/profiles        the device profiles: expected interval per mode, by device or firmware
    GET    /api/analysis/{id}            its report
    GET    /api/analysis/{id}/part       the report on one trip of it (start=, end=: the trip's times)
    POST   /api/analysis/{id}/analyse    judge it again with other settings
    GET    /api/analysis/{id}/rows       its rows, a page at a time (flag=, type=, at=row, time=, start= end=)
    GET    /api/analysis/{id}/rows/{k}   one row, every column as exported
    GET    /api/analysis/{id}/report.xlsx  the report as an Excel workbook (start= end=: one trip's)
    DELETE /api/analysis/{id}            delete it (the uploaded file and its report)
    POST   /api/analysis/delete          delete several ({"ids": [...]}) or every one ({"all": true})
    GET    /api/analysis/compare         several logs side by side (ids=a&ids=b …), over the time they share
    GET    /api/analysis/compare.xlsx    the same as an Excel workbook
    GET    /api/live/track               the Dashboard's map: devices' live packets analysed as a log is
                                         (device=a&device=b …) — the same track, trips, gaps and events
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from ..analysis import analyse as an
from ..analysis import excel, store
from ..analysis import modes as md
from ..storage.db import DATA_DIR

router = APIRouter()
store.root = Path(DATA_DIR) / "analysis"
SETTINGS_KEY = "analysis_settings"
PROFILES_KEY = "analysis_profiles"
MAX_PROFILES = 100
MAX_BYTES = 300 * 1024 * 1024


def _settings() -> dict:
    from ..storage.live import config
    saved = config.get(SETTINGS_KEY, {}) or {}
    return {k: float(saved.get(k, v)) for k, v in an.SETTINGS.items()}


def _profiles() -> list[dict]:
    from ..storage.live import config
    saved = config.get(PROFILES_KEY, []) or []
    return [md.clean_profile(p) for p in saved if isinstance(p, dict)]


def _get(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except KeyError:
        raise HTTPException(404, "No such analysis")
    except ValueError as e:
        raise HTTPException(400, str(e))


class SettingsBody(BaseModel):
    settings: dict


@router.get("/api/analysis")
async def analysis_list():
    return {"items": await asyncio.to_thread(store.entries), "settings": _settings(), "defaults": an.SETTINGS,
            "profiles": _profiles()}


# ── the Dashboard's map: live packets, analysed as a log ──────────────────────────────────────────
LIVE_DEVICES = 30                   # the most devices one map asks for at once
LIVE_RECORDS = 1000                 # the live store keeps this many per device
_live_cache: dict[str, tuple] = {}  # device → (its records' first and last seq, count, settings, the track)


@router.get("/api/live/track")
async def live_track(device: List[str] = Query(default=[])):
    """Each device's live packets (the latest the live store keeps) read as a drive log and analysed by
    the same ``analyse`` as Log analysis — its track, trips, data lost, asleep, stale positions, jumps and
    driving events, and the record behind each point. Worked out again only when a device sent more."""
    from ..analysis import live as lv
    from ..storage.live import store as live_store
    settings, profiles = _settings(), _profiles()
    key_s = repr(sorted(settings.items()))
    out = []
    for d in list(dict.fromkeys(x for x in device if x))[:LIVE_DEVICES]:
        recs = live_store.device_records(d, LIVE_RECORDS)
        key = (recs[0]["seq"] if recs else 0, recs[-1]["seq"] if recs else 0, len(recs), key_s)
        hit = _live_cache.get(d)
        if hit and hit[0] == key:
            out.append(hit[1])
            continue
        t = await asyncio.to_thread(lv.track, d, recs, settings, profiles)
        if len(_live_cache) > 200:
            _live_cache.clear()
        _live_cache[d] = (key, t)
        out.append(t)
    return {"devices": out}


@router.get("/api/analysis/settings")
async def analysis_settings():
    return {"settings": _settings(), "defaults": an.SETTINGS, "limits": an.LIMITS, "profiles": _profiles()}


@router.put("/api/analysis/settings")
async def analysis_settings_put(body: SettingsBody):
    from ..storage.live import config
    clean = {}
    for k, v in (body.settings or {}).items():
        if k not in an.SETTINGS:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise HTTPException(400, f"{k}: a number")
        if f < 0:
            raise HTTPException(400, f"{k}: not negative")
        clean[k] = f
    config.set(SETTINGS_KEY, clean)
    return {"settings": _settings()}


class ProfilesBody(BaseModel):
    profiles: list[dict]


@router.put("/api/analysis/profiles")
async def analysis_profiles_put(body: ProfilesBody):
    from ..storage.live import config
    clean = []
    for p in body.profiles[:MAX_PROFILES]:
        c = md.clean_profile(p)
        if not (c["name"] or c["devices"] or c["firmware"]):
            continue                             # an empty row
        if not (c["devices"] or c["firmware"]):
            raise HTTPException(400, f"{c['name']}: name the devices or the firmware it is for")
        clean.append(c)
    config.set(PROFILES_KEY, clean)
    return {"profiles": clean}


@router.post("/api/analysis")
async def analysis_add(file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(400, "The file is empty")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "The file is larger than 300 MB")
    try:
        entry = await asyncio.to_thread(store.add, file.filename or "log.csv", data, _settings(), _profiles())
    except ValueError as e:
        raise HTTPException(400, str(e))
    from ..usage import count
    count("logsAnalysed")
    return entry


class DeleteBody(BaseModel):
    ids: list[str] = []
    all: bool = False


@router.post("/api/analysis/delete")
async def analysis_delete_many(body: DeleteBody):
    ids = [e["id"] for e in await asyncio.to_thread(store.entries)] if body.all else body.ids
    deleted = []
    for aid in ids:
        try:
            await asyncio.to_thread(store.delete, aid)
            deleted.append(aid)
        except KeyError:
            pass                                 # already gone, or not one of ours: nothing to do
    return {"deleted": deleted}


@router.get("/api/analysis/compare")
async def analysis_compare(ids: List[str] = Query(default=[])):
    from ..usage import count
    out = await asyncio.to_thread(_get, store.compare, ids)
    count("logsCompared")
    return out


@router.get("/api/analysis/compare.xlsx")
async def analysis_compare_xlsx(ids: List[str] = Query(default=[])):
    c = await asyncio.to_thread(_get, store.compare, ids)
    data = await asyncio.to_thread(excel.comparison, c)
    devs = "-".join(str(d.get("device") or "") for d in c["devices"])[:80]
    name = f"Runway comparison {devs} {(c['window'].get('start') or '')[:10]}.xlsx".replace(":", "-")
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/api/analysis/{aid}")
async def analysis_report(aid: str):
    return await asyncio.to_thread(_get, store.report, aid)


@router.get("/api/analysis/{aid}/part")
async def analysis_part(aid: str, start: str, end: str):
    return await asyncio.to_thread(_get, store.part, aid, start, end)


class AnalyseBody(BaseModel):
    settings: Optional[dict] = None


@router.post("/api/analysis/{aid}/analyse")
async def analysis_again(aid: str, body: AnalyseBody):
    s = {**_settings(), **{k: v for k, v in (body.settings or {}).items() if k in an.SETTINGS}}
    return await asyncio.to_thread(_get, store.reanalyse, aid, s, _profiles())


@router.get("/api/analysis/{aid}/rows")
async def analysis_rows(aid: str, offset: int = 0, limit: int = 200, flag: Optional[str] = None,
                        type: Optional[str] = None, at: Optional[int] = None, time: Optional[str] = None,
                        start: Optional[str] = None, end: Optional[str] = None):
    return await asyncio.to_thread(_get, store.rows, aid, offset, limit, flag, type, at, time, start, end)


@router.get("/api/analysis/{aid}/rows/{k}")
async def analysis_row(aid: str, k: int):
    return await asyncio.to_thread(_get, store.row, aid, k)


@router.get("/api/analysis/{aid}/report.xlsx")
async def analysis_xlsx(aid: str, start: Optional[str] = None, end: Optional[str] = None, trip: Optional[int] = None):
    if start and end:
        rep = await asyncio.to_thread(_get, store.part, aid, start, end)
    else:
        rep = await asyncio.to_thread(_get, store.report, aid)
    data = await asyncio.to_thread(excel.workbook, rep)
    e = rep.get("entry", {})
    what = f"trip {trip} " if start and end and trip else ""
    name = f"Runway analysis {e.get('device') or aid} {what}{(e.get('first') or '')[:10]}.xlsx".replace(":", "-")
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.delete("/api/analysis/{aid}")
async def analysis_delete(aid: str):
    await asyncio.to_thread(_get, store.delete, aid)
    return {"deleted": aid}
