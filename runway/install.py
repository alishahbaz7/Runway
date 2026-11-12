"""How this copy of Runway runs — one place the data folder, usage reporting and the updater read.

A release is the frozen build (PyInstaller). Installed by the launcher it sits in a version folder:

    <install>\\Runway.exe                                   the launcher people pin
    <install>\\data\\                                       shared by every version
    <install>\\versions\\Runway-v<x.y.z>\\Runway-v<x.y.z>.exe  one folder per version

A copy unzipped by hand (no ``versions`` above it) and a development run from source are not
launcher installs: their data sits beside the program (or in the repo), and an update opens the
release page instead of installing in place.
"""

from __future__ import annotations

import os
import re
import sys

from . import __version__

APP_NAME = "Runway"                  # as copies are named in the usage list and in latest.json
LAUNCHER_EXE = "Runway.exe"
VERSIONS_DIR = "versions"
VERSION_FOLDER = re.compile(r"^Runway-v(\d+)\.(\d+)\.(\d+)$")

FROZEN = bool(getattr(sys, "frozen", False))
# release: a frozen build (reports usage, checks for updates); dev: a run from source (does neither,
# unless pointed at a test server on purpose — RUNWAY_USAGE_URL / RUNWAY_UPDATE_MANIFEST_URL).
CHANNEL = os.environ.get("RUNWAY_CHANNEL") or ("release" if FROZEN else "dev")

# How the running server was started, so a restart into a new version serves on the same address.
# Set by the CLI's `serve` (same process as the app).
SERVE_ARGS: list[str] = []


def release_name(version: str = __version__) -> str:
    """Everything in a release carries this one name: the zip, the version folder, the program."""
    return f"{APP_NAME}-v{version}"


def launcher_root(executable: str | None = None) -> str | None:
    """The install folder when the program runs from ``<install>/versions/Runway-v<x>/``, else None."""
    exe = os.path.abspath(executable or sys.executable)
    version_dir = os.path.dirname(exe)
    versions = os.path.dirname(version_dir)
    name = os.path.basename(version_dir)
    if os.path.basename(versions).lower() != VERSIONS_DIR:
        return None
    if not VERSION_FOLDER.match(name):
        return None
    if os.path.splitext(os.path.basename(exe))[0] != name:
        return None
    return os.path.dirname(versions)


def base_dir() -> str:
    """The folder that holds ``data/``: the install folder of a launcher install (shared by every
    version), beside the program for a copy unzipped by hand, the repo root from source."""
    if FROZEN:
        return launcher_root() or os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
