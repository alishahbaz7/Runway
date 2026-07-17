"""CoverageLedger (proinfo/qc/ledger.py): per-device QC state must ACCUMULATE — the
in-memory record list is a bounded window, and recomputing coverage over it meant a long
session forgot its own history (a login seen at minute 0 "unhappened" once 5000 later
packets pushed it out, flipping a passing device back to IN PROGRESS). Also covers field
validation (Step 2): an implausible packet marks its item INVALID with a reason instead
of a silent OK, and the verdict settles as Conditional Pass, never a clean PASS.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import proinfo.qc.session as qc_session
from proinfo.core.models import Position, UnifiedRecord
from proinfo.qc import engine
from proinfo.qc.ledger import CoverageLedger
from proinfo.qc.session import QcCaptureSink

IMEI = "861329080405658"

CATALOG = [
    {"key": "login", "label": "Login", "group": "packet", "code": "LGN",
     "match": {"message_type": "login"}, "required": True},
    {"key": "live", "label": "Location Update (live)", "group": "packet", "code": "NR",
     "match": {"alert_id": 1}, "required": True},
]


def _rec(message_type="normal", alert_id=1, lat=28.55, lon=77.26, gps_valid=True,
         event_offset_s=0):
    now = datetime.now(timezone.utc)
    return UnifiedRecord(
        device_id=IMEI, protocol="ais140", message_type=message_type,
        raw_hex=b"$PVT,test*".hex(), peer="1.2.3.4:99",
        received_at=now, event_time=now + timedelta(seconds=event_offset_s),
        position=Position(latitude=lat, longitude=lon), gps_valid=gps_valid,
        io={"alert_id": alert_id, "firmware_version": "2.2.5", "vendor_id": "LOCT14A"},
    )


async def test_coverage_survives_the_record_window(tmp_path, monkeypatch):
    """A login observed once must STAY seen after thousands of later packets push it out
    of the bounded records window — the exact bug the ledger exists to prevent."""
    monkeypatch.setattr(qc_session, "MAX_CHECK_RECORDS", 5)   # tiny window for the test
    sink = QcCaptureSink(str(tmp_path / "cap.txt"), catalog_loader=lambda proto: CATALOG)
    await sink.handle(_rec(message_type="login", alert_id=None))
    for _ in range(20):                                       # blows well past the window
        await sink.handle(_rec())
    assert len(sink.records) == 5, "the window itself must stay bounded"
    cov = {c["key"]: c for c in sink.ledger.coverage(IMEI, CATALOG)}
    assert cov["login"]["seen"] and cov["login"]["count"] == 1, \
        "login fell out of the window but must remain SEEN in the ledger"
    assert cov["live"]["count"] == 20
    vit = sink.ledger.vitals(IMEI)
    assert vit["packet_count"] == 21 and vit["firmware_version"] == "2.2.5"


def test_invalid_packet_marks_item_invalid_with_reason_and_conditional_verdict():
    led = CoverageLedger()
    led.observe(_rec(message_type="login", alert_id=None), CATALOG)
    led.observe(_rec(lat=200.0), CATALOG)                     # impossible latitude
    cov = {c["key"]: c for c in led.coverage(IMEI, CATALOG)}
    assert cov["live"]["valid"] is False
    assert "out of range" in cov["live"]["invalid_reason"]
    assert cov["login"]["valid"] is True, "the sane packet stays valid"
    verdict = engine.overall_verdict(led.coverage(IMEI, CATALOG),
                                     {"packet_count": 2, "skipped": []})
    assert verdict == "conditional_pass", \
        "all required seen but one invalid -> deviation, never a clean PASS"


def test_validate_record_reasons():
    assert engine.validate_record(_rec()) is None
    assert "out of range" in engine.validate_record(_rec(lon=181.0))
    assert "GPS fix" in engine.validate_record(_rec(lat=0.0, lon=0.0, gps_valid=True))
    assert "ahead of receive" in engine.validate_record(_rec(event_offset_s=200000))


def test_late_catalog_replay_backfills_items():
    """Records observed before the protocol was identified still count once the
    catalogue arrives (sink replays the window through replay_items)."""
    led = CoverageLedger()
    early = [_rec(message_type="login", alert_id=None), _rec()]
    for r in early:
        led.observe(r, None)                                  # catalogue not known yet
    assert led.coverage(IMEI, CATALOG)[0]["seen"] is False
    led.replay_items(early, CATALOG)
    cov = {c["key"]: c for c in led.coverage(IMEI, CATALOG)}
    assert cov["login"]["seen"] and cov["live"]["seen"]
    assert led.vitals(IMEI)["packet_count"] == 2, "replay must not double-count vitals"
