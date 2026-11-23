"""A device's live packets read as a drive log, so the Dashboard's map is Log analysis's map.

The live store keeps each device's latest records (UnifiedRecord dicts); ``log_of`` turns them into
the rows ``drivelog.read`` makes of a platform export — device time, arrival, position, speed,
ignition, live or history, the AIS-140 alert — and ``track`` runs the one ``analyse`` on them. Trips,
data lost, asleep, stale positions, jumps and driving events are judged exactly as for a log; only
what the map draws is sent back. Pure: no I/O.
"""

from __future__ import annotations

from datetime import datetime

from .analyse import analyse
from .drivelog import DriveLog

# the export's columns these rows stand for — the analysis's column table reads them
COLUMNS = ["timestampStr", "insertTimeStr", "latitude", "longitude", "speed", "numberOfSatellites", "gsmlevel",
           "digitalInput1", "powerSupplyVoltage", "internalBatteryVoltage", "history", "altitude", "otherData",
           "firmwareVersion"]


def _local(iso) -> datetime | None:
    """A record's time (UTC, ISO) → the local wall-clock time a drive log carries (naive)."""
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.astimezone().replace(tzinfo=None) if d.tzinfo else d


def _txt(v) -> str:
    return "" if v is None else str(v)


def log_of(records: list[dict], device: str = "") -> tuple[DriveLog, list]:
    """The records as a DriveLog in device-time order, and each row's record ``seq`` (by row number k).
    A record without a device time cannot be placed in the log and is left out."""
    rows, raw = [], []
    for i, rec in enumerate(records):
        t = _local(rec.get("event_time"))
        if t is None:
            continue
        pos = rec.get("position") or {}
        io = rec.get("io") or {}
        status = io.get("packet_status")
        ptype, aid = io.get("packet_type"), io.get("alert_id")
        ign = rec.get("ignition")
        r = {"i": i, "t": t, "ins": _local(rec.get("received_at")),
             "lat": pos.get("latitude"), "lon": pos.get("longitude"), "speed": rec.get("speed_kph"),
             "sats": rec.get("satellites"), "gsm": rec.get("gsm_signal"),
             "ign": None if ign is None else int(bool(ign)),
             "supply": None if rec.get("external_v") is None else rec["external_v"] * 1000,     # mV, as the export
             "ibatt_v": rec.get("battery_v"), "alt": rec.get("altitude_m"),
             "history": status == "H" if status in ("L", "H") else None,
             "firmware": io.get("firmware_version") or "", "seq": rec.get("seq")}
        r["meta"] = {k: v for k, v in (("AISPacketType", status if status in ("L", "H") else None),
                                       ("frameNumber", _txt(io.get("frame_number")))) if v}
        if ptype:
            alert = "" if aid is None else str(int(aid)) if str(aid).isdigit() else str(aid)
            r["other"] = f"PacketType={ptype}, alertId={alert}"
            r["od"] = {"PacketType": ptype, "alertId": alert}
        rows.append(r)
        raw.append([_txt(rec.get("event_time")), _txt(rec.get("received_at")), _txt(r["lat"]), _txt(r["lon"]),
                    _txt(r["speed"]), _txt(r["sats"]), _txt(r["gsm"]), _txt(r["ign"]), _txt(r["supply"]),
                    _txt(r["ibatt_v"]), _txt(r["history"]).lower(), _txt(r["alt"]), r.get("other", ""), r["firmware"]])
    order = sorted(range(len(rows)), key=lambda k: (rows[k]["t"], k))
    rows = [rows[k] for k in order]
    raw = [raw[k] for k in order]
    for k, r in enumerate(rows):
        r["k"] = k
    return DriveLog(rows=rows, columns=list(COLUMNS), raw=raw, name={"device": device}), [r["seq"] for r in rows]


def track(device: str, records: list[dict], settings: dict | None = None, profiles: list[dict] | None = None) -> dict:
    """What the map draws for one device: its track, breaks, stale positions and jumps (``series``), its
    trips and driving events (``driving``), the overspeed limit, and the record behind each row (``seqs``)."""
    log, seqs = log_of(records, device)
    if not log.rows:
        return {"device": device, "rows": 0, "series": {}, "driving": {}, "settings": {}, "seqs": []}
    rep = analyse(log, settings, profiles=profiles)
    series = rep.get("series") or {}
    driving = rep.get("driving") or {}
    return {"device": device, "rows": len(log.rows),
            "series": {k: series.get(k) for k in ("track", "breaks", "stale", "jumps", "dups", "start", "end")},
            "driving": {"trips": driving.get("trips") or [], "events": driving.get("events") or []},
            "settings": {"overspeed_kmh": (rep.get("settings") or {}).get("overspeed_kmh")},
            "seqs": seqs}
