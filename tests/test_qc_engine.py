"""QC pass/fail checks (proinfo/qc/engine.py) — pure logic, no sockets/DB needed.

Each check's pass/fail/na branch gets a direct test; the docstring on each mirrors the
check's meaning from docs/QC_MODULE.md so a failing assertion is self-explanatory.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from proinfo.core.models import Position, UnifiedRecord
from proinfo.qc import engine


def _rec(**kw) -> UnifiedRecord:
    kw.setdefault("device_id", "123456789012345")
    kw.setdefault("protocol", "teltonika")
    return UnifiedRecord(**kw)


def test_imei_present_pass_and_fail():
    assert engine._check_imei_present([], {"device_id": "123456789012345"}, {})[0] == "pass"
    assert engine._check_imei_present([], {"device_id": ""}, {})[0] == "fail"
    assert engine._check_imei_present([], {"device_id": "gt06-unknown"}, {})[0] == "fail"


def test_location_fix_pass_within_window_fail_outside_and_when_absent():
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    cfg = {**engine.DEFAULT_CONFIG, "location_window_s": 60}
    meta = {"started_at": t0.timestamp()}

    good = _rec(event_time=t0 + timedelta(seconds=10),
                position=Position(latitude=22.5, longitude=88.3))
    assert engine._check_location_fix([good], meta, cfg)[0] == "pass"

    late = _rec(event_time=t0 + timedelta(seconds=90),
                position=Position(latitude=22.5, longitude=88.3))
    assert engine._check_location_fix([late], meta, cfg)[0] == "fail"

    no_fix = _rec(event_time=t0 + timedelta(seconds=10))
    assert engine._check_location_fix([no_fix], meta, cfg)[0] == "fail"

    zero_zero = _rec(event_time=t0 + timedelta(seconds=10), position=Position(latitude=0, longitude=0))
    assert engine._check_location_fix([zero_zero], meta, cfg)[0] == "fail"   # Position.is_valid excludes 0,0


def test_gps_valid_flag_na_when_never_reported():
    cfg = engine.DEFAULT_CONFIG
    records = [_rec(), _rec()]
    assert engine._check_gps_valid_flag(records, {}, cfg)[0] == "na"


def test_gps_valid_flag_pass_and_fail():
    cfg = engine.DEFAULT_CONFIG
    assert engine._check_gps_valid_flag([_rec(gps_valid=False), _rec(gps_valid=True)], {}, cfg)[0] == "pass"
    assert engine._check_gps_valid_flag([_rec(gps_valid=False), _rec(gps_valid=False)], {}, cfg)[0] == "fail"


def test_packet_cadence_na_with_fewer_than_two_and_pass_fail_by_gap():
    cfg = {**engine.DEFAULT_CONFIG, "cadence_max_s": 30}
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

    assert engine._check_packet_cadence([_rec(event_time=t0)], {}, cfg)[0] == "na"

    tight = [_rec(event_time=t0), _rec(event_time=t0 + timedelta(seconds=10))]
    assert engine._check_packet_cadence(tight, {}, cfg)[0] == "pass"

    gappy = [_rec(event_time=t0), _rec(event_time=t0 + timedelta(seconds=45))]
    assert engine._check_packet_cadence(gappy, {}, cfg)[0] == "fail"


def test_external_voltage_na_without_config_and_without_field():
    cfg = engine.DEFAULT_CONFIG   # voltage_min/max unset by default
    assert engine._check_external_voltage([_rec(external_v=12.0)], {}, cfg)[0] == "na"

    cfg2 = {**engine.DEFAULT_CONFIG, "voltage_min": 11.0, "voltage_max": 15.0}
    assert engine._check_external_voltage([_rec()], {}, cfg2)[0] == "na"   # field never present


def test_external_voltage_pass_and_fail_when_configured():
    cfg = {**engine.DEFAULT_CONFIG, "voltage_min": 11.0, "voltage_max": 15.0}
    assert engine._check_external_voltage([_rec(external_v=12.6)], {}, cfg)[0] == "pass"
    assert engine._check_external_voltage([_rec(external_v=9.0)], {}, cfg)[0] == "fail"


def test_verdict_fail_beats_pass_and_na_only_yields_no_verdict():
    assert engine._verdict([{"result": "pass"}, {"result": "fail"}, {"result": "na"}]) == "fail"
    assert engine._verdict([{"result": "pass"}, {"result": "na"}]) == "pass"
    assert engine._verdict([{"result": "na"}, {"result": "na"}]) == ""


def test_evaluate_returns_all_default_checks_in_order():
    checks, verdict = engine.evaluate([], {"device_id": ""})
    assert [c["key"] for c in checks] == [k for k, _, _ in engine.DEFAULT_CHECKS]
    assert verdict == "fail"   # imei_present fails with no device_id
