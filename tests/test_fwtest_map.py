"""Firmware test's Map tab and its Live / All switch: the test's packets on Log analysis's map, read as a
drive log and analysed by the one ``analyse`` (as the Dashboard's map is) — every packet with a device time
on it, each point back to its packet. Packets from the simulated Locate 140 4G (tests/fwsim.py), a made-up
IMEI."""

from __future__ import annotations

import time

from fwsim import CONFIG, Device, day

from runway.fwtest import maptrack
from runway.fwtest.gate import Gate
from runway.fwtest.spec import load

SPEC = load("Locate 140 4G")


def day_gate() -> Gate:
    g = Gate(SPEC, config=CONFIG)
    for raw, received, conn, skipped in day(Device()):
        g.feed(raw, received, conn, skipped=skipped)
    return g


def test_a_days_test_on_the_map_every_packet_back_to_its_own():
    g = day_gate()
    started = time.perf_counter()
    t = maptrack.track(g.rows, "dev")
    took = time.perf_counter() - started
    timed = [r for r in g.rows if r["t"] is not None]
    assert t["rows"] == len(timed) and sorted(t["seqs"]) == sorted(r["n"] for r in timed)   # every packet, none left out
    s, d = t["series"], t["driving"]
    assert s["track"] and d["trips"] and d["trips"][0]["n"] == 1
    by_n = {r["n"]: r for r in g.rows}
    pts = [p for seg in s["track"] for p in seg]                                           # lat, lon, speed, k, history
    for lat, lon, _speed, k, hist in pts:
        r = by_n[t["seqs"][k]]                                                             # a point → its packet
        assert abs(r["lat"] - lat) < 1e-5 and abs(r["lon"] - lon) < 1e-5
        assert bool(hist) == (r["status"] in ("H", "SP"))
    assert any(p[4] for p in pts) and not all(p[4] for p in pts)                           # history kept as history
    for st, late in (("SP", True), ("NM", False)):                                          # an emergency packet's own
        n = next(r["n"] for r in g.rows if r["status"] == st)
        assert next(bool(p[4]) for p in pts if t["seqs"][p[3]] == n) is late
    assert took < 5, f"a day's map took {took:.1f} s"


def test_records_carry_what_the_map_reads():
    g = day_gate()
    recs = maptrack.records(g.rows)
    first = next(r for r in g.rows if r["t"] is not None and r["status"] == "L")
    rec = next(x for x in recs if x["seq"] == first["n"])
    assert rec["io"]["packet_status"] == "L" and rec["io"]["frame_number"] == first["frame"]
    assert rec["ignition"] is (first["ign"] == "1") and rec["position"]["latitude"] == first["lat"]
    assert rec["event_time"].endswith("+00:00") and rec["received_at"]
    assert all(r["t"] is not None for r in g.rows if r["n"] in {x["seq"] for x in recs})


def test_an_emergency_packet_has_its_device_time():
    """The EPB carries its date and time as one field (DDMMYYYYhhmmss): read, so an SOS stands on the map and
    the timeline. Sent normal (NM) and again stored (SP) at one moment is a resend — a warning, as live and
    history are — not a duplicate."""
    from fwsim import Device
    dev = Device()
    dev.conn = "a"
    at = 1_795_000_000
    dev.epb(at, "EMR", "NM")
    dev.epb(at, "EMR", "SP")
    g = Gate(SPEC, config=CONFIG)
    for raw, received, conn, skipped in dev.events:
        g.feed(raw, received + 600, conn, skipped=skipped)
    nm, sp = g.rows
    assert nm["t"] == sp["t"] == at
    tr = [f for f in sp["flags"] if f["key"] == "time_repeat"]
    assert len(tr) == 1 and tr[0]["level"] == "warn" and tr[0]["why"] == "resent"


def test_no_packet_no_track():
    assert maptrack.track([], "dev")["rows"] == 0


def test_the_live_list_shows_all_or_live_only():
    """The Live packets pane: every packet, or the live ones only — every packet but history (a login was
    sent as it happened). Asked of the gate, so a history flood never pushes the live ones out of the newest."""
    g = day_gate()
    every = g.packets(limit=100000)
    live = g.packets(status="live", limit=100000)
    hist = g.packets(status="history", limit=100000)
    assert hist["total"] and live["total"] + hist["total"] == every["total"]
    assert {r["status"] for r in hist["rows"]} == {"H", "SP"}                               # a stored emergency packet too
    assert {r["status"] for r in live["rows"]} == {"L", "NM", None}                         # its normal one is live
    assert g.summary()["late"] == hist["total"]                                             # the switch's count
    assert any(r["header"] == "LGN" for r in live["rows"])                                  # the login is live
    ns = [r["n"] for r in live["rows"]]
    assert ns == sorted(ns, reverse=True)                                                   # newest first
