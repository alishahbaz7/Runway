"""Which pages (features) this copy shows: the owner's policy first, then each page's own default, and
for IMU the data itself.

    FEATURES   one row per feature, named as the sidebar names its page: its default
    status()   for the page: {id: {"state", "by"}} plus when the policy last came

The owner sets features per app, group, person or copy on the shared usage server (app-control,
server v4); the policy comes back in the answer to this copy's usage report and is kept, so it holds
offline (`usage.feature(name)`: True / False / None when the owner did not set it).

States: "shown" (in its group in the sidebar), "more" (folded under More), "hidden" (not in the
sidebar). By: "owner" (the policy says so), "data" (IMU, once IMU data exists), "default". The
person's own Settings choices are applied in the page: they may hide anything and show what is in
More or waiting for data, never what the owner switched off.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time

# feature → its default when the owner has not set it: shown | more | data (shown once data exists)
FEATURES: dict[str, str] = {
    "dashboard": "shown",
    "parser": "shown",
    "firmware": "shown",
    "converter": "shown",
    "ota": "shown",
    "analysis": "shown",
    "imu": "data",              # one firmware's custom packet: shown once a device has sent IMU data
    "capture": "shown",
    "archive": "shown",
    "protocols": "more",        # used now and then: folded under More
    "signatures": "more",
    "logs": "shown",
}

_imu = {"at": 0.0, "has": False}
_lock = threading.Lock()


def imu_data_exists() -> bool:
    """Has any device sent IMU samples to this copy? Read from the running summary, at most once a minute."""
    with _lock:
        if time.monotonic() - _imu["at"] < 60:
            return _imu["has"]
    try:
        from .storage.telemetry import telemetry
        has = any((d.get("imu_samples") or 0) > 0 for d in telemetry.devices())
    except Exception:
        has = False
    with _lock:
        _imu.update(at=time.monotonic(), has=has)
    return has


def resolve(owner, has_imu: bool) -> dict:
    """owner(name) → True / False / None (not set). Returns {feature: {"state", "by"}}."""
    out = {}
    for name, default in FEATURES.items():
        said = owner(name)
        if said is True:
            out[name] = {"state": "shown", "by": "owner"}
        elif said is False:
            out[name] = {"state": "hidden", "by": "owner"}
        elif default == "data":
            out[name] = {"state": "shown" if has_imu else "hidden", "by": "data"}
        else:
            out[name] = {"state": default, "by": "default"}
    return out


def status() -> dict:
    from . import usage as usage_mod
    u = usage_mod.usage
    resolved = resolve(u.feature, imu_data_exists())
    policy = u.policy or {}
    return {"features": resolved, "groups": policy.get("groups") or [],
            "policy_at": u.status().get("policy_at") if u.enabled else None,
            "controlled": u.enabled, "key": key(resolved)}


def key(resolved: dict) -> str:
    """A short fingerprint, so the page sees a change in /api/health without fetching the list."""
    return hashlib.sha1(json.dumps(resolved, sort_keys=True).encode()).hexdigest()[:12]
