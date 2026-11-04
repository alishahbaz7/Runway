"""Relay: a listener that forwards each device's raw bytes to the next server, one upstream
connection per device, with that server's replies passed back down — losslessly, through the
outbox — over real sockets."""

import asyncio
from pathlib import Path

import pytest

from runway.core.pipeline import Pipeline
from runway.sources.tcp_server import TcpServerSource, _Link, parse_hostport
from runway.storage.outbox import Outbox

FIX = Path(__file__).parent / "fixtures"
LOGIN = b"$LGN,DL1CAB1234,865577071515754,5.1.30I,SCAN,28.550465,N,77.268908,E*5B\r\n"
IMU = (FIX / "zenithra_imu_sample.log").read_bytes()
IMEI = "865577071515754"


@pytest.fixture(autouse=True)
def fast_links(monkeypatch):
    monkeypatch.setattr(_Link, "BACKOFF", (0.1, 0.2))
    monkeypatch.setattr(_Link, "GRACE", 0.1)


@pytest.fixture
def box(tmp_path):
    b = Outbox(str(tmp_path / "outbox.db"))
    yield b
    b.close()


class NextServer:
    """Stands in for the platform: records every connection's bytes, ACKs each chunk."""

    def __init__(self, reply=b"+##Received OK\r\n", port=0):
        self.reply, self.conns, self._server, self.port = reply, [], None, port

    async def __aenter__(self):
        async def handle(reader, writer):
            got = bytearray()
            self.conns.append((got, writer))
            while data := await reader.read(4096):
                got.extend(data)
                if self.reply:
                    writer.write(self.reply)
                    await writer.drain()
        self._server = await asyncio.start_server(handle, "127.0.0.1", self.port)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        for _, w in self.conns:
            w.close()
        self._server.close()


async def _free_port() -> int:
    probe = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = probe.sockets[0].getsockname()[1]
    probe.close()
    await probe.wait_closed()
    return port


async def _listener(src: TcpServerSource, codec="zenithra"):
    pipe = Pipeline(src, codec, sinks=[])
    task = asyncio.create_task(pipe.run())
    for _ in range(100):
        if src._server is not None:
            break
        await asyncio.sleep(0.01)
    return pipe, task, src._server.sockets[0].getsockname()[1]


async def _wait(cond, secs=5.0):
    for _ in range(int(secs / 0.02)):
        if cond():
            return True
        await asyncio.sleep(0.02)
    return False


async def _stop(src, task):
    await src.close()
    await asyncio.wait_for(task, 3)


def test_parse_hostport():
    assert parse_hostport("10.0.0.5:5001") == ("10.0.0.5", 5001)
    assert parse_hostport("gps.example.com:80") == ("gps.example.com", 80)
    for bad in ("", "host", "host:", ":80", "host:99999", "host:x"):
        with pytest.raises(ValueError):
            parse_hostport(bad)


async def test_relay_forwards_raw_bytes_and_passes_the_servers_acks_down(box):
    async with NextServer() as nxt:
        src = TcpServerSource("127.0.0.1", 0, relay=f"127.0.0.1:{nxt.port}", outbox=box)
        pipe, task, port = await _listener(src)
        reader, dev = await asyncio.open_connection("127.0.0.1", port)
        dev.write(LOGIN)                                  # sent the instant it connects
        dev.write(IMU[:400])                              # a sentence split across two writes
        await dev.drain()
        await asyncio.sleep(0.05)
        dev.write(IMU[400:])
        await dev.drain()

        assert await _wait(lambda: nxt.conns and bytes(nxt.conns[0][0]) == LOGIN + IMU)
        assert await _wait(lambda: pipe.records_emitted == 2)       # still decoded locally
        acks = await asyncio.wait_for(reader.read(4096), 2)
        # only the next server's ACKs come down — Runway adds none of its own while relaying
        assert set(acks.split(b"\r\n")) <= {b"+##Received OK", b""}
        (st,) = src.relay_status().values()
        assert st["state"] == "up" and st["bytes_up"] == len(LOGIN + IMU) and st["bytes_down"] > 0
        assert await _wait(lambda: box.total == 0)                 # delivered rows leave the outbox
        assert len(nxt.conns) == 1                        # one upstream per device
        dev.close()
        await _stop(src, task)


async def test_each_device_gets_its_own_upstream_connection(box):
    async with NextServer(reply=b"") as nxt:
        src = TcpServerSource("127.0.0.1", 0, relay=f"127.0.0.1:{nxt.port}", outbox=box)
        _, task, port = await _listener(src)
        devs = []
        for i in range(2):
            _, w = await asyncio.open_connection("127.0.0.1", port)
            w.write(f"$LGN,X,86557707151575{i},5.1.30I,SCAN*00\r\n".encode())
            await w.drain()
            devs.append(w)
        assert await _wait(lambda: len(nxt.conns) == 2 and all(c[0] for c in nxt.conns))
        assert sorted(bytes(c[0]).split(b",")[2] for c in nxt.conns) == [b"865577071515750",
                                                                          b"865577071515751"]
        for w in devs:
            w.close()
        await _stop(src, task)


async def test_without_a_relay_runway_acks_itself(box):
    src = TcpServerSource("127.0.0.1", 0, outbox=box)
    _, task, port = await _listener(src)
    reader, dev = await asyncio.open_connection("127.0.0.1", port)
    dev.write(IMU)
    await dev.drain()
    assert await asyncio.wait_for(reader.read(4096), 2) == b"+##Received OK"
    dev.close()
    await _stop(src, task)


async def test_server_down_nothing_is_lost_it_all_arrives_when_back(box):
    """The platform is unreachable: the device's packets wait in the outbox (Runway sends no ACK,
    the device stays connected) and arrive, in order, login first, once it is back."""
    port_next = await _free_port()
    src = TcpServerSource("127.0.0.1", 0, relay=f"127.0.0.1:{port_next}", outbox=box)
    pipe, task, port = await _listener(src)
    reader, dev = await asyncio.open_connection("127.0.0.1", port)
    dev.write(LOGIN + IMU)
    await dev.drain()
    assert await _wait(lambda: pipe.records_emitted == 2)
    assert await _wait(lambda: any(s["state"] == "down" for s in src.relay_status().values()))
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(reader.read(4096), 0.3)              # no ACK from anyone
    assert box.total == len(LOGIN + IMU)                            # kept, not dropped
    async with NextServer(port=port_next) as nxt:                   # the platform is back
        assert await _wait(lambda: nxt.conns and bytes(nxt.conns[0][0]) == LOGIN + IMU)
        assert await _wait(lambda: box.total == 0)
        dev.close()
        await _stop(src, task)


async def test_server_closing_the_link_keeps_the_device_and_delivers_after_reconnect(box):
    async with NextServer(reply=b"") as nxt:
        src = TcpServerSource("127.0.0.1", 0, relay=f"127.0.0.1:{nxt.port}", outbox=box)
        _, task, port = await _listener(src)
        reader, dev = await asyncio.open_connection("127.0.0.1", port)
        dev.write(LOGIN)
        await dev.drain()
        assert await _wait(lambda: nxt.conns and bytes(nxt.conns[0][0]) == LOGIN)
        nxt.conns[0][1].close()                           # the platform drops the connection
        await asyncio.sleep(0.2)
        dev.write(IMU)                                    # the device carries on regardless
        await dev.drain()
        # a fresh connection: the device's login first (the server sees a session start), then IMU
        assert await _wait(lambda: len(nxt.conns) == 2 and bytes(nxt.conns[1][0]) == LOGIN + IMU)
        assert not dev.is_closing()
        dev.close()
        await _stop(src, task)


async def test_a_backlog_drains_after_the_device_has_gone(box):
    port_next = await _free_port()
    src = TcpServerSource("127.0.0.1", 0, relay=f"127.0.0.1:{port_next}", outbox=box)
    pipe, task, port = await _listener(src)
    _, dev = await asyncio.open_connection("127.0.0.1", port)
    dev.write(LOGIN + IMU)
    await dev.drain()
    assert await _wait(lambda: pipe.records_emitted == 2)
    dev.close()                                           # it leaves while the platform is down
    await asyncio.sleep(0.3)
    async with NextServer(port=port_next) as nxt:
        assert await _wait(lambda: nxt.conns and bytes(nxt.conns[0][0]) == LOGIN + IMU)
        assert await _wait(lambda: box.total == 0 and not box.links)   # the link ends when done
        await _stop(src, task)


async def test_a_backlog_survives_a_listener_restart(box):
    port_next = await _free_port()
    routes = {"all": f"127.0.0.1:{port_next}"}
    src = TcpServerSource("127.0.0.1", 0, routes=routes, outbox=box)
    pipe, task, port = await _listener(src)
    _, dev = await asyncio.open_connection("127.0.0.1", port)
    dev.write(LOGIN + IMU)
    await dev.drain()
    assert await _wait(lambda: pipe.records_emitted == 2 and box.total == len(LOGIN + IMU))
    dev.close()
    await _stop(src, task)                                # Runway stops; the rows stay on disk
    assert box.total == len(LOGIN + IMU)
    async with NextServer(port=port_next) as nxt:
        src2 = TcpServerSource("127.0.0.1", 0, routes=routes, outbox=box)
        _, task2, _ = await _listener(src2)               # started again: resumes the backlog
        assert await _wait(lambda: nxt.conns and bytes(nxt.conns[0][0]) == LOGIN + IMU)
        await _stop(src2, task2)


def test_the_outbox_is_bounded_and_counts_what_it_drops(tmp_path):
    b = Outbox(str(tmp_path / "o.db"), cap_bytes=1000)
    for i in range(3):
        b.append("d", "h:1", bytes([i]) * 600)
    assert b.total <= 1000 and b.dropped >= 600
    assert b.peek("d", "h:1", 0)[0][1] == bytes([2]) * 600    # the newest kept, the oldest gone
    b.close()


async def test_the_pipeline_frees_a_connection_when_it_ends(box):
    src = TcpServerSource("127.0.0.1", 0, outbox=box)
    pipe, task, port = await _listener(src)
    for _ in range(3):                                    # a device re-dialling
        _, dev = await asyncio.open_connection("127.0.0.1", port)
        dev.write(LOGIN)
        await dev.drain()
        await asyncio.sleep(0.05)
        dev.close()
    assert await _wait(lambda: pipe.records_emitted == 3 and not pipe._sessions and not pipe._codecs)
    assert len(pipe.per_peer) == 3                        # recent connections still listed
    await _stop(src, task)


# --------------------------------------------------------------------------- #
# Routes: per-device targets, changed while the listener runs
# --------------------------------------------------------------------------- #
async def test_a_device_moved_to_another_server_mid_connection_starts_there_with_its_login(box):
    async with NextServer() as a, NextServer() as b:
        src = TcpServerSource("127.0.0.1", 0, routes={"all": f"127.0.0.1:{a.port}"}, outbox=box)
        pipe, task, port = await _listener(src)
        reader, dev = await asyncio.open_connection("127.0.0.1", port)
        dev.write(LOGIN + IMU)
        await dev.drain()
        assert await _wait(lambda: a.conns and bytes(a.conns[0][0]) == LOGIN + IMU)

        await src.set_routes({"all": f"127.0.0.1:{a.port}", "devices": {IMEI: f"127.0.0.1:{b.port}"}})
        dev.write(IMU)
        await dev.drain()
        assert await _wait(lambda: b.conns and bytes(b.conns[0][0]) == LOGIN + IMU)  # login first
        await asyncio.sleep(0.1)
        assert bytes(a.conns[0][0]) == LOGIN + IMU                  # A got nothing more
        (st,) = src.relay_status().values()
        assert st["target"] == f"127.0.0.1:{b.port}" and st["device_id"] == IMEI
        assert not dev.is_closing()                                  # the device never noticed
        dev.close()
        await _stop(src, task)


async def test_turning_a_devices_relay_off_makes_runway_ack_it_again(box):
    async with NextServer(reply=b"") as a:                          # a server that never ACKs
        src = TcpServerSource("127.0.0.1", 0, routes={"all": f"127.0.0.1:{a.port}"}, outbox=box)
        pipe, task, port = await _listener(src)
        reader, dev = await asyncio.open_connection("127.0.0.1", port)
        dev.write(LOGIN)
        await dev.drain()
        assert await _wait(lambda: pipe.records_emitted == 1 and src.relay_status())
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(reader.read(4096), 0.3)          # relayed: Runway is silent
        await src.set_routes({"all": f"127.0.0.1:{a.port}", "devices": {IMEI: "off"}})
        assert src.relay_status() == {}
        dev.write(IMU)
        await dev.drain()
        assert await asyncio.wait_for(reader.read(4096), 2) == b"+##Received OK"
        dev.close()
        await _stop(src, task)


async def test_per_device_rules_apply_from_the_first_byte_and_others_stay_local(box):
    async with NextServer() as b:
        src = TcpServerSource("127.0.0.1", 0, routes={"devices": {IMEI: f"127.0.0.1:{b.port}"}}, outbox=box)
        pipe, task, port = await _listener(src)
        _, dev = await asyncio.open_connection("127.0.0.1", port)
        dev.write(LOGIN + IMU)
        await dev.drain()
        assert await _wait(lambda: b.conns and bytes(b.conns[0][0]) == LOGIN + IMU)

        other_reader, other = await asyncio.open_connection("127.0.0.1", port)   # not in the rules
        other.write(LOGIN.replace(IMEI.encode(), b"865577071515999"))
        await other.drain()
        assert await asyncio.wait_for(other_reader.read(4096), 2) == b"+##Received OK"
        assert len(b.conns) == 1                                    # nothing of it went to B
        for w in (dev, other):
            w.close()
        await _stop(src, task)


async def test_a_device_that_never_identifies_follows_the_all_devices_rule(monkeypatch, box):
    monkeypatch.setattr(TcpServerSource, "IDENTIFY_WAIT", 0.2)
    async with NextServer() as a:
        src = TcpServerSource("127.0.0.1", 0, routes={"all": f"127.0.0.1:{a.port}"}, outbox=box)
        pipe, task, port = await _listener(src, codec="generic_hex")
        _, dev = await asyncio.open_connection("127.0.0.1", port)
        dev.write(b"\x01\x02\x03 not a protocol")
        await dev.drain()
        assert await _wait(lambda: a.conns and bytes(a.conns[0][0]) == b"\x01\x02\x03 not a protocol")
        dev.close()
        await _stop(src, task)


def test_parse_routes_rejects_a_bad_target():
    from runway.sources.tcp_server import parse_routes

    assert parse_routes({"all": "", "devices": {"1": "off", "2": "h:1"}}) == (None, {"1": None, "2": ("h", 1)})
    with pytest.raises(ValueError):
        parse_routes({"devices": {"1": "nohost"}})
