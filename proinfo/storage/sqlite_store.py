"""Optional durable sink: append every record to SQLite.

Local-first persistence with zero setup. The same Sink contract means swapping this
for a Postgres/TimescaleDB sink in the cloud is a drop-in change (see ROADMAP).
SQLite work runs in a thread executor so it never blocks the asyncio pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3

from ..core.interfaces import Sink
from ..core.models import UnifiedRecord
from ..core.registry import register_sink

log = logging.getLogger("proinfo.sqlite")

_DDL = """
CREATE TABLE IF NOT EXISTS records (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   TEXT,
    protocol    TEXT,
    message_type TEXT,
    event_time  TEXT,
    received_at TEXT,
    lat         REAL,
    lon         REAL,
    speed_kph   REAL,
    heading_deg REAL,
    raw_hex     TEXT,
    payload     TEXT
);
CREATE INDEX IF NOT EXISTS idx_records_device ON records(device_id);
CREATE INDEX IF NOT EXISTS idx_records_time ON records(received_at);
"""


@register_sink
class SqliteStoreSink(Sink):
    name = "sqlite_store"

    def __init__(self, path: str = "proinfo.db"):
        self.path = path
        self._conn: sqlite3.Connection | None = None

    async def start(self) -> None:
        await asyncio.get_event_loop().run_in_executor(None, self._init)

    def _init(self) -> None:
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.executescript(_DDL)
        self._conn.commit()
        log.info("sqlite store ready at %s", self.path)

    async def handle(self, record: UnifiedRecord) -> None:
        if self._conn is None:
            return
        await asyncio.get_event_loop().run_in_executor(None, self._insert, record)

    def _insert(self, rec: UnifiedRecord) -> None:
        assert self._conn is not None
        pos = rec.position
        self._conn.execute(
            "INSERT INTO records (device_id, protocol, message_type, event_time, "
            "received_at, lat, lon, speed_kph, heading_deg, raw_hex, payload) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                rec.device_id,
                rec.protocol,
                rec.message_type,
                rec.event_time.isoformat() if rec.event_time else None,
                rec.received_at.isoformat() if rec.received_at else None,
                pos.latitude if pos else None,
                pos.longitude if pos else None,
                rec.speed_kph,
                rec.heading_deg,
                rec.raw_hex,
                rec.model_dump_json(),
            ),
        )
        self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
