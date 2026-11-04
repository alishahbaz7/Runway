"""Keep every record a listener produces in ``data/telemetry.db`` (packets + IMU samples).

The listener's *Store* switch adds this sink; the IMU page reads what it writes. SQLite work
runs in a thread executor so the pipeline — and with it a relay in front of the platform —
never waits on the disk.
"""

from __future__ import annotations

import asyncio
import logging

from ..core.interfaces import Sink
from ..core.models import UnifiedRecord
from ..core.registry import register_sink
from ..storage.telemetry import telemetry

log = logging.getLogger("runway.telemetry")


@register_sink
class TelemetryStoreSink(Sink):
    name = "telemetry_store"

    async def handle(self, record: UnifiedRecord) -> None:
        await asyncio.get_running_loop().run_in_executor(None, telemetry.write, record)
