"""What a firmware test has found, in plain words — the page's Insights panel.

Read from the gate's packets and flags: one insight per thing worth telling the firmware team, worst
first — failures, then warnings, then what is left to do on the bench, then what is right. Each says
how many packets, which (live, history, login …), and why when it can be told, and carries the filter
that shows its packets (``{"check": key}`` / ``{"scenario": key}``).

    insights(gate, live=False, now=None) -> [{level, title, text, filter}]

Pure: reads a Gate, returns dicts. A check with no wording of its own here still gets an insight from
its label and example, so a new check is never silent.
"""

from __future__ import annotations

import time as _time
from collections import Counter

ORDER = {"fail": 0, "warn": 1, "info": 2, "ok": 3}
SILENT_S = 300                     # a live test with no packet for this long says so
# a checksum worked out over another span than the sheet's (the gate's why) → what the firmware does
SPAN_WORDS = {
    "dollar_included": "the XOR is worked out from the $, which the standard leaves out",
    "dollar_left_out": "the XOR leaves out the $, which this device's sheet takes in",
    "star_included": "the XOR is worked out over the * too",
    "dollar_star_included": "the XOR is worked out over the $ and the * too",
}


def _n(x: int, one: str, many: str | None = None) -> str:
    return f"{x:,} {one if x == 1 else (many or one + 's')}"


def _kind(header: str, status: str | None, labels: dict) -> str:
    name = labels.get(header, header)
    if status == "L":
        return f"live {name}"
    if status == "H":
        return f"history {name}"
    return name if name.isupper() else f"{name.lower()} packet"          # "login packet", "PVT"


def _split(rows: list[dict]) -> str:
    """'21 history, 1 live' — which packets, by status."""
    c = Counter(r["status"] for r in rows)
    parts = [f"{c[s]:,} {w}" for s, w in (("H", "history"), ("L", "live")) if c.get(s)]
    other = sum(v for s, v in c.items() if s not in ("H", "L"))
    if other:
        parts.append(f"{other:,} other")
    return ", ".join(parts)


def insights(gate, live: bool = False, now: float | None = None) -> list[dict]:
    rows = gate.rows
    if not rows:
        return []
    labels = {h: p.label for h, p in gate.spec.packets.items()}
    out: list[dict] = []

    def add(level, title, text, **filt):
        out.append({"level": level, "title": title, "text": text, "filter": filt})

    flagged: dict[str, list[tuple[dict, dict]]] = {}
    for r in rows:
        for f in r["flags"]:
            flagged.setdefault(f["key"], []).append((r, f))
    done: set[str] = set()

    # ── checksum, by kind of packet, and why ──────────────────────────────────────────────────────
    kinds: dict[tuple, Counter] = {}
    for r in rows:
        if "*" not in r["raw"]:
            continue
        f = next((f for f in r["flags"] if f["key"] == "checksum"), None)
        kinds.setdefault((r["header"], r["status"]), Counter())[(f.get("why") or "other") if f else "ok"] += 1
    if kinds:
        checked = sum(sum(c.values()) for c in kinds.values())
        wrong = sum(sum(v for k, v in c.items() if k != "ok") for c in kinds.values())
        good, bad = [], []
        for (h, st), c in sorted(kinds.items(), key=lambda kv: -sum(kv[1].values())):
            total, ok, name = sum(c.values()), c["ok"], _kind(h, st, labels)
            if ok == total:
                good.append(f"{name} ({ok:,} of {total:,})")
                continue
            w = total - ok
            every = "every" if w == total else f"{w:,} of {total:,}"
            plural = "" if w == total else "s"                       # "every history PVT", "3 of 9 … PVTs"
            count = f" ({w:,})" if w == total else ""
            if c["kept_live"]:
                bad.append(f"Wrong on {every} {name}{plural}{count}: each keeps the checksum it had "
                           f"live — the firmware marks it history without working it out again.")
            elif (span := next((k for k in SPAN_WORDS if c[k]), None)):
                lead = f"the {name}" if total == 1 else f"{every} {name}{plural}{count}"
                bad.append(f"Wrong on {lead}: {SPAN_WORDS[span]}.")
            elif c["missing"]:
                bad.append(f"Missing on {'the' if total == 1 else every} {name}: nothing after the *.")
            else:
                bad.append(f"Wrong on {every} {name}{plural}.")
        if wrong:
            text = " ".join(bad) + (f" Right on every {', '.join(good)}." if good else "")
            add("fail", f"Checksum wrong on {wrong:,} of {checked:,} packets", text, check="checksum")
        else:
            add("ok", "Checksum right on every packet", f"{checked:,} packets checked.", check="checksum")
    done.add("checksum")

    # ── field count ───────────────────────────────────────────────────────────────────────────────
    if "field_count" in flagged:
        by = {}
        for r, f in flagged["field_count"]:
            by.setdefault((r["header"], f["got"], f["expected"]), []).append((r, f))
        for (h, got, want), items in by.items():
            total = gate.headers.get(h, 0)
            extra = items[0][1]["text"].partition("; ")[2]
            alone = sum(1 for r, _ in items if all(x["key"] == "field_count" for x in r["flags"] if x["level"] == "fail"))
            who = f"Every {labels.get(h, h)}" if len(items) == total else f"{len(items):,} of {total:,} {labels.get(h, h)} packets"
            text = f"{who} has {got} fields; expected {want}" + (f" — {extra}" if extra else "") + "."
            if alone:
                text += f" {_n(alone, 'packet')} fail{'s' if alone == 1 else ''} on this alone."
            add("fail", f"{labels.get(h, h)} has {got} fields, expected {want}", text, check="field_count")
    done.add("field_count")

    # ── fields not as the sheet says ──────────────────────────────────────────────────────────────
    for key, items in flagged.items():
        if not key.startswith("field."):
            continue
        r0, f0 = items[0]
        packets = {r["n"]: r for r, _ in items}
        got = Counter(f["got"] for _, f in items)
        shown = ", ".join(g or "(empty)" for g, _ in got.most_common(2)) + (" …" if len(got) > 2 else "")
        add(f0["level"], f"{f0['label']} not as the sheet says",
            f"{_n(len(packets), 'packet')} ({_split(list(packets.values()))}) carry {shown}; the sheet says {f0['expected']}.",
            check=key)
        done.add(key)

    # ── the order of packets ──────────────────────────────────────────────────────────────────────
    if "login_first" in flagged:
        items = flagged["login_first"]
        conns = len(gate.conns)
        add("fail", "Login missing on a connection",
            f"On {len(items):,} of {_n(conns, 'connection')} the first packet was not the login — "
            f"e.g. {items[0][1]['text']}.", check="login_first")
        done.add("login_first")
    if "identity" in flagged:
        items = flagged["identity"]
        add("fail", "Not the login's device",
            f"{_n(len({r['n'] for r, _ in items}), 'packet')} differ from the login or the test's expected values — "
            f"e.g. {items[0][1]['text']}.", check="identity")
        done.add("identity")
    if "frame_live" in flagged:
        items = flagged["frame_live"]
        kinds_ = Counter("repeated" if "repeated" in f["text"] else "never came" if "never came" in f["text"] else "went back"
                         for _, f in items)
        add("fail", "Live frame numbers break the sequence",
            f"{_n(len(items), 'break')}: " + ", ".join(f"{v:,} {k}" for k, v in kinds_.most_common()) +
            f" — e.g. {items[0][1]['text']}.", check="frame_live")
        done.add("frame_live")
    if "frame_repeat" in flagged:
        items = flagged["frame_repeat"]
        same = [r for r, f in items if f["level"] == "warn"]
        diff = [r for r, f in items if f["level"] == "fail"]
        if diff:
            add("fail", "Different packets share a frame number",
                f"{_n(len(diff), 'packet')} ({_split(diff)}) carry a frame number an earlier, different packet had.",
                check="frame_repeat")
        if same:
            add("warn", "Packets sent twice",
                f"{_n(len(same), 'packet')} ({_split(same)}) are the same packet again, frame number and all.",
                check="frame_repeat")
        done.add("frame_repeat")
    hist = [r for r in rows if r["status"] == "H"]
    if "history_order" in flagged:
        frames = [int(r["frame"]) for r in hist if (r["frame"] or "").isdigit()]
        steps = Counter(b - a for a, b in zip(frames, frames[1:]))
        down = steps.get(-1, 0)
        if "history_order" in flagged:
            n = len(flagged["history_order"])
            if gate.config["history_order"] == "fifo":
                text = f"Set to send oldest first (FIFO), but {_n(n, 'history packet')} came after a newer one."
                if frames and down > len(frames) / 2:
                    text += f" It sends newest first: its frame numbers count down ({down:,} of {len(frames) - 1:,} steps)."
                title = "History not oldest first"
            else:
                text = f"Set to send newest first (LIFO), but {_n(n, 'history packet')} came after an older one."
                title = "History not newest first"
            add("fail", title, text, check="history_order")
        done.add("history_order")
    if "time_repeat" in flagged:
        items = flagged["time_repeat"]
        clock = [r for r, f in items if f.get("why") == "clock"]
        dup = [r for r, f in items if f.get("why") == "duplicate"]
        resent = [r for r, f in items if f.get("why") == "resent"]
        if dup:
            times = Counter(r["time"] for r in dup if r["time"])
            (top, k), = times.most_common(1) or [(None, 0)]
            add("fail", "Duplicate packets",
                f"{_n(len(dup), 'packet')} ({_split(dup)}) are a packet made again: the same time and the same data, only a new "
                f"frame number" + (f" — {k + 1:,} copies of {top} UTC alone." if top and k > 1 else ".")
                + " They pile up at one point on the map and use up frame numbers.", check="time_repeat")
        if clock:
            times = Counter(r["time"] for r in clock if r["time"])
            (top, k), = times.most_common(1) or [(None, 0)]
            add("fail", "Packets share a time",
                f"{_n(len(clock), 'packet')} ({_split(clock)}) carry a time another packet of their kind already carried"
                + (f" — {k + 1:,} packets at {top} UTC alone." if top and k > 1 else "."), check="time_repeat")
        if resent:
            add("warn", "Sent live, then again as history",
                f"{_n(len(resent), 'packet')} reached Runway live and came again as history: the platform gets those moments twice.",
                check="time_repeat")
        done.add("time_repeat")
    if "live_delay" in flagged:
        items = flagged["live_delay"]
        late = [int(f["got"].split()[0]) for _, f in items if (f["got"] or "").split()[0].lstrip("-").isdigit()]
        add("warn", "Live packets arriving late",
            f"{_n(len(items), 'live packet')} reached Runway more than {gate.spec.settings['live_max_delay_s']} s "
            f"after the time they carry" + (f" — up to {max(late):,} s." if late else "."), check="live_delay")
        done.add("live_delay")

    # ── the interval the device is set to ─────────────────────────────────────────────────────────
    cfg = gate.config
    if "interval" in flagged:
        items = [f for _, f in flagged["interval"]]
        by = {}
        for f in items:
            if f["why"] != "silent":
                by.setdefault((f["state"], f["expected"]), Counter())[f["why"]] += 1
        missing = {}
        for f in items:
            missing[f["state"]] = missing.get(f["state"], 0) + (f.get("missed") or 0)
        parts = []
        for (state, want), c in by.items():
            bits = [f"{c[w]:,} {w}" for w in ("early", "late") if c.get(w)]
            if c.get("missed"):
                bits.append(f"about {missing[state]:,} missing over {_n(c['missed'], 'gap')}")
            parts.append(f"{state.capitalize()} (set to {want}): " + ", ".join(bits) + ".")
        if parts:
            add("fail", "Packets not at the interval set", " ".join(parts), check="interval")
        silent = [f for f in items if f["why"] == "silent"]
        if silent:
            from .gate import duration
            longest = max(f["gap"] for f in silent)
            add("warn", "Hours without a location packet",
                f"{_n(len(silent), 'silence')}, the longest {duration(longest)} — the device off, or out of reach.",
                check="interval")
    elif cfg["ign_on_s"] or cfg["ign_off_s"] or cfg["sleep_s"]:
        add("ok", "Every location packet at the interval set", "")
    done.add("interval")
    if not (cfg["ign_on_s"] or cfg["ign_off_s"] or cfg["sleep_s"]):
        add("info", "Intervals not checked", "The device's ping intervals were not given.")
    if not cfg["history_order"] and hist:
        add("info", "History order not checked", "Not given: oldest first (FIFO) or newest first (LIFO).")

    # anything else flagged: its label and an example
    for key, items in flagged.items():
        if key in done:
            continue
        r0, f0 = items[0]
        add(f0["level"], f0["label"], f"{_n(len({r['n'] for r, _ in items}), 'packet')} — e.g. {f0['text']}.", check=key)

    # ── the bench: scenarios left ─────────────────────────────────────────────────────────────────
    scen = list(gate.scenarios.values())
    left = [s["label"] for s in scen if not s["count"]]
    if left:
        add("info", f"{len(left)} of {len(scen)} scenarios not seen yet", ", ".join(left) + ".")
    elif scen:
        add("ok", "Every scenario seen", f"All {len(scen)}.")

    # ── what the bench has not made the device do yet ─────────────────────────────────────────────
    todo = untried(gate)
    if todo:
        add("info", "Not exercised yet", "; ".join(f"{what} — {how}" for what, how in todo) + ".")

    # ── a live test gone quiet ────────────────────────────────────────────────────────────────────
    last = rows[-1]["received"]
    if live and last and (now or _time.time()) - last > SILENT_S:
        mins = round(((now or _time.time()) - last) / 60)
        add("warn", f"No packet for {mins} min", "The device may have lost its connection, or the listener stopped.")

    # ── what is right ─────────────────────────────────────────────────────────────────────────────
    fw = list(gate.firmware)
    if len(fw) == 1:
        add("ok", f"Firmware {fw[0]} in every packet", "")
    elif len(fw) > 1:
        add("info", "More than one firmware", ", ".join(f"{v} ({c:,})" for v, c in gate.firmware.items()) + ".")
    if "identity" not in flagged and gate.learned.get("imei"):
        add("ok", "Same device throughout", "IMEI and number plate as the login in every packet.")
    for h, p in gate.spec.packets.items():
        if not gate.headers.get(h) or not p.field_list:
            continue
        bad = {k[6:] for k in flagged if k.startswith("field.")}
        good = [f for f in p.field_list if f.name not in bad]
        if good and len(good) < len(p.field_list):
            off = [f.label for f in p.field_list if f.name in bad]
            add("ok", f"{len(good)} of {len(p.field_list)} {p.label} fields as the sheet says",
                "All but " + ", ".join(off) + ", in every packet.")
        elif good:
            add("ok", f"Every {p.label} field as the sheet says", f"All {len(good)}, in every packet.")

    out.sort(key=lambda x: ORDER[x["level"]])
    return out


def untried(gate) -> list[tuple[str, str]]:
    """What the test has not made the device do yet: (what, how to make it) — so the bench knows what is left."""
    rows = [r for r in gate.rows if r["header"] == "PVT"]
    if not rows:
        return []
    def values(k):
        return {r.get("vals", {}).get(k) for r in rows} - {None, ""}
    out = []
    if max((r.get("speed") or 0) for r in rows) <= 0:
        out.append(("speed above 0", "drive the vehicle"))
    if len(values("heading_deg")) < 2:
        out.append(("heading changing", "drive and turn"))
    if len({r.get("ign") for r in rows} - {None}) < 2:
        out.append(("ignition both on and off", "switch the ignition"))
    if "0" not in values("main_power_status"):
        out.append(("main power disconnected", "disconnect the vehicle supply"))
    if "1" not in values("emergency_status"):
        out.append(("emergency on", "press SOS"))
    if "O" not in values("tamper_alert"):
        out.append(("cover open", "open the device's cover"))
    if "0" not in values("gps_fix"):
        out.append(("no GNSS fix", "cover the antenna or go indoors"))
    di = values("digital_input_status")
    for i in range(max((len(v) for v in di), default=0)):
        bits = {v[i] for v in di if len(v) > i}
        if len(bits) < 2:
            out.append((f"digital input {i + 1} changing", f"switch input {i + 1}"))
    for k, words in (("analog_input_1", "analog input 1"), ("analog_input_2", "analog input 2")):
        if len(values(k)) < 2:
            out.append((f"{words} changing", f"apply a voltage to {words}"))
    if not any(r["status"] == "H" for r in rows):
        out.append(("history packets", "take the device offline for a while"))
    return out
