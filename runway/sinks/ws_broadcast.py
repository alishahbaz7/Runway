"""Push every record to all connected dashboard WebSockets via the live hub."""

from __future__ import annotations

from ..core.interfaces import Sink
from ..core.models import UnifiedRecord
from ..core.registry import register_sink
from ..storage.live import hub


@register_sink
class WsBroadcastSink(Sink):
    name = "ws_broadcast"

    async def handle(self, record: UnifiedRecord) -> None:
        data = record.model_dump(mode="json")
        # "seq" isn't a UnifiedRecord field — memory_store stamps it onto record.extra (it
        # owns the store's counter); hoist it to top-level so the dashboard's Sr No column,
        # which reads live packets the same way it reads the initial /api/snapshot fetch,
        # gets a real number instead of undefined for anything pushed after page load.
        data["seq"] = record.extra.get("seq")
        await hub.publish({"type": "record", "data": data})
