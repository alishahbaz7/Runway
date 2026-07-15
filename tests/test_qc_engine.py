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
    assert "packet_cadence" not in {c["key"] for c in checks}   # dropped
    assert verdict == "fail"   # imei_present fails with no device_id


# ---- coverage + coverage-driven verdict (Phase A2) --------------------------- #
_CATALOG = [
    {"key": "login", "label": "Login", "match": {"message_type": "login"}, "required": True},
    {"key": "live", "label": "Live", "match": {"alert_id": 1}, "required": True},
    {"key": "history", "label": "History", "match": {"alert_id": 2}, "required": False},
]


def test_coverage_seen_count_and_last_sample():
    recs = [
        _rec(message_type="login", raw_hex="aa"),
        _rec(message_type="normal", io={"alert_id": 1}, raw_hex="bb"),
        _rec(message_type="normal", io={"alert_id": 1}, raw_hex="cc"),
    ]
    cov = {c["key"]: c for c in engine.coverage(recs, _CATALOG)}
    assert cov["login"]["seen"] is True and cov["login"]["count"] == 1
    assert cov["live"]["seen"] is True and cov["live"]["count"] == 2
    assert cov["live"]["last_raw_hex"] == "cc"          # most-recent matching packet
    assert cov["history"]["seen"] is False and cov["history"]["count"] == 0
    assert cov["history"]["valid"] is None              # validator (Step 2) hasn't run


def test_overall_verdict_states():
    cov_full = engine.coverage(
        [_rec(message_type="login"), _rec(message_type="normal", io={"alert_id": 1})], _CATALOG)
    cov_partial = engine.coverage([_rec(message_type="login")], _CATALOG)

    # no packets -> no verdict yet
    assert engine.overall_verdict([], {"packet_count": 0}) == ""
    # required still missing, device recently seen -> in_progress
    assert engine.overall_verdict(cov_partial,
        {"packet_count": 1, "now": 1000.0, "last_packet_epoch": 999.0, "stale_timeout_s": 600}) == "in_progress"
    # required missing + silent past the timeout -> timeout (resumable)
    assert engine.overall_verdict(cov_partial,
        {"packet_count": 1, "now": 2000.0, "last_packet_epoch": 1000.0, "stale_timeout_s": 600}) == "timeout"
    # all required seen -> pass
    assert engine.overall_verdict(cov_full,
        {"packet_count": 2, "now": 1000.0, "last_packet_epoch": 999.0}) == "pass"
    # a required item skipped (but the rest satisfied) -> conditional pass
    assert engine.overall_verdict(cov_partial,
        {"packet_count": 1, "skipped": ["live"], "now": 1000.0, "last_packet_epoch": 999.0}) == "conditional_pass"
    # operator's finalized verdict overrides the computed state
    assert engine.overall_verdict(cov_partial, {"packet_count": 1, "finalized_verdict": "fail"}) == "fail"
