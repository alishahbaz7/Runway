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


def _serve(args) -> None:
    import uvicorn

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
