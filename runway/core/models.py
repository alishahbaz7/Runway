"""The data model that flows through Runway.

Two worlds meet here:

* **Wire side** - ``RawFrame`` (bytes off a Source) and ``Session`` (per-connection
  state the pipeline owns and codecs may read/write).
* **Normalized side** - ``UnifiedRecord``: the single, human-readable shape every
  codec must produce regardless of the device/protocol it parsed. This is what the
  dashboard, storage and relays all consume, so adding a new protocol never touches
  anything downstream.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    """Timezone-aware UTC now (never use naive datetimes in Runway)."""
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Wire side
# --------------------------------------------------------------------------- #
@dataclass
class RawFrame:
    """A chunk of raw bytes produced by a Source.

    A frame is NOT necessarily one protocol message - it is whatever arrived in a
    read (a TCP segment, a UDP datagram, a slice of a replayed file). The pipeline
    buffers frames per-session and lets the codec decide message boundaries.
    """

    data: bytes
    transport: str = "unknown"          # tcp | udp | mqtt | http | file
    peer: Optional[str] = None          # remote address, topic, or file name
    source: Optional[str] = None        # name of the Source that produced it
    received_at: datetime = field(default_factory=utcnow)


@dataclass
class Session:
    """Per-connection (or per-file) state shared across ``decode`` calls.

    The pipeline owns ``buffer`` (the undecoded byte backlog). Codecs are created
    once per session and may keep protocol state in ``state`` - e.g. the IMEI learned
    from a GT06 login packet that later location packets do not repeat.
    """

    id: str
    transport: str = "unknown"
    peer: Optional[str] = None
    device_id: Optional[str] = None
    codec: Optional[str] = None
    buffer: bytearray = field(default_factory=bytearray)
    state: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Normalized side
# --------------------------------------------------------------------------- #
class Position(BaseModel):
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude_m: Optional[float] = None

    @property
    def is_valid(self) -> bool:
        return (
            self.latitude is not None
            and self.longitude is not None
            and -90.0 <= self.latitude <= 90.0
            and -180.0 <= self.longitude <= 180.0
            and not (self.latitude == 0.0 and self.longitude == 0.0)
        )


class UnifiedRecord(BaseModel):
    """The normalized, protocol-agnostic output of any codec.

    Only ``device_id`` and ``protocol`` are mandatory; every other field is optional
    so a codec fills in exactly what its message carried. Protocol-specific data that
    has no first-class field goes into ``io`` (telemetry IO elements) or ``extra``.
    """

    device_id: str
    protocol: str
    message_type: Optional[str] = None
    event_time: Optional[datetime] = None       # time reported by the device
    received_at: datetime = Field(default_factory=utcnow)

    # Location / motion
    position: Optional[Position] = None
    speed_kph: Optional[float] = None
    heading_deg: Optional[float] = None
    altitude_m: Optional[float] = None
    satellites: Optional[int] = None
    gps_valid: Optional[bool] = None

    # Vehicle / device state
    ignition: Optional[bool] = None
    moving: Optional[bool] = None
    battery_v: Optional[float] = None
    battery_pct: Optional[float] = None
    external_v: Optional[float] = None
    gsm_signal: Optional[int] = None
    odometer_m: Optional[float] = None

    # Catch-alls
    alarms: list[str] = Field(default_factory=list)
    io: dict[str, Any] = Field(default_factory=dict)        # protocol IO elements
    extra: dict[str, Any] = Field(default_factory=dict)     # anything else

    # Provenance
    transport: Optional[str] = None
    peer: Optional[str] = None
    raw_hex: Optional[str] = None

    def summary(self) -> str:
        """One-line human summary for console/logs."""
        bits = [self.device_id, self.protocol, self.message_type or "-"]
        if self.position and self.position.is_valid:
            bits.append(f"{self.position.latitude:.5f},{self.position.longitude:.5f}")
        if self.speed_kph is not None:
            bits.append(f"{self.speed_kph:.0f}km/h")
        if self.alarms:
            bits.append("ALARM:" + ",".join(self.alarms))
        return " | ".join(str(b) for b in bits)


@dataclass
class DecodeOutput:
    """What a codec returns from a single ``decode()`` call.

    ``consumed`` tells the pipeline how many bytes to drop from the session buffer.
    Return ``consumed == 0`` to mean "I need more bytes" - the pipeline keeps the
    buffer intact and waits for the next frame.
    """

    records: list[UnifiedRecord] = field(default_factory=list)
    consumed: int = 0
    ack: Optional[bytes] = None         # optional bytes to write back to the device
    note: Optional[str] = None          # diagnostic (e.g. "need more data", "bad crc")
