"""QcCaptureSink — the recording half of a Device QC session.

Plugs into a normal ``Pipeline`` alongside the usual sinks (so a QC device still shows
up on the Dashboard/live feed). On every record it:

* learns the device's **protocol** and **device_id** (IMEI) from the decoded record,
  since a QC session may start with ``codec="auto"`` and only know these once the
  first packets arrive;
* appends the packet to a dedicated capture file for this session (flushed + fsync'd,
  same durability approach as :class:`proinfo.storage.live.PacketLog`);
* calls an optional ``on_update`` callback with a small snapshot, so the API layer can
  push live updates and persist counters to the ``qc_sessions`` table.

Not a registered plugin — created per QC session with session-specific parameters,
the same pattern as the API's internal ``_BytesSource``.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from ..core.interfaces import Sink
from ..core.models import UnifiedRecord

# Placeholder device ids codecs fall back to before a real identity is learned from the
# wire (or, for NMEA/the template, because the protocol never carries one at all) — see
# each codec's `session.device_id or "..."` fallback. Kept in sync by hand; a codec that
# introduces a new placeholder needs an entry here too, or its placeholder would be
# mistaken for a genuine device id in the QC UI. Also read by qc/engine.py's IMEI check.
UNKNOWN_DEVICE_IDS = {"", "unknown", "teltonika-unknown", "gt06-unknown", "nmea-device", "device-1"}

# Cap on in-memory records kept for check evaluation — QC bench sessions are short, so this
# is a generous safety bound rather than an expected ceiling (mirrors MAX_FEED/LOG_MAX in
# the web layer).
MAX_CHECK_RECORDS = 5000


class QcCaptureSink(Sink):
    name = "qc_capture"

    def __init__(self, path: str, started_at: Optional[float] = None,
                 config: Optional[dict] = None, on_update: Optional[Callable[[dict], None]] = None):
        self.path = path
        self.started_at = started_at
        self.config = config
        self.on_update = on_update
        self.count = 0
        self.identified_as = ""
        self.device_id = ""
        self.records: list[UnifiedRecord] = []
        self._fh = None
        self._last_fsync = 0.0

    async def start(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a", encoding="utf-8", newline="")

    async def handle(self, record: UnifiedRecord) -> None:
        # Prefer any real protocol match; only fall back to showing "generic_hex" itself
        # if that's genuinely all we've ever seen (so at least something is displayed).
        if record.protocol and (record.protocol != "generic_hex" or not self.identified_as):
            self.identified_as = record.protocol
        if record.device_id and record.device_id not in UNKNOWN_DEVICE_IDS:
            self.device_id = record.device_id
        self.count += 1
        self.records.append(record)
        if len(self.records) > MAX_CHECK_RECORDS:
            self.records.pop(0)
        self._write(record)
        if self.on_update:
            try:
                self.on_update(self.snapshot())
            except Exception:
                pass

    def checks(self) -> tuple[list[dict], str]:
        from .engine import evaluate     # local import: engine imports UNKNOWN_DEVICE_IDS from here

        meta = {"device_id": self.device_id, "started_at": self.started_at}
        return evaluate(self.records, meta, self.config)

    def snapshot(self) -> dict:
        checks, verdict = self.checks()
        return {"packet_count": self.count, "identified_as": self.identified_as,
                "device_id": self.device_id, "checks": checks, "verdict": verdict}

    async def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.flush()
                os.fsync(self._fh.fileno())
                self._fh.close()
            except Exception:
                pass
            self._fh = None

    def _write(self, record: UnifiedRecord) -> None:
        if self._fh is None:
            return
        try:
            ts = self._fmt_ts(record.event_time or record.received_at)
            self._fh.write(f"{ts} | {record.device_id or ''} | {record.raw_hex or ''}\n")
            self._fh.flush()
            now = time.monotonic()
            if now - self._last_fsync >= 1.0:
                os.fsync(self._fh.fileno())
                self._last_fsync = now
        except Exception:
            pass

    @staticmethod
    def _fmt_ts(v) -> str:
        """``DD-MMM-YYYY HH:MM:SS:zzz`` in local time — matches the Live TCP log format."""
        if not v:
            return ""
        try:
            dt = v if isinstance(v, datetime) else datetime.fromisoformat(str(v).replace("Z", "+00:00"))
            dt = dt.astimezone()
            return dt.strftime("%d-%b-%Y %H:%M:%S:") + f"{dt.microsecond // 1000:03d}"
        except Exception:
            return str(v)
