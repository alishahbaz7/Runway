"""Application configuration & saved profiles.

A thin layer over :class:`runway.storage.db.Storage`'s ``settings`` table. Config is a
flat key→JSON store with sensible :data:`DEFAULTS`; "profiles" are just namespaced keys
(e.g. ``profile:bench-1``) so the same machinery serves both without a new table. Unknown
keys fall back to ``DEFAULTS``, so reading config never fails on a fresh database.
"""

from __future__ import annotations

from typing import Any

from .db import Storage

DEFAULTS: dict[str, Any] = {
    "ui_theme": "dark",
    "default_codec": "auto",
    "default_listen_port": 5050,
    "ws_push_hz": 5,
    "recent_frames": 250,
    "auto_open_browser": True,
    "log_dir": "",                 # where recordings are saved ("" = server default data/logs)
}


class Config:
    def __init__(self, storage: Storage):
        self._s = storage

    def get(self, key: str, default: Any = None) -> Any:
        sentinel = object()
        val = self._s.get_setting(key, sentinel)
        if val is not sentinel:
            return val
        return DEFAULTS.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._s.set_setting(key, value)

    def all(self) -> dict:
        """Effective config: defaults overlaid with stored overrides (profiles excluded)."""
        overrides = {k: v for k, v in self._s.all_settings().items()
                     if not k.startswith("profile:")}
        return {**DEFAULTS, **overrides}

    # ----- named profiles (namespaced settings keys) ----------------------- #
    def save_profile(self, name: str, data: dict) -> None:
        self._s.set_setting(f"profile:{name}", data)

    def load_profile(self, name: str) -> dict:
        return self._s.get_setting(f"profile:{name}", {})

    def list_profiles(self) -> list[str]:
        return [k.split(":", 1)[1] for k in self._s.all_settings() if k.startswith("profile:")]
