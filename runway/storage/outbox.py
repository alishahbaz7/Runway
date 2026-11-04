"""Relay outbox — what a relayed device sent that its server has not taken yet.

``data/relay_outbox.db``: one row per chunk of device bytes, per (device, target), in arrival
order. A relay link (``sources/tcp_server.py``) appends every chunk *before* sending it, sends
the oldest first, and deletes rows only once its connection has taken them — so a server outage,
a broken link or a Runway restart loses nothing: the rows are still here and go out, in order,
when the server is reachable again (the device's login first, from ``hello``).

Bounded: past ``cap_bytes`` the oldest rows are dropped (and counted) so a long outage cannot
fill the disk. All calls are synchronous; links run them on one dedicated thread
(:data:`EXECUTOR`) so appends, reads and deletes keep their order and never block the event loop.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .db import DATA_DIR

DEFAULT_PATH = os.path.join(DATA_DIR, "relay_outbox.db")
DEFAULT_CAP = 2 * 1024 ** 3                    # 2 GB — about 2 days of 20 devices sending IMU every 2 s
EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="outbox")

_DDL = """
CREATE TABLE IF NOT EXISTS queue (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    device TEXT NOT NULL,
    target TEXT NOT NULL,
    data   BLOB NOT NULL,
    at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_queue_key ON queue(device, target, id);
CREATE TABLE IF NOT EXISTS hello (device TEXT PRIMARY KEY, data BLOB NOT NULL);
"""


class Outbox:
    def __init__(self, path: str = DEFAULT_PATH, cap_bytes: int = DEFAULT_CAP):
        self.path, self.cap = path, cap_bytes
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._total: int | None = None
        self.dropped = 0                            # bytes dropped past the cap, since start
        self.links: dict = {}                       # (device, target) -> live link (tcp_server)

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            conn = sqlite3.connect(self.path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(_DDL)
            self._conn = conn
            self._total = conn.execute("SELECT COALESCE(SUM(LENGTH(data)), 0) FROM queue").fetchone()[0]
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def append(self, device: str, target: str, data: bytes) -> None:
        with self._lock:
            db = self._db()
            db.execute("INSERT INTO queue (device, target, data, at) VALUES (?,?,?,?)",
                       (device, target, data, time.time()))
            self._total += len(data)
            while self._total > self.cap:           # oldest out first, a few hundred at a time
                rows = db.execute("SELECT id, LENGTH(data) FROM queue ORDER BY id LIMIT 500").fetchall()
                if not rows:
                    break
                db.execute(f"DELETE FROM queue WHERE id IN ({','.join('?' * len(rows))})", [r[0] for r in rows])
                freed = sum(r[1] for r in rows)
                self._total -= freed
                self.dropped += freed
            db.commit()

    def peek(self, device: str, target: str, after_id: int, max_bytes: int = 65536) -> list[tuple[int, bytes]]:
        """The oldest rows for this key after ``after_id``, up to ~``max_bytes``."""
        with self._lock:
            out, size = [], 0
            for rid, data in self._db().execute(
                    "SELECT id, data FROM queue WHERE device=? AND target=? AND id>? ORDER BY id LIMIT 200",
                    (device, target, after_id)):
                out.append((rid, data))
                size += len(data)
                if size >= max_bytes:
                    break
            return out

    def delete_upto(self, device: str, target: str, last_id: int) -> None:
        with self._lock:
            db = self._db()
            n = db.execute("SELECT COALESCE(SUM(LENGTH(data)), 0) FROM queue WHERE device=? AND target=? "
                           "AND id<=?", (device, target, last_id)).fetchone()[0]
            db.execute("DELETE FROM queue WHERE device=? AND target=? AND id<=?", (device, target, last_id))
            db.commit()
            self._total -= n

    def stats(self, device: str, target: str) -> tuple[int, int, float | None]:
        """(rows, bytes, oldest arrival) still waiting for this key."""
        with self._lock:
            return self._db().execute(
                "SELECT COUNT(*), COALESCE(SUM(LENGTH(data)), 0), MIN(at) FROM queue "
                "WHERE device=? AND target=?", (device, target)).fetchone()

    def keys(self) -> list[tuple[str, str]]:
        with self._lock:
            return [tuple(r) for r in self._db().execute("SELECT DISTINCT device, target FROM queue")]

    def first_starts_with(self, device: str, target: str, prefix: bytes) -> bool:
        with self._lock:
            row = self._db().execute("SELECT data FROM queue WHERE device=? AND target=? ORDER BY id LIMIT 1",
                                     (device, target)).fetchone()
            return bool(row) and bytes(row[0]).startswith(prefix)

    def set_hello(self, device: str, data: bytes) -> None:
        with self._lock:
            db = self._db()
            db.execute("INSERT INTO hello (device, data) VALUES (?,?) ON CONFLICT(device) DO UPDATE "
                       "SET data=excluded.data", (device, data))
            db.commit()

    def hello(self, device: str) -> bytes:
        with self._lock:
            row = self._db().execute("SELECT data FROM hello WHERE device=?", (device,)).fetchone()
            return bytes(row[0]) if row else b""

    @property
    def total(self) -> int:
        with self._lock:
            self._db()
            return self._total

    def size(self) -> int:
        return sum(os.path.getsize(p) for p in (self.path, self.path + "-wal") if os.path.exists(p))


outbox = Outbox()
