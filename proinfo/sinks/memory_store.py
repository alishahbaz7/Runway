"""Write records into the in-memory live store the REST API reads from.

Also mirrors the per-device summary into the durable ``devices`` table so the device
registry survives a restart. The live ``store`` stays the hot read path; the SQLite write
runs in a thread executor so it never blocks the asyncio pipeline.
"""

from __future__ import annotations

import asyncio
import logging

from ..core.interfaces import Sink
from ..core.models import UnifiedRecord
from ..core.registry import register_sink
from ..storage.live import db, store

log = logging.getLogger("proinfo.memory_store")


@register_sink
class MemoryStoreSink(Sink):
    name = "memory_store"

    async def handle(self, record: UnifiedRecord) -> None:
        store.add(record)
        info = store.devices.get(record.device_id)
        if info is not None:
            await asyncio.get_event_loop().run_in_executor(None, self._persist, dict(info))

    @staticmethod
    def _persist(info: dict) -> None:
        try:
            db.upsert_device(info)
        except Exception:               # persistence must never break the live pipeline
            log.exception("failed to persist device %s", info.get("device_id"))
