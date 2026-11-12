# PyInstaller spec — builds Runway-v<version>\Runway-v<version>.exe as a ONE-FOLDER program, the
# folder build_release.py places under versions\ of the launcher install:
#
#   pip install pyinstaller
#   python packaging/build_release.py --rebuild          # the usual way (runs this spec)
#   pyinstaller packaging/Runway.spec --noconfirm --distpath build/pyinstaller --workpath build/pyinstaller-work
#
# One folder, not one file: it starts without unpacking itself to a temp folder each time, and the
# process the launcher starts is the server itself — which a restart into a new version relies on.

import sys

from PyInstaller.utils.hooks import collect_submodules

# The release name is spelled once, in build_release.py (from runway.__version__).
sys.path.insert(0, SPECPATH)
from build_release import RELEASE_NAME  # noqa: E402

# Paths in a .spec are relative to the spec's folder (packaging/), so reach up with ../.
# Bundle the static web UI at runway/web (app.py looks for it under _MEIPASS/runway/web)
# and the default QC coverage profiles (app.py: _MEIPASS/runway/qc/profiles — without
# them the packaged exe has no coverage catalogue until a user copy lands in data/).
datas = [
    ("../runway/web", "runway/web"),
    ("../runway/qc/profiles", "runway/qc/profiles"),
    # Protocol definitions (ota_commands catalogue etc.) — seeded into data/protocols on
    # first run by app.py:_seed_protocols so the send-command catalogue works from a bare
    # exe. Kept OUT of runway/ so it can't be mistaken for an importable package.
    ("../data/protocols", "_defaults/protocols"),
]

# uvicorn loads its protocol/loop backends by string; websockets and the runway plugins
# are imported dynamically — collect them all so nothing is missing at runtime.
hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("runway")
    + collect_submodules("websockets")
    # openpyxl is imported lazily by qc/report.py (XLS QC report export)
    + collect_submodules("openpyxl")
    + ["multipart", "anyio", "et_xmlfile"]
)

a = Analysis(
    ["runway_main.py"],
    pathex=[".."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pandas", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=RELEASE_NAME,
    icon="runway.ico",
    console=True,          # the startup banner, the URL and the logs
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name=RELEASE_NAME, upx=False)
