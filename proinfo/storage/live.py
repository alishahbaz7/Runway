"""Process-wide live state shared between the pipeline and the API.

* :data:`store` - an in-memory ring buffer of recent records plus a per-device summary.
  The ``memory_store`` sink writes to it; the REST API reads from it.
* :data:`hub`   - a fan-out bus for real-time push. The ``ws_broadcast`` sink publishes
  to it; each connected dashboard WebSocket subscribes to it.

Both are deliberately simple singletons - fine for a local utility and for a single
cloud instance. The :doc:`ROADMAP` describes swapping these for Redis/TimescaleDB when
scaling to a multi-instance cloud deployment.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.models import UnifiedRecord


class LiveStore:
    def __init__(self, max_packets: int = 5000, max_per_device: int = 1000):
        self.packets: deque[dict] = deque(maxlen=max_packets)
        self.by_device: dict[str, deque[dict]] = defaultdict(
            lambda: deque(maxlen=max_per_device)
        )
        self.devices: dict[str, dict[str, Any]] = {}
        self._seq = 0

    def add(self, rec: UnifiedRecord) -> dict:
        self._seq += 1
        d = rec.model_dump(mode="json")
        d["seq"] = self._seq
        self.packets.append(d)
        self.by_device[rec.device_id].append(d)

        info = self.devices.get(rec.device_id)
        if info is None:
            info = {
                "device_id": rec.device_id,
                "protocol": rec.protocol,
                "first_seen": d["received_at"],
                "count": 0,
                "last_position": None,
            }
            self.devices[rec.device_id] = info
        info["protocol"] = rec.protocol
        info["count"] += 1
        info["last_seen"] = d["received_at"]
        info["last_message_type"] = rec.message_type
        info["last"] = d
        if rec.position and rec.position.is_valid:
            info["last_position"] = {
                "lat": rec.position.latitude,
                "lon": rec.position.longitude,
            }
        packet_log.write(d)          # continuous logging (no-op unless a log is running)
        return d

    def device_records(self, device_id: str, limit: int = 200) -> list[dict]:
        return list(self.by_device.get(device_id, []))[-limit:]

    def recent(self, limit: int = 200) -> list[dict]:
        return list(self.packets)[-limit:]

    def snapshot(self) -> dict:
        return {"devices": list(self.devices.values()), "recent": self.recent(200)}

    def clear(self) -> None:
        self.packets.clear()
        self.by_device.clear()
        self.devices.clear()
        self._seq = 0


class Hub:
    """Fan-out bus: one asyncio.Queue per connected subscriber."""

    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    async def publish(self, message: dict) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                pass  # slow consumer: drop rather than block the pipeline

    @property
    def subscribers(self) -> int:
        return len(self._subs)


class PacketLog:
    """Server-side continuous packet logger.

    While running, every record added to the store is appended to a file on disk as
    ``timestamp | client | raw`` (or CSV). Start/stop are driven from the UI; the log
    keeps running even if the browser navigates away or closes.
    """

    def __init__(self) -> None:
        self._fh = None
        self.path: str | None = None
        self.fmt = "txt"            # txt | csv
        self.raw = "ascii"          # ascii | hex
        self.count = 0
        self._last_fsync = 0.0

    @property
    def active(self) -> bool:
        return self._fh is not None

    @staticmethod
    def default_dir() -> str:
        from .db import DATA_DIR
        return str(Path(DATA_DIR) / "logs")

    def status(self) -> dict:
        return {"active": self.active, "path": self.path, "count": self.count,
                "fmt": self.fmt, "raw": self.raw, "default_dir": self.default_dir()}

    def start(self, fmt: str = "txt", raw: str = "ascii",
              name: str = "", directory: str = "") -> dict:
        self.stop()
        self.fmt = "csv" if fmt == "csv" else "txt"
        self.raw = "hex" if raw == "hex" else "ascii"

        base = Path(directory.strip()) if directory and directory.strip() else Path(self.default_dir())
        base.mkdir(parents=True, exist_ok=True)

        fn = (name or "").strip()
        if not fn:
            fn = "runway-packets-" + datetime.now().strftime("%d-%b-%Y_%H-%M-%S") + "." + self.fmt
        if "/" in fn or "\\" in fn or ".." in fn:
            raise ValueError("file name must not contain path separators")
        if not fn.lower().endswith("." + self.fmt):
            fn += "." + self.fmt

        p = base / fn
        fresh = (not p.exists()) or p.stat().st_size == 0
        # append mode: never destroy an existing file the user named
        self._fh = open(p, "a", encoding="utf-8", newline="")
        self.path = str(p)
        self.count = 0
        self._last_fsync = 0.0
        if self.fmt == "csv" and fresh:
            self._fh.write("timestamp,client,raw\n")
        self._flush(force=True)
        return self.status()

    def stop(self) -> dict:
        if self._fh is not None:
            try:
                self._flush(force=True)      # final fsync so nothing is left in a buffer
                self._fh.close()
            except Exception:
                try:
                    self._fh.close()
                except Exception:
                    pass
            self._fh = None
        return self.status()        # keeps .path so the file can still be downloaded

    def _flush(self, force: bool = False) -> None:
        """flush() moves bytes to the OS (survives a process crash); fsync() forces them
        to physical disk (survives power loss). fsync is throttled to ~1/s for throughput."""
        if self._fh is None:
            return
        self._fh.flush()
        now = time.monotonic()
        if force or (now - self._last_fsync) >= 1.0:
            try:
                os.fsync(self._fh.fileno())
            except Exception:
                pass
            self._last_fsync = now

    def write(self, d: dict) -> None:
        if self._fh is None:
            return
        try:
            ts = self._fmt_ts(d.get("event_time") or d.get("received_at"))
            client = d.get("peer") or d.get("device_id") or ""
            raw = self._fmt_raw(d.get("raw_hex") or "")
            if self.fmt == "csv":
                esc = lambda s: '"' + str(s).replace('"', '""') + '"'   # noqa: E731
                self._fh.write(f"{esc(ts)},{esc(client)},{esc(raw)}\n")
            else:
                self._fh.write(f"{ts} | {client} | {raw}\n")
            self.count += 1
            self._flush()
        except Exception:
            pass

    @staticmethod
    def _fmt_ts(v) -> str:
        if not v:
            return ""
        try:
            dt = datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone()
            return dt.strftime("%d-%b-%Y %H:%M:%S:") + f"{dt.microsecond // 1000:03d}"
        except Exception:
            return str(v)

    def _fmt_raw(self, hexstr: str) -> str:
        if not hexstr:
            return ""
        try:
            b = bytes.fromhex(hexstr)
        except Exception:
            return hexstr
        if self.raw == "hex":
            return " ".join(f"{x:02X}" for x in b)
        return "".join(chr(x) if 32 <= x < 127 else "." for x in b)


# Module-level singletons.
store = LiveStore()
hub = Hub()
packet_log = PacketLog()

# Durable persistence rail (relational + config). Lazy-imported here so the in-memory
# singletons above stay usable even if SQLite is unavailable for some reason.
from .config import Config       # noqa: E402
from .db import Storage          # noqa: E402

db = Storage()
config = Config(db)
