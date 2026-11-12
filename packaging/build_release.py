"""Assemble a Runway release: the launcher install, its zip, latest.json and the release notes.

    python packaging/build_release.py                     # build what is missing, then package
    python packaging/build_release.py --rebuild           # force a fresh program and launcher build
    python packaging/build_release.py --carry-from DIR    # take data/ from this earlier install
    python packaging/build_release.py --no-carry          # leave every data/ folder where it is
    python packaging/build_release.py --minimum X.Y.Z     # copies below X.Y.Z must update (critical fixes only)

Everything carries one name, ``Runway-v<version>`` — the zip, the version folder and the program —
spelled once below (``RELEASE_NAME``; ``Runway.spec`` imports it). The install people pin:

    Runway\\Runway.exe                                  the launcher (never versioned)
    Runway\\README.txt, CHANGELOG.md, samples\\
    Runway\\versions\\Runway-v<version>\\Runway-v<version>.exe  + _internal\\
    Runway\\data\\                                       created on first start, shared by every version

Outputs in dist/:

    Runway-v<version>.zip     unzip anywhere the person can write, pin Runway.exe; copies that
                              already run a launcher install take it as their update
    Runway-v<version>/        the same install, runnable from here (its data/ survives rebuilds)
    latest.json               the version, the notes and the zip's SHA-256 — what copies read
    notes.md                  the CHANGELOG section, for the release text

Publish both files as a GitHub release of the repo named in runway/update/config.py, as the
*latest* release, tag ``v<version>`` — never a pre-release, which copies cannot see.

The build never zips or deletes a ``data/`` folder, and carries an earlier install's ``data/`` into
the new one — unless more than one install under ``dist/`` holds data, in which case it names them
and stops rather than guess which one is live.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from runway import __version__ as VER  # noqa: E402

RELEASE_NAME = f"Runway-v{VER}"
EXE_NAME = f"{RELEASE_NAME}.exe"
LAUNCHER_EXE = "Runway.exe"
INSTALL_FOLDER = "Runway"              # the zip's top folder: the install people unzip and pin
DIST = ROOT / "dist"
BUILD = ROOT / "build"
PROGRAM_DIR = BUILD / "pyinstaller" / RELEASE_NAME
LAUNCHER = BUILD / "launcher" / LAUNCHER_EXE

GUIDE = """\
Runway {ver} — Windows 64-bit
=============================

WHAT IT IS
  Runway parses, visualizes and relays telematics packets from IoT tracking
  devices (AIS-140 / Locate 140B, Zenithra Ax1 sCAN, Teltonika, GT06/Concox,
  NMEA, ...) regardless of OEM/protocol: device QC on the bench, live capture
  and relay, the packet parser, IMU charts, and the device OTA tester.

INSTALL
  Unzip this folder somewhere you can write — not Program Files, and better
  not a OneDrive-synced folder (Desktop and Documents often are). Pin
  Runway.exe to the taskbar or Start: it is the one program to start.

RUN IT
  1. Start Runway.exe. A console window opens (version banner + logs) and your
     browser opens the dashboard at  http://127.0.0.1:8080
       - If Windows SmartScreen warns (unsigned):  More info -> Run anyway.
  2. The first start asks your name once.
  3. To stop: close the console window.

UPDATES
  Runway checks for a newer version each time it starts and offers it; Update
  installs it beside this one and restarts into it. Your data stays where it is.
  The way back: hold Shift while starting Runway.exe and pick an earlier version.

YOUR DATA
  The "data" folder beside Runway.exe holds the database, logs, captures and
  the protocol/signature repositories — shared by every version. Coming from a
  version before the launcher (a Runway-v<x> folder with its own "data"): move
  that "data" folder here, beside Runway.exe, BEFORE the first start.

DEVICE QC (the main workflow)
  1. Capture -> + -> QC. Pick the protocol (or Auto-detect) and a port, bind
     0.0.0.0 so trackers on the LAN can reach it, and Start.
  2. Point the device at  <this-PC-IP>:<port>. As packets arrive the Summary
     fills in: device details, per-item coverage and a live verdict.
  3. Use Report to export the per-lot QC workbook. Fill "Lot details" first.

TRY IT (no device needed)
  - Packet parser: paste samples\\ais140_sample.log (or teltonika_demo.hex) and
    Decode — records, table, map.

COMMAND LINE (optional)
  versions\\{rel}\\{exe} serve --port 9000 --no-browser
  versions\\{rel}\\{exe} codecs
  versions\\{rel}\\{exe} --version

USAGE REPORTING
  Runway reports your name, this computer, the app version and how the app is
  used (time running, pages opened, errors, counts). Never device IMEIs,
  packets, endpoints, keys or files. Settings -> Usage reporting shows it.
"""


def _copy_if(src: Path, dst: Path) -> None:
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _program_folders_with_data(exclude: Path) -> list[Path]:
    """Folders under dist/ that hold a program *and* a data/ folder: an earlier launcher install
    (Runway.exe beside data/) or a pre-launcher release folder (Runway-v<x>.exe beside data/)."""
    candidates = [DIST] + [p for p in DIST.iterdir() if p.is_dir()]
    return [p for p in candidates
            if p != exclude and (p / "data").is_dir() and any(p.glob("*.exe"))]


def _carry_data(stage: Path, carry_from: str | None, no_carry: bool) -> int:
    if (stage / "data").exists():
        print(f"\nData: {stage / 'data'} already exists — left as is (it is this install's own).")
        return 0
    if no_carry:
        print("\nData: not carried (--no-carry).")
        return 0
    if carry_from:
        sources = [Path(carry_from).resolve()]
        if not (sources[0] / "data").is_dir():
            print(f"ERROR: {sources[0]} has no data/ folder", file=sys.stderr)
            return 1
    else:
        sources = _program_folders_with_data(exclude=stage)
    if not sources:
        print("\nData: no earlier install holds data — nothing to carry.")
        return 0
    if len(sources) > 1:
        print(f"\nData NOT carried: {len(sources)} installs hold data, and the build "
              "will not guess which one is live:", file=sys.stderr)
        for p in sources:
            print(f"  {p / 'data'}", file=sys.stderr)
        print("Re-run with --carry-from <folder> to pick one, or --no-carry. "
              "The release zip above is complete either way.", file=sys.stderr)
        return 2
    shutil.copytree(sources[0] / "data", stage / "data")
    print(f"\nData: carried {sources[0] / 'data'}  ->  {stage / 'data'}")
    return 0


def changelog_section(text: str, version: str) -> str:
    """The CHANGELOG section for ``version`` (``## [x.y.z] — …``), without its heading."""
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines)
                  if re.match(rf"^## \[?{re.escape(version)}\]?(\s|$)", ln)), None)
    if start is None:
        raise SystemExit(f"CHANGELOG.md has no section for {version}")
    body = []
    for ln in lines[start + 1:]:
        if ln.startswith("## "):
            break
        body.append(ln)
    return "\n".join(body).strip()


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(*, notes: str, sha: str, size: int, minimum: str | None) -> dict:
    from runway.update import config

    manifest = {
        "app": "Runway",
        "version": VER,
        "published": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "notes": notes,
        "assets": {"windows": {"file": f"{RELEASE_NAME}.zip", "url": config.asset_url(VER),
                               "sha256": sha, "size": size}},
    }
    if minimum:
        manifest["minimumVersion"] = minimum
    return manifest


def _build_program(rebuild: bool) -> None:
    if rebuild or not (PROGRAM_DIR / EXE_NAME).exists():
        print(f"Building {RELEASE_NAME} (one folder) ...")
        subprocess.run([sys.executable, "-m", "PyInstaller", "packaging/Runway.spec", "--noconfirm",
                        "--distpath", str(BUILD / "pyinstaller"), "--workpath", str(BUILD / "pyinstaller-work")],
                       cwd=ROOT, check=True)
    if rebuild or not LAUNCHER.exists():
        print("Building the launcher ...")
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                        str(ROOT / "packaging" / "launcher" / "build.ps1")], cwd=ROOT, check=True)


def main() -> int:
    args = sys.argv[1:]
    rebuild = "--rebuild" in args
    no_carry = "--no-carry" in args
    carry_from = args[args.index("--carry-from") + 1] if "--carry-from" in args else None
    minimum = args[args.index("--minimum") + 1] if "--minimum" in args else None

    from runway import usage_client
    print(f"Packaging {RELEASE_NAME}")
    # A copy released without the usage channel can never be monitored afterwards.
    print(f"  Usage reports go to {usage_client.URL.split('/')[2]}" if usage_client.URL
          else "  WARNING: usage reporting is not set — copies of this release cannot be monitored, ever")

    _build_program(rebuild)
    if not (PROGRAM_DIR / EXE_NAME).exists() or not LAUNCHER.exists():
        print("ERROR: the program or the launcher is missing after the build", file=sys.stderr)
        return 1

    # The staging folder doubles as a runnable install: replace only what the build makes,
    # so a data/ folder created by running it from here survives a rebuild.
    stage = DIST / RELEASE_NAME
    stage.mkdir(parents=True, exist_ok=True)
    for p in stage.iterdir():
        if p.name == "data":
            continue
        shutil.rmtree(p) if p.is_dir() else p.unlink()

    shutil.copy2(LAUNCHER, stage / LAUNCHER_EXE)
    shutil.copytree(PROGRAM_DIR, stage / "versions" / RELEASE_NAME)
    (stage / "README.txt").write_text(GUIDE.format(ver=VER, exe=EXE_NAME, rel=RELEASE_NAME), encoding="utf-8")
    _copy_if(ROOT / "CHANGELOG.md", stage / "CHANGELOG.md")
    _copy_if(ROOT / "samples" / "README.md", stage / "samples" / "README.md")
    # sample inputs to try in the Parser without a device — AIS-140 first, the primary QC protocol
    for name, src in (("ais140_sample.log", ROOT / "tests" / "fixtures" / "ais140_sample.log"),
                      ("zenithra_sample.log", ROOT / "tests" / "fixtures" / "zenithra_sample.log"),
                      ("zenithra_imu_sample.log", ROOT / "tests" / "fixtures" / "zenithra_imu_sample.log"),
                      ("teltonika_demo.hex", ROOT / "samples" / "teltonika_demo.hex"),
                      ("nmea_sample.log", ROOT / "tests" / "fixtures" / "nmea_sample.log")):
        _copy_if(src, stage / "samples" / name)

    files = sorted(p for p in stage.rglob("*")
                   if p.is_file() and p.relative_to(stage).parts[0] != "data")
    zpath = DIST / f"{RELEASE_NAME}.zip"
    if zpath.exists():
        zpath.unlink()
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, Path(INSTALL_FOLDER) / p.relative_to(stage))

    notes = changelog_section((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), VER)
    if not notes:
        print(f"ERROR: the CHANGELOG section for {VER} is empty — copies would be offered an update "
              "with no notes. Write it, then build again.", file=sys.stderr)
        return 1
    sha, size = sha256_of(zpath), zpath.stat().st_size
    (DIST / "latest.json").write_text(json.dumps(build_manifest(notes=notes, sha=sha, size=size, minimum=minimum),
                                                 indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (DIST / "notes.md").write_text(notes + "\n", encoding="utf-8")

    print(f"\nRelease ready: {zpath}  ({size / (1024 * 1024):.1f} MB, sha256 {sha[:16]}…)")
    print(f"  {DIST / 'latest.json'}\n  {DIST / 'notes.md'}")
    print(f"  {len(files)} files; the program: versions/{RELEASE_NAME}/{EXE_NAME}")
    from runway.update import config
    print(f"\nPublish: github.com/{config.REPO} → new release, tag v{VER}, title \"Runway {VER}\", "
          f"the text from notes.md, attach {zpath.name} and latest.json, publish as LATEST (not a pre-release).")
    return _carry_data(stage, carry_from, no_carry)


if __name__ == "__main__":
    raise SystemExit(main())
