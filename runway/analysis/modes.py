"""Packet generation by the device's mode — how evenly it makes packets, and whether it makes enough.

A tracking device reports at one of three rates (the user, 17-11-2026):

    active    the ignition is on
    standby   the ignition went off; the device stays awake for a while (``standby_min``)
    sleep     after that — or after its deep-sleep packet (DSEN) — the slowest rate, to save energy

Each mode's expected interval comes from a device profile — the user's own (Settings), matched by
the device or its firmware — and, for anything the profile leaves out, from this log itself: the
interval it shows most in that mode. Then, per mode:

    consistency   the steps between packets: on time (within a tolerance), early, late
    sufficiency   the packets made ÷ (time in the mode ÷ the interval) — too few, enough, too many
    delay         from making a packet to it landing on the platform

Every packet counts, whatever its type (the user's choice). A step across an ignition change
belongs to neither mode — when the change happened between the two rows is not known; any other
step belongs to the mode of the packet that ends it, so the first ping after falling asleep, which
the device waited in sleep, is a sleep step.

Pure computation, like ``analyse``: rows in, plain dicts out.
"""

from __future__ import annotations

import statistics
from collections import Counter

MODES = [("active", "Active"), ("standby", "Standby"), ("sleep", "Sleep")]
FIELDS = ("active_s", "standby_s", "standby_min", "sleep_s")      # what a profile says (any may be left out)
# Built-in device profiles, matched like the user's own. Empty until a device's figures are
# confirmed — never guess one in; the user's profiles (Settings) come first anyway.
PROFILES: list[dict] = []

MIN_STEPS = 20          # steps with the ignition on before the active interval is learned
MIN_OFF_STEPS = 5       # steps with it off before the standby interval is learned
SLEEP_REPEATS = 3       # a slow interval seen this often with the ignition off is the sleep interval
SLEEP_FLOOR_S = 120     # … and is at least this (and 3× the standby interval)
SLEEP_MATCH = 0.15      # a step within this share of the sleep interval is a sleep ping


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f >= 0 else None


def clean_profile(p: dict) -> dict:
    """A profile as the user typed it → the one the analysis reads (numbers or None)."""
    out = {"name": str(p.get("name") or "").strip()[:60],
           "devices": str(p.get("devices") or "").strip()[:400],
           "firmware": str(p.get("firmware") or "").strip()[:200]}
    for f in FIELDS:
        v = _num(p.get(f))
        out[f] = v
    return out


def match(profiles: list[dict], device: str | None, firmware: list[str] | None) -> dict | None:
    """The profile for this device: one naming the device itself first, then one whose firmware
    prefix matches a version the log shows."""
    dev = (device or "").strip()
    fws = [str(f).strip().lower() for f in firmware or [] if f]
    for p in profiles or []:
        if dev and dev in {d.strip() for d in str(p.get("devices") or "").split(",") if d.strip()}:
            return p
    for p in profiles or []:
        prefixes = [x.strip().lower() for x in str(p.get("firmware") or "").split(",") if x.strip()]
        if prefixes and any(f.startswith(x) for f in fws for x in prefixes):
            return p
    return None


def _step(rows, i) -> float:
    return (rows[i]["t"] - rows[i - 1]["t"]).total_seconds()


def _most(vals: list[float]) -> float | None:
    vals = [round(v) for v in vals if v > 0]
    return Counter(vals).most_common(1)[0][0] if vals else None


def _off_periods(ign: list) -> list[tuple[int, int, bool]]:
    """Each stretch with the ignition off: (first row, last row, whether the moment it went off is in
    the log — a log that opens with the ignition already off does not say how long it has been)."""
    out, start = [], None
    for i, st in enumerate(ign):
        if st == 0 and start is None:
            start = i
        elif st != 0 and start is not None:
            out.append((start, i - 1, start > 0 and ign[start - 1] == 1))
            start = None
    if start is not None:
        out.append((start, len(ign) - 1, start > 0 and ign[start - 1] == 1))
    return out


def learn(rows: list[dict], ign: list) -> dict:
    """The intervals this log shows: active, then — with the ignition off — sleep (a slow interval
    that repeats), how long standby lasts (until the first sleep ping), and the standby interval
    (the steps before sleep; every step off when the device never sleeps)."""
    n = len(rows)
    on = [_step(rows, i) for i in range(1, n) if ign[i - 1] == 1 and ign[i] == 1]
    out = {"active_s": _most(on) if len(on) >= MIN_STEPS else None,
           "standby_s": None, "standby_min": None, "sleep_s": None}
    periods = _off_periods(ign)
    steps = []                                     # (period, its start known, seconds since off at the step's start, step)
    for p, (s0, s1, known) in enumerate(periods):
        t_off = rows[s0]["t"]
        for i in range(s0 + 1, s1 + 1):
            d = _step(rows, i)
            if d > 0:
                steps.append((p, known, (rows[i - 1]["t"] - t_off).total_seconds(), d))
    slow = [d for *_, d in steps if d >= SLEEP_FLOOR_S]
    if len(slow) >= SLEEP_REPEATS:
        # every slow interval that repeats (within 10%) — the longest of them is sleep, so a slow
        # standby (2 min) is never taken for it; a one-off long silence never repeats enough
        need = max(SLEEP_REPEATS, 0.25 * len(slow))
        best = None
        for centre in sorted({round(d / 10) * 10 for d in slow}):
            near = [d for d in slow if abs(d - centre) <= 0.1 * centre]
            if len(near) >= need:
                best = near
        if best:
            out["sleep_s"] = round(statistics.median(best))
    if out["sleep_s"]:
        firsts = {}
        for p, known, at, d in steps:
            if known and p not in firsts and abs(d - out["sleep_s"]) <= SLEEP_MATCH * out["sleep_s"]:
                firsts[p] = at
        if firsts:
            out["standby_min"] = round(statistics.median(firsts.values()) / 30) / 2      # to the half minute
    if out["sleep_s"] and out["standby_min"] is not None:
        pool = [d for _, known, at, d in steps
                if known and at < out["standby_min"] * 60 and d < out["sleep_s"] * (1 - SLEEP_MATCH)]
    else:
        pool = [d for _, known, at, d in steps if known and at < 300]
        if len(pool) < MIN_OFF_STEPS:
            pool = [d for *_, d in steps]
    if len(pool) >= MIN_OFF_STEPS:
        out["standby_s"] = _most(pool)
    return out


def _assign(rows, ign, used, is_sleep_packet) -> list:
    """Each row's mode."""
    mode = ["active" if st == 1 else None for st in ign]
    sleep_s, standby_min = used.get("sleep_s"), used.get("standby_min")
    for s0, s1, known in _off_periods(ign):
        t_off = rows[s0]["t"]
        asleep = False
        for i in range(s0, s1 + 1):
            r = rows[i]
            if not asleep:
                if known and standby_min is not None:
                    asleep = (r["t"] - t_off).total_seconds() > standby_min * 60
                elif sleep_s and i > s0:             # how long it has been off is not known: from the first sleep ping
                    asleep = abs(_step(rows, i) - sleep_s) <= SLEEP_MATCH * sleep_s
            mode[i] = "sleep" if asleep else "standby"
            if is_sleep_packet(r):
                asleep = True                        # it said it is going to sleep: the rows after it are sleep
    return mode


def _q(vals: list[float], p: float) -> float | None:
    if not vals:
        return None
    v = sorted(vals)
    return v[min(len(v) - 1, int(p * (len(v) - 1) + 0.5))]


# the bands of interval a margin applies to: (up to seconds, the settings key of its margin)
MARGIN_BANDS = [(20, "margin_20s"), (120, "margin_2min"), (600, "margin_10min"), (None, "margin_over")]
MARGIN_DEFAULTS = {"margin_20s": 1, "margin_2min": 2, "margin_10min": 3, "margin_over": 5}


def margin_for(every: float, settings: dict) -> float:
    """How far from its interval a step may fall and still be on time — by the interval's band."""
    for upto, key in MARGIN_BANDS:
        if upto is None or every <= upto:
            return float(settings.get(key, MARGIN_DEFAULTS[key]))
    return float(MARGIN_DEFAULTS["margin_over"])


def _stats(vals: list[float]) -> dict:
    if not vals:
        return {"count": 0, "avg": None, "median": None, "min": None, "max": None, "p90": None}
    return {"count": len(vals), "avg": round(sum(vals) / len(vals), 1), "median": _q(vals, 0.5), "min": min(vals),
            "max": max(vals), "p90": _q(vals, 0.9)}


def generation(rows: list[dict], ign: list, settings: dict, profile: dict | None = None,
               fixed: dict | None = None, is_sleep_packet=lambda r: False) -> dict:
    """Per mode: the expected interval and where it came from; the packets made against those due,
    live and history; the interval between packets (average, median, min, max, how evenly); the
    delay to the platform (the same, live and history apart); the packet types behind it.

    Due = for each stretch in the mode, its length / the interval + 1 (its first packet) — a device
    that keeps its interval exactly makes 100%. ``fixed`` (a trip judged like its whole log)
    replaces both profile and log."""
    n = len(rows)
    learned = learn(rows, ign)
    used, source = {}, {}
    for f in FIELDS:
        if fixed and fixed.get("values", {}).get(f) is not None:
            used[f], source[f] = fixed["values"][f], fixed["source"].get(f, "log")
        elif profile and profile.get(f) is not None:
            used[f], source[f] = profile[f], "profile"
        elif learned[f] is not None:
            used[f], source[f] = learned[f], "log"
        else:
            used[f], source[f] = None, None
    mode = _assign(rows, ign, used, is_sleep_packet)
    # stretches: rows in one mode one after another (an ignition change always starts a new one)
    stretches: list[list] = []
    for i in range(n):
        m = mode[i]
        if m is None:
            continue
        if stretches and stretches[-1][0] == m and stretches[-1][2] == i - 1 and ign[i - 1] == ign[i]:
            stretches[-1][2] = i
        else:
            stretches.append([m, i, i])
    out_modes = []
    for m, label in MODES:
        own = [x for x in stretches if x[0] == m]
        if not own:
            continue
        idx = [i for _, a, b in own for i in range(a, b + 1)]
        steps, at = [], []                               # each step, and the row it ends on
        for _, a, b in own:
            for i in range(a + 1, b + 1):
                steps.append(max(0.0, _step(rows, i)))
                at.append(i)
        secs = sum((rows[b]["t"] - rows[a]["t"]).total_seconds() for _, a, b in own)
        hist = [i for i in idx if rows[i].get("history")]
        lags, lag_live, lag_hist = [], [], []
        for i in idx:
            r = rows[i]
            if r.get("ins") is None:
                continue
            lag = (r["ins"] - r["t"]).total_seconds()
            lags.append(lag)
            (lag_hist if r.get("history") else lag_live).append(lag)
        types = Counter(((rows[i].get("od") or {}).get("PacketType") or "") for i in idx)
        every = used.get(f"{m}_s")
        longest = max(range(len(steps)), key=steps.__getitem__) if steps else None
        rec = {"mode": m, "label": label, "interval_s": every, "source": source.get(f"{m}_s"),
               "seen_s": _most(steps), "seconds": round(secs), "periods": len(own),
               "stretch_avg_s": round(secs / len(own)) if own else None,
               "packets": len(idx), "live": len(idx) - len(hist), "history": len(hist),
               "history_pct": round(100 * len(hist) / len(idx), 1) if idx else 0.0,
               "expected": None, "diff": None, "sufficiency_pct": None,
               "gen": {**_stats(steps), "jitter": round(statistics.pstdev(steps), 1) if len(steps) > 1 else None},
               "delay": _stats(lags), "delay_live": _stats(lag_live), "delay_history": _stats(lag_hist),
               "clock_ahead": sum(1 for x in lags if x < 0),
               "duplicates": sum(1 for d in steps if d == 0),
               "longest_s": steps[longest] if steps else None,
               "longest_at": rows[at[longest] - 1]["t"].isoformat() if steps else None,
               "types": types.most_common(8),
               "on_time_pct": None, "early": 0, "late": 0, "missed_steps": 0, "missed": 0, "on_time": 0,
               "median_s": _q(steps, 0.5), "p90_s": _q(steps, 0.9),
               "delay_median_s": _q(lags, 0.5), "delay_p90_s": _q(lags, 0.9)}
        if every:
            tol = margin_for(every, settings)
            due = secs / every + len(own)
            rec.update(expected=round(due, 1), diff=round(len(idx) - due), tolerance_s=tol,
                       sufficiency_pct=round(100 * len(idx) / due, 1) if due else None)
            if steps:
                rec["on_time"] = sum(1 for d in steps if abs(d - every) <= tol)
                rec["on_time_pct"] = round(100 * rec["on_time"] / len(steps), 1)
                rec["early"] = sum(1 for d in steps if d < every - tol)
                rec["late"] = sum(1 for d in steps if every + tol < d <= 1.5 * every)
                rec["missed_steps"] = sum(1 for d in steps if d > 1.5 * every)
                rec["missed"] = sum(max(0, round(d / every) - 1) for d in steps if d > 1.5 * every)
                for k_ in ("early", "late", "missed_steps"):
                    rec[k_ + "_pct"] = round(100 * rec[k_] / len(steps), 1)
        out_modes.append(rec)
    # how long standby really lasted: from the ignition going off to the last packet before sleep
    lasted = []
    for s0, s1, known in _off_periods(ign):
        if not known:
            continue
        first_sleep = next((i for i in range(s0, s1 + 1) if mode[i] == "sleep"), None)
        if first_sleep is not None:
            prev = rows[first_sleep - 1]["t"] if first_sleep > s0 else rows[s0]["t"]
            lasted.append((prev - rows[s0]["t"]).total_seconds())
    for rec in out_modes:
        if rec["mode"] == "standby":
            rec["standby_lasted"] = _stats(lasted)
    # the modes along the log, for the timeline: one span per stretch in one mode
    spans, cur = [], None
    for i, m in enumerate(mode):
        if m is None:
            cur = None
            continue
        end = rows[i + 1]["t"] if i + 1 < n and mode[i + 1] == m else rows[i]["t"]
        if cur and cur["mode"] == m:
            cur["end"] = end.isoformat()
        else:
            cur = {"mode": m, "start": rows[i]["t"].isoformat(), "end": end.isoformat()}
            spans.append(cur)
    return {"profile": profile, "values": used, "source": source, "learned": learned,
            "modes": out_modes, "spans": spans}
