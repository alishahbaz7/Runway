"""An analysis as an Excel workbook: the summary and findings first, then one sheet per detail table."""

from __future__ import annotations

import io

from ..xlsx_text import row as _xr

LEVEL_WORDS = {"ok": "Good", "info": "Note", "warn": "Attention", "bad": "Poor"}
FILLS = {"ok": "E3F6EC", "info": "EEF0F3", "warn": "FDF0D5", "bad": "FBE0DA"}


def _t(s):
    return (s or "").replace("T", " ")


MODE_HEAD = ["Mode", "Expected every (s)", "From", "Seen most (s)", "Time (s)", "Times entered", "Packets", "Due",
             "Difference", "Packets / due %", "Status", "Live", "History", "History %",
             "Interval avg (s)", "Interval median (s)", "Interval min (s)", "Interval max (s)", "Jitter (s)",
             "On time %", "Early", "Late", "Silences over 1.5×", "Missed (estimated)",
             "Delay avg (s)", "Delay median (s)", "Delay min (s)", "Delay max (s)", "Delay 90% (s)",
             "Delay live median (s)", "Delay history median (s)", "Clock ahead", "Notes"]


def mode_row(g: dict, src: dict) -> list:
    gen, d = g.get("gen") or {}, g.get("delay") or {}
    return [g["label"], g.get("interval_s"), src.get(g.get("source"), ""), g.get("seen_s"), g.get("seconds"), g.get("periods"),
            g.get("packets"), g.get("expected"), g.get("diff"), g.get("sufficiency_pct"), LEVEL_WORDS.get(g.get("level"), ""),
            g.get("live"), g.get("history"), g.get("history_pct"),
            gen.get("avg"), gen.get("median"), gen.get("min"), gen.get("max"), gen.get("jitter"),
            g.get("on_time_pct"), g.get("early"), g.get("late"), g.get("missed_steps"), g.get("missed"),
            d.get("avg"), d.get("median"), d.get("min"), d.get("max"), d.get("p90"),
            (g.get("delay_live") or {}).get("median"), (g.get("delay_history") or {}).get("median"), g.get("clock_ahead"),
            " · ".join(g.get("notes") or [])]


def workbook(rep: dict) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    bold = Font(bold=True)

    def sheet(title, header, rows, widths=None, first=False):
        ws = wb.active if first else wb.create_sheet()
        ws.title = title
        ws.append(_xr(header))
        for c in ws[1]:
            c.font = bold
            c.fill = PatternFill("solid", fgColor="E6E6E6")
        for r in rows:
            ws.append(_xr(r))
        ws.freeze_panes = "A2"
        for i, w in enumerate(widths or [], 1):
            ws.column_dimensions[ws.cell(1, i).column_letter].width = w
        return ws

    m, s = rep.get("meta", {}), rep.get("summary", {})
    ws = sheet("Summary", ["Item", "Value"], [
        ["Device", m.get("device")],
        ["Trip", f"{_t(rep['scope']['start'])} – {_t(rep['scope']['end'])}"] if rep.get("scope")
        else ["Period", f"{_t(m.get('from'))} – {_t(m.get('to'))}"],
        ["First row", _t(s.get("first"))], ["Last row", _t(s.get("last"))], ["Rows", s.get("rows")],
        ["Usual step (s)", s.get("step_s")], ["Status", rep.get("status_word")], ["Issues", rep.get("issues")],
    ] + [[a["label"], f"{a['status_word']} · {a.get('figure', '')} {a.get('caption', '')}"] for a in rep.get("areas", [])],
        [24, 70], first=True)
    if rep.get("scorecard"):
        ws = sheet("Scorecard", ["Group", "Measure", "Value", "Unit", "Status", "Attention · poor at"],
                   [[x["group"], x["label"], x["value"], x["unit"], LEVEL_WORDS.get(x.get("level"), ""), x.get("limit", "")]
                    for x in rep["scorecard"]], [12, 44, 12, 8, 12, 40])
        for i, x in enumerate(rep["scorecard"], start=2):
            if x.get("level") in FILLS and x["level"] != "info":
                ws.cell(i, 3).fill = PatternFill("solid", fgColor=FILLS[x["level"]])
    gen = rep.get("generation") or {}
    src = {"profile": f"profile {((gen.get('profile') or {}).get('name') or '')}".strip(), "log": "this log"}
    if gen.get("modes"):
        sheet("Modes", MODE_HEAD, [mode_row(g, src) for g in gen["modes"]]
              + [[], ["Standby lasts (min)", (gen.get("values") or {}).get("standby_min"),
                      src.get((gen.get("source") or {}).get("standby_min"), "")]],
              [12] + [11] * (len(MODE_HEAD) - 2) + [100])
    ws = sheet("Findings", ["Status", "Area", "Finding", "Value", "Unit"],
               [[LEVEL_WORDS.get(f["level"], f["level"]), f["area"], f["text"], f.get("value"), f.get("unit")]
                for f in rep.get("findings", [])], [12, 14, 100, 12, 8])
    for row in ws.iter_rows(min_row=2):
        lvl = next((k for k, v in LEVEL_WORDS.items() if v == row[0].value), None)
        if lvl:
            row[0].fill = PatternFill("solid", fgColor=FILLS[lvl])
        row[2].alignment = Alignment(wrap_text=True, vertical="top")
    d = rep.get("driving", {})
    sheet("Trips", ["#", "Start", "End", "Duration", "Distance (km)", "Top speed", "Average speed", "From", "To"],
          [[t["n"], _t(t["start"]), _t(t["end"]), t["duration"], t["km"], t["max_speed"], t["avg_speed"],
            f"{t['from'][0]}, {t['from'][1]}", f"{t['to'][0]}, {t['to'][1]}"] for t in d.get("trips", [])],
          [5, 20, 20, 12, 13, 10, 13, 22, 22])
    pos = lambda q: f"{q[0]:.6f}, {q[1]:.6f}" if q else ""
    sheet("Gaps", ["Start", "End", "Duration", "Kind", "Ignition before", "Speed before", "Speed after",
                   "From (position)", "To (position)", "Apart (m)"],
          [[_t(g["start"]), _t(g["end"]), g["duration"], g["kind"], g["ign_before"], g["speed_before"], g["speed_after"],
            pos(g.get("from")), pos(g.get("to")), g.get("metres")]
           for g in rep.get("gaps", [])], [20, 20, 12, 10, 15, 13, 12, 22, 22, 10])
    fr = rep.get("frames", {})
    if fr.get("numbered"):
        sheet("Frame holes", ["After frame", "Before frame", "Missing", "Time", "Periodic"],
              [[h["after"], h["before"], h["missing"], _t(h["time"]), "yes" if h["periodic"] else ""]
               for h in fr.get("holes", [])], [12, 12, 10, 20, 10])
    g = rep.get("gps", {})
    sheet("GPS jumps", ["Kind", "From time", "Time", "Metres", "km/h", "From", "To", "Satellites"],
          [[x["kind"], _t(x["from_time"]), _t(x["time"]), x["metres"], x["kmh"], f"{x['from'][0]}, {x['from'][1]}",
            f"{x['to'][0]}, {x['to'][1]}", f"{x['sats'][0]} → {x['sats'][1]}"] for x in g.get("stale", []) + g.get("jumps", [])],
          [8, 20, 20, 10, 8, 22, 22, 12])
    ev = d.get("events", [])
    ov = d.get("overspeed", [])
    sheet("Events", ["Kind", "Time", "Code / top speed", "Value / duration", "Speed", "Position"],
          [["Driving event", _t(e["time"]), e["code"], e["value"], e["speed"], f"{e['at'][0]}, {e['at'][1]}"] for e in ev] +
          [["Overspeed", _t(e["start"]), e["max"], e["duration"], "", f"{e['at'][0]}, {e['at'][1]}"] for e in ov],
          [14, 20, 16, 16, 8, 24])
    dv = rep.get("device", {})
    if dv.get("alerts") or (fr.get("starts") or []):
        sheet("Device", ["Kind", "Time", "Alert / frame", "Ignition", "Speed", "Supply V", "Note"],
              [["Start", _t(x["time"]), x["frame"], "", "", "", "log starts here" if x.get("first_row") else
                ", ".join(f"{k} {v}" for k, v in (x.get("boot") or {}).items()) or "restart"] for x in fr.get("starts", [])] +
              [["Alert", _t(a["time"]), a["name"], "" if a["ign"] is None else ("on" if a["ign"] else "off"), a["speed"],
                a["supply"], ""] for a in dv.get("alerts", [])], [8, 20, 34, 9, 8, 9, 40])
    pw = rep.get("power", {})
    missing = [["Main battery", e] for e in pw.get("cuts", [])] + [["Internal battery", e] for e in pw.get("internal_missing", [])]
    if missing:
        sheet("Battery missing", ["Battery", "From", "To", "Duration", "Rows", "Lowest V"],
              [[kind, _t(e["start"]), _t(e["end"]), e.get("duration", ""), e.get("rows"),
                e.get("lowest") if kind == "Main battery" else 0] for kind, e in missing], [16, 20, 20, 14, 8, 10])
    sheet("Packet types", ["Type", "Rows", "%", "First", "Last", "With position %", "Moving %", "History", "Columns filled"],
          [[t["name"], t["rows"], t["pct"], _t(t["first"]), _t(t["last"]), t["with_position_pct"], t["moving_pct"],
            t["history"], ", ".join(t["columns"])] for t in rep.get("types", [])], [10, 8, 6, 20, 20, 14, 10, 8, 90])
    sheet("Columns", ["Column", "Filled %", "Distinct", "Min", "Max", "Most common"],
          [[c["column"], c["filled_pct"], c["distinct"], c.get("min"), c.get("max"),
            "; ".join(f"{v} ×{n}" for v, n in c.get("top", []))] for c in rep.get("columns", [])],
          [26, 10, 10, 12, 12, 70])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def comparison(c: dict) -> bytes:
    """A comparison as a workbook: the scorecard (best green, worst red), each device's modes, the devices."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    bold = Font(bold=True)
    devs = c.get("devices", [])
    names = [d.get("device") or d.get("file") or d.get("id") for d in devs]
    ws = wb.active
    ws.title = "Scorecard"
    w = c.get("window", {})
    ws.append(_xr(["Compared over", f"{_t(w.get('start'))} – {_t(w.get('end'))}" + ("" if w.get("common") else " (no time in common: each whole log)")]))
    ws.append(_xr([]))
    ws.append(_xr(["Group", "Measure", "Unit"] + names))
    for cell in ws[3]:
        cell.font = bold
        cell.fill = PatternFill("solid", fgColor="E6E6E6")
    for m in c.get("metrics", []):
        ws.append(_xr([m["group"], m["label"], m["unit"]] + [v for v in m["values"]]))
        row = ws.max_row
        for idxs, tone in ((m.get("best") or [], "ok"), (m.get("worst") or [], "bad")):
            for idx in idxs:
                ws.cell(row, 4 + idx).fill = PatternFill("solid", fgColor=FILLS[tone])
    ws.freeze_panes = "D4"
    for col, width in zip("ABC", (12, 44, 8)):
        ws.column_dimensions[col].width = width
    for i in range(len(names)):
        ws.column_dimensions[ws.cell(3, 4 + i).column_letter].width = 16
    ws = wb.create_sheet("Modes")
    ws.append(_xr(["Device"] + MODE_HEAD))
    for cell in ws[1]:
        cell.font = bold
    for d in devs:
        for g in d.get("modes", []):
            ws.append(_xr([d.get("device")] + mode_row(g, {"profile": "profile", "log": "this log"})))
    ws = wb.create_sheet("Devices")
    ws.append(_xr(["Device", "File", "First row", "Last row", "Status", "Issues", "Firmware", "Profile"]))
    for cell in ws[1]:
        cell.font = bold
    for d in devs:
        ws.append(_xr([d.get("device"), d.get("file"), _t(d.get("first")), _t(d.get("last")), d.get("status_word"),
                   d.get("issues"), ", ".join(d.get("firmware") or []), d.get("profile") or ""]))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
