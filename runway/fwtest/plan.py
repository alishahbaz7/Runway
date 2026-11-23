"""The test plan — a device's test tracker as data (``plans/<name>.json``), judged from the packets.

A plan has areas and cases (``01.06 Fix validity shown in every packet``). Each case is one kind:

    automatic   judged from the packets of every test of the firmware version under test
    assisted    a step on the bench (press SOS, cut main power…), judged from the packets that came
                in its time window (``steps.py``)
    command     Runway sends the device a command, then judges its answer (``steps.py``)
    manual      seen by the tester: status and remarks by hand

A case's ``judge`` says what decides it:

    checks      flag keys that fail it ("checksum", "field.*" for any field, "interval")
    state       for "interval": only these states ("ignition on", "asleep" …)
    needs       {"headers": […]} / {"scenarios": […]}: without these packets it is not run yet
    scenarios   all must be seen — some seen is "partial"
    config      device settings it needs (intervals, history order): not given, not run
    measure     a function below that judges it (parked drift, time to fix, restarts …)

    judge(case, gate) -> {"status": pass|fail|partial|not_run, "note": words, "filter": {…}}

Pure: reads a Gate, returns dicts. A plan's results over several tests (one firmware version) are
combined by ``combine``: any test failing fails the case.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

from .gate import LEVELS, duration

SHIPPED = Path(__file__).resolve().parent / "plans"
STATUS_ORDER = {"fail": 0, "partial": 1, "pass": 2, "not_run": 3}
SETTINGS = {"parked_drift_m": 30, "soak_hours": 72, "reset_loop_per_hour": 3}
CONFIG_WORDS = {"ign_on_s": "interval with ignition on", "ign_off_s": "interval with ignition off",
                "sleep_after_min": "sleep after ignition off", "sleep_s": "interval asleep",
                "history_order": "history order"}


def load(name: str, own_dir: Path | None = None) -> dict:
    for d in (own_dir, SHIPPED):
        if d and d.is_dir():
            for p in sorted(d.glob("*.json")):
                data = json.loads(p.read_text(encoding="utf-8"))
                if data.get("name") == name:
                    return data
    raise ValueError(f"no test plan named {name!r}")


def cases(plan: dict) -> list[dict]:
    return [{**c, "area": a["id"], "area_title": a["title"]} for a in plan["areas"] for c in a["cases"]]


def _r(status, note, **filt):
    return {"status": status, "note": note, "filter": filt}


def _matches(key: str, wanted: list[str]) -> bool:
    return any(key == w or (w.endswith(".*") and key.startswith(w[:-1])) for w in wanted)


# ── judging ──────────────────────────────────────────────────────────────────────────────────────
def judge(case: dict, gate) -> dict:
    j = case.get("judge") or {}
    if not j:
        return _r("not_run", "")
    gate._derive()
    missing = [k for k in j.get("config", []) if not gate.config.get(k)]
    if missing:
        return _r("not_run", "Configuration not given: " + ", ".join(CONFIG_WORDS.get(k, k) for k in missing))
    if j.get("measure"):
        fn = MEASURES.get(j["measure"])
        if fn is None:                    # a plan newer than the code running: never a failure of the page
            return _r("not_run", f"This Runway cannot judge it yet ({j['measure']}) — restart Runway to load the latest code")
        return fn(gate, j)
    rows = gate.rows
    labels = {s["key"]: s["label"] for s in gate.scenarios.values()}
    need = j.get("needs") or {}
    if need.get("headers") and not any(gate.headers.get(h) for h in need["headers"]):
        return _r("not_run", "No " + " or ".join(need["headers"]) + " packets yet")
    if need.get("scenarios") and not any(gate.scenarios[s]["count"] for s in need["scenarios"] if s in gate.scenarios):
        return _r("not_run", "Not seen yet: " + ", ".join(labels.get(s, s) for s in need["scenarios"]))
    want = j.get("scenarios") or []
    seen = [s for s in want if s in gate.scenarios and gate.scenarios[s]["count"]]
    if want and not seen:
        return _r("not_run", "Not seen yet: " + ", ".join(labels.get(s, s) for s in want))
    if want:
        rows = [r for r in rows if set(r.get("scen") or ()) & set(seen)]
    checks = j.get("checks") or []
    states = j.get("state")
    # a case that names a check judges it in full: a failure the device's spec weighs as a warning
    # ("was" fail) still fails the case that tests exactly that behaviour
    bad = [r for r in rows if any(_matches(f["key"], checks) and (not states or f.get("state") in states)
                                  and "fail" in (f["level"], f.get("was")) for f in r["flags"])]
    if checks and states and not bad:
        # an interval state that never came (no ignition off in the test) is not a pass
        if not any(_in_state(gate, r, states) for r in gate.rows):
            return _r("not_run", "Not seen yet: the device " + " / ".join(states))
    left = [labels.get(s, s) for s in want if s not in seen]
    if want and not bad:
        dirty = [r for r in rows if r["level"] == "fail"]
        if dirty:
            f = next(f for f in dirty[0]["flags"] if f["level"] == "fail")
            return _r("fail", f"Seen, but {len(dirty):,} of its {len(rows):,} packets break the sheet — e.g. "
                      f"{f['label']}: {f['text']}", check=f["key"])
    if bad:
        first = next(f for f in bad[0]["flags"] if _matches(f["key"], checks))
        return _r("fail", f"{len(bad):,} packet{'s' if len(bad) != 1 else ''} — e.g. {first['label']}: {first['text']}",
                  check=first["key"])
    if left:
        return _r("partial", f"Seen {len(seen)} of {len(want)}; not yet: " + ", ".join(left),
                  scenario=seen[0])
    what = (", ".join(labels.get(s, s) for s in seen) + " seen") if seen else f"{len(rows):,} packets"
    return _r("pass", what + (", none failing" if checks else ""), **({"scenario": seen[0]} if seen else {}))


def _in_state(gate, row, states) -> bool:
    if "ignition on" in states and row.get("ign") == "1":
        return True
    return bool(set(states) - {"ignition on"}) and row.get("ign") == "0"


def _periodic(gate):
    per = gate.spec.settings.get("periodic") or {"header": "PVT", "packet_type": "NR"}
    return sorted((r for r in gate.rows if r["header"] == per["header"] and r["type"] == per["packet_type"]
                   and r.get("t") is not None), key=lambda r: r["t"])


def _metres(a, b) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(h))


# ── measures: what a flag cannot say on its own ─────────────────────────────────────────────────────
def _parked_drift(gate, j):
    """Parked (speed 0) with ignition as asked: how far the reported position wanders."""
    # each parked stretch on its own — parked here, then again elsewhere, is not wandering
    stretches, run = [], []
    for r in _periodic(gate):
        parked = r.get("speed") == 0 and r.get("ign") == j["ignition"] and r.get("fix") == "1" and r.get("lat") is not None
        if parked and run and r["t"] - run[-1]["t"] <= 1800:
            run.append(r)
        else:
            if len(run) >= 5:
                stretches.append(run)
            run = [r] if parked else []
    if len(run) >= 5:
        stretches.append(run)
    if not stretches:
        return _r("not_run", "Not parked long enough yet " + ("with ignition off" if j["ignition"] == "0" else "with engine idling"))
    worst = 0.0
    for pts in stretches:
        lats, lons = sorted(p["lat"] for p in pts), sorted(p["lon"] for p in pts)
        mid = (lats[len(lats) // 2], lons[len(lons) // 2])
        worst = max(worst, max(_metres(mid, (p["lat"], p["lon"])) for p in pts))
    limit = SETTINGS["parked_drift_m"]
    n = sum(len(p) for p in stretches)
    words = (f"{n:,} parked packets in {len(stretches)} stretch{'es' if len(stretches) > 1 else ''}; "
             f"the position wandered up to {worst:.0f} m (limit {limit} m)")
    return _r("fail" if worst > limit else "pass", words)


def _parked_distance(gate, j):
    pts = [r for r in _periodic(gate) if r.get("speed") == 0]
    odo = [r for r in pts if r.get("odo") is not None]
    if len(pts) < 2:
        return _r("not_run", "Not parked long enough yet")
    if not odo:
        return _r("not_run", "No odometer in the packets (the format set has none)")
    added = sum(max(0.0, b["odo"] - a["odo"]) for a, b in zip(odo, odo[1:]) if b["t"] - a["t"] < 3600)
    return _r("fail" if added > 0.05 else "pass", f"{len(odo):,} parked packets; odometer added {added:g} while parked")


def _time_to_fix(gate, j):
    """From a connection's first packet without a fix to its first with one."""
    by = {}
    for r in gate.rows:
        if r["header"] == "PVT" and r.get("t") is not None:
            by.setdefault(r["conn"] or "", []).append(r)
    times, never = [], 0
    for rows in by.values():
        rows.sort(key=lambda r: r["n"])
        start = next((r for r in rows if r.get("fix") == "0"), None)
        if start is None or rows.index(start) > 2:
            continue                                   # began with a fix: nothing to time
        fixed = next((r for r in rows[rows.index(start):] if r.get("fix") == "1"), None)
        if fixed is None:
            never += 1
        else:
            times.append(fixed["t"] - start["t"])
    if not times and not never:
        return _r("not_run", "No start without a fix seen yet")
    if never:
        return _r("fail", f"{never} start{'s' if never > 1 else ''} never reached a fix" +
                  (f"; others in {', '.join(duration(t) for t in times)}" if times else ""))
    return _r("pass", "Fix after " + ", ".join(duration(t) for t in times))


def _clock_boot(gate, j):
    import time as _t
    rows = [r for r in gate.rows if r.get("t") is not None]
    if not rows:
        return _r("not_run", "No packets with a time yet")
    year_now = _t.gmtime().tm_year
    odd = [r for r in rows if not 2020 <= int(r["time"][:4]) <= year_now + 1]
    return _r("fail", f"{len(odd):,} packets carry a time from {odd[0]['time'][:4]}") if odd else \
        _r("pass", f"{len(rows):,} packets, every one with a plausible date")


def _soak(gate, j):
    rows = [r for r in gate.rows if r.get("received")]
    if not rows:
        return _r("not_run", "No packets yet")
    span = rows[-1]["received"] - rows[0]["received"]
    need = SETTINGS["soak_hours"] * 3600
    if span < need:
        return _r("not_run", f"Ran {duration(span)} of the {SETTINGS['soak_hours']} h a soak needs")
    gaps = [b["received"] - a["received"] for a, b in zip(rows, rows[1:]) if b["received"] - a["received"] > 1800]
    return _r("fail", f"{len(gaps)} silences over 30 min in {duration(span)}") if gaps else \
        _r("pass", f"{duration(span)} without a silence over 30 min")


def _version_report(gate, j):
    fw = gate.firmware
    if not fw:
        return _r("not_run", "No firmware version reported yet")
    if len(fw) > 1:
        return _r("partial", "More than one version: " + ", ".join(f"{v} ({c:,})" for v, c in fw.items()))
    return _r("pass", f"{next(iter(fw))} in every packet")


def _version_change(gate, j):
    seen = []
    for r in gate.rows:
        v = next((f for f in [r["raw"].split(",")[2] if r["header"] == "PVT" and r["raw"].count(",") > 2 else None] if f), None)
        if v and (not seen or seen[-1] != v):
            seen.append(v)
    if len(seen) < 2:
        return _r("not_run", "No firmware change within the test")
    return _r("pass", "Reported " + " → ".join(seen))


def _restarts(gate):
    """Live frame numbers starting over: the device restarted."""
    frames = [(r["n"], int(r["frame"])) for r in gate.rows if r["status"] == "L" and (r["frame"] or "").isdigit()]
    return [n for (_, a), (n, b) in zip(frames, frames[1:]) if b < a - 100 or (b < 10 <= a)]


def _restarts_measure(gate, j):
    found = _restarts(gate)
    if not found:
        return _r("not_run", "No restart seen yet (live frame numbers never started over)")
    return _r("not_run", f"{len(found)} restart{'s' if len(found) > 1 else ''} seen (frame numbers started over) — "
              "the restart event packet is not defined in the sheet")


def _reset_loops(gate, j):
    rows = [r for r in gate.rows if r.get("received")]
    if not rows or rows[-1]["received"] - rows[0]["received"] < 3600:
        return _r("not_run", "Less than an hour of packets yet")
    hours = (rows[-1]["received"] - rows[0]["received"]) / 3600
    found = _restarts(gate)
    rate = len(found) / hours
    words = f"{len(found)} restart{'s' if len(found) != 1 else ''} in {duration(hours * 3600)}"
    return _r("fail" if rate > SETTINGS["reset_loop_per_hour"] else "pass", words)


def _no_harsh(gate, j):
    found = sum(gate.scenarios[k]["count"] for k in ("hb", "ha", "rt") if k in gate.scenarios)
    moving = [r for r in gate.rows if (r.get("speed") or 0) > 5]
    if not moving:
        return _r("not_run", "No driving yet (speed never above 5 km/h)")
    return _r("fail", f"{found} harsh alerts over {len(moving):,} packets of normal driving") if found else \
        _r("pass", f"{len(moving):,} packets of driving, no harsh alert")


def _sos_retry(gate, j):
    """No acknowledgement (Runway held it back): the device must send its emergency again."""
    rows = [r for r in gate.rows if set(r.get("scen") or ()) & {"ea", "epb", "emr"}]
    if not rows:
        return _r("not_run", "No emergency packet yet")
    if len(rows) < 2:
        return _r("fail", "Sent once, and not again without an acknowledgement")
    return _r("pass", f"Sent {len(rows)} times without an acknowledgement")


def _platform(gate, j):
    res = getattr(gate, "platform", None)
    if not res:
        return _r("not_run", "No platform log compared yet — Platform log on the test, with the platform's drive log")
    return _r(res["status"], res["note"])


def _multi_server(gate, j):
    return _r("not_run", "Judged in its step, over the tests on each server's port")


MEASURES = {"parked_drift": _parked_drift, "parked_distance": _parked_distance, "time_to_fix": _time_to_fix,
            "clock_boot": _clock_boot, "soak": _soak, "version_report": _version_report,
            "version_change": _version_change, "restarts": _restarts_measure, "reset_loops": _reset_loops,
            "no_harsh": _no_harsh, "multi_server": _multi_server, "sos_retry": _sos_retry, "platform_match": _platform}


def combine(results: list[tuple[str, dict]]) -> dict:
    """One case over several tests (run id, result): any failing fails it; else any passing passes it."""
    if not results:
        return _r("not_run", "No test of this firmware yet")
    worst = min(results, key=lambda x: STATUS_ORDER[x[1]["status"]])
    rid, res = worst
    out = dict(res)
    out["run"] = rid
    if res["status"] == "not_run":
        best = next((x for x in results if x[1]["note"]), worst)
        out = {**best[1], "run": best[0]}
    return out


def check_plan(plan: dict, spec) -> list[str]:
    """What a plan names that the spec or the judges do not know — empty when it is whole."""
    from .gate import CHECKS
    problems = []
    known_checks = set(CHECKS) | {"field.*"} | {f"field.{f.name}" for p in spec.packets.values() for f in p.field_list}
    scen = {s["key"] for s in spec.scenarios}
    for c in cases(plan):
        j = c.get("judge") or {}
        for k in j.get("checks", []):
            if k not in known_checks:
                problems.append(f"{c['id']}: check {k}")
        for k in j.get("scenarios", []) + (j.get("needs") or {}).get("scenarios", []):
            if k not in scen:
                problems.append(f"{c['id']}: scenario {k}")
        if j.get("measure") and j["measure"] not in MEASURES:
            problems.append(f"{c['id']}: measure {j['measure']}")
    kinds = {c["id"]: c["kind"] for c in cases(plan)}
    for su in plan.get("suites") or []:
        for st in su["steps"]:
            if kinds.get(st["case"]) not in ("assisted", "command"):
                problems.append(f"suite {su['key']}: {st['case']} is not a bench or command case")
    return problems


__all__ = ["load", "cases", "judge", "combine", "check_plan", "MEASURES", "SETTINGS", "LEVELS", "Counter"]


# ── what a case is, what the device should do, and what passes it — from what judges it ──────────
MEASURE_WORDS = {
    "parked_drift": ("Parked, the reported position stays put — no false movement.",
                     "Pass: over at least 5 parked packets with a fix, the position wanders no more than {parked_drift_m} m."),
    "parked_distance": ("Parked, the odometer does not grow.",
                        "Pass: the odometer adds nothing between parked packets (format with an odometer set)."),
    "time_to_fix": ("After a start without a fix, the device gets one and says so in its packets.",
                    "Pass: every start that began without a fix reaches one within the test; the time to fix is shown."),
    "clock_boot": ("Every packet carries a real date, even before the first time sync.",
                   "Pass: no packet carries a year before 2020 or after next year."),
    "soak": ("The device runs for days without stopping or going silent.",
             "Pass: {soak_hours} h of packets with no silence over 30 min."),
    "version_report": ("Every packet reports the one firmware version installed.", "Pass: one version in every packet."),
    "version_change": ("After an update, the device reports the new version.", "Pass: the version changes within the test."),
    "restarts": ("A restart is reported.", "Seen when live frame numbers start over; the restart event packet is not defined yet."),
    "reset_loops": ("The device does not keep restarting.", "Pass: no more than {reset_loop_per_hour} restarts an hour, over at least an hour."),
    "no_harsh": ("No harsh-driving alert in normal driving.", "Pass: no HB, HA or RT packet in the step's window."),
    "multi_server": ("Every server the device sends to gets every packet, with no loss or delay.",
                     "Pass: over the time the tests on each server's port overlap, every packet one got the others got too."),
    "platform_match": ("What the device sent is what the platform stored — every packet, read the same.",
                       "Pass: over the time the test and the platform's drive log share, every location packet is on the platform with the same position, speed and ignition."),
    "sos_retry": ("Without an acknowledgement, the device sends its emergency again.",
                  "Pass: with Runway holding back its acknowledgement, the emergency packet is sent at least twice."),
}
KIND_WORDS = {"automatic": "Judged from the packets of every test of this firmware version.",
              "assisted": "A bench step: press Run, do it, and it is judged from the packets that come in its window (3 min unless set).",
              "command": "Runway sends the device the command typed for the case, then judges the packets that come in the window.",
              "manual": "Seen by the tester; the result is set by hand."}


def describe(case: dict, spec, area: dict) -> dict:
    """The case in words: test case, expected behaviour, acceptance criteria — read from its judge,
    so what it says is what the gate does."""
    from .gate import CHECKS
    j = case.get("judge") or {}
    labels = {s["key"]: s.get("label") or s["key"] for s in spec.scenarios}
    fields = {f"field.{f.name}": f.label for p in spec.packets.values() for f in p.field_list}

    def check_words(k):
        if k == "field.*":
            return "every field's format"
        if k in fields:
            return f"{fields[k]} format"
        return CHECKS.get(k, (k,))[0].lower()

    test = f"{case['id']} · {area['title']} — {case['group']}: {case['what']}."
    scen = [labels.get(s, s) for s in j.get("scenarios") or []]
    need = [labels.get(s, s) for s in (j.get("needs") or {}).get("scenarios") or []]
    checks = [check_words(k) for k in j.get("checks") or []]
    if j.get("measure"):
        exp, acc = MEASURE_WORDS[j["measure"]]
        acc = acc.format(**SETTINGS)
    elif scen or checks:
        state = f" (in the state: {', '.join(j['state'])})" if j.get("state") else ""
        if scen:
            one = scen[0]
            exp = ("The device sends " + ", ".join(scen) + " packets" if len(scen) > 1
                   else f"The device sends {'an' if one[:1].lower() in 'aeiou' else 'a'} {one} packet")
            exp += (", each as the sheet says for " + ", ".join(checks) + state if checks else ", as the sheet says") + "."
        else:
            exp = "Every packet is as the sheet says for " + ", ".join(checks) + state + "."
        acc_bits = []
        if scen:
            acc_bits.append(("all of " if len(scen) > 1 else "") + ", ".join(scen) + " seen, and those packets pass the sheet")
        if need:
            acc_bits.append("at least one " + " or ".join(need) + " seen")
        if checks:
            acc_bits.append("no packet fails " + ", ".join(checks))
        acc = "Pass: " + "; ".join(acc_bits) + "."
        if len(scen) > 1:
            acc += " Some seen, none failing: partial."
    else:
        exp = area["goal"]
        acc = "Pass: as the product specification states, seen by the tester."
    if j.get("config"):
        acc += " Needs the device's configuration: " + ", ".join(CONFIG_WORDS.get(k, k) for k in j["config"]) + "."
    if case["kind"] in ("assisted", "command") and j and not j.get("measure"):
        acc = acc.replace("Pass: ", "Pass: within the step's window, ", 1)
    return {"test": test, "expected": exp, "accept": acc, "how": KIND_WORDS[case["kind"]]}
