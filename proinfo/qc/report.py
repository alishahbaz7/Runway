"""QC report file builder — Sheet 1 "Summary" + one sheet per device (IMEI).

Builds the per-lot QC deliverable (modelled on the bench's reference workbook, e.g.
"LOCATE 140B[TS]"): a summary sheet with one row per tested device and one column per
coverage item, then a per-IMEI sheet carrying the same detail the Capture page's QC
Summary view shows — device details plus the Packets / Emergency server / Alerts tables
with the most recent raw packet received for each item.

Two output formats:

* ``build_xlsx``    — a real Excel workbook (needs ``openpyxl``; imported lazily).
* ``build_csv_zip`` — a ZIP of CSV files, one per would-be sheet (CSV has no sheets).

Framework-free like the rest of ``qc/``: takes the assembled session view (the dict the
API's ``_qc_view`` returns) + the QC profile, returns bytes. No FastAPI/DB imports.
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime
from typing import Optional

VERDICT_LABELS = {
    "in_progress": "IN PROGRESS",
    "timeout": "TIMEOUT",
    "pass": "PASS",
    "conditional_pass": "CONDITIONAL PASS",
    "fail": "FAIL",
}

# coverage group -> section heading, in display order (same as the Summary panel)
_GROUPS = [
    (lambda g: g not in ("alert", "emergency"), "Packets"),
    (lambda g: g == "emergency", "Emergency server"),
    (lambda g: g == "alert", "Alerts"),
]


def verdict_label(v: str) -> str:
    return VERDICT_LABELS.get(v or "", (v or "").upper())


def incomplete_devices(devices: list) -> list:
    """Devices whose QC hasn't settled yet. The report is the lot's FINAL deliverable, so
    an in-progress device blocks printing — the operator either finishes the test or marks
    the missing required items as Skip (the device then closes as Conditional Pass).
    Settled verdicts (pass / conditional_pass / fail / timeout / a finalized override)
    print fine."""
    return [d for d in devices if (d.get("verdict") or "") in ("", "in_progress")]


def missing_required(dev: dict) -> list:
    """The coverage items still blocking this device: required, not seen, not skipped."""
    skipped = set(dev.get("skipped") or [])
    return [c for c in (dev.get("coverage") or [])
            if c.get("required") and not c.get("seen") and c.get("key") not in skipped]


def _fmt_epoch(t) -> str:
    if not t:
        return ""
    try:
        return datetime.fromtimestamp(float(t)).strftime("%d-%m-%Y %H:%M:%S")
    except Exception:
        return str(t)


def _fmt_iso(s) -> str:
    if not s:
        return ""
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone()
        return dt.strftime("%d-%m-%Y %H:%M:%S")
    except Exception:
        return str(s)


def _mostly_printable(data: bytes) -> bool:
    """True when the first bytes read as text — fallback for a session whose protocol
    identity (and so its profile's raw_view) is unknown: $-ASCII sentences should still
    land in the report as readable text, not hex."""
    sample = data[:64]
    if not sample:
        return False
    printable = sum(1 for b in sample if 32 <= b < 127 or b in (9, 10, 13))
    return printable / len(sample) >= 0.9


def _raw_text(raw_hex: str, raw_view: str) -> str:
    """The raw packet as the operator sees it: ASCII for text protocols, spaced hex else."""
    if not raw_hex:
        return ""
    try:
        data = bytes.fromhex(raw_hex)
    except ValueError:
        return raw_hex
    if raw_view == "ascii" or (not raw_view and _mostly_printable(data)):
        data = data.strip(b"\r\n")               # don't render the line terminator as "·"
        return "".join(chr(b) if 32 <= b < 127 else "·" for b in data)
    return " ".join(f"{b:02X}" for b in data)


def _item_label(c: dict) -> str:
    """Same decoration as the Summary panel: alerts show "(id - code)", emergency "(code)"."""
    if c.get("group") == "alert" and c.get("alert_id") is not None:
        code = f" - {c['code']}" if c.get("code") else ""
        return f"{c.get('label', c.get('key', ''))} ({c['alert_id']}{code})"
    if c.get("group") == "emergency" and c.get("code"):
        return f"{c.get('label', c.get('key', ''))} ({c['code']})"
    return c.get("label", c.get("key", ""))


def _item_state(c: dict, skipped: set) -> str:
    if c.get("key") in skipped:
        return "SKIP"
    if c.get("valid") is False:
        return "INVALID"
    if c.get("seen"):
        return "OK"
    return "MISSING" if c.get("required") else "—"


def _required_fraction(dev: dict) -> str:
    cov = dev.get("coverage") or []
    skipped = set(dev.get("skipped") or [])
    req = [c for c in cov if c.get("required") and c.get("key") not in skipped]
    if not req:
        return ""
    return f"{sum(1 for c in req if c.get('seen'))}/{len(req)}"


def _remarks(dev: dict) -> str:
    cov = dev.get("coverage") or []
    skipped = set(dev.get("skipped") or [])
    parts = []
    skip_labels = [c.get("label", c.get("key", "")) for c in cov if c.get("key") in skipped]
    bad_labels = [c.get("label", c.get("key", "")) for c in cov if c.get("valid") is False]
    if skip_labels:
        parts.append("skipped: " + ", ".join(skip_labels))
    if bad_labels:
        parts.append("invalid: " + ", ".join(bad_labels))
    return " · ".join(parts)


# Bench-only columns from the reference QC sheet (LOCATE 140B) the utility cannot
# observe: (field key, column label). Values come from the session's in-app "Lot
# details" form (view["manual"][device_id]) — anything not typed stays a blank
# placeholder for hand entry, so the export is always the complete lot deliverable.
MANUAL_COLUMNS = [
    ("data_on_intouch", "Data on Intouch"),
    ("all_io_static", "All Input and Output (On Static)"),
    ("harness", "Harness"),
    ("battery_charging", "Battery Charging"),
    ("ignition_on_intouch", "Ignition On (Intouch)"),
    ("ignition_off_intouch", "Ignition Off (Intouch)"),
    ("plugged_alarm_intouch", "Plugged/Unplugged Alarm on Intouch"),
    ("cota", "COTA"),
    ("fota", "FOTA"),
    ("sms_command", "SMS Command"),
    ("gprs_command", "GPRS Command"),
    ("serial_command", "Serial Command"),
    ("power_ma_without_ign", "Power consumption without Ignition (mA)"),
    ("power_ma_with_ign", "Power consumption with Ignition ON (mA)"),
    ("field_testing", "Field Testing"),
]


def _gps_fix_ok(dev: dict) -> str:
    """"OK" once a valid GPS fix was observed for this device (per-device checks first,
    falling back to the newest positioned packet), else blank for the operator."""
    for c in dev.get("checks") or []:
        if c.get("key") in ("location_fix", "gps_valid_flag") and c.get("result") == "pass":
            return "OK"
    pos = (dev.get("last_packet") or {}).get("position") or {}
    return "OK" if pos.get("gps_valid") else ""


# --------------------------------------------------------------------------- #
# Logical grids — shared by both formats so XLSX and CSV carry the same content.
# A grid is a list of rows; a row is a list of cell values. Row 0 semantics are
# format-specific (the XLSX writer styles by the markers below).
# --------------------------------------------------------------------------- #
def _summary_grid(view: dict, devices: list, profile: Optional[dict]) -> tuple[list, int, int]:
    """The Summary sheet rows + the table-header row index + the footer start index.

    Column order follows the reference lot sheet (LOCATE 140B): identity → the bench
    result columns (auto-filled where the utility observes them, blank placeholders for
    the manual/iNTouch ones — see MANUAL_COLUMNS) → the per-item coverage detail →
    verdict/remarks. The footer is the sheet's sign-off block, mostly blank for hand
    entry."""
    label = (profile or {}).get("label") or view.get("identified_as") or view.get("protocol") or ""
    started = view.get("started_at")
    rows: list = []
    rows.append([f"QC Report — {label}" if label else "QC Report"])
    rows.append([])
    rows.append(["Date of Inspection:", _fmt_epoch(started).split(" ")[0] if started else ""])
    rows.append(["Item Name:", label])
    sess_line = f"#{view.get('id', '')} · port {view.get('port', '')}"
    if view.get("operator"):
        sess_line += f" · operator: {view['operator']}"
    rows.append(["Session:", sess_line])
    rows.append(["Started:", _fmt_epoch(started)])
    if view.get("ended_at"):
        rows.append(["Ended:", _fmt_epoch(view.get("ended_at"))])
    lot = view.get("lot") or {}                          # in-app "Lot details" form values
    manual_all = view.get("manual") or {}                # {device_id: {field: value}}
    if lot.get("lot_id"):
        rows.append(["Lot / Batch:", lot["lot_id"]])
    rows.append(["Total Devices Tested:", len(devices)])
    rows.append(["Lot Size:", lot.get("lot_size", "")])
    rows.append([])

    # item columns follow the profile catalogue order; devices without coverage still get
    # the identity/verdict columns
    catalog = devices[0].get("coverage") or [] if devices else []
    head = (["S.No", "Mobile No", "IMEI", "Serial", "Firmware", "Vendor ID", "Packets",
             "GSM Fix", "GPS Fix", "Data on Static"]
            + [label for _, label in MANUAL_COLUMNS]
            + [_item_label(c) for c in catalog]
            + ["Required", "Verdict", "Remarks"])
    header_idx = len(rows)
    rows.append(head)

    for i, dev in enumerate(devices, start=1):
        skipped = set(dev.get("skipped") or [])
        cov = dev.get("coverage") or []
        by_key = {c.get("key"): c for c in cov}
        manual = manual_all.get(dev.get("device_id", "")) or {}
        got_data = "OK" if dev.get("packet_count") else ""
        row = [i,
               manual.get("mobile_no", ""),              # SIM MSISDN — typed in-app
               dev.get("device_id", ""),
               f"#{dev['serial']}" if dev.get("serial") is not None else "",
               dev.get("firmware_version", ""), dev.get("vendor_id", ""),
               dev.get("packet_count", 0),
               got_data,                                 # GSM Fix — it dialled in over GPRS
               _gps_fix_ok(dev),                         # GPS Fix — a valid fix was seen
               got_data]                                 # Data on Static — packets landed here
        row += [manual.get(key, "") for key, _ in MANUAL_COLUMNS]
        for c in catalog:
            m = by_key.get(c.get("key"))
            row.append(_item_state(m, skipped) if m else "")
        remarks = " · ".join(x for x in (_remarks(dev), manual.get("remarks", "")) if x)
        row += [_required_fraction(dev), verdict_label(dev.get("verdict", "")), remarks]
        rows.append(row)

    # --- sign-off footer (mirrors the reference sheet's block below the table) ---
    footer_idx = len(rows)
    firmwares = sorted({d.get("firmware_version") for d in devices if d.get("firmware_version")})
    rows.append([])
    rows.append(["Observation:", lot.get("observation", "")])
    rows.append(["Firmware Version:", ", ".join(firmwares)])
    rows.append(["PCB Number:", lot.get("pcb_number", "")])
    rows.append(["Corrective Action:", lot.get("corrective_action", "")])
    rows.append(["Final Result:", verdict_label(view.get("verdict", ""))])
    rows.append(["Quality Tested By:", view.get("operator") or ""])
    rows.append(["Quality Approved By:", lot.get("approved_by", "")])
    rows.append(["Tested on (IP:Port):", f"{view.get('host', '')}:{view.get('port', '')}"])
    return rows, header_idx, footer_idx


def _device_grid(dev: dict, view: dict, profile: Optional[dict]) -> tuple[list, list[int]]:
    """One device's sheet rows + the indices of its table-header rows (for styling)."""
    raw_view = (profile or {}).get("raw_view") or ""
    did = dev.get("device_id", "")
    rows: list = []
    header_rows: list[int] = []
    rows.append([f"Device {did}"
                 + (f"  (#{dev['serial']})" if dev.get("serial") is not None else "")])
    rows.append([])

    # --- Device details (mirrors the Summary panel block) ---
    rows.append(["Device details"])
    kv = [("IMEI", did),
          ("Serial", f"#{dev['serial']}" if dev.get("serial") is not None else ""),
          ("Firmware", dev.get("firmware_version", "")),
          ("Vendor ID", dev.get("vendor_id", "")),
          ("Protocol", view.get("identified_as", "")),
          ("Packets received", dev.get("packet_count", 0)),
          ("Verdict", verdict_label(dev.get("verdict", "")))]
    lp = dev.get("last_packet") or {}
    if lp.get("received_at"):
        kv.append(("Last received", _fmt_iso(lp["received_at"])))
    pos = lp.get("position")
    if pos:
        kv.append(("Lat, Long", f"{pos.get('lat'):.6f}, {pos.get('lon'):.6f}"
                   if pos.get("lat") is not None else ""))
        sats = f"{pos['satellites']} sats" if pos.get("satellites") is not None else "? sats"
        kv.append(("GPS", f"{sats} · {'fixed' if pos.get('gps_valid') else 'no fix'}"))
    bat = lp.get("battery")
    if bat:
        fv = lambda v: "—" if v is None else f"{v:.1f}V"   # noqa: E731
        kv.append(("Battery (ext · int)", f"{fv(bat.get('external_v'))} · {fv(bat.get('internal_v'))}"))
    rows.extend([k, v] for k, v in kv)
    rows.append([])

    cov = dev.get("coverage") or []
    skipped = set(dev.get("skipped") or [])
    if cov:
        for pred, heading in _GROUPS:
            items = [c for c in cov if pred(c.get("group", ""))]
            if not items:
                continue
            req = [c for c in items if c.get("required") and c.get("key") not in skipped]
            seen = sum(1 for c in req if c.get("seen"))
            rows.append([f"{heading}" + (f" — {seen}/{len(req)} required" if req else "")])
            header_rows.append(len(rows))
            rows.append(["Status", "Item", "Code", "Required", "Count", "Last received",
                         "Raw packet"])
            for c in items:
                rows.append([_item_state(c, skipped), _item_label(c), c.get("code", ""),
                             "yes" if c.get("required") else "—", c.get("count") or 0,
                             _fmt_iso(c.get("last_seen")),
                             _raw_text(c.get("last_raw_hex") or "", raw_view)])
            rows.append([])
        devi = [c for c in cov if c.get("key") in skipped or c.get("valid") is False]
        if devi:
            # who skipped what and why, from the session's audit trail — a Conditional
            # Pass on a QC certificate must be accountable, not anonymous
            skip_meta: dict = {}
            for entry in (view.get("audit") or []):
                if entry.get("action") == "skip" and entry.get("device_id") == did:
                    for k in entry.get("added") or []:
                        skip_meta[k] = entry
            rows.append([f"Deviations — {len(devi)} (conditional)"])
            for c in devi:
                if c.get("valid") is False:
                    detail = c.get("invalid_reason") or "invalid packet fields"
                    rows.append(["", _item_label(c), "invalid", detail])
                else:
                    m = skip_meta.get(c.get("key")) or {}
                    who = m.get("operator") or ""
                    detail = " — ".join(x for x in (
                        m.get("reason") or "", f"by {who}" if who else "",
                        _fmt_epoch(m.get("at")) if m.get("at") else "") if x)
                    rows.append(["", _item_label(c), "skipped", detail])
    else:
        # no coverage profile for this protocol — fall back to the pass/fail checks
        checks = dev.get("checks") or []
        if checks:
            rows.append(["Checks"])
            header_rows.append(len(rows))
            rows.append(["Result", "Check", "Detail"])
            for c in checks:
                rows.append([(c.get("result") or "").upper(), c.get("label", ""),
                             c.get("detail", "")])
    return rows, header_rows


def _sheet_title(name: str, used: set) -> str:
    """A legal, unique worksheet title (Excel: max 31 chars, no []:*?/\\)."""
    clean = "".join(ch for ch in str(name) if ch not in "[]:*?/\\").strip() or "device"
    clean = clean[:31]
    title, n = clean, 2
    while title in used:
        suffix = f" ({n})"
        title = clean[: 31 - len(suffix)] + suffix
        n += 1
    used.add(title)
    return title


# --------------------------------------------------------------------------- #
# Writers
# --------------------------------------------------------------------------- #
def build_xlsx(view: dict, devices: list, profile: Optional[dict]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    bold = Font(bold=True)
    title_font = Font(bold=True, size=13)
    head_fill = PatternFill("solid", fgColor="DCE6F1")
    thin = Side(style="thin", color="B0B0B0")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    verdict_fills = {"PASS": PatternFill("solid", fgColor="C6EFCE"),
                     "CONDITIONAL PASS": PatternFill("solid", fgColor="FFEB9C"),
                     "FAIL": PatternFill("solid", fgColor="FFC7CE"),
                     "TIMEOUT": PatternFill("solid", fgColor="FFC7CE")}

    wb = Workbook()

    def write_grid(ws, rows, header_rows: list[int], footer_from: Optional[int] = None):
        table_cols = 0
        for ri, row in enumerate(rows, start=1):
            in_footer = footer_from is not None and (ri - 1) >= footer_from
            is_head = (ri - 1) in header_rows and not in_footer
            in_table = (any((ri - 1) > h for h in header_rows) or is_head) and not in_footer
            for ci, val in enumerate(row, start=1):
                cell = ws.cell(row=ri, column=ci, value=val)
                if ri == 1:
                    cell.font = title_font
                elif is_head:
                    cell.font = bold
                    cell.fill = head_fill
                    cell.border = border
                    table_cols = max(table_cols, len(row))
                elif in_table and len(row) > 1:
                    cell.border = border
                    if isinstance(val, str) and val in verdict_fills:
                        cell.fill = verdict_fills[val]
                elif len(row) == 1 and isinstance(val, str):
                    cell.font = bold                     # section headings
                elif ci == 1 and len(row) == 2:
                    cell.font = bold                     # key/value labels
                if isinstance(val, str) and len(val) > 60:
                    cell.alignment = Alignment(wrap_text=False, vertical="top")
        return table_cols

    def size_columns(ws, rows, wide_cols: dict):
        widths: dict[int, int] = {}
        for row in rows:
            for ci, val in enumerate(row, start=1):
                widths[ci] = max(widths.get(ci, 0), min(len(str(val)), 40))
        for ci, w in widths.items():
            ws.column_dimensions[get_column_letter(ci)].width = wide_cols.get(ci, max(w + 2, 8))

    # --- Summary ---
    ws = wb.active
    ws.title = "Summary"
    rows, header_idx, footer_idx = _summary_grid(view, devices, profile)
    write_grid(ws, rows, [header_idx], footer_from=footer_idx)
    size_columns(ws, rows, {3: 18})                      # IMEI column
    ws.freeze_panes = ws.cell(row=header_idx + 2, column=1)

    # --- one sheet per device, titled with the IMEI ---
    used = {"Summary"}
    for dev in devices:
        dws = wb.create_sheet(_sheet_title(dev.get("device_id", "device"), used))
        drows, dheads = _device_grid(dev, view, profile)
        write_grid(dws, drows, dheads)
        # raw-packet column (7) gets a fixed wide width instead of content-sized
        size_columns(dws, drows, {7: 90, 2: 34})

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_csv_zip(view: dict, devices: list, profile: Optional[dict]) -> bytes:
    """CSV cannot hold multiple sheets — pack one CSV per sheet into a ZIP."""
    def to_csv(rows) -> bytes:
        s = io.StringIO()
        w = csv.writer(s, lineterminator="\r\n")
        w.writerows(rows)
        return s.getvalue().encode("utf-8-sig")          # BOM so Excel opens it as UTF-8

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        rows, _, _ = _summary_grid(view, devices, profile)
        z.writestr("Summary.csv", to_csv(rows))
        used = {"Summary"}
        for dev in devices:
            name = _sheet_title(dev.get("device_id", "device"), used)
            drows, _ = _device_grid(dev, view, profile)
            z.writestr(f"{name}.csv", to_csv(drows))
    return buf.getvalue()


def report_filename(view: dict, profile: Optional[dict], ext: str) -> str:
    """Download name modelled on the bench's own files: "<item> QC-<dd-mm-yyyy>-S<id>"."""
    label = (profile or {}).get("label") or view.get("identified_as") or "report"
    safe = "".join(ch for ch in label if ch.isalnum() or ch in " ()-_.").strip() or "report"
    day = _fmt_epoch(view.get("started_at")).split(" ")[0] or datetime.now().strftime("%d-%m-%Y")
    return f"{safe} QC-{day}-S{view.get('id', '')}.{ext}"
