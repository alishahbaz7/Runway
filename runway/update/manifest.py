"""``latest.json`` — attached to every release, read by every copy:

    {
      "app": "Runway",
      "version": "9.1.0",
      "published": "2026-11-20T10:00:00Z",
      "notes": "What changed and why — the changelog section",
      "minimumVersion": "9.0.2",
      "assets": {
        "windows": {"file": "Runway-v9.1.0.zip", "url": "https://…", "sha256": "…", "size": 123}
      }
    }

``url`` may be relative; it resolves against the manifest's own address. ``minimumVersion`` is
optional: a running version below it must update (used only for a critical fix). Old copies must
keep reading new manifests — add fields, never rename or retype one.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin, urlsplit

from .semver import SemVer

_SHA = re.compile(r"^[0-9a-f]{64}$")


class ManifestError(ValueError):
    """latest.json is wrong — said in words, never a crash halfway through a check."""


@dataclass(frozen=True)
class Asset:
    url: str
    file: str
    sha256: str            # lower-case hex
    size: int | None = None


@dataclass(frozen=True)
class Manifest:
    app: str
    version: SemVer
    notes: str
    windows: Asset | None
    published: datetime | None = None
    minimum: SemVer | None = None

    def is_newer_than(self, current: SemVer) -> bool:
        return self.version > current

    def requires(self, current: SemVer) -> bool:
        """The running version is below the release's minimum: updating is not optional."""
        return self.minimum is not None and current < self.minimum


def _str(v: object) -> str | None:
    return v if isinstance(v, str) else None


def parse(text: str, *, base: str, expected_app: str) -> Manifest:
    """Parses and validates; raises ManifestError naming what is wrong."""
    try:
        data = json.loads(text)
    except ValueError:
        raise ManifestError("latest.json is not valid JSON") from None
    if not isinstance(data, dict):
        raise ManifestError("latest.json is not an object")

    app = data.get("app")
    if app != expected_app:
        raise ManifestError(f'latest.json is for "{app}", not {expected_app}')
    version = SemVer.try_parse(data.get("version"))
    if version is None:
        raise ManifestError(f"latest.json has no valid version: {data.get('version')!r}")
    minimum_raw = data.get("minimumVersion")
    minimum = None if minimum_raw is None else SemVer.try_parse(minimum_raw)
    if minimum_raw is not None and minimum is None:
        raise ManifestError(f"latest.json has an invalid minimumVersion: {minimum_raw!r}")

    windows = None
    assets = data.get("assets")
    if isinstance(assets, dict) and isinstance(assets.get("windows"), dict):
        a = assets["windows"]
        file = a.get("file")
        if not isinstance(file, str) or not file or "/" in file or "\\" in file:
            raise ManifestError("latest.json: the windows asset has no valid file name")
        sha = (_str(a.get("sha256")) or "").lower()
        if not _SHA.match(sha):
            raise ManifestError("latest.json: the windows asset has no valid sha256")
        url = urljoin(base, a["url"] if isinstance(a.get("url"), str) else file)
        if urlsplit(url).scheme not in ("http", "https"):
            raise ManifestError("latest.json: the windows asset URL is not http(s)")
        size = a.get("size")
        windows = Asset(url=url, file=file, sha256=sha,
                        size=int(size) if isinstance(size, (int, float)) and not isinstance(size, bool) else None)

    published = None
    try:
        published = datetime.fromisoformat((_str(data.get("published")) or "").replace("Z", "+00:00"))
    except ValueError:
        pass

    return Manifest(app=app, version=version, notes=(_str(data.get("notes")) or "").strip(),
                    windows=windows, published=published, minimum=minimum)
