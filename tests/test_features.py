"""Which pages a copy shows: the owner's policy (from the usage server's answer, app-control v4)
first, then each page's default — IMU once IMU data exists, Protocols and Signatures under More.
The person's own choices are applied in the page (shell.js); its rule is checked in test_ui."""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from runway import features
from runway import usage as usage_mod
from runway.usage_client import UsageReporter

FACTS = {"platform": "windows", "login": "MMI\\tester", "computer": "BENCH01"}


def test_defaults_more_and_imu_by_data():
    r = features.resolve(lambda name: None, has_imu=False)
    assert r["imu"] == {"state": "hidden", "by": "data"}
    assert r["protocols"] == {"state": "more", "by": "default"} and r["signatures"]["state"] == "more"
    assert r["capture"] == {"state": "shown", "by": "default"}
    assert features.resolve(lambda name: None, has_imu=True)["imu"] == {"state": "shown", "by": "data"}


def test_the_owner_wins_over_every_default():
    said = {"imu": True, "capture": False, "protocols": True}
    r = features.resolve(said.get, has_imu=False)
    assert r["imu"] == {"state": "shown", "by": "owner"}          # even without IMU data
    assert r["capture"] == {"state": "hidden", "by": "owner"}
    assert r["protocols"] == {"state": "shown", "by": "owner"}    # out of More
    assert features.key(r) != features.key(features.resolve(lambda n: None, has_imu=False))


async def test_the_route_reads_the_policy_the_usage_server_answered(tmp_path, monkeypatch):
    def client():
        from runway.api.app import app
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")

    async with client() as c:                                       # reporting off (the test suite)
        d = (await c.get("/api/features")).json()
        h = (await c.get("/api/health")).json()
    assert d["controlled"] is False and d["features"]["protocols"]["state"] == "more"
    assert h["features_key"] == d["key"]

    answer = {"ok": True, "policy": {"features": {"capture": False, "imu": True}, "groups": ["pilot"]}}
    r = UsageReporter(app="Runway", version="9.0.0", data_dir=tmp_path, facts=FACTS, transport=lambda *a, **kw: answer)
    r.start(background=False, at_exit=False)
    r.flush()
    monkeypatch.setattr(usage_mod, "usage", r)
    async with client() as c:
        d2 = (await c.get("/api/features")).json()
        h2 = (await c.get("/api/health")).json()
    r.stop()
    assert d2["controlled"] is True and d2["groups"] == ["pilot"] and d2["policy_at"]
    assert d2["features"]["capture"] == {"state": "hidden", "by": "owner"}
    assert d2["features"]["imu"] == {"state": "shown", "by": "owner"}
    assert h2["features_key"] == d2["key"] != d["key"]              # the page sees the change
