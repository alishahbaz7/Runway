"""Persistence rail — a thin, thread-safe SQLite store.

ProInfo's live state ([live.py]) is in-memory and lost on restart. This rail is the
durable companion: it remembers **devices**, protocol **profiles**, the **captures**
index and **settings** across restarts. It is deliberately tiny and dependency-free
(stdlib ``sqlite3`` only), mirroring the proven pattern from the CAN-utility sibling.

The app is multi-threaded (the asyncio pipeline plus background jobs), so a single
connection is opened with ``check_same_thread=False`` in WAL mode and every mutation is
serialized through one lock; reads are cheap and concurrent under WAL. The schema is a
plain list of ``CREATE TABLE IF NOT EXISTS`` statements — extend it by appending one
entry, no migration framework required yet.

This rail owns the *relational + config* tables only; the high-volume ``records``
time-series stays with the optional ``sqlite_store`` sink.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
from typing import Any, Optional

# Writable state lives under data/ (data/proinfo.db, data/uploads, data/logs).
# When frozen (PyInstaller .exe) the source tree is a read-only temp extraction, so keep
# data next to the executable instead; otherwise use the repo root.
if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(_BASE_DIR, "data")
DEFAULT_DB_PATH = os.path.join(DATA_DIR, "proinfo.db")

# Each entry is an idempotent DDL statement. Append to extend the schema.
SCHEMA: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL                 -- JSON-encoded
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS devices (
        device_id         TEXT PRIMARY KEY,
        protocol          TEXT,
        first_seen        TEXT,
        last_seen         TEXT,
        count             INTEGER DEFAULT 0,
        last_lat          REAL,
        last_lon          REAL,
        last_message_type TEXT,
        meta              TEXT DEFAULT '{}'  -- JSON
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS profiles (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        name       TEXT NOT NULL,
        vendor     TEXT DEFAULT '',
        model      TEXT DEFAULT '',
        transport  TEXT DEFAULT '',
        codec      TEXT DEFAULT '',
        framing    TEXT DEFAULT '{}',        -- JSON (declarative field-map etc.)
        signature  TEXT DEFAULT '{}',        -- JSON fingerprint for future auto-identify
        status     TEXT DEFAULT 'draft',     -- draft | reviewed | shipped
        version    INTEGER DEFAULT 1,
        created_at REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS captures (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        name       TEXT NOT NULL,
        path       TEXT DEFAULT '',
        fmt        TEXT DEFAULT '',
        codec      TEXT DEFAULT '',
        device_id  TEXT DEFAULT '',
        records    INTEGER DEFAULT 0,
        bytes      INTEGER DEFAULT 0,
        meta       TEXT DEFAULT '{}',        -- JSON
        created_at REAL NOT NULL
    )
    """,
]


class Storage:
    """Thread-safe SQLite wrapper with small per-table conveniences."""

    def __init__(self, path: str = DEFAULT_DB_PATH):
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self.init_schema()

    def init_schema(self) -> None:
        with self._lock:
            for ddl in SCHEMA:
                self._conn.execute(ddl)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ----- generic helpers ------------------------------------------------- #
    def execute(self, sql: str, params: tuple = ()) -> int:
        """Run a write statement; returns ``lastrowid``."""
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur.lastrowid

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def query_one(self, sql: str, params: tuple = ()) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def insert(self, table: str, **cols) -> int:
        """Insert a row from kwargs; stamps ``created_at`` if the column is omitted."""
        cols.setdefault("created_at", round(time.time(), 3))
        keys = list(cols)
        placeholders = ", ".join("?" for _ in keys)
        sql = f"INSERT INTO {table} ({', '.join(keys)}) VALUES ({placeholders})"
        return self.execute(sql, tuple(cols[k] for k in keys))

    def list(self, table: str, order: str = "id") -> list[dict]:
        return self.query(f"SELECT * FROM {table} ORDER BY {order}")

    def get(self, table: str, row_id: int) -> Optional[dict]:
        return self.query_one(f"SELECT * FROM {table} WHERE id = ?", (row_id,))

    def delete(self, table: str, row_id: int) -> None:
        self.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))

    # ----- settings (key -> JSON) ------------------------------------------ #
    def set_setting(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )

    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.query_one("SELECT value FROM settings WHERE key = ?", (key,))
        return json.loads(row["value"]) if row else default

    def all_settings(self) -> dict:
        return {r["key"]: json.loads(r["value"]) for r in self.list("settings", order="key")}

    # ----- devices (durable mirror of the live registry) ------------------- #
    def upsert_device(self, info: dict) -> None:
        """Insert or update a device row keyed by ``device_id`` (idempotent).

        ``info`` matches the live ``LiveStore`` device summary shape. ``first_seen`` is
        preserved on update; ``count`` and the last-* fields are overwritten.
        """
        self.execute(
            """
            INSERT INTO devices
                (device_id, protocol, first_seen, last_seen, count,
                 last_lat, last_lon, last_message_type, meta)
            VALUES (:device_id, :protocol, :first_seen, :last_seen, :count,
                    :last_lat, :last_lon, :last_message_type, :meta)
            ON CONFLICT(device_id) DO UPDATE SET
                protocol          = excluded.protocol,
                last_seen         = excluded.last_seen,
                count             = excluded.count,
                last_lat          = excluded.last_lat,
                last_lon          = excluded.last_lon,
                last_message_type = excluded.last_message_type,
                meta              = excluded.meta
            """,
            {
                "device_id": info["device_id"],
                "protocol": info.get("protocol"),
                "first_seen": info.get("first_seen"),
                "last_seen": info.get("last_seen"),
                "count": info.get("count", 0),
                "last_lat": (info.get("last_position") or {}).get("lat"),
                "last_lon": (info.get("last_position") or {}).get("lon"),
                "last_message_type": info.get("last_message_type"),
                "meta": json.dumps(info.get("meta", {})),
            },
        )

    def devices(self) -> list[dict]:
        return self.list("devices", order="last_seen DESC")
