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
# data next to the executable instead; otherwise use the repo root. RUNWAY_DATA_DIR
# overrides everything (tests point it at a temp dir; ops can relocate state).
if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.environ.get("RUNWAY_DATA_DIR") or os.path.join(_BASE_DIR, "data")
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
    """
    CREATE TABLE IF NOT EXISTS qc_devices (
        device_id     TEXT PRIMARY KEY,       -- IMEI — the durable identity across reconnects/sessions
        serial        INTEGER NOT NULL,       -- stable per-IMEI serial: 1st new device seen = 1, 2nd = 2, ...
        first_seen    REAL NOT NULL,
        last_seen     REAL,
        last_protocol TEXT DEFAULT '',
        last_qc_id    INTEGER,                -- the QC session that produced last_verdict (its OWN id once final)
        last_verdict  TEXT DEFAULT ''          -- last FINAL verdict (pass/conditional_pass/fail/timeout) — never
                                                -- overwritten by a still-in_progress session, so a returning
                                                -- device's prior result stays visible until this run finalizes
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS qc_sessions (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id         TEXT DEFAULT '',       -- the live-TCP job id backing this session
        port           INTEGER NOT NULL,
        host           TEXT DEFAULT '0.0.0.0',
        protocol_mode  TEXT DEFAULT 'auto',   -- 'auto' | 'manual'
        protocol       TEXT DEFAULT '',       -- requested codec name ('auto' or a specific one)
        identified_as  TEXT DEFAULT '',       -- codec actually identified once a device connects
        device_id      TEXT DEFAULT '',       -- IMEI / device id learned from the session
        peer           TEXT DEFAULT '',       -- ip:port of the connected device
        operator       TEXT DEFAULT '',
        notes          TEXT DEFAULT '',
        status         TEXT DEFAULT 'running',  -- running | stopped | error
        packet_count   INTEGER DEFAULT 0,
        capture_path   TEXT DEFAULT '',
        capture_fmt    TEXT DEFAULT 'txt',
        started_at     REAL NOT NULL,
        ended_at       REAL,
        created_at     REAL NOT NULL
    )
    """,
]

# Columns added to an existing table after its initial release. SQLite has no
# CREATE-TABLE-style "IF NOT EXISTS" for ADD COLUMN, so these are applied with a
# PRAGMA table_info guard in Storage.init_schema() instead of a migration framework.
_ALTER_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "qc_sessions": [
        ("checks_json", "TEXT DEFAULT '[]'"),   # QC Target 2: last-evaluated check results
        ("verdict", "TEXT DEFAULT ''"),         # '' | in_progress | timeout | pass | conditional_pass | fail
        # QC coverage (Phase A2): expected-packet coverage snapshot + the operator's
        # skip/finalize choices + the capture-file mode.
        ("coverage_json", "TEXT DEFAULT '[]'"),        # session-level coverage (unidentified/aggregate)
        ("skipped_json", "TEXT DEFAULT '{}'"),         # {device_id: [coverage keys marked optional]}
        ("finalized_verdict", "TEXT DEFAULT ''"),      # session-level operator override
        ("finalized_json", "TEXT DEFAULT '{}'"),       # {device_id: operator-set verdict}
        ("devices_json", "TEXT DEFAULT '[]'"),         # per-device coverage/verdict snapshot (persisted on stop)
        ("deviations_json", "TEXT DEFAULT '[]'"),      # accepted deviations (Step 2)
        ("capture_mode", "TEXT DEFAULT 'full'"),       # 'full' = every packet | 'sample' = one per type
        # Lot report (v1.1.21): the bench-only fields typed into the app instead of into
        # Excel afterwards, the operator-action audit trail, and the printed-report log.
        ("lot_json", "TEXT DEFAULT '{}'"),             # session-level lot details (lot size, PCB no, sign-off)
        ("manual_json", "TEXT DEFAULT '{}'"),          # {device_id: {manual column -> value}}
        ("audit_json", "TEXT DEFAULT '[]'"),           # [{at, action, device_id, ...}] skip/finalize history
        ("reports_json", "TEXT DEFAULT '[]'"),         # [{at, fmt, file, devices, sha256}] printed reports
    ],
}


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
            for table, columns in _ALTER_COLUMNS.items():
                existing = {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})")}
                for col, decl in columns:
                    if col not in existing:
                        self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def backup(self, dest_path: str) -> None:
        """Consistent online copy of the whole DB (sqlite backup API — WAL-safe, unlike a
        plain file copy which can catch a checkpoint mid-write)."""
        os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
        with self._lock:
            dest = sqlite3.connect(dest_path)
            try:
                self._conn.backup(dest)
            finally:
                dest.close()

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

    # ----- QC sessions ------------------------------------------------------ #
    def qc_sessions(self) -> list[dict]:
        return self.list("qc_sessions", order="id DESC")

    def qc_session(self, qc_id: int) -> Optional[dict]:
        return self.get("qc_sessions", qc_id)

    def qc_update(self, qc_id: int, **cols) -> None:
        if not cols:
            return
        sets = ", ".join(f"{k} = ?" for k in cols)
        self.execute(f"UPDATE qc_sessions SET {sets} WHERE id = ?",
                     (*cols.values(), qc_id))

    def qc_sessions_by_device(self, device_id: str) -> list[dict]:
        """Every QC session ever run for this device id (IMEI), newest first — the device
        id is the durable identity a device carries across many separate QC sessions."""
        if not device_id:
            return []
        return self.query(
            "SELECT * FROM qc_sessions WHERE device_id = ? ORDER BY id DESC", (device_id,)
        )

    # ----- QC device registry (persistent, IMEI-keyed) ---------------------- #
    def qc_device_touch(self, device_id: str, *, protocol: str = "",
                        qc_id: Optional[int] = None, verdict: Optional[str] = None) -> dict:
        """Get-or-create the durable registry row for this IMEI: assigns the next
        incrementing serial number the FIRST time this device is ever seen (never
        reassigned after that — a reconnect of the same IMEI always gets the same
        number), and bumps last_seen. Pass qc_id/verdict only when the caller has a
        FINAL verdict to record (the caller is responsible for not passing an
        in-progress one) — that way a returning device's prior result stays visible
        in the registry for the whole time its new session is still running.
        Get-or-create must be atomic (two devices connecting at once must never race
        onto the same serial), so this bypasses execute()/query_one() and holds the
        lock across the whole read-modify-write itself."""
        now = time.time()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM qc_devices WHERE device_id = ?", (device_id,)).fetchone()
            if row is None:
                nxt = self._conn.execute(
                    "SELECT COALESCE(MAX(serial), 0) + 1 AS n FROM qc_devices").fetchone()["n"]
                self._conn.execute(
                    "INSERT INTO qc_devices "
                    "(device_id, serial, first_seen, last_seen, last_protocol, last_qc_id, last_verdict) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (device_id, nxt, now, now, protocol, qc_id, verdict or ""),
                )
            else:
                updates: dict[str, Any] = {"last_seen": now}
                if protocol:
                    updates["last_protocol"] = protocol
                if verdict is not None:
                    updates["last_qc_id"] = qc_id
                    updates["last_verdict"] = verdict
                set_clause = ", ".join(f"{k} = ?" for k in updates)
                self._conn.execute(f"UPDATE qc_devices SET {set_clause} WHERE device_id = ?",
                                   (*updates.values(), device_id))
            self._conn.commit()
            return dict(self._conn.execute(
                "SELECT * FROM qc_devices WHERE device_id = ?", (device_id,)).fetchone())

    def qc_device_registry(self, device_id: str) -> Optional[dict]:
        """Read-only lookup — no last_seen bump, no row creation (unlike qc_device_touch)."""
        if not device_id:
            return None
        return self.query_one("SELECT * FROM qc_devices WHERE device_id = ?", (device_id,))
