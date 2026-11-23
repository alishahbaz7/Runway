"""Runway on the desktop — the parts that can be checked without a screen: what Quit asks, the tray's
tip, the app-window command, and how a second start recognises a Runway already running.
(The window and the tray icon themselves were checked by driving them on Windows.)"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from runway import cli, desktop


def test_quit_asks_only_while_listeners_run():
    assert desktop.quit_question(0) is None
    assert desktop.quit_question(1).startswith("1 listener is running")
    assert desktop.quit_question(3).startswith("3 listeners are running")
    assert "Quit Runway?" in desktop.quit_question(2)


def test_the_tray_tip_says_the_version_and_what_runs():
    assert desktop.tray_tip("9.1.0", 0) == "Runway 9.1.0 · idle"
    assert desktop.tray_tip("9.1.0", 2) == "Runway 9.1.0 · 2 listeners running"


def test_the_app_window_is_edges_app_mode():
    cmd = desktop.app_window_command(r"C:\Edge\msedge.exe", "http://127.0.0.1:8080")
    assert cmd[0].endswith("msedge.exe") and "--app=http://127.0.0.1:8080" in cmd


def test_the_window_has_its_own_profile_and_follows_the_theme():
    """Its own Edge profile, so its options apply while the person's Edge runs; Edge's dark mode
    while Runway is dark — the title bar then matches the banner (the user, 13-11-2026)."""
    dark = desktop.app_window_command("msedge.exe", "http://x", profile_dir=r"D:\r\data\window", dark=True)
    assert r"--user-data-dir=D:\r\data\window" in dark and "--force-dark-mode" in dark
    light = desktop.app_window_command("msedge.exe", "http://x", profile_dir=r"D:\r\data\window", dark=False)
    assert "--force-dark-mode" not in light


def test_the_title_bar_colour_is_windows_colorref():
    assert desktop._colorref("#171918") == 0x181917          # 0x00BBGGRR
    assert desktop._colorref("ffffff") == 0xFFFFFF


async def test_the_page_tells_the_window_its_theme(monkeypatch):
    """The page sends its banner colour and theme; the theme is remembered for the next window.
    Bad colours are refused. (No real window is touched: the recolouring is stubbed.)"""
    from httpx import ASGITransport, AsyncClient

    from runway.api.app import app
    from runway.storage.live import config

    monkeypatch.setattr(desktop, "color_title_bars", lambda bg, fg, dark: 0)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.post("/api/desktop/titlebar", json={"background": "red", "text": "#ffffff", "dark": True})).status_code == 400
        r = await c.post("/api/desktop/titlebar", json={"background": "#ffffff", "text": "#171717", "dark": False})
        assert r.status_code == 200 and config.get("ui_theme") == "light"
        await c.post("/api/desktop/titlebar", json={"background": "#171918", "text": "#edefee", "dark": True})
    assert config.get("ui_theme") == "dark"


async def test_a_window_in_the_other_mode_reopens_on_the_same_page(monkeypatch):
    """The user, 17-11-2026: in the light theme the title bar stayed dark. Edge fixes its dark mode
    for the window's process when it opens, so a theme switched in Runway's own window reopens it on
    the same page; a browser tab, or a window already in that mode, is left alone."""
    import time

    from httpx import ASGITransport, AsyncClient

    from runway.api.app import app

    calls = []
    monkeypatch.setattr(desktop, "color_title_bars", lambda bg, fg, dark: 1)
    monkeypatch.setattr(desktop, "window_dark", lambda profile: True)            # opened dark
    monkeypatch.setattr(desktop, "reopen_window", lambda url, profile, dark: calls.append((url, dark)))
    page = "http://127.0.0.1:8080/analysis.html?id=x"
    light = {"background": "#ffffff", "text": "#171717", "dark": False}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/desktop/titlebar", json={**light, "app": True, "url": page})
        assert r.json()["reopen"] is True
        tab = await c.post("/api/desktop/titlebar", json={**light, "app": False, "url": page})
        assert tab.json()["reopen"] is False                                    # a browser tab: not its window
        same = await c.post("/api/desktop/titlebar", json={"background": "#171918", "text": "#edefee", "dark": True,
                                                           "app": True, "url": page})
        assert same.json()["reopen"] is False                                   # already dark
    time.sleep(0.2)
    assert calls == [(page, False)]


def test_a_window_already_in_the_mode_is_not_reopened(monkeypatch):
    monkeypatch.setattr(desktop, "window_process", lambda profile: (4242, False))
    monkeypatch.setattr(desktop, "_last_reopen", [0.0])
    assert desktop.reopen_window("http://127.0.0.1:8080/", "x", False) is False


def test_a_browser_is_found_where_one_is_installed():
    path = desktop.find_browser()
    assert path is None or os.path.isfile(path)
    if sys.platform == "win32":
        assert path, "Edge ships with Windows 10/11"


def test_a_second_start_recognises_a_running_runway():
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = json.dumps({"status": "ok", "version": "9.1.0"}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        assert cli._runway_answers(f"http://127.0.0.1:{port}")
        assert not cli._port_free("127.0.0.1", port)               # the port is taken by it
    finally:
        srv.shutdown()
        srv.server_close()
    with socket.socket() as s:                                    # a port nobody serves
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    assert not cli._runway_answers(f"http://127.0.0.1:{free}", timeout=0.5)
    assert cli._port_free("127.0.0.1", free)


def test_a_windowed_program_gets_quiet_streams(monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))
    import runway_main

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(sys, "argv", ["Runway.exe"])              # double-clicked: serve, no console
    runway_main._streams()
    assert sys.stdout is not None and sys.stderr is not None
    print("goes nowhere, fails nothing")
