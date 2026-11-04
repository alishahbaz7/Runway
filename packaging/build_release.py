"""Assemble a shareable release ZIP around the standalone exe.

    python packaging/build_release.py                     # build exe if missing, then zip
    python packaging/build_release.py --rebuild           # force a fresh PyInstaller build
    python packaging/build_release.py --carry-from DIR    # take data/ from this program folder
    python packaging/build_release.py --no-carry          # leave every data/ folder where it is

Everything in a release carries one name, ``Runway-v<version>`` — the zip, the folder it
unpacks to (also the working build folder ``dist/Runway-v<version>/``) and the exe itself::

    Runway-v<version>.exe   the standalone app (no Python needed)
    GETTING_STARTED.txt     how to run + test
    CHANGELOG.md            what's in this build
    samples/                demo inputs (hex dump, NMEA log) to try in the Parser

``RELEASE_NAME`` below is the only place that name is spelled; ``Runway.spec`` imports it.

The exe keeps its ``data/`` folder beside itself, so a new version's folder starts empty.
The build never zips or deletes a ``data/`` folder, and carries the previous program
folder's ``data/`` into the new one — unless more than one folder under ``dist/`` holds
data, in which case it names them and stops rather than guess which one is live.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from runway import __version__ as VER  # noqa: E402

RELEASE_NAME = f"Runway-v{VER}"
EXE_NAME = f"{RELEASE_NAME}.exe"
DIST = ROOT / "dist"

GUIDE = """\
Runway {ver} — standalone build (Windows 64-bit)
==================================================

WHAT IT IS
  Runway parses, visualizes and relays telematics packets from IoT tracking
  devices (AIS-140 / Locate 140B, Zenithra Ax1 sCAN, Teltonika, GT06/Concox,
  NMEA, ...) regardless of OEM/protocol. Its main job on the bench is DEVICE
  QC: watch a device's live packets, check coverage against the protocol's QC
  profile, and export the per-lot QC report.

RUN IT
  1. Double-click  {exe}
  2. A console window opens (version/branch banner + logs) and your browser
     opens the dashboard at  http://127.0.0.1:8080
       - If Windows SmartScreen warns (unsigned exe):  More info -> Run anyway.
  3. To stop: close the console window.

  A "data" folder is created next to {exe} on first run (database, logs,
  QC captures, and the protocol/signature repositories). The protocol
  definitions ship inside the exe and are copied in on first run, so the
  send-command catalogue works out of the box. Delete "data" to reset.

UPGRADING FROM AN EARLIER VERSION
  Every release unpacks to its own folder, and the app keeps its data beside
  the exe — so a new version opens EMPTY until you bring the data along:
  BEFORE the first start, move the previous version's "data" folder into this
  folder (next to {exe}). Data from ProInfo-era builds (proinfo.db) is
  adopted automatically on that first start.

DEVICE QC (the main workflow)
  1. Capture -> New session -> Device QC. Pick the protocol (or Auto-detect) and
     a port, bind 0.0.0.0 so trackers on the LAN can reach it, and Start.
  2. Point the device at  <this-PC-IP>:<port>. As packets arrive the Summary
     view fills in: device details, per-item coverage (Packets / Emergency /
     Alerts), and a live verdict.
  3. Mandatory items must be received; only the profile's skippable alerts can be
     waived. Stop the session and use  Report  to export the per-lot QC workbook
     (Summary sheet + one sheet per device). Fill the bench-only fields under
     "Lot details" first so they print on the report.
  4. Send downlink commands with the paper-plane button. IP/port SET commands
     (PIP/EIP/GIP) pre-fill this session's port; with several devices connected
     you can send one command to all of them at once.

  A resumed session keeps everything received earlier — packets, coverage and
  device details are restored from its capture file, so restarting the server or
  the session does not start the Summary blank.

TRY IT (no device needed)
  - Tools -> Packet Parser: open samples/ais140_sample.log (or
    samples/teltonika_demo.hex), paste it in and Decode — records, table, map.
  - Tools -> Converter: load a CanPlus *.Config.XML to view/edit CAN signals and
    export JSON / DBC.

COMMAND LINE (optional)
  {exe} serve --port 9000 --no-browser
  {exe} replay samples\\ais140_sample.log --codec ais140
  {exe} codecs
  {exe} --version

NOTES
  - Windows-only build. Local dashboard is http://127.0.0.1:8080.
  - Questions / issues: contact the Runway maintainer.
"""


def _copy_if(src: Path, dst: Path) -> None:
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _program_folders_with_data(exclude: Path) -> list[Path]:
    """Folders under dist/ that hold an exe *and* the data/ it created beside itself —
    dist/ itself (where PyInstaller drops the exe) and every unpacked release folder."""
    candidates = [DIST] + [p for p in DIST.iterdir() if p.is_dir()]
    return [p for p in candidates
            if p != exclude and (p / "data").is_dir() and any(p.glob("*.exe"))]


def _carry_data(stage: Path, carry_from: str | None, no_carry: bool) -> int:
    if (stage / "data").exists():
        print(f"\nData: {stage / 'data'} already exists — left as is (it is this version's own).")
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
        print("\nData: no earlier program folder holds data — nothing to carry.")
        return 0
    if len(sources) > 1:
        print(f"\nData NOT carried: {len(sources)} program folders hold data, and the build "
              "will not guess which one is live:", file=sys.stderr)
        for p in sources:
            print(f"  {p / 'data'}", file=sys.stderr)
        print("Re-run with --carry-from <folder> to pick one, or --no-carry. "
              "The release zip above is complete either way.", file=sys.stderr)
        return 2
    shutil.copytree(sources[0] / "data", stage / "data")
    print(f"\nData: carried {sources[0] / 'data'}  ->  {stage / 'data'}")
    return 0


def main() -> int:
    args = sys.argv[1:]
    rebuild = "--rebuild" in args
    no_carry = "--no-carry" in args
    carry_from = args[args.index("--carry-from") + 1] if "--carry-from" in args else None

    exe = DIST / EXE_NAME
    if rebuild or not exe.exists():
        print(f"Building {EXE_NAME} ...")
        subprocess.run([sys.executable, "-m", "PyInstaller", "packaging/Runway.spec",
                        "--noconfirm"], cwd=ROOT, check=True)
    if not exe.exists():
        print(f"ERROR: dist/{EXE_NAME} not found after build", file=sys.stderr)
        return 1

    # The staging folder doubles as a runnable install: replace only what the build makes,
    # so a data/ folder created by running the exe from here survives a rebuild.
    stage = DIST / RELEASE_NAME
    stage.mkdir(parents=True, exist_ok=True)
    for p in stage.iterdir():
        if p.name == "data":
            continue
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()

    shutil.copy2(exe, stage / EXE_NAME)
    (stage / "GETTING_STARTED.txt").write_text(GUIDE.format(ver=VER, exe=EXE_NAME), encoding="utf-8")
    _copy_if(ROOT / "CHANGELOG.md", stage / "CHANGELOG.md")
    _copy_if(ROOT / "samples" / "README.md", stage / "samples" / "README.md")
    # sample inputs a teammate can try in the Parser without a device — AIS-140 first,
    # since that's the primary QC protocol
    _copy_if(ROOT / "tests" / "fixtures" / "ais140_sample.log", stage / "samples" / "ais140_sample.log")
    _copy_if(ROOT / "tests" / "fixtures" / "zenithra_sample.log", stage / "samples" / "zenithra_sample.log")
    _copy_if(ROOT / "samples" / "teltonika_demo.hex", stage / "samples" / "teltonika_demo.hex")
    _copy_if(ROOT / "tests" / "fixtures" / "nmea_sample.log", stage / "samples" / "nmea_sample.log")

    files = sorted(p for p in stage.rglob("*")
                   if p.is_file() and p.relative_to(stage).parts[0] != "data")
    zpath = DIST / f"{RELEASE_NAME}.zip"
    if zpath.exists():
        zpath.unlink()
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, Path(RELEASE_NAME) / p.relative_to(stage))

    mb = zpath.stat().st_size / (1024 * 1024)
    print(f"\nRelease ready: {zpath}  ({mb:.1f} MB)")
    print("Contents:")
    for p in files:
        print(f"  {p.relative_to(stage).as_posix()}")
    return _carry_data(stage, carry_from, no_carry)


if __name__ == "__main__":
    raise SystemExit(main())
