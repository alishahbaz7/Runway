"""The gate: each packet held against the spec, and against the packets before it.

    gate = Gate(spec, expect={"imei": "…"})          # expect: what the person pins, else the login's
                                                     # wire=False for a file: its line endings are not the device's
    row = gate.feed(raw_bytes, received=epoch, conn="ip:port")
    gate.summary()      verdict, tiles, checks (one row per kind of deviation), scenarios
    gate.packets(…)     the packet rows, filtered
    gate.detail(n)      one packet field by field

Every deviation is a *flag* on its packet: ``{key, check, field, level, text, expected, got}``. Its
``key`` groups like flags into one row of the checks table ("Date: 21 packets"), which is what the
page and the report lead with. A packet's result is its worst flag: fail, warn, or pass.

The checks across packets read the spec's tables — status by alert, alert ids by packet type,
ignition by packet type — so a device whose sheet differs changes the spec, not this file.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from functools import reduce

from ..analysis.modes import margin_for
from . import rules
from .spec import Spec, clean_config

# check id → (label, what it holds the packets to, level)
CHECKS: dict[str, tuple[str, str, str]] = {
    "framing": ("Framing", "Starts with $, a * before the checksum, and ends as the sheet says (CR LF).", "fail"),
    "not_packet": ("Bytes that are not a packet", "Everything the device sends is a packet the protocol reads — a broken sentence, a packet cut off, stray bytes are not.", "fail"),
    "checksum": ("Checksum", "The two hex digits after * are the XOR of every byte from $ up to the *.", "fail"),
    "field_count": ("Field count", "As many fields as the sheet lists for the packet.", "fail"),
    "unknown_packet": ("Packet in the sheet", "A header the sheet does not describe.", "fail"),
    "consistency": ("Fields agree with each other", "What a packet says in one field agrees with another — a main-power-disconnect packet says main power 0, a fix has at least 3 satellites…", "fail"),
    "live_stale": ("Live packets are live", "A packet marked live that reached Runway long after its time is history marked live.", "fail"),
    "identity": ("Same device as the login", "IMEI, number plate and firmware as the login sent them, or as the test pins them.", "fail"),
    "login_first": ("Login first", "The first packet on every connection is the login.", "fail"),
    "frame_live": ("Live frame numbers step by one", "Each live packet's frame number is the one before it plus one — or the numbers between came as history (made while offline).", "fail"),
    "frame_repeat": ("Frame number used once", "No two packets share a frame number. The same packet sent again is a warning; a different packet with it is a failure.", "fail"),
    "live_time_order": ("Live times go forward", "A live packet is never older than the live packet before it.", "fail"),
    "live_delay": ("Live packets arrive live", "A live packet reaches Runway within the spec's limit of the time it carries.", "warn"),
    "future_time": ("Device clock not ahead", "No packet carries a time later than when it reached Runway.", "fail"),
    "history_order": ("History in the order set", "History packets in the order the device is set to send them: oldest first (FIFO) or newest first (LIFO).", "fail"),
    "interval": ("Packets at the interval set", "Location packets made at the interval the device is set to — ignition on, ignition off, asleep — "
                 "within the margin for that interval (±1 s up to 20 s, ±2 s up to 2 min, ±3 s up to 10 min, ±5 s beyond); "
                 "judged over every location packet in the order of its time, live and history together.", "fail"),
    "time_repeat": ("Packet times differ", "One moment, one packet: no two different packets of one kind (location, each alert) carry the "
                    "same time — live or history, in a row or not. The clock did not move, or a packet was made twice. "
                    "Sent live and again as history is a warning; the same packet again is a resend (Frame number used once).", "fail"),
    "status_alert": ("Status matches the alert ID", "Location updates: alert 1 is live (L), alert 2 history (H).", "fail"),
    "alert_type": ("Alert ID matches the packet type", "The AIS-140 table of alert IDs per packet type.", "fail"),
    "ignition_alert": ("Ignition matches IN / IF", "An ignition-on packet says ignition 1, an ignition-off packet 0.", "fail"),
}
LEVELS = {"pass": 0, "warn": 1, "fail": 2}
# where each check's rule comes from — so a developer sees which document says so
CHECK_SOURCE = {
    "framing": "AIS-140 standard: $…*CC, one sentence per line (CR LF)",
    "checksum": "The XOR the device's own live packets carry — neither the sheet nor the standard names the algorithm",
    "field_count": "The device's sheet: its field list (or the format set in Configuration)",
    "unknown_packet": "The device's sheet: its packet types",
    "not_packet": "AIS-140 standard: everything sent is a $…* sentence",
    "identity": "AIS-140 standard: one IMEI, firmware and number plate per device, as its login says",
    "login_first": "AIS-140 standard: the login opens every connection",
    "frame_live": "AIS-140 standard: frame number, a running sequence",
    "frame_repeat": "AIS-140 standard: frame number, a running sequence",
    "live_time_order": "AIS-140 standard: live packets as they are made",
    "live_delay": "Runway's limit: live within 60 s",
    "live_stale": "AIS-140 standard: packets buffered while offline are history (H)",
    "future_time": "AIS-140 standard: time in UTC",
    "history_order": "The device's configuration, as given (FIFO / LIFO)",
    "time_repeat": "AIS-140 standard: each packet carries the time it was made",
    "status_alert": "AIS-140 standard: alert 1 live, alert 2 history",
    "alert_type": "AIS-140 standard: the table of alert IDs",
    "ignition_alert": "AIS-140 standard: IN / IF are ignition on / off",
    "consistency": "AIS-140 standard: what each alert means",
    "interval": "The device's configuration, as given; margins as Log analysis's",
}
# the fields whose values a test should see change — what has not been exercised yet is said
WATCH = ("heading_deg", "digital_input_status", "digital_output_status", "analog_input_1", "analog_input_2",
         "emergency_status", "tamper_alert", "main_power_status", "gsm_signal", "gps_fix", "satellites")


def xor(data: bytes) -> int:
    return reduce(lambda a, b: a ^ b, data, 0)


def split_fields(body: str) -> list[str]:
    """Header and fields; a parenthesized field (OTA response, a custom packet's data) may hold
    commas — folded back into one field."""
    tokens = body.split(",")
    o = next((i for i, t in enumerate(tokens) if "(" in t), None)
    if o is not None:
        c = next((i for i in range(o, len(tokens)) if ")" in tokens[i]), None)
        if c is not None and c > o:
            tokens[o:c + 1] = [",".join(tokens[o:c + 1])]
    return tokens


def device_time(date: str | None, time: str | None) -> datetime | None:
    """DDMMYYYY (or a short DDMMYY, flagged by the date rule but still placed in order) + hhmmss, UTC."""
    if not date or not time or len(time) != 6 or not (date + time).isdigit():
        return None
    year = date[4:] if len(date) == 8 else ("20" + date[4:] if len(date) == 6 else None)
    if year is None:
        return None
    try:
        return datetime(int(year), int(date[2:4]), int(date[:2]), int(time[:2]), int(time[2:4]), int(time[4:]),
                        tzinfo=timezone.utc)
    except ValueError:
        return None


def _iso(t: datetime | None) -> str | None:
    return t.strftime("%Y-%m-%d %H:%M:%S") if t else None


def duration(sec: float) -> str:
    """'45 s', '12 min', '2 h 35 min', '3 days 4 h'."""
    sec = int(round(sec))
    if sec < 60:
        return f"{sec} s"
    if sec < 3600:
        return f"{sec // 60} min"
    if sec < 86400:
        h, m = divmod(sec // 60, 60)
        return f"{h} h {m} min" if m else f"{h} h"
    d, h = divmod(sec // 3600, 24)
    return f"{d} day{'s' if d > 1 else ''} {h} h" if h else f"{d} day{'s' if d > 1 else ''}"


SILENCE_MIN_S = 1800              # a gap this long, and over 10 intervals, is a silence, not packets missing


def _float(v: str | None) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def _coord(v: str | None, hemi: str | None, neg: str) -> float | None:
    x = _float(v)
    return None if x is None else (-x if hemi == neg else x)


def _int(v: str | None) -> int | None:
    return int(v) if v and v.lstrip("-").isdigit() else None


class Gate:
    def __init__(self, spec: Spec, expect: dict | None = None, wire: bool = True, config: dict | None = None):
        self.spec = spec
        self.config = clean_config(spec, config)      # the device's settings: intervals, history order, formats
        self.wire = wire               # packets as they came off the wire; a file's line endings are the editor's
        self.expect = {k: str(v).strip() for k, v in (expect or {}).items() if v and str(v).strip()}
        self.learned: dict[str, str] = {}
        self.rows: list[dict] = []
        self.checks: dict[str, dict] = {}
        # per scenario: packets received, how many of them failed (passed = received - failed: a packet
        # with warnings only did not fail), and the first one
        self.scenarios = {s["key"]: {"key": s["key"], "label": s.get("label") or s["key"], "count": 0, "failed": 0,
                                     "first": None}
                          for s in spec.scenarios}
        self.conns: Counter = Counter()
        self.headers: Counter = Counter()
        self.status: Counter = Counter()
        self.checksum = Counter()
        self.firmware: dict[str, int] = {}
        self.devices: dict[str, int] = {}
        self._frames: dict[int, tuple[int, int]] = {}
        self._live = {"frame": None, "time": None, "n": None}
        self._hist = {"time": None, "n": None}
        self._times: dict[tuple, tuple] = {}     # (kind, device time) → the first packet of that kind at that time
        self._derived_n = -1           # how many packets the timeline checks last saw

    # ── one packet ────────────────────────────────────────────────────────────────────────────
    def feed(self, raw: bytes, received: float | None = None, conn: str | None = None,
             skipped: bool = False) -> dict:
        if skipped:
            return self._not_packet(raw, received, conn)
        n = len(self.rows) + 1
        flags: list[dict] = []
        spec = self.spec

        def flag(check, text, field=None, label=None, expected=None, got=None, level=None, why=None):
            key = f"field.{field}" if check == "field" else check
            flags.append({"key": key, "check": check, "field": field,
                          "label": label or CHECKS.get(check, ("",))[0],
                          "level": level or CHECKS.get(check, ("", "", "fail"))[2],
                          "text": text, "expected": expected, "got": got, "why": why})

        text = raw.decode("ascii", "replace")
        star = text.find("*")
        term = spec.terminator
        if not text.startswith("$"):
            flag("framing", "does not start with $")
        if star < 0:
            flag("framing", "no * before the checksum")
            body, tail = text[1:].rstrip("\r\n"), ""
        else:
            body, tail = text[1:star], text[star + 1:]
        if self.wire and term and not text.endswith(term):
            end = repr(text[-2:]) if text else "nothing"
            flag("framing", f"ends with {end}, not CR LF", expected="CR LF", got=end)
        sent = tail[:-len(term)] if term and tail.endswith(term) else tail.rstrip("\r\n")

        tokens = split_fields(body)
        header, fields = tokens[0], tokens[1:]
        pspec = spec.packets.get(header)
        vals: dict[str, str] = {}
        if pspec is None:
            flag("unknown_packet", f"{header} is not in the {spec.name} sheet", got=header)
        else:
            count = len(fields)
            fmt = self.config["formats"].get(header)          # the layout the device is set to, if the spec offers one
            expected = int(fmt) if fmt and fmt.isdigit() else pspec.fields
            at = {}
            for fs in pspec.field_list:
                if fs.formats and fmt not in fs.formats:
                    continue
                idx = fs.index if fs.index > 0 else count + 1 + fs.index
                if 1 <= idx <= count:
                    at[idx] = fs
            if expected is not None and count != expected:
                note = ""
                extra = [i for i in range(1, count + 1) if i not in at]
                if count > expected and extra:
                    note = "; not in the sheet: " + ", ".join(f"field {i} ({fields[i - 1]})" for i in extra[:3])
                flag("field_count", f"{count} fields, expected {expected}{note}", expected=str(expected), got=str(count))
            for idx, fs in sorted(at.items()):
                v = fields[idx - 1]
                vals[fs.name] = v
                want = "a value" if v == "" and "" not in fs.allow else rules.check(v, fs.rules, list(fs.allow))
                if want:
                    flag("field", f"{v or '(empty)'}, expected {want}", field=fs.name, label=fs.label,
                         expected=want, got=v, level=fs.level)
                key = fs.rules.get("expect")
                if key and v:
                    pinned, said = (self.expect[key], "the test") if key in self.expect else (self.learned.get(key), "the login")
                    if pinned and v != pinned:
                        flag("identity", f"{fs.label} {v}, {said} says {pinned}", field=fs.name,
                             expected=pinned, got=v)
            if header == spec.login or not self.learned:
                for key in spec.expect:
                    if vals.get(key):
                        self.learned[key] = vals[key]

        # checksum: what came, against the XOR of the packet — and, when it differs, what it does match
        if spec.checksum.get("algorithm") == "xor" and star >= 0:
            if not sent:
                self.checksum["missing"] += 1
                flag("checksum", "no checksum after *", expected="2 hex digits", got="(none)", why="missing")
            else:
                want = f"{xor(raw[:star]):02X}"
                if sent.upper() == want:
                    self.checksum["ok"] += 1
                else:
                    self.checksum["bad"] += 1
                    why, hint = self._checksum_hint(raw, star, sent, vals)
                    flag("checksum", f"{sent}, the packet's XOR is {want}{hint}", expected=want, got=sent, why=why)

        # the order of packets
        conn_key = conn if conn is not None else ""
        first_on_conn = self.conns[conn_key] == 0
        self.conns[conn_key] += 1
        if first_on_conn and header != spec.login:
            where = f"connection {conn}" if conn else "the capture"
            flag("login_first", f"the first packet on {where} is {header}, not the login", got=header)

        ptype, status = vals.get("packet_type"), vals.get("packet_status")
        alert = _int(vals.get("alert_id"))
        if ptype is not None and alert is not None:
            m = spec.status_by_alert.get(ptype) or {}
            if str(alert) in m and status != m[str(alert)]:
                flag("status_alert", f"{ptype} alert {vals['alert_id']} with status {status}, expected {m[str(alert)]}",
                     expected=m[str(alert)], got=status)
            ids = spec.alert_ids.get(ptype)
            interim = (spec.alert_ids_interim.get(ptype) or {}).get(alert)
            if interim:
                flag("alert_type", f"{ptype} with alert ID {vals['alert_id']}: {interim}", level="warn",
                     expected=f"an ID of its own for {ptype}", got=vals["alert_id"], why="interim")
            elif ids is not None and alert not in ids:
                flag("alert_type", f"{ptype} with alert ID {vals['alert_id']}, the standard gives {', '.join(map(str, ids))}",
                     expected=", ".join(map(str, ids)), got=vals["alert_id"])
        for rule in spec.when:
            cond = rule.get("if") or {}
            if not cond or not all((alert == _int(str(v))) if k == "alert_id" else vals.get(k) == str(v)
                                   for k, v in cond.items()):
                continue
            for k, want in (rule.get("then") or {}).items():
                got = vals.get(k)
                if got is None or got == "":
                    continue
                need = rules.check(got, want)
                if need:
                    flag("consistency", f"{got} in {k.replace('_', ' ')}, but {rule.get('says') or need}", field=k,
                         expected=need, got=got)
        want_ign = spec.ignition_by_type.get(ptype or "")
        if want_ign is not None and vals.get("ignition") not in (None, want_ign):
            flag("ignition_alert", f"{ptype} with ignition {vals['ignition']}", expected=want_ign, got=vals["ignition"])

        dt = device_time(vals.get("date"), vals.get("time"))
        if dt and received is not None and dt.timestamp() > received + spec.settings["future_tolerance_s"]:
            flag("future_time", f"carries {_iso(dt)}, {round(dt.timestamp() - received)} s after it reached Runway")

        frame = _int(vals.get("frame_number"))
        if frame is not None:
            sig = hash(raw)
            prior = self._frames.get(frame)
            if prior:
                same = prior[1] == sig
                flag("frame_repeat", f"frame {vals['frame_number']} again, " +
                     (f"the same packet as #{prior[0]}" if same else f"a different packet from #{prior[0]}"),
                     level="warn" if same else "fail", got=vals["frame_number"])
            self._frames[frame] = (n, sig)
            # live frame numbers are judged over the whole test (_frame_flags): a jump is no fault when
            # the numbers between came as history — a device numbers every packet it makes

        if dt and status == "L":
            last = self._live["time"]
            if last and dt < last:
                flag("live_time_order", f"{_iso(dt)} after {_iso(last)} (#{self._live['n']})")
            if received is not None and received - dt.timestamp() > spec.settings["live_stale_s"]:
                flag("live_stale", f"marked live, but {duration(received - dt.timestamp())} old when it reached Runway",
                     expected=f"within {duration(spec.settings['live_stale_s'])}", got=duration(received - dt.timestamp()))
            elif received is not None and received - dt.timestamp() > spec.settings["live_max_delay_s"]:
                flag("live_delay", f"{round(received - dt.timestamp())} s old when it reached Runway",
                     expected=f"within {spec.settings['live_max_delay_s']} s", got=f"{round(received - dt.timestamp())} s")
            self._live.update(time=dt if not last or dt > last else last, n=n)
        elif status == "L":
            self._live["n"] = n
        if dt and status == "H":
            last = self._hist["time"]
            order = self.config["history_order"]
            if last and order == "fifo" and dt < last:
                flag("history_order", f"{_iso(dt)} after {_iso(last)} (#{self._hist['n']}), set to send oldest first (FIFO)")
            elif last and order == "lifo" and dt > last:
                flag("history_order", f"{_iso(dt)} after {_iso(last)} (#{self._hist['n']}), set to send newest first (LIFO)")
            self._hist.update(time=dt, n=n, sig=hash(raw))

        # one moment, one packet: a packet of one kind (a location update, or each alert) carrying a time an
        # earlier different packet of that kind carried. A location update's live and history IDs (NR 1 / 2)
        # are one kind: the same moment sent live and again as history is a warning, not a clock fault.
        if dt:
            kind = (header, ptype or "", "" if ptype in spec.status_by_alert else str(alert))
            # what the packet says, but for its number and how it was sent: equal → the same packet made again
            data = tuple(sorted((k, v) for k, v in vals.items() if k not in ("frame_number", "packet_status", "alert_id")))
            seen = self._times.get((kind, dt))
            if seen is None:
                self._times[(kind, dt)] = (n, hash(raw), status, data)
            elif seen[1] != hash(raw):                    # the same packet again is a resend (frame_repeat)
                pn, _, pstatus, pdata = seen
                if {pstatus, status} == {"L", "H"} and pdata == data:
                    flag("time_repeat", f"{_iso(dt)}: sent live as #{pn}, and again as history", level="warn", why="resent")
                elif pdata == data:
                    flag("time_repeat", f"{_iso(dt)}, as #{pn}: the same data again, only the frame number differs "
                         f"— a duplicate packet", why="duplicate")
                else:
                    flag("time_repeat", f"{_iso(dt)}, as #{pn}: different data at one time — the clock did not move",
                         why="clock")

        for f in flags:
            self._weigh(f)
        level = max((f["level"] for f in flags), key=LEVELS.get, default="pass")

        # scenarios exercised (how many failed is read from the packets: a timeline check may still flag them)
        scen = []
        for sc in spec.scenarios:
            match = sc.get("match") or {}
            if all((header if k == "header" else vals.get(k)) == str(v) or
                   (k == "alert_id" and alert == _int(str(v))) for k, v in match.items()):
                s = self.scenarios[sc["key"]]
                s["count"] += 1
                s["first"] = s["first"] or n
                scen.append(sc["key"])
        row = {"n": n, "received": received, "conn": conn, "header": header, "type": ptype,
               "alert": vals.get("alert_id"), "status": status, "frame": vals.get("frame_number"),
               "time": _iso(dt), "t": dt.timestamp() if dt else None, "ign": vals.get("ignition"),
               "fix": vals.get("gps_fix") or vals.get("gps_valid"), "speed": _float(vals.get("speed_kph")),
               "lat": _coord(vals.get("latitude"), vals.get("latitude_dir"), "S"),
               "lon": _coord(vals.get("longitude"), vals.get("longitude_dir"), "W"),
               "odo": _float(vals.get("odometer_m")),
               "vals": {k: vals[k] for k in WATCH if k in vals},
               "scen": scen, "level": level, "flags": flags, "raw": text.rstrip("\r\n")}
        self.rows.append(row)
        self.headers[header] += 1
        if status:
            self.status[status] += 1
        if vals.get("firmware_version"):
            self.firmware[vals["firmware_version"]] = self.firmware.get(vals["firmware_version"], 0) + 1
        if vals.get("imei"):
            self.devices[vals["imei"]] = self.devices.get(vals["imei"], 0) + 1
        return row

    def _weigh(self, f: dict) -> dict:
        """The level a flag carries for this device: the spec's ``levels`` over the gate's own. When the
        spec lowers it, ``was`` keeps the gate's — a test-plan case that names the check still judges it."""
        lv = self.spec.levels.get(f["key"]) or self.spec.levels.get(f["check"])
        if lv and lv != f["level"]:
            if LEVELS[lv] < LEVELS[f["level"]]:
                f["was"] = f["level"]
            f["level"] = lv
        return f

    def _not_packet(self, raw: bytes, received, conn) -> dict:
        """Bytes the device sent that the protocol could not read: a row of their own, failed."""
        n = len(self.rows) + 1
        text = raw.decode("ascii", "replace")
        shown = text.replace("\r", "\\r").replace("\n", "\\n")
        flag = {"key": "not_packet", "check": "not_packet", "field": None, "label": CHECKS["not_packet"][0],
                "level": "fail", "text": f"{len(raw)} byte{'s' if len(raw) != 1 else ''}: {shown[:80]}"
                + ("…" if len(shown) > 80 else ""), "expected": "a packet", "got": shown[:40], "why": None}
        self._weigh(flag)
        row = {"n": n, "received": received, "conn": conn, "header": "?", "type": None, "alert": None, "status": None,
               "frame": None, "time": None, "t": None, "ign": None, "fix": None, "speed": None, "lat": None,
               "lon": None, "odo": None, "vals": {}, "scen": [], "level": flag["level"], "flags": [flag], "raw": text.rstrip("\r\n")}
        self.rows.append(row)
        self.headers["?"] += 1
        return row

    # ── checks over the whole timeline ────────────────────────────────────────────────────────────
    def _derive(self) -> None:
        """Flags that need every packet in the order of its time — the interval the device is set to.
        History arrives late and fills the live stream's gaps, so these are worked out again whenever
        packets came since (marked ``derived``, replaced each time), never judged on arrival."""
        if self._derived_n == len(self.rows):
            return
        self._derived_n = len(self.rows)
        for r in self.rows:
            if any(f.get("derived") for f in r["flags"]):
                r["flags"] = [f for f in r["flags"] if not f.get("derived")]
                r["level"] = max((f["level"] for f in r["flags"]), key=LEVELS.get, default="pass")
        for n, f in [*self._interval_flags(), *self._frame_flags()]:
            self._weigh(f)
            r = self.rows[n - 1]
            r["flags"].append(f)
            if LEVELS[f["level"]] > LEVELS[r["level"]]:
                r["level"] = f["level"]

    def _frame_flags(self) -> list[tuple[int, dict]]:
        """Live frame numbers step by one — unless the numbers between came as history (made while
        offline) — a repeat, a number that never came, or a step back is a fault; a restart is not."""
        frames = [(r["n"], int(r["frame"])) for r in self.rows if (r["frame"] or "").isdigit()]
        every = {f for _, f in frames}
        live = [(r["n"], int(r["frame"]), r["frame"]) for r in self.rows if r["status"] == "L" and (r["frame"] or "").isdigit()]
        out = []
        for (pn, a, _), (n, b, text) in zip(live, live[1:]):
            if b == a + 1 or (a == 999999 and b in (0, 1)) or b < a - 100 or (b < 10 <= a):
                continue                                   # in step, rolled over, or a restart (counted apart)
            if b == a:
                what = "repeated"
            elif b > a:
                never = sum(1 for k in range(a + 1, b) if k not in every)
                if not never:
                    continue                               # the numbers between came as history
                what = f"{never} never came"
            else:
                what = "went back"
            out.append((n, {"key": "frame_live", "check": "frame_live", "field": None, "label": CHECKS["frame_live"][0],
                            "level": "fail", "text": f"frame {text} after {a} (#{pn}): {what}", "expected": str(a + 1),
                            "got": text, "why": None, "derived": True}))
        return out

    def _interval_flags(self) -> list[tuple[int, dict]]:
        cfg = self.config
        on, off, after, sleep = cfg["ign_on_s"], cfg["ign_off_s"], cfg["sleep_after_min"], cfg["sleep_s"]
        if not (on or off or sleep):
            return []
        per = self.spec.settings.get("periodic") or {"header": "PVT", "packet_type": "NR"}
        pts = sorted((r["t"], r["n"], r["ign"]) for r in self.rows
                     if r["header"] == per["header"] and r["type"] == per["packet_type"]
                     and r.get("t") is not None and r.get("ign") in ("0", "1"))
        out, prev, off_since = [], None, None
        for t, n, ign in pts:
            if prev and t == prev[0]:
                continue                                  # the same moment twice is its own check (history times)
            if ign == "0" and (prev is None or prev[2] == "1"):
                off_since = t
            if prev and prev[2] == ign:
                gap = t - prev[0]
                if ign == "1":
                    wants, state = [on], "ignition on"
                else:
                    lim = after * 60 if after and sleep else None
                    if lim is None:
                        wants, state = [off], "ignition off"
                    elif prev[0] - off_since >= lim:
                        wants, state = [sleep], "asleep"
                    elif t - off_since > lim:
                        wants, state = [off, sleep], "going to sleep"
                    else:
                        wants, state = [off], "ignition off"
                wants = [w for w in wants if w]
                if wants and not any(abs(gap - w) <= margin_for(w, {}) for w in wants):
                    w = min(wants, key=lambda x: abs(gap - x))
                    m = margin_for(w, {})
                    if gap > max(10 * w, SILENCE_MIN_S):
                        # hours without a packet: the device off or out of reach — told apart from packets missing
                        why, missed, level = "silent", 0, "warn"
                        text = f"no location packet for {duration(gap)} after #{prev[1]} ({state})"
                    else:
                        missed = round(gap / w) - 1 if gap > w + m else 0
                        why = "early" if gap < w - m else ("missed" if missed >= 1 else "late")
                        level = "fail"
                        text = f"{gap:g} s after #{prev[1]}, set to {w:g} s ({state})" + \
                            (f": about {missed} missing" if why == "missed" else f": {why}")
                    out.append((n, {"key": "interval", "check": "interval", "field": None, "label": CHECKS["interval"][0],
                                    "level": level, "text": text, "expected": f"{w:g} s, ±{m:g} s", "got": duration(gap),
                                    "why": why, "state": state, "missed": missed, "gap": gap, "derived": True}))
            prev = (t, n, ign)
        return out

    def _checks(self) -> list[dict]:
        """One row per kind of deviation, from the packets' flags."""
        checks: dict[str, dict] = {}
        for r in self.rows:
            seen = set()
            for f in r["flags"]:
                c = checks.get(f["key"])
                if c is None:
                    about = CHECKS.get(f["check"], ("", ""))[1] if f["check"] != "field" else self._field_about(r["header"], f["field"])
                    src = CHECK_SOURCE.get(f["check"], "") if f["check"] != "field" else self._field_source(r["header"], f["field"])
                    if f.get("was"):
                        about += f" A warning for the {self.spec.name} (its spec's levels); a test-plan case that names it still fails on it."
                    c = checks[f["key"]] = {"key": f["key"], "label": f["label"], "level": f["level"], "about": about,
                                            "packets": 0, "first": r["n"], "last": r["n"], "example": f["text"], "source": src}
                if f["key"] not in seen:                     # a packet counts once per check
                    c["packets"] += 1
                    seen.add(f["key"])
                c["last"] = r["n"]
                if LEVELS[f["level"]] > LEVELS[c["level"]]:
                    c["level"], c["example"] = f["level"], f["text"]     # the example is one of the worst
        self.checks = checks
        return sorted(checks.values(), key=lambda c: (-LEVELS[c["level"]], -c["packets"]))

    def _checksum_hint(self, raw: bytes, star: int, sent: str, vals: dict) -> tuple[str, str]:
        """Why a checksum is wrong, when it can be told: (why, the words for the flag)."""
        if f"{xor(raw[:star + 1]):02X}" == sent.upper():
            return "star_included", "; it matches only with the * included"
        # a history packet whose checksum is still the one it had live: rewrite status and alert back
        m = self.spec.status_by_alert.get(vals.get("packet_type") or "") or {}
        live_alert = next((a for a, s in m.items() if s == "L"), None)
        if vals.get("packet_status") == "H" and live_alert and vals.get("alert_id"):
            width = len(vals["alert_id"])
            before = f",{vals['alert_id']},H,".encode()
            after = f",{live_alert.zfill(width)},L,".encode()
            if before in raw[:star] and f"{xor(raw[:star].replace(before, after, 1)):02X}" == sent.upper():
                return "kept_live", f"; it is the checksum of this packet live ({live_alert.zfill(width)}, L), not as history"
        return "other", ""

    def _field_source(self, header: str, name: str) -> str:
        p = self.spec.packets.get(header)
        fs = next((f for f in (p.field_list if p else ()) if f.name == name), None)
        return fs.source if fs else ""

    def _field_about(self, header: str, name: str) -> str:
        p = self.spec.packets.get(header)
        fs = next((f for f in (p.field_list if p else ()) if f.name == name), None)
        if not fs:
            return ""
        bits = [f"{k} {v}" if v is not True else k for k, v in fs.rules.items() if k != "expect"]
        return f"{fs.label}: " + ", ".join(bits)

    # ── reading the run ───────────────────────────────────────────────────────────────────────
    def summary(self) -> dict:
        self._derive()
        rows = self.rows
        failed = sum(1 for r in rows if r["level"] == "fail")
        warned = sum(1 for r in rows if any(f["level"] == "warn" for f in r["flags"]))   # any warning, failed or not
        checks = self._checks()
        bad = Counter(k for r in rows if r["level"] == "fail" for k in r["scen"])
        scen = [{**s, "failed": bad.get(s["key"], 0), "passed": s["count"] - bad.get(s["key"], 0)}
                for s in self.scenarios.values()]
        seen = sum(1 for s in scen if s["count"])
        unchecked = [k for k in ("ign_on_s", "ign_off_s", "history_order") if not self.config.get(k)]
        verdict = "fail" if failed else ("incomplete" if seen < len(scen) or unchecked else "pass")
        return {
            "spec": self.spec.name, "packets": len(rows), "failed": failed, "warned": warned,
            "passed": len(rows) - failed, "live": self.status.get("L", 0), "history": self.status.get("H", 0),
            "headers": dict(self.headers), "checksum": dict(self.checksum),
            "checks": checks, "scenarios": scen, "scenarios_seen": seen, "scenarios_total": len(scen),
            "verdict": verdict, "firmware": self.firmware, "devices": self.devices,
            "learned": dict(self.learned), "expect": dict(self.expect), "config": self.config, "unchecked": unchecked,
            "first": rows[0]["received"] if rows else None, "last": rows[-1]["received"] if rows else None,
        }

    def packets(self, level: str = "", check: str = "", scenario: str = "", offset: int = 0, limit: int = 100,
                newest_first: bool = True) -> dict:
        self._derive()
        rows = self.rows
        if level == "fail":
            rows = [r for r in rows if r["level"] == "fail"]
        elif level == "warn":
            rows = [r for r in rows if any(f["level"] == "warn" for f in r["flags"])]
        elif level == "flagged":
            rows = [r for r in rows if r["level"] != "pass"]
        if check:
            rows = [r for r in rows if any(f["key"] == check for f in r["flags"])]
        if scenario:
            sc = next((s for s in self.spec.scenarios if s["key"] == scenario), None)
            if sc:
                m = sc.get("match") or {}
                rows = [r for r in rows if all(str(r.get({"header": "header", "packet_type": "type",
                                                          "packet_status": "status", "alert_id": "alert"}.get(k, k))) == str(v)
                                               for k, v in m.items())]
        total = len(rows)
        if newest_first:
            rows = rows[::-1]
        return {"total": total, "rows": rows[offset:offset + limit]}

    def detail(self, n: int) -> dict | None:
        """One packet field by field: number, name, value, and what its rules expected."""
        self._derive()
        if not 1 <= n <= len(self.rows):
            return None
        row = self.rows[n - 1]
        text = row["raw"]
        star = text.find("*")
        tokens = split_fields(text[1:star] if star >= 0 else text[1:])
        fields = tokens[1:]
        p = self.spec.packets.get(tokens[0])
        at = {}
        fmt = self.config["formats"].get(tokens[0])
        for fs in (p.field_list if p else ()):
            if fs.formats and fmt not in fs.formats:
                continue                                   # a field of another format the device is not set to
            idx = fs.index if fs.index > 0 else len(fields) + 1 + fs.index
            if 1 <= idx <= len(fields):
                at[idx] = fs
        by_field = {}
        for f in row["flags"]:
            if f["field"]:
                by_field.setdefault(f["field"], []).append(f)
        out = []
        for i, v in enumerate(fields, 1):
            fs = at.get(i)
            fl = by_field.get(fs.name, []) if fs else []
            out.append({"index": i, "name": fs.name if fs else "", "label": fs.label if fs else "not in the sheet",
                        "source": fs.source if fs else "",
                        "value": v, "level": max((f["level"] for f in fl), key=LEVELS.get, default="pass" if fs else "warn"),
                        "note": "; ".join(f["text"] for f in fl)})
        return {**row, "fields": out, "packet": p.label if p else tokens[0]}
