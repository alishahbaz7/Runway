"""Where Runway's releases are published, and how copies find them — one place, read by the app and
by packaging/build_release.py, so the two cannot disagree.

Releases are published in Runway's own (public) GitHub repo. Copies read GitHub's
``releases/latest/download/`` redirect — no API, no rate limit, no token. A release must be
published as *latest*, not as a pre-release, or copies never see it.
"""

from __future__ import annotations

import os

from .. import install

REPO = os.environ.get("RUNWAY_UPDATE_REPO") or "alishahbaz7/Runway"
MANIFEST_NAME = "latest.json"
# A development run checks only when pointed at a test channel on purpose.
TEST_MANIFEST_URL = os.environ.get("RUNWAY_UPDATE_MANIFEST_URL")
MANIFEST_URL = TEST_MANIFEST_URL or f"https://github.com/{REPO}/releases/latest/download/{MANIFEST_NAME}"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"


def asset_url(version: str) -> str:
    """Where a release's Windows zip is downloaded from."""
    return f"https://github.com/{REPO}/releases/download/v{version}/{install.release_name(version)}.zip"


def enabled() -> bool:
    return install.CHANNEL == "release" or bool(TEST_MANIFEST_URL)


def disabled_reason() -> str | None:
    return None if enabled() else "Development runs do not check for updates"
