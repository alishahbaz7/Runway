"""QcCaptureSink — the recording half of a Device QC session.

Plugs into a normal ``Pipeline`` alongside the usual sinks (so a QC device still shows
up on the Dashboard/live feed). On every record it:

* learns the device's **protocol** and **device_id** (IMEI) from the decoded record,
  since a QC session may start with ``codec="auto"`` and only know these once the
  first packets arrive;
* appends the packet to a dedicated capture file for this session (flushed + fsync'd,
  same durability approach as :class:`runway.storage.live.PacketLog`);
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
UNKNOWN_DEVICE_IDS = {"", "unknown", "teltonika-unknown", "gt06-unknown", "ais140-unknown",
                      "zenithra-unknown", "nmea-device", "device-1"}

# Cap on in-memory records kept for check evaluation — QC bench sessions are short, so this
# is a generous safety bound rather than an expected ceiling (mirrors MAX_FEED/LOG_MAX in
# the web layer).
MAX_CHECK_RECORDS = 5000


class QcCaptureSink(Sink):
    name = "qc_capture"

    def __init__(self, path: str, started_at: Optional[float] = None,
                 config: Optional[dict] = None, on_update: Optional[Callable[[dict], None]] = None,
                 catalog_loader: Optional[Callable[[str], Optional[list]]] = None):
        self.path = path
        self.started_at = started_at
        self.config = config
        self.on_update = on_update
        self.count = 0
        self.identified_as = ""
        self.device_id = ""
        self.records: list[UnifiedRecord] = []
        # Incremental per-device coverage/vitals — the records list above is a bounded
        # WINDOW (drops oldest past MAX_CHECK_RECORDS); the ledger is what the verdict
        # reads, so a long session can never "forget" coverage it already earned.
        from .ledger import CoverageLedger      # local import: ledger imports from here
        self.ledger = CoverageLedger()
        self.catalog: Optional[list] = None     # coverage catalogue, loaded on identify
        self._catalog_loader = catalog_loader   # protocol -> catalogue (API supplies it)
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
        # Load the coverage catalogue once the protocol is known, and backfill coverage
        # for anything that arrived before identification (still inside the window then).
        if self.catalog is None and self.identified_as and self._catalog_loader is not None:
            try:
                self.catalog = self._catalog_loader(self.identified_as) or []
            except Exception:
                self.catalog = []
            self.ledger.replay_items(self.records, self.catalog)
        self.ledger.observe(record, self.catalog)
        self.records.append(record)
        if len(self.records) > MAX_CHECK_RECORDS:
            self.records.pop(0)
        self._write(record)
        if self.on_update:
            try:
                self.on_update(self.snapshot())
            except Exception:
                pass

    def rehydrate(self, codec_name: str = "auto") -> int:
        """Rebuild ``records`` (and identity/count) by re-decoding this session's own
        capture file. Called before the pipeline starts when a session is resumed —
        after a Stop, a crash or a full server restart — so the Summary keeps everything
        received before the interruption instead of starting blank. The capture file is
        append-only across resumes, so it is the complete history; a brand-new session
        has no file yet and this is a no-op. Returns the number of records restored."""
        from ..core import registry
        from ..core.models import Session

        try:
            text = Path(self.path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return 0
        registry.load_plugins()

        # One decode session per device-id column, so cross-packet codec state (e.g. an
        # IMEI learned from a login) is replayed per device, like the live per-peer runs.
        sessions: dict[str, Session] = {}
        codecs: dict[str, object] = {}
        restored = 0
        pre_existing = len(self.records)
        for line in text.splitlines():
            parts = line.split(" | ", 2)
            if len(parts) != 3:
                continue
            ts_raw, did_col, hex_col = parts
            try:
                data = bytes.fromhex(hex_col.strip())
            except ValueError:
                continue
            if not data:
                continue
            key = did_col.strip()
            sess = sessions.get(key)
            if sess is None:
                sess = Session(id=f"rehydrate:{key or 'unidentified'}", transport="file")
                sessions[key] = sess
            codec = codecs.get(key)
            if codec is None:
                codec = self._rehydrate_codec(codec_name, data)
                codecs[key] = codec
            sess.buffer.extend(data)
            ts = self._parse_ts(ts_raw)
            while sess.buffer:
                try:
                    out = codec.decode(bytes(sess.buffer), sess)
                except Exception:
                    del sess.buffer[:1]   # resync, same as the live pipeline
                    continue
                if out.consumed <= 0:
                    del sess.buffer[:]    # a written line is a complete frame; don't stall
                    break
                del sess.buffer[:out.consumed]
                for rec in out.records:
                    # identity carry, as the live pipeline does — with the file's own
                    # device-id column as the last-resort source of truth
                    if rec.device_id in (None, "", "unknown"):
                        if sess.device_id:
                            rec.device_id = sess.device_id
                    elif not sess.device_id:
                        sess.device_id = rec.device_id
                    if (not rec.device_id or rec.device_id in UNKNOWN_DEVICE_IDS) \
                            and key and key not in UNKNOWN_DEVICE_IDS:
                        rec.device_id = key
                    if ts is not None:
                        rec.received_at = ts
                    if rec.protocol and (rec.protocol != "generic_hex" or not self.identified_as):
                        self.identified_as = rec.protocol
                    if rec.device_id and rec.device_id not in UNKNOWN_DEVICE_IDS:
                        self.device_id = rec.device_id
                    self.records.append(rec)
                    restored += 1
        # Fold the restored history into the ledger BEFORE the window truncation below —
        # the ledger is the verdict's source of truth and must see every record, even the
        # ones about to fall out of the bounded records window.
        if self.catalog is None and self.identified_as and self._catalog_loader is not None:
            try:
                self.catalog = self._catalog_loader(self.identified_as) or []
            except Exception:
                self.catalog = []
        for rec in self.records[pre_existing:]:
            self.ledger.observe(rec, self.catalog)
        if len(self.records) > MAX_CHECK_RECORDS:
            del self.records[: len(self.records) - MAX_CHECK_RECORDS]
        self.count = max(self.count, restored)
        return restored

    @staticmethod
    def _rehydrate_codec(codec_name: str, sample: bytes):
        """The named codec, or auto-detection over the registry (the live pipeline's
        ``_pick_codec`` logic) when the session never learned a protocol name."""
        from ..core import registry

        if codec_name and codec_name != "auto":
            try:
                return registry.get_codec(codec_name)
            except KeyError:
                pass
        best_cls, best_conf = None, 0.0
        for cls in registry.CODECS.values():
            try:
                conf = cls().detect(sample)
            except Exception:
                conf = 0.0
            if conf > best_conf:
                best_cls, best_conf = cls, conf
        return best_cls() if best_cls is not None else registry.get_codec("generic_hex")

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

    @staticmethod
    def _parse_ts(s: str) -> Optional[datetime]:
        """Inverse of :meth:`_fmt_ts` — parses the capture line's local-time stamp back
        into an aware datetime (None if it doesn't parse; the record then keeps the
        decode-time default)."""
        try:
            base, ms = s.strip().rsplit(":", 1)
            dt = datetime.strptime(base, "%d-%b-%Y %H:%M:%S")
            return dt.replace(microsecond=min(int(ms), 999) * 1000).astimezone()
        except Exception:
            return None
