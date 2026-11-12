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


PAGE_GRACE_S = 5.0          # an open Runway tab checks in every 4 s


def _open_unless_open(url: str) -> None:
    """Open the dashboard in a browser — but not when a Runway tab is already open. Every
    restart used to add one more tab; an open page checks in with the server within seconds
    (``page_seen_s`` on /api/health), so wait for the server, give pages that long, then decide.
    ``?probe=1`` keeps this check from counting as a page itself."""
    import json
    import time
    import urllib.request
    import webbrowser

    probe = f"{url}/api/health?probe=1"
    for _ in range(120):                   # wait until the server accepts connections
        try:
            urllib.request.urlopen(probe, timeout=2).read()
            break
        except Exception:
            time.sleep(0.5)
    time.sleep(PAGE_GRACE_S)
    try:
        seen = json.load(urllib.request.urlopen(probe, timeout=2)).get("page_seen_s")
    except Exception:
        seen = None
    if seen is not None:
        print(f"A Runway tab is already open — not opening another ({url})")
        return
    try:
        webbrowser.open(url)
    except Exception:
        pass  # no browser available (headless) - the URL is printed anyway


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
    import uvicorn

    from . import install

    # how this server was started — a restart into a new version serves on the same address
    install.SERVE_ARGS[:] = ["serve", "--host", args.host, "--port", str(args.port)] + \
        (["--no-browser"] if getattr(args, "no_browser", False) else [])
    if getattr(args, "wait_port", 0):
        _wait_for_port(args.host, args.port, args.wait_port)

    # 0.0.0.0 means "all interfaces" - the browser should still hit localhost.
    host = "127.0.0.1" if args.host in ("0.0.0.0", "") else args.host
    url = f"http://{host}:{args.port}"

    if not getattr(args, "no_browser", False):
        import threading

        # Daemon thread: it must never keep Ctrl+C waiting.
        threading.Thread(target=_open_unless_open, args=(url,), daemon=True).start()
        print(f"Opening {url} in your browser unless a Runway tab is already open "
              "(use --no-browser to disable)")

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
