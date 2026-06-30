"""TCP server source: accept device connections and stream their bytes.

Many telematics devices are TCP clients that dial home to a host:port. Point a device
(or your relay) at this listener and every connection becomes a ProInfo session. ACKs
that codecs emit are written back to the correct socket via :meth:`reply`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Awaitable, Callable, Optional

from ..core.interfaces import Source
from ..core.models import RawFrame
from ..core.registry import register_source

log = logging.getLogger("proinfo.tcp_server")

# on_event(kind, peer): "up" when a device connects, "down" when it disconnects.
EventCb = Callable[[str, str], Awaitable[None]]


@register_source
class TcpServerSource(Source):
    name = "tcp_server"

    def __init__(self, host: str = "0.0.0.0", port: int = 5050, read_size: int = 4096,
                 on_event: Optional[EventCb] = None):
        self.host = host
        self.port = int(port)
        self.read_size = int(read_size)
        self.on_event = on_event
        self._queue: asyncio.Queue[RawFrame | None] = asyncio.Queue()
        self._writers: dict[str, asyncio.StreamWriter] = {}
        self._server: asyncio.AbstractServer | None = None

    def peers(self) -> list:
        """Currently connected device peers (``"ip:port"`` strings)."""
        return list(self._writers)

    async def _emit(self, kind: str, peer: str) -> None:
        if self.on_event is None:
            return
        try:
            await self.on_event(kind, peer)
        except Exception:
            log.exception("on_event callback failed (%s %s)", kind, peer)

    async def _handle(self, reader: asyncio.StreamReader,
                      writer: asyncio.StreamWriter) -> None:
        peername = writer.get_extra_info("peername") or ("?", 0)
        peer = f"{peername[0]}:{peername[1]}"
        self._writers[peer] = writer
        log.info("device connected: %s", peer)
        await self._emit("up", peer)
        try:
            while True:
                data = await reader.read(self.read_size)
                if not data:
                    break
                await self._queue.put(
                    RawFrame(data=data, transport="tcp", peer=peer, source=self.name)
                )
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self._writers.pop(peer, None)
            try:
                writer.close()
            except Exception:
                pass
            log.info("device disconnected: %s", peer)
            await self._emit("down", peer)

    async def frames(self) -> AsyncIterator[RawFrame]:
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        log.info("TCP listener up on %s:%d", self.host, self.port)
        try:
            while True:
                frame = await self._queue.get()
                if frame is None:
                    break
                yield frame
        finally:
            await self.close()

    async def reply(self, frame: RawFrame, data: bytes) -> None:
        await self.send(frame.peer or "", data)

    async def send(self, peer: str, data: bytes) -> bool:
        """Write bytes back to a connected peer (downlink/ACK). Returns success."""
        writer = self._writers.get(peer or "")
        if writer is None:
            return False
        try:
            writer.write(data)
            await writer.drain()
            return True
        except Exception:
            log.exception("failed to send to %s", peer)
            return False

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None
        await self._queue.put(None)
