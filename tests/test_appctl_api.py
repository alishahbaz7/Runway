"""Usage reporting and updates through the API — the first-start name, Settings' facts, the update
routes. No test here reaches the usage server or GitHub: reporting runs with a fake transport, the
updater with a fake channel.
"""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from runway import usage as usage_mod
from runway.api import appctl
from runway.update.service import UpdateService
from runway.usage_client import UsageReporter

FACTS = {"platform": "windows", "os": "Windows 11 Pro (build 26300)", "login": "MMI\\tester", "computer": "BENCH01"}


def _client():
    from runway.api.app import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.fixture
def reporting(tmp_path, monkeypatch):
    """An enabled reporter that records what it would send, instead of sending it."""
    sent = []
    r = UsageReporter(app="Runway", version="9.0.0", data_dir=tmp_path, enabled=True, facts=FACTS,
                      transport=lambda url, key, report, **kw: sent.append(report))
    r.metrics = usage_mod._metrics                 # Runway's own counters, as in runway/usage.py
    r.start(background=False, at_exit=False)
    monkeypatch.setattr(usage_mod, "usage", r)
    yield r, sent
    r.stop()


async def test_a_development_run_reports_nothing():
    async with _client() as c:
        s = (await c.get("/api/usage")).json()
    assert s["enabled"] is False and s["needs_name"] is False
    assert s["disabled_reason"] == "Development runs do not report usage"


async def test_the_first_start_asks_the_name_once(reporting):
    r, sent = reporting
    async with _client() as c:
        assert (await c.get("/api/usage")).json()["needs_name"] is True
        assert (await c.post("/api/usage/name", json={"name": "  "})).status_code == 400
        s = (await c.post("/api/usage/name", json={"name": "Shahbaz"})).json()
    assert s["name"] == "Shahbaz" and s["needs_name"] is False
    r.flush()
    assert sent[-1]["install"]["name"] == "Shahbaz" and sent[-1]["install"]["app"] == "Runway"


async def test_pages_and_counts_are_reported_never_contents(reporting):
    r, sent = reporting
    usage_mod.count("listenersStarted")
    async with _client() as c:
        await c.get("/parser.html")
        await c.get("/")
        await c.get("/api/health")                         # an API call is not a page
    r.tick()                                               # measures, then sends (first send of the run)
    with_session = [rep for rep in sent if rep["sessions"]]
    session = with_session[-1]["sessions"][-1]
    assert session["pages"] == {"parser": 1, "dashboard": 1}
    assert session["metrics"]["listenersStarted"] >= 1
    report = json.dumps(with_session[-1])
    assert "teltonika" not in report.lower() and "imei" not in report.lower()


def test_page_names_come_from_the_file():
    assert usage_mod.page_of("/") == "dashboard"
    assert usage_mod.page_of("/index.html") == "dashboard"
    assert usage_mod.page_of("/capture.html") == "capture"
    assert usage_mod.page_of("/style.css") is None and usage_mod.page_of("/api/x.html") is None


@pytest.fixture
def updates(tmp_path, monkeypatch):
    m = {"app": "Runway", "version": "9.9.0", "notes": "Better things",
         "assets": {"windows": {"file": "Runway-v9.9.0.zip", "sha256": "c" * 64, "size": 1}}}
    events = []
    u = UpdateService(data_dir=str(tmp_path), current="9.0.0", enabled=True, root=None,
                      manifest_url="https://example.test/latest.json", get_text=lambda url: json.dumps(m),
                      on_event=lambda k, d: events.append(k), running_listeners=lambda: 2)
    u.check()
    monkeypatch.setattr(appctl, "updates", u)
    return u, events


async def test_the_update_routes(updates):
    u, events = updates
    async with _client() as c:
        s = (await c.get("/api/update")).json()
        assert s["available"] and s["latest"] == "9.9.0" and s["notes"] == "Better things"
        assert s["listeners"] == 2 and s["offered"] is False
        assert (await c.post("/api/update/offered")).json()["offered"] is True
        await c.post("/api/update/offered")                       # once per run
        await c.post("/api/update/later")
        s = (await c.post("/api/update/install")).json()          # not a launcher install here
    assert s["open"].startswith("https://github.com/")
    assert events == ["update_offered", "update_postponed"]
