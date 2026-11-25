"""Firmware test — the gate holds each packet against the Locate 140 4G sheet and against the packets
before it; a run survives a restart; the API runs a test over a file and over a listener.

Packets are built here, with the real XOR checksum and a made-up IMEI — never a device's capture.
"""

import io
import json
from functools import reduce

import pytest

from runway.fwtest import rules
from runway.fwtest.gate import Gate
from runway.fwtest.runs import FirmwareTestSink, Runs
from runway.fwtest.spec import load, parse

IMEI = "356938035643809"
OTHER = "490154203237518"
T0 = 1795000000.0          # received times: 2026-11-18 around 10:26 UTC


def xor(b: bytes) -> int:
    return reduce(lambda a, c: a ^ c, b, 0)


def sentence(body: str, checksum: str | None = "auto", end: str = "\r\n") -> bytes:
    """$body*CC CR LF — CC the XOR of the bytes between '$' and '*', the standard's."""
    raw = ("$" + body).encode()
    cs = f"{xor(body.encode()):02X}" if checksum == "auto" else (checksum or "")
    return raw + b"*" + cs.encode() + end.encode()


def login(imei=IMEI, fw="V7.6.0", **kw) -> bytes:
    return sentence(f"LGN,DL1CAB1234,{imei},{fw},AIS140,28.550455,N,77.268884,E", **kw)


def hhmmss(epoch: float) -> tuple[str, str]:
    from datetime import datetime, timezone
    d = datetime.fromtimestamp(epoch, timezone.utc)
    return d.strftime("%d%m%Y"), d.strftime("%H%M%S")


def pvt(frame=1, at=T0, ptype="NR", alert="01", status="L", imei=IMEI, fw="V7.6.0", ign="1", extra="",
        date=None, time=None, **kw) -> bytes:
    d, t = hhmmss(at)
    fields = ["PVT", "GTRP", fw, ptype, alert, status, imei, "DL1CAB1234", "1", date or d, time or t,
              "28.550455", "N", "77.268884", "E", "0.0", "000", "15", "222.0", "0.8", "0.4", "VI", ign, "1",
              "14.0", "3.7", "0", "C", "31", "404", "11", "CDF3", "642D633",
              "0", "0CDF", "0430", "0", "0CDF", "0280", "0", "0CDF", "0045", "0", "0CDF", "0094",
              "1100", "11", f"{frame:06d}", "0.0", "0.0"] + ([extra] if extra else []) + ["()"]
    return sentence(",".join(fields), **kw)


@pytest.fixture
def gate():
    return Gate(load("Locate 140 4G"))


def keys(row):
    return sorted({f["key"] for f in row["flags"]})


# ── a clean device ─────────────────────────────────────────────────────────────────────────────────
def test_a_clean_session_raises_nothing(gate):
    rows = [gate.feed(login(), T0 + 1, "c1")]
    for i in range(3):
        rows.append(gate.feed(pvt(frame=10 + i, at=T0 + 10 * i), T0 + 10 * i + 2, "c1"))
    rows.append(gate.feed(pvt(frame=13, at=T0 + 31, ptype="IF", alert="08", ign="0"), T0 + 33, "c1"))
    assert [r["level"] for r in rows] == ["pass"] * 5, [r["flags"] for r in rows]
    s = gate.summary()
    assert s["checksum"] == {"ok": 5}
    assert s["verdict"] == "incomplete"                       # nothing failed, but not every scenario yet
    seen = {x["key"]: x["count"] for x in s["scenarios"]}
    assert (seen["login"], seen["live"], seen["if"], seen["in"]) == (1, 3, 1, 0)
    assert s["learned"] == {"imei": IMEI, "vehicle_reg_no": "DL1CAB1234", "firmware_version": "V7.6.0"}


def test_each_scenario_counts_received_passed_and_failed(gate):
    """Passed = did not fail: a packet with a warning only (here, late) still passed."""
    gate.feed(login(), T0, "c")
    gate.feed(pvt(frame=1, at=T0), T0 + 1, "c")                       # clean
    gate.feed(pvt(frame=2, at=T0 + 10), T0 + 200, "c")                # late: a warning, not a failure
    gate.feed(pvt(frame=3, at=T0 + 20, checksum="00"), T0 + 21, "c")  # wrong checksum: failed
    s = gate.summary()
    live = next(x for x in s["scenarios"] if x["key"] == "live")
    assert (live["count"], live["passed"], live["failed"]) == (3, 2, 1)
    assert (s["packets"], s["passed"], s["failed"], s["warned"]) == (4, 3, 1, 1)
    rows = gate.packets(level="fail", scenario="live")
    assert [r["n"] for r in rows["rows"]] == [4]


def test_insights_say_what_was_found_worst_first(gate):
    from runway.fwtest.insights import insights

    gate.feed(login(), T0, "c")
    for i in range(3):
        gate.feed(pvt(frame=10 + i, at=T0 + 10 * i), T0 + 10 * i + 1, "c")                      # live: right
    live = pvt(frame=5, status="L", alert="01", at=T0 - 600)
    kept = live[live.index(b"*") + 1:][:2].decode()
    gate.feed(pvt(frame=5, status="H", alert="02", at=T0 - 600, checksum=kept), T0 + 40, "c")    # history: kept
    gate.feed(pvt(frame=6, status="H", alert="02", at=T0 - 590, date="171126"), T0 + 41, "c")  # short date
    found = insights(gate)
    levels = [i["level"] for i in found]
    assert levels == sorted(levels, key=["fail", "warn", "info", "ok"].index)          # worst first
    by = {i["title"]: i for i in found}
    ck = next(i for i in found if i["title"].startswith("Checksum wrong"))
    assert "Wrong on 1 of 2 history PVTs: each keeps the checksum it had live" in ck["text"]
    assert "Right on every live PVT (3 of 3)" in ck["text"] and ck["filter"] == {"check": "checksum"}
    date = by["Date not as the sheet says"]
    assert date["text"] == "1 packet (1 history) carry 171126; the sheet says a date DDMMYYYY."
    assert date["filter"] == {"check": "field.date"}
    assert any(i["level"] == "info" and "scenarios not seen yet" in i["title"] for i in found)
    assert "Firmware V7.6.0 in every packet" in by


def test_every_scenario_and_no_failure_is_a_pass():
    spec = parse({"name": "Tiny", "checksum": {"algorithm": "xor"}, "terminator": "\r\n",
                  "packets": {"LGN": {"fields": 8, "field_list": []}},
                  "scenarios": [{"key": "login", "label": "Login", "match": {"header": "LGN"}}]})
    g = Gate(spec)
    g.feed(login(), T0, "c")
    assert g.summary()["verdict"] == "incomplete"            # its intervals and history order never given
    assert g.summary()["unchecked"] == ["ign_on_s", "ign_off_s", "history_order"]
    g = Gate(spec, config={"ign_on_s": 10, "ign_off_s": 60, "history_order": "fifo"})
    g.feed(login(), T0, "c")
    assert g.summary()["verdict"] == "pass"


# ── framing and checksum ───────────────────────────────────────────────────────────────────────────
def test_a_wrong_or_missing_checksum_fails(gate):
    gate.feed(login(), T0, "c")
    bad = gate.feed(pvt(checksum="00"), T0, "c")
    assert keys(bad) == ["checksum"] and bad["flags"][0]["got"] == "00"
    none = gate.feed(sentence("HEL,GTRP,V7.6.0," + IMEI + ",31,10", checksum=None), T0, "c")
    assert "checksum" in keys(none) and "no checksum" in none["flags"][0]["text"]
    assert gate.summary()["checksum"] == {"ok": 1, "bad": 1, "missing": 1}


def test_a_history_packet_keeping_its_live_checksum_is_named(gate):
    """The fault V7.06.10 has: a packet buffered as history keeps the checksum it had live."""
    gate.feed(login(), T0, "c")
    live = pvt(frame=5, status="L", alert="01")
    stale = pvt(frame=5, status="H", alert="02", checksum=live[live.index(b"*") + 1:][:2].decode())
    row = gate.feed(stale, T0, "c")
    f = next(f for f in row["flags"] if f["key"] == "checksum")
    assert "checksum of this packet live (01, L)" in f["text"]


def test_a_checksum_over_the_star_too_is_named(gate):
    raw = b"$LGN,DL1CAB1234," + IMEI.encode() + b",V7.6.0,AIS140,28.550455,N,77.268884,E*"
    row = gate.feed(raw + f"{xor(raw[1:]):02X}".encode() + b"\r\n", T0, "c")
    assert "matches only with the * included" in row["flags"][0]["text"]


def test_the_checksum_is_the_standards_between_the_dollar_and_the_star(gate):
    """The XOR of the bytes between '$' and '*', both left out (NMEA 0183). V7.06.11's PVT takes in the '$':
    failed, and said so — the fix is one byte at the loop's start. A device whose sheet says otherwise sets it."""
    from runway.fwtest import diagnosis
    from runway.fwtest.insights import insights
    body = pvt(frame=1)[1:pvt(frame=1).index(b"*")]
    right = f"{xor(body):02X}".encode()
    from_dollar = f"{xor(b'$' + body):02X}".encode()
    assert right != from_dollar
    gate.feed(login(), T0, "c")
    assert gate.feed(b"$" + body + b"*" + right + b"\r\n", T0 + 1, "c")["level"] == "pass"
    row = gate.feed(b"$" + body.replace(b",000001,", b",000002,") + b"*"
                    + f"{xor(b'$' + body.replace(b',000001,', b',000002,')):02X}".encode() + b"\r\n", T0 + 11, "c")
    f = next(f for f in row["flags"] if f["key"] == "checksum")
    assert (f["why"], f["level"]) == ("dollar_included", "fail") and "matches only with the $ included" in f["text"]
    ck = next(c for c in gate.summary()["checks"] if c["key"] == "checksum")
    assert "between $ and *, both left out" in ck["about"]
    ins = next(i for i in insights(gate) if i["title"].startswith("Checksum wrong"))
    assert "the XOR is worked out from the $, which the standard leaves out" in ins["text"]
    d = next(d for d in diagnosis.defects(gate)["defects"] if d["key"] == "checksum")
    assert d["why"] == "dollar_included" and d["proved"] and "after '$'" in d["fix"]
    # a sheet that takes the '$' in says so, and then the standard's XOR is the one that is wrong
    from runway.fwtest.spec import SHIPPED
    data = json.loads((SHIPPED / load("Locate 140 4G").file).read_text(encoding="utf-8"))
    data["checksum"]["over"] = "from_dollar"
    g = Gate(parse(data))
    g.feed(sentence(login()[1:login().index(b"*")].decode(), checksum=f"{xor(b'$' + login()[1:login().index(b'*')]):02X}"), T0, "c")
    assert g.rows[0]["level"] == "pass"
    wrong = g.feed(b"$" + body + b"*" + right + b"\r\n", T0 + 1, "c")
    assert next(f for f in wrong["flags"] if f["key"] == "checksum")["why"] == "dollar_left_out"
    data["checksum"]["over"] = "from_star"
    with pytest.raises(ValueError, match="checksum over"):
        parse(data)


def test_line_endings_count_on_the_wire_not_in_a_file():
    lf = login(end="\n")
    assert "framing" in keys(Gate(load("Locate 140 4G")).feed(lf, T0, "c"))
    assert Gate(load("Locate 140 4G"), wire=False).feed(lf, None, None)["level"] == "pass"


# ── fields ─────────────────────────────────────────────────────────────────────────────────────────
def test_an_extra_field_is_named_and_the_rest_still_checked(gate):
    gate.feed(login(), T0, "c")
    row = gate.feed(pvt(extra="29.1", date="171126"), T0, "c")
    fc = next(f for f in row["flags"] if f["key"] == "field_count")
    assert fc["text"] == "51 fields, expected 50; not in the sheet: field 50 (29.1)"
    assert "field.date" in keys(row)                         # field 9 is still read where the sheet puts it
    assert "field.ota_update_details" not in keys(row)       # the last field is still the OTA response


def test_field_formats(gate):
    gate.feed(login(), T0, "c")
    row = gate.feed(pvt(time="07804.000", date="17112026"), T0, "c")
    f = next(f for f in row["flags"] if f["key"] == "field.time")
    assert (f["expected"], f["got"]) == ("a time hhmmss", "07804.000")
    assert rules.check("356938035643809", {"imei": True}) is None
    assert rules.check("356938035643808", {"imei": True}) == "15 digits with a valid check digit"
    assert rules.check("99", {"int": True, "range": [0, 31]}, ["99"]) is None
    assert rules.check("32", {"int": True, "range": [0, 31]}) == "0 to 31"


# ── the order of packets ───────────────────────────────────────────────────────────────────────────
def test_the_login_comes_first_on_each_connection(gate):
    assert "login_first" in keys(gate.feed(pvt(), T0, "c1"))
    assert "login_first" not in keys(gate.feed(login(), T0, "c2"))


def test_every_packet_is_the_logins_device(gate):
    gate.feed(login(), T0, "c")
    row = gate.feed(pvt(frame=1, imei=OTHER, fw="V7.6.1"), T0, "c")
    texts = [f["text"] for f in row["flags"] if f["key"] == "identity"]
    assert any(OTHER in t and IMEI in t for t in texts) and any("V7.6.1" in t for t in texts)


def test_a_pinned_firmware_wins_over_the_login():
    g = Gate(load("Locate 140 4G"), expect={"firmware_version": "V7.6.2"})
    row = g.feed(login(), T0, "c")
    f = next(f for f in row["flags"] if f["key"] == "identity")
    assert f["text"] == "Firmware version V7.6.0, the test says V7.6.2"


def test_live_frame_numbers_step_by_one(gate):
    gate.feed(login(), T0, "c")
    gate.feed(pvt(frame=100, at=T0), T0 + 1, "c")
    rep = gate.feed(pvt(frame=100, at=T0 + 10, alert="01"), T0 + 11, "c")
    skip = gate.feed(pvt(frame=103, at=T0 + 20), T0 + 21, "c")
    gate.summary()                                            # judged over the whole test
    assert "repeated" in next(f for f in rep["flags"] if f["key"] == "frame_live")["text"]
    f = next(f for f in rep["flags"] if f["key"] == "frame_repeat")             # a different packet: a failure
    assert (f["level"], f["was"]) == ("warn", "fail")                           # the Locate 140 4G weighs it a warning
    assert "2 never came" in next(f for f in skip["flags"] if f["key"] == "frame_live")["text"]
    gate.feed(pvt(frame=101, status="H", alert="02", at=T0 + 12), T0 + 30, "c")     # made offline, sent later
    gate.summary()
    assert "1 never came" in next(f for f in skip["flags"] if f["key"] == "frame_live")["text"]
    gate.feed(pvt(frame=102, status="H", alert="02", at=T0 + 14), T0 + 31, "c")
    gate.summary()
    assert not any(f["key"] == "frame_live" for f in skip["flags"])   # every number between came as history


def test_the_same_packet_sent_again_is_a_warning(gate):
    gate.feed(login(), T0, "c")
    p = pvt(frame=50, status="H", alert="02", at=T0 - 3600)
    gate.feed(p, T0, "c")
    again = gate.feed(p, T0 + 1, "c")
    f = next(f for f in again["flags"] if f["key"] == "frame_repeat")
    assert f["level"] == "warn" and "the same packet as #2" in f["text"]


def _history(g, *ages):
    """History packets made ``ages`` seconds before T0, sent in that order."""
    g.feed(login(), T0, "c")
    return [g.feed(pvt(frame=100 - i, status="H", alert="02", at=T0 - a), T0, "c") for i, a in enumerate(ages)]


def test_history_order_is_the_one_the_device_is_set_to():
    """FIFO: oldest first; LIFO: newest first — newest first is no fault on a LIFO device."""
    fifo = _history(Gate(load("Locate 140 4G"), config={"history_order": "fifo"}), 100, 200)
    assert "history_order" in keys(fifo[1]) and fifo[1]["level"] == "fail"
    assert "oldest first (FIFO)" in next(f for f in fifo[1]["flags"] if f["key"] == "history_order")["text"]
    lifo = _history(Gate(load("Locate 140 4G"), config={"history_order": "lifo"}), 100, 200)
    assert "history_order" not in keys(lifo[1])
    lifo_wrong = _history(Gate(load("Locate 140 4G"), config={"history_order": "lifo"}), 200, 100)
    assert "history_order" in keys(lifo_wrong[1])
    unset = _history(Gate(load("Locate 140 4G")), 100, 200)              # not set: not judged
    assert "history_order" not in keys(unset[1])


def test_history_packets_with_one_time(gate):
    same = _history(gate, 200, 200)                           # two different packets, one time: fails
    assert "time_repeat" in keys(same[1]) and same[1]["level"] == "fail"
    again = Gate(load("Locate 140 4G"))
    again.feed(login(), T0, "c")
    p = pvt(frame=7, status="H", alert="02", at=T0 - 300)
    again.feed(p, T0, "c")
    resent = again.feed(p, T0 + 1, "c")                      # the same packet again: a resend, a warning
    assert "time_repeat" not in keys(resent) and resent["level"] == "warn"


def test_one_moment_one_packet_live_or_history_in_a_row_or_not(gate):
    """Duplicate times are flagged wherever they fall: two live packets, a time that comes back later, and a
    moment sent live then again as history (a warning). Different kinds at one second are fine."""
    gate.feed(login(), T0, "c")
    gate.feed(pvt(frame=1, at=T0), T0 + 1, "c")
    live_again = gate.feed(pvt(frame=2, at=T0), T0 + 11, "c")              # the same data again, a new frame number
    f = next(f for f in live_again["flags"] if f["key"] == "time_repeat")
    assert (f["level"], f["why"]) == ("fail", "duplicate") and "only the frame number differs" in f["text"]
    stuck = gate.feed(pvt(frame=8, at=T0, ign="0"), T0 + 12, "c")          # other data at that time: a stuck clock
    f = next(f for f in stuck["flags"] if f["key"] == "time_repeat")
    assert (f["level"], f["why"]) == ("fail", "clock")
    gate.feed(pvt(frame=3, at=T0 + 10), T0 + 21, "c")
    later = gate.feed(pvt(frame=4, at=T0 + 10, ptype="NR", alert="01"), T0 + 31, "c")
    assert "time_repeat" in keys(later)
    alert = gate.feed(pvt(frame=5, at=T0 + 10, ptype="IN", alert="07"), T0 + 32, "c")   # another kind, one second
    assert "time_repeat" not in keys(alert)
    as_history = gate.feed(pvt(frame=6, at=T0 + 20, status="L"), T0 + 33, "c")
    resent = gate.feed(pvt(frame=7, at=T0 + 20, status="H", alert="02"), T0 + 600, "c")
    f = next(f for f in resent["flags"] if f["key"] == "time_repeat")
    assert (f["level"], f["why"]) == ("warn", "resent") and as_history["n"] == resent["n"] - 1


# ── nothing slips through ──────────────────────────────────────────────────────────────────────────
def test_bytes_that_are_not_a_packet_fail(tmp_path):
    run = Runs(tmp_path).create("Locate 140 4G", {"kind": "live", "port": 5999})
    run.add(login(), T0, "c")
    row = run.add(b"$PVT,GTRP,V7.6.0,NR,01,L,35693", T0 + 1, "c", skipped=True)     # cut off mid-way
    assert row["level"] == "fail" and row["flags"][0]["key"] == "not_packet"
    run.close()
    again = Runs(tmp_path).get(run.id)                       # kept as such across a restart
    assert again.gate.rows[1]["flags"][0]["key"] == "not_packet"


# ── packets.log: the packet as the device sent it ──────────────────────────────────────────────────
def test_the_log_holds_each_packet_as_its_own_text(tmp_path):
    """A developer opening packets.log reads what the device sent — CR LF and a stray byte shown where
    they are, nothing else converted; the first line says how it is written."""
    from runway.fwtest.runs import HEADER
    run = Runs(tmp_path).create("Locate 140 4G", {"kind": "live", "port": 5999})
    run.add(login(), T0, "1.2.3.4:5")
    run.add(pvt(frame=1).replace(b",404,", b",4\x0204,"), T0 + 1, "1.2.3.4:5")
    run.add(b"GARBAGE", T0 + 2, "1.2.3.4:5", skipped=True)
    run.close()
    lines = (run.folder / "packets.log").read_text(encoding="ascii").splitlines()
    assert lines[0] == HEADER
    assert lines[1] == f"{T0:.3f}\t1.2.3.4:5\t" + login().decode().replace("\r\n", "\\r\\n")
    assert ",4\\x0204," in lines[2] and lines[2].endswith("\\r\\n")
    assert lines[3].endswith("\tGARBAGE\tS")
    again = Runs(tmp_path).get(run.id)                       # read back: the same packets, judged the same
    assert [r["raw"] for r in again.gate.rows] == [r["raw"] for r in run.gate.rows]
    assert [r["level"] for r in again.gate.rows] == [r["level"] for r in run.gate.rows]


@pytest.mark.parametrize("raw", [
    b"$LGN,DL1CAB1234*5A\r\n",                 # a sentence with its CR LF
    b"$PVT,1,2,3*",                            # a firmware that ends without checksum or CR LF
    b"$A\tB\\C\x02\x7f\r\n",                   # a tab, a backslash, a control byte, DEL
    b"0x41\r\n", b"abcd", b"\\x41",             # text that would read back as something else
    bytes.fromhex("78780d010356938035643809000a0d0a"),   # a binary packet: hex
    b"\r\n", b"\x00",
])
def test_every_packet_comes_back_exactly(raw):
    from runway.fwtest.runs import log_line, packet_text, read_line
    back, received, conn, skipped = read_line(log_line(raw, T0, "c", skipped=True).rstrip("\n"))
    assert (back, received, conn, skipped) == (raw, T0, "c", True)
    assert ("\t" not in packet_text(raw)) and ("\n" not in packet_text(raw))


def test_a_log_from_before_is_read_and_turned_to_text(tmp_path, monkeypatch):
    """1.1.28 wrote every packet in hex: such a log still reads, and is rewritten as text when Runway loads
    it — the same packets, checked before the file is replaced; a line it cannot read is kept as it is."""
    from runway.fwtest import runs as runs_mod
    run = Runs(tmp_path).create("Locate 140 4G", {"kind": "live", "port": 5999})
    old = [f"{T0:.3f}\tc\t{login().hex()}", "not a packet line",
           f"{T0 + 1:.3f}\tc\t{pvt(frame=1).hex()}", f"{T0 + 2:.3f}\tc\t{b'GARBAGE'.hex()}\tS"]
    p = run.folder / "packets.log"
    p.write_text("\n".join(old) + "\n", encoding="ascii")
    p.write_text(p.read_text(encoding="ascii") + runs_mod.log_line(pvt(frame=2, at=T0 + 10), T0 + 11, "c"),
                 encoding="ascii")                           # and a line the new code added
    monkeypatch.setattr(runs_mod.os, "replace", lambda *a: (_ for _ in ()).throw(PermissionError("busy")))
    busy = Runs(tmp_path).get(run.id)                        # another Runway holds it: left in hex, still read
    assert p.read_text(encoding="ascii").splitlines()[0] == old[0] and len(busy.gate.rows) == 4
    assert not (run.folder / "packets.log.tmp").exists()
    monkeypatch.undo()
    again = Runs(tmp_path).get(run.id)
    lines = p.read_text(encoding="ascii").splitlines()
    assert lines[0] == runs_mod.HEADER and lines[2] == "not a packet line"
    assert lines[1] == f"{T0:.3f}\tc\t" + login().decode().replace("\r\n", "\\r\\n")
    assert lines[4] == f"{T0 + 2:.3f}\tc\tGARBAGE\tS"
    assert [r["raw"] for r in again.gate.rows] == [r["raw"] for r in busy.gate.rows]
    assert [r["header"] for r in again.gate.rows] == ["LGN", "PVT", "?", "PVT"]
    assert runs_mod.to_text(p) == 0                          # once text, left alone


def test_a_file_keeps_its_unreadable_bytes(client):
    c, _, _ = client
    text = (login() + b"GARBAGE\r\n" + pvt(frame=1)).decode()
    v = c.post("/api/fwtest/runs/file", data={"spec": "Locate 140 4G"}, files={"file": ("b.txt", text, "text/plain")}).json()
    assert "not_packet" in [x["key"] for x in v["summary"]["checks"]]


async def test_a_wrong_imei_on_the_devices_connection_is_caught(tmp_path):
    from runway.core.models import UnifiedRecord
    run = Runs(tmp_path).create("Locate 140 4G", {"kind": "live", "port": 5999}, device=IMEI)
    sink = FirmwareTestSink(run)
    await sink.handle(UnifiedRecord(device_id=IMEI, protocol="ais140", raw_hex=login().hex(), peer="1.2.3.4:5"))
    await sink.handle(UnifiedRecord(device_id=OTHER, protocol="ais140", raw_hex=pvt(imei=OTHER).hex(), peer="1.2.3.4:5"))
    await sink.handle(UnifiedRecord(device_id=OTHER, protocol="ais140", raw_hex=pvt(imei=OTHER).hex(), peer="9.9.9.9:1"))
    rows = run.gate.rows
    assert len(rows) == 2 and "identity" in keys(rows[1])    # same connection: tested; another device: not


def test_a_packet_not_in_the_sheet_fails(gate):
    gate.feed(login(), T0, "c")
    assert gate.feed(sentence("XYZ,1,2"), T0, "c")["level"] == "fail"


def test_fields_agree_with_the_alert(gate):
    gate.feed(login(), T0, "c")
    bd = pvt(frame=1, ptype="BD", alert="03").replace(b",14.0,3.7,", b",14.0,3.7,")
    row = gate.feed(bd, T0, "c")                              # main power status 1 on a disconnect packet
    f = next(f for f in row["flags"] if f["key"] == "consistency")
    assert f["field"] == "main_power_status" and "main power 0" in f["text"]


def test_a_stale_live_packet_is_history_marked_live(gate):
    gate.feed(login(), T0, "c")
    row = gate.feed(pvt(frame=1, at=T0 - 3600), T0, "c")
    assert "live_stale" in keys(row) and row["level"] == "fail"


def _periodic(g, times, ign="1"):
    """Location packets made at ``times`` (seconds after T0), all live, received as made."""
    g.feed(login(), T0, "c")
    for i, t in enumerate(times):
        g.feed(pvt(frame=i + 1, at=T0 + t, ign=ign), T0 + t + 1, "c")
    g.summary()
    return {r["n"]: r for r in g.rows}


def test_packets_at_the_interval_set():
    g = Gate(load("Locate 140 4G"), config={"ign_on_s": 10})
    rows = _periodic(g, [0, 10, 21, 25, 55, 65])     # on time, on time (±1), early, about 2 missing, on time
    flags = {n: next((f for f in r["flags"] if f["key"] == "interval"), None) for n, r in rows.items()}
    assert flags[3] is None and flags[4] is None and flags[7] is None
    assert flags[5]["why"] == "early" and flags[5]["text"] == "4 s after #4, set to 10 s (ignition on): early"
    assert flags[6]["why"] == "missed" and flags[6]["missed"] == 2
    s = g.summary()
    assert next(c for c in s["checks"] if c["key"] == "interval")["packets"] == 2


def test_history_filling_a_gap_clears_it():
    """A gap in the live stream that history fills later is no gap: the timeline is judged again."""
    g = Gate(load("Locate 140 4G"), config={"ign_on_s": 10})
    rows = _periodic(g, [0, 10, 30])
    assert any(f["key"] == "interval" for f in rows[4]["flags"])
    g.feed(pvt(frame=3, status="H", alert="02", at=T0 + 20), T0 + 40, "c")
    g.summary()
    assert not any(f["key"] == "interval" for r in g.rows for f in r["flags"])


def test_asleep_after_ignition_off():
    g = Gate(load("Locate 140 4G"), config={"ign_off_s": 60, "sleep_after_min": 5, "sleep_s": 600})
    rows = _periodic(g, [0, 60, 120, 180, 240, 300, 360, 420], ign="0")   # never goes to sleep
    late = [r for r in rows.values() if any(f["key"] == "interval" for f in r["flags"])]
    assert late and all(f["state"] == "asleep" and f["why"] == "early"
                        for r in late for f in r["flags"] if f["key"] == "interval")


def test_the_format_set_decides_the_field_count():
    g = Gate(load("Locate 140 4G"), config={"formats": {"PVT": "51"}})
    g.feed(login(), T0, "c")
    row = g.feed(pvt(extra="29.1"), T0, "c")
    assert "field_count" not in keys(row)
    assert {f["label"]: f["value"] for f in g.detail(2)["fields"]}["Odometer"] == "29.1"
    short = g.feed(pvt(frame=2), T0, "c")                               # 50 fields on a device set to 51
    assert next(f for f in short["flags"] if f["key"] == "field_count")["text"] == "50 fields, expected 51"


def test_a_run_reconfigured_is_judged_again(tmp_path):
    run = Runs(tmp_path).create("Locate 140 4G", {"kind": "live", "port": 5999})
    run.add(login(), T0, "c")
    run.add(pvt(extra="29.1"), T0, "c")
    assert run.view()["summary"]["failed"] == 1
    run.reconfigure({"formats": {"PVT": "51"}})
    assert run.view()["summary"]["failed"] == 0
    assert Runs(tmp_path).get(run.id).meta["config"] == {"formats": {"PVT": "51"}}


def test_status_alert_type_and_ignition_agree(gate):
    gate.feed(login(), T0, "c")
    assert "status_alert" in keys(gate.feed(pvt(frame=1, alert="02", status="L"), T0, "c"))
    assert "alert_type" in keys(gate.feed(pvt(frame=2, ptype="IN", alert="08"), T0, "c"))
    assert "ignition_alert" in keys(gate.feed(pvt(frame=3, ptype="IF", alert="08", ign="1"), T0, "c"))


def test_tl_on_movees_event_id_is_a_warning(gate):
    """TL and MoveE are different events; TL uses MoveE's ID 22 until it gets its own — a warning.
    Any other ID on TL still fails; MoveE's own 22 passes, and so does MoveS on 21."""
    gate.feed(login(), T0, "c")
    tl = gate.feed(pvt(frame=1, ptype="TL", alert="22"), T0, "c")
    f = next(f for f in tl["flags"] if f["key"] == "alert_type")
    assert (f["level"], f["why"]) == ("warn", "interim") and "MoveE" in f["text"] and tl["level"] == "warn"
    assert next(f for f in gate.feed(pvt(frame=2, ptype="TL", alert="23"), T0, "c")["flags"]
                if f["key"] == "alert_type")["level"] == "fail"
    assert "alert_type" not in keys(gate.feed(pvt(frame=3, ptype="MoveE", alert="22"), T0, "c"))
    assert "alert_type" not in keys(gate.feed(pvt(frame=4, ptype="MoveS", alert="21"), T0, "c"))


def test_live_packets_late_or_from_the_future(gate):
    gate.feed(login(), T0, "c")
    late = gate.feed(pvt(frame=1, at=T0), T0 + 118, "c")
    assert next(f for f in late["flags"] if f["key"] == "live_delay")["got"] == "118 s"
    ahead = gate.feed(pvt(frame=2, at=T0 + 600), T0 + 120, "c")
    assert "future_time" in keys(ahead)


# ── a spec ─────────────────────────────────────────────────────────────────────────────────────────
def test_a_spec_naming_an_unknown_rule_is_refused():
    with pytest.raises(ValueError, match="no rule named shoe_size"):
        parse({"name": "X", "packets": {"PVT": {"field_list": [{"index": 1, "name": "a", "rules": {"shoe_size": 9}}]}}})


def test_the_packet_view_names_each_field(gate):
    gate.feed(login(), T0, "c")
    gate.feed(pvt(extra="29.1", date="171126"), T0, "c")
    d = gate.detail(2)
    by = {f["index"]: f for f in d["fields"]}
    assert (by[9]["label"], by[9]["level"]) == ("Date", "fail")
    assert (by[50]["label"], by[50]["value"]) == ("not in the sheet", "29.1")
    assert (by[51]["label"], by[51]["level"]) == ("OTA response", "pass")


# ── runs on disk, the live sink ────────────────────────────────────────────────────────────────────
def test_a_run_is_rebuilt_from_its_packets(tmp_path):
    runs = Runs(tmp_path)
    run = runs.create("Locate 140 4G", {"kind": "live", "port": 5999}, {"imei": IMEI})
    run.add(login(), T0, "c")
    run.add(pvt(checksum="00"), T0 + 1, "c")
    before = run.view()["summary"]
    run.close()
    again = Runs(tmp_path).get(run.id)
    after = again.view()["summary"]
    assert (after["packets"], after["failed"], after["checksum"]) == (before["packets"], before["failed"], before["checksum"])
    assert again.meta["expect"] == {"imei": IMEI}


def test_a_running_test_is_in_progress_until_it_stops(tmp_path):
    """The verdict is given when the test stops; while it runs, what the packets so far give is kept
    beside it — a failure seen early is not the test's result yet."""
    run = Runs(tmp_path).create("Locate 140 4G", {"kind": "live", "port": 5999})
    run.add(login(), T0, "c")
    run.add(pvt(checksum="00"), T0 + 1, "c")
    s = run.view()["summary"]
    assert (s["verdict"], s["outcome"], s["failed"]) == ("in_progress", "fail", 1)
    run.meta["state"] = "stopped"
    assert run.view()["summary"]["verdict"] == "fail"
    file_run = Runs(tmp_path).create("Locate 140 4G", {"kind": "file", "name": "x.txt"})
    file_run.add(login(), None, None)
    assert file_run.view()["summary"]["verdict"] == "incomplete"        # a file is judged at once


async def test_the_sink_takes_its_device_only(tmp_path):
    from runway.core.models import UnifiedRecord
    run = Runs(tmp_path).create("Locate 140 4G", {"kind": "live", "port": 5999})
    sink = FirmwareTestSink(run)
    await sink.handle(UnifiedRecord(device_id="ais140-unknown", protocol="ais140", raw_hex=b"$XX*00\r\n".hex()))
    await sink.handle(UnifiedRecord(device_id=IMEI, protocol="ais140", raw_hex=login().hex(), peer="1.2.3.4:5"))
    await sink.handle(UnifiedRecord(device_id=OTHER, protocol="ais140", raw_hex=login(imei=OTHER).hex()))
    assert run.meta["device"] == IMEI                 # the first device to identify itself
    assert run.gate.summary()["packets"] == 1


# ── the API ────────────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from runway.api import app as appmod
    from runway.api import fwtest as api
    monkeypatch.setattr(api, "runs", Runs(tmp_path / "fwtest"))
    api._attached.clear()
    with TestClient(appmod.app) as c:
        yield c, appmod, api


def test_a_file_is_judged_and_reported(client):
    c, _, _ = client
    text = (login() + pvt(frame=1) + pvt(frame=2, at=T0 + 10, checksum="00")).decode()
    r = c.post("/api/fwtest/runs/file", data={"spec": "Locate 140 4G"},
               files={"file": ("bench.txt", text.replace("\r\n", "\n"), "text/plain")})
    assert r.status_code == 200, r.text
    v = r.json()
    s = v["summary"]
    assert (s["packets"], s["failed"], s["verdict"]) == (3, 1, "fail")
    assert [c_["key"] for c_ in s["checks"]] == ["checksum"]    # a file's LF endings are not the device's
    rows = c.get(f"/api/fwtest/runs/{v['id']}/packets?level=fail").json()
    assert rows["total"] == 1 and rows["rows"][0]["n"] == 3
    assert c.get(f"/api/fwtest/runs/{v['id']}/packets/3").json()["fields"][0]["label"] == "Vendor ID"
    x = c.get(f"/api/fwtest/runs/{v['id']}/report.xlsx")
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(x.content))
    assert wb.sheetnames == ["Summary", "Checks", "Defects", "Scenarios", "Flags", "Packets"]
    defect = list(wb["Defects"].iter_rows(values_only=True))[1]
    assert defect[1] == "Critical" and defect[3] == "Checksum"
    head = [c.value for c in wb["Scenarios"][1]]
    assert head == ["Scenario", "Received", "Passed", "Failed", "First packet"]
    live = next(r for r in wb["Scenarios"].iter_rows(values_only=True) if r[0] == "Location (live)")
    assert live[1:4] == (2, 1, 1)
    assert c.delete(f"/api/fwtest/runs/{v['id']}").status_code == 200
    assert c.get("/api/fwtest/runs").json() == []


def test_the_timeline_gives_a_zoomed_window_every_packet(client):
    c, _, api = client
    run = api.runs.create("Locate 140 4G", {"kind": "live", "port": 5097})
    run.add(login(), T0, "a")
    for i in range(1, 120):                                    # a reconnection halfway
        run.add(pvt(frame=i, at=T0 + 10 * i), T0 + 10 * i + 1, "a" if i < 60 else "b")
    whole = c.get(f"/api/fwtest/runs/{run.id}/timeline?points=50").json()
    assert whole["thinned"] and whole["connections"] == 2
    assert len(whole["points"]) < 120
    a, b = T0 + 10 * 80, T0 + 10 * 90 + 1
    win = c.get(f"/api/fwtest/runs/{run.id}/timeline?points=50&start={a}&end={b}").json()
    assert not win["thinned"]
    assert [p[0] for p in win["points"]] == list(range(81, 92))   # packet n: the login is #1
    assert {p[5] for p in win["points"]} == {1}                  # connections numbered over the whole test
    assert win["connections"] == 2


def test_a_file_tests_timeline_stands_on_the_devices_time(client):
    """A capture has no arrival times — its timeline came out empty ("Not enough packets yet"). Its packets
    stand at the device's own time instead, in that order, history among them."""
    c, _, _ = client
    text = (login() + pvt(frame=1, at=T0 + 10) + pvt(frame=2, at=T0 + 30)
            + pvt(frame=3, at=T0 + 20, status="H") + pvt(frame=4, at=T0 + 40)).decode()
    v = c.post("/api/fwtest/runs/file", data={"spec": "Locate 140 4G"},
               files={"file": ("cap.txt", text, "text/plain")}).json()
    tl = c.get(f"/api/fwtest/runs/{v['id']}/timeline").json()
    assert tl["axis"] == "device"
    assert [p[1] for p in tl["points"]] == [T0 + 10, T0 + 20, T0 + 30, T0 + 40]
    assert [p[2] for p in tl["points"]] == ["L", "H", "L", "L"]
    win = c.get(f"/api/fwtest/runs/{v['id']}/timeline?start={T0 + 15}&end={T0 + 35}").json()
    assert [p[1] for p in win["points"]] == [T0 + 20, T0 + 30]


def test_the_tests_packets_on_the_map_and_the_live_list_live_only(client):
    """The rail's Map: every packet with a device time on the track, in device-time order, each row back to
    its packet number (the login has no time). The Live list asks for live only: every packet but history."""
    c, _, api = client
    run = api.runs.create("Locate 140 4G", {"kind": "live", "port": 5097})
    run.add(login(), T0, "a")
    run.add(pvt(frame=1, at=T0 + 10), T0 + 11, "a")
    run.add(pvt(frame=2, at=T0 + 20), T0 + 21, "a")
    run.add(pvt(frame=3, at=T0 + 15, status="H"), T0 + 22, "a")
    t = c.get(f"/api/fwtest/runs/{run.id}/track").json()
    assert t["rows"] == 3 and t["seqs"] == [2, 4, 3]
    assert [p[4] for seg in t["series"]["track"] for p in seg] == [False, True, False]   # the history one, dimmed
    assert c.get(f"/api/fwtest/runs/{run.id}/track").json() == t                        # cached until packets come
    run.add(pvt(frame=4, at=T0 + 30), T0 + 31, "a")
    assert c.get(f"/api/fwtest/runs/{run.id}/track").json()["seqs"] == [2, 4, 3, 5]
    live = c.get(f"/api/fwtest/runs/{run.id}/packets?status=live").json()
    assert live["total"] == 4 and [r["n"] for r in live["rows"]] == [5, 3, 2, 1]
    assert [r["n"] for r in c.get(f"/api/fwtest/runs/{run.id}/packets?status=history").json()["rows"]] == [4]
    assert c.get(f"/api/fwtest/runs/{run.id}/packets?status=other").status_code == 422


def test_a_report_holds_a_damaged_packets_bytes(client):
    """A control byte inside a field (a damaged packet) reached a check's example, and openpyxl refused
    it: the whole export answered an internal error. It is written as \\x02 instead."""
    from openpyxl import load_workbook

    from runway.xlsx_text import cell
    assert cell("CD\x06\x02") == "CD\\x06\\x02" and cell(5) == 5 and cell("a\tb\r\n") == "a\tb\r\n"
    c, _, api = client
    run = api.runs.create("Locate 140 4G", {"kind": "live", "port": 5097})
    run.add(login(), T0, "a")
    run.add(pvt(frame=1, at=T0 + 10).replace(b",404,", b",4\x0204,"), T0 + 11, "a")
    x = c.get(f"/api/fwtest/runs/{run.id}/report.xlsx")
    assert x.status_code == 200, x.text
    wb = load_workbook(io.BytesIO(x.content))
    assert any("4\\x0204" in str(v) for ws in wb for r in ws.iter_rows(values_only=True) for v in r)


def test_packets_download_for_the_developers(client):
    """All, failed or with warnings: each packet with what is wrong, each finding with its cause and fix,
    what each check means, and every field under its sheet name — a broken one coloured."""
    from openpyxl import load_workbook
    c, _, api = client
    run = api.runs.create("Locate 140 4G", {"kind": "live", "port": 5097})
    run.add(login(), T0, "a")
    run.add(pvt(frame=1, at=T0 + 10), T0 + 11, "a")
    run.add(pvt(frame=2, at=T0 + 20, checksum="00"), T0 + 21, "a")          # a failure
    run.add(pvt(frame=2, at=T0 + 20, checksum="00"), T0 + 22, "a")          # and the same packet again: a warning too
    books = {}
    for which in ("all", "fail", "warn"):
        x = c.get(f"/api/fwtest/runs/{run.id}/packets.xlsx?which={which}")
        assert x.status_code == 200, x.text
        assert "attachment" in x.headers["content-disposition"]
        books[which] = load_workbook(io.BytesIO(x.content))
    rows = lambda wb, name: list(wb[name].iter_rows(min_row=2, values_only=True))
    assert [r[0] for r in rows(books["all"], "Packets")] == [1, 2, 3, 4]
    assert [r[0] for r in rows(books["fail"], "Packets")] == [3, 4]
    assert [r[0] for r in rows(books["warn"], "Packets")] == [4]
    issue = next(r for r in rows(books["fail"], "Issues") if r[2] == "Checksum")
    assert issue[1] == "Fail" and issue[4] and issue[5] == "00" and issue[7] and issue[9]   # expected, sent, cause, fix
    explained = {r[0]: r for r in rows(books["fail"], "Checks explained")}
    assert explained["Checksum"][2] == 2 and explained["Checksum"][4]                       # packets, what the rule says
    pvt_sheet = books["all"]["PVT fields"]
    assert pvt_sheet.cell(1, 3).value == "1 Vendor ID"
    assert c.get(f"/api/fwtest/runs/{run.id}/packets.xlsx?which=other").status_code == 422


def test_a_file_with_nothing_readable_is_refused(client):
    c, _, _ = client
    r = c.post("/api/fwtest/runs/file", data={"spec": "Locate 140 4G"}, files={"file": ("x.txt", "hello", "text/plain")})
    assert r.status_code == 400


def test_a_live_test_rides_the_listener_on_its_port(client):
    """Attached as one more sink on the running listener — and let go when stopped."""
    c, appmod, api = client

    class Pipe:
        sinks = ["relay-sink"]

    appmod._jobs["job-t"] = {"kind": "listen", "status": "running", "port": 5998}
    appmod._pipelines["job-t"] = pipe = Pipe()
    try:
        v = c.post("/api/fwtest/runs", json={"spec": "Locate 140 4G", "port": 5998, "device": IMEI}).json()
        assert v["listening"] is True
        assert pipe.sinks[0] == "relay-sink" and isinstance(pipe.sinks[1], FirmwareTestSink)
        assert c.post(f"/api/fwtest/runs/{v['id']}/stop").json()["listening"] is False
        assert pipe.sinks == ["relay-sink"]
        assert c.post(f"/api/fwtest/runs/{v['id']}/resume").json()["listening"] is True
    finally:
        appmod._jobs.pop("job-t", None)
        appmod._pipelines.pop("job-t", None)
        api._attach_all()


def test_the_specs_are_listed(client):
    c, _, _ = client
    names = [s["name"] for s in c.get("/api/fwtest/specs").json()]
    assert "Locate 140 4G" in names


def test_the_shipped_spec_reads():
    s = load("Locate 140 4G")
    assert s.packets["PVT"].fields == 50 and s.packets["LGN"].fields == 8
    assert json.loads(json.dumps(s.scenarios))[0]["key"] == "login"


def test_hours_without_a_packet_are_a_silence_not_packets_missing():
    g = Gate(load("Locate 140 4G"), config={"ign_on_s": 10})
    _periodic(g, [0, 10, 10 + 6 * 3600])
    f = next(f for r in g.rows for f in r["flags"] if f["key"] == "interval")
    assert (f["why"], f["level"], f["missed"]) == ("silent", "warn", 0)
    assert f["text"] == "no location packet for 6 h after #3 (ignition on)"


def test_the_configuration_is_set_through_the_api(client):
    c, _, _ = client
    text = (login() + pvt(frame=1, extra="29.1")).decode()
    v = c.post("/api/fwtest/runs/file", data={"spec": "Locate 140 4G", "config": json.dumps({"history_order": "lifo"})},
               files={"file": ("bench.txt", text, "text/plain")}).json()
    assert v["summary"]["config"]["history_order"] == "lifo" and v["summary"]["failed"] == 1
    v = c.put(f"/api/fwtest/runs/{v['id']}/config", json={"config": {"formats": {"PVT": "51"}}}).json()
    assert v["summary"]["failed"] == 0 and v["summary"]["config"]["formats"] == {"PVT": "51"}
    spec = next(s for s in c.get("/api/fwtest/specs").json() if s["name"] == "Locate 140 4G")
    assert set(spec["formats"]["PVT"]) == {"50", "51"}
