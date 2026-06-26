"""Field-by-field Teltonika packet explainer for the interactive parser UI.

This is an *inspection* tool, separate from the streaming :class:`TeltonikaCodec`:
the codec normalizes bytes into ``UnifiedRecord``s for the pipeline, whereas this walks
the same bytes and emits a flat list of every field - ``{group, name, size, value, hex,
bit}`` - so a UI can render the breakdown the way the official Teltonika parser does.
Crucially, IO elements are labelled with their **parameter name** (via
:mod:`.teltonika_ids`), not just the raw AVL id.

It accepts a buffer of one *or many* concatenated packets and handles both TCP framing
(``00000000`` preamble + CRC) and the UDP channel framing, plus the IMEI handshake and
every codec the :class:`TeltonikaCodec` decodes (8 / 8E / 16 and GPRS 12 / 13 / 14).
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..core.crc import crc16_ibm
from .teltonika import AVL_SPECS
from .teltonika_ids import avl_param

CODEC_NAMES = {
    0x08: "Codec 8",
    0x8E: "Codec 8 Extended",
    0x10: "Codec 16",
    0x0C: "Codec 12 (GPRS command)",
    0x0D: "Codec 13 (GPRS)",
    0x0E: "Codec 14 (GPRS command, IMEI)",
}
# IO ids in this inclusive range are vendor/raw parameters - show the value as raw hex,
# never decoded to an integer.
RAW_HEX_IO_LOW, RAW_HEX_IO_HIGH = 900, 930

PRIORITY = {0: "Low", 1: "High", 2: "Panic"}
GENERATION = {0: "On Exit", 1: "On Entrance", 2: "On Both", 3: "Reserved",
              4: "Hysteresis", 5: "On Change", 6: "Eventual", 7: "Periodical"}
TYPE_NAMES = {0x05: "command", 0x06: "response"}


def _iso_ms(ts_ms: int) -> str:
    if not ts_ms:
        return "0"
    try:
        return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return f"{ts_ms} (invalid)"


def _iso_s(ts: int) -> str:
    if not ts:
        return "0"
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return f"{ts} (invalid)"


class _Cursor:
    """A byte walker that appends a named field for every chunk it reads."""

    def __init__(self, data: bytes):
        self.d = data
        self.off = 0
        self.fields: list[dict] = []
        self.group: str | None = None

    def peek_int(self, size: int, signed: bool = False) -> int:
        return int.from_bytes(self.d[self.off:self.off + size], "big", signed=signed)

    def take(self, name: str, size: int, *, signed: bool = False, fmt=None,
             unit: str = "", bit: bool = True) -> int:
        raw = self.d[self.off:self.off + size]
        if len(raw) < size:
            raise IndexError(f"need {size} bytes for {name!r}, have {len(raw)}")
        intval = int.from_bytes(raw, "big", signed=signed)
        disp = fmt(intval) if fmt else intval
        self.add(name, size, disp, raw.hex(),
                 format(intval, f"0{size * 8}b") if (bit and 0 < size <= 4) else "", unit)
        self.off += size
        return intval

    def raw(self, size: int) -> bytes:
        r = self.d[self.off:self.off + size]
        if len(r) < size:
            raise IndexError(f"need {size} bytes, have {len(r)}")
        self.off += size
        return r

    def add(self, name: str, size: int, value, hexstr: str, bit: str = "",
            unit: str = "", at: int | None = None) -> None:
        # `at` is the byte offset of this field within the packet. For sequential
        # take() it is the current cursor (call happens before advancing); for fields
        # read via raw() the caller passes the start offset explicitly.
        self.fields.append({
            "group": self.group, "name": name, "size": size,
            "value": str(value), "hex": hexstr, "bit": bit, "unit": unit,
            "at": self.off if at is None else at,
        })


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def explain(buffer: bytes, transport: str = "tcp") -> dict:
    """Decode a buffer of one or more Teltonika packets into a field breakdown.

    Returns ``{ok, transport, packets[], records[], error}``. ``records`` is the flat
    list of AVL records (timestamp/lon/lat/speed/…) across every packet, for the table
    view; ``packets[].fields`` is the per-packet field breakdown for the inspector.
    """
    out: dict = {"ok": True, "transport": transport, "packets": [], "records": [],
                 "error": None}
    off, guard = 0, 0
    while off < len(buffer):
        guard += 1
        if guard > 2000:
            out["error"] = "too many packets (stopped at 2000)"
            break
        chunk = buffer[off:]
        try:
            pkt, consumed = (_parse_udp(chunk) if transport == "udp"
                             else _parse_tcp(chunk))
        except (IndexError, ValueError) as exc:
            out["error"] = f"parse error at byte {off}: {exc}"
            break
        if consumed <= 0:
            out["error"] = "incomplete packet - need more bytes"
            break
        pkt["byte_offset"] = off            # absolute start of this packet in the buffer
        out["packets"].append(pkt)
        out["records"].extend(pkt.get("records", []))
        off += consumed
    out["ok"] = out["error"] is None
    out["consumed"] = off
    return out


# --------------------------------------------------------------------------- #
# TCP framing
# --------------------------------------------------------------------------- #
def _parse_tcp(b: bytes):
    if len(b) < 4:
        return None, 0
    if b[:4] == b"\x00\x00\x00\x00":
        return _parse_tcp_data(b)
    length = int.from_bytes(b[0:2], "big")
    if 8 <= length <= 20:
        if len(b) < 2 + length:
            return None, 0
        if b[2:2 + length].isdigit():
            return _parse_imei(b, length)
    raise ValueError("not a Teltonika TCP frame (need 0x00000000 preamble or IMEI handshake)")


def _parse_imei(b: bytes, length: int):
    cur = _Cursor(b[:2 + length])
    cur.group = "IMEI Handshake"
    cur.take("IMEI Length", 2)
    imei_raw = cur.raw(length)
    cur.add("IMEI", length, imei_raw.decode("ascii", "replace"), imei_raw.hex(),
            at=cur.off - length)
    return _packet("imei", None, "IMEI handshake", None, 0, cur.fields, []), 2 + length


def _parse_tcp_data(b: bytes):
    if len(b) < 12:
        return None, 0
    data_len = int.from_bytes(b[4:8], "big")
    total = 8 + data_len + 4
    if len(b) < total:
        return None, 0

    pkt = b[:total]
    data = pkt[8:8 + data_len]
    crc_rx = int.from_bytes(pkt[total - 4:total], "big") & 0xFFFF
    crc_ok = crc_rx == crc16_ibm(data)

    cur = _Cursor(pkt)
    cur.group = "Header"
    cur.take("Preamble", 4, bit=False)
    cur.take("Data Field Length", 4)
    codec_id, codec_name, count, records = _take_codec_data(cur, reserve_tail=4)

    cur.group = "Footer"
    cur.off = total - 4
    crc_raw = cur.raw(4)
    cur.add("CRC-16", 4, f"0x{crc_rx:04X} ({'OK' if crc_ok else 'MISMATCH'})", crc_raw.hex(),
            at=cur.off - 4)
    return _packet("tcp_data", codec_id, codec_name, crc_ok, count, cur.fields, records), total


# --------------------------------------------------------------------------- #
# UDP channel framing (no preamble, no CRC)
# --------------------------------------------------------------------------- #
def _parse_udp(b: bytes):
    if len(b) < 8:
        return None, 0
    length = int.from_bytes(b[0:2], "big")
    total = 2 + length
    if len(b) < total:
        return None, 0
    imei_len = int.from_bytes(b[6:8], "big")
    if not (8 <= imei_len <= 20 and b[8:8 + imei_len].isdigit()):
        raise ValueError("not a Teltonika UDP channel frame")

    cur = _Cursor(b[:total])
    cur.group = "UDP Header"
    cur.take("Length", 2)
    cur.take("Packet ID", 2, bit=False)
    cur.take("Un-usable byte", 1)
    cur.take("AVL Packet ID", 1)
    cur.take("IMEI Length", 2)
    imei_raw = cur.raw(imei_len)
    cur.add("IMEI", imei_len, imei_raw.decode("ascii", "replace"), imei_raw.hex(),
            at=cur.off - imei_len)
    codec_id, codec_name, count, records = _take_codec_data(cur, reserve_tail=0)
    return _packet("udp_data", codec_id, codec_name, None, count, cur.fields, records), total


# --------------------------------------------------------------------------- #
# Codec data (shared by TCP + UDP) - starts at the Codec ID byte
# --------------------------------------------------------------------------- #
def _take_codec_data(cur: _Cursor, reserve_tail: int):
    codec_id = cur.peek_int(1)
    codec_name = CODEC_NAMES.get(codec_id, f"Codec 0x{codec_id:02X}")
    cur.group = "Header"
    cur.take("Codec ID", 1, fmt=lambda v: f"0x{v:02X} ({codec_name})", bit=False)

    records: list[dict] = []
    count = 0
    if codec_id in AVL_SPECS:
        spec = AVL_SPECS[codec_id]
        count = cur.take("Number of Data 1", 1)
        for i in range(count):
            cur.group = f"AVL Record {i + 1}"
            records.append(_take_avl_record(cur, spec))
        cur.group = "Footer"
        cur.take("Number of Data 2", 1)
    elif codec_id in (0x0C, 0x0D, 0x0E):
        _take_gprs(cur, codec_id)
    else:
        cur.group = "Data"
        rest = max(0, len(cur.d) - cur.off - reserve_tail)
        cur.add("Data (unparsed)", rest, "<unsupported codec>",
                cur.d[cur.off:cur.off + rest].hex())
        cur.off += rest
    return codec_id, codec_name, count, records


def _take_avl_record(cur: _Cursor, spec: dict) -> dict:
    id_sz, cnt_sz = spec["id_sz"], spec["cnt_sz"]

    ts_ms = cur.take("Timestamp", 8, fmt=_iso_ms, bit=False)
    cur.take("Priority", 1, fmt=lambda v: f"{v} ({PRIORITY.get(v, '?')})")
    lon = cur.take("Longitude", 4, signed=True, fmt=lambda v: f"{v / 1e7:.7f}", unit="deg")
    lat = cur.take("Latitude", 4, signed=True, fmt=lambda v: f"{v / 1e7:.7f}", unit="deg")
    alt = cur.take("Altitude", 2, signed=True, unit="m")
    angle = cur.take("Angle", 2, unit="deg")
    sats = cur.take("Satellites", 1)
    speed = cur.take("Speed", 2, unit="km/h")

    cur.take("Event IO ID", id_sz,
             fmt=lambda v: f"{v} ({avl_param(v)['name']})" if v else "0 (none)")
    if spec["gen"]:
        cur.take("Generation Type", 1, fmt=lambda v: f"{v} ({GENERATION.get(v, '?')})")
    cur.take("N of Total IO", cnt_sz)

    for vlen in (1, 2, 4, 8):
        n = cur.take(f"N{vlen} ({vlen}-byte IO count)", cnt_sz)
        for _ in range(n):
            _take_io_element(cur, id_sz, vlen)
    if spec["nx"]:
        nx = cur.take("NX (variable-length IO count)", cnt_sz)
        for _ in range(nx):
            _take_io_element(cur, id_sz, None)

    return {"timestamp": _iso_ms(ts_ms), "longitude": round(lon / 1e7, 7),
            "latitude": round(lat / 1e7, 7), "altitude_m": alt, "angle_deg": angle,
            "satellites": sats, "speed_kph": speed}


def _take_io_element(cur: _Cursor, id_sz: int, vlen: int | None) -> None:
    iid = int.from_bytes(cur.raw(id_sz), "big")
    p = avl_param(iid)
    if vlen is None:  # NX variable-length element
        ln = int.from_bytes(cur.raw(2), "big")
        val_raw = cur.raw(ln)
        cur.add(f"{p['name']} (AVL {iid})", ln, val_raw.hex(), val_raw.hex(), "",
                p["unit"], at=cur.off - ln)
        return
    val_raw = cur.raw(vlen)
    if RAW_HEX_IO_LOW <= iid <= RAW_HEX_IO_HIGH:        # vendor/raw range -> hex only
        cur.add(f"{p['name']} (AVL {iid})", vlen, "0x" + val_raw.hex(), val_raw.hex(),
                "", p["unit"], at=cur.off - vlen)
        return
    val = int.from_bytes(val_raw, "big")
    cur.add(f"{p['name']} (AVL {iid})", vlen, val, val_raw.hex(),
            format(val, f"0{vlen * 8}b") if vlen <= 4 else "", p["unit"], at=cur.off - vlen)


def _take_gprs(cur: _Cursor, codec_id: int) -> None:
    cur.group = "Header"
    cur.take("Command Quantity 1", 1)
    cur.take("Type", 1, fmt=lambda v: f"0x{v:02X} ({TYPE_NAMES.get(v, '?')})")
    size = cur.take("Command/Response Size", 4)

    cur.group = "Payload"
    if codec_id == 0x0D:
        cur.take("Timestamp", 4, fmt=_iso_s, bit=False)
        body_len = max(0, size - 4)
    elif codec_id == 0x0E:
        imei_raw = cur.raw(8)
        cur.add("IMEI (addressed)", 8, imei_raw.hex(), imei_raw.hex(), at=cur.off - 8)
        body_len = max(0, size - 8)
    else:
        body_len = size
    body = cur.raw(body_len)
    cur.add("Command/Response", body_len, body.decode("ascii", "replace"), body.hex(),
            at=cur.off - body_len)

    cur.group = "Footer"
    cur.take("Command Quantity 2", 1)


def _packet(kind, codec_id, codec_name, crc_ok, count, fields, records) -> dict:
    return {"kind": kind, "codec_id": codec_id, "codec_name": codec_name,
            "crc_ok": crc_ok, "record_count": count, "fields": fields, "records": records}
