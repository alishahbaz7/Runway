"""A device's protocol sheet in — a spec out; and a test held against what the platform stored.

    read_sheet(xlsx bytes) -> [{"label", "datatype", "example"}]   the sheet's field rows, in order
    spec_from_sheet(spec_dict, header, rows, source) -> (new spec_dict, diff)
        the packet ``header``'s field list rebuilt from the sheet: each field's rules from its datatype
        (Integer → a whole number, Float → a number, a date / time / IMEI by its name), the rules a field
        of the same name already had kept; ``diff`` says what was added, removed or changed
    platform_match(gate rows, drive-log rows) -> result
        the device's location packets against the rows the platform stored, matched by the device's
        time: packets missing on the platform, and position / speed / ignition read differently

Pure: bytes and dicts in, dicts out.
"""

from __future__ import annotations

import io
import re
from datetime import timedelta, timezone

FRAMING = {"start character", "header", "end character", "checksum", "separator", "character"}
IST = timezone(timedelta(hours=5, minutes=30))     # a drive log's times without a zone are India's


def _slug(text: str) -> str:
    s = re.sub(r"\(.*?\)", "", text.lower())
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "field"


def read_sheet(data: bytes) -> list[dict]:
    """The field rows of a protocol sheet (columns found by heading: Field, Datatype, and the example)."""
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    for ws in wb.worksheets:
        cols = None
        rows = []
        for row in ws.iter_rows(values_only=True):
            cells = ["" if v is None else str(v).strip() for v in row]
            low = [c.lower() for c in cells]
            if cols is None:
                if "field" in low:
                    fi = low.index("field")
                    di = next((i for i, c in enumerate(low) if c in ("datatype", "data type", "type")), None)
                    ei = next((i for i, c in enumerate(low) if c in ("packet", "example", "value")), None)
                    cols = (fi, di, ei)
                continue
            fi, di, ei = cols
            label = cells[fi] if fi < len(cells) else ""
            if not label:
                continue
            rows.append({"label": label, "datatype": (cells[di] if di is not None and di < len(cells) else ""),
                         "example": (cells[ei] if ei is not None and ei < len(cells) else "")})
        if rows:
            return rows
    raise ValueError("no sheet with a Field column")


def _rules(label: str, datatype: str) -> dict:
    name, dt = label.lower(), datatype.lower()
    if "imei" in name:
        return {"imei": True, "expect": "imei"}
    if re.search(r"\bdate\b", name):
        return {"date": "DDMMYYYY"}
    if re.search(r"\btime\b", name) and "update" not in name:
        return {"time": "hhmmss"}
    if dt.startswith("int"):
        return {"int": True}
    if dt.startswith(("float", "decimal", "double")):
        return {"decimal": True}
    return {}


def spec_from_sheet(spec: dict, header: str, rows: list[dict], source: str) -> tuple[dict, dict]:
    old = ((spec.get("packets") or {}).get(header) or {}).get("field_list") or []
    by_name = {f["name"]: f for f in old}
    fields, seen = [], set()
    for r in rows:
        if r["label"].strip().lower() in FRAMING:
            continue
        name = _slug(r["label"])
        while name in seen:
            name += "_2"
        seen.add(name)
        prev = by_name.get(name)
        idx = len(fields) + 1
        f = {"index": idx, "name": name, "label": r["label"],
             "rules": dict(prev["rules"]) if prev else _rules(r["label"], r["datatype"]),
             "source": f"{source}, field {idx}"}
        if prev and prev.get("allow"):
            f["allow"] = prev["allow"]
        fields.append(f)
    if fields and fields[-1]["rules"] == {} and fields[-1]["label"].lower().startswith("ota"):
        fields[-1]["index"] = -1
    old_names = [f["name"] for f in old]
    new_names = [f["name"] for f in fields]
    diff = {"added": [f["label"] for f in fields if f["name"] not in by_name],
            "removed": [f.get("label") or f["name"] for f in old if f["name"] not in new_names],
            "moved": [f["label"] for f in fields if f["name"] in by_name and old_names.index(f["name"]) + 1 != f["index"]
                      and by_name[f["name"]]["index"] > 0],
            "fields": len(fields), "was": len(old)}
    out = dict(spec)
    packets = dict(out.get("packets") or {})
    pk = dict(packets.get(header) or {"label": header})
    pk["field_list"] = fields
    pk["fields"] = len(fields)
    pk["source"] = source
    packets[header] = pk
    out["packets"] = packets
    return out, diff


def platform_match(rows: list[dict], log_rows: list[dict]) -> dict:
    """Runway's location packets against the platform's drive log, over the time both cover."""
    def epoch(t):
        if t is None:
            return None
        return (t if t.tzinfo else t.replace(tzinfo=IST)).timestamp()
    mine = {int(r["t"]): r for r in rows if r["header"] == "PVT" and r.get("t") is not None}
    theirs = {}
    for r in log_rows:
        e = epoch(r.get("t"))
        if e is not None:
            theirs[int(e)] = r
    if not mine or not theirs:
        return {"status": "not_run", "note": "No location packets, or no rows in the platform's log"}
    lo, hi = max(min(mine), min(theirs)), min(max(mine), max(theirs))
    if hi < lo:
        return {"status": "not_run", "note": "The test and the platform's log do not overlap in time"}
    span = [t for t in mine if lo <= t <= hi]
    missing = [t for t in span if t not in theirs]
    differ = []
    for t in span:
        p = theirs.get(t)
        if p is None:
            continue
        r = mine[t]
        bad = []
        if r.get("lat") is not None and p.get("lat") is not None and abs(r["lat"] - p["lat"]) > 1e-4:
            bad.append("latitude")
        if r.get("lon") is not None and p.get("lon") is not None and abs(r["lon"] - p["lon"]) > 1e-4:
            bad.append("longitude")
        if r.get("speed") is not None and p.get("speed") is not None and abs(r["speed"] - p["speed"]) > 1:
            bad.append("speed")
        if r.get("ign") in ("0", "1") and p.get("ign") is not None and int(r["ign"]) != int(p["ign"]):
            bad.append("ignition")
        if bad:
            differ.append((t, bad))
    words = f"{len(span):,} packets in the time both cover"
    if missing or differ:
        parts = []
        if missing:
            parts.append(f"{len(missing):,} not on the platform")
        if differ:
            parts.append(f"{len(differ):,} read differently ({', '.join(sorted({b for _, x in differ for b in x}))})")
        return {"status": "fail", "note": f"{words}: " + "; ".join(parts), "missing": len(missing), "differ": len(differ),
                "span": [lo, hi]}
    return {"status": "pass", "note": f"{words}: every one on the platform, read the same", "missing": 0, "differ": 0,
            "span": [lo, hi]}
