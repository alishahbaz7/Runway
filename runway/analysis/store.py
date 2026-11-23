"""The analysed logs, kept under data/analysis/<id>/: the file as uploaded (gzipped), its report,
and a small index entry — so a log can be reopened, re-judged with other settings and its rows
browsed after a restart. Deleting an analysis deletes its folder; nothing else refers to it.
"""

from __future__ import annotations

import gzip
import json
import re
import secrets
import shutil
import threading
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from . import analyse as an
from . import compare as cmp
from . import drivelog

_lock = threading.Lock()
_cache: "OrderedDict[str, drivelog.DriveLog]" = OrderedDict()     # the last few logs, parsed
CACHE = 8                                                           # a comparison holds up to 8 at once
_parts: "OrderedDict[tuple, dict]" = OrderedDict()                  # the last few trips' reports
PARTS = 12
root: Path | None = None                                            # set by the API (data/analysis)


def _root() -> Path:
    if root is None:
        raise RuntimeError("analysis store has no root")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _dir(aid: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", aid or ""):
        raise KeyError(aid)
    d = _root() / aid
    if not d.is_dir():
        raise KeyError(aid)
    return d


def _json_default(o):
    if isinstance(o, datetime):
        return o.isoformat()
    raise TypeError(type(o).__name__)


def _entry(aid: str, filename: str, rep: dict, created: str) -> dict:
    m = rep.get("meta", {})
    s = rep.get("summary", {})
    return {"id": aid, "file": filename, "device": m.get("device"), "from": m.get("from"), "to": m.get("to"),
            "first": s.get("first"), "last": s.get("last"), "rows": m.get("rows"), "bytes": m.get("bytes"),
            "created": created, "status": rep.get("status"), "status_word": rep.get("status_word"),
            "issues": rep.get("issues", 0), "kind": rep.get("kind"),
            "areas": [{"id": a["id"], "status": a["status"]} for a in rep.get("areas", [])]}


def _save_report(d: Path, aid: str, filename: str, rep: dict, created: str) -> dict:
    flags = rep.pop("_flags", {})
    (d / "report.json").write_text(json.dumps(rep, default=_json_default), encoding="utf-8")
    (d / "flags.json").write_text(json.dumps({str(k): v for k, v in flags.items()}), encoding="utf-8")
    entry = _entry(aid, filename, rep, created)
    (d / "entry.json").write_text(json.dumps(entry), encoding="utf-8")
    return entry


def add(filename: str, data: bytes, settings: dict | None = None, profiles: list | None = None) -> dict:
    """Read, analyse and keep an uploaded log. Raises ValueError when it is not one we can read."""
    log = drivelog.read(data, filename)
    rep = an.analyse(log, settings, profiles=profiles)
    stamp = datetime.now()
    device = re.sub(r"[^A-Za-z0-9]", "", (log.name or {}).get("device") or "log")[:24] or "log"
    aid = f"{device}-{stamp:%Y%m%d%H%M%S}-{secrets.token_hex(2)}"
    d = _root() / aid
    d.mkdir(parents=True)
    with gzip.open(d / "source.csv.gz", "wb", compresslevel=6) as f:
        f.write(data)
    entry = _save_report(d, aid, filename, rep, stamp.isoformat(timespec="seconds"))
    with _lock:
        _cache[aid] = log
        while len(_cache) > CACHE:
            _cache.popitem(last=False)
    return entry


def entries() -> list[dict]:
    out = []
    for d in sorted(_root().iterdir(), reverse=True):
        try:
            out.append(json.loads((d / "entry.json").read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda e: e.get("created") or "", reverse=True)


def report(aid: str) -> dict:
    d = _dir(aid)
    rep = json.loads((d / "report.json").read_text(encoding="utf-8"))
    rep["entry"] = json.loads((d / "entry.json").read_text(encoding="utf-8"))
    return rep


def flags(aid: str) -> dict[int, list]:
    d = _dir(aid)
    try:
        return {int(k): v for k, v in json.loads((d / "flags.json").read_text(encoding="utf-8")).items()}
    except (OSError, ValueError):
        return {}


def log(aid: str) -> drivelog.DriveLog:
    with _lock:
        if aid in _cache:
            _cache.move_to_end(aid)
            return _cache[aid]
    d = _dir(aid)
    entry = json.loads((d / "entry.json").read_text(encoding="utf-8"))
    with gzip.open(d / "source.csv.gz", "rb") as f:
        lg = drivelog.read(f.read(), entry.get("file") or "")
    with _lock:
        _cache[aid] = lg
        while len(_cache) > CACHE:
            _cache.popitem(last=False)
    return lg


def reanalyse(aid: str, settings: dict | None, profiles: list | None = None) -> dict:
    d = _dir(aid)
    entry = json.loads((d / "entry.json").read_text(encoding="utf-8"))
    rep = an.analyse(log(aid), settings, profiles=profiles)
    _forget_parts(aid)
    return _save_report(d, aid, entry.get("file") or "", rep, entry.get("created") or "")


def _span(start: str, end: str) -> tuple[datetime, datetime]:
    try:
        a, b = datetime.fromisoformat(start), datetime.fromisoformat(end)
    except (TypeError, ValueError):
        raise ValueError("start and end: times like 2026-11-17T07:53:32")
    if b < a:
        raise ValueError("end is before start")
    return a, b


def part(aid: str, start: str, end: str) -> dict:
    """The report on one stretch of a log (a trip), judged with the settings its whole report used.
    Worked out when asked and kept in memory for the last few; never stored."""
    whole = report(aid)
    a, b = _span(start, end)
    key = (aid, a.isoformat(), b.isoformat())
    with _lock:
        if key in _parts:
            _parts.move_to_end(key)
            return _parts[key]
    gen = whole.get("generation") or {}
    fixed = {"values": gen.get("values") or {}, "source": gen.get("source") or {}, "profile": gen.get("profile")}
    rep = an.analyse(log(aid), whole.get("settings"), span=(a, b), fixed=fixed)
    rep.pop("_flags", None)
    rep["entry"] = whole.get("entry", {})
    rep["scope"] = {"start": a.isoformat(), "end": b.isoformat()}
    with _lock:
        _parts[key] = rep
        while len(_parts) > PARTS:
            _parts.popitem(last=False)
    return rep


def compare(ids: list[str]) -> dict:
    """Several logs side by side (devices in one vehicle), each judged over the time all of them cover;
    each whole log when they share none."""
    ids = list(dict.fromkeys(ids))
    if not 2 <= len(ids) <= cmp.MAX_DEVICES:
        raise ValueError(f"Choose 2 to {cmp.MAX_DEVICES} logs to compare")
    wholes = [report(a) for a in ids]                                  # KeyError: no such analysis
    firsts = [w.get("summary", {}).get("first") for w in wholes]
    lasts = [w.get("summary", {}).get("last") for w in wholes]
    if not all(firsts) or not all(lasts):
        raise ValueError("A log without rows cannot be compared")
    start, end = max(firsts), min(lasts)
    common = start < end
    reps, rows = [], []
    for aid, whole in zip(ids, wholes):
        lg = log(aid)
        if common:
            reps.append(part(aid, start, end))
            a, b = datetime.fromisoformat(start), datetime.fromisoformat(end)
            rows.append([r for r in lg.rows if a <= r["t"] <= b])
        else:
            reps.append(whole)
            rows.append(lg.rows)
    secs = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() if common else None
    window = {"common": common, "start": start if common else min(firsts), "end": end if common else max(lasts),
              "seconds": secs}
    return cmp.compare([w.get("entry", {}) for w in wholes], reps, rows, window)


def _forget_parts(aid: str) -> None:
    with _lock:
        for key in [k for k in _parts if k[0] == aid]:
            del _parts[key]


def delete(aid: str) -> None:
    d = _dir(aid)
    with _lock:
        _cache.pop(aid, None)
    _forget_parts(aid)
    shutil.rmtree(d)


# the row view: the fields a person reads first, plus the row's flags
ROW_FIELDS = ["t", "ins", "type", "speed", "lat", "lon", "sats", "gsm", "ign", "supply", "ibatt_v", "ibatt_pct", "history", "alt"]


def rows(aid: str, offset: int = 0, limit: int = 200, flag: str | None = None, type_: str | None = None,
         at: int | None = None, time: str | None = None, start: str | None = None, end: str | None = None) -> dict:
    lg = log(aid)
    fl = flags(aid)
    span = _span(start, end) if start and end else None
    if time and at is None:                      # the first row at or after a moment (a click on the timeline)
        try:
            when = datetime.fromisoformat(time)
            at = next((r["k"] for r in lg.rows if r["t"] >= when), len(lg.rows) - 1)
        except ValueError:
            pass
    keep = range(len(lg.rows))
    if span:                                     # one trip's rows
        keep = [k for k in keep if span[0] <= lg.rows[k]["t"] <= span[1]]
    if flag:
        keep = [k for k in keep if flag in fl.get(k, ())]
    if type_ is not None and type_ != "":
        keep = [k for k in keep if (lg.rows[k].get("type") or "") == type_]
    keep = list(keep)
    if at is not None:
        first = next((i for i, k in enumerate(keep) if k >= at), max(0, len(keep) - 1))
        offset = max(0, first - 20)
    limit = max(1, min(1000, limit))
    page = keep[offset:offset + limit]
    out = []
    for k in page:
        r = lg.rows[k]
        rec = {f: r.get(f) for f in ROW_FIELDS}
        rec["t"] = r["t"].isoformat()
        rec["ins"] = r["ins"].isoformat() if r.get("ins") else None
        rec["delay"] = (r["ins"] - r["t"]).total_seconds() if r.get("ins") else None
        meta = r.get("meta") or {}
        rec["frame"] = meta.get("frameNumber")
        rec["mark"] = meta.get("AISPacketType")
        od = r.get("od") or {}
        rec["alert"] = od.get("PacketType") if od.get("PacketType") not in (None, "", "NR", "IMU") else None
        rec["k"] = k
        rec["flags"] = fl.get(k, [])
        out.append(rec)
    return {"total": len(keep), "offset": offset, "limit": limit, "rows": out}


def row(aid: str, k: int) -> dict:
    lg = log(aid)
    if not 0 <= k < len(lg.rows):
        raise KeyError(k)
    return {"k": k, "flags": flags(aid).get(k, []), "columns": dict(zip(lg.columns, lg.raw[k]))}
