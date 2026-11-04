"""Packet archive: one file per device per day, the packets exactly as received, gzipped once
the day is over — and replayed to a platform in order, login first."""

import asyncio
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from runway.core.models import Session
from runway.core.registry import get_codec, load_plugins
from runway.storage.archive import PacketArchive, decode_raw, encode_raw, replay_lines

FIX = Path(__file__).parent / "fixtures"
DEV = "865577071515754"
LOGIN = b"$LGN,DL1CAB1234,865577071515754,5.1.30I,SCAN,28.550465,N,77.268908,E*5B\r\n"


def _records(stream: bytes):
    load_plugins()
    c, s, out = get_codec("zenithra"), Session(id="t"), []
    while stream:
        o = c.decode(stream, s)
        out += o.records
        stream = stream[o.consumed:]
    return out


def _at(rec, when: datetime):
    r = rec.model_copy(deep=True)
    r.received_at = when.astimezone()
    return r


@pytest.fixture
def arch(tmp_path):
    a = PacketArchive(str(tmp_path / "archive"))
    yield a
    a.close()


def test_raw_encoding_is_exact_for_text_and_binary():
    for raw in (LOGIN, b"\x00\x00\x00\x00\x00\x00\x00\x2a\x08\x01", b"$MMI,no terminator*61",
                b"$MMI,lf only*61\n"):
        assert decode_raw(encode_raw(raw)) == raw
    assert encode_raw(LOGIN) == LOGIN[:-2].decode()          # readable: ASCII as it is


def test_a_device_day_is_one_file_appended_across_reconnects(arch):
    imu = (FIX / "zenithra_imu_sample.log").read_bytes()
    recs = _records(LOGIN + imu) + _records(LOGIN + imu)         # it connected twice
    now = datetime.now()
    for r in recs:
        arch.write(_at(r, now))
    (day,) = arch.days()
    assert (day["device_id"], day["day"], day["compressed"]) == (DEV, now.date().isoformat(), False)
    lines = list(arch.lines(DEV, now.date().isoformat()))
    assert [x.kind for x in lines] == ["login", "imu", "login", "imu"]
    assert lines[1].raw == imu                     # LF-only on the wire: kept as hex, comes back exact
    assert lines[0].raw == LOGIN                                  # byte for byte


def test_a_finished_day_is_gzipped_and_still_reads(arch):
    yesterday = datetime.now() - timedelta(days=1)
    for r in _records(LOGIN + (FIX / "zenithra_imu_sample.log").read_bytes()):
        arch.write(_at(r, yesterday))
    arch.close()
    assert arch.compress_finished() == 1
    (day,) = arch.days()
    assert day["compressed"] and day["file"].endswith(".log.gz")
    assert [x.kind for x in arch.lines(DEV, yesterday.date().isoformat())] == ["login", "imu"]
    assert arch.compress_finished() == 0                          # today's file is never touched


def test_replay_of_a_day_that_starts_mid_session_sends_the_last_login_first(arch):
    imu_recs = _records((FIX / "zenithra_imu_sample.log").read_bytes())
    login_rec = _records(LOGIN)[0]
    yesterday, today = datetime.now() - timedelta(days=1), datetime.now()
    arch.write(_at(login_rec, yesterday))                        # session began yesterday
    arch.write(_at(imu_recs[0], today))
    lines = replay_lines(arch, DEV, today.date().isoformat())
    assert [x.kind for x in lines] == ["login", "imu"] and lines[0].raw == LOGIN


async def test_api_lists_downloads_and_replays_a_day_to_a_platform():
    from httpx import ASGITransport, AsyncClient

    from runway.api.app import app
    from runway.storage.archive import archive

    recs = _records(LOGIN + (FIX / "zenithra_imu_sample.log").read_bytes()
                    + (FIX / "zenithra_sample.log").read_bytes().split(b"\n", 1)[1])
    day = date.today().isoformat()
    for r in recs:
        archive.write(_at(r, datetime.now()))
    got = bytearray()

    async def platform(reader, writer):
        while data := await reader.read(65536):
            got.extend(data)
            writer.write(b"+##Received OK\r\n")
            await writer.drain()
    srv = await asyncio.start_server(platform, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        days = (await c.get("/api/archive")).json()
        assert any(d["device_id"] == DEV and d["day"] == day for d in days)
        dl = await c.get(f"/api/archive/{DEV}/{day}")
        assert dl.status_code == 200 and b"\tlogin\t$LGN," in dl.content
        r = (await c.post("/api/archive/replay", json={"device": DEV, "day": day,
                                                       "target": f"127.0.0.1:{port}", "rate": 0})).json()
        for _ in range(100):
            st = next(x for x in (await c.get("/api/archive/replays")).json() if x["id"] == r["id"])
            if st["status"] != "running":
                break
            await asyncio.sleep(0.05)
        bad = await c.post("/api/archive/replay", json={"device": DEV, "day": day, "target": "nohost"})
    srv.close()
    expected = b"".join(x.raw for x in replay_lines(archive, DEV, day))
    assert st["status"] == "done" and st["sent"] == st["total"] == len(replay_lines(archive, DEV, day))
    assert bytes(got) == expected and expected.startswith(LOGIN)    # in order, exactly as received
    assert st["bytes_down"] > 0 and bad.status_code == 400


def test_purge_removes_day_files_past_the_limit_but_never_the_open_day(arch):
    imu = LOGIN + (FIX / "zenithra_imu_sample.log").read_bytes()
    now = datetime.now()
    for days_ago in (40, 10, 0):
        for r in _records(imu):
            arch.write(_at(r, now - timedelta(days=days_ago)))
    assert len(arch.days()) == 3 and arch.size() > 0
    assert arch.purge(30) == 1
    assert sorted(d["day"] for d in arch.days()) == sorted(
        (now - timedelta(days=n)).date().isoformat() for n in (10, 0))
    assert arch.purge(0) == 1 and [d["day"] for d in arch.days()] == [now.date().isoformat()]


async def test_storage_api_reports_sizes_and_sets_retention():
    from httpx import ASGITransport, AsyncClient

    from runway.api.app import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        st = (await c.get("/api/storage")).json()
        assert st["retention"] == {"samples_days": 7, "minute_days": 365, "archive_days": 0}
        assert set(st["sizes"]) == {"telemetry", "archive", "outbox", "outbox_queued"}
        r = await c.put("/api/storage/retention", json={"samples_days": 3, "minute_days": 90, "archive_days": 30})
        assert r.status_code == 200 and r.json()["retention"]["archive_days"] == 30
        for bad in ({"samples_days": 0}, {"samples_days": 30, "minute_days": 10}, {"archive_days": -1}):
            assert (await c.put("/api/storage/retention", json=bad)).status_code == 400
        await c.put("/api/storage/retention", json={})                 # back to the defaults
