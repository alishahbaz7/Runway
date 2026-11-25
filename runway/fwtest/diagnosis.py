"""From what is wrong to why, where, and how to fix it — the defects a firmware test finds.

Every kind of failure has a diagnosis (``DIAG``): the firmware area it most likely sits in, how severe
it is, the likely cause, where to look, the fix, how to verify it, and what it costs if shipped. Where
Runway tests the cause itself — the checksum of a history packet matches its live version; the XOR
matches over another span, the '$' or the '*' taken in — the cause is *proved*, not likely.

    defects(gate, plan=None) -> {"defects": [...], "fix_first": {...}, "areas": {...}}

A defect gathers every packet one cause explains (a check, and for some a reason: ``why``), with its
share of the failing packets, live / history split, first and last packet, examples, and the plan's
cases that verify it. ``fix_first`` orders them by severity and reach: the fewest causes that explain
most of the failing packets. ``report_text`` drafts the defect report a developer gets.

Pure: reads a Gate (and a plan), returns dicts. A new diagnosis is a row in ``DIAG``.
"""

from __future__ import annotations

import hashlib
from collections import Counter

SEVERITY = {"critical": 0, "major": 1, "minor": 2}
AREAS = {"builder": "Packet builder", "history": "History buffer", "gnss": "GNSS and device time",
         "conn": "Connection manager", "alerts": "Alert engine", "timers": "Reporting timers and sleep",
         "unknown": "Not placed yet"}

# (check, why) → diagnosis; (check, None) is the check's own when no reason is more precise
DIAG: dict[tuple[str, str | None], dict] = {
    ("checksum", "kept_live"): dict(
        area="history", severity="critical", proved=True,
        cause="Each history packet carries the checksum of its live version: the checksum is worked out when the packet is made, and not again when it is re-marked as history (status H, alert 02). Runway rewrote these packets back to live and their XOR matched.",
        where="Where a buffered packet's status and alert ID are set for history, before it is sent.",
        fix="Work out the XOR again after setting the packet status and alert ID for history.",
        impact="A server that validates checksums drops every history packet — the data buffered while offline is lost."),
    ("checksum", "dollar_included"): dict(
        area="builder", severity="critical", proved=True,
        cause="The XOR starts one byte too early: it takes in the '$'. The standard practice (NMEA 0183) XORs only the bytes between '$' and '*'. Runway worked the XOR out with the '$' included and it matched.",
        where="The checksum routine for this packet type — its loop start.",
        fix="Start the XOR at the byte after '$' and end it at the byte before '*'.",
        impact="A server that validates checksums the standard way rejects every such packet."),
    ("checksum", "dollar_left_out"): dict(
        area="builder", severity="critical", proved=True,
        cause="The XOR leaves out the '$', which this device's sheet takes in. Runway worked the XOR out without the '$' and it matched.",
        where="The checksum routine for this packet type — its loop start.",
        fix="Start the XOR at the '$', as the sheet says.",
        impact="A server that validates checksums as the sheet says rejects the packet."),
    ("checksum", "star_included"): dict(
        area="builder", severity="critical", proved=True,
        cause="The XOR runs one byte too far: it takes in the '*'. Runway worked the XOR out with the '*' included and it matched.",
        where="The checksum routine for this packet type — its loop end.",
        fix="End the XOR at the byte before '*'.",
        impact="A server that validates checksums rejects the packet — for the login, the whole session."),
    ("checksum", "dollar_star_included"): dict(
        area="builder", severity="critical", proved=True,
        cause="The XOR covers the '$' and the '*' too: it starts one byte early and ends one byte late. Runway worked the XOR out with both included and it matched.",
        where="The checksum routine for this packet type — its loop start and end.",
        fix="Start the XOR at the byte after '$' and end it at the byte before '*'.",
        impact="A server that validates checksums rejects the packet."),
    ("checksum", "missing"): dict(
        area="builder", severity="major", proved=True,
        cause="Nothing is written after the '*' for this packet type.",
        where="The builder of this packet type.", fix="Append the two hex digits of the XOR after '*'.",
        impact="A server that validates checksums rejects the packet."),
    ("checksum", None): dict(
        area="builder", severity="critical", proved=False,
        cause="The checksum does not match the packet: the packet is changed after its checksum is worked out, or the XOR covers another span.",
        where="The checksum routine, and anything that edits a packet after it.",
        fix="Work the XOR out last, over the bytes the sheet names (the standard: between '$' and '*').", impact="A server that validates checksums rejects the packet."),
    ("field_count", None): dict(
        area="builder", severity="critical", proved=True,
        cause="The packet has another number of fields than the sheet: a parser that follows the sheet reads every field after the difference wrongly.",
        where="The packet builder's field list.",
        fix="Bring the packet to the sheet's layout — or, if the change is intended, update the sheet and set the format in Configuration.",
        impact="Fields after the difference are read as the wrong field on the platform."),
    ("field.date", None): dict(
        area="gnss", severity="major", proved=False,
        cause="The date is written in another format in some packets — likely the GNSS (RMC) date taken as it is, DDMMYY, on that path.",
        where="The date formatting on the path these packets take.", fix="Write the date as DDMMYYYY on every path, live and history.",
        impact="The platform cannot place these packets in time."),
    ("field.time", None): dict(
        area="gnss", severity="major", proved=False,
        cause="The time field carries the GNSS time text with a digit lost and its decimals left in (hhmmss.sss cut short).",
        where="The time formatting on the path these packets take.", fix="Write hhmmss, six digits, from the parsed time.",
        impact="The platform cannot place these packets in time."),
    ("field", None): dict(
        area="builder", severity="major", proved=True,
        cause="The field is not written in the sheet's format.", where="Where this field is formatted.",
        fix="Write the field as the sheet says.", impact="The platform may read the value wrongly, or not at all."),
    ("login_first", None): dict(
        area="conn", severity="critical", proved=True,
        cause="The login is sent at start-up only, not each time the connection is opened again.",
        where="The connection manager's reconnect path.", fix="Send the login first on every new connection — reconnects included.",
        impact="A server that waits for the login ignores the packets of that connection."),
    ("identity", None): dict(
        area="builder", severity="critical", proved=True,
        cause="Packets carry another IMEI, firmware version or number plate than the login.",
        where="Where these fields are filled in.", fix="Fill them from the one stored identity in every packet.",
        impact="The platform files the packets under another device, or rejects them."),
    ("not_packet", None): dict(
        area="conn", severity="critical", proved=True,
        cause="Bytes that are not a packet: a sentence cut off (a send interrupted, a buffer overrun) or bytes written outside a packet.",
        where="The transmit path: buffer sizes, partial sends, what is written between packets.",
        fix="Send whole packets only; resend a packet whose send was interrupted from its start.",
        impact="The server drops those bytes — and may lose the packet they belonged to."),
    ("unknown_packet", None): dict(
        area="builder", severity="major", proved=True, cause="A packet type the sheet does not describe.",
        where="The packet types this firmware sends.", fix="Send only the sheet's packet types — or add this one to the sheet.",
        impact="A server that follows the sheet does not read it."),
    ("framing", None): dict(
        area="builder", severity="major", proved=True, cause="The packet's framing — '$', the '*' before the checksum, CR LF at the end — is not as the sheet says.",
        where="The packet builder's start and end.", fix="Frame every packet as $…*CC CR LF.", impact="A server may join or split packets wrongly."),
    ("frame_live", None): dict(
        area="builder", severity="major", proved=True,
        cause="The frame counter does not step once per live packet: it repeats or skips.",
        where="Where the frame number is taken and incremented.", fix="Increment the frame number exactly once per packet made.",
        impact="The platform sees packets missing or repeated that were not."),
    ("frame_repeat", "different"): dict(
        area="history", severity="major", proved=True,
        cause="Different packets carry the same frame number: the counter is reused, e.g. on the history path.",
        where="The frame number on the history path.", fix="Give every packet its own frame number when it is made.",
        impact="A platform that de-duplicates by frame number drops real packets."),
    ("frame_repeat", "same"): dict(
        area="conn", severity="minor", proved=True,
        cause="The same packet is sent again — a resend after a missing acknowledgement, or a buffer entry read twice.",
        where="The resend logic and the buffer's read pointer.", fix="Resend only what was not acknowledged; advance the read pointer once.",
        impact="Duplicates on the platform."),
    ("history_order", None): dict(
        area="history", severity="major", proved=True,
        cause="History is read from the buffer in another order than the device is set to (FIFO: oldest first; LIFO: newest first).",
        where="The buffer's read order.", fix="Read the buffer in the order set.", impact="The platform draws the offline period out of order."),
    ("time_repeat", "clock"): dict(
        label="Different packets at one time", area="gnss", severity="critical", proved=False,
        cause="Different packets carry the same time: packets made while the clock does not move (GNSS time not refreshing), or one packet stored several times.",
        where="The time a packet takes when it is made and when it is buffered.", fix="Stamp each packet with the time it is made; store it once.",
        impact="The platform cannot tell these packets apart in time — a track and its reports get duplicate moments."),
    ("time_repeat", "duplicate"): dict(
        label="Duplicate packets", area="builder", severity="critical", proved=True,
        cause="The device makes the same packet again: same time, same data, only a new frame number. Runway compared the packets field by field — they match but for the frame number.",
        where="Where a packet is made and queued: the same reading built (or queued) more than once, each time numbered anew.",
        fix="Build each reading into one packet; number a packet once, when it is made.",
        impact="Duplicate packets pile up at one point on the map, use up frame numbers (real packets look missing), and load the server and every screen that shows them."),
    ("time_repeat", "resent"): dict(
        label="Sent live, then again as history", area="history", severity="minor", proved=False,
        cause="A packet that reached the server live was stored and sent again as history: the platform gets the moment twice.",
        where="The store-and-forward path: what marks a packet as sent.", fix="Keep a packet for history only when its live send was not acknowledged.",
        impact="Duplicate points and doubled counts on the platform."),
    ("live_stale", None): dict(
        area="history", severity="major", proved=True,
        cause="Packets made while offline are sent marked live.", where="Where the live / history status is set.",
        fix="Mark a packet history when it was buffered; live only when sent as it is made.",
        impact="The platform shows old positions as the vehicle's current one."),
    ("live_delay", None): dict(
        area="conn", severity="minor", proved=True, cause="Live packets reach the server late.",
        where="The send queue, or the network.", fix="Send live packets ahead of history; check the network at the time.",
        impact="The platform's live view lags."),
    ("live_time_order", None): dict(
        area="gnss", severity="major", proved=True, cause="A live packet carries an earlier time than the live packet before it.",
        where="The time source of live packets.", fix="Take every live packet's time from one clock that only moves forward.",
        impact="The platform draws the track backwards in time."),
    ("future_time", None): dict(
        area="gnss", severity="major", proved=True, cause="The device clock is ahead of real time: time sync not applied, or a time zone added.",
        where="Time sync.", fix="Keep the clock in UTC from GNSS or the network.", impact="Packets are filed in the future."),
    ("status_alert", None): dict(
        area="alerts", severity="major", proved=True, cause="The packet status does not match the alert ID (alert 1 live, alert 2 history).",
        where="Where status and alert ID are set.", fix="Set both together.", impact="The platform files live data as history, or the other way."),
    ("alert_type", None): dict(
        area="alerts", severity="major", proved=True, cause="The alert ID does not match the packet type in the AIS-140 table.",
        where="The alert engine's table.", fix="Use the standard's alert ID for each packet type.", impact="The platform raises the wrong alert."),
    ("alert_type", "interim"): dict(
        area="alerts", severity="minor", proved=True,
        cause="The packet uses another event's ID for now — agreed until it gets an ID of its own (the spec's alert_ids_interim).",
        where="The alert engine's table.", fix="Give the packet an ID of its own; then the spec's alert_ids names it and its interim row goes.",
        impact="The platform cannot tell the two events apart by their ID."),
    ("ignition_alert", None): dict(
        area="alerts", severity="major", proved=True, cause="The ignition field disagrees with the IN / IF alert.",
        where="Where the ignition field is read for the packet.", fix="Fill the field from the same reading the alert came from.",
        impact="The platform shows ignition wrongly at the alert."),
    ("consistency", None): dict(
        area="alerts", severity="major", proved=True,
        cause="A field disagrees with the alert or with another field — the alert raised from one reading, the field filled from another, or the field filled before it changed.",
        where="Where the packet's fields are filled for an alert.", fix="Fill every field from the same moment the alert was raised.",
        impact="The platform shows a state that contradicts the alert."),
    ("interval", "early"): dict(
        area="timers", severity="major", proved=True, cause="Packets come sooner than the interval set — a second timer, or the interval not applied in this state.",
        where="The reporting timer of this state.", fix="Report at the interval set for the state.", impact="More data, more battery, than set."),
    ("interval", "missed"): dict(
        area="timers", severity="major", proved=True, cause="Packets are missing at the interval set — the timer stops, or the device sleeps early.",
        where="The reporting timer of this state, and the sleep entry.", fix="Keep the timer running in the state; enter sleep only when set.",
        impact="Gaps on the platform."),
    ("interval", "late"): dict(
        area="timers", severity="minor", proved=True, cause="Packets drift later than the interval set.",
        where="The reporting timer.", fix="Re-arm the timer from when the packet was due, not when it was sent.", impact="The interval drifts."),
    ("interval", "silent"): dict(
        area="timers", severity="minor", proved=False, cause="Hours without a location packet — the device off, out of coverage, or stopped.",
        where="Power, coverage, and the watchdog.", fix="Check what the device was doing then; a stop needs the watchdog to recover it.",
        impact="No data for that time."),
}


def diag_for(key: str, why: str | None) -> dict:
    check = "field" if key.startswith("field.") and (key, None) not in DIAG else key
    return DIAG.get((key, why)) or DIAG.get((key, None)) or DIAG.get((check, None)) or dict(
        area="unknown", severity="major", proved=False, cause="", where="", fix="", impact="")


def _why(f: dict) -> str | None:
    if f["key"] == "frame_repeat":
        return "same" if f["level"] == "warn" else "different"
    return f.get("why") if f["key"] in ("checksum", "interval", "alert_type", "time_repeat") else None


def _verifies(plan: dict | None, key: str) -> list[str]:
    if not plan:
        return []
    out = []
    for a in plan["areas"]:
        for c in a["cases"]:
            checks = (c.get("judge") or {}).get("checks") or []
            if any(key == k or (k.endswith(".*") and key.startswith(k[:-1])) for k in checks):
                out.append(c["id"])
    return out


def defects(gate, plan: dict | None = None, firmware: str = "", device: str = "") -> dict:
    gate._derive()
    gate._checks()                                        # the checks' sources, for each defect
    groups: dict[tuple, list[tuple[dict, dict]]] = {}
    for r in gate.rows:
        seen = set()
        for f in r["flags"]:
            g = (f["key"], _why(f))
            if g not in seen:
                groups.setdefault(g, []).append((r, f))
                seen.add(g)
    failing = {r["n"] for r in gate.rows if r["level"] == "fail"}
    out = []
    for (key, why), items in groups.items():
        d = diag_for(key, why)
        rows = [r for r, _ in items]
        f0 = items[0][1]
        status = Counter(r["status"] for r in rows)
        got = Counter(f["got"] for _, f in items if f.get("got"))
        sev = d["severity"] if f0["level"] == "fail" or d["severity"] == "minor" else "minor"
        did = hashlib.sha1(f"{key}|{why}".encode()).hexdigest()[:8]
        out.append({
            "id": f"D-{did}", "key": key, "why": why, "label": d.get("label") or f0["label"], "severity": sev,
            "area": d["area"], "area_label": AREAS[d["area"]], "proved": d["proved"], "cause": d["cause"],
            "where": d["where"], "fix": d["fix"], "impact": d["impact"],
            "packets": len(rows), "failing": len({r["n"] for r in rows} & failing),
            "live": status.get("L", 0), "history": status.get("H", 0),
            "first": rows[0]["n"], "last": rows[-1]["n"], "first_time": rows[0].get("received"),
            "expected": f0.get("expected"), "got": [g for g, _ in got.most_common(3)], "example": f0["text"],
            "evidence": [r["raw"] for r in rows[:3]], "verify": _verifies(plan, key),
            "scenarios": sorted({k for r in rows for k in r.get("scen") or ()}),
            "source": (gate.checks.get(key) or {}).get("source", "") if hasattr(gate, "checks") else "",
            "firmware": firmware, "device": device, "level": f0["level"],
        })
    out.sort(key=lambda x: (SEVERITY[x["severity"]], -x["failing"], -x["packets"]))
    return {"defects": out, "fix_first": fix_first(out, gate), "areas": _areas(out)}


def fix_first(found: list[dict], gate) -> dict:
    """The fewest causes that explain most failing packets: greedy, worst severity first."""
    failing = {r["n"] for r in gate.rows if r["level"] == "fail"}
    if not failing:
        return {"causes": [], "explained": 0, "failing": 0}
    by = {}
    for r in gate.rows:
        for f in r["flags"]:
            if f["level"] == "fail":
                by.setdefault((f["key"], _why(f)), set()).add(r["n"])
    left, chosen = set(failing), []
    while left:
        best = max(found, key=lambda d: (len(by.get((d["key"], d["why"]), set()) & left), -SEVERITY[d["severity"]]),
                   default=None)
        gain = by.get((best["key"], best["why"]), set()) & left if best else set()
        if not gain:
            break
        chosen.append({"id": best["id"], "label": best["label"], "severity": best["severity"], "adds": len(gain)})
        left -= gain
        if (len(failing) - len(left)) / len(failing) >= 0.8 and len(chosen) >= 1:
            break
    return {"causes": chosen, "explained": len(failing) - len(left), "failing": len(failing)}


def _areas(found: list[dict]) -> dict:
    out: dict[str, dict] = {}
    for d in found:
        a = out.setdefault(d["area"], {"label": d["area_label"], "defects": 0, "critical": 0, "packets": 0})
        a["defects"] += 1
        a["critical"] += d["severity"] == "critical"
        a["packets"] += d["packets"]
    return dict(sorted(out.items(), key=lambda kv: (-kv[1]["critical"], -kv[1]["defects"])))


def report_text(d: dict) -> str:
    """A defect report, ready to paste into the tracker or a ticket."""
    split = ", ".join(x for x in (f"{d['live']} live" if d["live"] else "", f"{d['history']} history" if d["history"] else "") if x)
    lines = [
        f"[{d['severity'].upper()}] {d['label']} — {d['example']}",
        f"Firmware: {d['firmware'] or '—'}    Device: {d['device'] or '—'}    Area: {d['area_label']}",
        f"Seen in: {d['packets']} packets" + (f" ({split})" if split else "") + f", packets #{d['first']} to #{d['last']}",
        f"Expected: {d['expected'] or '—'}    Got: {', '.join(d['got']) or '—'}",
        f"{'Cause (proved)' if d['proved'] else 'Likely cause'}: {d['cause']}",
        f"Where to look: {d['where']}",
        f"Fix: {d['fix']}",
        f"Impact if shipped: {d['impact']}",
        f"Rule from: {d.get('source') or '—'}",
        f"Verify: {', '.join(d['verify']) or 'run the test again'} — the check must show no packet.",
        "Evidence:",
        *[f"  {e}" for e in d["evidence"]],
    ]
    return "\n".join(lines)


def merge(lists: list[list[dict]]) -> list[dict]:
    """One firmware version's defects over several tests: a cause once, its packets added up."""
    out: dict[str, dict] = {}
    for found in lists:
        for d in found:
            m = out.get(d["id"])
            if m is None:
                out[d["id"]] = {**d, "tests": 1}
                continue
            m["tests"] += 1
            for k in ("packets", "failing", "live", "history"):
                m[k] += d[k]
            if SEVERITY[d["severity"]] < SEVERITY[m["severity"]]:
                m["severity"] = d["severity"]
    return sorted(out.values(), key=lambda x: (SEVERITY[x["severity"]], -x["failing"], -x["packets"]))


CRITICAL_AREAS = ("06", "23", "24", "25")       # SOS, InTouch, government and emergency servers
GATE_MIN_RUN = 80                                # % of the plan's cases run before a release


def release_gate(view: dict, found: list[dict]) -> dict:
    """Can this firmware version be released? Every reason it cannot, in words."""
    reasons = []
    for a in view["areas"]:
        if a["id"] in CRITICAL_AREAS:
            bad = [c["id"] for c in a["cases"] if c["result"]["status"] == "fail"]
            if bad:
                reasons.append(f"{a['id']} {a['title']}: {', '.join(bad)} failing")
    crit = [d for d in found if d["severity"] == "critical"]
    if crit:
        reasons.append(f"{len(crit)} critical defect{'s' if len(crit) > 1 else ''}: " + ", ".join(d["label"] for d in crit[:4]))
    cases = [c for a in view["areas"] for c in a["cases"]]
    run = sum(1 for c in cases if c["result"]["status"] != "not_run")
    pct = round(100 * run / len(cases)) if cases else 0
    if pct < GATE_MIN_RUN:
        reasons.append(f"{pct}% of the plan run — {GATE_MIN_RUN}% needed")
    return {"ready": not reasons, "reasons": reasons, "run_pct": pct}


def compare(view_a: dict, view_b: dict, defects_a: list[dict], defects_b: list[dict]) -> dict:
    """Firmware A against firmware B: what B fixed, what it broke, what still fails."""
    bad = ("fail", "partial")
    a = {c["id"]: c for ar in view_a["areas"] for c in ar["cases"]}
    out = {"fixed": [], "regressed": [], "still_failing": [], "newly_failing": [], "newly_passing": []}
    for ar in view_b["areas"]:
        for c in ar["cases"]:
            sa, sb = a.get(c["id"], {}).get("result", {}).get("status", "not_run"), c["result"]["status"]
            row = {"id": c["id"], "what": c["what"], "a": sa, "b": sb, "note": c["result"].get("note", "")}
            if sa in bad and sb == "pass":
                out["fixed"].append(row)
            elif sa == "pass" and sb in bad:
                out["regressed"].append(row)
            elif sa in bad and sb in bad:
                out["still_failing"].append(row)
            elif sa == "not_run" and sb in bad:
                out["newly_failing"].append(row)
            elif sa == "not_run" and sb == "pass":
                out["newly_passing"].append(row)
    ida, idb = {d["id"]: d for d in defects_a}, {d["id"]: d for d in defects_b}
    out["defects"] = {
        "fixed": [{"id": i, "label": d["label"], "severity": d["severity"]} for i, d in ida.items() if i not in idb],
        "new": [{"id": i, "label": d["label"], "severity": d["severity"]} for i, d in idb.items() if i not in ida],
        "still": [{"id": i, "label": d["label"], "severity": d["severity"], "a": ida[i]["packets"], "b": d["packets"]}
                  for i, d in idb.items() if i in ida]}
    out["a"], out["b"] = view_a["firmware"], view_b["firmware"]
    return out
