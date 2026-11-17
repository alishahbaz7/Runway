"""The drive-log checks: a DriveLog in, a report out (plain JSON-able dicts).

The report has a status per area (completeness, delivery, GPS, power, driving, payload) and
overall, the findings behind each status (each pointing at the detail view that shows its rows),
the detail tables, and downsampled series for the charts. Every limit a status is judged by is a
row in LIMITS, every setting in SETTINGS — the user's figures replace them without code.

Packet types are named in PACKET_TYPES once the platform's meaning is known; until then a type is
shown by its number and described by what its rows hold.
"""

from __future__ import annotations

import math
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any

from . import modes as md
from .drivelog import DriveLog

SETTINGS: dict[str, float] = {
    "overspeed_kmh": 80,        # an overspeed episode is above this
    "moving_kmh": 3,            # moving is above this
    "trip_stop_min": 5,         # a stop at least this long ends a trip
    "gap_min_s": 60,            # a gap is at least this long (and at least 5 usual steps)
    "jump_kmh": 150,            # a hop faster than this …
    "jump_min_m": 500,          # … and longer than this is a jump, not driving
    "frozen_kmh": 10,           # a repeated position counts above this speed
    "speed_diff_kmh": 25,       # reported vs position speed differing by more than this
    "parked_motion_ms2": 0.3,   # IMU: acceleration spread above this while parked is movement
    # a step between packets is on time within a margin that grows with the interval (the user,
    # 17-11-2026: ±1 s at 5–20 s, ±5 s once pings are over 10 min apart) — one per band
    "margin_20s": 1,            # interval up to 20 s
    "margin_2min": 2,           # … up to 2 min
    "margin_10min": 3,          # … up to 10 min
    "margin_over": 5,           # … longer
    "enough_pct": 98,           # packets made, % of those due in a mode: enough from here (the user, 17-11-2026)
    "too_few_pct": 90,          # … too few below this (Poor); between the two, Attention
    "too_many_pct": 110,        # … too many above this
}

# check → (attention at, poor at); LOWER marks the checks where less is worse
LIMITS: dict[str, tuple[float, float]] = {
    "loss_min": (1, 10),                 # minutes missing with ignition on
    "lost_frames_pct": (0.1, 1),         # frames lost (periodic holes not counted)
    "latency_p90_s": (30, 120),          # delivery delay, 90th percentile
    "late_pct": (1, 5),                  # rows that arrived after a newer one
    "history_mismatch_pct": (1, 10),     # platform history flag ≠ the device's live/history mark
    "clock_ahead": (1, 10),              # rows stored before the device says it made them
    "no_position_per_h": (1, 4),         # rows without a position, per hour
    "no_fix_pct": (2, 10),               # rows with no satellites
    "stale_fixes": (1, 3),               # stale positions (0 satellites, far from the real one)
    "jumps": (1, 3),                     # other jumps
    "phantom_pct": (2, 10),              # distance that never happened, % of the driven distance
    "frozen_pct": (1, 5),                # repeated positions while moving, % of moving rows
    "speed_mismatch_pct": (2, 10),       # reported vs position speed, % of comparable hops
    "resting_v": (12.2, 11.5),           # LOWER — battery with ignition off (12 V system; ×2 for 24 V)
    "charging_v": (13.0, 12.6),          # LOWER — supply with ignition on
    "main_missing": (1, 1),              # the main (external) battery gone: any is critical
    "internal_missing": (1, 1),          # the internal battery reading 0 V: any is critical
    "overspeed_episodes": (1, 5),
    "driving_events": (5, 20),
    "cadence_on_time_pct": (90, 75),     # LOWER — steps between packets on time, per mode
    "steps_off_pct": (5, 15),            # steps early, or late (not missed), per mode — % of its steps
    "very_late_pct": (0.1, 1),           # rows stored more than 10 min after the device made them
    "cell_missing_pct": (5, 25),         # rows with no cell id while there is a GSM signal
    "start_v": (9.6, 7.5),               # LOWER — the lowest supply while the engine starts (12 V; ×2 for 24 V)
    "restarts": (1, 3),                  # the device started again (its frame counter back to the start)
    "ignition_alerts_wrong": (1, 5),     # IN/IF alerts that disagree with the ignition input, or are missing
    "ignition_flicker": (1, 5),          # ignition changes within 30 s of the previous one
}
LOWER = {"resting_v", "charging_v", "cadence_on_time_pct", "start_v"}
INTERNAL_LOW_V = 3.6                     # a Li-ion internal battery under this is low (or its level ≤ 20%)

AREAS = [("data", "Completeness"), ("delivery", "Delivery"), ("gps", "GPS"),
         ("power", "Power"), ("driving", "Driving"), ("device", "Device"), ("payload", "Payload")]
# what each area judges (its tile's hold-tip)
AREA_ABOUT = {
    "data": "Whether all the data is there: the time with the ignition on that has data, gaps, frame numbers, and the "
            "packets made in each mode against those due.",
    "delivery": "How fast, and in what order, packets land on InTouch: the delay from the device to the platform, history "
                "packets, and packets that arrive late or out of order.",
    "gps": "How good the positions are: packets without satellites, stale positions and jumps, and distance that never happened.",
    "power": "The main supply and the internal battery: voltage at rest and while charging, at engine start, and when "
             "either goes missing.",
    "driving": "Trips, distance, speed, overspeed and the driving events the device reported.",
    "device": "The device itself: the alerts it sent, restarts, ignition alerts against the ignition input, firmware.",
    "payload": "What the packets carry beyond the position: IMU and CAN values.",
}
LEVELS = ["ok", "info", "warn", "bad"]           # worst last; info never changes a status
STATUS_WORDS = {"ok": "Good", "warn": "Attention", "bad": "Poor", "none": "No data"}

PACKET_TYPES: dict[str, str] = {}                # "0": "Position" … once the platform's list is known
# greenDriveType codes, as the user confirmed them (16-11-2026; 2 also matches the HB alert on its rows);
# a code not here shows by its number and is not judged
DRIVING_EVENTS: dict[str, str] = {"1": "Harsh acceleration", "2": "Harsh braking"}
# AIS-140's alert ids (the standard's table) — the device's own code (IN, IF, HB …) is shown beside
# the name; an id not listed is shown by its code and number only
AIS140_ALERTS: dict[str, str] = {
    "1": "Location update", "2": "Location update (history)", "3": "Main power disconnected",
    "4": "Internal battery low", "5": "Internal battery charged again", "6": "Main power reconnected",
    "7": "Ignition on", "8": "Ignition off", "9": "Box opened", "10": "Emergency on", "11": "Emergency off",
    "12": "Parameters changed over the air", "13": "Harsh braking", "14": "Harsh acceleration",
    "15": "Rash turning", "16": "Emergency wire disconnected",
}
ROUTINE_ALERTS = {"1", "2"}                      # position reports, not alerts
ROUTINE_CODES = {"NR", "IMU"}                    # normal reports, and our own custom payload packets (IMU)
# packets a device sends that are not in the AIS-140 table, by code (the user names them)
DEVICE_PACKETS: dict[str, str] = {
    "DBG": "Debug report",       # Zenithra's own; after a boot it carries RST, BTC …
    "DSEN": "Deep sleep",        # DeepSleepENable: the last packet before the device sleeps — sent when its
                                 # internal battery is low and the main supply is disconnected
}
DEEP_SLEEP = "DSEN"
DEEP_SLEEP_QUIET_S = 60          # after announcing deep sleep, a row sooner than this means it did not sleep
HARSH_ALERTS = {"13", "14", "15"}
VERY_LATE_S = 600
# an alert flag and the value it should follow (the alert set ⇒ the value higher)
ALERT_PAIRS = [("batteryHighTempAlert", "hvBatteryTemp"), ("battHighTempAlert", "hvBatteryTemp")]

_IMU_BLOCK = re.compile(r"imu=\(([^)]*)\)")
_IMU_SAMPLE = re.compile(r"\d+:(-?\d+(?:\.\d+)?)\|(-?\d+(?:\.\d+)?)\|(-?\d+(?:\.\d+)?)\|")


# ── helpers ──────────────────────────────────────────────────────────────────────────────────────

def _m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Metres between two positions (haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371008.8 * math.asin(min(1.0, math.sqrt(h)))


def _iso(t: datetime | None) -> str | None:
    return t.isoformat() if t else None


def _q(sorted_vals: list[float], p: float) -> float | None:
    if not sorted_vals:
        return None
    return sorted_vals[min(len(sorted_vals) - 1, int(p * len(sorted_vals)))]


def _level(check: str, value: float | None) -> str:
    if value is None:
        return "ok"
    warn, bad = LIMITS[check]
    if check in LOWER:
        return "bad" if value < bad else "warn" if value < warn else "ok"
    return "bad" if value >= bad else "warn" if value >= warn else "ok"


def _n(x: float) -> str:
    """1234 → '1,234'; 12.345 → '12.3'."""
    if x is None:
        return "—"
    if abs(x - round(x)) < 1e-9:
        return f"{int(round(x)):,}"
    return f"{x:,.1f}"


def _dur(seconds: float) -> str:
    s = int(round(seconds))
    if s < 60:
        return f"{s} s"
    m = s // 60
    if m < 60:
        return f"{m} min" + (f" {s % 60} s" if s % 60 and m < 10 else "")
    return f"{m // 60} h {m % 60} min"


def _valid(r: dict) -> bool:
    lat, lon = r.get("lat"), r.get("lon")
    return lat is not None and lon is not None and not (abs(lat) < 1e-4 and abs(lon) < 1e-4)


def _when_list(eps: list[dict], unit: str | None, most: int = 3) -> str:
    """'03:10:05–03:12:00 (1 min 55 s, lowest 0.0 V), …' — when each episode was, how long, how low."""
    parts = []
    for e in eps[:most]:
        span = e["start"][11:19] + ("" if e["start"] == e["end"] else "–" + e["end"][11:19])
        extra = [e["duration"] + (" to the end of the log" if e.get("to_end") else "")] if e["seconds"] else []
        if unit and e.get("lowest") is not None:
            extra.append(f"lowest {e['lowest']:.1f} {unit}")
        parts.append(span + (f" ({', '.join(extra)})" if extra else ""))
    return ", ".join(parts) + (f" and {len(eps) - most} more" if len(eps) > most else "")


def _volts(v: float | None) -> float | None:
    if v is None:
        return None
    return v / 1000 if v > 100 else v                 # the export gives mV


def type_name(t: str) -> str:
    return PACKET_TYPES.get(t, f"Type {t}" if t not in ("", None) else "No type")


def alert_name(code: str, aid: str) -> str:
    """'IN', '7' → 'Ignition on (IN)'; a device's own packet → 'Debug report (DBG)'; else 'DSEN (38)'."""
    name = AIS140_ALERTS.get(aid or "") or DEVICE_PACKETS.get(code or "")
    if name:
        return f"{name} ({code})" if code else name
    return f"{code or 'Alert'} ({aid})" if aid else (code or "Alert")


# ── the analysis ─────────────────────────────────────────────────────────────────────────────────

def part(log: DriveLog, start: datetime, end: datetime) -> DriveLog:
    """One stretch of a log (a trip): its rows, each keeping its number ("k") in the whole log, so a
    finding about the stretch points at the same row as in the whole log. Rows are in time order, so
    the stretch is one run of numbers."""
    keep = [i for i, r in enumerate(log.rows) if start <= r["t"] <= end]
    name = {k: v for k, v in (log.name or {}).items() if k not in ("from", "to")}   # not the export's period
    size = round(log.size * len(keep) / len(log.rows)) if log.rows else 0
    return DriveLog(rows=[log.rows[i] for i in keep], columns=log.columns, raw=[log.raw[i] for i in keep],
                    name=name, size=size)


def analyse(log: DriveLog, settings: dict | None = None, span: tuple[datetime, datetime] | None = None,
            profiles: list[dict] | None = None, fixed: dict | None = None) -> dict:
    """The report on a log — or, given a span (a trip's start and end), on that stretch of it alone.
    ``profiles``: the user's device profiles (expected interval per mode); ``fixed``: the intervals a
    whole log was judged by, so a trip of it is judged the same."""
    if span:
        log = part(log, *span)
    s = {**SETTINGS, **{k: float(v) for k, v in (settings or {}).items() if k in SETTINGS}}
    rows = log.rows
    n = len(rows)
    findings: list[dict] = []
    flags: dict[int, set] = defaultdict(set)

    def find(fid, area, level, text, value=None, unit="", view=None, filt=None):
        findings.append({"id": fid, "area": area, "level": level, "text": text, "value": value,
                         "unit": unit, "view": view, "filter": filt})

    report: dict[str, Any] = {"kind": "drive_log", "settings": s, "limits": LIMITS,
                              "meta": _meta(log), "findings": findings}
    if not n:
        report.update(status="none", status_word=STATUS_WORDS["none"], areas=[])
        return report

    t0, t1 = rows[0]["t"], rows[-1]["t"]
    span = max(1.0, (t1 - t0).total_seconds())
    hours = span / 3600

    # cadence and gaps ---------------------------------------------------------------------------
    steps = [(rows[k]["t"] - rows[k - 1]["t"]).total_seconds() for k in range(1, n)]
    pos = Counter(round(x) for x in steps if x > 0)
    step = pos.most_common(1)[0][0] if pos else 1
    gap_s = max(s["gap_min_s"], 5 * step)
    ffill, last = [], None
    for r in rows:
        if r.get("ign") in (0, 1):
            last = r["ign"]
        ffill.append(last)
    bfill, nxt = [None] * n, None
    for k in range(n - 1, -1, -1):
        if rows[k].get("ign") in (0, 1):
            nxt = rows[k]["ign"]
        bfill[k] = nxt

    gaps, on_s, off_s, idle_s = [], 0.0, 0.0, 0.0

    def near_fix(k0, step):                      # the nearest row with a position, within 30 rows
        for j in range(k0, max(-1, k0 - 30) if step < 0 else min(n, k0 + 30), step):
            if _valid(rows[j]):
                return rows[j]
        return None
    for k in range(1, n):
        d = steps[k - 1]
        a, b = rows[k - 1], rows[k]
        state = ffill[k - 1]
        if d > gap_s:
            if state == 1:
                kind = "loss"
            elif state == 0:
                kind = "sleep"
            else:
                kind = "loss" if (a.get("speed") or 0) > s["moving_kmh"] else "unknown"
            fa, fb = near_fix(k - 1, -1), near_fix(k, 1)
            gaps.append({"k": b["k"], "k_from": a["k"], "start": _iso(a["t"]), "end": _iso(b["t"]), "seconds": d,
                         "duration": _dur(d), "kind": kind, "ign_before": state, "ign_after": bfill[k],
                         "speed_before": a.get("speed"), "speed_after": b.get("speed"),
                         "from": [round(fa["lat"], 6), round(fa["lon"], 6)] if fa else None,
                         "to": [round(fb["lat"], 6), round(fb["lon"], 6)] if fb else None,
                         "metres": round(_m(fa["lat"], fa["lon"], fb["lat"], fb["lon"])) if fa and fb else None})
            flags[b["k"]].add("gap")
            if kind == "loss":
                flags[b["k"]].add("loss")
            continue
        if state == 1:
            on_s += d
            if (a.get("speed") or 0) == 0:
                idle_s += d
        elif state == 0:
            off_s += d
    loss = [g for g in gaps if g["kind"] == "loss"]
    loss_s = sum(g["seconds"] for g in loss)
    ign_k = [None] * rows[0]["k"] + ffill            # the same, read by a row's "k" (a part starts past 0)

    # the reporting interval, separately with the ignition on and off (devices report faster when on)
    cadence = {}
    for state, word in ((1, "on"), (0, "off")):
        st = [steps[k - 1] for k in range(1, n) if ffill[k - 1] == state and 0 < steps[k - 1] <= gap_s]
        if len(st) >= 20:
            mode = Counter(round(x) for x in st).most_common(1)[0][0]
            tol = max(1.0, 0.1 * mode)
            cadence[word] = {"seconds": mode, "steps": len(st),
                             "on_time_pct": round(100 * sum(1 for x in st if abs(x - mode) <= tol) / len(st), 1)}
    # ignition changes, from the input itself
    transitions = []
    last = None
    for r in rows:
        if r.get("ign") in (0, 1):
            if last is not None and r["ign"] != last:
                transitions.append({"time": r["t"], "to": r["ign"], "k": r["k"]})
            last = r["ign"]

    # frame numbers ------------------------------------------------------------------------------
    frames = _frames(rows, flags)

    # delivery -----------------------------------------------------------------------------------
    delivery = _delivery(rows, flags, hours)

    # GPS ----------------------------------------------------------------------------------------
    gps = _gps(rows, flags, s, step, gap_s)

    # power --------------------------------------------------------------------------------------
    power = _power(rows, ign_k, transitions, flags)

    # driving ------------------------------------------------------------------------------------
    driving = _driving(rows, flags, s, gap_s, gps, on_s, off_s, idle_s)

    # payloads -----------------------------------------------------------------------------------
    payload = _payload(rows, ign_k, s, step, hours, log.size)

    # the device itself: starts, alerts and their agreement with the inputs, firmware, network
    device = _device(rows, flags, transitions, frames, power)
    network = _network(rows)

    # packet generation by mode: active, standby, sleep
    profile = (fixed or {}).get("profile") if fixed else md.match((profiles or []) + md.PROFILES,
                                                                   (log.name or {}).get("device"), device["firmware"])
    generation = md.generation(rows, ffill, s, profile, fixed,
                               lambda r: (r.get("od") or {}).get("PacketType") == DEEP_SLEEP)

    # findings: completeness ---------------------------------------------------------------------
    name = log.name or {}
    if name.get("from") and t0 - name["from"] > timedelta(seconds=gap_s):
        find("first_row", "data", "info", f"First row at {t0:%H:%M:%S}, {_dur((t0 - name['from']).total_seconds())} "
             f"after the period starts", view="gaps")
    lm = loss_s / 60
    if loss:
        find("loss_min", "data", _level("loss_min", lm),
             f"{_dur(loss_s)} missing with ignition on, in {len(loss)} gap{'s' if len(loss) != 1 else ''}",
             round(lm, 1), "min", "gaps", {"kind": "loss"})
    else:
        find("loss_min", "data", "ok", "Nothing missing with ignition on", 0, "min", "gaps")
    sleep = [g for g in gaps if g["kind"] == "sleep"]
    if sleep:
        find("sleep", "data", "info", f"{len(sleep)} silent period{'s' if len(sleep) != 1 else ''} with ignition off, "
             f"{_dur(sum(g['seconds'] for g in sleep))} in all", view="gaps", filt={"kind": "sleep"})
    _generation_findings(generation, s, find, device["firmware"])
    unknown = [g for g in gaps if g["kind"] == "unknown"]
    if unknown:
        find("gaps_unknown", "data", "warn", f"{len(unknown)} gap{'s' if len(unknown) != 1 else ''} with no ignition "
             f"state, {_dur(sum(g['seconds'] for g in unknown))}", view="gaps", filt={"kind": "unknown"})
    if frames["numbered"]:
        lp = frames["lost_pct"]
        # holes right at an ignition change: packets the device sends then (alerts) that this log lacks
        near = 0
        for h in frames["holes"]:
            if h["periodic"]:
                continue
            t = datetime.fromisoformat(h["time"])
            h["near_ignition"] = any(abs((tr["time"] - t).total_seconds()) <= 60 for tr in transitions)
            near += h["missing"] if h["near_ignition"] else 0
        text = (f"{_n(frames['lost'])} frame number{'s' if frames['lost'] != 1 else ''} missing from this log ({lp:.2f}%)"
                + (f", {_n(near)} of them within a minute of an ignition change" if near else "")) if frames["lost"]             else "No frame numbers missing"
        find("lost_frames_pct", "data", _level("lost_frames_pct", lp), text, round(lp, 3), "%", "frames")
        if frames["periodic_holes"]:
            find("periodic_frames", "data", "info",
                 f"{frames['periodic_holes']} single-frame holes repeat every {frames['period']} frames "
                 f"(~{_dur(frames['period'] * step)}) — another packet type, not counted as loss", view="frames",
                 filt={"periodic": True})
        if frames["resets"]:
            find("frame_resets", "data", "info", f"Frame numbers start again {frames['resets']} time"
                 f"{'s' if frames['resets'] != 1 else ''}", view="frames")

    # findings: delivery -------------------------------------------------------------------------
    if delivery["latency"]["count"]:
        p90 = delivery["latency"]["p90"]
        find("latency_p90_s", "delivery", _level("latency_p90_s", p90),
             f"90% stored within {_dur(p90)}, the slowest after {_dur(delivery['latency']['max'])}",
             p90, "s", "delivery")
        lp = delivery["late_pct"]
        if delivery["late"]:
            find("late_pct", "delivery", _level("late_pct", lp),
                 f"{_n(delivery['late'])} rows arrived after a newer one ({lp:.1f}%)", round(lp, 2), "%", "rows",
                 {"flag": "late"})
        if delivery["clock_ahead"]:
            find("clock_ahead", "delivery", _level("clock_ahead", delivery["clock_ahead"]),
                 f"{_n(delivery['clock_ahead'])} rows stored before the device's own time — its clock runs ahead",
                 delivery["clock_ahead"], "", "rows", {"flag": "clock"})
    vl = delivery.get("very_late") or 0
    if vl:
        vp = 100 * vl / n
        find("very_late_pct", "delivery", _level("very_late_pct", vp),
             f"{_n(vl)} rows stored over 10 min after the device made them, the latest after "
             f"{_dur(delivery['latency']['max'])}", round(vp, 3), "%", "rows", {"flag": "very_late"})
    if network.get("considered"):
        mp = network["missing_pct"]
        if network["missing"]:
            find("cell_missing_pct", "delivery", _level("cell_missing_pct", mp),
                 f"No cell id in {_n(network['missing'])} rows with a GSM signal ({mp:.0f}%)", round(mp, 1), "%", "delivery")
        if network["changes"]:
            find("cells", "delivery", "info", f"{network['cells']} cell{'s' if network['cells'] != 1 else ''}, changed "
                 f"{network['changes']} time{'s' if network['changes'] != 1 else ''}", view="delivery")
    if delivery["history"]:
        find("history", "delivery", "info", f"{_n(delivery['history'])} rows flagged history "
             f"({delivery['history_pct']:.1f}%)", view="rows", filt={"flag": "history"})
    if delivery["marked"]:
        mp = delivery["history_mismatch_pct"]
        mm = delivery["history_mismatch"]
        find("history_mismatch_pct", "delivery", _level("history_mismatch_pct", mp),
             f"{_n(mm['live_as_history'])} live rows flagged history by the platform, "
             f"{_n(mm['history_as_live'])} history rows not" if (mm["live_as_history"] or mm["history_as_live"])
             else "The platform's history flag matches the device's live/history mark", round(mp, 2), "%", "rows",
             {"flag": "history_mismatch"})
    if delivery["no_position"]:
        ph = delivery["no_position"] / hours
        types = ", ".join(f"{type_name(t)} ×{c}" for t, c in delivery["no_position_types"].items())
        find("no_position_per_h", "delivery", _level("no_position_per_h", ph),
             f"{_n(delivery['no_position'])} rows without a position ({types}), {ph:.1f} an hour",
             round(ph, 2), "/h", "rows", {"flag": "noposition"})
        if delivery.get("burst_after_no_position"):
            find("burst_after_no_position", "delivery", "info",
                 f"After each, about {delivery['burst_after_no_position']} history rows arrive within 30 s — "
                 f"a reconnect and its buffer", view="rows", filt={"flag": "noposition"})
    b = delivery["batches"]
    if b["typical"] and b["typical"] > 1:
        find("batches", "delivery", "info", f"Stored {b['typical']} rows at a time (largest {b['largest']})",
             view="delivery")

    # findings: GPS ------------------------------------------------------------------------------
    if gps["rows"]:
        if gps["no_fix"]:
            find("no_fix_pct", "gps", _level("no_fix_pct", gps["no_fix_pct"]),
                 f"{_n(gps['no_fix'])} rows with no satellites ({gps['no_fix_pct']:.1f}%)", round(gps["no_fix_pct"], 2),
                 "%", "rows", {"flag": "nofix"})
        st = gps["stale"]
        if st:
            far = max(x["metres"] for x in st) / 1000
            ns = gps["stale_points"]
            find("stale_fixes", "gps", _level("stale_fixes", ns),
                 f"{ns} stale position{'s' if ns != 1 else ''} — 0 satellites, up to {far:.1f} km "
                 f"from the real one", ns, "", "gps", {"kind": "stale"})
        oj = gps["jumps"]
        if oj:
            find("jumps", "gps", _level("jumps", len(oj)), f"{len(oj)} position jump{'s' if len(oj) != 1 else ''} "
                 f"faster than {_n(s['jump_kmh'])} km/h", len(oj), "", "gps", {"kind": "jump"})
        ph = gps["phantom_km"]
        if ph > 0.05:
            base = gps["driven_km"]
            pct = 100 * ph / base if base > 0.5 else None
            lvl = _level("phantom_pct", pct) if pct is not None else ("warn" if ph > 1 else "info")
            find("phantom_pct", "gps", lvl,
                 f"{ph:.1f} km that never happened (jumps {gps['jump_km']:.1f} km, drift while parked "
                 f"{gps['drift_km']:.2f} km)" + (f" — {pct:.0f}% of the {base:.1f} km driven" if pct is not None else ""),
                 round(pct, 1) if pct is not None else round(ph, 2), "%" if pct is not None else "km", "gps")
        if gps["frozen"]:
            fp = gps["frozen_pct"]
            first = gps["frozen_batch_first"]
            find("frozen_pct", "gps", _level("frozen_pct", fp),
                 f"{_n(gps['frozen'])} repeated positions while moving ({fp:.1f}% of moving rows)"
                 + (f", {_n(first)} of them the first row of an upload" if first else ""), round(fp, 2), "%", "rows",
                 {"flag": "frozen"})
        if gps["speed_mismatch"]:
            sp = gps["speed_mismatch_pct"]
            find("speed_mismatch_pct", "gps", _level("speed_mismatch_pct", sp),
                 f"Reported speed differs from position speed by over {_n(s['speed_diff_kmh'])} km/h in "
                 f"{_n(gps['speed_mismatch'])} hops ({sp:.1f}%)", round(sp, 2), "%", "rows", {"flag": "speed"})
        for p in gps["placeholder_sats"]:
            find("placeholder_sats", "gps", "warn", f"Satellites always {p['value']} in {type_name(p['type'])} "
                 f"({_n(p['rows'])} rows)", view="types")

    # findings: power ----------------------------------------------------------------------------
    if power["rows"]:
        sysv = power["system"]
        k = sysv / 12
        if power["resting"] is not None:
            lvl = _level("resting_v", power["resting"] / k)
            find("resting_v", "power", lvl, f"Battery {power['resting']:.1f} V with ignition off (median)",
                 power["resting"], "V", "power")
        if power["running"] is not None:
            find("charging_v", "power", _level("charging_v", power["running"] / k),
                 f"Supply {power['running']:.1f} V with ignition on (median)", power["running"], "V", "power")
        starts = power.get("starts") or []
        if starts:
            low = min(starts, key=lambda x: x["lowest"])
            find("start_v", "power", _level("start_v", low["lowest"] / k),
                 f"Supply fell to {low['lowest']:.1f} V while the engine started (lowest of {len(starts)} start"
                 f"{'s' if len(starts) != 1 else ''}, at {low['time'][11:19]})", low["lowest"], "V", "power")
        cuts = power["cuts"]
        if cuts:
            find("main_missing", "power", _level("main_missing", len(cuts)),
                 f"Main battery missing {len(cuts)} time{'s' if len(cuts) != 1 else ''} — " + _when_list(cuts, "V"),
                 len(cuts), "", "power", {"kind": "main"})
    im = power.get("internal_missing") or []
    if im:
        find("internal_missing", "power", _level("internal_missing", len(im)),
             f"Internal battery missing (reads 0 V) {len(im)} time{'s' if len(im) != 1 else ''} — " + _when_list(im, None),
             len(im), "", "power", {"kind": "internal"})
    il = power.get("internal_low") or []
    if il:
        total = sum(e["seconds"] for e in il)
        find("internal_low", "power", "warn",
             f"Internal battery low (under {INTERNAL_LOW_V} V or 20%) for {_dur(total)}, lowest "
             f"{min(e['lowest'] for e in il):.2f} V", round(total / 60, 1), "min", "power", {"kind": "internal_low"})
    if power.get("internal"):
        i = power["internal"]
        find("internal_battery", "power", "info", i["text"], view="power")

    # findings: driving --------------------------------------------------------------------------
    trips = driving["trips"]
    if trips:
        find("trips", "driving", "info", f"{len(trips)} trip{'s' if len(trips) != 1 else ''}, "
             f"{driving['distance_km']:.1f} km, top speed {_n(driving['max_speed'])} km/h", view="trips")
    else:
        find("trips", "driving", "info", "Not driven", view="trips")
    ov = driving["overspeed"]
    if ov:
        find("overspeed_episodes", "driving", _level("overspeed_episodes", len(ov)),
             f"{len(ov)} time{'s' if len(ov) != 1 else ''} over {_n(s['overspeed_kmh'])} km/h, "
             f"{_dur(sum(e['seconds'] for e in ov))} in all", len(ov), "", "events", {"kind": "overspeed"})
    ev = driving["events"]
    if ev:
        codes = ", ".join(f"{e_name} ×{n_}" for e_name, n_ in Counter(e.get("name") or DRIVING_EVENTS.get(e["code"], "code " + e["code"])
                                                                       for e in ev).most_common())
        named = [e for e in ev if e.get("name")]          # only events whose meaning is known are judged
        find("driving_events", "driving", _level("driving_events", len(named)) if named else "info",
             f"{len(ev)} driving event{'s' if len(ev) != 1 else ''} ({codes})", len(ev), "", "events",
             {"kind": "driving"})
    if driving["idle_s"] > 60:
        find("idle", "driving", "info", f"Idling {_dur(driving['idle_s'])} (ignition on, standing)", view="trips")

    # findings: device ---------------------------------------------------------------------------
    rs = device["restarts"]
    if frames.get("starts") or device["alerts_total"] or device["firmware"]:
        if rs:
            when_ = ", ".join(x["time"][11:19] + (f" after {_dur(x['gap_before'])} silent" if x.get("gap_before", 0) > 120 else "")
                              for x in rs[:4])
            find("restarts", "device", _level("restarts", len(rs)),
                 f"Restarted {len(rs)} time{'s' if len(rs) != 1 else ''} ({when_})", len(rs), "", "device", {"kind": "restart"})
        elif frames.get("numbered"):
            find("restarts", "device", "ok", "No restarts", 0, "", "device")
        first = (frames.get("starts") or [None])[0]
        if first and first.get("first_row"):
            find("started", "device", "info", f"Started at {first['time'][11:19]} — the log begins with frame {first['frame']}",
                 view="device")
        for b in device["boots"][:3]:
            find("boot", "device", "info", f"Boot report at {b['time'][11:19]}: {b['text']}", view="device")
    fw = device["firmware"]
    if fw:
        if len(fw) == 1:
            find("firmware", "device", "info", f"Firmware {fw[0]}", view="device")
        else:
            find("firmware", "device", "warn", f"Firmware changed: {' → '.join(fw)}", view="device")
    if device["alerts_total"]:
        top = ", ".join(f"{c} ×{k_}" for c, k_ in device["codes"][:6])
        find("alerts", "device", "info", f"{device['alerts_total']} device alerts ({top})", view="device")
        ia = device["ignition"]
        if ia["checked"]:
            wrong = ia["disagree"] + ia["missing"]
            parts = []
            if ia["disagree"]:
                parts.append(f"{ia['disagree']} disagree with the ignition input")
            if ia["missing"]:
                parts.append(f"{ia['missing']} ignition change{'s' if ia['missing'] != 1 else ''} without one")
            if ia["repeated"]:
                parts.append(f"{ia['repeated']} repeated")
            find("ignition_alerts_wrong", "device", _level("ignition_alerts_wrong", wrong),
                 "Ignition alerts: " + ("; ".join(parts) if parts else "every change has its alert"), wrong, "", "device",
                 {"kind": "ignition"})
        if device["ota"]:
            find("ota", "device", "info", f"Parameters changed over the air {device['ota']} time{'s' if device['ota'] != 1 else ''}",
                 view="device")
        if device["unknown_codes"]:
            find("unknown_alerts", "device", "info", "Not in the AIS-140 table: " + ", ".join(device["unknown_codes"]),
                 view="device")
    if device["tamper_open"]:
        find("tamper", "device", "warn", f"Box opened in {_n(device['tamper_open'])} rows", device["tamper_open"], "",
             "device")
    if device["emergency"]:
        find("emergency", "device", "warn", f"Emergency {device['emergency']} time{'s' if device['emergency'] != 1 else ''}",
             device["emergency"], "", "device")
    bd = sum(1 for a in device["alerts"] if a["id"] == "3")
    if bd:
        find("main_missing_alert", "power", "bad", f"The device alerted main power disconnected {bd} time{'s' if bd != 1 else ''}",
             bd, "", "device")
    bl = sum(1 for a in device["alerts"] if a["id"] == "4")
    if bl:
        find("internal_low_alert", "power", "warn", f"The device alerted internal battery low {bl} time{'s' if bl != 1 else ''}",
             bl, "", "device")
    for ds in device.get("deep_sleep", []):
        why = []
        why.append(f"main supply {ds['supply']:.1f} V" if ds["supply"] is not None else "no supply reading")
        if ds["internal_v"] is not None:
            why.append(f"internal battery {ds['internal_v']:.2f} V" + (f" ({ds['internal_pct']:.0f}%)" if ds["internal_pct"] is not None else ""))
        where = " while the engine was starting" if ds["during_start"] else ""
        if ds["slept"]:
            find("deep_sleep", "device", "info",
                 f"Deep sleep at {ds['time'][11:19]}{where} — {', '.join(why)}"
                 + (f"; silent {_dur(ds['next_after_s'])} after" if ds["next_after_s"] else "; the log ends there"),
                 view="device", filt={"kind": "deep_sleep"})
        else:
            find("deep_sleep_awake", "device", "warn",
                 f"Deep sleep announced at {ds['time'][11:19]}{where} ({', '.join(why)}), but it reported again "
                 f"{_dur(ds['next_after_s'])} later", view="device", filt={"kind": "deep_sleep"})
        if not (ds["main_gone"] and ds["internal_low"]):
            missing = [w for w, ok in (("the main supply connected", not ds["main_gone"]),
                                       ("the internal battery not low", not ds["internal_low"])) if ok]
            find("deep_sleep_why", "device", "warn",
                 f"Deep sleep at {ds['time'][11:19]} announced with {' and '.join(missing)}", view="device",
                 filt={"kind": "deep_sleep"})
    fl = device["flicker"]
    if fl:
        find("ignition_flicker", "device", _level("ignition_flicker", len(fl)),
             f"Ignition changed {len(fl)} time{'s' if len(fl) != 1 else ''} within 30 s of the previous change",
             len(fl), "", "rows", {"flag": "flicker"})

    # findings: payload --------------------------------------------------------------------------
    imu = payload.get("imu")
    if imu:
        find("imu", "payload", "info", f"IMU {_n(imu['packets'])} packets × {imu['samples_per_packet']} samples "
             f"(~{imu['rate_hz']:.0f} Hz)", view="payload")
        if imu["parked_packets"]:
            find("imu_parked", "payload", "info", f"{_n(imu['parked_packets'])} IMU packets with ignition off "
                 f"({imu['parked_pct']:.0f}%) — {payload['mb_per_hour']:.1f} MB of log an hour", view="payload")
        if imu["motion_minutes"]:
            find("imu_motion", "payload", "warn" if imu["motion_minutes"] >= 5 else "info",
                 f"Movement while parked in {imu['motion_minutes']} minute"
                 f"{'s' if imu['motion_minutes'] != 1 else ''}", imu["motion_minutes"], "min", "payload")
    for a in payload.get("alert_mismatch", []):
        find("alert_mismatch", "payload", "warn",
             f"{a['alert']} set at the same {a['value']} as when clear ({a['mean_set']:.1f} vs {a['mean_clear']:.1f})",
             view="payload")
    for sp in payload.get("spikes", []):
        find("spike", "payload", "info", f"{sp['key']} {_n(sp['value'])} {sp['count']}× "
             f"(otherwise {_n(sp['low'])}–{_n(sp['high'])})", view="payload")
    if payload.get("can_speed_zero"):
        find("can_speed_zero", "payload", "warn", f"CAN speed always 0 ({_n(payload['can_speed_zero'])} rows)",
             view="payload")

    # statuses -----------------------------------------------------------------------------------
    present = {"data": True, "delivery": delivery["latency"]["count"] > 0 or delivery["history"] > 0,
               "gps": gps["rows"] > 0, "power": power["rows"] > 0, "driving": True,
               "device": bool(frames.get("numbered") or device["alerts_total"] or device["firmware"]),
               "payload": bool(payload.get("imu") or payload.get("can"))}
    areas = []
    for aid, label in AREAS:
        fs = [f for f in findings if f["area"] == aid]
        if not present[aid] and not fs:
            continue
        worst = max((f["level"] for f in fs if f["level"] in ("warn", "bad")), key=LEVELS.index, default="ok")
        areas.append({"id": aid, "label": label, "status": worst, "status_word": STATUS_WORDS[worst], "about": AREA_ABOUT.get(aid, ""),
                      "issues": sum(1 for f in fs if f["level"] in ("warn", "bad"))})
    _headlines(areas, frames, delivery, gps, power, driving, payload, on_s, loss_s, device)
    overall = max((a["status"] for a in areas), key=LEVELS.index, default="ok")

    report.update({
        "status": overall, "status_word": STATUS_WORDS[overall],
        "issues": sum(1 for f in findings if f["level"] in ("warn", "bad")),
        "areas": areas,
        "summary": {"first": _iso(t0), "last": _iso(t1), "rows": n, "hours": round(hours, 2), "step_s": step,
                    "gap_s": gap_s, "ignition_on_s": on_s, "ignition_off_s": off_s, "loss_s": loss_s,
                    "coverage_pct": round(100 * on_s / (on_s + loss_s), 1) if on_s + loss_s else None,
                    "cadence": cadence, "firmware": device["firmware"]},
        "gaps": gaps, "generation": generation, "frames": frames, "delivery": delivery, "gps": gps, "power": power,
        "driving": driving, "payload": payload, "device": device, "network": network,
        "types": _types(log), "columns": _columns(log),
        "series": _series(rows, ign_k, t0, t1, gaps, gps, driving, delivery, frames, flags, device, power, gap_s, s),
        "flag_counts": dict(Counter(f for fs in flags.values() for f in fs)),
    })
    from .scorecard import card
    report["scorecard"] = card(report, s)
    report["_flags"] = {k: sorted(v) for k, v in flags.items()}      # for the row view (kept off the wire)
    findings.sort(key=lambda f: (-LEVELS.index(f["level"]), [a for a, _ in AREAS].index(f["area"])))
    return report


# ── the parts ────────────────────────────────────────────────────────────────────────────────────

def enough_level(pct: float | None, s: dict) -> str:
    """Packets made against those due in a mode: too few (Poor below too_few, Attention below
    enough), enough, or too many (Attention)."""
    if pct is None:
        return "info"
    if pct < s["too_few_pct"]:
        return "bad"
    if pct < s["enough_pct"] or pct > s["too_many_pct"]:
        return "warn"
    return "ok"


PERIODIC_CODES = {"NR", ""}          # the periodic report; anything else is a packet besides it


def _mode_notes(g: dict, gen: dict, s: dict) -> list[str]:
    """Plain sentences behind a mode's figures: what made too many or too few, history and its
    delay, the slow tail, the clock, repeats, how long standby really lasted."""
    notes = []
    every, diff, pct = g["interval_s"], g.get("diff"), g.get("sufficiency_pct")
    besides = [(c, k) for c, k in g.get("types") or [] if c not in PERIODIC_CODES]
    extra = sum(k for _, k in besides)
    if every and diff is not None and pct is not None:
        if diff >= 1 and pct > 100.5:
            if extra:
                kinds = ", ".join(f"{c} ×{_n(k)}" for c, k in besides[:4])
                notes.append(f"{_n(diff)} more than due — {_n(extra)} came besides the periodic report ({kinds})")
            elif g["early"]:
                notes.append(f"{_n(diff)} more than due — {_n(g['early'])} steps came early, under "
                             f"{_dur(every - g.get('tolerance_s', 0))}")
            else:
                notes.append(f"{_n(diff)} more than due")
        elif diff <= -1 and pct < 99.5:
            text = f"{_n(-diff)} short"
            if g["missed_steps"]:
                text += (f" — about {_n(g['missed'])} missed in {_n(g['missed_steps'])} silence"
                         f"{'s' if g['missed_steps'] != 1 else ''} longer than {_dur(1.5 * every)}")
            if g.get("longest_s") and g["longest_s"] > 1.5 * every:
                text += f"; the longest {_dur(g['longest_s'])} at {datetime.fromisoformat(g['longest_at']):%H:%M:%S}"
            notes.append(text)
    if g["history"]:
        dh, dl = g["delay_history"].get("median"), g["delay_live"].get("median")
        notes.append(f"{_n(g['history'])} history packet{'s' if g['history'] != 1 else ''} ({g['history_pct']:.1f}%) "
                     f"landed {_dur(dh)} after {'they were' if g['history'] != 1 else 'it was'} made"
                     + (f" (median) — live ones {_dur(dl)}" if dl is not None else " (median)"))
    d = g["delay"]
    if d.get("p90") is not None and d.get("median") is not None and d["p90"] >= max(60, 4 * max(d["median"], 1)):
        notes.append(f"1 in 10 took over {_dur(d['p90'])} to land (median {_dur(d['median'])})")
    if g["clock_ahead"]:
        notes.append(f"{_n(g['clock_ahead'])} stored before the device says it made them — its clock runs ahead")
    if g["duplicates"]:
        notes.append(f"{_n(g['duplicates'])} packet{'s' if g['duplicates'] != 1 else ''} with the same time as the one before")
    lasted = g.get("standby_lasted") or {}
    if lasted.get("count"):
        said = (gen.get("values") or {}).get("standby_min")
        src = (gen.get("source") or {}).get("standby_min")
        times = f"{lasted['count']} time{'s' if lasted['count'] != 1 else ''}"
        if not lasted["median"]:
            notes.append(f"Went to sleep as soon as the ignition went off ({times})"
                         + (f"; the profile says standby lasts {_dur(said * 60)}" if src == "profile" and said else ""))
        else:
            notes.append(f"Standby lasted {_dur(lasted['median'])} after the ignition went off (median of {times})"
                         + (f"; the profile says {_dur(said * 60)}" if src == "profile" and said is not None else ""))
    return notes


def _generation_findings(gen: dict, s: dict, find, firmware: list) -> None:
    prof = gen.get("profile")
    if prof:
        find("profile", "data", "info", f"Intervals from the device profile “{prof.get('name') or 'unnamed'}”",
             view="modes")
    elif gen["modes"]:
        fw = f" (firmware {', '.join(firmware)})" if firmware else ""
        find("profile", "data", "info", f"Intervals as this log shows them — no device profile matches{fw}",
             view="modes")
    for g in gen["modes"]:
        g["notes"] = _mode_notes(g, gen, s)
        g["level"] = enough_level(g["sufficiency_pct"], s)
        g["steps_level"] = _level("cadence_on_time_pct", g["on_time_pct"]) if g["on_time_pct"] is not None else "info"
        every, m, label = g["interval_s"], g["mode"], g["label"]
        if not every or g["packets"] < 3 or g["sufficiency_pct"] is None:
            continue
        due, made, pct = g["expected"], g["packets"], g["sufficiency_pct"]
        when = f"every {_dur(every)}"
        if pct > s["too_many_pct"]:
            text = f"{label}: {_n(made)} packets where {_n(round(due))} were due ({when}) — {pct:.0f}%, too many"
        elif pct < s["enough_pct"]:
            text = f"{label}: {_n(made)} of {_n(round(due))} packets due ({when}) — {pct:.1f}%, {_n(round(due - made))} short"
        else:
            text = f"{label}: {_n(made)} of {_n(round(due))} packets due ({when}) — {pct:.1f}%, enough"
        find(f"enough_{m}", "data", g["level"], text, pct, "%", "modes", {"mode": m})
        if g["packets"] >= 5:
            find(f"steps_{m}", "data", g["steps_level"],
                 f"{label}: {g['on_time_pct']:.0f}% of steps on time ({when} ± {_dur(g['tolerance_s'])}) — "
                 f"{_n(g['early'])} early, {_n(g['late'])} late", g["on_time_pct"], "%", "modes", {"mode": m})


def _meta(log: DriveLog) -> dict:
    name = log.name or {}
    return {"device": name.get("device"), "from": _iso(name.get("from")), "to": _iso(name.get("to")),
            "report": name.get("kind") or "driveLogs", "columns": len(log.columns), "bytes": log.size,
            "rows": len(log.rows)}


def _frames(rows: list[dict], flags) -> dict:
    """Frame numbers, one counter per start of the device: when the number falls back to the first
    few, the device started again and its counter with it — the repeats are not duplicates and the
    numbers before are not lost. Holes are found within each run; a hole that repeats at a fixed
    spacing is another packet type using the counter."""
    out = {"numbered": 0, "holes": [], "lost": 0, "lost_pct": 0.0, "periodic_holes": 0, "period": None,
           "resets": 0, "starts": [], "duplicates": 0, "runs": 0}
    fr = []
    for r in rows:
        v = (r.get("meta") or {}).get("frameNumber", "")
        if v.isdigit():
            fr.append((int(v), r))
    if not fr:
        return out
    out["numbered"] = len(fr)
    runs: list[list] = [[]]
    for f, r in fr:
        if runs[-1]:
            lastf = runs[-1][-1][0]
            if (f <= 3 and f <= lastf) or f < lastf - 1000:
                runs.append([])
        runs[-1].append((f, r))
    out["runs"] = len(runs)
    out["resets"] = len(runs) - 1
    prev_t = None
    for i, run in enumerate(runs):
        f0, r0 = min(run, key=lambda x: x[1]["t"])
        if f0 <= 3:
            out["starts"].append({"time": _iso(r0["t"]), "frame": f0, "k": r0["k"], "first_row": r0["k"] == 0,
                                  "gap_before": (r0["t"] - prev_t).total_seconds() if prev_t else None})
            flags[r0["k"]].add("restart")
        prev_t = max(x[1]["t"] for x in run)
    holes, span = [], 0
    for run in runs:
        nums = Counter(f for f, _ in run)
        out["duplicates"] += sum(c - 1 for c in nums.values())
        by = sorted(run, key=lambda x: x[0])
        span += by[-1][0] - by[0][0] + 1
        for (a_, ra), (b_, rb) in zip(by, by[1:]):
            miss = b_ - a_ - 1
            if 0 < miss < 100000:
                holes.append({"after": a_, "before": b_, "missing": miss, "time": _iso(ra["t"]), "k": rb["k"],
                              "periodic": False, "run": runs.index(run)})
    singles = [h for h in holes if h["missing"] == 1]
    diffs = [b_["after"] - a_["after"] for a_, b_ in zip(singles, singles[1:]) if a_["run"] == b_["run"]]
    if len(diffs) >= 2:
        cand = max(set(diffs), key=lambda c: sum(1 for d in diffs if abs(d - c) <= 2))
        hits = sum(1 for d in diffs if abs(d - cand) <= 2)
        if cand > 10 and hits >= max(2, len(diffs) // 2):
            out["period"] = cand
            for i, h in enumerate(singles):
                near = []
                if i > 0 and singles[i - 1]["run"] == h["run"]:
                    near.append(h["after"] - singles[i - 1]["after"])
                if i + 1 < len(singles) and singles[i + 1]["run"] == h["run"]:
                    near.append(singles[i + 1]["after"] - h["after"])
                if any(abs(d - cand) <= 2 or abs(d - 2 * cand) <= 4 for d in near):
                    h["periodic"] = True
    out["holes"] = holes
    out["periodic_holes"] = sum(1 for h in holes if h["periodic"])
    out["lost"] = sum(h["missing"] for h in holes if not h["periodic"])
    out["lost_pct"] = 100 * out["lost"] / span if span else 0.0
    out["range"] = [min(f for f, _ in fr), max(f for f, _ in fr)]
    for h in holes:
        if not h["periodic"]:
            flags[h["k"]].add("frames")
    return out


def _delivery(rows: list[dict], flags, hours: float) -> dict:
    lat = []
    clock = 0
    for r in rows:
        if r.get("ins") is not None:
            d = (r["ins"] - r["t"]).total_seconds()
            lat.append(d)
            if d > VERY_LATE_S:
                flags[r["k"]].add("very_late")
            if d < -5:
                clock += 1
                flags[r["k"]].add("clock")
    sl = sorted(lat)
    edges = [0, 5, 10, 20, 30, 60, 120, 300, 600]
    hist = [0] * (len(edges) + 1)
    for d in lat:
        i = 0
        while i < len(edges) and d >= edges[i]:
            i += 1
        hist[i] += 1
    # arrival order: a row that arrives after a newer one (by the device's time) arrived late
    late = 0
    newest = None
    for r in sorted((r for r in rows if r.get("ins") is not None), key=lambda r: (r["ins"], r["i"])):
        if newest is not None and r["t"] < newest:
            late += 1
            flags[r["k"]].add("late")
        elif newest is None or r["t"] > newest:
            newest = r["t"]
    hist_rows = [r for r in rows if r.get("history")]
    for r in hist_rows:
        flags[r["k"]].add("history")
    marked = live_h = hist_l = 0
    for r in rows:
        mark = (r.get("meta") or {}).get("AISPacketType")
        if mark in ("L", "H") and r.get("history") is not None:
            marked += 1
            if mark == "L" and r["history"]:
                live_h += 1
                flags[r["k"]].add("history_mismatch")
            elif mark == "H" and not r["history"]:
                hist_l += 1
                flags[r["k"]].add("history_mismatch")
    nopos = [r for r in rows if not _valid(r)]
    for r in nopos:
        flags[r["k"]].add("noposition")
    # what arrives right after a row with no position: a burst of history rows means a reconnect
    burst = None
    if nopos and hist_rows and len(nopos) <= 500:
        ins_sorted = sorted((r["ins"], bool(r.get("history"))) for r in rows if r.get("ins") is not None)
        import bisect
        keys = [x[0] for x in ins_sorted]
        counts = []
        for r in nopos:
            lo = bisect.bisect_left(keys, r["t"])
            hi = bisect.bisect_right(keys, r["t"] + timedelta(seconds=30))
            counts.append(sum(1 for x in ins_sorted[lo:hi] if x[1]))
        med = statistics.median(counts)
        if med >= 3:
            burst = int(med)
    per_ins = Counter(r["ins"] for r in rows if r.get("ins") is not None)
    sizes = Counter(per_ins.values())
    typical = max(sizes, key=lambda k: sizes[k] * k) if sizes else None     # the size most rows came in
    n = len(rows)
    return {
        "latency": {"count": len(lat), "p50": _q(sl, .5), "p90": _q(sl, .9), "p99": _q(sl, .99),
                    "max": sl[-1] if sl else None, "min": sl[0] if sl else None,
                    "edges": edges, "hist": hist},
        "late": late, "late_pct": 100 * late / n if n else 0,
        "very_late": sum(1 for d in lat if d > VERY_LATE_S),
        "clock_ahead": clock,
        "history": len(hist_rows), "history_pct": 100 * len(hist_rows) / n if n else 0,
        "marked": marked, "history_mismatch": {"live_as_history": live_h, "history_as_live": hist_l},
        "history_mismatch_pct": 100 * (live_h + hist_l) / marked if marked else 0,
        "no_position": len(nopos), "no_position_types": dict(Counter(r.get("type") or "" for r in nopos)),
        "no_position_times": [_iso(r["t"]) for r in nopos[:500]],
        "burst_after_no_position": burst,
        "batches": {"typical": typical, "largest": max(per_ins.values()) if per_ins else None,
                    "sizes": sorted(sizes.items())[:20]},
    }


def _gps(rows: list[dict], flags, s: dict, step: float, gap_s: float) -> dict:
    out = {"rows": 0, "no_fix": 0, "no_fix_pct": 0.0, "stale": [], "stale_points": 0, "jumps": [], "phantom_km": 0.0,
           "jump_km": 0.0, "drift_km": 0.0, "driven_km": 0.0, "naive_km": 0.0, "frozen": 0, "frozen_pct": 0.0,
           "frozen_batch_first": 0, "speed_mismatch": 0, "speed_mismatch_pct": 0.0, "placeholder_sats": [],
           "sats": [], "gsm": [], "excluded": []}
    has_pos = [r for r in rows if r.get("lat") is not None]
    if not has_pos:
        return out
    out["rows"] = len(has_pos)
    with_sats = [r for r in rows if r.get("sats") is not None]
    nofix = [r for r in with_sats if r["sats"] == 0]
    for r in nofix:
        flags[r["k"]].add("nofix")
    out["no_fix"] = len(nofix)
    out["no_fix_pct"] = 100 * len(nofix) / len(with_sats) if with_sats else 0.0
    out["sats"] = sorted(Counter(r["sats"] for r in with_sats).items())
    out["gsm"] = sorted(Counter(r["gsm"] for r in rows if r.get("gsm") is not None).items())

    v = [r for r in rows if _valid(r)]
    moving = lambda r: (r.get("speed") or 0) > s["moving_kmh"]

    def hop(a, b):
        dt = (b["t"] - a["t"]).total_seconds()
        dm = _m(a["lat"], a["lon"], b["lat"], b["lon"])
        kmh = dm / dt * 3.6 if dt > 0 else math.inf
        return dt, dm, kmh

    def is_jump(dm, kmh):
        return dm > s["jump_min_m"] and kmh > s["jump_kmh"]

    excluded: set[int] = set()
    stale_keys: set = set()                         # one stale point is often left and re-entered
    for a, b in zip(v, v[1:]):
        dt, dm, kmh = hop(a, b)
        out["naive_km"] += dm / 1000
        # a position with no satellites is not a fix: a long hop to or from one is a jump at any speed
        # (the hop into a stale point often spans a sleep, so its speed looks believable)
        if not (is_jump(dm, kmh) or (dm > s["jump_min_m"] and 0 in (a.get("sats"), b.get("sats")))):
            continue
        out["jump_km"] += dm / 1000
        stale_side = a if a.get("sats") == 0 else b if b.get("sats") == 0 else None
        rec = {"time": _iso(b["t"]), "from_time": _iso(a["t"]), "metres": round(dm), "kmh": round(kmh) if kmh != math.inf else None,
               "seconds": dt, "from": [a["lat"], a["lon"]], "to": [b["lat"], b["lon"]],
               "sats": [a.get("sats"), b.get("sats")], "types": [a.get("type"), b.get("type")], "k": b["k"], "k_from": a["k"],
               "kind": "stale" if stale_side is not None else "jump"}
        if stale_side is not None:
            stale_keys.add((stale_side["lat"], stale_side["lon"], stale_side["t"].replace(second=0, microsecond=0)))
            excluded.add(stale_side["k"])
            flags[stale_side["k"]].add("stale")
            rec["stale"] = [stale_side["lat"], stale_side["lon"]]
            out["stale"].append(rec)
        else:
            out["jumps"].append(rec)
        flags[b["k"]].add("jump")
    # a point whose hops in and out are both jumps is a spike: leave it out of the track too
    for x, y, z in zip(v, v[1:], v[2:]):
        _, d1, k1 = hop(x, y)
        _, d2, k2 = hop(y, z)
        if is_jump(d1, k1) and is_jump(d2, k2) and _m(x["lat"], x["lon"], z["lat"], z["lon"]) < 0.3 * min(d1, d2):
            excluded.add(y["k"])
            flags[y["k"]].add("spike")
    # a stale point repeats until the first real fix: leave out every 0-satellite row at a stale position
    stale_pos = {(r["lat"], r["lon"]) for r in v if r["k"] in excluded}
    for r in v:
        if (r["lat"], r["lon"]) in stale_pos and (r.get("sats") in (0, None) or r["k"] in excluded):
            excluded.add(r["k"])
    out["excluded"] = sorted(excluded)
    out["stale_points"] = len(stale_keys)
    clean = [r for r in v if r["k"] not in excluded]
    comparable = mismatch = frozen = frozen_first = 0
    prev_ins = {r["k"]: r.get("ins") for r in rows}
    for a, b in zip(clean, clean[1:]):
        dt, dm, kmh = hop(a, b)
        if is_jump(dm, kmh):
            continue                                 # counted above
        if dt > gap_s:
            continue
        if moving(a) or moving(b):
            out["driven_km"] += dm / 1000
        elif not (a.get("speed") or b.get("speed")):
            out["drift_km"] += dm / 1000            # standing still, yet the position moved
        if (a["lat"], a["lon"]) == (b["lat"], b["lon"]) and (b.get("speed") or 0) > s["frozen_kmh"] \
                and (a.get("speed") or 0) > s["frozen_kmh"] and 0 < dt <= 2 * step:
            frozen += 1
            flags[b["k"]].add("frozen")
            if b["k"] > 0 and prev_ins.get(b["k"] - 1) != b.get("ins"):
                frozen_first += 1
        if 0 < dt <= 3 * step and a.get("speed") is not None and b.get("speed") is not None:
            comparable += 1
            rep = (a["speed"] + b["speed"]) / 2
            if abs(rep - kmh) > s["speed_diff_kmh"] and max(rep, kmh) > 20:
                mismatch += 1
                flags[b["k"]].add("speed")
    moving_rows = sum(1 for r in clean if moving(r))
    out.update(frozen=frozen, frozen_batch_first=frozen_first,
               frozen_pct=100 * frozen / moving_rows if moving_rows else 0.0,
               speed_mismatch=mismatch, speed_mismatch_pct=100 * mismatch / comparable if comparable else 0.0,
               phantom_km=out["jump_km"] + out["drift_km"])
    for k_ in ("phantom_km", "jump_km", "drift_km", "driven_km", "naive_km"):
        out[k_] = round(out[k_], 3)
    by_type = defaultdict(list)
    for r in with_sats:
        by_type[r.get("type") or ""].append(r["sats"])
    for t, vals in by_type.items():
        if len(vals) >= 100 and len(set(vals)) == 1 and vals[0] > 0:
            out["placeholder_sats"].append({"type": t, "value": vals[0], "rows": len(vals)})
    return out


def _power(rows: list[dict], ffill: list, transitions: list | None = None, flags=None) -> dict:
    out = {"rows": 0, "system": 12, "resting": None, "running": None, "cuts": [], "on": None, "off": None,
           "starts": []}
    sup = [(r, _volts(r.get("supply"))) for r in rows if r.get("supply") is not None]
    out["line"] = _power_line(rows, ffill)
    if not sup:
        out.update(_internal(rows))
        out.update(_internal_episodes(rows, flags))
        return out
    out["rows"] = len(sup)
    allv = sorted(x for _, x in sup)
    out["system"] = 24 if statistics.median(allv) > 18 else 12
    on = sorted(x for r, x in sup if ffill[r["k"]] == 1)
    off = sorted(x for r, x in sup if ffill[r["k"]] == 0)

    def stat(vals):
        return {"min": vals[0], "median": statistics.median(vals), "max": vals[-1], "rows": len(vals)} if vals else None
    out["on"], out["off"] = stat(on), stat(off)
    out["resting"] = round(statistics.median(off), 2) if len(off) >= 10 else None
    out["running"] = round(statistics.median(on), 2) if len(on) >= 10 else None
    # each time the ignition comes on: the lowest supply from just before to a minute after — the
    # starter motor's pull on the battery (a healthy 12 V battery stays above ~9.6 V)
    windows = []
    # an engine start: the ignition input coming on, or the device's own ignition-on alert (a log may
    # begin with one, before any change of the input is seen)
    times = sorted([tr["time"] for tr in transitions or [] if tr["to"] == 1] +
                   [r["t"] for r in rows if (r.get("od") or {}).get("alertId") == "7"])
    kept = []
    for t_ in times:
        if not kept or (t_ - kept[-1]).total_seconds() > 10:
            kept.append(t_)
    for t_ in kept:
        tr = {"time": t_}
        a_, b_ = tr["time"] - timedelta(seconds=5), tr["time"] + timedelta(seconds=60)
        near = [(r, x) for r, x in sup if a_ <= r["t"] <= b_]
        if not near:
            continue
        r_low, low = min(near, key=lambda q: q[1])
        out["starts"].append({"time": _iso(tr["time"]), "lowest": round(low, 2), "k": r_low["k"]})
        windows.append((a_, b_))
        if flags is not None and low < 11.5 * out["system"] / 12:
            flags[r_low["k"]].add("start_dip")
    in_start = lambda t: any(a_ <= t <= b_ for a_, b_ in windows)
    # the main battery missing: the supply under a running vehicle's floor, or the device's own flag
    # (power 0) — outside an engine start, where a dip is the starter, not a missing battery
    cut_v = 7 * out["system"] / 12
    out["cuts"] = _episodes([(r, x) for r, x in sup],
                            lambda r, x: (x < cut_v or r.get("power") == 0) and not in_start(r["t"]),
                            flags, "main_missing")
    out.update(_internal(rows))
    out.update(_internal_episodes(rows, flags))
    return out


def _power_line(rows: list[dict], ffill: list) -> list:
    """For the chart: [time, main supply V, internal battery V, ignition, internal %] — a 0 V internal
    reading is no reading. Thinned to ~1,500 points keeping each stretch's lowest supply, so a dip
    at engine start still shows."""
    pts = []
    for r in rows:
        sv_, iv = _volts(r.get("supply")), _volts(r.get("ibatt_v"))
        if sv_ is None and iv is None:
            continue
        pts.append([_iso(r["t"]), round(sv_, 2) if sv_ is not None else None, round(iv, 2) if iv is not None else None,
                    ffill[r["k"]], r.get("ibatt_pct")])
    if len(pts) <= 3000:
        return pts
    stride = len(pts) // 1500 + 1
    out = []
    for i in range(0, len(pts), stride):
        chunk = pts[i:i + stride]
        out.append(min(chunk, key=lambda p: p[1] if p[1] is not None else 1e9))
    return out


def _episodes(readings, bad, flags, flag) -> list[dict]:
    """Consecutive readings (row, value) where bad(row, value) holds → [{start, end, seconds, rows, lowest, k}],
    each row flagged."""
    out, cur = [], None
    for r, x in readings:
        if bad(r, x):
            if flags is not None:
                flags[r["k"]].add(flag)
            if cur is None:
                cur = {"start": _iso(r["t"]), "end": _iso(r["t"]), "k": r["k"], "lowest": x, "rows": 0, "_t0": r["t"]}
            cur["end"], cur["rows"] = _iso(r["t"]), cur["rows"] + 1
            cur["lowest"] = min(cur["lowest"], x) if x is not None else cur["lowest"]
            cur["_t1"] = r["t"]
        elif cur is not None:
            cur["seconds"] = (r["t"] - cur.pop("_t0")).total_seconds()      # until the reading came back
            cur.pop("_t1", None)
            out.append(cur)
            cur = None
    if cur is not None:
        cur["seconds"] = (cur.pop("_t1") - cur.pop("_t0")).total_seconds()   # still missing at the log's end
        cur["to_end"] = True
        out.append(cur)
    for e in out:
        e["duration"] = _dur(e["seconds"])
    return out


def _internal_episodes(rows, flags) -> dict:
    """The internal battery missing (it reads 0 V while the device reports) and low."""
    readings = [(r, _volts(r["ibatt_v"])) for r in rows if r.get("ibatt_v") is not None]
    if not readings or not any(x and x > 0 for _, x in readings):
        return {"internal_missing": [], "internal_low": []}       # this device does not read its internal battery
    missing = _episodes(readings, lambda r, x: x == 0, flags, "internal_missing")
    low = _episodes(readings, lambda r, x: (x is not None and 0 < x < INTERNAL_LOW_V)
                    or (x and r.get("ibatt_pct") is not None and r["ibatt_pct"] <= 20), flags, "internal_low")
    return {"internal_missing": missing, "internal_low": low}


def _internal(rows: list[dict]) -> dict:
    allv = [_volts(r["ibatt_v"]) for r in rows if r.get("ibatt_v") is not None]
    zero = sum(1 for v in allv if v == 0)
    iv = [v for v in allv if v > 0]
    ip = [r["ibatt_pct"] for r in rows if r.get("ibatt_pct") is not None]
    if not iv and not ip:
        return {}
    parts = []
    if iv:
        parts.append(f"{min(iv):.2f}–{max(iv):.2f} V" if min(iv) != max(iv) else f"{iv[0]:.2f} V")
    if ip:
        parts.append(f"{min(ip):.0f}–{max(ip):.0f}%" if min(ip) != max(ip) else f"{ip[0]:.0f}%")
    if zero:
        parts.append(f"0 V in {zero} rows")
    return {"internal": {"text": "Internal battery " + ", ".join(parts), "zero_rows": zero,
                         "volts": [min(iv), max(iv)] if iv else None, "level": [min(ip), max(ip)] if ip else None}}


def _driving(rows, flags, s, gap_s, gps, on_s, off_s, idle_s) -> dict:
    excluded = set(gps.get("excluded") or [])
    mv = [r for r in rows if (r.get("speed") or 0) > s["moving_kmh"] and r["k"] not in excluded]
    trips = []
    for r in mv:
        if trips and (r["t"] - trips[-1]["_end"]["t"]).total_seconds() <= s["trip_stop_min"] * 60:
            trips[-1]["_end"] = r
            trips[-1]["_rows"].append(r)
        else:
            trips.append({"_start": r, "_end": r, "_rows": [r]})
    clean = [r for r in rows if _valid(r) and r["k"] not in excluded]
    out_trips = []
    for i, tr in enumerate(trips, 1):
        a, b = tr["_start"], tr["_end"]
        seg = [r for r in clean if a["t"] <= r["t"] <= b["t"]]
        dist = 0.0
        for x, y in zip(seg, seg[1:]):
            dt = (y["t"] - x["t"]).total_seconds()
            dm = _m(x["lat"], x["lon"], y["lat"], y["lon"])
            if dt <= gap_s and not (dm > s["jump_min_m"] and (dt <= 0 or dm / dt * 3.6 > s["jump_kmh"])):
                dist += dm
        speeds = [r["speed"] for r in tr["_rows"]]
        dur = (b["t"] - a["t"]).total_seconds()
        out_trips.append({"n": i, "start": _iso(a["t"]), "end": _iso(b["t"]), "seconds": dur, "duration": _dur(dur),
                          "km": round(dist / 1000, 2), "max_speed": max(speeds),
                          "avg_speed": round(sum(speeds) / len(speeds), 1),
                          "from": [a.get("lat"), a.get("lon")], "to": [b.get("lat"), b.get("lon")],
                          "rows": len(seg), "k": a["k"], "k_end": b["k"]})
    over = []
    cur = None
    for r in rows:
        if (r.get("speed") or 0) > s["overspeed_kmh"] and r["k"] not in excluded:
            flags[r["k"]].add("overspeed")
            if cur and (r["t"] - cur["_last"]).total_seconds() <= gap_s:
                cur["_last"], cur["max"] = r["t"], max(cur["max"], r["speed"])
            else:
                cur = {"start": _iso(r["t"]), "_last": r["t"], "max": r["speed"], "k": r["k"],
                       "at": [r.get("lat"), r.get("lon")]}
                over.append(cur)
    for e in over:
        e["seconds"] = (e.pop("_last") - datetime.fromisoformat(e["start"])).total_seconds()
        e["duration"] = _dur(e["seconds"])
    events = []
    for r in rows:
        if r.get("green"):
            flags[r["k"]].add("event")
            od = r.get("od") or {}
            events.append({"time": _iso(r["t"]), "code": r["green"], "value": r.get("green_value") or "",
                           "name": alert_name(od.get("PacketType", ""), od.get("alertId", ""))
                           if od.get("alertId") in HARSH_ALERTS else DRIVING_EVENTS.get(r["green"], ""),
                           "speed": r.get("speed"), "at": [r.get("lat"), r.get("lon")], "type": r.get("type"),
                           "k": r["k"]})
    ign = []
    last = None
    for r in rows:
        if r.get("ign") in (0, 1):
            if last is not None and r["ign"] != last:
                ign.append({"time": _iso(r["t"]), "to": r["ign"], "k": r["k"]})
            last = r["ign"]
    return {"trips": out_trips, "distance_km": round(sum(t["km"] for t in out_trips), 2),
            "max_speed": max((t["max_speed"] for t in out_trips), default=0),
            "overspeed": over, "events": events, "ignition": ign,
            "ignition_on_s": on_s, "ignition_off_s": off_s, "idle_s": idle_s}


def _payload(rows, ffill, s, step, hours, size) -> dict:
    out: dict[str, Any] = {"mb_per_hour": (size / 1e6) / hours if hours else 0}
    # IMU (our custom packet in otherData)
    packets = parked = 0
    spp = Counter()
    motion_min: set = set()
    for r in rows:
        other = r.get("other") or ""
        if "imu=(" not in other:
            continue
        m = _IMU_BLOCK.search(other)
        if not m:
            continue
        samp = _IMU_SAMPLE.findall(m.group(1))
        if not samp:
            continue
        packets += 1
        spp[len(samp)] += 1
        off = ffill[r["k"]] != 1 and (r.get("speed") or 0) == 0
        if off:
            parked += 1
        mags = [math.sqrt(float(x) ** 2 + float(y) ** 2 + float(z) ** 2) for x, y, z in samp]
        if off and len(mags) > 2 and statistics.pstdev(mags) > s["parked_motion_ms2"]:
            motion_min.add(r["t"].replace(second=0))
    if packets:
        per = spp.most_common(1)[0][0]
        out["imu"] = {"packets": packets, "samples_per_packet": per, "rate_hz": per / step if step else 0,
                      "parked_packets": parked, "parked_pct": 100 * parked / packets,
                      "motion_minutes": len(motion_min), "motion_times": sorted(_iso(t) for t in motion_min)[:200]}
    # CAN values (canData)
    keys: dict[str, list] = defaultdict(list)
    for r in rows:
        for k, v in (r.get("can") or {}).items():
            keys[k].append(v)
    can = []
    spikes = []
    for k, vals in keys.items():
        nums = []
        for v in vals:
            try:
                nums.append(float(v))
            except ValueError:
                pass
        entry = {"key": k, "rows": len(vals), "distinct": len(set(vals)),
                 "top": Counter(vals).most_common(6)}
        if nums and len(nums) >= 0.9 * len(vals):
            sn = sorted(nums)
            entry.update(min=sn[0], max=sn[-1], mean=round(sum(sn) / len(sn), 2))
            lo, hi = _q(sn, .01), _q(sn, .99)
            width = (hi - lo) or 1.0
            far = [x for x in sn if x > hi + 3 * width or x < lo - 3 * width]
            if far and len(far) <= max(3, len(sn) // 1000):
                spikes.append({"key": k, "value": max(far, key=lambda x: abs(x - hi)), "count": len(far),
                               "low": lo, "high": hi})
        can.append(entry)
    if can:
        out["can"] = sorted(can, key=lambda e: e["key"])
    out["spikes"] = spikes
    mism = []
    for alert, value in ALERT_PAIRS:
        if alert not in keys or value not in keys:
            continue
        set_, clear = [], []
        for r in rows:
            c = r.get("can") or {}
            try:
                a, v = float(c[alert]), float(c[value])
            except (KeyError, ValueError):
                continue
            (set_ if a > 0 else clear).append(v)
        if len(set_) >= 20 and len(clear) >= 20:
            ms, mc = statistics.mean(set_), statistics.mean(clear)
            if ms - mc < 1.0:
                mism.append({"alert": alert, "value": value, "mean_set": ms, "mean_clear": mc, "rows": len(set_)})
    out["alert_mismatch"] = mism
    cs = [r["can_speed"] for r in rows if r.get("can_speed") is not None]
    if len(cs) >= 100 and not any(cs):
        out["can_speed_zero"] = len(cs)
    return out


def _device(rows, flags, transitions, frames, power=None) -> dict:
    """The device's own alerts (AIS-140: PacketType + alertId in otherData), their agreement with the
    inputs they report, its starts (with what its boot report says), firmware, tamper."""
    out = {"alerts": [], "codes": [], "alerts_total": 0, "unknown_codes": [], "ota": 0, "emergency": 0,
           "tamper_open": 0, "firmware": [], "firmware_changes": [], "boots": [], "restarts": [],
           "flicker": [], "ignition": {"checked": False, "disagree": 0, "missing": 0, "repeated": 0, "rows": []}}
    codes = Counter()
    unknown = set()
    ign_alerts = []
    for r in rows:
        od = r.get("od") or {}
        code, aid = od.get("PacketType", ""), od.get("alertId", "")
        if od.get("deviceTamper") not in (None, "", "C"):
            out["tamper_open"] += 1
            flags[r["k"]].add("alert")
        if not code and not aid:
            continue
        if aid in ROUTINE_ALERTS or code in ROUTINE_CODES:
            continue
        codes[code or aid] += 1
        if aid not in AIS140_ALERTS and code not in DEVICE_PACKETS:
            unknown.add(f"{code} ({aid})" if code else aid)
        rec = {"time": _iso(r["t"]), "code": code, "id": aid, "name": alert_name(code, aid), "k": r["k"],
               "ign": r.get("ign"), "speed": r.get("speed"), "supply": _volts(r.get("supply")), "type": r.get("type"),
               "power": r.get("power")}
        out["alerts"].append(rec)
        flags[r["k"]].add("alert")
        if aid == "12":
            out["ota"] += 1
        if aid == "10":
            out["emergency"] += 1
        if aid in ("7", "8"):
            ign_alerts.append(rec)
    out["alerts_total"] = len(out["alerts"])
    out["codes"] = codes.most_common()
    out["unknown_codes"] = sorted(unknown)
    # ignition alerts against the ignition input (only when the device sends them at all)
    if ign_alerts:
        ia = out["ignition"]
        ia["checked"] = True
        last = None
        for a in ign_alerts:
            want = 1 if a["id"] == "7" else 0
            wrong = a["ign"] in (0, 1) and a["ign"] != want
            repeated = last == a["id"]
            if wrong:
                ia["disagree"] += 1
                flags[a["k"]].add("alert_mismatch")
            if repeated:
                ia["repeated"] += 1
            ia["rows"].append({**a, "disagrees": wrong, "repeated": repeated})
            last = a["id"]
        times = [(datetime.fromisoformat(a["time"]), a["id"]) for a in ign_alerts]
        for tr in transitions:
            want = "7" if tr["to"] == 1 else "8"
            if not any(i == want and abs((t - tr["time"]).total_seconds()) <= 30 for t, i in times):
                ia["missing"] += 1
                flags[tr["k"]].add("alert_mismatch")
    # the ignition flickering: a change within 30 s of the previous one
    for a, b in zip(transitions, transitions[1:]):
        if (b["time"] - a["time"]).total_seconds() <= 30:
            out["flicker"].append({"time": _iso(b["time"]), "to": b["to"], "after_s": (b["time"] - a["time"]).total_seconds(),
                                   "k": b["k"]})
            flags[b["k"]].add("flicker")
    # starts: the frame counter back to the start; a boot report belongs to the last start before it
    starts = frames.get("starts") or []
    for r in rows:
        if "RST:" not in (r.get("response") or ""):
            continue
        before = [st for st in starts if datetime.fromisoformat(st["time"]) <= r["t"] + timedelta(seconds=5)]
        if not before:
            continue
        st = before[-1]
        if (r["t"] - datetime.fromisoformat(st["time"])).total_seconds() > 300 or st.get("boot"):
            continue
        inner = r["response"].split("(", 1)[-1].split(")", 1)[0]
        facts = dict(x.split(":", 1) for x in inner.split(";") if ":" in x)
        st["boot"] = facts
        out["boots"].append({"time": _iso(r["t"]), "text": ", ".join(f"{k} {v}" for k, v in facts.items()), "facts": facts,
                             "k": r["k"]})
    out["restarts"] = [st for st in starts if not st.get("first_row")]
    # deep sleep (DSEN): announced only with the internal battery low and the main supply gone — then
    # the device should fall silent. Say what the readings were, and whether it really went quiet.
    out["deep_sleep"] = []
    start_windows = [(datetime.fromisoformat(x["time"]) - timedelta(seconds=5),
                      datetime.fromisoformat(x["time"]) + timedelta(seconds=60)) for x in (power or {}).get("starts", [])]
    system = (power or {}).get("system") or 12
    for i, r in enumerate(rows):
        if (r.get("od") or {}).get("PacketType") != DEEP_SLEEP:
            continue
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        after = (nxt["t"] - r["t"]).total_seconds() if nxt else None
        supply = _volts(r.get("supply"))
        main_gone = r.get("power") == 0 or (supply is not None and supply < 7 * system / 12)
        internal_v = _volts(r.get("ibatt_v"))
        internal_low = (r.get("ibatt_pct") is not None and r["ibatt_pct"] <= 20) or (internal_v is not None and 0 < internal_v < 3.6)
        out["deep_sleep"].append({
            "time": _iso(r["t"]), "k": r["k"], "supply": supply, "power": r.get("power"),
            "internal_v": internal_v, "internal_pct": r.get("ibatt_pct"), "main_gone": main_gone,
            "internal_low": internal_low, "next_after_s": after, "next_time": _iso(nxt["t"]) if nxt else None,
            "slept": after is None or after > DEEP_SLEEP_QUIET_S,
            "during_start": any(a_ <= r["t"] <= b_ for a_, b_ in start_windows)})
        flags[r["k"]].add("deep_sleep")
    # firmware: the versions in the order they appear
    for r in rows:
        fw = r.get("firmware")
        if fw and (not out["firmware"] or out["firmware"][-1] != fw):
            if out["firmware"]:
                out["firmware_changes"].append({"time": _iso(r["t"]), "from": out["firmware"][-1], "to": fw, "k": r["k"]})
            out["firmware"].append(fw)
    return out


def _network(rows) -> dict:
    """The cell the device reports: missing (0 or blank) while it has a GSM signal, and how often it changes."""
    if not any("cell" in r for r in rows):
        return {}
    have = [r for r in rows if (r.get("gsm") or 0) > 0]
    if not any((r.get("cell") or "0") not in ("0", "") for r in have):
        return {"considered": 0, "never": True}       # this device does not report its cell at all
    missing = [r for r in have if (r.get("cell") or "0") in ("0", "") or (r.get("mcc") or "") == "0"]
    cells = [r.get("cell") for r in have if (r.get("cell") or "0") not in ("0", "")]
    changes = sum(1 for a, b in zip(cells, cells[1:]) if a != b)
    gsm = sorted(r["gsm"] for r in have)
    return {"considered": len(have), "missing": len(missing), "missing_pct": 100 * len(missing) / len(have) if have else 0,
            "cells": len(set(cells)), "changes": changes, "gsm_median": gsm[len(gsm) // 2] if gsm else None,
            "operators": Counter(f"{r.get('mcc')}/{r.get('mnc')}" for r in have if (r.get("mcc") or "0") != "0").most_common(4)}


def _types(log: DriveLog) -> list[dict]:
    by = defaultdict(list)
    for k, r in enumerate(log.rows):
        by[r.get("type") or ""].append(k)
    n = len(log.rows)
    out = []
    for t, ks in sorted(by.items(), key=lambda x: -len(x[1])):
        rs = [log.rows[k] for k in ks]
        filled = [c for ci, c in enumerate(log.columns) if sum(1 for k in ks if log.raw[k][ci]) > 0.5 * len(ks)]
        sats = Counter(r.get("sats") for r in rs if r.get("sats") is not None)
        out.append({"type": t, "name": type_name(t), "rows": len(ks), "pct": round(100 * len(ks) / n, 1),
                    "first": _iso(rs[0]["t"]), "last": _iso(rs[-1]["t"]),
                    "with_position_pct": round(100 * sum(1 for r in rs if _valid(r)) / len(rs), 1),
                    "moving_pct": round(100 * sum(1 for r in rs if (r.get("speed") or 0) > 0) / len(rs), 1),
                    "sats": sats.most_common(3), "history": sum(1 for r in rs if r.get("history")),
                    "columns": filled})
    return out


def _columns(log: DriveLog) -> list[dict]:
    n = len(log.raw)
    types = [r.get("type") or "" for r in log.rows]
    tcount = Counter(types)
    out = []
    for ci, c in enumerate(log.columns):
        vals = [row[ci] for row in log.raw]
        filled = [v for v in vals if v != ""]
        avg = sum(len(v) for v in filled[:200]) / max(1, min(200, len(filled)))
        cnt = Counter(filled) if avg < 300 else None
        entry = {"column": c, "filled_pct": round(100 * len(filled) / n, 1) if n else 0,
                 "distinct": len(cnt) if cnt is not None else len(set(filled)),
                 "top": [[v[:48], k] for v, k in cnt.most_common(3)] if cnt is not None else [],
                 "by_type": {t: round(100 * sum(1 for k, v in enumerate(vals) if v != "" and types[k] == t) / tc, 1)
                             for t, tc in tcount.items()} if len(tcount) <= 12 else {}}
        nums = []
        for v in filled[:5000]:
            try:
                nums.append(float(v))
            except ValueError:
                break
        if filled and len(nums) == min(len(filled), 5000):
            entry["min"], entry["max"] = min(nums), max(nums)
        out.append(entry)
    return out


def _series(rows, ffill, t0, t1, gaps, gps, driving, delivery, frames, flags, device=None, power=None,
            gap_s: float = 300, s: dict | None = None) -> dict:
    span = max(1.0, (t1 - t0).total_seconds())
    bucket = next((b for b in (10, 30, 60, 120, 300, 600, 900, 1800, 3600) if span / b <= 1500), 3600)
    buckets: dict[int, dict] = {}
    for r in rows:
        i = int((r["t"] - t0).total_seconds() // bucket)
        b = buckets.get(i)
        if b is None:
            b = buckets[i] = {"i": i, "n": 0, "speed": 0.0, "ign": None, "hist": 0, "lat": 0.0, "sats": None,
                              "lats": []}
        b["n"] += 1
        b["speed"] = max(b["speed"], r.get("speed") or 0)
        if ffill[r["k"]] is not None:
            b["ign"] = ffill[r["k"]]
        if r.get("history"):
            b["hist"] += 1
        if r.get("ins") is not None:
            d = (r["ins"] - r["t"]).total_seconds()      # made by the device → landed on the platform
            b["lat"] = max(b["lat"], d)
            b["lats"].append(d)
        if r.get("sats") is not None:
            b["sats"] = r["sats"] if b["sats"] is None else min(b["sats"], r["sats"])
    pts = [[_iso(t0 + timedelta(seconds=b["i"] * bucket)), b["n"], b["speed"], b["ign"], b["hist"], round(b["lat"], 1),
            b["sats"], round(statistics.median(b["lats"]), 1) if b["lats"] else None]
           for b in sorted(buckets.values(), key=lambda b: b["i"])]
    markers = []
    for x in gps["stale"]:
        markers.append({"t": x["time"], "kind": "stale", "k": x["k"]})
    for x in gps["jumps"]:
        markers.append({"t": x["time"], "kind": "jump", "k": x["k"]})
    for t in delivery["no_position_times"]:
        markers.append({"t": t, "kind": "noposition"})
    for e in driving["events"]:
        markers.append({"t": e["time"], "kind": "event", "k": e["k"]})
    for e in driving["overspeed"]:
        markers.append({"t": e["start"], "kind": "overspeed", "k": e["k"]})
    for h in frames["holes"]:
        if not h["periodic"]:
            markers.append({"t": h["time"], "kind": "frames", "k": h["k"]})
    for e in (power or {}).get("cuts", []) + (power or {}).get("internal_missing", []):
        markers.append({"t": e["start"], "kind": "battery", "k": e["k"]})
    for st in (device or {}).get("restarts", []):
        markers.append({"t": st["time"], "kind": "restart", "k": st["k"]})
    for a in (device or {}).get("alerts", []):
        if a["id"] not in ("7", "8"):                 # the ignition is its own band on the timeline
            markers.append({"t": a["time"], "kind": "alert", "k": a["k"]})
    # the track: clean positions, split where the vehicle did not really go (a jump) or where there is
    # no data (a gap) — a gap is kept as a break, drawn dotted from the last position before it to the
    # first after it: red while the ignition was on (data lost), grey while it was off (asleep). A long
    # hop at a believable speed is driving (a 10 s device on a highway), never a break.
    s = s or SETTINGS
    excluded = set(gps.get("excluded") or [])
    jump_ks = {x["k"] for x in gps.get("jumps") or []}
    clean = [r for r in rows if _valid(r) and r["k"] not in excluded]
    segs, cur, breaks = [], [], []
    for a, b in zip([None] + clean, clean):
        if a is not None:
            dt = (b["t"] - a["t"]).total_seconds()
            far = _m(a["lat"], a["lon"], b["lat"], b["lon"])
            jump = b["k"] in jump_ks or (far > s["jump_min_m"] and far / max(dt, 1.0) * 3.6 > s["jump_kmh"])
            if dt > gap_s or jump:
                if len(cur) > 1:
                    segs.append(cur)
                cur = []
                if dt > gap_s and far > 20:
                    state = ffill[a["k"]]
                    breaks.append({"from": [round(a["lat"], 6), round(a["lon"], 6)], "to": [round(b["lat"], 6), round(b["lon"], 6)],
                                   "start": _iso(a["t"]), "end": _iso(b["t"]), "seconds": dt, "duration": _dur(dt),
                                   "kind": "loss" if state == 1 else "sleep" if state == 0 else "unknown",
                                   "metres": round(far), "k_from": a["k"], "k": b["k"]})
        cur.append(b)
    if len(cur) > 1:
        segs.append(cur)
    total = sum(len(x) for x in segs) or 1
    stride = max(1, total // 4000)
    # each point: latitude, longitude, speed, row, and 1 when the platform marked it history (sent later)
    track = [[[round(r["lat"], 6), round(r["lon"], 6), r.get("speed") or 0, r["k"], 1 if r.get("history") else 0]
              for r in seg[::stride] + [seg[-1]]] for seg in segs]
    return {"bucket_s": bucket, "start": _iso(t0), "end": _iso(t1),
            "points": pts, "fields": ["t", "rows", "speed", "ign", "history", "latency", "sats", "latency_median"],
            "gaps": [{"start": g["start"], "end": g["end"], "kind": g["kind"]} for g in gaps],
            "markers": markers, "track": track, "breaks": breaks,
            "stale": [_hop(x) for x in gps["stale"]], "jumps": [_hop(x) for x in gps["jumps"]]}


def _hop(x: dict) -> dict:
    """A jump or a stale position as the map reads it: both ends, when, how far, how fast, satellites."""
    return {k: x.get(k) for k in ("from", "to", "stale", "time", "from_time", "metres", "kmh", "seconds", "sats", "k", "k_from", "kind")}


def _headlines(areas, frames, delivery, gps, power, driving, payload, on_s, loss_s, device=None) -> None:
    """The figure each area's tile shows."""
    for a in areas:
        if a["id"] == "data":
            cov = 100 * on_s / (on_s + loss_s) if on_s + loss_s else None
            if cov is not None:
                a["figure"] = f"{cov:.1f}%"
                a["caption"] = f"{_dur(loss_s)} lost" if loss_s else "Ignition-on time covered"
            elif frames["numbered"]:
                a["figure"] = f"{100 - frames['lost_pct']:.2f}%"
                a["caption"] = "Frames received"
            else:
                a["figure"], a["caption"] = "—", "Never switched on"
        elif a["id"] == "delivery":
            p90 = delivery["latency"]["p90"]
            a["figure"] = _dur(p90) if p90 is not None else "—"
            a["caption"] = "90% stored within"
        elif a["id"] == "gps":
            fix = 100 - gps["no_fix_pct"]
            a["figure"] = f"{fix:.1f}%"
            a["caption"] = f"{len(gps['stale']) + len(gps['jumps'])} jumps · {gps['phantom_km']:.1f} km phantom"
        elif a["id"] == "power":
            v = power.get("resting") or power.get("running")
            a["figure"] = f"{v:.1f} V" if v is not None else "—"
            a["caption"] = "Battery, ignition off" if power.get("resting") is not None else "Supply, ignition on"
        elif a["id"] == "driving":
            a["figure"] = f"{driving['distance_km']:.1f} km"
            a["caption"] = f"{len(driving['trips'])} trip{'s' if len(driving['trips']) != 1 else ''} · top {_n(driving['max_speed'])} km/h"
        elif a["id"] == "device":
            d = device or {}
            # the alerts in front (the user, 17-11-2026): how many, then the three most frequent by name
            total = d.get("alerts_total", 0)
            a["figure"] = f"{_n(total)} alert{'s' if total != 1 else ''}"
            names = {}
            for x in d.get("alerts") or []:
                names.setdefault(x["code"], x["name"].rsplit(" (", 1)[0] if x["name"].endswith(")") else x["name"])
            counts = d.get("codes") or []
            a["caption"] = " · ".join(f"{names.get(c, c)} {_n(k)}" for c, k in counts[:3]) or "No alerts"
            nr = len(d.get("restarts") or [])
            fw = (d.get("firmware") or [""])[-1]
            a["tip"] = " · ".join(x for x in (f"{nr} restart{'s' if nr != 1 else ''}", f"firmware {fw}" if fw else "",
                                               ", ".join(f"{names.get(c, c)} {_n(k)}" for c, k in counts[:8])) if x)
        elif a["id"] == "payload":
            imu = payload.get("imu")
            if imu:
                a["figure"], a["caption"] = f"{imu['rate_hz']:.0f} Hz", f"IMU · {_n(imu['packets'])} packets"
            else:
                a["figure"], a["caption"] = f"{len(payload.get('can') or [])}", "CAN values"
