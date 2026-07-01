"""PyInstaller entry point for the standalone Runway.exe.

Double-clicking the exe (no args) starts the web server and opens the browser — same
default-to-``serve`` behaviour as ``main.py``. ``freeze_support()`` guards against the
Windows frozen-app re-launch loop if anything ever spawns a subprocess.
"""

from __future__ import annotations

import multiprocessing
import sys

from proinfo.cli import main

SUBCOMMANDS = {"serve", "replay", "listen", "codecs"}


def run() -> None:
    multiprocessing.freeze_support()
    # Default to `serve` when no sub-command is given (double-click, or only flags).
    if len(sys.argv) == 1 or sys.argv[1] not in SUBCOMMANDS:
        sys.argv.insert(1, "serve")
    main()


if __name__ == "__main__":
    run()
