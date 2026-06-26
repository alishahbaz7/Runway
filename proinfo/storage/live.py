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
from collections import defaultdict, deque
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


# Module-level singletons.
store = LiveStore()
hub = Hub()

# Durable persistence rail (relational + config). Lazy-imported here so the in-memory
# singletons above stay usable even if SQLite is unavailable for some reason.
from .config import Config       # noqa: E402
from .db import Storage          # noqa: E402

db = Storage()
config = Config(db)
