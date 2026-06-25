"""End-to-end pipeline test: replay a file through Source -> Codec -> Sink."""

from __future__ import annotations

from pathlib import Path

from proinfo.core import registry
from proinfo.core.interfaces import Sink
from proinfo.core.models import UnifiedRecord
from proinfo.core.pipeline import Pipeline
from proinfo.sources.file_replay import FileReplaySource

FIX = Path(__file__).parent / "fixtures"


class CaptureSink(Sink):
    name = "capture"

    def __init__(self):
        self.records: list[UnifiedRecord] = []

    async def handle(self, record: UnifiedRecord) -> None:
        self.records.append(record)


async def test_replay_nmea_through_pipeline():
    registry.load_plugins()
    cap = CaptureSink()
    src = FileReplaySource(str(FIX / "nmea_sample.log"), chunk_size=16)  # force re-framing
    n = await Pipeline(src, "nmea", [cap]).run()
    assert n == len(cap.records) >= 2
    assert any(r.message_type == "RMC" and r.position.is_valid for r in cap.records)


async def test_replay_auto_detect():
    registry.load_plugins()
    cap = CaptureSink()
    src = FileReplaySource(str(FIX / "nmea_sample.log"))
    await Pipeline(src, "auto", [cap]).run()      # should auto-pick the nmea codec
    assert any(r.protocol == "nmea" for r in cap.records)
