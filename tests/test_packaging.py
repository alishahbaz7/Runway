"""The release carries every file the code reads beside its modules.

PyInstaller bundles Python modules by itself, but data files only when ``packaging/Runway.spec`` lists their
folder. The firmware test's plans were left out once: a release would have opened Firmware test with no
Test plan. Every non-Python file under ``runway/`` must sit in a folder the spec bundles.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_every_data_file_under_runway_is_bundled():
    spec = (ROOT / "packaging" / "Runway.spec").read_text(encoding="utf-8")
    bundled = re.findall(r'\(\s*"\.\./(runway/[^"]+)"\s*,', spec)
    assert bundled, "no runway/ folders found in Runway.spec's datas"
    missing = []
    for p in (ROOT / "runway").rglob("*"):
        if not p.is_file() or p.suffix in (".py", ".pyc") or "__pycache__" in p.parts:
            continue
        rel = p.relative_to(ROOT).as_posix()
        if not any(rel.startswith(b.rstrip("/") + "/") for b in bundled):
            missing.append(rel)
    assert not missing, f"not bundled by packaging/Runway.spec: {missing}"
