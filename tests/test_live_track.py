"""The Dashboard's map is Log analysis's map on live data: a device's records, as the AIS-140 codec
makes them, read as a drive log and analysed by the one ``analyse`` — trips, gaps, live and history.
Packets come from the simulated Locate 140 4G (tests/fwsim.py), a made-up IMEI."""

from __future__ import annotations

from fwsim import Device, day

from runway.analysis.live import log_of, track
from runway.core.models import Session
from runway.core.registry import get_codec


def records(dev=None) -> list[dict]:
    """The simulated day through the real codec, as the live store keeps them (dicts with a seq)."""
    codec, sess, out, seq = get_codec("ais140"), Session(id="t"), [], 0
    for raw, received, _conn, skipped in day(dev or Device()):
        if skipped:
            continue
        res = codec.decode(raw, sess)
        for rec in res.records:
            seq += 1
            d = rec.model_dump(mode="json")
            d["seq"] = seq
            out.append(d)
    return out


def test_live_records_read_as_a_drive_log():
    recs = records()
    log, seqs = log_of(recs, "dev")
    assert len(log.rows) > 100 and len(seqs) == len(log.rows)
    ts = [r["t"] for r in log.rows]
    assert ts == sorted(ts) and [r["k"] for r in log.rows] == list(range(len(log.rows)))
    assert any(r["history"] for r in log.rows) and any(r["history"] is False for r in log.rows)
    assert {r["ign"] for r in log.rows} >= {0, 1}
    assert any((r.get("od") or {}).get("PacketType") == "IN" for r in log.rows)
    assert len(log.raw) == len(log.rows) and all(len(x) == len(log.columns) for x in log.raw)


def test_the_map_gets_trips_track_and_the_record_behind_each_point():
    recs = records()
    t = track("dev", recs)
    s, d = t["series"], t["driving"]
    assert s["track"] and all(len(p) == 5 for seg in s["track"] for p in seg)        # lat, lon, speed, k, history
    assert d["trips"] and d["trips"][0]["n"] == 1
    by_seq = {r["seq"]: r for r in recs}
    k = s["track"][0][0][3]
    rec = by_seq[t["seqs"][k]]                                                        # a point → its record
    assert abs(rec["position"]["latitude"] - s["track"][0][0][0]) < 1e-5
    assert t["settings"]["overspeed_kmh"]


def test_no_records_no_track():
    assert track("dev", []) == {"device": "dev", "rows": 0, "series": {}, "driving": {}, "settings": {}, "seqs": []}


def test_the_live_track_endpoint(monkeypatch):
    """The live store's records of each device asked for, analysed — and not again until it sends more."""
    from fastapi.testclient import TestClient

    from runway.analysis import live as lv
    from runway.api import app as appmod
    from runway.storage import live as live_mod

    store = live_mod.LiveStore()
    monkeypatch.setattr(live_mod, "store", store)
    recs = records()
    for d in recs:
        d = dict(d, device_id="356938035643809")
        store.packets.append(d)
        store.by_device["356938035643809"].append(d)
    calls = []
    real = lv.track
    monkeypatch.setattr(lv, "track", lambda *a, **k: calls.append(a[0]) or real(*a, **k))
    with TestClient(appmod.app) as c:
        r = c.get("/api/live/track?device=356938035643809&device=nobody").json()
        again = c.get("/api/live/track?device=356938035643809").json()
    one, none = r["devices"]
    assert one["device"] == "356938035643809" and one["series"]["track"] and one["driving"]["trips"]
    assert none == {"device": "nobody", "rows": 0, "series": {}, "driving": {}, "settings": {}, "seqs": []}
    assert again["devices"][0]["seqs"] == one["seqs"] and calls.count("356938035643809") == 1


def test_packets_at_one_time_are_marked_where_they_pile_up():
    """The same moment in several packets — a duplicate made again under a new frame number, or a clock
    that did not move — is listed once with how many, where and which frame numbers; a correct day has none."""
    assert track("dev", records())["series"]["dups"] == []
    t = track("dev", records(Device(faults={"time_repeat", "stuck_clock"})))
    dups = t["series"]["dups"]
    assert len(dups) == 2 and all(d["count"] == 2 and d["at"] and d["frames"] for d in dups)
    same = {d["same"]: d for d in dups}
    assert same[True]["history"] == 2                           # the duplicate: the same data, both history
    assert same[False]["history"] == 0                          # the stuck clock: other data at one time, live
