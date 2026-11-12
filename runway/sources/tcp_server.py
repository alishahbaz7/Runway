"""TCP server source: accept device connections and stream their bytes.

Many telematics devices are TCP clients that dial home to a host:port. Point a device
(or your relay) at this listener and every connection becomes a Runway session. ACKs
that codecs emit are written back to the correct socket via :meth:`reply`.

**Relay** (``routes={"all": "host:port", "devices": {imei: "host:port" | "off"}}``, or the
shorthand ``relay="host:port"``): Runway sits in front of the next server(s) as a tap. Each
device is delivered to the server its rule names over a connection of its own — a platform
identifies a device by its connection and login, so devices are never multiplexed onto one
socket. Every byte the device sends goes up exactly as received, before any decoding; everything
the server sends back (ACKs, commands) goes down to the device. Runway ACKs only the devices it
does not relay.

**Lossless:** what a relayed device sends goes into the durable outbox first
(``storage/outbox.py``) and leaves it only once the server's connection has taken it, so an
outage, a broken link or a Runway restart loses nothing — the backlog goes out, in order, the
device's login first, when the server is back. See :class:`_Link`.

Rules change while the listener runs (:meth:`TcpServerSource.set_routes`). A new connection is
held until the pipeline names its device (:meth:`identified`, normally its login) so its own
rule applies from the first byte; a device moved to another server is delivered there from then
on, its login first.

**Dead connections are closed.** A cellular device that loses its data session re-dials from a
new address and never closes the old connection — no FIN arrives, and nothing is ever written to
it, so neither side notices. Seen live: one device left 60 such connections open in 40 minutes,
the newest of them keeping its relay link (and the platform's view of it) alive for hours. So a
connection is closed when its device has dialled in again and it has gone quiet
(``REPLACED_AFTER``), when it has sent nothing for ``idle_timeout``, or when the OS's keepalive
probes go unanswered (``KEEPALIVE``).
"""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from typing import AsyncIterator, Awaitable, Callable, Optional

from ..core.interfaces import Source
from ..core.models import RawFrame
from ..core.registry import register_source
from ..storage.outbox import EXECUTOR, Outbox, outbox as default_outbox

log = logging.getLogger("runway.tcp_server")

# on_event(kind, peer): "up" when a device connects, "down" when it disconnects.
EventCb = Callable[[str, str], Awaitable[None]]
Target = tuple[str, int]


def parse_hostport(value: str) -> tuple[str, int]:
    """``"host:port"`` -> ``(host, port)``; raises ValueError on anything else."""
    host, sep, port = (value or "").strip().rpartition(":")
    if not sep or not host or not port.isdigit() or not 0 < int(port) < 65536:
        raise ValueError(f"expected host:port, got {value!r}")
    return host.strip("[]"), int(port)


def parse_routes(routes: dict | None) -> tuple[Optional[Target], dict[str, Optional[Target]]]:
    """``{"all": "host:port" | "", "devices": {id: "host:port" | "off"}}`` -> parsed form.
    ``all`` is where every device goes unless it has its own entry; ``"off"`` = no relay."""
    routes = routes or {}
    every = parse_hostport(routes["all"]) if routes.get("all") else None
    per = {str(dev).strip(): (None if str(t).strip().lower() == "off" else parse_hostport(t))
           for dev, t in (routes.get("devices") or {}).items() if str(dev).strip()}
    return every, per


class _Link:
    """One device's delivery to one server, through the outbox.

    :meth:`push` appends the device's bytes to the outbox — nothing is sent straight from
    memory. The run loop connects (with backoff), sends the device's login first when the queue
    does not already open with it, then the queue oldest-first, then whatever is pushed next.
    Rows are deleted only ``GRACE`` seconds after they went out with the connection still up:
    bytes the kernel had accepted when a link broke are sent again, so a platform may see a few
    twice — never none. A link outlives its device's connection until its queue is empty; a
    device that re-dials meanwhile re-attaches to it.
    """

    CONNECT_TIMEOUT = 10.0
    BACKOFF = (1, 2, 5, 10, 30)
    GRACE = 2.0

    def __init__(self, device: str, host: str, port: int, box: Outbox):
        self.device, self.host, self.port, self.box = device, host, port, box
        self.target = f"{host}:{port}"
        self.peer: str | None = None
        self._dev: asyncio.StreamWriter | None = None
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self.finished = False
        self.state = "connecting"          # connecting | up | down | idle (server closed it, nothing to send)
        self.error: str | None = None
        self.since = time.time()
        self.bytes_up = self.bytes_down = 0
        self.queued_rows = self.queued_bytes = 0
        self.oldest: float | None = None

    # -- device side ---------------------------------------------------------- #
    def attach(self, peer: str, writer: asyncio.StreamWriter) -> None:
        self.peer, self._dev = peer, writer

    def detach(self, writer: asyncio.StreamWriter) -> None:
        if self._dev is writer:
            self.peer, self._dev = None, None
            self._wake.set()

    def push(self, data: bytes) -> None:
        if not data:
            return
        self.queued_rows += 1
        self.queued_bytes += len(data)
        if self.oldest is None:
            self.oldest = time.time()
        fut = asyncio.get_running_loop().run_in_executor(EXECUTOR, self.box.append, self.device,
                                                         self.target, data)
        fut.add_done_callback(lambda _f: self._wake.set())

    def status(self) -> dict:
        return {"target": self.target, "state": self.state, "error": self.error, "since": self.since,
                "bytes_up": self.bytes_up, "bytes_down": self.bytes_down, "queued": self.queued_rows,
                "queued_bytes": self.queued_bytes, "oldest": self.oldest, "dropped": self.box.dropped}

    # -- server side ------------------------------------------------------------ #
    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    def _set(self, state: str, error: str | None = None) -> None:
        if state != self.state:
            self.since = time.time()
        self.state, self.error = state, error

    async def _ex(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(EXECUTOR, fn, *args)

    async def _refresh(self) -> None:
        self.queued_rows, self.queued_bytes, self.oldest = await self._ex(self.box.stats, self.device, self.target)

    async def _run(self) -> None:
        attempt = 0
        try:
            while True:
                await self._refresh()
                if self._dev is None and not self.queued_rows:
                    return                                    # nothing to deliver, nobody to serve
                self._set("connecting", self.error)
                try:
                    reader, writer = await asyncio.wait_for(
                        asyncio.open_connection(self.host, self.port), self.CONNECT_TIMEOUT)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — any connect failure means "down, retry"
                    self._set("down", f"{type(exc).__name__}: {exc}".strip(": "))
                    log.warning("relay %s -> %s unreachable (%s); %d queued", self.device, self.target,
                                self.error, self.queued_rows)
                    await asyncio.sleep(self.BACKOFF[min(attempt, len(self.BACKOFF) - 1)])
                    attempt += 1
                    continue
                attempt = 0
                self._set("up")
                log.info("relay %s -> %s up (%d queued)", self.device, self.target, self.queued_rows)
                done = await self._deliver(reader, writer)
                if done:
                    return
                self._set("down", self.error or "closed by the server")
                await asyncio.sleep(self.BACKOFF[0])
                # Nothing left to send (a platform drops a quiet connection after a minute or
                # two): dial again when the device sends, not at once. Every dial opens with the
                # device's login, so dialling at once made a quiet — or vanished — device look
                # like one logging in every two minutes, for hours.
                self._wake.clear()
                await self._refresh()
                if not self.queued_rows and self._dev is not None:
                    self._set("idle")
                    await self._wake.wait()           # bytes pushed, or the device left
        finally:
            self.finished = True
            self._set("down", self.error)
            if self.box.links.get((self.device, self.target)) is self:
                del self.box.links[(self.device, self.target)]

    async def _deliver(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> bool:
        """Send until the link breaks (False) or there is nothing left and no device (True)."""
        down = asyncio.create_task(self._pump_down(reader))
        sent_until, inflight = 0, []                  # (last row id, when it went out)
        try:
            hello = await self._ex(self.box.hello, self.device)
            if hello and not await self._ex(self.box.first_starts_with, self.device, self.target, hello[:24]):
                writer.write(hello)                   # the server sees a normal session start
                await writer.drain()
                self.bytes_up += len(hello)
            while not down.done():
                now = time.monotonic()
                ripe = [rid for rid, at in inflight if now - at >= self.GRACE]
                if ripe:
                    await self._ex(self.box.delete_upto, self.device, self.target, max(ripe))
                    inflight = [(rid, at) for rid, at in inflight if rid > max(ripe)]
                    await self._refresh()
                batch = await self._ex(self.box.peek, self.device, self.target, sent_until)
                if not batch:
                    if self._dev is None and not inflight:
                        await self._refresh()
                        if not self.queued_rows and self._dev is None:   # none re-attached meanwhile
                            return True
                    self._wake.clear()
                    waiter = asyncio.ensure_future(self._wake.wait())
                    # wake when the oldest in-flight rows ripen, not a whole GRACE later: rows
                    # still here when the server drops the link are sent again
                    ripen = self.GRACE - (time.monotonic() - inflight[0][1]) if inflight else 30
                    await asyncio.wait({waiter, down}, timeout=max(0.05, ripen),
                                       return_when=asyncio.FIRST_COMPLETED)
                    waiter.cancel()
                    continue
                data = b"".join(d for _, d in batch)
                writer.write(data)
                await writer.drain()
                self.bytes_up += len(data)
                sent_until = batch[-1][0]
                inflight.append((sent_until, time.monotonic()))
            return False
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.error = f"{type(exc).__name__}: {exc}"
            return False
        finally:
            down.cancel()
            try:
                writer.close()
            except Exception:
                pass

    async def _pump_down(self, reader: asyncio.StreamReader) -> None:
        """The server's replies (ACKs, commands) straight back to the device, if it is here."""
        while True:
            data = await reader.read(4096)
            if not data:
                return
            self.bytes_down += len(data)
            dev = self._dev
            if dev is not None:
                try:
                    dev.write(data)
                    await dev.drain()
                except Exception:
                    pass


class _Conn:
    """One device connection, as the router sees it."""

    def __init__(self, peer: str, writer: asyncio.StreamWriter):
        self.peer, self.writer = peer, writer
        self.device_id: str | None = None
        self.pending = bytearray()         # bytes held until the device is identified
        self.waiting = False
        self.link: _Link | None = None
        self.timer: asyncio.TimerHandle | None = None
        self.opened = self.last_rx = time.monotonic()


def _keepalive(writer: asyncio.StreamWriter, idle: int, interval: int, count: int) -> None:
    """Have the OS probe a quiet connection: one whose device vanished without closing it then
    errors out, while a device that is only quiet answers and stays. Best effort — the idle
    timeout closes it anyway."""
    sock = writer.get_extra_info("socket")
    if sock is None:
        return
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        for opt, value in (("TCP_KEEPIDLE", idle), ("TCP_KEEPINTVL", interval), ("TCP_KEEPCNT", count)):
            if hasattr(socket, opt):
                sock.setsockopt(socket.IPPROTO_TCP, getattr(socket, opt), value)
    except OSError:
        log.debug("keepalive not set on %s", writer.get_extra_info("peername"))


@register_source
class TcpServerSource(Source):
    """Device listener; optionally a router: each device's raw stream relayed to the server its
    rule names, losslessly (through the outbox). Rules (:meth:`set_routes`) change while the
    listener runs."""

    name = "tcp_server"
    IDENTIFY_WAIT = 3.0                    # longest a new connection is held to learn its device
    HOLD_MAX = 256 * 1024
    # Closing dead connections (see the module docstring). IDLE_TIMEOUT sits well above a quiet
    # device's slowest report (ignition-off / sleep heartbeat): a live device is never cut off.
    IDLE_TIMEOUT = 600.0                   # nothing received for this long -> closed (0 = never)
    REPLACED_AFTER = 30.0                  # an older connection of a device that dialled in again
    SWEEP_EVERY = 15.0
    KEEPALIVE = (120, 10, 3)               # probe after 120 s quiet, every 10 s, 3 unanswered = dead

    def __init__(self, host: str = "0.0.0.0", port: int = 5050, read_size: int = 4096,
                 on_event: Optional[EventCb] = None, relay: str = "",
                 routes: Optional[dict] = None, local_ack: Optional[bool] = None,
                 outbox: Optional[Outbox] = None, idle_timeout: Optional[float] = None):
        self.host = host
        self.port = int(port)
        self.read_size = int(read_size)
        self.on_event = on_event
        self.idle_timeout = self.IDLE_TIMEOUT if idle_timeout is None else float(idle_timeout)
        self._sweeper: asyncio.Task | None = None
        self._all, self._per = parse_routes(routes if routes is not None else {"all": relay})
        # None = Runway ACKs exactly the devices it does not relay (their server ACKs the rest)
        self.local_ack = local_ack
        self.box = outbox or default_outbox
        self._queue: asyncio.Queue[RawFrame | None] = asyncio.Queue()
        self._writers: dict[str, asyncio.StreamWriter] = {}
        self._conns: dict[str, _Conn] = {}
        self._links: set[_Link] = set()            # links this listener started (stopped with it)
        self._server: asyncio.AbstractServer | None = None

    @property
    def relay(self) -> Optional[Target]:
        """The all-devices target (kept for callers that only know one relay)."""
        return self._all

    def peers(self) -> list:
        """Currently connected device peers (``"ip:port"`` strings)."""
        return list(self._writers)

    def relay_status(self) -> dict[str, dict]:
        """Per connected, relayed device: its link to the server its rule names."""
        return {peer: {**c.link.status(), "device_id": c.device_id}
                for peer, c in self._conns.items() if c.link is not None}

    # -- routing ------------------------------------------------------------- #
    def _routing(self) -> bool:
        return self._all is not None or bool(self._per)

    def _targets(self) -> set[str]:
        ts = {self._all} | set(self._per.values())
        return {f"{t[0]}:{t[1]}" for t in ts if t is not None}

    def _resolve(self, device_id: Optional[str]) -> Optional[Target]:
        if device_id is not None and device_id in self._per:
            return self._per[device_id]
        return self._all

    def _link_for(self, device: str, target: Target) -> _Link:
        """The one link for (device, target) — shared, so a device that re-dials re-attaches to
        the link still draining its backlog."""
        key = (device, f"{target[0]}:{target[1]}")
        link = self.box.links.get(key)
        if link is None or link.finished:
            link = _Link(device, target[0], target[1], self.box)
            self.box.links[key] = link
            self._links.add(link)
            link.start()
        return link

    def _open(self, conn: _Conn, target: Target) -> _Link:
        link = self._link_for(conn.device_id or conn.peer, target)
        link.attach(conn.peer, conn.writer)
        conn.link = link
        return link

    async def _settle(self, conn: _Conn) -> None:
        """End the hold: route the connection by its device (or by the all-devices rule when it
        never identified) and hand over what was held — its login included."""
        if not conn.waiting:
            return
        conn.waiting = False
        if conn.timer is not None:
            conn.timer.cancel()
        target = self._resolve(conn.device_id)
        if target is not None:
            self._open(conn, target).push(bytes(conn.pending))
        conn.pending.clear()

    async def _retarget(self, conn: _Conn) -> None:
        """Apply the current rules to a connection that is already routed (or unrouted)."""
        want = self._resolve(conn.device_id)
        have = (conn.link.host, conn.link.port) if conn.link is not None else None
        if want == have:
            return
        if conn.link is not None:
            conn.link.detach(conn.writer)             # it drains what it holds, then ends
            conn.link = None
        if want is not None:
            self._open(conn, want)                    # its login goes first on that link
            log.info("relay %s (%s) now -> %s:%d", conn.peer, conn.device_id or "?", *want)
        else:
            log.info("relay %s (%s) stopped — Runway ACKs it from now on",
                     conn.peer, conn.device_id or "?")

    async def set_routes(self, routes: dict) -> None:
        """Replace the rules while running; every connected device follows them at once."""
        self._all, self._per = parse_routes(routes)
        for conn in list(self._conns.values()):
            if not conn.waiting:
                await self._retarget(conn)
        await self._resume_backlog()

    async def identified(self, peer: str, device_id: str, hello: bytes) -> None:
        conn = self._conns.get(peer)
        if conn is None:
            return
        conn.device_id = device_id
        if hello:                                     # kept for every later (re)connection
            await asyncio.get_running_loop().run_in_executor(EXECUTOR, self.box.set_hello,
                                                             device_id, hello)
        if conn.waiting:
            await self._settle(conn)
        else:
            await self._retarget(conn)                # identified after the hold ran out

    async def _resume_backlog(self) -> None:
        """Deliver what earlier runs left in the outbox for this listener's targets."""
        keys = await asyncio.get_running_loop().run_in_executor(EXECUTOR, self.box.keys)
        mine = self._targets()
        for device, target in keys:
            if target in mine:
                self._link_for(device, parse_hostport(target))

    # -- connections ------------------------------------------------------------ #
    async def _sweep(self) -> None:
        while True:
            await asyncio.sleep(self.SWEEP_EVERY)
            try:
                self.close_dead()
            except Exception:                     # a listener runs for weeks: keep sweeping
                log.exception("closing dead connections failed")

    def close_dead(self) -> int:
        """Close the connections that are dead though still open: an older one of a device that
        has dialled in again, once it has gone quiet, and any that has sent nothing for
        ``idle_timeout``. Their read loops then end and clean up as for any disconnect."""
        now = time.monotonic()
        newest: dict[str, _Conn] = {}
        for conn in self._conns.values():
            if conn.device_id and (conn.device_id not in newest
                                   or conn.opened > newest[conn.device_id].opened):
                newest[conn.device_id] = conn
        closed = 0
        for conn in list(self._conns.values()):
            if conn.writer.is_closing():
                continue
            quiet = now - conn.last_rx
            if self.idle_timeout and quiet >= self.idle_timeout:
                why = f"nothing received for {quiet:.0f} s"
            elif (conn.device_id and newest[conn.device_id] is not conn
                  and quiet >= self.REPLACED_AFTER):
                why = f"device dialled in again from {newest[conn.device_id].peer}"
            else:
                continue
            log.info("closing %s (%s): %s", conn.peer, conn.device_id or "?", why)
            conn.writer.transport.abort()
            closed += 1
        return closed

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
        conn = self._conns[peer] = _Conn(peer, writer)
        _keepalive(writer, *self.KEEPALIVE)
        # Hold the first bytes until the pipeline names the device (its login, normally
        # milliseconds) so a per-device rule applies from the very first byte.
        conn.waiting = self._routing()
        log.info("device connected: %s", peer)
        await self._emit("up", peer)
        try:
            while True:
                data = await reader.read(self.read_size)
                if not data:
                    break
                conn.last_rx = time.monotonic()
                if conn.waiting:
                    # The wait starts with the first bytes: a connection that sends nothing
                    # gets no link to the server at all.
                    if conn.timer is None:
                        conn.timer = asyncio.get_running_loop().call_later(
                            self.IDENTIFY_WAIT, lambda: asyncio.ensure_future(self._settle(conn)))
                    if len(conn.pending) < self.HOLD_MAX:
                        conn.pending.extend(data)
                elif conn.link is not None:
                    if conn.link.finished or conn.link.peer is None:
                        # its device's newer connection ended and took the link down with it:
                        # this one is the device's connection now
                        self._open(conn, (conn.link.host, conn.link.port))
                    conn.link.push(data)            # into the outbox first, untouched
                await self._queue.put(
                    RawFrame(data=data, transport="tcp", peer=peer, source=self.name)
                )
        except (OSError, asyncio.IncompleteReadError):    # incl. TimeoutError: keepalive gave up
            pass
        finally:
            self._writers.pop(peer, None)
            if conn.timer is not None:
                conn.timer.cancel()
            if conn.waiting and conn.pending:        # gone before it identified: still deliver it
                await self._settle(conn)
            if conn.link is not None:
                conn.link.detach(writer)            # the link drains its backlog, then ends
            self._conns.pop(peer, None)
            try:
                writer.close()
            except Exception:
                pass
            log.info("device disconnected: %s", peer)
            await self._queue.put(RawFrame(data=b"", transport="tcp", peer=peer, source=self.name,
                                           closed=True))
            await self._emit("down", peer)

    async def frames(self) -> AsyncIterator[RawFrame]:
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        self._sweeper = asyncio.create_task(self._sweep())
        log.info("TCP listener up on %s:%d%s%s", self.host, self.port,
                 f" — all devices relayed to {self._all[0]}:{self._all[1]}" if self._all else "",
                 f" — {len(self._per)} device rule(s)" if self._per else "")
        if self._routing():
            await self._resume_backlog()
        try:
            while True:
                frame = await self._queue.get()
                if frame is None:
                    break
                yield frame
        finally:
            await self.close()

    async def reply(self, frame: RawFrame, data: bytes) -> None:
        if self.local_ack is None:
            conn = self._conns.get(frame.peer or "")
            if conn is not None and (conn.link is not None or conn.waiting):
                return                      # relayed: its server's ACK goes down instead
        elif not self.local_ack:
            return
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
        if self._sweeper is not None:
            self._sweeper.cancel()
            self._sweeper = None
        if self._server is not None:
            self._server.close()
            # Drop live client connections too. server.close() only stops ACCEPTING new
            # sockets — existing ones stay open, and wait_closed() then blocks on them
            # indefinitely. So a listener stopped while a device is still connected would
            # hang here forever: the pipeline task never finishes cancelling, its job stays
            # in _tasks, and the session can't be removed ("stop the session first" 400s).
            # Closing each writer ends its _handle() read loop so wait_closed() completes;
            # a timeout is a final backstop so teardown can never block.
            for w in list(self._writers.values()):
                try:
                    w.close()
                except Exception:
                    pass
            self._writers.clear()
            try:
                await asyncio.wait_for(self._server.wait_closed(), timeout=2.0)
            except Exception:            # incl. TimeoutError; never let teardown hang
                pass
            self._server = None
        # The session stopped: its links stop too. Undelivered bytes stay in the outbox and go
        # out when a listener relaying to that server starts again.
        for link in list(self._links):
            await link.stop()
        self._links.clear()
        await self._queue.put(None)
