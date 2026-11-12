"""Usage reporting for Runway (the app-control skill) — who uses this copy, which version, and how.

One reporter for the app, sending to the shared usage server every app of this user reports to,
as app "Runway". Release builds only: a run from source reports nothing unless pointed at a test
server on purpose (``RUNWAY_USAGE_URL``). Reports wait in ``data/usage.json`` until the server has
them, so starts made offline still count; nothing here may slow or stop Runway.

What is reported: the name the person gave once, this computer (login, computer name, Windows
version), the app version, how long Runway ran, the pages opened, errors, and Runway's own counts
below. Never reported: device IMEIs, packets, endpoints, keys or files — counts only.
"""

from __future__ import annotations

import os
import threading
from collections import Counter

from . import __version__, install
from .storage.db import DATA_DIR
from .usage_client import URL, UsageReporter

_TEST_URL = os.environ.get("RUNWAY_USAGE_URL")
ENABLED = install.CHANNEL == "release" or bool(_TEST_URL)

usage = UsageReporter(app=install.APP_NAME, version=__version__, data_dir=DATA_DIR,
                      enabled=ENABLED, build=install.CHANNEL, url=_TEST_URL or URL)

# Runway's own counters — camelCase, counts only, read into the session's metrics on each tick.
_counts: Counter = Counter()
_lock = threading.Lock()


def count(name: str, n: int = 1) -> None:
    """Something Runway did, counted: listenersStarted, qcSessionsStarted, parserDecodes, …"""
    with _lock:
        _counts[name] += n


def _metrics() -> dict:
    with _lock:
        out = dict(_counts)
    try:
        from .storage.live import store          # devices heard on the live view this run
        out["devicesSeen"] = len(store.devices)
    except Exception:
        pass
    return out


usage.metrics = _metrics

# A page is named by its file: index.html is the Dashboard.
_PAGE_NAMES = {"": "dashboard", "index": "dashboard"}


def page_of(path: str) -> str | None:
    """The page a GET for ``path`` opens (``/parser.html`` → parser), or None for anything else."""
    if path in ("", "/"):
        return "dashboard"
    if not path.endswith(".html") or path.count("/") != 1:
        return None
    stem = path[1:-5]
    return _PAGE_NAMES.get(stem, stem)


def disabled_reason() -> str | None:
    """Why reporting is off on this copy, in words; None when it is on."""
    if usage.enabled:
        return None
    return "Development runs do not report usage"


def status() -> dict:
    """For Settings → Usage reporting, and the first-start name prompt."""
    s = usage.status()
    s["needs_name"] = usage.needs_name
    s["disabled_reason"] = disabled_reason()
    return s
