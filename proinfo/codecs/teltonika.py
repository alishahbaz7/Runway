"""Teltonika codec - Codec 8 and Codec 8 Extended (0x08 / 0x8E) over TCP.

A Teltonika session has two phases:

1. **IMEI handshake** - device sends ``[len:2][IMEI ascii]``; server replies ``0x01``
   to accept. We learn the device id here.
2. **AVL data** - ``00000000 [data_len:4] [codec_id:1] [n:1] <records> [n:1] [crc:4]``.
   Server replies with the 4-byte count of accepted records.

Each AVL record carries a timestamp, GPS block and a set of IO elements keyed by AVL
ID. A handful of well-known IDs are mapped onto first-class unified fields; everything
else is preserved under ``io`` as ``io_<id>``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..core.crc import crc16_ibm
from ..core.interfaces import Codec
from ..core.models import DecodeOutput, Position, Session, UnifiedRecord
from ..core.registry import register_codec

# A few common AVL IDs -> unified fields. Extend freely.
IO_IGNITION = 239
IO_MOVEMENT = 240
IO_GSM = 21
IO_EXT_VOLT_MV = 66
IO_BAT_VOLT_MV = 67
IO_BAT_LEVEL = 113
IO_ODOMETER = 16
IO_SPEED = 24


@register_codec
class TeltonikaCodec(Codec):
    name = "teltonika"
    display_name = "Teltonika (Codec 8 / 8E)"

    def detect(self, sample: bytes) -> float:
        if sample[:4] == b"\x00\x00\x00\x00":
            return 0.85
        if len(sample) >= 4 and sample[0] == 0:
            length = int.from_bytes(sample[0:2], "big")
            if 8 <= length <= 20 and sample[2:2 + min(length, len(sample) - 2)].isdigit():
                return 0.7
        return 0.0

    # -- top-level framing ------------------------------------------------- #
    def decode(self, buffer: bytes, session: Session) -> DecodeOutput:
        b = buffer
        if len(b) < 4:
            return DecodeOutput(consumed=0, note="need more data")

        if b[:4] == b"\x00\x00\x00\x00":
            return self._decode_data(b, session)

        # IMEI handshake
        length = int.from_bytes(b[0:2], "big")
        if 8 <= length <= 20:
            if len(b) < 2 + length:
                return DecodeOutput(consumed=0, note="need imei bytes")
            imei_bytes = b[2:2 + length]
            if imei_bytes.isdigit():
                imei = imei_bytes.decode("ascii")
                session.device_id = imei
                rec = UnifiedRecord(
                    device_id=imei, protocol=self.name, message_type="imei",
                    io={"imei": imei}, raw_hex=b[:2 + length].hex(),
                )
                return DecodeOutput(records=[rec], consumed=2 + length, ack=b"\x01")

        return DecodeOutput(consumed=0, note="unrecognized teltonika frame")

    def _decode_data(self, b: bytes, session: Session) -> DecodeOutput:
        if len(b) < 12:
            return DecodeOutput(consumed=0, note="need header")
        data_len = int.from_bytes(b[4:8], "big")
        total = 8 + data_len + 4
        if len(b) < total:
            return DecodeOutput(consumed=0, note="need full packet")

        pkt = b[:total]
        data_field = pkt[8:8 + data_len]
        crc_rx = int.from_bytes(pkt[total - 4:total], "big") & 0xFFFF
        crc_ok = crc_rx == crc16_ibm(data_field)

        codec_id = data_field[0]
        count = data_field[1]
        extended = codec_id == 0x8E

        records: list[UnifiedRecord] = []
        off = 2
        for _ in range(count):
            try:
                rec, off = self._parse_record(data_field, off, extended, session, crc_ok)
            except (IndexError, ValueError):
                break  # truncated/garbled record; keep what we have
            rec.raw_hex = pkt.hex()
            records.append(rec)

        ack = count.to_bytes(4, "big")  # accept all records
        return DecodeOutput(records=records, consumed=total, ack=ack)

    # -- one AVL record ---------------------------------------------------- #
    def _parse_record(self, d: bytes, off: int, ext: bool, session: Session,
                      crc_ok: bool) -> tuple[UnifiedRecord, int]:
        id_sz = 2 if ext else 1
        cnt_sz = 2 if ext else 1

        ts_ms = int.from_bytes(d[off:off + 8], "big"); off += 8
        priority = d[off]; off += 1
        lon = int.from_bytes(d[off:off + 4], "big", signed=True) / 1e7; off += 4
        lat = int.from_bytes(d[off:off + 4], "big", signed=True) / 1e7; off += 4
        alt = int.from_bytes(d[off:off + 2], "big", signed=True); off += 2
        angle = int.from_bytes(d[off:off + 2], "big"); off += 2
        sats = d[off]; off += 1
        speed = int.from_bytes(d[off:off + 2], "big"); off += 2

        event_id = int.from_bytes(d[off:off + id_sz], "big"); off += id_sz
        _total = int.from_bytes(d[off:off + cnt_sz], "big"); off += cnt_sz

        io: dict[int, object] = {}
        for vlen in (1, 2, 4, 8):
            n = int.from_bytes(d[off:off + cnt_sz], "big"); off += cnt_sz
            for _ in range(n):
                iid = int.from_bytes(d[off:off + id_sz], "big"); off += id_sz
                io[iid] = int.from_bytes(d[off:off + vlen], "big"); off += vlen
        if ext:  # NX variable-length group
            nx = int.from_bytes(d[off:off + cnt_sz], "big"); off += cnt_sz
            for _ in range(nx):
                iid = int.from_bytes(d[off:off + id_sz], "big"); off += id_sz
                ln = int.from_bytes(d[off:off + 2], "big"); off += 2
                io[iid] = d[off:off + ln].hex(); off += ln

        event_time = None
        if ts_ms:
            try:
                event_time = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                event_time = None

        rec = UnifiedRecord(
            device_id=session.device_id or "teltonika-unknown",
            protocol=self.name,
            message_type="avl",
            event_time=event_time,
            position=Position(latitude=lat, longitude=lon, altitude_m=float(alt)),
            altitude_m=float(alt),
            heading_deg=float(angle),
            satellites=sats,
            speed_kph=float(speed),
            gps_valid=sats > 0,
            io={f"io_{k}": v for k, v in io.items()},
            extra={"priority": priority, "event_io_id": event_id,
                   **({"crc": "mismatch"} if not crc_ok else {})},
        )
        self._map_known(rec, io)
        return rec, off

    @staticmethod
    def _map_known(rec: UnifiedRecord, io: dict[int, object]) -> None:
        if IO_IGNITION in io:
            rec.ignition = bool(io[IO_IGNITION])
        if IO_MOVEMENT in io:
            rec.moving = bool(io[IO_MOVEMENT])
        if IO_GSM in io:
            rec.gsm_signal = int(io[IO_GSM])  # type: ignore[arg-type]
        if IO_EXT_VOLT_MV in io:
            rec.external_v = int(io[IO_EXT_VOLT_MV]) / 1000.0  # type: ignore[arg-type]
        if IO_BAT_VOLT_MV in io:
            rec.battery_v = int(io[IO_BAT_VOLT_MV]) / 1000.0  # type: ignore[arg-type]
        if IO_BAT_LEVEL in io:
            rec.battery_pct = float(io[IO_BAT_LEVEL])  # type: ignore[arg-type]
        if IO_ODOMETER in io:
            rec.odometer_m = float(io[IO_ODOMETER])  # type: ignore[arg-type]
        if IO_SPEED in io and not rec.speed_kph:
            rec.speed_kph = float(io[IO_SPEED])  # type: ignore[arg-type]
