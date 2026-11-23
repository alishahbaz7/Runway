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
        # not `rev-parse --abbrev-ref HEAD`: once the release tag of the same name exists it
        # answers "heads/v1.1.24", and the check would quietly skip
        branch = subprocess.run(["git", "branch", "--show-current"], cwd=ROOT,
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


async def test_health_tells_the_launcher_whether_a_page_is_open():
    """The launcher opens a browser tab only when no Runway page has checked in: its own
    ?probe=1 call must not count as a page, a page's call must."""
    import asyncio

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        await c.get("/api/health")                                     # a page polling
        await asyncio.sleep(0.25)
        first = (await c.get("/api/health", params={"probe": 1})).json()["page_seen_s"]
        await asyncio.sleep(0.25)
        second = (await c.get("/api/health", params={"probe": 1})).json()["page_seen_s"]
    assert first is not None and second > first                       # probes don't refresh it


# ── a run from source names its branch ─────────────────────────────────────────────────────────────
def test_a_run_from_source_names_its_branch(tmp_path):
    """The banner shows the branch from source ("Branch v<x.y.z>"), so a copy run from a branch is never
    taken for a released copy of that version."""
    from runway import install
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text("ref: refs/heads/v0.9.1\n")
    assert install.branch(str(tmp_path)) == "v0.9.1"
    (git / "HEAD").write_text("0123456789abcdef0123456789abcdef01234567\n")      # detached
    assert install.branch(str(tmp_path)) == "0123456"
    wt = tmp_path / "wt"                                                          # a worktree
    (wt / "real").mkdir(parents=True)
    (wt / "real" / "HEAD").write_text("ref: refs/heads/feature-x\n")
    (wt / ".git").write_text("gitdir: real\n")
    assert install.branch(str(wt)) == "feature-x"
    assert install.branch(str(tmp_path / "nowhere")) is None


def test_a_release_names_its_version_not_a_branch(monkeypatch, tmp_path):
    from runway import install
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/v0.9.1\n")
    monkeypatch.setattr(install, "FROZEN", True)
    assert install.branch(str(tmp_path)) is None


async def test_health_carries_the_branch():
    from runway import install
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        d = (await c.get("/api/health")).json()
    assert d["branch"] == install.branch()
