# PyInstaller spec — builds a standalone one-file Runway.exe.
#
#   pip install pyinstaller
#   pyinstaller packaging/Runway.spec --noconfirm       # run from the REPO ROOT
#
# Output: dist/Runway.exe  (double-click to start the server + open the dashboard).
# A `data/` folder is created next to the exe on first run (db, logs, repositories).
#
# One-file build: passing a.binaries + a.datas into EXE (no COLLECT) makes it one-file.

from PyInstaller.utils.hooks import collect_submodules

# Paths in a .spec are relative to the spec's folder (packaging/), so reach up with ../.
# Bundle the static web UI at proinfo/web (app.py looks for it under _MEIPASS/proinfo/web)
# and the default QC coverage profiles (app.py: _MEIPASS/proinfo/qc/profiles — without
# them the packaged exe has no coverage catalogue until a user copy lands in data/).
datas = [
    ("../proinfo/web", "proinfo/web"),
    ("../proinfo/qc/profiles", "proinfo/qc/profiles"),
    # Protocol definitions (ota_commands catalogue etc.) — seeded into data/protocols on
    # first run by app.py:_seed_protocols so the send-command catalogue works from a bare
    # exe. Kept OUT of proinfo/ so it can't be mistaken for an importable package.
    ("../data/protocols", "_defaults/protocols"),
]

# uvicorn loads its protocol/loop backends by string; websockets and the proinfo plugins
# are imported dynamically — collect them all so nothing is missing at runtime.
hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("proinfo")
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
    a.binaries,
    a.datas,
    [],
    name="Runway",
    console=True,          # show the startup banner + URL + logs while testing
    upx=False,
)
