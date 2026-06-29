"""Teltonika codec - the full Teltonika protocol family.

A Teltonika session can carry several frame shapes; this codec decodes all of them:

**TCP**
1. **IMEI handshake** - device sends ``[len:2][IMEI ascii]``; server replies ``0x01``
   to accept. We learn the device id here.
2. **AVL / GPRS data** - ``00000000 [data_len:4] [codec_id:1] <data> [crc:4]``. The CRC
   (CRC-16/IBM) covers the data field. Server replies with the 4-byte count of accepted
   records for AVL codecs.

(UDP channel framing is deferred - it will be added on a need basis. The codec-data parser
is already transport-agnostic, so adding UDP later is just a thin framing wrapper.)

**Codecs handled**
   * ``0x08`` Codec 8        - AVL location data (1-byte IO ids/counts)
   * ``0x8E`` Codec 8 Ext    - AVL with 2-byte IO ids/counts + NX variable-length group
   * ``0x10`` Codec 16       - AVL with 2-byte IO ids, a per-record generation-type byte
   * ``0x0C`` Codec 12       - GPRS command/response (ASCII)
   * ``0x0D`` Codec 13       - GPRS response with a leading timestamp
   * ``0x0E`` Codec 14       - GPRS command/response addressed by an 8-byte IMEI

Each AVL record carries a timestamp, GPS block and a set of IO elements keyed by AVL ID.
A handful of well-known IDs are mapped onto first-class unified fields; everything else is
preserved under ``io`` as ``io_<id>``.

.. note::
   No real OEM captures are available yet (kickoff brief, open question F). Codecs
   8/8E/16/12 follow well-documented Teltonika layouts. **Codecs 13 and 14 are implemented
   to the public spec and validated only against self-built synthetic packets** - a real
   capture is needed to confirm their exact framing.
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

# AVL codec ids -> framing widths.
#   id_sz  : bytes per IO id and per event io id
#   cnt_sz : bytes per "N of total" and per N1/N2/N4/N8 group count
#   gen    : a generation-type byte follows the event io id (Codec 16)
#   nx     : an NX variable-length IO group follows the fixed groups (Codec 8 Extended)
AVL_SPECS = {
    0x08: {"id_sz": 1, "cnt_sz": 1, "gen": False, "nx": False},
    0x8E: {"id_sz": 2, "cnt_sz": 2, "gen": False, "nx": True},
    0x10: {"id_sz": 2, "cnt_sz": 1, "gen": True, "nx": False},
}

# GPRS command/response codecs.
CODEC_12 = 0x0C
CODEC_13 = 0x0D
CODEC_14 = 0x0E

TYPE_COMMAND = 0x05
TYPE_RESPONSE = 0x06


@register_codec
class TeltonikaCodec(Codec):
    name = "teltonika"
    display_name = "Teltonika (Codec 8 / 8E / 16 / 12 / 13 / 14)"
    description = ("Teltonika AVL over TCP — IMEI handshake, AVL Codec 8 / 8E / 16, and the "
                   "GPRS command/response Codec 12 / 13 / 14. CRC-16/IBM, server count ACK.")

    def detect(self, sample: bytes) -> float:
        if sample[:4] == b"\x00\x00\x00\x00":
            return 0.85
        # TCP IMEI handshake: [len:2][ascii imei]
        if len(sample) >= 4 and sample[0] == 0:
            length = int.from_bytes(sample[0:2], "big")
            if 8 <= length <= 20 and sample[2:2 + min(length, len(sample) - 2)].isdigit():
                return 0.7
        return 0.0

    # -- top-level framing dispatch ---------------------------------------- #
    def decode(self, buffer: bytes, session: Session) -> DecodeOutput:
        b = buffer
        if len(b) < 4:
            return DecodeOutput(consumed=0, note="need more data")

        if b[:4] == b"\x00\x00\x00\x00":
            return self._decode_tcp(b, session)

        # TCP IMEI handshake.
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

    # -- TCP AVL / GPRS ---------------------------------------------------- #
    def _decode_tcp(self, b: bytes, session: Session) -> DecodeOutput:
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

        records, ack_count, codec_id = self._parse_codec_data(data_field, session, crc_ok)
        for rec in records:
            rec.raw_hex = pkt.hex()

        ack = ack_count.to_bytes(4, "big") if ack_count is not None else None
        return DecodeOutput(records=records, consumed=total, ack=ack)

    # -- codec-data dispatch (transport-agnostic) -------------------------- #
    def _parse_codec_data(self, data: bytes, session: Session, crc_ok: bool):
        """Parse a data field starting at the Codec ID byte.

        Returns ``(records, ack_count, codec_id)``. ``ack_count`` is the number of accepted
        records for AVL codecs (the value the server echoes back), or ``None`` for GPRS
        codecs / unknown ids where no count ack is sent.
        """
        if not data:
            return [], None, None
        codec_id = data[0]

        if codec_id in AVL_SPECS:
            return self._parse_avl(data, codec_id, session, crc_ok)
        if codec_id == CODEC_12:
            return self._parse_gprs12(data, session), None, codec_id
        if codec_id == CODEC_13:
            return self._parse_gprs13(data, session), None, codec_id
        if codec_id == CODEC_14:
            return self._parse_gprs14(data, session), None, codec_id

        # Unknown codec: keep a diagnostic record rather than crash.
        rec = UnifiedRecord(
            device_id=session.device_id or "teltonika-unknown",
            protocol=self.name, message_type="unknown",
            extra={"codec_id": codec_id, "note": "unsupported teltonika codec id"},
            raw_hex=data.hex(),
        )
        return [rec], None, codec_id

    # -- AVL records (Codec 8 / 8E / 16) ----------------------------------- #
    def _parse_avl(self, data: bytes, codec_id: int, session: Session, crc_ok: bool):
        spec = AVL_SPECS[codec_id]
        count = data[1]
        records: list[UnifiedRecord] = []
        off = 2
        for _ in range(count):
            try:
                rec, off = self._parse_record(data, off, codec_id, spec, session, crc_ok)
            except (IndexError, ValueError):
                break  # truncated/garbled record; keep what we have
            records.append(rec)
        return records, count, codec_id

    def _parse_record(self, d: bytes, off: int, codec_id: int, spec: dict,
                      session: Session, crc_ok: bool) -> tuple[UnifiedRecord, int]:
        id_sz = spec["id_sz"]
        cnt_sz = spec["cnt_sz"]

        ts_ms = int.from_bytes(d[off:off + 8], "big"); off += 8
        priority = d[off]; off += 1
        lon = int.from_bytes(d[off:off + 4], "big", signed=True) / 1e7; off += 4
        lat = int.from_bytes(d[off:off + 4], "big", signed=True) / 1e7; off += 4
        alt = int.from_bytes(d[off:off + 2], "big", signed=True); off += 2
        angle = int.from_bytes(d[off:off + 2], "big"); off += 2
        sats = d[off]; off += 1
        speed = int.from_bytes(d[off:off + 2], "big"); off += 2

        event_id = int.from_bytes(d[off:off + id_sz], "big"); off += id_sz
        gen_type = None
        if spec["gen"]:  # Codec 16 generation-type byte
            gen_type = d[off]; off += 1
        _total = int.from_bytes(d[off:off + cnt_sz], "big"); off += cnt_sz

        io: dict[int, object] = {}
        for vlen in (1, 2, 4, 8):
            n = int.from_bytes(d[off:off + cnt_sz], "big"); off += cnt_sz
            for _ in range(n):
                iid = int.from_bytes(d[off:off + id_sz], "big"); off += id_sz
                io[iid] = int.from_bytes(d[off:off + vlen], "big"); off += vlen
        if spec["nx"]:  # Codec 8 Extended variable-length group
            nx = int.from_bytes(d[off:off + cnt_sz], "big"); off += cnt_sz
            for _ in range(nx):
                iid = int.from_bytes(d[off:off + id_sz], "big"); off += id_sz
                ln = int.from_bytes(d[off:off + 2], "big"); off += 2
                io[iid] = d[off:off + ln].hex(); off += ln

        extra = {"priority": priority, "event_io_id": event_id, "codec_id": codec_id}
        if gen_type is not None:
            extra["generation_type"] = gen_type
        if not crc_ok:
            extra["crc"] = "mismatch"

        rec = UnifiedRecord(
            device_id=session.device_id or "teltonika-unknown",
            protocol=self.name,
            message_type="avl",
            event_time=_epoch_ms(ts_ms),
            position=Position(latitude=lat, longitude=lon, altitude_m=float(alt)),
            altitude_m=float(alt),
            heading_deg=float(angle),
            satellites=sats,
            speed_kph=float(speed),
            gps_valid=sats > 0,
            io={f"io_{k}": v for k, v in io.items()},
            extra=extra,
        )
        self._map_known(rec, io)
        return rec, off

    # -- GPRS command/response codecs (12 / 13 / 14) ----------------------- #
    def _parse_gprs12(self, d: bytes, session: Session) -> list[UnifiedRecord]:
        # [codec][qty1][type][size:4][command:size][qty2]
        ctype = d[2]
        size = int.from_bytes(d[3:7], "big")
        payload = d[7:7 + size]
        rec = self._gprs_record(session, CODEC_12, ctype, payload)
        return [rec]

    def _parse_gprs13(self, d: bytes, session: Session) -> list[UnifiedRecord]:
        # [codec][qty1][type][size:4][timestamp:4][response:size-4][qty2]
        ctype = d[2]
        size = int.from_bytes(d[3:7], "big")
        ts = int.from_bytes(d[7:11], "big")
        payload = d[11:7 + size]
        rec = self._gprs_record(session, CODEC_13, ctype, payload, event_time=_epoch_s(ts))
        return [rec]

    def _parse_gprs14(self, d: bytes, session: Session) -> list[UnifiedRecord]:
        # [codec][qty1][type][size:4][imei:8][command:size-8][qty2]
        ctype = d[2]
        size = int.from_bytes(d[3:7], "big")
        imei_hex = d[7:15].hex()
        payload = d[15:7 + size]
        rec = self._gprs_record(session, CODEC_14, ctype, payload, imei_hex=imei_hex)
        return [rec]

    def _gprs_record(self, session: Session, codec_id: int, ctype: int, payload: bytes,
                     event_time=None, imei_hex: str | None = None) -> UnifiedRecord:
        text = payload.decode("ascii", errors="replace")
        is_cmd = ctype == TYPE_COMMAND
        extra: dict[str, object] = {"codec_id": codec_id, "type": ctype}
        extra["command_text" if is_cmd else "response_text"] = text
        if imei_hex is not None:
            extra["imei_hex"] = imei_hex
        return UnifiedRecord(
            device_id=session.device_id or "teltonika-unknown",
            protocol=self.name,
            message_type="command" if is_cmd else "response",
            event_time=event_time,
            extra=extra,
        )

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

    # -- downlink (bidirectional) ------------------------------------------ #
    @classmethod
    def build_command(cls, text: str) -> bytes:
        """Build a Codec 12 TCP command frame (type 0x05) to send to a device."""
        body = text.encode("ascii")
        data = (bytes([CODEC_12, 1, TYPE_COMMAND])
                + len(body).to_bytes(4, "big") + body + bytes([1]))
        return (b"\x00\x00\x00\x00" + len(data).to_bytes(4, "big")
                + data + crc16_ibm(data).to_bytes(4, "big"))


def _epoch_ms(ts_ms: int):
    if not ts_ms:
        return None
    try:
        return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _epoch_s(ts: int):
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
