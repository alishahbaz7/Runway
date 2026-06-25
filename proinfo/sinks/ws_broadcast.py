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
        await hub.publish({"type": "record", "data": record.model_dump(mode="json")})
