"""Assemble a shareable release ZIP around the standalone exe.

    python packaging/build_release.py            # build exe if missing, then zip
    python packaging/build_release.py --rebuild   # force a fresh PyInstaller build

Produces ``dist/Runway-<version>-win64.zip`` containing:

    Runway.exe            the standalone app (no Python needed)
    GETTING_STARTED.txt   how to run + test
    CHANGELOG.md          what's in this build
    samples/              demo inputs (hex dump, NMEA log) to try in the Parser

Everything the app itself needs (web UI, codecs) is already baked into the exe; the
extras here are just docs + sample data so a teammate can start testing immediately.
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

GUIDE = """\
Runway {ver} — standalone build (Windows 64-bit)
==================================================

WHAT IT IS
  Runway parses, visualizes and relays telematics packets from IoT tracking
  devices (AIS-140 / Locate 140B, Teltonika, GT06/Concox, NMEA, ...) regardless
  of OEM/protocol. Its main job on the bench is DEVICE QC: watch a device's
  live packets, check coverage against the protocol's QC profile, and export the
  per-lot QC report.

RUN IT
  1. Double-click  Runway.exe
  2. A console window opens (version/branch banner + logs) and your browser
     opens the dashboard at  http://127.0.0.1:8080
       - If Windows SmartScreen warns (unsigned exe):  More info -> Run anyway.
  3. To stop: close the console window.

  A "data" folder is created next to Runway.exe on first run (database, logs,
  QC captures, and the protocol/signature repositories). The protocol
  definitions ship inside the exe and are copied in on first run, so the
  send-command catalogue works out of the box. Delete "data" to reset.

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
  Runway.exe serve --port 9000 --no-browser
  Runway.exe replay samples\\ais140_sample.log --codec ais140
  Runway.exe codecs

NOTES
  - Windows-only build. Local dashboard is http://127.0.0.1:8080.
  - Questions / issues: contact the Runway maintainer.
"""


def _copy_if(src: Path, dst: Path) -> None:
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def main() -> int:
    rebuild = "--rebuild" in sys.argv[1:]
    exe = ROOT / "dist" / "Runway.exe"
    if rebuild or not exe.exists():
        print("Building Runway.exe ...")
        subprocess.run([sys.executable, "-m", "PyInstaller", "packaging/Runway.spec",
                        "--noconfirm"], cwd=ROOT, check=True)
    if not exe.exists():
        print("ERROR: dist/Runway.exe not found after build", file=sys.stderr)
        return 1

    name = f"Runway-{VER}-win64"
    stage = ROOT / "dist" / name
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    shutil.copy2(exe, stage / "Runway.exe")
    (stage / "GETTING_STARTED.txt").write_text(GUIDE.format(ver=VER), encoding="utf-8")
    _copy_if(ROOT / "CHANGELOG.md", stage / "CHANGELOG.md")
    _copy_if(ROOT / "samples" / "README.md", stage / "samples" / "README.md")
    # sample inputs a teammate can try in the Parser without a device — AIS-140 first,
    # since that's the primary QC protocol
    _copy_if(ROOT / "tests" / "fixtures" / "ais140_sample.log", stage / "samples" / "ais140_sample.log")
    _copy_if(ROOT / "samples" / "teltonika_demo.hex", stage / "samples" / "teltonika_demo.hex")
    _copy_if(ROOT / "tests" / "fixtures" / "nmea_sample.log", stage / "samples" / "nmea_sample.log")

    zpath = ROOT / "dist" / f"{name}.zip"
    if zpath.exists():
        zpath.unlink()
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(stage.rglob("*")):
            if p.is_file():
                z.write(p, Path(name) / p.relative_to(stage))

    mb = zpath.stat().st_size / (1024 * 1024)
    print(f"\nRelease ready: {zpath}  ({mb:.1f} MB)")
    print("Contents:")
    for p in sorted(stage.rglob("*")):
        if p.is_file():
            print(f"  {p.relative_to(stage).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
