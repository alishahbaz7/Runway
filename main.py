"""ProInfo launcher — the simple way to start the app.

    python main.py                 # starts the web server (dashboard + parser) on :8080
    python main.py --port 9000     # ...on a different port
    python main.py replay samples/teltonika_demo.hex --codec teltonika
    python main.py codecs          # list available codecs

This forwards to the real CLI in ``proinfo/cli.py``. With no sub-command it defaults to
``serve``, so just running (or double-clicking) this file brings the server up.
"""

from __future__ import annotations

import sys

from proinfo.cli import main

SUBCOMMANDS = {"serve", "replay", "listen", "codecs"}

if __name__ == "__main__":
    # Default to `serve` when no sub-command is given (no args, or only flags like --port).
    if len(sys.argv) == 1 or sys.argv[1] not in SUBCOMMANDS:
        sys.argv.insert(1, "serve")
    main()
