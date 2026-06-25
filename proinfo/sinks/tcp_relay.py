"""Relay onward over a persistent TCP connection to a platform / server.

Two modes:
  * ``raw``  - forward the original device bytes (``raw_hex``) untouched, so ProInfo
    acts as a transparent pass-through / tap in front of your platform.
  * ``json`` - forward the normalized record as one JSON object per line.

Reconnects lazily on failure so a flaky platform link doesn't stall the pipeline.
"""

from __future__ import annotations

import asyncio
import json
import logging

from ..core.interfaces import Sink
from ..core.models import UnifiedRecord
from ..core.registry import register_sink

log = logging.getLogger("proinfo.tcp_relay")


@register_sink
class TcpRelaySink(Sink):
    name = "tcp_relay"

    def __init__(self, host: str, port: int, mode: str = "raw"):
        self.host = host
        self.port = int(port)
        self.mode = mode  # raw | json
        self._writer: asyncio.StreamWriter | None = None

    async def start(self) -> None:
        await self._connect()

    async def _connect(self) -> None:
        try:
            _, self._writer = await asyncio.open_connection(self.host, self.port)
            log.info("relay connected to %s:%d (%s)", self.host, self.port, self.mode)
        except Exception:
            self._writer = None
            log.exception("relay connect to %s:%d failed", self.host, self.port)

    async def handle(self, record: UnifiedRecord) -> None:
        if self._writer is None:
            await self._connect()
            if self._writer is None:
                return
        try:
            if self.mode == "raw" and record.raw_hex:
                payload = bytes.fromhex(record.raw_hex)
            else:
                payload = (json.dumps(record.model_dump(mode="json")) + "\n").encode()
            self._writer.write(payload)
            await self._writer.drain()
        except Exception:
            log.exception("relay send failed; dropping connection to reconnect")
            self._writer = None

    async def close(self) -> None:
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:
                pass
            self._writer = None
