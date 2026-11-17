"""PyInstaller entry point for Runway-v<version>.exe.

Started with no arguments (the launcher, a pinned icon) it serves and opens Runway in its own
window, with its icon by the clock — no console window: the program is built windowed. Run from a
terminal with a command (``--version``, ``codecs``, ``replay …``) it prints in that terminal.
``freeze_support()`` guards against the Windows frozen-app re-launch loop if anything ever spawns
a subprocess.
"""

from __future__ import annotations

import multiprocessing
import os
import sys
import traceback

SUBCOMMANDS = {"serve", "replay", "listen", "codecs", "ota"}
TOP_LEVEL_FLAGS = {"--version", "-h", "--help"}   # answered by the CLI itself, not `serve`


def _streams() -> None:
    """A windowed program has no stdout/stderr. A command run from a terminal writes to that
    terminal; the server writes its log to data/runway.log and needs no console at all."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    wants_console = len(sys.argv) > 1 and sys.argv[1] != "serve"
    if wants_console and sys.platform == "win32":
        import ctypes

        if ctypes.windll.kernel32.AttachConsole(-1):              # the terminal it was run from
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
            sys.stderr = sys.stdout
            return
    sys.stdout = open(os.devnull, "w")
    sys.stderr = open(os.devnull, "w")


def _report_crash(exc: BaseException) -> None:
    """Runway could not start: say so in a box, with the details in data/runway.crash.log."""
    path = None
    try:
        from runway import install

        folder = os.path.join(install.base_dir(), "data")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "runway.crash.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)) + "\n")
    except Exception:
        pass
    try:
        from runway import desktop

        desktop.message_box(f"Runway could not start: {type(exc).__name__}: {exc}"
                            + (f"\n\nDetails: {path}" if path else ""), "Runway",
                            desktop.MB_OK | desktop.MB_ICONERROR)
    except Exception:
        pass


def run() -> None:
    multiprocessing.freeze_support()
    _streams()
    # Default to `serve` when no sub-command is given (double-click, or only flags).
    if len(sys.argv) == 1 or sys.argv[1] not in SUBCOMMANDS | TOP_LEVEL_FLAGS:
        sys.argv.insert(1, "serve")
    try:
        from runway.cli import main

        main()
    except SystemExit:
        raise
    except BaseException as exc:          # noqa: BLE001 — a windowed program must not fail silently
        _report_crash(exc)
        raise SystemExit(1)


if __name__ == "__main__":
    run()
