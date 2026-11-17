"""The scorecard — every figure that tells a device's data apart, one row each.

One log's own scorecard judges each row against its limit (the analysis LIMITS and settings:
Good, Attention, Poor) and shows the limit beside it; a comparison puts the same rows side by side
for several devices and marks the best and worst of each (compare.py). A new measure is a METRICS
row; a limit for it, a JUDGE row.
"""

from __future__ import annotations

from typing import Any, Callable


def _mode(rep: dict, mode: str, field: str):
    for g in (rep.get("generation") or {}).get("modes", []):
        if g["mode"] == mode:
            return g.get(field)
    return None


def _g(*path) -> Callable[[dict], Any]:
    def get(rep):
        v = rep
        for p in path:
            if not isinstance(v, dict):
                return None
            v = v.get(p)
        return v
    return get


# key → (group, label, unit, better, read from a report): better is "high", "low", "100" (closest to
# 100 — packets made of due) or None (a fact, not compared). A new measure is a row here — it shows
# in the comparison and, judged by JUDGE where it has a limit, in every log's own scorecard.
METRICS: list[tuple[str, str, str, str, str | None, Callable[[dict], Any]]] = [
    ("rows", "Data", "Packets", "", None, _g("summary", "rows")),
    ("step", "Data", "Usual step", "s", None, _g("summary", "step_s")),
    ("coverage", "Data", "Ignition-on time covered", "%", "high", _g("summary", "coverage_pct")),
    ("loss", "Data", "Lost with ignition on", "min", "low",
     lambda r: round((r.get("summary") or {}).get("loss_s", 0) / 60, 1)),
    ("frames_lost", "Data", "Frame numbers missing", "%", "low",
     lambda r: r["frames"].get("lost_pct") if (r.get("frames") or {}).get("numbered") else None),
    ("active_enough", "Modes", "Active · packets made of due", "%", "100", lambda r: _mode(r, "active", "sufficiency_pct")),
    ("active_on_time", "Modes", "Active · steps on time", "%", "high", lambda r: _mode(r, "active", "on_time_pct")),
    ("active_early", "Modes", "Active · steps early", "%", "low", lambda r: _mode(r, "active", "early_pct")),
    ("active_late", "Modes", "Active · steps late", "%", "low", lambda r: _mode(r, "active", "late_pct")),
    ("active_seen", "Modes", "Active · interval seen", "s", None, lambda r: _mode(r, "active", "seen_s")),
    ("active_missed", "Modes", "Active · packets missed (estimated)", "", "low", lambda r: _mode(r, "active", "missed")),
    ("active_history", "Modes", "Active · history packets", "%", "low", lambda r: _mode(r, "active", "history_pct")),
    ("standby_enough", "Modes", "Standby · packets made of due", "%", "100", lambda r: _mode(r, "standby", "sufficiency_pct")),
    ("standby_on_time", "Modes", "Standby · steps on time", "%", "high", lambda r: _mode(r, "standby", "on_time_pct")),
    ("standby_early", "Modes", "Standby · steps early", "%", "low", lambda r: _mode(r, "standby", "early_pct")),
    ("standby_late", "Modes", "Standby · steps late", "%", "low", lambda r: _mode(r, "standby", "late_pct")),
    ("standby_seen", "Modes", "Standby · interval seen", "s", None, lambda r: _mode(r, "standby", "seen_s")),
    ("standby_missed", "Modes", "Standby · packets missed (estimated)", "", "low", lambda r: _mode(r, "standby", "missed")),
    ("standby_history", "Modes", "Standby · history packets", "%", "low", lambda r: _mode(r, "standby", "history_pct")),
    ("sleep_enough", "Modes", "Sleep · packets made of due", "%", "100", lambda r: _mode(r, "sleep", "sufficiency_pct")),
    ("sleep_on_time", "Modes", "Sleep · steps on time", "%", "high", lambda r: _mode(r, "sleep", "on_time_pct")),
    ("sleep_early", "Modes", "Sleep · steps early", "%", "low", lambda r: _mode(r, "sleep", "early_pct")),
    ("sleep_late", "Modes", "Sleep · steps late", "%", "low", lambda r: _mode(r, "sleep", "late_pct")),
    ("sleep_seen", "Modes", "Sleep · interval seen", "s", None, lambda r: _mode(r, "sleep", "seen_s")),
    ("sleep_missed", "Modes", "Sleep · packets missed (estimated)", "", "low", lambda r: _mode(r, "sleep", "missed")),
    ("sleep_history", "Modes", "Sleep · history packets", "%", "low", lambda r: _mode(r, "sleep", "history_pct")),
    ("delay_median", "Delivery", "Delay median", "s", "low", _g("delivery", "latency", "p50")),
    ("delay_p90", "Delivery", "Delay 90%", "s", "low", _g("delivery", "latency", "p90")),
    ("delay_max", "Delivery", "Slowest", "s", "low", _g("delivery", "latency", "max")),
    ("late", "Delivery", "Arrived after a newer one", "%", "low", _g("delivery", "late_pct")),
    ("very_late", "Delivery", "Over 10 min late", "", "low", _g("delivery", "very_late")),
    ("history", "Delivery", "History packets", "%", None, _g("delivery", "history_pct")),
    ("no_fix", "GPS", "No satellites", "%", "low", _g("gps", "no_fix_pct")),
    ("sats", "GPS", "Satellites, median", "", "high", lambda r: _sats_median(r)),
    ("stale_jumps", "GPS", "Jumps and stale positions", "", "low",
     lambda r: len((r.get("gps") or {}).get("stale") or []) + len((r.get("gps") or {}).get("jumps") or [])),
    ("phantom", "GPS", "Distance that never happened", "km", "low", _g("gps", "phantom_km")),
    ("frozen", "GPS", "Repeated positions while moving", "%", "low", _g("gps", "frozen_pct")),
    ("speed_mismatch", "GPS", "Speed differs from the positions", "%", "low", _g("gps", "speed_mismatch_pct")),
    ("apart_median", "Agreement", "Apart from the first device, median", "m", "low", _g("_extra", "apart_median_m")),
    ("apart_p90", "Agreement", "Apart from the first device, 90%", "m", "low", _g("_extra", "apart_p90_m")),
    ("speed_diff", "Agreement", "Speed differs from the first device, median", "km/h", "low", _g("_extra", "speed_diff_median")),
    ("distance", "Driving", "Distance driven", "km", None, _g("driving", "distance_km")),
    ("trips", "Driving", "Trips", "", None, lambda r: len((r.get("driving") or {}).get("trips") or [])),
    ("top_speed", "Driving", "Top speed", "km/h", None, _g("driving", "max_speed")),
    ("events", "Driving", "Driving events", "", None, lambda r: len((r.get("driving") or {}).get("events") or [])),
    ("resting", "Power", "Battery, ignition off", "V", None, _g("power", "resting")),
    ("running", "Power", "Supply, ignition on", "V", None, _g("power", "running")),
    ("cuts", "Power", "Main battery missing", "", "low", lambda r: len((r.get("power") or {}).get("cuts") or [])),
    ("restarts", "Device", "Restarts", "", "low", lambda r: len((r.get("device") or {}).get("restarts") or [])),
    ("alerts", "Device", "Alerts", "", None, _g("device", "alerts_total")),
    ("gsm", "Network", "Signal (GSM), median", "", "high", _g("network", "gsm_median")),
    ("cell_missing", "Network", "No cell id", "%", "low", _g("network", "missing_pct")),
]




MODE_WORDS = {
    "active": "Active — the ignition on",
    "standby": "Standby — the ignition off, the device still awake",
    "sleep": "Sleep — after standby (or the deep-sleep packet), the slowest rate",
}
_MODE_ABOUT = {
    "enough": "packets made against those due (the time in the mode divided by its interval, plus one for each stretch). "
              "100% is exact; below means packets missing, above means extra packets.",
    "on_time": "steps between one packet and the next that fall within the margin of the interval (Settings: the margin "
               "grows with the interval).",
    "early": "steps shorter than the interval less the margin — packets sent before their time.",
    "late": "steps longer than the interval plus the margin, up to one and a half intervals — packets sent after their "
            "time (longer silences count as missed).",
    "seen": "the interval seen most often between one packet and the next.",
    "missed": "packets that should have come during silences longer than one and a half intervals.",
    "history": "packets the platform marked history — kept on the device and sent later.",
}
# key → what the measure is and what it is used for (the scorecard's and the comparison's hold-tip)
ABOUT: dict[str, str] = {
    "rows": "Packets in the log (or the trip): every row the platform stored.",
    "step": "The interval seen most often between one packet and the next, whatever the mode.",
    "coverage": "The share of the time with the ignition on that has data; the rest fell in gaps longer than the gap setting.",
    "loss": "Time with the ignition on and no packets for longer than a gap — data lost on the way, or never sent.",
    "frames_lost": "Frame numbers the device used that never reached this log. A hole that repeats at a fixed spacing is "
                   "another packet type using the counter, not loss.",
    "delay_median": "Half the packets landed on InTouch within this time of being made (stored time less device time).",
    "delay_p90": "Nine in ten packets landed on InTouch within this time of being made.",
    "delay_max": "The longest any packet took to land on InTouch.",
    "late": "Packets stored after one the device made later — they arrived out of order.",
    "very_late": "Packets stored more than 10 minutes after the device made them.",
    "history": "Packets marked history — kept on the device while it could not send, and sent later.",
    "no_fix": "Packets with no satellites in view; their position cannot be trusted.",
    "sats": "The number of satellites in view in half the packets or more.",
    "stale_jumps": "Positions off the track: a stale fix (no satellites, an old position) or a hop faster than the jump setting.",
    "phantom": "Distance the track would gain from jumps, and from drift while parked — distance that never happened.",
    "frozen": "Packets while moving that repeat the position before — the position did not update.",
    "speed_mismatch": "Hops where the reported speed and the speed worked out from the positions differ by more than the setting.",
    "apart_median": "How far this device put the vehicle from the first device at the same moment — half the time within this.",
    "apart_p90": "How far this device put the vehicle from the first device at the same moment — nine times in ten within this.",
    "speed_diff": "The reported speed against the first device's at the same moment — the median difference.",
    "distance": "Distance driven, from the positions, without jumps and drift.",
    "trips": "Stretches of driving; a stop longer than the trip setting ends one.",
    "top_speed": "The highest speed the device reported.",
    "events": "Driving events the device reported — harsh acceleration, harsh braking and the like.",
    "resting": "Battery voltage with the ignition off (median). Below 12.2 V on a 12 V system the battery is weak.",
    "running": "Supply voltage with the ignition on (median) — the alternator charging; below 13 V on a 12 V system it is not.",
    "cuts": "Times the main (external) battery read as missing — critical.",
    "restarts": "Times the device started again: its frame counter went back to the start.",
    "alerts": "Alerts the device sent — AIS-140 alerts and its own packets; periodic reports not counted.",
    "gsm": "The GSM signal level in half the packets or more.",
    "cell_missing": "Packets with a GSM signal but no cell id.",
}
for _m, _w in MODE_WORDS.items():
    for _k, _t in _MODE_ABOUT.items():
        ABOUT[f"{_m}_{_k}"] = f"{_w}: {_t}"


def _sats_median(rep: dict):
    extra = (rep.get("_extra") or {}).get("sats_median")
    if extra is not None:
        return extra
    dist = sorted((rep.get("gps") or {}).get("sats") or [])          # [(satellites, rows), …]
    total = sum(k for _, k in dist)
    if not total:
        return None
    seen = 0
    for v, k in dist:
        seen += k
        if seen * 2 >= total:
            return v
    return None


def _volts_factor(rep: dict) -> float:
    return ((rep.get("power") or {}).get("system") or 12) / 12


def _pct(part, whole):
    return 100 * part / whole if whole else None


# key → how the row is judged: (LIMITS check, how its value is read for it) — or "enough" (packets
# made of due, by the settings). A row without one is a fact, shown unjudged.
JUDGE: dict[str, tuple[str, Callable[[dict, Any], Any]]] = {
    "loss": ("loss_min", lambda r, v: v),
    "frames_lost": ("lost_frames_pct", lambda r, v: v),
    "active_enough": ("enough", lambda r, v: v), "standby_enough": ("enough", lambda r, v: v),
    "sleep_enough": ("enough", lambda r, v: v),
    "active_on_time": ("cadence_on_time_pct", lambda r, v: v), "standby_on_time": ("cadence_on_time_pct", lambda r, v: v),
    "sleep_on_time": ("cadence_on_time_pct", lambda r, v: v),
    "active_early": ("steps_off_pct", lambda r, v: v), "standby_early": ("steps_off_pct", lambda r, v: v),
    "sleep_early": ("steps_off_pct", lambda r, v: v),
    "active_late": ("steps_off_pct", lambda r, v: v), "standby_late": ("steps_off_pct", lambda r, v: v),
    "sleep_late": ("steps_off_pct", lambda r, v: v),
    "delay_p90": ("latency_p90_s", lambda r, v: v),
    "late": ("late_pct", lambda r, v: v),
    "very_late": ("very_late_pct", lambda r, v: _pct(v, (r.get("summary") or {}).get("rows"))),
    "no_fix": ("no_fix_pct", lambda r, v: v),
    "stale_jumps": ("jumps", lambda r, v: v),
    "phantom": ("phantom_pct", lambda r, v: _pct(v, (r.get("gps") or {}).get("driven_km"))),
    "frozen": ("frozen_pct", lambda r, v: v),
    "speed_mismatch": ("speed_mismatch_pct", lambda r, v: v),
    "events": ("driving_events", lambda r, v: v),
    "resting": ("resting_v", lambda r, v: v / _volts_factor(r)),
    "running": ("charging_v", lambda r, v: v / _volts_factor(r)),
    "cuts": ("main_missing", lambda r, v: v),
    "restarts": ("restarts", lambda r, v: v),
    "cell_missing": ("cell_missing_pct", lambda r, v: v),
}


def _limit_text(check: str, unit: str, rep: dict, settings: dict) -> str:
    """The limits a row is judged by, in its own unit: "1 · 10 min", "below 90 · 75%"."""
    from .analyse import LIMITS, LOWER
    if check == "enough":
        return (f"below {settings.get('enough_pct', 98):g} · {settings.get('too_few_pct', 90):g}%, "
                f"above {settings.get('too_many_pct', 110):g}%")
    warn, bad = LIMITS[check]
    if check in ("resting_v", "charging_v"):
        f = _volts_factor(rep)
        warn, bad = warn * f, bad * f
    unit_of = {"very_late_pct": "% of rows", "phantom_pct": "% of the distance driven"}.get(check, unit)
    num = lambda x: f"{x:g}"
    sep = "" if unit_of.startswith("%") or not unit_of else " "
    if warn == bad:                                                 # critical at once: no attention band
        return f"Poor from {num(bad)}" + sep + unit_of
    return (f"below {num(warn)} · {num(bad)}" if check in LOWER else f"{num(warn)} · {num(bad)}") + sep + unit_of


def card(rep: dict, settings: dict) -> list[dict]:
    """One log's scorecard: each measure it has, its value, Good / Attention / Poor by its limit."""
    from .analyse import _level, enough_level
    out = []
    for key, group, label, unit, better, read in METRICS:
        try:
            v = read(rep)
        except (KeyError, TypeError, IndexError):
            v = None
        if v is None:
            continue
        v = round(v, 2) if isinstance(v, float) else v
        level, limit = None, ""
        if key in JUDGE:
            check, value_for = JUDGE[key]
            x = value_for(rep, v)
            if x is not None:
                level = enough_level(x, settings) if check == "enough" else _level(check, x)
                limit = _limit_text(check, unit, rep, settings)
        # the steps of a mode: say what "on time" meant — its interval and margin
        mode, _, what = key.partition("_")
        if what in ("on_time", "early", "late"):
            every, tol = _mode(rep, mode, "interval_s"), _mode(rep, mode, "tolerance_s")
            if every and tol is not None:
                limit = (limit + " · " if limit else "") + f"{every:g} s ± {tol:g} s"
        out.append({"key": key, "group": group, "label": label, "unit": unit, "value": v, "level": level, "limit": limit,
                    "about": ABOUT.get(key, "")})
    return out
