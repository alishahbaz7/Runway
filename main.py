"""Runway launcher — the simple way to start the app.

    python main.py                 # starts the web server (dashboard + parser) on :8080
    python main.py --port 9000     # ...on a different port
    python main.py replay samples/teltonika_demo.hex --codec teltonika
    python main.py codecs          # list available codecs

This forwards to the real CLI in ``runway/cli.py``. With no sub-command it defaults to
``serve``, so just running (or double-clicking) this file brings the server up.
"""

from __future__ import annotations

import sys
from pathlib import Path

from runway import __version__
from runway.cli import main

SUBCOMMANDS = {"serve", "replay", "listen", "codecs"}
TOP_LEVEL_FLAGS = {"--version", "-h", "--help"}   # answered by the CLI itself, not `serve`


def _git_branch() -> str:
    """Current git branch (or short commit if detached) — read from .git, no subprocess."""
    try:
        head = Path(__file__).resolve().parent / ".git" / "HEAD"
        text = head.read_text(encoding="utf-8").strip()
        if text.startswith("ref:"):
            return text.split("/")[-1]            # refs/heads/v1.0.1 -> v1.0.1
        return text[:8]                           # detached HEAD -> short sha
    except Exception:
        return "unknown"


def _banner() -> None:
    print(f"  Runway  v{__version__}  |  branch: {_git_branch()}", file=sys.stderr)


if __name__ == "__main__":
    # Default to `serve` when no sub-command is given (no args, or only flags like --port).
    if len(sys.argv) == 1 or sys.argv[1] not in SUBCOMMANDS | TOP_LEVEL_FLAGS:
        sys.argv.insert(1, "serve")
    _banner()
    main()
