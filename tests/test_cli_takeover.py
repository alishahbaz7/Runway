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
