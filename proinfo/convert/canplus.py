"""CanPlus signature parser + converter — MG / CanPlus ``*.Config.XML`` files.

A CanPlus signature file (exported by the CanPlus Editor) describes the CAN
parameters ("variables") for one vehicle: a human name, units, and the linear
scaling — multiplier / divider / offset and decimal places — used to convert a
raw CAN value into an engineering value. Each parameter also carries a binary
*descriptor* (``<VarDesc RawDataStr=...>``) that encodes the same scaling plus an
addressing byte.

This module turns such a file into normalized :class:`CanSignal` objects and can
export them as a JSON model or a CAN ``.dbc`` database.

Standalone-friendly: standard library only (``xml.etree``).

Descriptor binary layout — decoded from ``<VarDesc RawDataStr=...>`` and *verified*
against the file's own ``FW_Multiplier``/``FW_Divider`` attributes:

    byte 1        : CAN id low byte (tentative — SOC 0xAC matches PID 0x3AC)
    bytes 2..3 LE : VarID
    bytes 8..9 LE : multiplier
    bytes 10..11  : divider   (little-endian)
    bytes 12..13  : offset    (little-endian)

The field's start-bit / length inside the CAN payload is not in the file, so those
are exposed as editable defaults (start 0, length 16, big-endian) for the user to
correct in the Converter UI before exporting a DBC.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _bytes_from_rawstr(s: str) -> list:
    """'00-AC-3D-80' or '00 AC 3D 80' -> [0x00, 0xAC, 0x3D, 0x80]."""
    if not s:
        return []
    out = []
    for t in re.split(r"[-\s]+", s.strip()):
        if re.fullmatch(r"[0-9A-Fa-f]{1,2}", t):
            out.append(int(t, 16))
    return out


def _to_int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def extract_bits(data: bytes, start_bit: int, length: int,
                 byte_order: str = "big", signed: bool = False) -> int:
    """Extract ``length`` bits at ``start_bit`` from a CAN payload.

    ``big`` counts bits MSB-first (bit 0 = MSB of byte 0; Motorola). ``little``
    treats the payload as a little-endian integer and slices from ``start_bit``.
    """
    if byte_order == "little":
        whole = int.from_bytes(bytes(data), "little")
        raw = (whole >> start_bit) & ((1 << length) - 1)
    else:
        whole = int.from_bytes(bytes(data), "big")
        shift = len(data) * 8 - (start_bit + length)
        if shift < 0:
            raise ValueError("field exceeds payload length")
        raw = (whole >> shift) & ((1 << length) - 1)
    if signed and raw & (1 << (length - 1)):
        raw -= 1 << length
    return raw


# --------------------------------------------------------------------------- #
# model
# --------------------------------------------------------------------------- #
@dataclass
class CanSignal:
    name: str
    var_id: str
    units: str = ""
    multiplier: int = 1
    divider: int = 1
    offset: int = 0
    decimals: int = 2
    function: str = ""
    can_id_low: int | None = None     # descriptor byte 1 (tentative CAN id low byte)
    # editable conversion fields (defaults — corrected in the Converter UI):
    can_id: int | None = None         # full CAN id for the message this signal lives in
    start_bit: int = 0
    length: int = 16
    byte_order: str = "big"           # "big" (Motorola) | "little" (Intel)
    signed: bool = False

    @property
    def factor(self) -> float:
        return self.multiplier / (self.divider or 1)

    def scale(self, raw_value):
        val = raw_value * self.multiplier / (self.divider or 1) + self.offset
        return int(round(val)) if self.decimals <= 0 else round(val, self.decimals)

    def decode(self, can_bytes: bytes, start_bit=None, length=None,
               byte_order=None, signed=None):
        raw = extract_bits(can_bytes,
                           self.start_bit if start_bit is None else start_bit,
                           self.length if length is None else length,
                           self.byte_order if byte_order is None else byte_order,
                           self.signed if signed is None else signed)
        return {"name": self.name, "var_id": self.var_id, "raw": raw,
                "value": self.scale(raw), "unit": self.units}

    def to_dict(self) -> dict:
        return {"var_id": self.var_id, "name": self.name, "unit": self.units,
                "multiplier": self.multiplier, "divider": self.divider,
                "offset": self.offset, "decimals": self.decimals,
                "function": self.function, "can_id_low": self.can_id_low,
                "can_id": self.can_id, "start_bit": self.start_bit,
                "length": self.length, "byte_order": self.byte_order,
                "signed": self.signed}


@dataclass
class CanSignature:
    vehicle: dict = field(default_factory=dict)
    signals: list = field(default_factory=list)

    def by_var_id(self, var_id: str):
        v = var_id.upper().lstrip("0X")
        return next((s for s in self.signals if s.var_id.upper() == v), None)

    def by_name(self, text: str):
        t = text.lower()
        return next((s for s in self.signals if t in s.name.lower()), None)


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #
def _signal_from_variable(var: ET.Element, desc_bytes: list | None) -> CanSignal:
    a = var.attrib
    desc_bytes = desc_bytes or []
    low = desc_bytes[1] if len(desc_bytes) > 1 else None
    return CanSignal(
        name=a.get("Title", "").strip(),
        var_id=a.get("VarID", "").strip().upper(),
        units=a.get("FW_Units", "").strip(),
        multiplier=_to_int(a.get("FW_Multiplier"), 1),
        divider=_to_int(a.get("FW_Divider"), 1),
        offset=_to_int(a.get("FW_Offset"), 0),
        decimals=_to_int(a.get("Decimal_Places"), 2),
        function=a.get("Var_Function", "").strip(),
        can_id_low=low,
        can_id=low,                   # editable default (tentative)
    )


def parse(xml_text: str) -> CanSignature:
    """Parse CanPlus signature XML into a :class:`CanSignature`."""
    root = ET.fromstring(xml_text)
    sig = CanSignature()

    info = root.find(".//VehicleLibraryInfo/Vehicle")
    if info is not None:
        a = info.attrib
        sig.vehicle = {"brand": a.get("Brand", ""), "model": a.get("ModelName", ""),
                       "year": a.get("ProductionYear", ""), "type": a.get("Type", "")}

    seen = {}
    for vd in root.findall(".//Variable_Descriptors/VarDesc"):
        var = vd.find("Variable")
        if var is None:
            continue
        s = _signal_from_variable(var, _bytes_from_rawstr(vd.get("RawDataStr", "")))
        if s.var_id:
            seen[s.var_id] = s
    for var in root.findall(".//Free_Variables/Variable") + root.findall(".//J1939/Variable"):
        vid = var.get("VarID", "").strip().upper()
        if vid and vid not in seen:
            seen[vid] = _signal_from_variable(var, None)

    sig.signals = sorted(seen.values(), key=lambda s: s.var_id)
    return sig


def load(path_or_xml) -> CanSignature:
    s = str(path_or_xml)
    if "<" not in s[:256] and Path(s).exists():
        s = Path(s).read_text(encoding="utf-8", errors="replace")
    return parse(s)


# --------------------------------------------------------------------------- #
# export
# --------------------------------------------------------------------------- #
def to_model(sig: CanSignature) -> dict:
    """The editable JSON model the Converter UI round-trips."""
    return {"format": "canplus", "vehicle": sig.vehicle,
            "signals": [s.to_dict() for s in sig.signals]}


def _num(x) -> str:
    f = float(x)
    return str(int(f)) if f.is_integer() else f"{f:.6g}"


def _dbc_name(name: str, used: set) -> str:
    n = re.sub(r"[^0-9A-Za-z_]", "_", name).strip("_") or "SIG"
    if n[0].isdigit():
        n = "_" + n
    base, i = n, 2
    while n in used:
        n = f"{base}_{i}"; i += 1
    used.add(n)
    return n


def model_to_dbc(model: dict, dlc: int = 8) -> str:
    """Render the (possibly user-edited) model as a CAN ``.dbc`` database."""
    signals = model.get("signals", [])
    veh = model.get("vehicle", {})
    tag = re.sub(r"[^0-9A-Za-z]", "", f"{veh.get('brand','')}{veh.get('model','')}") or "VEH"

    groups: dict = {}
    for s in signals:
        cid = s.get("can_id")
        groups.setdefault(cid if isinstance(cid, int) else -1, []).append(s)

    lines = ['VERSION ""', "", "NS_ :", "", "BS_:", "", "BU_ Tool", ""]
    used_msg = set()
    for cid in sorted(groups, key=lambda c: (c < 0, c)):
        msg_id = cid if cid >= 0 else 0
        mname = _dbc_name(f"{tag}_{msg_id:X}" if cid >= 0 else f"{tag}_UNASSIGNED", used_msg)
        lines.append(f"BO_ {msg_id} {mname}: {dlc} Tool")
        used_sig = set()
        for s in groups[cid]:
            order = 1 if s.get("byte_order") == "little" else 0
            sign = "-" if s.get("signed") else "+"
            sname = _dbc_name(s.get("name") or s.get("var_id") or "SIG", used_sig)
            factor = _num((s.get("multiplier", 1)) / (s.get("divider", 1) or 1))
            off = _num(s.get("offset", 0))
            unit = (s.get("unit") or "").replace('"', "'")
            lines.append(f' SG_ {sname} : {int(s.get("start_bit", 0))}|{int(s.get("length", 16))}'
                         f'@{order}{sign} ({factor},{off}) [0|0] "{unit}" Tool')
        lines.append("")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _main(argv: list) -> int:
    import json
    import sys
    if not argv:
        print("usage: python -m proinfo.convert.canplus <signature.xml> [--dbc|--json]")
        return 2
    sig = load(argv[0])
    fmt = argv[1] if len(argv) > 1 else "--table"
    if fmt == "--dbc":
        sys.stdout.write(model_to_dbc(to_model(sig)))
    elif fmt == "--json":
        print(json.dumps(to_model(sig), indent=2))
    else:
        print(f"# {sig.vehicle}  —  {len(sig.signals)} signals")
        for s in sig.signals:
            cid = f"{s.can_id_low:02X}" if s.can_id_low is not None else "--"
            print(f"{s.var_id:<6} CANlo={cid}  {s.multiplier}/{s.divider}  {s.units:<6} {s.name}")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv[1:]))
