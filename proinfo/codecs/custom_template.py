"""TEMPLATE - copy this file to add a new OEM / proprietary protocol.

Steps:
  1. Copy to ``proinfo/codecs/<your_protocol>.py``.
  2. Rename the class and set ``name`` (unique slug) + ``display_name``.
  3. Implement ``detect`` (so 'auto' can pick it) and ``decode``.
  4. Drop a sample capture in ``samples/`` and a test in ``tests/`` - done.

The ``@register_codec`` decorator makes it appear in the CLI, the API and the dashboard
codec dropdown automatically. Nothing else in ProInfo needs to change.

This template is intentionally inert: ``detect`` returns 0 so it never auto-selects,
and ``decode`` consumes the buffer without emitting records. Replace both.
"""

from __future__ import annotations

from ..core.interfaces import Codec
from ..core.models import DecodeOutput, Position, Session, UnifiedRecord
from ..core.registry import register_codec


@register_codec
class CustomTemplateCodec(Codec):
    name = "custom_template"
    display_name = "Custom (template - copy me)"
    description = "Starter template — copy this file to add a new protocol (auto-discovered)."

    def detect(self, sample: bytes) -> float:
        # Return 0..1 confidence that this codec parses `sample`. Look for a magic
        # header, a fixed prefix, a sentence marker, etc. Return 0 to opt out.
        #
        # Example: return 0.9 if sample[:2] == b"\xAA\x55" else 0.0
        return 0.0

    def decode(self, buffer: bytes, session: Session) -> DecodeOutput:
        """Parse the next message at the front of `buffer`.

        Contract:
          * Return ``DecodeOutput(consumed=0)`` when you need more bytes - the
            pipeline keeps the buffer and calls you again after more data arrives.
          * Otherwise set ``consumed`` to the exact number of bytes you handled.
          * Put any reply for the device in ``DecodeOutput.ack``.
          * Keep per-connection state (a learned device id, a counter) in
            ``session.state`` - NOT on ``self`` - because a codec instance is reused
            across packets but must stay stateless between sessions.

        Reference skeleton for a length-prefixed binary protocol:

            HEADER = b"\\xAA\\x55"
            if len(buffer) < 4:
                return DecodeOutput(consumed=0)              # need header + length
            if buffer[:2] != HEADER:
                return DecodeOutput(consumed=1)              # resync: drop a byte
            length = buffer[2]
            total = 3 + length + 1                           # header+len+payload+crc
            if len(buffer) < total:
                return DecodeOutput(consumed=0)              # need full packet
            payload = buffer[3:3 + length]
            device_id = session.device_id or "device-1"
            rec = UnifiedRecord(
                device_id=device_id,
                protocol=self.name,
                message_type="location",
                position=Position(latitude=..., longitude=...),
                speed_kph=...,
                raw_hex=buffer[:total].hex(),
            )
            return DecodeOutput(records=[rec], consumed=total)
        """
        # Inert default: swallow whatever is buffered without producing records.
        return DecodeOutput(consumed=len(buffer))
