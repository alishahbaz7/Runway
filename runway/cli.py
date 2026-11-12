"""Runway command-line interface.

    runway serve                    # launch dashboard + API (default :8080)
    runway replay capture.log --codec gt06
    runway listen --codec teltonika --port 5050
    runway codecs                   # list available protocol codecs
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from . import __version__
from .core import registry
from .core.pipeline import Pipeline


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _build_sinks(args) -> list:
    sinks = [registry.get_sink("console", show_hex=getattr(args, "hex", False))]
    if getattr(args, "db", None):
        sinks.append(registry.get_sink("sqlite_store", path=args.db))
    if getattr(args, "webhook", None):
        sinks.append(registry.get_sink("webhook", url=args.webhook))
    if getattr(args, "relay", None):
        host, _, port = args.relay.rpartition(":")
        sinks.append(registry.get_sink("tcp_relay", host=host, port=int(port),
                                       mode=args.relay_mode))
    return sinks


async def _replay(args) -> None:
    registry.load_plugins()
    from .sources.file_replay import FileReplaySource

    src = FileReplaySource(args.file, fmt=args.fmt, delay=args.delay)
    pipe = Pipeline(src, args.codec, _build_sinks(args))
    n = await pipe.run()
    print(f"\n{n} record(s) parsed from {args.file} ({pipe.bytes_in} bytes).")


async def _listen(args) -> None:
    registry.load_plugins()
    from .sources.tcp_server import TcpServerSource

    src = TcpServerSource(host=args.host, port=args.port)
    pipe = Pipeline(src, args.codec, _build_sinks(args))
    print(f"Listening on {args.host}:{args.port} (codec={args.codec}). Ctrl+C to stop.")
    await pipe.run()


PAGE_GRACE_S = 5.0          # an open Runway page checks in every 4 s


def _runway_answers(url: str, timeout: float = 1.5) -> bool:
    """A Runway server already answers at ``url`` (``?probe=1``: not counted as a page)."""
    import json
    import urllib.request

    try:
        with urllib.request.urlopen(f"{url}/api/health?probe=1", timeout=timeout) as r:
            return "version" in json.load(r)
    except Exception:
        return False


def _port_free(host: str, port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host or "0.0.0.0", port))
            return True
        except OSError:
            return False


def _open_when_ready(url: str) -> None:
    """Show Runway once the server answers: in its own window (Edge's app mode), unless a Runway
    window is already open — after a restart or an update it reconnects by itself. Off Windows,
    a browser tab, unless an open page checks in within a few seconds."""
    import json
    import time
    import urllib.request

    from . import desktop

    probe = f"{url}/api/health?probe=1"
    for _ in range(120):                   # wait until the server accepts connections
        try:
            urllib.request.urlopen(probe, timeout=2).read()
            break
        except Exception:
            time.sleep(0.5)
    # No Runway window anywhere: open at once (a cold start). One open — after a restart, or a copy
    # on another port — counts only if a page of THIS server checks in within a few seconds.
    if desktop.IS_WINDOWS and not desktop.runway_windows():
        desktop.open_app_window(url)
        return
    deadline = time.monotonic() + PAGE_GRACE_S
    while time.monotonic() < deadline:
        try:
            if json.load(urllib.request.urlopen(probe, timeout=2)).get("page_seen_s") is not None:
                print(f"A Runway window is already open — not opening another ({url})")
                return
        except Exception:
            pass
        time.sleep(0.5)
    desktop.open_app_window(url)


def _start_tray(url: str) -> None:
    """The icon by the clock: Open Runway, Logs, Quit Runway (asks first while listeners run)."""
    import os
    import sys

    from . import __version__, desktop

    def listeners() -> int:
        app_mod = sys.modules.get("runway.api.app")
        try:
            return app_mod._running_listeners() if app_mod else 0
        except Exception:
            return 0

    def quit_runway() -> None:
        question = desktop.quit_question(listeners())
        if question and desktop.message_box(question, "Quit Runway?",
                                            desktop.MB_YESNO | desktop.MB_ICONWARNING) != desktop.IDYES:
            return
        from .update.service import shut_down
        shut_down()

    base = getattr(sys, "_MEIPASS", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "packaging"))
    desktop.start_tray(icon_path=os.path.join(base, "runway.ico"),
                       on_open=lambda: desktop.show(url),
                       on_logs=lambda: desktop.open_app_window(url + "/logs.html"),
                       on_quit=quit_runway,
                       tip=lambda: desktop.tray_tip(__version__, listeners()))


def _wait_for_port(host: str, port: int, seconds: float) -> None:
    """After an update the old server is still shutting down when the new one starts: wait until
    it has let go of the port (bounded — uvicorn reports it if the port never frees)."""
    import socket
    import time

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host or "0.0.0.0", port))
                return
            except OSError:
                pass
        time.sleep(0.5)


def _serve(args) -> None:
    import os

    import uvicorn

    from . import desktop, install

    # how this server was started — a restart into a new version serves on the same address
    install.SERVE_ARGS[:] = ["serve", "--host", args.host, "--port", str(args.port)] + \
        (["--no-browser"] if getattr(args, "no_browser", False) else [])

    # 0.0.0.0 means "all interfaces" - the browser should still hit localhost.
    host = "127.0.0.1" if args.host in ("0.0.0.0", "") else args.host
    url = f"http://{host}:{args.port}"

    if getattr(args, "wait_port", 0):
        _wait_for_port(args.host, args.port, args.wait_port)       # a restart into a new version
    elif _runway_answers(url):
        # Runway already runs here (started again from the pinned icon): show it, don't start a second
        print(f"Runway is already running at {url}")
        if not getattr(args, "no_browser", False):
            desktop.show(url)
        return
    elif not _port_free(args.host, args.port):
        text = (f"Port {args.port} is in use by another program, so Runway cannot start.\n\n"
                f"Close that program, or start Runway on another port (serve --port 9000).")
        if install.FROZEN:
            desktop.message_box(text, "Runway", desktop.MB_OK | desktop.MB_ICONERROR)
        print(text)
        raise SystemExit(1)

    if not getattr(args, "no_browser", False):
        import threading

        # Daemon thread: it must never keep Ctrl+C waiting.
        threading.Thread(target=_open_when_ready, args=(url,), daemon=True).start()
        print(f"Opening {url} in its own window unless a Runway window is already open "
              "(use --no-browser to disable)")

    # A release build has no console window: the tray icon is how Runway is seen running and stopped.
    if install.FROZEN or os.environ.get("RUNWAY_TRAY"):
        _start_tray(url)

    uvicorn.run("runway.api.app:app", host=args.host, port=args.port,
                reload=args.reload, log_level="info")


def _codecs(_args) -> None:
    registry.load_plugins()
    print("Available codecs:")
    for c in registry.list_codecs():
        print(f"  {c['name']:<16} {c['display_name']}")


def _print_tx(tx) -> None:
    """Show one OTA hop as the four panes."""
    print(f"\n── {tx.step}: type {tx.message_type} ({tx.type_label})  "
          f"[{tx.key_source} key #{tx.key_code} {tx.key_masked}]")
    print(f"  1. readable request : {tx.readable_request}")
    print(f"  2. encrypted request: {tx.encrypted_request}")
    print(f"     -> {tx.endpoint}  (HTTP {tx.http_status})")
    print(f"  3. encrypted response: {tx.encrypted_response or '(none)'}")
    print(f"  4. decrypted response: {tx.decrypted_response or '(none)'}")
    flag = "ok" if tx.ok else "FAILED"
    chk = "" if tx.response_checksum_ok is None else f", checksum {'ok' if tx.response_checksum_ok else 'BAD'}"
    print(f"     [{flag}{chk}] {tx.note}")


async def _ota(args) -> None:
    import json as _json

    from .ota import ENVIRONMENTS, DeviceKeys, OtaSession

    base_url = args.base_url or ENVIRONMENTS.get(args.env)
    if not base_url:
        raise SystemExit(f"unknown env {args.env!r}; choose {list(ENVIRONMENTS)} or pass --base-url")
    statics = [k.strip() for k in (args.static or "").split(",") if k.strip()]
    if args.action in ("keys", "send") and not statics:
        raise SystemExit("--static KEY1,KEY2,KEY3 is required (the 3 device static keys)")

    keys = DeviceKeys(imei=args.imei, static_keys=statics)
    session = OtaSession(base_url, keys)
    print(f"OTA {args.action} · env={args.env} · {base_url} · imei={args.imei}")

    if args.action == "keys":
        _print_tx(await session.download_keys(message_type=args.type or 1, key_code=args.key_code))
    else:  # send
        body = _json.loads(args.body) if args.body else {"ReqVersion": "1", "imei": args.imei}
        for tx in await session.send(args.type or 3, body, key_code=args.key_code):
            _print_tx(tx)


def _add_relay_opts(p: argparse.ArgumentParser) -> None:
    p.add_argument("--hex", action="store_true", help="print raw hex per record")
    p.add_argument("--db", metavar="PATH", help="also persist to a SQLite file")
    p.add_argument("--webhook", metavar="URL", help="also POST each record as JSON")
    p.add_argument("--relay", metavar="HOST:PORT", help="also relay onward over TCP")
    p.add_argument("--relay-mode", default="raw", choices=["raw", "json"])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="runway",
                                description="Unified telematics packet parser & relay")
    p.add_argument("--version", action="version", version=f"Runway {__version__}")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the web dashboard + API")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8080)
    s.add_argument("--reload", action="store_true", help="auto-reload (dev)")
    s.add_argument("--no-browser", action="store_true",
                   help="don't auto-open the dashboard in a browser")
    s.add_argument("--wait-port", type=float, default=0.0, metavar="SECONDS",
                   help="wait this long for the port to be free (a restart into a new version)")
    s.set_defaults(func=_serve, is_async=False)

    r = sub.add_parser("replay", help="parse a log / .loc / dump file")
    r.add_argument("file")
    r.add_argument("--codec", default="auto")
    r.add_argument("--fmt", default="auto", choices=["auto", "raw", "hex", "hexlines", "log_export"])
    r.add_argument("--delay", type=float, default=0.0, help="seconds between chunks")
    _add_relay_opts(r)
    r.set_defaults(func=_replay, is_async=True)

    li = sub.add_parser("listen", help="run a live TCP listener for devices")
    li.add_argument("--codec", default="auto")
    li.add_argument("--host", default="0.0.0.0")
    li.add_argument("--port", type=int, default=5050)
    _add_relay_opts(li)
    li.set_defaults(func=_listen, is_async=True)

    c = sub.add_parser("codecs", help="list available protocol codecs")
    c.set_defaults(func=_codecs, is_async=False)

    o = sub.add_parser("ota", help="act as a device against the MMI InTouch OTA cloud")
    o.add_argument("action", choices=["keys", "send"],
                   help="keys = download comm keys (type 1); send = send a message (auto-keys)")
    o.add_argument("--env", default="dev", help="dev | prod (or use --base-url)")
    o.add_argument("--base-url", help="override the OTA endpoint base URL")
    o.add_argument("--imei", required=True, help="device serial (IMEI)")
    o.add_argument("--static", help="the 3 static keys, comma-separated hex")
    o.add_argument("--type", type=int, help="message type (keys:1/2, send:3/4/5/6)")
    o.add_argument("--body", help="readable JSON body for send")
    o.add_argument("--key-code", type=int, default=1, help="which key encrypts (1-based)")
    o.set_defaults(func=_ota, is_async=True)
    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    try:
        if getattr(args, "is_async", False):
            asyncio.run(args.func(args))
        else:
            args.func(args)
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
