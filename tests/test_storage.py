"""Tests for the persistence rail: Storage (SQLite) + Config.

All tests use an in-memory database so they leave nothing on disk and run fast.
"""

from __future__ import annotations

from proinfo.storage.config import DEFAULTS, Config
from proinfo.storage.db import Storage


def _db() -> Storage:
    return Storage(":memory:")


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #
def test_schema_builds_and_tables_exist():
    db = _db()
    names = {r["name"] for r in db.query(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"settings", "devices", "profiles", "captures"} <= names


def test_settings_roundtrip():
    db = _db()
    assert db.get_setting("missing", "fallback") == "fallback"
    db.set_setting("k", {"a": 1, "b": [2, 3]})
    assert db.get_setting("k") == {"a": 1, "b": [2, 3]}
    db.set_setting("k", "overwritten")            # upsert, not duplicate
    assert db.get_setting("k") == "overwritten"
    assert db.all_settings()["k"] == "overwritten"


def test_profiles_crud():
    db = _db()
    pid = db.insert("profiles", name="Teltonika FMB", vendor="Teltonika",
                    transport="tcp", codec="teltonika")
    assert pid == db.get("profiles", pid)["id"]
    assert db.list("profiles")[0]["name"] == "Teltonika FMB"
    db.delete("profiles", pid)
    assert db.get("profiles", pid) is None


def test_upsert_device_is_idempotent():
    db = _db()
    info = {"device_id": "356307042441013", "protocol": "teltonika",
            "first_seen": "t0", "last_seen": "t1", "count": 1,
            "last_position": {"lat": 22.5, "lon": 88.3}, "last_message_type": "avl"}
    db.upsert_device(info)
    info.update(count=7, last_seen="t2", last_position={"lat": 23.0, "lon": 88.4})
    db.upsert_device(info)

    rows = db.devices()
    assert len(rows) == 1                          # one row, not two
    row = rows[0]
    assert row["count"] == 7
    assert row["first_seen"] == "t0"               # preserved on update
    assert row["last_lat"] == 23.0


def test_upsert_device_without_position():
    db = _db()
    db.upsert_device({"device_id": "X", "protocol": "teltonika", "count": 1})
    row = db.devices()[0]
    assert row["last_lat"] is None and row["last_lon"] is None


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def test_config_defaults_and_override():
    cfg = Config(_db())
    assert cfg.get("ui_theme") == DEFAULTS["ui_theme"]
    cfg.set("ui_theme", "light")
    assert cfg.get("ui_theme") == "light"
    assert cfg.all()["ui_theme"] == "light"
    assert cfg.all()["default_listen_port"] == DEFAULTS["default_listen_port"]


def test_config_named_profiles_excluded_from_all():
    cfg = Config(_db())
    cfg.save_profile("bench-1", {"port": 5050, "codec": "teltonika"})
    assert cfg.load_profile("bench-1") == {"port": 5050, "codec": "teltonika"}
    assert cfg.list_profiles() == ["bench-1"]
    assert not any(k.startswith("profile:") for k in cfg.all())   # profiles aren't config
