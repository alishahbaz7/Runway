"""The three plugin contracts that make ProInfo extensible.

Add a new transport  -> subclass :class:`Source`.
Add a new protocol   -> subclass :class:`Codec`   (the common case).
Add a new relay/store -> subclass :class:`Sink`.

Every implementation lives in its package (``sources/``, ``codecs/``, ``sinks/``)
and registers itself with a ``@register_*`` decorator. Nothing else in the system
needs to change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional

from .models import DecodeOutput, RawFrame, Session, UnifiedRecord


class Source(ABC):
    """Produces raw bytes from somewhere (TCP socket, file, MQTT topic, ...)."""

    name: str = "source"

    @abstractmethod
    def frames(self) -> AsyncIterator[RawFrame]:
        """Async-iterate ``RawFrame`` objects until the source is exhausted/closed.

        Implemented as ``async def frames(self): ... yield RawFrame(...)``.
        """
        raise NotImplementedError

    async def reply(self, frame: RawFrame, data: bytes) -> None:
        """Write bytes back to the origin of ``frame`` (e.g. a TCP ACK).

        No-op for one-way sources such as file replay.
        """
        return None

    async def close(self) -> None:
        return None


class Codec(ABC):
    """Decodes one device protocol into ``UnifiedRecord`` objects.

    Codecs are instantiated **once per session**, so per-connection state (a learned
    IMEI, a running counter) belongs in ``session.state`` - never on ``self`` if it
    must survive across packets, and never as module globals.
    """

    name: str = "codec"
    display_name: str = "Codec"

    def detect(self, sample: bytes) -> float:
        """Confidence in [0, 1] that this codec can parse ``sample``.

        Used only when a source is configured with codec ``"auto"``. Return a high
        value on a clear signature (magic bytes, sentence prefix) and 0 otherwise.
        """
        return 0.0

    @abstractmethod
    def decode(self, buffer: bytes, session: Session) -> DecodeOutput:
        """Parse the next message(s) from the front of ``buffer``.

        Set ``DecodeOutput.consumed`` to the number of bytes handled. Return
        ``consumed == 0`` to signal "need more bytes" - the pipeline will keep the
        buffer and call again after more data arrives.
        """
        raise NotImplementedError

    def encode_ack(self, record: UnifiedRecord, session: Session) -> Optional[bytes]:
        """Optional standalone ACK builder. Most codecs instead set
        ``DecodeOutput.ack`` directly inside :meth:`decode`."""
        return None


class Sink(ABC):
    """Consumes normalized records: store them, broadcast them, relay them."""

    name: str = "sink"

    async def start(self) -> None:
        return None

    @abstractmethod
    async def handle(self, record: UnifiedRecord) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        return None
