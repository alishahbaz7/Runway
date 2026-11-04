"""Append every record a storing listener produces to its device's daily packet archive
(``data/archive/<device>/<device>_<day>.log`` — see ``storage/archive.py``). Added with the
listener's *Store* switch, beside ``telemetry_store``; the Archive page replays what it writes."""

from __future__ import annotations

import asyncio

from ..core.interfaces import Sink
from ..core.models import UnifiedRecord
from ..core.registry import register_sink
from ..storage.archive import archive


@register_sink
class ArchiveStoreSink(Sink):
    name = "archive_store"

    async def handle(self, record: UnifiedRecord) -> None:
        await asyncio.get_running_loop().run_in_executor(None, archive.write, record)
