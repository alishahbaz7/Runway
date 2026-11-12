"""Telemetry store: packets + IMU samples kept for internal use, read back for the IMU page.
Fed from the real 15-minute export of sCAN 865577071515754 (538 messages, 448 IMU)."""

from pathlib import Path

import pytest

from runway.convert.log_export import unwrap
from runway.core.models import Session
from runway.core.registry import get_codec, load_plugins
from runway.storage.telemetry import TelemetryStore

FIX = Path(__file__).parent / "fixtures"
DEV = "865577071515754"


def _records(stream: bytes):
    load_plugins()
    c, sess, out = get_codec("zenithra"), Session(id="t"), []
    while stream:
        o = c.decode(stream, sess)
        out += o.records
        stream = stream[o.consumed:]
    return out


@pytest.fixture
def store(tmp_path):
    s = TelemetryStore(str(tmp_path / "telemetry.db"))
    yield s
    s.close()


def test_store_keeps_packets_and_one_row_per_imu_sample(store):
    recs = _records(unwrap((FIX / "zenithra_log_export.csv").read_text(encoding="ascii")))
    for r in recs:
        store.write(r)
    (dev,) = store.devices()
    assert (dev["device_id"], dev["packets"], dev["imu_samples"]) == (DEV, 3, 40)
    raw = store._db().execute("SELECT raw FROM packets ORDER BY frame_number").fetchone()[0]
    assert raw.startswith(b"$MMI,V2.2.0,5.1.30I,IMU,200,")       # the bytes as received
    amag = store._db().execute("SELECT amag FROM imu_samples LIMIT 1").fetchone()[0]
    assert amag == pytest.approx(9.8, abs=0.1)                     # |a| at rest ~ g


def test_one_packet_is_spread_over_the_configured_interval_for_its_ignition(store):
    """The device spreads a packet's 20 samples over the packet interval (gap = interval / 20):
    ignition ON defaults to 10 s -> 500 ms apart. One packet can't be measured, so the
    configured IMUPKT value applies."""
    imu = _records((FIX / "zenithra_imu_sample.log").read_bytes())[0]
    assert imu.ignition is True
    store.write(imu)
    pt = imu.event_time.timestamp()
    last = store.imu_series(DEV, pt - 20, pt + 20)
    assert last["mode"] == "samples" and last["samples"] == 20
    assert last["t"][-1] == pytest.approx(pt) and last["t"][0] == pytest.approx(pt - 9.5)
    assert last["timing"]["on"] == {"interval": 10.0, "gap": 0.5, "source": "setting", "packets": 0}
    first = store.imu_series(DEV, pt - 20, pt + 20, timing={"anchor": "first", "ign_on_s": 4})
    assert first["t"][0] == pytest.approx(pt) and first["t"][-1] == pytest.approx(pt + 3.8)
    assert last["series"]["az"]["mean"][0] == 9.82 and last["series"]["yaw"]["mean"][-1] == 0.69
    assert last["peak"]["amag"] == pytest.approx(max(last["series"]["amag"]["mean"]))
    assert last["extremes"]["yaw_abs"] == pytest.approx(0.79)


def test_measured_packet_spacing_wins_and_windows_join_back_to_back(store):
    """This morning's export: ignition ON but a packet every 2 s (a different IMUPKT config) —
    the spacing is measured, so samples land 100 ms apart with no hole between packets."""
    from datetime import timedelta

    imu = _records((FIX / "zenithra_imu_sample.log").read_bytes())[0]
    for i in range(6):
        r = imu.model_copy(deep=True)
        r.event_time = imu.event_time + timedelta(seconds=2 * i)
        store.write(r)
    off = imu.model_copy(deep=True)
    off.ignition, off.event_time = False, imu.event_time + timedelta(seconds=60)
    store.write(off)
    pt = imu.event_time.timestamp()
    s = store.imu_series(DEV, pt - 3, pt + 11)
    assert s["timing"]["on"]["source"] == "measured" and s["timing"]["on"]["gap"] == pytest.approx(0.1)
    steps = {round(b - a, 3) for a, b in zip(s["t"], s["t"][1:])}
    assert steps == {0.1}                              # one even 10 Hz line across all packets
    o = store.imu_series(DEV, pt + 55, pt + 61)
    assert o["timing"]["off"] == {"interval": 2.0, "gap": 0.1, "source": "setting", "packets": 0}
    assert o["t"][0] == pytest.approx(pt + 60 - 1.9)


def test_a_long_range_is_thinned_to_buckets_with_history_and_normal_packets_marked(store):
    from datetime import timedelta

    imu = _records((FIX / "zenithra_imu_sample.log").read_bytes())[0]
    nr = _records((FIX / "zenithra_sample.log").read_bytes())[1]
    for i in range(200):                               # 400 s of IMU at one packet per 2 s
        r = imu.model_copy(deep=True)
        r.event_time = imu.event_time + timedelta(seconds=2 * i)
        r.io["frame_number"] = f"{1000 + 2 * i:06d}"
        r.io["packet_status"] = "H" if i == 50 else "L"
        store.write(r)
        if i % 5 == 0:                                 # an NR every 10 s, on the same counter
            n = nr.model_copy(deep=True)
            n.device_id, n.event_time = DEV, r.event_time
            n.io["frame_number"] = f"{1001 + 2 * i:06d}"
            store.write(n)
    t0 = imu.event_time.timestamp()
    s = store.imu_series(DEV, t0 - 5, t0 + 405, points=100)
    assert s["mode"] == "buckets" and s["samples"] == 4000 and len(s["t"]) <= 101
    assert all(lo <= m <= hi for lo, m, hi in zip(
        s["series"]["az"]["min"], s["series"]["az"]["mean"], s["series"]["az"]["max"]))
    assert (s["packets"], s["imu_packets"], s["history_packets"]) == (240, 200, 1)
    assert len([e for e in s["events"] if e["kind"] == "normal"]) == 40
    assert [e["history"] for e in s["events"] if e["kind"] == "imu"] == [True]


def test_gaps_in_the_frame_counter_are_reported(store):
    imu = _records((FIX / "zenithra_imu_sample.log").read_bytes())[0]
    for frame, dt in ((100, 0), (101, 2), (105, 10)):              # 102-104 never arrived
        r = imu.model_copy(deep=True)
        r.io["frame_number"] = f"{frame:06d}"
        r.event_time = imu.event_time.replace(second=(imu.event_time.second + dt) % 60)
        store.write(r)
    pt = imu.event_time.timestamp()
    s = store.imu_series(DEV, pt - 60, pt + 60)
    assert s["missing_frames"] == 3 and s["gaps"][0]["frames"] == 3


def test_each_point_carries_its_own_spacing_so_a_hole_shows(store):
    """Live device, ignition toggled at 16:41:26: the next IMU packet came 3 s after the last
    (not 2) and carried 18 samples. Each sample's spacing g = its packet's interval / its count;
    the page breaks the line where the next sample is > 2.5 g away — exactly at that hole."""
    from datetime import timedelta

    imu = _records((FIX / "zenithra_imu_sample.log").read_bytes())[0]
    for i in range(5):                                   # ignition OFF: every 2 s
        r = imu.model_copy(deep=True)
        r.ignition, r.event_time = False, imu.event_time + timedelta(seconds=2 * i)
        store.write(r)
    short = imu.model_copy(deep=True)
    short.ignition, short.event_time = False, imu.event_time + timedelta(seconds=11)
    short.io["imu"] = short.io["imu"][:18]
    store.write(short)
    pt = imu.event_time.timestamp()
    s = store.imu_series(DEV, pt - 3, pt + 12)
    t, g = s["t"], s["g"]
    assert len(t) == 5 * 20 + 18 and set(round(x, 4) for x in g[:100]) == {0.1}
    assert g[-1] == pytest.approx(2 / 18)
    holes = [i for i in range(1, len(t)) if t[i] - t[i - 1] > 2.5 * max(g[i], g[i - 1])]
    assert holes == [100] and t[100] - t[99] == pytest.approx(11 - 8 - 17 * 2 / 18)


# -- 24x7: summaries, long ranges, retention ---------------------------------------------------- #
def _imu_every(store, seconds: int, step: int, start=None):
    """IMU packets every ``step`` s for ``seconds`` s, from the fixture packet's time."""
    from datetime import timedelta

    imu = _records((FIX / "zenithra_imu_sample.log").read_bytes())[0]
    t0 = start or imu.event_time
    for i in range(seconds // step):
        r = imu.model_copy(deep=True)
        r.event_time = t0 + timedelta(seconds=step * i)
        store.write(r)
    return imu, t0.timestamp()


def test_device_list_and_minute_summaries_are_kept_as_packets_arrive(store):
    imu, t0 = _imu_every(store, 600, 10)                       # 10 min, a packet every 10 s
    (dev,) = store.devices()
    assert (dev["packets"], dev["imu_samples"]) == (60, 1200)
    assert dev["first"] == pytest.approx(t0) and dev["last"] == pytest.approx(t0 + 590)
    rows = store._db().execute("SELECT SUM(n), SUM(pk), MIN(az_min), MAX(az_max) FROM imu_minute").fetchone()
    raw = store._db().execute("SELECT COUNT(*), MIN(az), MAX(az) FROM imu_samples").fetchone()
    assert rows == (1200, 60, raw[1], raw[2])


def test_a_range_of_a_minute_a_point_or_more_reads_the_minute_summaries(store):
    imu, t0 = _imu_every(store, 3600, 10)                      # an hour, a packet every 10 s
    s = store.imu_series(DEV, t0 - 60, t0 + 3600 + 60, points=50)     # ~74 s a point
    assert s["mode"] == "minutes" and s["samples"] == 7200 and s["imu_packets"] == 360
    assert s["w"] == pytest.approx(3720 / 50) and len(s["t"]) <= 51
    assert s["missing_frames"] is None and s["events"] == []
    raw = store.imu_series(DEV, t0 - 60, t0 + 3660, points=5000)      # the samples themselves
    assert raw["mode"] == "buckets"
    for axis in ("az", "amag", "yaw"):
        assert min(s["series"][axis]["min"]) == pytest.approx(min(raw["series"][axis]["min"]))
        assert max(s["series"][axis]["max"]) == pytest.approx(max(raw["series"][axis]["max"]))
    assert s["peak"]["amag"] == pytest.approx(raw["peak"]["amag"])
    assert s["extremes"] == pytest.approx(raw["extremes"])


def test_purge_drops_old_samples_keeps_their_minutes_and_the_device(store):
    from datetime import datetime, timedelta, timezone

    old = datetime.now(timezone.utc) - timedelta(days=10)
    _imu_every(store, 300, 10, start=old)
    imu, new_t0 = _imu_every(store, 300, 10, start=datetime.now(timezone.utc) - timedelta(hours=1))
    gone = store.purge(keep_days=7, minute_keep_days=365, batch=7)     # small batches: the loop
    assert gone == {"imu_samples": 600, "packets": 30, "imu_minute": 0}
    assert store._db().execute("SELECT COUNT(*) FROM imu_samples").fetchone()[0] == 600
    (dev,) = store.devices()
    assert dev["packets"] == 60                                     # lifetime count stays
    s = store.imu_series(DEV, old.timestamp() - 30, old.timestamp() + 330)
    assert s["mode"] == "minutes" and s["samples"] == 600           # old day: minute detail
    assert store.purge(keep_days=7, minute_keep_days=7)["imu_minute"] >= 5
    assert store.imu_series(DEV, old.timestamp() - 30, old.timestamp() + 330)["samples"] == 0


def test_a_database_from_before_the_summaries_gets_them_on_open(tmp_path):
    path = str(tmp_path / "telemetry.db")
    s = TelemetryStore(path)
    _imu_every(s, 120, 10)
    db = s._db()
    db.execute("DELETE FROM device_summary")
    db.execute("DROP TABLE imu_minute")
    db.commit()
    s.close()
    s = TelemetryStore(path)                                        # as an older version left it
    (dev,) = s.devices()
    assert (dev["packets"], dev["imu_samples"]) == (12, 240)
    assert s._db().execute("SELECT SUM(n), SUM(pk) FROM imu_minute").fetchone() == (240, 12)
    s.close()


# -- speed alongside the samples ---------------------------------------------------------------- #
def test_speed_comes_per_packet_then_per_bucket_then_per_minute(store):
    from datetime import timedelta

    imu = _records((FIX / "zenithra_imu_sample.log").read_bytes())[0]
    for i in range(360):                                   # 12 min, a packet every 2 s, 0-59 km/h
        r = imu.model_copy(deep=True)
        r.event_time, r.speed_kph = imu.event_time + timedelta(seconds=2 * i), float(i % 60)
        store.write(r)
    t0 = imu.event_time.timestamp()
    fine = store.imu_series(DEV, t0 - 1, t0 + 61, points=50)          # 31 packets fit: as reported
    assert fine["speed"]["t"][:3] == pytest.approx([t0, t0 + 2, t0 + 4])
    assert fine["speed"]["mean"][:3] == [0.0, 1.0, 2.0] and fine["speed"]["w"] is None
    wide = store.imu_series(DEV, t0 - 1, t0 + 721, points=50)         # 360 packets: buckets
    sp = wide["speed"]
    assert sp["w"] == pytest.approx(722 / 50) and max(sp["max"]) == 59.0 and min(sp["min"]) == 0.0
    assert wide["mode"] == "buckets" and set(sp["t"]) <= set(wide["t"])  # the samples' own buckets
    long = store.imu_series(DEV, t0 - 3600, t0 + 3600 * 23, points=50)   # minute summaries
    assert long["mode"] == "minutes" and max(long["speed"]["max"]) == 59.0
    assert long["speed"]["mean"][0] == pytest.approx(sum(range(30)) / 30, abs=15)


def test_a_v1_1_25_database_gets_speed_in_its_minute_summaries(tmp_path):
    from datetime import timedelta

    path = str(tmp_path / "telemetry.db")
    s = TelemetryStore(path)
    imu = _records((FIX / "zenithra_imu_sample.log").read_bytes())[0]
    for i in range(30):
        r = imu.model_copy(deep=True)
        r.event_time, r.speed_kph = imu.event_time + timedelta(seconds=2 * i), 40.0 + i
        s.write(r)
    db = s._db()
    db.execute("DROP TABLE imu_minute")                    # rebuild it as v1.1.25 made it
    db.execute("CREATE TABLE imu_minute (device_id TEXT NOT NULL, minute INTEGER NOT NULL, "
               "n INTEGER NOT NULL, pk INTEGER NOT NULL, "
               + ", ".join(f"{a}_min REAL, {a}_max REAL, {a}_sum REAL" for a in
                           ("ax", "ay", "az", "pitch", "roll", "yaw", "amag"))
               + ", PRIMARY KEY (device_id, minute)) WITHOUT ROWID")
    db.commit()
    s.close()
    s = TelemetryStore(path)                               # opens: summaries rebuilt, speed added
    row = s._db().execute("SELECT SUM(spd_n), MIN(spd_min), MAX(spd_max), SUM(spd_sum) FROM imu_minute").fetchone()
    assert row == (30, 40.0, 69.0, sum(40.0 + i for i in range(30)))
    s.close()
