"""Per-device OTA key state: the 3 static keys and the downloaded comMap.

A device is provisioned with 3 *static* keys (constant for its life) used only to bootstrap
and refresh the 8 *communication* keys (``comMap``). The comMap carries an ``expireTimestamp``
(epoch milliseconds); past it, the device re-requests keys (type 2).

Keys are per ``(environment, imei)`` - a device's dev keys differ from its prod keys - so a
:class:`DeviceKeys` is stored under both. This module is pure: it models the state and the
"do we need fresh keys?" decision. Persistence (via the settings store) and the HTTP exchange
live in the API / :mod:`runway.ota.client`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

# Refresh this many ms *before* expiry, so a key never goes stale mid-exchange.
EXPIRY_SKEW_MS = 60_000


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class DeviceKeys:
    """One saved device — a *profile* in the UI: its keys plus how the person names it."""

    imei: str
    static_keys: list[str] = field(default_factory=list)   # 3 hex keys (16 bytes each)
    comm_map: dict[str, str] = field(default_factory=dict)  # {"1".."8" -> hex key}
    expire_ts: int = 0                                      # epoch ms; 0 = no keys yet
    fetched_ms: int = 0                                     # when we stored the comMap
    name: str = ""                                          # the person's name for it; "" = show the IMEI
    endpoint: str = ""                                      # base URL, used only by a "custom" environment

    # ---- key lookup (keyCode is 1-based) ----
    def static_key(self, code: int) -> str:
        try:
            return self.static_keys[code - 1]
        except IndexError:
            raise KeyError(f"no static key #{code} for {self.imei} "
                           f"(have {len(self.static_keys)})")

    def comm_key(self, code: int) -> str:
        key = self.comm_map.get(str(code))
        if not key:
            raise KeyError(f"no communication key #{code} for {self.imei} "
                           f"(comMap has {sorted(self.comm_map)})")
        return key

    def key_for(self, source: str, code: int) -> str:
        """Resolve a key by source (``"static"`` / ``"comm"``) and 1-based code."""
        return self.static_key(code) if source == "static" else self.comm_key(code)

    # ---- lifecycle ----
    def has_comm_keys(self) -> bool:
        return bool(self.comm_map)

    def is_expired(self, at_ms: int | None = None) -> bool:
        at = now_ms() if at_ms is None else at_ms
        return self.expire_ts <= (at + EXPIRY_SKEW_MS)

    def needs_keys(self, at_ms: int | None = None) -> bool:
        """True when a comm message must trigger a type-1/2 key download first."""
        return (not self.has_comm_keys()) or self.is_expired(at_ms)

    def apply_comm_response(self, body: dict) -> None:
        """Fold a decoded type-1/2 response (``{"comMap":{...},"expireTimestamp":...}``)."""
        cm = body.get("comMap") or {}
        if not cm:
            raise ValueError("keys response carried no comMap")
        self.comm_map = {str(k): str(v) for k, v in cm.items()}
        self.expire_ts = int(body.get("expireTimestamp") or 0)
        self.fetched_ms = now_ms()

    # ---- persistence ----
    def to_dict(self) -> dict:
        return {
            "imei": self.imei,
            "static_keys": self.static_keys,
            "comm_map": self.comm_map,
            "expire_ts": self.expire_ts,
            "fetched_ms": self.fetched_ms,
            "name": self.name,
            "endpoint": self.endpoint,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DeviceKeys":
        return cls(
            imei=str(d.get("imei", "")),
            static_keys=list(d.get("static_keys") or []),
            comm_map=dict(d.get("comm_map") or {}),
            expire_ts=int(d.get("expire_ts") or 0),
            fetched_ms=int(d.get("fetched_ms") or 0),
            name=str(d.get("name") or ""),
            endpoint=str(d.get("endpoint") or ""),
        )
