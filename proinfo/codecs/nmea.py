"""NMEA-0183 text codec ($GPRMC / $GPGGA / $GPVTG ...).

The simplest 'real' protocol: line-delimited ASCII, no device id on the wire (so a
synthetic id is used unless the session already learned one). A good reference for how
a codec frames messages (here: on newline) and fills the unified model.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..core.interfaces import Codec
from ..core.models import DecodeOutput, Position, Session, UnifiedRecord
from ..core.registry import register_codec


def _nmea_deg(value: str, hemi: str) -> float | None:
    """Convert ddmm.mmmm / dddmm.mmmm + hemisphere to signed decimal degrees."""
    if not value:
        return None
    try:
        dot = value.index(".")
    except ValueError:
        return None
    deg_len = dot - 2
    if deg_len < 0:
        return None
    degrees = float(value[:deg_len] or 0)
    minutes = float(value[deg_len:])
    result = degrees + minutes / 60.0
    if hemi in ("S", "W"):
        result = -result
    return result


@register_codec
class NmeaCodec(Codec):
    name = "nmea"
    display_name = "NMEA-0183"

    def detect(self, sample: bytes) -> float:
        try:
            text = sample[:128].decode("ascii")
        except UnicodeDecodeError:
            return 0.0
        return 0.9 if any(tag in text for tag in ("$GP", "$GN", "$GL")) else 0.0

    def decode(self, buffer: bytes, session: Session) -> DecodeOutput:
        nl = buffer.rfind(b"\n")
        if nl == -1:
            return DecodeOutput(consumed=0, note="need a full line")
        chunk = buffer[: nl + 1]
        text = chunk.decode("ascii", errors="ignore")
        records: list[UnifiedRecord] = []
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("$"):
                continue
            rec = self._parse(line, session)
            if rec is not None:
                records.append(rec)
        return DecodeOutput(records=records, consumed=nl + 1)

    # -- per-sentence parsing ---------------------------------------------- #
    def _parse(self, sentence: str, session: Session) -> UnifiedRecord | None:
        body = sentence[1:].split("*", 1)[0]
        parts = body.split(",")
        talker = parts[0]
        kind = talker[2:] if len(talker) >= 5 else talker
        device_id = session.device_id or "nmea-device"
        if kind == "RMC":
            return self._rmc(parts, device_id)
        if kind == "GGA":
            return self._gga(parts, device_id)
        return None

    def _rmc(self, p: list[str], device_id: str) -> UnifiedRecord | None:
        # $..RMC,time,status,lat,N/S,lon,E/W,speed(kn),course,date,...
        if len(p) < 10:
            return None
        valid = p[2] == "A"
        lat = _nmea_deg(p[3], p[4])
        lon = _nmea_deg(p[5], p[6])
        speed_kn = float(p[7]) if p[7] else None
        course = float(p[8]) if p[8] else None
        event_time = self._datetime(p[9], p[1])
        return UnifiedRecord(
            device_id=device_id,
            protocol=self.name,
            message_type="RMC",
            event_time=event_time,
            gps_valid=valid,
            position=Position(latitude=lat, longitude=lon),
            speed_kph=round(speed_kn * 1.852, 2) if speed_kn is not None else None,
            heading_deg=course,
        )

    def _gga(self, p: list[str], device_id: str) -> UnifiedRecord | None:
        # $..GGA,time,lat,N/S,lon,E/W,fix,sats,hdop,alt,M,...
        if len(p) < 10:
            return None
        lat = _nmea_deg(p[2], p[3])
        lon = _nmea_deg(p[4], p[5])
        fix = p[6]
        sats = int(p[7]) if p[7] else None
        alt = float(p[9]) if p[9] else None
        return UnifiedRecord(
            device_id=device_id,
            protocol=self.name,
            message_type="GGA",
            event_time=self._datetime(None, p[1]),
            gps_valid=fix not in ("", "0"),
            position=Position(latitude=lat, longitude=lon, altitude_m=alt),
            altitude_m=alt,
            satellites=sats,
            io={"hdop": p[8] or None, "fix_quality": fix or None},
        )

    @staticmethod
    def _datetime(date: str | None, time: str | None) -> datetime | None:
        if not time or len(time) < 6:
            return None
        try:
            hh, mm, ss = int(time[0:2]), int(time[2:4]), int(float(time[4:]))
            if date and len(date) == 6:
                dd, mo, yy = int(date[0:2]), int(date[2:4]), 2000 + int(date[4:6])
                return datetime(yy, mo, dd, hh, mm, ss, tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            return now.replace(hour=hh, minute=mm, second=ss, microsecond=0)
        except (ValueError, IndexError):
            return None
