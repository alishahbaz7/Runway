"""Tests for the persistence rail: Storage (SQLite) + Config.

Most tests use an in-memory database so they leave nothing on disk and run fast; the
legacy-adoption tests need real files and use tmp_path.
"""

from __future__ import annotations

from runway.storage.config import DEFAULTS, Config
from runway.storage.db import Storage, adopt_legacy_db


def _db() -> Storage:
    return Storage(":memory:")


def _seed(path, device_id: str = "860123456789012") -> None:
    """Build a real pre-rename DB at *path* (full schema + one device), then release it."""
    db = Storage(str(path))
    db.upsert_device({"device_id": device_id, "protocol": "teltonika", "count": 3})
    db.close()


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
# Legacy DB adoption (data/proinfo.db -> data/runway.db, the v1.1.24 rename)
# --------------------------------------------------------------------------- #
def test_adopts_legacy_db_when_no_current_db(tmp_path):
    _seed(tmp_path / "proinfo.db")
    (tmp_path / "proinfo.db-wal").write_bytes(b"")          # siblings must move too
    target = tmp_path / "runway.db"

    assert adopt_legacy_db(str(target)) == str(tmp_path / "proinfo.db")
    assert target.exists() and not (tmp_path / "proinfo.db").exists()
    assert (tmp_path / "runway.db-wal").exists()
    assert Storage(str(target)).query("SELECT device_id FROM devices")[0]["device_id"] \
        == "860123456789012"


def test_adopts_over_a_row_empty_current_db(tmp_path):
    """One app start before migrating leaves a bare runway.db — it must not orphan history."""
    target = tmp_path / "runway.db"
    Storage(str(target)).close()                            # bare schema, zero rows
    _seed(tmp_path / "proinfo.db")                           # legacy appears afterwards

    assert adopt_legacy_db(str(target)) == str(tmp_path / "proinfo.db")
    db = Storage(str(target))
    assert db.devices()[0]["device_id"] == "860123456789012"
    db.close()


def test_never_overwrites_a_current_db_that_has_data(tmp_path):
    target = tmp_path / "runway.db"
    live = Storage(str(target))
    live.upsert_device({"device_id": "live-device", "count": 1})
    live.close()
    _seed(tmp_path / "proinfo.db", "legacy-device")

    assert adopt_legacy_db(str(target)) is None
    db = Storage(str(target))
    assert [r["device_id"] for r in db.devices()] == ["live-device"]
    db.close()
    assert (tmp_path / "proinfo.db").exists()               # left in place, not consumed


def test_adoption_is_a_noop_without_a_legacy_db(tmp_path):
    assert adopt_legacy_db(str(tmp_path / "runway.db")) is None
    assert adopt_legacy_db(":memory:") is None


def test_adoption_ignores_an_empty_legacy_db(tmp_path):
    Storage(str(tmp_path / "proinfo.db")).close()            # schema but no rows
    assert adopt_legacy_db(str(tmp_path / "runway.db")) is None


def test_storage_adopts_on_construction(tmp_path):
    """The rename is invisible in normal use: constructing Storage carries the data over."""
    _seed(tmp_path / "proinfo.db")
    assert Storage(str(tmp_path / "runway.db")).devices()[0]["device_id"] == "860123456789012"


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
