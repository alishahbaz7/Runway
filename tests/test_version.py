"""One version, in one place: ``runway/__init__.py``.

pyproject reads it (``dynamic``), the UI learns it from ``/api/health``, the release is named
from it. These fail the build instead of relying on a checklist to keep the rest in step.
"""

import re
import subprocess
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from runway import __version__
from runway.api.app import app

ROOT = Path(__file__).resolve().parent.parent


def test_changelog_newest_section_is_the_current_version():
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    newest = re.search(r"^## \[([^\]]+)\]", text, re.M)
    assert newest and newest.group(1) == __version__


def test_branch_name_matches_version():
    try:
        branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT,
                                capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    if not re.fullmatch(r"v?\d+\.\d+\.\d+", branch):
        pytest.skip(f"not on a version branch ({branch})")
    assert branch.lstrip("v") == __version__


def test_no_second_version_literal():
    quoted = re.compile(r"""["']v?%s["']""" % re.escape(__version__))
    files = [ROOT / "pyproject.toml", *(ROOT / "packaging").glob("*"),
             *(p for p in (ROOT / "runway").rglob("*")
               if p.suffix in {".py", ".js", ".html", ".json", ".toml"})]
    offenders = [str(p.relative_to(ROOT)) for p in files
                 if p.is_file() and p.name != "__init__.py" and "__pycache__" not in p.parts
                 and quoted.search(p.read_text(encoding="utf-8", errors="ignore"))]
    # runway/__init__.py is the declaration; any other package __init__ must not repeat it
    inits = [p for p in (ROOT / "runway").rglob("__init__.py") if p.parent != ROOT / "runway"]
    offenders += [str(p.relative_to(ROOT)) for p in inits
                  if quoted.search(p.read_text(encoding="utf-8"))]
    assert offenders == []


async def test_health_reports_running_and_on_disk_version():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        body = (await c.get("/api/health")).json()
    assert body["version"] == __version__
    assert body["code_version"] == __version__
