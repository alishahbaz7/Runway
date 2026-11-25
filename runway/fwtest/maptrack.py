"""A firmware test's packets on Log analysis's map.

The gate keeps each packet's device time, arrival, position, speed, ignition, live or history, frame
number and AIS-140 type and alert. ``records`` hands them over as the records the Dashboard's map reads
(``analysis.live.log_of``), so the one ``analyse`` judges the test's track exactly as it judges a drive
log or a device on the Dashboard — trips, data lost, asleep, stale positions, jumps, duplicates. Every
packet with a device time is on it; ``seqs`` gives back each row's packet number. Pure: no I/O.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..analysis import live


def _iso(epoch) -> str | None:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat() if epoch else None


def _num(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def records(rows: list[dict]) -> list[dict]:
    """The gate's rows as UnifiedRecord-shaped dicts (only what the map reads); ``seq`` is the packet number."""
    out = []
    for r in rows:
        if r.get("t") is None:
            continue                       # a login or bytes that are no packet: no device time to place it by
        vals = r.get("vals") or {}
        ign = r.get("ign")
        out.append({
            "seq": r["n"], "event_time": _iso(r["t"]), "received_at": _iso(r.get("received")),
            "position": {"latitude": r.get("lat"), "longitude": r.get("lon")},
            "speed_kph": r.get("speed"), "satellites": _num(vals.get("satellites")),
            "gsm_signal": _num(vals.get("gsm_signal")),
            "ignition": None if ign not in ("0", "1") else ign == "1",
            "io": {"packet_status": r.get("status"), "packet_type": r.get("type"), "alert_id": r.get("alert"),
                   "frame_number": r.get("frame")},
        })
    return out


def track(rows: list[dict], device: str = "", settings: dict | None = None,
          profiles: list[dict] | None = None) -> dict:
    """What the map draws for the test: as ``analysis.live.track``, ``seqs`` holding packet numbers."""
    return live.track(device, records(rows), settings, profiles)
