"""Print a one-line summary per record. Default sink for CLI replay."""

from __future__ import annotations

from ..core.interfaces import Sink
from ..core.models import UnifiedRecord
from ..core.registry import register_sink


@register_sink
class ConsoleSink(Sink):
    name = "console"

    def __init__(self, show_hex: bool = False):
        self.show_hex = bool(show_hex)

    async def handle(self, record: UnifiedRecord) -> None:
        line = record.summary()
        if self.show_hex and record.raw_hex:
            line += f"  [{record.raw_hex}]"
        print(line)
