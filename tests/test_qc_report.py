"""QC report export (proinfo/qc/report.py): the per-lot deliverable — Sheet 1 "Summary"
with one row per device, plus one sheet per device titled with its IMEI carrying the
Summary view's coverage detail (item / required / count / last raw packet).

Both writers (XLSX workbook, ZIP-of-CSVs) render the same logical grids, so the tests
assert content through each format's own reader.
"""

from __future__ import annotations

import csv
import io
import zipfile

from proinfo.qc import report

IMEI_A = "861329080405658"
IMEI_B = "861329080406565"

PROFILE = {
    "protocol": "ais140",
    "label": "AIS-140 (Locate 140B)",
    "raw_view": "ascii",
}


def _cov(key, label, group, code, seen, required=True, count=0, raw=b"", alert_id=None):
    return {
        "key": key, "label": label, "group": group, "code": code,
        "alert_id": alert_id, "required": required, "seen": seen, "count": count,
        "last_raw_hex": raw.hex() if raw else "", "last_seen": "2026-07-13T11:26:18+05:30",
        "valid": None,
    }


def _device(did, serial, verdict, cov, skipped=()):
    return {
        "device_id": did, "serial": serial, "packet_count": 42, "verdict": verdict,
        "firmware_version": "2.2.5", "vendor_id": "LOCT14A",
        "coverage": cov, "checks": [], "skipped": list(skipped),
        "last_packet": {
            "message_type": "normal", "received_at": "2026-07-13T11:26:18+05:30",
            "position": {"lat": 28.550419, "lon": 77.268768, "satellites": 8, "gps_valid": True},
            "battery": {"external_v": 12.4, "internal_v": 4.1},
        },
    }


def _view(devices):
    return {
        "id": 7, "port": 5051, "host": "0.0.0.0", "operator": "shahbaz",
        "identified_as": "ais140", "protocol_mode": "auto",
        "started_at": 1784000000.0, "ended_at": None,
        "packet_count": sum(d["packet_count"] for d in devices), "devices": devices,
    }


def _sample_devices():
    cov_a = [
        _cov("login", "Login", "packet", "LGN", True, count=2,
             raw=b"$LGN,0000000000,861329080405658,2.2.5*"),
        _cov("emr", "Emergency ON", "emergency", "EMR", True, count=1,
             raw=b"$EPB,EMR,861329080405658,NM*74"),
        _cov("alert_7", "Ignition ON", "alert", "IN", False, alert_id=7),
    ]
    cov_b = [
        _cov("login", "Login", "packet", "LGN", True, count=1,
             raw=b"$LGN,0000000000,861329080406565,2.2.5*"),
        _cov("emr", "Emergency ON", "emergency", "EMR", False),
        _cov("alert_7", "Ignition ON", "alert", "IN", True, count=3, alert_id=7,
             raw=b"$PVT,LOCT14A,2.2.5,IN,7,L,861329080406565*"),
    ]
    return [_device(IMEI_A, 1, "in_progress", cov_a),
            _device(IMEI_B, 2, "conditional_pass", cov_b, skipped=["emr"])]


def test_xlsx_has_summary_plus_one_sheet_per_imei():
    openpyxl = __import__("openpyxl")
    devices = _sample_devices()
    data = report.build_xlsx(_view(devices), devices, PROFILE)
    wb = openpyxl.load_workbook(io.BytesIO(data))
    assert wb.sheetnames == ["Summary", IMEI_A, IMEI_B], \
        "Sheet 1 must be the Summary, then one sheet per device named by IMEI"

    # Summary: header block carries the lot metadata, table has one row per device
    text = [[c.value for c in row] for row in wb["Summary"].iter_rows()]
    flat = [str(v) for row in text for v in row if v is not None]
    assert any("AIS-140 (Locate 140B)" in v for v in flat)
    header = next(row for row in text if row and row[0] == "S.No")
    assert "IMEI" in header and "Verdict" in header
    imei_col = header.index("IMEI")
    devrows = [row for row in text[text.index(header) + 1:]
               if row and isinstance(row[0], (int, float))]
    assert [row[imei_col] for row in devrows] == [IMEI_A, IMEI_B]

    # reference-sheet placeholders: manual/iNTouch columns exist and stay BLANK, the
    # utility-observed bench columns are auto-filled, and the sign-off footer is present
    for col in ("Mobile No", "Harness", "COTA", "FOTA", "SMS Command",
                "Power consumption without Ignition (mA)", "Field Testing",
                "Ignition On (Intouch)", "Plugged/Unplugged Alarm on Intouch"):
        assert col in header, f"missing placeholder column {col!r}"
        assert all(row[header.index(col)] in (None, "") for row in devrows), \
            f"manual column {col!r} must stay blank for hand entry"
    for col in ("GSM Fix", "GPS Fix", "Data on Static"):
        assert [row[header.index(col)] for row in devrows] == ["OK", "OK"], \
            f"utility-observed column {col!r} should auto-fill OK"
    assert any("Lot Size:" in str(row[0]) for row in text if row and row[0] is not None)
    for label in ("Observation:", "PCB Number:", "Final Result:",
                  "Quality Tested By:", "Quality Approved By:"):
        assert any(str(row[0]) == label for row in text if row and row[0] is not None), \
            f"missing sign-off footer line {label!r}"

    # per-IMEI sheet: coverage rows show the item + the last raw packet as ASCII
    dev_flat = [str(c.value) for row in wb[IMEI_A].iter_rows() for c in row if c.value is not None]
    assert any(v.startswith("$LGN,0000000000,861329080405658") for v in dev_flat), \
        "the last received raw packet must be on the device sheet (ASCII for ais140)"
    assert "MISSING" in dev_flat, "an unseen required item must read MISSING"


def test_xlsx_skipped_item_reads_skip_not_missing():
    openpyxl = __import__("openpyxl")
    devices = _sample_devices()
    data = report.build_xlsx(_view(devices), devices, PROFILE)
    wb = openpyxl.load_workbook(io.BytesIO(data))
    dev_flat = [str(c.value) for row in wb[IMEI_B].iter_rows() for c in row if c.value is not None]
    assert "SKIP" in dev_flat and "MISSING" not in dev_flat, \
        "device B's only unseen required item is skipped -> SKIP, nothing MISSING"


def test_csv_zip_mirrors_the_sheet_layout():
    devices = _sample_devices()
    data = report.build_csv_zip(_view(devices), devices, PROFILE)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert sorted(z.namelist()) == sorted(["Summary.csv", f"{IMEI_A}.csv", f"{IMEI_B}.csv"])
        rows = list(csv.reader(io.StringIO(z.read("Summary.csv").decode("utf-8-sig"))))
        header = next(r for r in rows if r and r[0] == "S.No")
        assert header[header.index("IMEI")] == "IMEI"
        # device rows = the S.No-numbered rows right below the header (the sign-off
        # footer that follows starts with text labels, not numbers)
        body = [r for r in rows[rows.index(header) + 1:] if r and r[0].isdigit()]
        assert [r[header.index("IMEI")] for r in body] == [IMEI_A, IMEI_B]
        assert [r[header.index("Verdict")] for r in body] == ["IN PROGRESS", "CONDITIONAL PASS"]


def test_ascii_sniff_when_protocol_unknown():
    """A session that lost its identity (no profile -> raw_view unknown) must still render
    $-ASCII sentences as text in the report, not collapse to hex."""
    devices = _sample_devices()[:1]
    view = {**_view(devices), "identified_as": ""}
    data = report.build_csv_zip(view, devices, None)      # no profile at all
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        body = z.read(f"{IMEI_A}.csv").decode("utf-8-sig")
    assert "$LGN,0000000000,861329080405658" in body, \
        "printable packets must sniff to ASCII when the profile's raw_view is unknown"


def test_infer_protocol_from_persisted_coverage():
    """The startup heal recovers a session's protocol from its persisted coverage keys
    (rows damaged by the old self-heal path, which lost identified_as/packet_count)."""
    import json as _json

    from proinfo.api.app import _infer_protocol_from_devices

    devices = _sample_devices()                          # keys login/emr/alert_7 ⊆ ais140
    proto, packets = _infer_protocol_from_devices(_json.dumps(devices))
    assert proto == "ais140"
    assert packets == sum(d["packet_count"] for d in devices)
    assert _infer_protocol_from_devices("[]") == ("", 0)
    alien = _json.dumps([{"packet_count": 1, "coverage": [{"key": "no-such-item"}]}])
    assert _infer_protocol_from_devices(alien) == ("", 0), \
        "keys that match no profile must not guess a protocol"


def test_completion_gate_blocks_in_progress_devices():
    """The report is the lot's final deliverable: in-progress devices block printing;
    settled ones (incl. conditional_pass via skips) don't. missing_required names exactly
    the items the operator must skip to unblock."""
    devices = _sample_devices()          # A in_progress, B conditional_pass (emr skipped)
    assert [d["device_id"] for d in report.incomplete_devices(devices)] == [IMEI_A]
    assert [c["key"] for c in report.missing_required(devices[0])] == ["alert_7"]
    assert report.missing_required(devices[1]) == [], \
        "skipped items must not count as missing"
    settled = dict(devices[0], verdict="conditional_pass")
    assert report.incomplete_devices([settled, devices[1]]) == []


def test_lot_and_manual_fields_merge_into_the_report():
    """Values typed into the in-app "Lot details" form must land in the workbook: the
    per-device manual columns, the header's Lot Size, and the sign-off footer."""
    devices = _sample_devices()
    view = _view(devices)
    view["lot"] = {"lot_id": "L-77", "lot_size": "100", "pcb_number": "PCB-9",
                   "observation": "all good", "approved_by": "AG"}
    view["manual"] = {IMEI_A: {"mobile_no": "9876543210", "cota": "OK",
                               "power_ma_without_ign": "84mA", "remarks": "sticker ok"}}
    data = report.build_csv_zip(view, devices, PROFILE)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        rows = list(csv.reader(io.StringIO(z.read("Summary.csv").decode("utf-8-sig"))))
    header = next(r for r in rows if r and r[0] == "S.No")
    dev_a = next(r for r in rows[rows.index(header) + 1:] if r and r[0].isdigit())
    for col, val in (("Mobile No", "9876543210"), ("COTA", "OK"),
                     ("Power consumption without Ignition (mA)", "84mA")):
        assert dev_a[header.index(col)] == val, f"{col!r} must carry the typed value"
    assert "sticker ok" in dev_a[header.index("Remarks")]
    assert dev_a[header.index("Harness")] == "", "untyped manual fields stay blank"
    flat = ["|".join(r) for r in rows]
    assert any(r.startswith("Lot Size:|100") for r in flat)
    assert any(r.startswith("PCB Number:|PCB-9") for r in flat)
    assert any(r.startswith("Quality Approved By:|AG") for r in flat)
    assert any(r.startswith("Lot / Batch:|L-77") for r in flat)


def test_skip_reason_from_audit_lands_on_the_device_sheet():
    """A skipped item's who/why/when (from the audit trail) prints on the Deviations
    section — a Conditional Pass must be accountable."""
    devices = _sample_devices()
    view = _view(devices)
    view["audit"] = [{"at": 1784000100.0, "action": "skip", "device_id": IMEI_B,
                      "added": ["emr"], "removed": [],
                      "reason": "no emergency rig on bench", "operator": "shahbaz"}]
    data = report.build_csv_zip(view, devices, PROFILE)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        body = z.read(f"{IMEI_B}.csv").decode("utf-8-sig")
    assert "no emergency rig on bench" in body
    assert "by shahbaz" in body


def test_device_subset_and_filename():
    devices = _sample_devices()[:1]
    data = report.build_csv_zip(_view(devices), devices, PROFILE)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert f"{IMEI_B}.csv" not in z.namelist()
    name = report.report_filename(_view(devices), PROFILE, "xlsx")
    assert name.endswith("-S7.xlsx") and "AIS-140" in name
