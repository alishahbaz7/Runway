"""Write records into the in-memory live store the REST API reads from."""

from __future__ import annotations

from ..core.interfaces import Sink
from ..core.models import UnifiedRecord
from ..core.registry import register_sink
from ..storage.live import store


@register_sink
class MemoryStoreSink(Sink):
    name = "memory_store"

    async def handle(self, record: UnifiedRecord) -> None:
        store.add(record)
