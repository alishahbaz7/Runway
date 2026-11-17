"""Read a platform drive-log CSV into plain rows.

The export comes in many column layouts (12 to 92 columns seen); only a dozen are always there.
So columns are found by name through COLUMNS — one row per column the analysis understands, with
the field it becomes and how its text is read. A column not listed is kept as it is, for the
column table. Adding a column the analysis should use is a row here, not code.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable

csv.field_size_limit(1 << 30)


def _float(s: str) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _int(s: str) -> int | None:
    v = _float(s)
    return None if v is None else int(v)


def _time(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s.strip())
    except (AttributeError, ValueError):
        return None


def _bool(s: str) -> bool | None:
    s = (s or "").strip().lower()
    return True if s == "true" else False if s == "false" else None


_KV = re.compile(r"([A-Za-z_][\w]*)\s*:?=\s*([^,}]*)")


def _kv(s: str) -> dict[str, str]:
    """'{hvBatteryTemp=38.0, AISPacketType=L}' → {'hvBatteryTemp': '38.0', 'AISPacketType': 'L'}.
    An IMU block (imu=(…)) is skipped here; the IMU check reads it from the raw text."""
    if not s:
        return {}
    s = re.sub(r"imu=\([^)]*\)", "", s)
    return {k: v.strip() for k, v in _KV.findall(s)}


def _text(s: str) -> str:
    return (s or "").strip()


READERS: dict[str, Callable[[str], Any]] = {
    "float": _float, "int": _int, "time": _time, "bool": _bool, "kv": _kv, "text": _text,
}

# column in the export → (field in a row, how its text is read)
COLUMNS: dict[str, tuple[str, str]] = {
    "timestampStr": ("t", "time"),               # the device's time
    "insertTimeStr": ("ins", "time"),            # when the platform stored it
    "speed": ("speed", "float"),
    "latitude": ("lat", "float"),
    "longitude": ("lon", "float"),
    "numberOfSatellites": ("sats", "int"),
    "gsmlevel": ("gsm", "int"),
    "type": ("type", "text"),
    "digitalInput1": ("ign", "int"),             # ignition
    "powerSupplyVoltage": ("supply", "float"),   # mV in this export
    "internalBatteryVoltage": ("ibatt_v", "float"),
    "internalBatteryLevel": ("ibatt_pct", "float"),
    "power": ("power", "int"),
    "history": ("history", "bool"),
    "altitude": ("alt", "float"),
    "canSpeed": ("can_speed", "float"),
    "greenDriveType": ("green", "text"),
    "greenDriveValue": ("green_value", "text"),
    "panic": ("panic", "int"),
    "mcc": ("mcc", "text"),
    "mnc": ("mnc", "text"),
    "cellId": ("cell", "text"),
    "canData": ("can", "kv"),
    "otherData": ("other", "text"),             # custom payloads (IMU) and the AIS-140 alert (PacketType, alertId)
    "meta_data": ("meta", "kv"),
    "firmwareVersion": ("firmware", "text"),
    "deviceResponse": ("response", "text"),      # a command's answer; after a boot, the boot facts (RST:…)
}
REQUIRED = ("timestampStr", "latitude", "longitude")

# the platform's export file name: <device>_<from>_<to>_driveLogs.csv
_NAME = re.compile(r"^(?P<device>[^_]+)_(?P<frm>[A-Z][a-z]{2} [A-Z][a-z]{2} \d{2} \d{2}_\d{2}_\d{2} \w+ \d{4})_"
                   r"(?P<to>[A-Z][a-z]{2} [A-Z][a-z]{2} \d{2} \d{2}_\d{2}_\d{2} \w+ \d{4})_(?P<kind>\w+?)\.csv$", re.I)


def _name_time(s: str) -> datetime | None:
    # 'Mon Nov 16 00_00_00 IST 2026' → 2026-11-16 00:00:00 (the zone is dropped: times in the file are local too)
    try:
        p = s.replace("_", ":").split()
        return datetime.strptime(f"{p[1]} {p[2]} {p[5]} {p[3]}", "%b %d %Y %H:%M:%S")
    except (IndexError, ValueError):
        return None


def parse_name(filename: str) -> dict:
    """What the file name says: the device, the period asked for, the report kind."""
    m = _NAME.match((filename or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1])
    if not m:
        return {}
    return {"device": m["device"], "from": _name_time(m["frm"]), "to": _name_time(m["to"]), "kind": m["kind"]}


@dataclass
class DriveLog:
    rows: list[dict]                 # one per packet, in device-time order; each has "i" (its file row)
    columns: list[str]               # the export's columns, in order
    raw: list[list[str]]             # the export's text per row, aligned with columns (column table, row view)
    name: dict = field(default_factory=dict)
    size: int = 0                    # bytes


def is_drive_log(header: Iterable[str]) -> bool:
    h = set(header)
    return all(c in h for c in REQUIRED) and ("insertTimeStr" in h or "speed" in h)


def read(data: bytes | str, filename: str = "") -> DriveLog:
    """The export's bytes (or text) → a DriveLog. Raises ValueError when it is not a drive log."""
    text = data.decode("utf-8-sig", errors="replace") if isinstance(data, (bytes, bytearray)) else data
    reader = csv.DictReader(io.StringIO(text, newline=""))
    columns = [c for c in (reader.fieldnames or []) if c is not None]
    if not is_drive_log(columns):
        raise ValueError("Not a drive log: no timestampStr, latitude and longitude columns")
    known = [(c, f, READERS[k]) for c, (f, k) in COLUMNS.items() if c in columns]
    rows, raw = [], []
    unread, first_bad = 0, None
    for i, rec in enumerate(reader):
        r: dict[str, Any] = {"i": i}
        for c, f, read_ in known:
            r[f] = read_(rec.get(c) or "")
        if r.get("t") is None:
            unread += 1
            if first_bad is None and (rec.get("timestampStr") or "").strip():
                first_bad = rec["timestampStr"].strip()
            continue
        if r.get("other"):
            r["od"] = _kv(r["other"])                # PacketType, alertId, deviceTamper …
        rows.append(r)
        raw.append([rec.get(c) or "" for c in columns])
    if not rows and first_bad is not None:
        # Excel, saving the file again, writes the times as it shows them — "16-11-2026 18:07", the
        # seconds gone; an analysis of that would be wrong, so say so instead of reading nothing
        raise ValueError(f"The device times (timestampStr) cannot be read — the first is “{first_bad[:40]}”. "
                         f"A file saved again from Excel loses its seconds: use the export as downloaded")
    order = sorted(range(len(rows)), key=lambda k: (rows[k]["t"], k))   # device time; file order breaks ties
    rows = [rows[k] for k in order]
    raw = [raw[k] for k in order]
    for k, r in enumerate(rows):
        r["k"] = k                                       # position in device-time order
    return DriveLog(rows=rows, columns=columns, raw=raw, name=parse_name(filename),
                    size=len(data) if isinstance(data, (bytes, bytearray)) else len(text.encode()))
