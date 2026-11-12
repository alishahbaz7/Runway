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
