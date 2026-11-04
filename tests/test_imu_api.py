"""IMU endpoints over what a storing listener kept, and the listener's relay/store options."""

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from runway.api.app import app
from runway.core.models import Session
from runway.core.registry import get_codec, load_plugins
from runway.storage.telemetry import telemetry

FIX = Path(__file__).parent / "fixtures"
DEV = "865577071515754"


async def _call(method: str, path: str, **kw):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.request(method, path, **kw)


@pytest.fixture
def stored_imu():
    load_plugins()
    rec = get_codec("zenithra").decode((FIX / "zenithra_imu_sample.log").read_bytes(),
                                       Session(id="t")).records[0]
    telemetry.write(rec)
    return rec.event_time.timestamp()


async def test_imu_devices_and_series_default_to_the_latest_data(stored_imu):
    devs = (await _call("GET", "/api/imu/devices")).json()
    assert any(d["device_id"] == DEV and d["imu_samples"] >= 20 for d in devs)
    s = (await _call("GET", "/api/imu/series", params={"device": DEV})).json()
    assert s["samples"] >= 20 and s["end"] > stored_imu > s["start"]
    assert (await _call("GET", "/api/imu/series", params={"device": "nope"})).status_code == 404


async def test_imu_timing_setting_re_times_the_samples(stored_imu):
    r = await _call("PUT", "/api/imu/timing", json={"ign_on_s": 4, "ign_off_s": 2, "anchor": "first"})
    assert r.json() == {"ign_on_s": 4, "ign_off_s": 2, "anchor": "first", "measure": True}
    s = (await _call("GET", "/api/imu/series", params={
        "device": DEV, "start": stored_imu - 5, "end": stored_imu + 5})).json()
    assert s["t"][0] == pytest.approx(stored_imu) and s["timing"]["anchor"] == "first"
    ts = sorted({round(t, 6) for t in s["t"]})           # the fixture packet may be stored twice
    assert ts[1] - ts[0] == pytest.approx(0.2)                     # 4 s / 20 samples
    assert (await _call("PUT", "/api/imu/timing", json={"ign_on_s": 1})).status_code == 400
    assert (await _call("PUT", "/api/imu/timing", json={"anchor": "mid"})).status_code == 400
    await _call("PUT", "/api/imu/timing", json={})                  # back to the device defaults


async def test_a_storing_listener_is_remembered_until_stopped_by_hand():
    import asyncio

    from runway.api.app import AUTOSTART_KEY
    from runway.storage.live import db

    probe = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = probe.sockets[0].getsockname()[1]
    probe.close()
    await probe.wait_closed()

    r = await _call("POST", "/api/listen", json={"host": "127.0.0.1", "port": port,
                                                 "codec": "zenithra", "store": True})
    assert r.status_code == 200
    job = r.json()["job"]
    assert db.get_setting(AUTOSTART_KEY)[str(port)] == {
        "codec": "zenithra", "host": "127.0.0.1", "port": port, "relay": "", "store": True,
        "routes": {"all": "", "devices": {}}}
    await asyncio.sleep(0.1)

    # store off and relay rules on, while it runs — kept for the restart
    r = await _call("PUT", f"/api/listen/{job['id']}/store", json={"store": False})
    assert r.json()["job"]["store"] is False
    from runway.api.app import _pipelines
    assert "telemetry_store" not in [s.name for s in _pipelines[job["id"]].sinks]
    r = await _call("PUT", f"/api/listen/{job['id']}/routes",
                    json={"all": "10.0.0.9:4047", "devices": {DEV: "10.0.0.8:5000", "X": "off", "Y": ""}})
    assert r.status_code == 200
    assert r.json()["job"]["routes"] == {"all": "10.0.0.9:4047",
                                         "devices": {DEV: "10.0.0.8:5000", "X": "off"}}
    assert _pipelines[job["id"]].source.relay == ("10.0.0.9", 4047)
    assert db.get_setting(AUTOSTART_KEY)[str(port)]["routes"]["devices"] == {DEV: "10.0.0.8:5000", "X": "off"}
    bad = await _call("PUT", f"/api/listen/{job['id']}/routes", json={"devices": {DEV: f"127.0.0.1:{port}"}})
    assert bad.status_code == 400 and "loop" in bad.json()["detail"]
    r = await _call("PUT", f"/api/listen/{job['id']}/store", json={"store": True})
    assert "telemetry_store" in [s.name for s in _pipelines[job["id"]].sinks]

    assert (await _call("POST", f"/api/stop/{job['id']}")).status_code == 200
    assert str(port) not in db.get_setting(AUTOSTART_KEY)
    await asyncio.sleep(0.2)


async def test_listen_validates_the_relay_target():
    bad = await _call("POST", "/api/listen", json={"port": 5999, "relay": "nohost"})
    assert bad.status_code == 400 and "host:port" in bad.json()["detail"]
    loop = await _call("POST", "/api/listen", json={"port": 5999, "relay": "127.0.0.1:5999"})
    assert loop.status_code == 400 and "loop" in loop.json()["detail"]
