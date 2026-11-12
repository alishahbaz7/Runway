"""The launcher install — unpack a release beside the running version, rename it into place, keep
three versions.

    <root>\\Runway.exe                                   launcher, pinned, never versioned
    <root>\\data\\                                       shared by every version
    <root>\\versions\\Runway-v<x.y.z>\\Runway-v<x.y.z>.exe  + _internal\\ (PyInstaller one-folder)
    <root>\\versions\\.download\\                         downloads waiting to be verified

A release zip holds ``Runway/Runway.exe`` (the launcher) and ``Runway/versions/Runway-v<x>/…``;
a bare ``Runway-v<x>/…`` folder works too. Files are written to ``<name>.partial`` and renamed at
the end, so an interrupted install never leaves a half version the launcher would start.
"""

from __future__ import annotations

import os
import shutil
import time
import zipfile
from dataclasses import dataclass

from .. import install
from .semver import SemVer
from .transport import UpdateError


@dataclass
class InstallResult:
    folder: str
    launcher_updated: bool


def versions_dir(root: str) -> str:
    return os.path.join(root, install.VERSIONS_DIR)


def staging_dir(root: str) -> str:
    return os.path.join(versions_dir(root), ".download")


def _version_of(folder_name: str) -> SemVer | None:
    m = install.VERSION_FOLDER.match(folder_name)
    return SemVer(*(int(g) for g in m.groups())) if m else None


def installed(root: str) -> list[tuple[SemVer, str]]:
    """Complete versions (folder name right and its program present), newest first."""
    vdir = versions_dir(root)
    out = []
    if os.path.isdir(vdir):
        for name in os.listdir(vdir):
            v = _version_of(name)
            path = os.path.join(vdir, name)
            if v and os.path.isfile(os.path.join(path, name + ".exe")):
                out.append((v, path))
    return sorted(out, key=lambda t: t[0], reverse=True)


def install_from_zip(zip_path: str, root: str, version: str) -> InstallResult:
    name = install.release_name(version)
    vdir = versions_dir(root)
    os.makedirs(vdir, exist_ok=True)
    partial = os.path.join(vdir, name + ".partial")
    if os.path.exists(partial):
        shutil.rmtree(partial, ignore_errors=True)

    launcher_bytes: bytes | None = None
    found_program = False
    try:
        with zipfile.ZipFile(zip_path) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                path = info.filename.replace("\\", "/")
                parts = [p for p in path.split("/") if p]
                if path.startswith("/") or ":" in path or ".." in parts:
                    raise UpdateError(f"The package contains an unsafe path: {info.filename}")
                if name in parts and parts.index(name) < len(parts) - 1:
                    rel = parts[parts.index(name) + 1:]
                    if rel == [name + ".exe"]:
                        found_program = True
                    target = os.path.join(partial, *rel)
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with z.open(info) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst, 1024 * 1024)
                elif parts and parts[-1] == install.LAUNCHER_EXE and install.VERSIONS_DIR not in parts:
                    launcher_bytes = z.read(info)
    except zipfile.BadZipFile:
        shutil.rmtree(partial, ignore_errors=True)
        raise UpdateError("The package is not a valid zip") from None
    except UpdateError:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    if not found_program:
        shutil.rmtree(partial, ignore_errors=True)
        raise UpdateError(f"The package does not contain {name}.exe")

    target = os.path.join(vdir, name)
    if os.path.exists(target):              # a leftover of the same version: move it aside first
        old = f"{target}.old-{int(time.time() * 1000)}"
        os.replace(target, old)
        shutil.rmtree(old, ignore_errors=True)     # in use? left for the next cleanup
    os.replace(partial, target)

    return InstallResult(folder=target, launcher_updated=_refresh_launcher(root, launcher_bytes))


def _refresh_launcher(root: str, data: bytes | None) -> bool:
    """Replaces the launcher when the release carries a different one. If it is in use, the old one
    keeps working and the next update tries again."""
    if data is None:
        return False
    current = os.path.join(root, install.LAUNCHER_EXE)
    try:
        if os.path.isfile(current):
            with open(current, "rb") as f:
                if f.read() == data:
                    return False
        fresh = current + ".new"
        with open(fresh, "wb") as f:
            f.write(data)
        os.replace(fresh, current)
        return True
    except OSError:
        return False


def cleanup(root: str, current: SemVer | None) -> list[str]:
    """Keeps the running version, the newest, and the newest below the running one (the way back);
    removes every other version folder and leftovers (``.partial``, ``.old-…``, the download
    folder). A folder in use is skipped until a later start."""
    vdir = versions_dir(root)
    removed: list[str] = []
    if not os.path.isdir(vdir):
        return removed
    have = installed(root)
    keep = set()
    if current is not None:
        keep.add(current)
    if have:
        keep.add(have[0][0])
    below = [v for v, _ in have if current is None or v < current]
    if below:
        keep.add(below[0])
    for name in os.listdir(vdir):
        path = os.path.join(vdir, name)
        if not os.path.isdir(path):
            continue
        v = _version_of(name)
        leftover = name.endswith(".partial") or ".old-" in name or name == ".download"
        if not leftover and not (v is not None and v not in keep):
            continue
        try:
            shutil.rmtree(path)
            removed.append(name)
        except OSError:
            pass
    return removed
