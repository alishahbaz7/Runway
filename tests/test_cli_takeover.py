"""main.py: one copy, always the latest code. A Runway on the port running older code (a release,
another checkout, or this one before the last edit) is stopped and the latest starts in its place."""

import os
import socket
import time
from pathlib import Path

import pytest

from runway import cli

ROOT = str(Path(cli.__file__).resolve().parent.parent)


def test_the_same_code_running_is_the_latest():
    assert cli._is_latest({"root": ROOT, "started": time.time() + 5})


def test_a_copy_started_before_the_last_edit_is_not():
    assert not cli._is_latest({"root": ROOT, "started": cli._code_changed_at() - 60})


def test_a_release_or_another_checkout_is_not():
    assert not cli._is_latest({"root": None, "started": time.time() + 5, "version": "1.0.0"})
    assert not cli._is_latest({"root": str(Path(ROOT).parent / "elsewhere"), "started": time.time() + 5})
    assert not cli._is_latest({"version": "1.0.0"})            # an older copy that does not say


def test_main_takes_over_only_when_serving():
    text = (Path(ROOT) / "main.py").read_text(encoding="utf-8")
    assert 'if sys.argv[1] == "serve" and "--take-over" not in sys.argv' in text
    args = cli.build_parser().parse_args(["serve", "--take-over"])
    assert args.take_over is True


@pytest.mark.skipif(os.name != "nt", reason="the port's owner is read from netstat on Windows")
def test_the_ports_owner_is_found():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        assert cli._port_owner(s.getsockname()[1]) == os.getpid()


async def test_only_this_computer_may_stop_runway():
    from httpx import ASGITransport, AsyncClient

    from runway.api.app import app
    async with AsyncClient(transport=ASGITransport(app=app, client=("10.0.0.9", 5000)), base_url="http://t") as c:
        assert (await c.post("/api/app/quit")).status_code == 403


async def test_health_says_where_and_when():
    from httpx import ASGITransport, AsyncClient

    from runway.api.app import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        d = (await c.get("/api/health")).json()
    assert os.path.normcase(d["root"]) == os.path.normcase(ROOT)
    assert d["started"] <= time.time()


# ── a release and a copy run from source, side by side ─────────────────────────────────────────────
def test_each_kind_of_copy_has_its_own_port():
    from runway import install
    assert (install.default_port("release"), install.default_port("dev")) == (8080, 8180)
    assert cli.build_parser().parse_args(["serve"]).port is None             # the copy's own, unless given
    assert cli.build_parser().parse_args(["serve", "--port", "9000"]).port == 9000
    assert install.channel_of({"channel": "release", "root": None}) == "release"
    assert install.channel_of({"version": "1.1.28", "root": ROOT}) == "dev"     # before 1.1.29: by its root
    assert install.channel_of({"version": "1.1.28", "root": None}) == "release"


class _Started(Exception):
    pass


def _serve(monkeypatch, argv, answers):
    """Run serve up to the point it would start the server; ``answers`` is url → health."""
    import uvicorn
    calls = {"stopped": [], "carried": [], "listeners": []}
    monkeypatch.setattr(cli, "_health", lambda url, timeout=1.5: answers.get(url))
    monkeypatch.setattr(cli, "_runway_answers", lambda url, timeout=1.5: url in answers)
    monkeypatch.setattr(cli, "_port_free", lambda host, port: True)
    monkeypatch.setattr(cli, "_listeners_of", lambda url: [{"port": 4082, "from": url}])
    monkeypatch.setattr(cli, "_stop_copy", lambda url, host, port: calls["stopped"].append(url) or True)
    monkeypatch.setattr(cli, "_bring_back", lambda url, listeners: calls["carried"].append((url, listeners)))

    def run(*a, **kw):
        calls["port"] = kw.get("port")
        raise _Started

    monkeypatch.setattr(uvicorn, "run", run)
    args = cli.build_parser().parse_args(argv)
    try:
        cli._serve(args)
    except _Started:
        pass
    import time
    time.sleep(0.2)                                        # the carry-over thread
    return calls


def test_main_moves_this_checkouts_copy_off_the_release_port(monkeypatch):
    """Before 1.1.29 a copy from source served 8080: main.py stops it and starts on 8180 with its listeners."""
    from runway import install
    monkeypatch.setattr(install, "CHANNEL", "dev")
    old = {"version": "1.1.28", "root": ROOT, "started": 0}
    calls = _serve(monkeypatch, ["serve", "--take-over", "--no-browser"], {"http://127.0.0.1:8080": old})
    assert calls["stopped"] == ["http://127.0.0.1:8080"] and calls["port"] == 8180
    assert calls["carried"] == [("http://127.0.0.1:8180", [{"port": 4082, "from": "http://127.0.0.1:8080"}])]


def test_main_never_touches_the_release(monkeypatch):
    from runway import install
    monkeypatch.setattr(install, "CHANNEL", "dev")
    release = {"version": "1.1.29", "root": None, "channel": "release", "started": 0}
    calls = _serve(monkeypatch, ["serve", "--take-over", "--no-browser"], {"http://127.0.0.1:8080": release})
    assert calls["stopped"] == [] and calls["port"] == 8180


def test_a_start_never_shows_the_other_kind_in_its_place(monkeypatch):
    """A release started while a copy from source holds 8080 (or the other way round) says so and stops —
    it does not bring the other copy forward as if it were itself."""
    from runway import install
    monkeypatch.setattr(install, "CHANNEL", "release")
    dev = {"version": "1.1.28", "root": ROOT, "started": 0}
    with pytest.raises(SystemExit):
        _serve(monkeypatch, ["serve", "--no-browser"], {"http://127.0.0.1:8080": dev})


def test_the_same_kind_already_running_is_shown(monkeypatch, capsys):
    from runway import install
    monkeypatch.setattr(install, "CHANNEL", "release")
    release = {"version": "1.1.29", "root": None, "channel": "release", "started": 0}
    calls = _serve(monkeypatch, ["serve", "--no-browser"], {"http://127.0.0.1:8080": release})
    assert "port" not in calls and "already running" in capsys.readouterr().out


async def test_health_says_which_kind_of_copy():
    from httpx import ASGITransport, AsyncClient

    from runway import install
    from runway.api.app import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/api/health")).json()["channel"] == install.CHANNEL
