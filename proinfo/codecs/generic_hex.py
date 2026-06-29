"""Last-resort codec: never fails, just surfaces the raw bytes.

Useful when you point ProInfo at an unknown device/log and simply want to *see* the
traffic (hex + length) in the inspector before writing a real codec for it.
"""

from __future__ import annotations

from ..core.interfaces import Codec
from ..core.models import DecodeOutput, Session, UnifiedRecord
from ..core.registry import register_codec


@register_codec
class GenericHexCodec(Codec):
    name = "generic_hex"
    display_name = "Generic / Hex dump"
    description = "Fallback — passes raw bytes through as a hex dump when no codec matches."

    def detect(self, sample: bytes) -> float:
        # Lowest possible confidence: only auto-selected when nothing else matches.
        return 0.01

    def decode(self, buffer: bytes, session: Session) -> DecodeOutput:
        if not buffer:
            return DecodeOutput(consumed=0)
        rec = UnifiedRecord(
            device_id=session.device_id or "unknown",
            protocol=self.name,
            message_type="raw",
            raw_hex=buffer.hex(),
            extra={"length": len(buffer)},
        )
        return DecodeOutput(records=[rec], consumed=len(buffer))
