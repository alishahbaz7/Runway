"""GT06 / Concox family codec (GT06N, GT02, and many low-cost trackers).

Frame layout::

    7878 LL PP <information ...> SS SS CC CC 0D0A     (LL = 1-byte length)
    7979 LLLL PP <information ...> SS SS CC CC 0D0A   (LLLL = 2-byte length)

    LL/LLLL counts: protocol(1) + information + serial(2) + crc(2)
    PP = protocol number   SS = serial   CC = CRC-ITU over [LL .. SS]

Handled messages: login (0x01), location (0x12), status/heartbeat (0x13),
GPS+LBS+status (0x22), alarm (0x16/0x18/0x26). The server ACK that keeps the device
online is built for login/heartbeat/alarm and returned via ``DecodeOutput.ack``.

Course/status bit mapping follows the GT06 spec: bit10=North, bit11=West,
bit12=positioned. Verify against your specific device datasheet if positions look off.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..core.crc import crc_itu
from ..core.interfaces import Codec
from ..core.models import DecodeOutput, Position, Session, UnifiedRecord
from ..core.registry import register_codec

_STOP = b"\x0d\x0a"
_ACK_PROTOCOLS = {0x01, 0x13, 0x16, 0x18, 0x26}

_PROTO_NAMES = {
    0x01: "login",
    0x12: "location",
    0x13: "status",
    0x15: "string",
    0x16: "alarm",
    0x18: "alarm",
    0x1A: "gps_query",
    0x22: "gps_lbs_status",
    0x26: "alarm",
    0x8A: "time_sync",
}

# GT06 terminal-information alarm codes (in the status/alarm byte).
_ALARM_CODES = {
    0x01: "sos",
    0x02: "power_cut",
    0x03: "shock",
    0x04: "fence_in",
    0x05: "fence_out",
    0x06: "overspeed",
    0x09: "vibration",
    0x0E: "low_battery",
    0x13: "power_off",
}


@register_codec
class Gt06Codec(Codec):
    name = "gt06"
    display_name = "GT06 / Concox"
    description = ("GT06 / Concox family (GT06N, GT02…) over TCP — login, location, "
                   "heartbeat, GPS+LBS, alarms. 7878/7979 framing, CRC-ITU, server ACK.")

    def detect(self, sample: bytes) -> float:
        if sample[:2] in (b"\x78\x78", b"\x79\x79"):
            return 0.9
        return 0.0

    # -- framing ----------------------------------------------------------- #
    def decode(self, buffer: bytes, session: Session) -> DecodeOutput:
        b = buffer
        if len(b) < 2:
            return DecodeOutput(consumed=0, note="need more data")

        start = b[:2]
        if start == b"\x78\x78":
            header, length = 3, b[2]
        elif start == b"\x79\x79":
            if len(b) < 4:
                return DecodeOutput(consumed=0, note="need length bytes")
            header, length = 4, int.from_bytes(b[2:4], "big")
        else:
            return self._resync(b)

        total = header + length + 2  # + STOP
        if len(b) < total:
            return DecodeOutput(consumed=0, note="need full packet")

        pkt = b[:total]
        if pkt[-2:] != _STOP:
            return self._resync(b)

        proto = pkt[header]
        info = pkt[header + 1 : total - 4]   # information content only
        serial = pkt[total - 4 : total - 2]
        crc_rx = int.from_bytes(pkt[total - 2 - 2 : total - 2], "big")
        crc_calc = crc_itu(pkt[2 : total - 4])  # [length .. serial]
        crc_ok = crc_rx == crc_calc

        rec = self._build(proto, info, pkt, session, crc_ok)
        ack = self._ack(proto, serial) if proto in _ACK_PROTOCOLS else None
        return DecodeOutput(records=[rec] if rec else [], consumed=total, ack=ack)

    def _resync(self, b: bytes) -> DecodeOutput:
        """Drop leading garbage up to the next plausible start marker."""
        for marker in (b"\x78\x78", b"\x79\x79"):
            idx = b.find(marker, 1)
            if idx != -1:
                return DecodeOutput(consumed=idx, note="resync")
        # keep only the last byte (could be the first half of a start marker)
        return DecodeOutput(consumed=max(0, len(b) - 1), note="resync-flush")

    # -- message builders -------------------------------------------------- #
    def _build(self, proto, info, pkt, session, crc_ok) -> UnifiedRecord | None:
        mtype = _PROTO_NAMES.get(proto, f"0x{proto:02X}")
        device_id = session.device_id or "gt06-unknown"
        base = dict(
            device_id=device_id,
            protocol=self.name,
            message_type=mtype,
            raw_hex=pkt.hex(),
        )
        if not crc_ok:
            base["extra"] = {"crc": "mismatch"}

        if proto == 0x01:
            imei = self._bcd_imei(info[:8])
            session.device_id = imei
            return UnifiedRecord(**{**base, "device_id": imei, "io": {"imei": imei}})

        if proto in (0x12, 0x22, 0x16, 0x18, 0x26):
            gps = self._parse_gps(info)
            rec = UnifiedRecord(**base)
            if gps:
                rec.event_time = gps["time"]
                rec.position = Position(latitude=gps["lat"], longitude=gps["lon"])
                rec.speed_kph = gps["speed"]
                rec.heading_deg = gps["course"]
                rec.satellites = gps["sats"]
                rec.gps_valid = gps["valid"]
            if proto in (0x16, 0x18, 0x26):
                alarm = self._extract_alarm(info)
                if alarm:
                    rec.alarms.append(alarm)
            return rec

        if proto == 0x13:
            rec = UnifiedRecord(**base)
            if len(info) >= 3:
                ti = info[0]
                rec.ignition = bool(ti & 0x02)
                rec.io["terminal_info"] = ti
                rec.io["voltage_level"] = info[1]
                rec.gsm_signal = info[2]
            return rec

        # Unknown/unhandled protocol: still surface it.
        return UnifiedRecord(**{**base, "io": {"info_hex": info.hex()}})

    # -- field parsers ----------------------------------------------------- #
    @staticmethod
    def _parse_gps(info: bytes) -> dict | None:
        if len(info) < 18:
            return None
        try:
            yy, mo, dd, hh, mi, ss = info[0], info[1], info[2], info[3], info[4], info[5]
            event_time = datetime(2000 + yy, mo, dd, hh, mi, ss, tzinfo=timezone.utc)
        except ValueError:
            event_time = None
        sats = info[6] & 0x0F
        lat = int.from_bytes(info[7:11], "big") / 1800000.0
        lon = int.from_bytes(info[11:15], "big") / 1800000.0
        speed = float(info[15])
        flags = int.from_bytes(info[16:18], "big")
        course = flags & 0x03FF
        valid = bool(flags & 0x1000)
        if not (flags & 0x0400):   # bit10: 1=North
            lat = -lat
        if flags & 0x0800:         # bit11: 1=West
            lon = -lon
        return {
            "time": event_time, "sats": sats, "lat": lat, "lon": lon,
            "speed": speed, "course": course, "valid": valid,
        }

    @staticmethod
    def _extract_alarm(info: bytes) -> str | None:
        # In alarm packets the terminal-information/alarm byte sits after the GPS
        # (18) + LBS block. Layouts vary; scan the tail for a known alarm code.
        for byte in reversed(info[18:]):
            if byte in _ALARM_CODES:
                return _ALARM_CODES[byte]
        return None

    @staticmethod
    def _bcd_imei(b: bytes) -> str:
        digits = b.hex()
        return digits[-15:].lstrip("0") or digits[-15:]

    # -- server ACK -------------------------------------------------------- #
    @staticmethod
    def _ack(proto: int, serial: bytes) -> bytes:
        body = bytes([0x05, proto]) + serial
        crc = crc_itu(body).to_bytes(2, "big")
        return b"\x78\x78" + body + crc + _STOP
