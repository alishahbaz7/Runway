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


# ── definitions a release ships into data/ ───────────────────────────────────────────────────────
# A release carries the protocol definitions (data/protocols) and copies them into the data folder
# on its first start; the copy there is the person's to edit (Configuration → Protocols) and wins.
# With nothing more, a later release could never bring a definition up to date — the Locate 140 4G
# field names would reach a fresh install only. So every release also ships SHIPPED_DEFINITIONS:
# the hash of every version of each definition any release has carried (built from git history by
# packaging/build_release.py). A copy whose hash is in that list is one a release put there,
# untouched — it is replaced by the new one; any other is the person's own edit, and stays.
SHIPPED_DEFINITIONS = "protocols_shipped.json"


def definition_hash(data: bytes) -> str:
    """A definition's hash, line endings aside (a checkout on Windows writes CRLF, git keeps LF)."""
    import hashlib
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def seed_definitions(src, dst, shipped: dict | None = None) -> list[str]:
    """Copy the shipped definitions in ``src`` into the data folder's ``dst``: all of them into an
    empty folder (the first start), and later each one whose copy is a release's own, untouched
    (its hash in ``shipped``). Never a file the person edited, and never one they removed.
    Returns the names written."""
    import shutil
    from pathlib import Path
    src, dst = Path(src), Path(dst)
    if not src.is_dir() or src.resolve() == dst.resolve():
        return []                                # a run from source: the shipped folder IS the data
    dst.mkdir(parents=True, exist_ok=True)
    first = not any(dst.iterdir())
    written = []
    for f in sorted(src.iterdir()):
        if not f.is_file():
            continue
        to = dst / f.name
        if not to.exists():
            if first:
                shutil.copy2(f, to)
                written.append(f.name)
            continue
        have = definition_hash(to.read_bytes())
        if have != definition_hash(f.read_bytes()) and have in set((shipped or {}).get(f.name) or ()):
            shutil.copy2(f, to)
            written.append(f.name)
    return written


def branch(root: str | None = None) -> str | None:
    """The git branch a run from source is on ("v<x.y.z>") — the banner shows it in place of the version,
    so a copy run from a branch is never taken for a release. None in a release build, or outside a
    checkout. Read from .git/HEAD (the banner asks every few seconds; no git process for that); a
    detached HEAD gives its short commit."""
    if FROZEN:
        return None
    root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    git = os.path.join(root, ".git")
    try:
        if os.path.isfile(git):                                   # a worktree: ".git" names the real one
            with open(git, encoding="utf-8") as f:
                line = f.read().strip()
            if not line.startswith("gitdir:"):
                return None
            git = os.path.join(root, line.split(":", 1)[1].strip())
        with open(os.path.join(git, "HEAD"), encoding="utf-8") as f:
            head = f.read().strip()
    except OSError:
        return None
    if head.startswith("ref:"):
        return head.split("/", 2)[-1] if head.startswith("ref: refs/heads/") else head[4:].strip()
    return head[:7] or None
