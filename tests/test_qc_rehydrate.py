"""Same-session QC retention (QcCaptureSink.rehydrate, proinfo/qc/session.py):
a resumed/restarted QC session re-decodes its own append-only capture file, so the
Summary (packet counts, coverage samples, device identity, timestamps) carries on where
it left off instead of starting blank after a Stop, a crash or a server restart.

Sentences are real Locate 140B captures (the 48-field PVT wire schema), so the tests
exercise framing + field parsing end-to-end like the codec tests do.
"""

from __future__ import annotations

import json
from pathlib import Path

from proinfo.core import registry
from proinfo.core.models import Session
from proinfo.qc import engine
from proinfo.qc.session import QcCaptureSink

IMEI = "868329087318766"

LGN = b"$LGN,0000000000,868329087318766,2.2.5,AIS140,28.550463,77.268898*\r\n"
PVT_LIVE = (b"$PVT,LOCT14A,2.2.5,NR,1,L,868329087318766,0000000000,1,17072026,073029,"
            b"28.550447,N,77.268875,E,000.0,335.91,32,225.945,0.88,0.47,airtel,1,1,13.7,"
            b"4.0,0,C,31,404,10,092,9305,9304,092,18,d40f,092,12,96e3,06f,11,0,0,31,"
            b"0010,00,097149,D9*\r\n")
PVT_HISTORY = (b"$PVT,LOCT14A,2.2.5,NR,2,H,868329087318766,0000000000,1,17072026,045507,"
               b"28.550442,N,77.268860,E,000.0,206.12,33,223.213,0.82,0.47,airtel,1,1,"
               b"13.7,4.0,0,C,27,404,10,092,9305,9304,092,18,d40f,092,14,d410,092,12,"
               b"96e3,06f,12,0010,00,096224,56*\r\n")
HEL = b"$HEL,LOCT14A,2.2.5,868329087318766,68,30,0,10,600,0010,00*\r\n"

STREAM = LGN + PVT_LIVE + PVT_HISTORY + HEL

CATALOG = json.loads(
    (Path(__file__).resolve().parents[1] / "proinfo" / "qc" / "profiles" / "ais140.json")
    .read_text(encoding="utf-8")
)["coverage"]


def _decode(codec_name: str, data: bytes):
    """Drain `data` through the codec the way the live pipeline does."""
    codec = registry.get_codec(codec_name)
    sess = Session(id="test", transport="tcp")
    buf, recs = bytearray(data), []
    while buf:
        out = codec.decode(bytes(buf), sess)
        if out.consumed <= 0:
            break
        del buf[: out.consumed]
        recs.extend(out.records)
    return recs


async def _capture(path, records) -> QcCaptureSink:
    """Run records through a live sink so the capture file is written for real."""
    sink = QcCaptureSink(str(path))
    await sink.start()
    for r in records:
        await sink.handle(r)
    await sink.close()
    return sink


async def test_rehydrate_restores_records_identity_and_count(tmp_path):
    cap = tmp_path / "qc-1.txt"
    original = _decode("ais140", STREAM)
    await _capture(cap, original)

    fresh = QcCaptureSink(str(cap))
    restored = fresh.rehydrate("ais140")

    assert restored == len(original) == fresh.count == len(fresh.records)
    assert fresh.device_id == IMEI
    assert fresh.identified_as == "ais140"
    assert [r.message_type for r in fresh.records] == [r.message_type for r in original]
    assert [r.raw_hex for r in fresh.records] == [r.raw_hex for r in original]


async def test_rehydrate_preserves_coverage(tmp_path):
    """The Summary's per-item counts + captured samples must survive a resume unchanged."""
    cap = tmp_path / "qc-2.txt"
    original = _decode("ais140", STREAM)
    await _capture(cap, original)

    fresh = QcCaptureSink(str(cap))
    fresh.rehydrate("ais140")

    def slim(cov):   # last_seen intentionally excluded: tz representation may differ
        return [(c["key"], c["seen"], c["count"], c["last_raw_hex"]) for c in cov]

    before = engine.coverage(original, CATALOG)
    after = engine.coverage(fresh.records, CATALOG)
    assert slim(after) == slim(before)
    covered = {c["key"] for c in after if c["seen"]}
    assert covered == {"login", "live", "history", "health"}


async def test_rehydrate_restores_the_coverage_ledger(tmp_path):
    """The verdict reads the incremental CoverageLedger (not the bounded record window),
    so a resume must restore the ledger too — otherwise the Summary would still come
    back blank after a restart."""
    cap = tmp_path / "qc-ledger.txt"
    await _capture(cap, _decode("ais140", STREAM))

    fresh = QcCaptureSink(str(cap),
                          catalog_loader=lambda proto: CATALOG if proto == "ais140" else None)
    fresh.rehydrate("ais140")

    counts = {c["key"]: c["count"] for c in fresh.ledger.coverage(IMEI, CATALOG) if c["seen"]}
    assert counts == {"login": 1, "live": 1, "history": 1, "health": 1}
    vitals = fresh.ledger.vitals(IMEI)
    assert vitals["packet_count"] == 4
    assert vitals["firmware_version"] == "2.2.5"


async def test_rehydrate_restores_received_at_to_the_millisecond(tmp_path):
    cap = tmp_path / "qc-3.txt"
    original = _decode("ais140", LGN)      # login carries no event_time -> ts is received_at
    await _capture(cap, original)

    fresh = QcCaptureSink(str(cap))
    fresh.rehydrate("ais140")
    drift = abs((fresh.records[0].received_at - original[0].received_at).total_seconds())
    assert drift < 0.001, "rehydrated received_at must match the capture line's timestamp"


async def test_rehydrate_auto_detects_the_protocol(tmp_path):
    """A session that never learned a protocol name (codec 'auto') still rehydrates."""
    cap = tmp_path / "qc-4.txt"
    await _capture(cap, _decode("ais140", STREAM))

    fresh = QcCaptureSink(str(cap))
    assert fresh.rehydrate("auto") == 4
    assert fresh.identified_as == "ais140"
    assert fresh.device_id == IMEI


def test_rehydrate_missing_file_is_a_noop(tmp_path):
    fresh = QcCaptureSink(str(tmp_path / "never-written.txt"))
    assert fresh.rehydrate("ais140") == 0
    assert fresh.records == [] and fresh.count == 0


async def test_rehydrate_skips_corrupt_lines(tmp_path):
    cap = tmp_path / "qc-5.txt"
    await _capture(cap, _decode("ais140", HEL))
    with open(cap, "a", encoding="utf-8") as fh:
        fh.write("this is not a capture line\n")
        fh.write("17-Jul-2026 10:00:00:000 | 868329087318766 | zz-not-hex\n")

    fresh = QcCaptureSink(str(cap))
    assert fresh.rehydrate("ais140") == 1, "the valid line survives, garbage is skipped"
    assert fresh.records[0].message_type == "health"


async def test_rehydrate_count_stays_cumulative(tmp_path):
    """count never goes backwards: a resume's DB packet_count wins if it is larger
    (e.g. the capture decoded fewer records than were originally counted)."""
    cap = tmp_path / "qc-6.txt"
    await _capture(cap, _decode("ais140", STREAM))

    fresh = QcCaptureSink(str(cap))
    fresh.count = 999
    fresh.rehydrate("ais140")
    assert fresh.count == 999


def test_rehydrate_falls_back_to_the_file_device_column(tmp_path):
    """When re-decoding can't recover an identity (e.g. generic_hex), the capture line's
    own device-id column — written by the live run — is the source of truth."""
    cap = tmp_path / "qc-7.txt"
    cap.write_text("16-Jul-2026 10:00:00:000 | 990000000000009 | 0102ab\n", encoding="utf-8")

    fresh = QcCaptureSink(str(cap))
    assert fresh.rehydrate("generic_hex") == 1
    assert fresh.records[0].device_id == "990000000000009"
    assert fresh.device_id == "990000000000009"
