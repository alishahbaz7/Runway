"""A firmware test as an Excel workbook: the verdict first, then the checks, the scenarios, every
flag, and every packet as it came — the record a firmware release is signed off with."""

from __future__ import annotations

import io
from datetime import datetime

from ..xlsx_text import row as _xr

VERDICT = {"pass": "Pass", "fail": "Fail", "incomplete": "Incomplete — scenarios not seen", "in_progress": "In progress"}
LEVEL = {"pass": "Pass", "warn": "Warning", "fail": "Fail"}
FILLS = {"pass": "E3F6EC", "warn": "FDF0D5", "fail": "FBE0DA"}


def _t(epoch) -> str:
    return datetime.fromtimestamp(epoch).strftime("%d-%m-%Y %H:%M:%S") if epoch else ""


def _config_rows(c: dict) -> list[list]:
    """The device's settings the test held it to — empty ones said as not given (not checked)."""
    def v(x, unit):
        return f"{x:g} {unit}" if x else "Not given (not checked)"
    order = {"fifo": "FIFO, oldest first", "lifo": "LIFO, newest first"}.get(c.get("history_order") or "", "Not given (not checked)")
    rows = [["Interval, ignition on", v(c.get("ign_on_s"), "s")], ["Interval, ignition off", v(c.get("ign_off_s"), "s")],
            ["Sleep after ignition off", v(c.get("sleep_after_min"), "min")], ["Interval, asleep", v(c.get("sleep_s"), "s")],
            ["History order", order]]
    rows += [[f"{h} format", f"{f} fields"] for h, f in (c.get("formats") or {}).items()]
    return rows


# ── the packets a developer is handed ─────────────────────────────────────────────────────────────
PACKET_SETS = {"all": "All packets", "fail": "Failed packets", "warn": "Packets with warnings"}


def packet_set(gate, which: str) -> list[dict]:
    """The packets of a set — as the page's All / Failed / Warnings show them."""
    rows = list(gate.rows)
    if which == "fail":
        return [r for r in rows if r["level"] == "fail"]
    if which == "warn":
        return [r for r in rows if any(f["level"] == "warn" for f in r["flags"])]
    return rows


def packets_workbook(view: dict, gate, which: str = "all") -> bytes:
    """Every packet of a set (all, failed, with warnings) for whoever fixes the firmware: each packet with
    what is wrong in words; each finding with the value expected and the one sent, its likely cause, where
    to look and the fix; what each check means and where its rule comes from; and every field under its
    sheet name, a cell that broke its rule coloured. Written row by row (write-only): a week-long soak fits."""
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.cell.cell import Cell
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    from ..xlsx_text import cell as _xc
    from .diagnosis import _why, diag_for
    from .gate import CHECK_SOURCE, CHECKS

    gate._derive()
    rows = packet_set(gate, which)
    wb = Workbook(write_only=True)
    bold, wrap = Font(bold=True), Alignment(wrap_text=True, vertical="top")
    head_fill = PatternFill("solid", fgColor="E6E6E6")
    fills = {k: PatternFill("solid", fgColor=v) for k, v in FILLS.items() if k != "pass"}

    def sheet(title, header, widths):
        ws = wb.create_sheet(title)
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = "A2"
        put(ws, [cell(ws, h, font=bold, fill=head_fill) for h in header])
        return ws

    def put(ws, values):
        ws.append([v if isinstance(v, Cell) else _xc(v) for v in values])

    def cell(ws, v, level=None, font=None, fill=None, wrapped=False):
        c = WriteOnlyCell(ws, value=_xc(v))
        if fill is not None or level in fills:
            c.fill = fill if fill is not None else fills[level]
        if font is not None:
            c.font = font
        if wrapped:
            c.alignment = wrap
        return c

    # field positions and labels, per header, as the spec names them
    fmt = gate.config.get("formats") or {}

    def field_of(header, name):
        p = gate.spec.packets.get(header)
        fs = next((f for f in (p.field_list if p else ()) if f.name == name
                   and (not f.formats or fmt.get(header) in f.formats)), None)
        return (f"field {fs.index}, {fs.label}" if fs and fs.index > 0 else f"last field, {fs.label}") if fs else name

    s = view["summary"]
    ws = sheet("About", ["Item", "Value"], (30, 90))
    for k, v in [
        ["Test", view.get("title")], ["Device", view.get("device") or ", ".join(s.get("devices") or {})],
        ["Firmware", ", ".join(s.get("firmware") or {})], ["Spec", s["spec"]],
        ["In this file", f"{PACKET_SETS.get(which, which)}: {len(rows):,} of {s['packets']:,} packets"],
        ["Failed packets in the test", s["failed"]], ["Packets with warnings in the test", s["warned"]],
        ["First packet", _t(s.get("first"))], ["Last packet", _t(s.get("last"))], ["Written", _t(datetime.now().timestamp())],
        ["Packets", "One row per packet: when it came, what it is, its result, everything wrong with it, the packet as received"],
        ["Issues", "One row per finding: the field, the value expected and the one sent, the likely cause, where to look, the fix"],
        ["Checks explained", "Each kind of finding once: what the rule says, where it comes from, the cause, the fix, the impact if shipped"],
        ["<packet> fields", "Every field of each packet under its sheet name; a cell that broke its rule is coloured (red fail, amber warning)"],
        *_config_rows(s.get("config") or {}),
    ]:
        put(ws, [cell(ws, k, font=bold), cell(ws, v, wrapped=True)])

    ws = sheet("Packets", ["Packet", "Received", "Connection", "Device time", "Header", "Type", "Alert", "Status", "Frame",
                           "Result", "What is wrong", "Packet as received"], (8, 20, 22, 20, 8, 7, 7, 7, 9, 9, 70, 110))
    for r in rows:
        wrong = "\n".join(f"{LEVEL[f['level']]} · {f['label']}: {f['text']}" for f in r["flags"])
        put(ws, [r["n"], _t(r["received"]), r["conn"] or "", r["time"] or "", r["header"], r["type"] or "", r["alert"] or "",
                   r["status"] or "", r["frame"] or "", cell(ws, LEVEL[r["level"]], r["level"]), cell(ws, wrong, wrapped=True),
                   cell(ws, r["raw"])])

    ws = sheet("Issues", ["Packet", "Result", "Check", "Field", "Expected", "Sent", "Detail", "Likely cause", "Where to look",
                          "Fix", "Rule from"], (8, 9, 28, 26, 22, 18, 60, 60, 40, 50, 40))
    kinds: dict[tuple, dict] = {}
    for r in rows:
        for f in r["flags"]:
            why = _why(f)
            d = diag_for(f["key"], why)
            src = gate._field_source(r["header"], f["field"]) if f["check"] == "field" else CHECK_SOURCE.get(f["check"], "")
            put(ws, [r["n"], cell(ws, LEVEL[f["level"]], f["level"]), cell(ws, f["label"]),
                       cell(ws, field_of(r["header"], f["field"]) if f["field"] else ""), cell(ws, f["expected"] or ""),
                       cell(ws, f["got"] or ""), cell(ws, f["text"], wrapped=True), cell(ws, d["cause"], wrapped=True),
                       cell(ws, d["where"], wrapped=True), cell(ws, d["fix"], wrapped=True), cell(ws, src, wrapped=True)])
            k = kinds.get((f["key"], why))
            if k is None:
                about = (gate._field_about(r["header"], f["field"]) if f["check"] == "field"
                         else CHECKS.get(f["check"], ("", ""))[1])
                k = kinds[(f["key"], why)] = {"label": f["label"], "level": f["level"], "packets": set(), "first": r["n"],
                                              "about": about, "src": src, "d": d, "example": f["text"], "was": f.get("was")}
            k["packets"].add(r["n"])
            if LEVELS_ORDER[f["level"]] > LEVELS_ORDER[k["level"]]:
                k["level"], k["example"] = f["level"], f["text"]

    ws = sheet("Checks explained", ["Check", "Result", "Packets", "First packet", "What the rule says", "Rule from",
                                    "Likely cause", "Where to look", "Fix", "Impact if shipped", "Example"],
               (28, 9, 9, 12, 60, 40, 60, 40, 50, 50, 60))
    for k in sorted(kinds.values(), key=lambda k: (-LEVELS_ORDER[k["level"]], -len(k["packets"]))):
        about = k["about"] + (f" A warning for the {gate.spec.name} (its spec's levels)." if k["was"] else "")
        put(ws, [cell(ws, k["label"]), cell(ws, LEVEL[k["level"]], k["level"]), len(k["packets"]), k["first"],
                   cell(ws, about, wrapped=True), cell(ws, k["src"], wrapped=True), cell(ws, k["d"]["cause"], wrapped=True),
                   cell(ws, k["d"]["where"], wrapped=True), cell(ws, k["d"]["fix"], wrapped=True),
                   cell(ws, k["d"]["impact"], wrapped=True), cell(ws, k["example"], wrapped=True)])

    # every field, one sheet per kind of packet — the most common first
    by_header: dict[str, list[dict]] = {}
    for r in rows:
        if r["header"] != "?":
            by_header.setdefault(r["header"], []).append(r)
    for header, hrows in sorted(by_header.items(), key=lambda x: -len(x[1])):
        details = [gate.detail(r["n"]) for r in hrows]
        width = max((len(d["fields"]) for d in details), default=0)
        names: dict[int, str] = {}
        for d in details:
            for f in d["fields"]:
                if f["name"] and f["index"] not in names:
                    names[f["index"]] = f["label"]
        ws = sheet(f"{header} fields"[:31], ["Packet", "Result"] + [f"{i} {names.get(i, 'not in the sheet')}" for i in range(1, width + 1)],
                   (8, 9) + tuple(14 for _ in range(width)))
        for r, d in zip(hrows, details):
            put(ws, [r["n"], cell(ws, LEVEL[r["level"]], r["level"])] +
                      [cell(ws, f["value"], f["level"] if f["level"] != "pass" else None) for f in d["fields"]])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


LEVELS_ORDER = {"pass": 0, "warn": 1, "fail": 2}


def workbook(view: dict, gate) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    bold = Font(bold=True)

    def sheet(title, header, rows, widths=(), first=False, level_col=None):
        ws = wb.active if first else wb.create_sheet()
        ws.title = title
        ws.append(_xr(header))
        for c in ws[1]:
            c.font = bold
            c.fill = PatternFill("solid", fgColor="E6E6E6")
        for r in rows:
            ws.append(_xr(r))
            if level_col is not None:
                lv = {v: k for k, v in LEVEL.items()}.get(r[level_col])
                if lv in FILLS and lv != "pass":
                    ws.cell(ws.max_row, level_col + 1).fill = PatternFill("solid", fgColor=FILLS[lv])
        ws.freeze_panes = "A2"
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[ws.cell(1, i).column_letter].width = w
        return ws

    s = view["summary"]
    src = view.get("source") or {}
    where = f"Live on port {src.get('port')}" if src.get("kind") == "live" else f"File {src.get('name') or ''}"
    cks = s.get("checksum") or {}
    sheet("Summary", ["Item", "Value"], [
        ["Test", view.get("title")],
        ["Spec", s["spec"]],
        ["Verdict", VERDICT.get(s["verdict"], s["verdict"]) + (
            f" — so far: {VERDICT.get(s['outcome'], s['outcome'])}" if s["verdict"] == "in_progress" else "")],
        ["Device", view.get("device") or ", ".join(s.get("devices") or {})],
        ["Firmware", ", ".join(s.get("firmware") or {})],
        ["Source", where],
        ["First packet", _t(s.get("first"))],
        ["Last packet", _t(s.get("last"))],
        ["Packets", s["packets"]],
        ["Live / history", f"{s['live']} / {s['history']}"],
        ["Failed packets", s["failed"]],
        ["Packets with warnings", s["warned"]],
        ["Passed packets (no failure)", s["passed"]],
        ["Checksum correct / wrong / missing", f"{cks.get('ok', 0)} / {cks.get('bad', 0)} / {cks.get('missing', 0)}"],
        ["Scenarios seen", f"{s['scenarios_seen']} of {s['scenarios_total']}"],
        *[[f"Pinned: {k}", v] for k, v in (s.get("expect") or {}).items()],
        *_config_rows(s.get("config") or {}),
    ], widths=(34, 60), first=True)
    sheet("Checks", ["Check", "Result", "Packets", "First packet", "Last packet", "Example", "What it holds"],
          [[c["label"], LEVEL[c["level"]], c["packets"], c["first"], c["last"], c["example"], c["about"]]
           for c in s["checks"]], widths=(32, 10, 9, 12, 12, 70, 70), level_col=1)
    if view.get("defects"):
        defects_sheet(wb, view["defects"])
    sheet("Scenarios", ["Scenario", "Received", "Passed", "Failed", "First packet"],
          [[x["label"], x["count"], x["passed"], x["failed"], x["first"] or ""] for x in s["scenarios"]],
          widths=(28, 10, 10, 10, 12))
    flags = []
    for r in gate.rows:
        for f in r["flags"]:
            flags.append([r["n"], _t(r["received"]), r["time"] or "", r["header"], r["type"] or "", r["alert"] or "",
                          r["status"] or "", r["frame"] or "", LEVEL[f["level"]], f["label"], f["expected"] or "",
                          f["got"] or "", f["text"]])
    sheet("Flags", ["Packet", "Received", "Device time", "Header", "Type", "Alert", "Status", "Frame", "Result",
                    "Check", "Expected", "Got", "Detail"], flags,
          widths=(8, 20, 20, 8, 7, 7, 7, 9, 9, 30, 22, 16, 80), level_col=8)
    sheet("Packets", ["Packet", "Received", "Connection", "Device time", "Header", "Type", "Alert", "Status", "Frame",
                      "Result", "Flags", "Packet as received"],
          [[r["n"], _t(r["received"]), r["conn"] or "", r["time"] or "", r["header"], r["type"] or "", r["alert"] or "",
            r["status"] or "", r["frame"] or "", LEVEL[r["level"]], len(r["flags"]), r["raw"]] for r in gate.rows],
          widths=(8, 20, 22, 20, 8, 7, 7, 7, 9, 9, 7, 120), level_col=9)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ── the test plan in the tracker's own layout ─────────────────────────────────────────────────────
TRACKER_HEAD = ["ID", "Area", "What to test", "Dev owner", "Dev status", "Dev FW version", "Dev remarks",
                "Test owner", "Test status", "Test date", "Tested FW version", "Defect ID / remarks", "How", "Evidence",
                "Expected behaviour", "Acceptance criteria"]
STATUS_WORDS = {"pass": "Pass", "fail": "Fail", "partial": "Partial", "not_run": "Not run", "running": "Running",
                "blocked": "Blocked", "na": "Not applicable"}
STATUS_FILL = {"pass": "D9F2DD", "fail": "FBE0DA", "partial": "FDF0D5", "not_run": "FFF3C4", "running": "E3ECFB"}
HOW = {"automatic": "From packets", "assisted": "Bench step", "command": "By command", "manual": "By hand"}


def tracker_workbook(view: dict) -> bytes:
    """The plan as the tracker sheet: an area row, then its cases; results from the packets filled in."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Tracker"
    ws.append(_xr([f"{view['plan']} — firmware {view['firmware']}"
               + (f" — {view['out_of_scope']} cases out of scope, not listed" if view.get("out_of_scope") else "")]))
    ws["A1"].font = Font(bold=True, size=12)
    ws.append(_xr(TRACKER_HEAD))
    for c in ws[2]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="2BB39A")
    for area in view["areas"]:
        ws.append(_xr([area["id"], f"{area['title']}: {area['goal']}"]))
        for c in ws[ws.max_row]:
            c.font = Font(bold=True)
            c.fill = PatternFill("solid", fgColor="E6F6F2")
        group = None
        for case in area["cases"]:
            e, res = case["entry"], case["result"]
            st = res["status"]
            ws.append(_xr([case["id"], case["group"] if case["group"] != group else "", case["what"],
                       e.get("dev_owner", ""), e.get("dev_status", "Not started"), e.get("dev_fw", ""), e.get("dev_remarks", ""),
                       e.get("tester", ""), STATUS_WORDS.get(st, st), e.get("date", ""),
                       view["firmware"] if st not in ("not_run",) else "", e.get("remarks", ""),
                       HOW.get(case["kind"], case["kind"]), res.get("note", ""),
                       (case.get("about") or {}).get("expected", ""), (case.get("about") or {}).get("accept", "")]))
            group = case["group"]
            if st in STATUS_FILL:
                ws.cell(ws.max_row, 9).fill = PatternFill("solid", fgColor=STATUS_FILL[st])
    for i, w in enumerate((7, 22, 46, 12, 12, 12, 22, 12, 11, 12, 14, 40, 13, 70, 50, 60), 1):
        ws.column_dimensions[ws.cell(2, i).column_letter].width = w
    for row in ws.iter_rows(min_row=3):
        for c in row:
            c.alignment = Alignment(vertical="top", wrap_text=c.column in (3, 7, 12, 14, 15, 16))
    ws.freeze_panes = "D3"

    # the scorecard: how far the plan has got, by category
    sc = wb.create_sheet("Scorecard")
    sc.append(["Area", "Category", "Cases", "Pass", "Fail", "Partial", "Running", "Not run", "Run %", "Pass rate %"])
    for c in sc[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="2BB39A")
    total = {}
    for area in view["areas"]:
        n = {}
        for case in area["cases"]:
            st = case["result"]["status"]
            n[st] = n.get(st, 0) + 1
            total[st] = total.get(st, 0) + 1
        sc.append(_score_row(area["id"], area["title"], len(area["cases"]), n))
    sc.append(_score_row("", "All", sum(total.values()), total))
    if view.get("gate"):
        g = view["gate"]
        sc.append([])
        sc.append(["", "Release gate", "Ready" if g["ready"] else "Not ready"])
        for r in g["reasons"]:
            sc.append(["", r])
    for c in sc[sc.max_row]:
        c.font = Font(bold=True)
    for i, w in enumerate((7, 44, 8, 8, 8, 9, 9, 9, 8, 12), 1):
        sc.column_dimensions[sc.cell(1, i).column_letter].width = w
    if view.get("defects"):
        defects_sheet(wb, view["defects"])
    import io
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _score_row(aid: str, title: str, cases: int, n: dict) -> list:
    judged = n.get("pass", 0) + n.get("fail", 0) + n.get("partial", 0)
    run = cases - n.get("not_run", 0)
    return [aid, title, cases, n.get("pass", 0), n.get("fail", 0), n.get("partial", 0), n.get("running", 0),
            n.get("not_run", 0), round(100 * run / cases) if cases else 0,
            round(100 * n.get("pass", 0) / judged) if judged else ""]


def read_tracker(data: bytes) -> dict:
    """A tracker sheet back in: {case id: {dev_owner, dev_status, dev_fw, dev_remarks, tester, status, remarks}},
    columns found by their heading (the tracker's own, or this export's)."""
    import io
    import re

    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    names = {"dev owner": "dev_owner", "dev status": "dev_status", "dev fw version": "dev_fw", "dev remarks": "dev_remarks",
             "test owner": "tester", "test status": "status", "defect id / remarks": "remarks"}
    status = {"pass": "pass", "fail": "fail", "partial": "partial", "not run": "not_run", "blocked": "blocked",
              "not applicable": "na", "n/a": "na"}
    out: dict = {}
    for ws in wb.worksheets:
        cols = None
        for row in ws.iter_rows(values_only=True):
            cells = ["" if v is None else str(v).strip() for v in row]
            low = [c.lower() for c in cells]
            if "id" in low and "test status" in low:
                cols = {names[h]: i for i, h in enumerate(low) if h in names}
                idc = low.index("id")
                continue
            if not cols or not re.fullmatch(r"\d{2}\.\d{2}", cells[idc] if idc < len(cells) else ""):
                continue
            e = {}
            for key, i in cols.items():
                v = cells[i] if i < len(cells) else ""
                if key == "status":
                    v = status.get(v.lower(), "")
                if key == "dev_status" and v.lower() == "not started":
                    v = ""
                if v:
                    e[key] = v
            if e:
                out[cells[idc]] = e
    return out


DEFECT_HEAD = ["Defect", "Severity", "Area", "Check", "Packets", "Live", "History", "First packet", "Last packet",
               "Expected", "Got", "Cause", "Proved", "Where to look", "Fix", "Impact", "Verify with", "Example"]


def defects_sheet(wb, found: list[dict], title: str = "Defects") -> None:
    from openpyxl.styles import Alignment, Font, PatternFill
    ws = wb.create_sheet(title)
    ws.append(_xr(DEFECT_HEAD))
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="E6E6E6")
    fills = {"critical": "FBE0DA", "major": "FDF0D5", "minor": "EEF0F3"}
    for d in found:
        ws.append(_xr([d["id"], d["severity"].capitalize(), d["area_label"], d["label"], d["packets"], d["live"], d["history"],
                   d["first"], d["last"], d["expected"] or "", ", ".join(d["got"]), d["cause"], "Yes" if d["proved"] else "Likely",
                   d["where"], d["fix"], d["impact"], ", ".join(d["verify"]), d["example"]]))
        ws.cell(ws.max_row, 2).fill = PatternFill("solid", fgColor=fills[d["severity"]])
    for i, w in enumerate((12, 10, 22, 26, 9, 7, 8, 11, 11, 18, 18, 60, 8, 40, 50, 50, 18, 60), 1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = w
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(vertical="top", wrap_text=c.column in (12, 14, 15, 16, 18))
    ws.freeze_panes = "B2"
