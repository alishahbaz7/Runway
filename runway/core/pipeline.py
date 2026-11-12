"""The engine that wires a Source -> Codec -> Sinks.

Flow per session (one TCP connection, or one replayed file):

1. Frames from the source are appended to the session buffer.
2. A codec is chosen once (explicit name, or ``auto`` via ``detect``).
3. The buffer is drained: ``decode`` is called repeatedly until it stops consuming
   (``consumed == 0`` => needs more bytes).
4. Each produced ``UnifiedRecord`` is stamped with provenance and fanned out to every
   sink. Any ACK the codec returned is written back through the source.

Codecs are cached per session so protocol state in ``session.state`` survives.
"""

from __future__ import annotations

import logging
import time
from collections import deque

from . import registry
from .interfaces import Codec, Sink, Source
from .models import RawFrame, Session

log = logging.getLogger("runway.pipeline")


def _known_id(device_id: str | None) -> bool:
    """A real device id — not empty, and not a codec's ``<name>-unknown`` placeholder."""
    return bool(device_id) and device_id != "unknown" and not device_id.endswith("-unknown")


class Pipeline:
    def __init__(self, source: Source, codec: str, sinks: list[Sink], enrich=None):
        self.source = source
        self.codec_name = codec or "auto"
        self.sinks = sinks
        # Optional ``(record) -> None`` hook, called once per record BEFORE the sinks see it, so
        # every consumer (store, dashboard, relays, QC capture) gets the same enriched record.
        # Kept as a caller-supplied callable so core/ stays free of what the enrichment knows
        # about — the API passes one that decodes CAN frames with the device's bound signature.
        self.enrich = enrich
        self._sessions: dict[str, Session] = {}
        self._codecs: dict[str, Codec] = {}
        self._identified: set[str] = set()     # sessions whose device the source has been told
        self._closed: deque[str] = deque()     # ended connections, oldest first (stats pruning)
        self.records_emitted = 0
        self.bytes_in = 0
        # peer -> {records, bytes, first, last, device_id, protocol} (server lifetime)
        self.per_peer: dict[str, dict] = {}

    # -- session / codec selection ----------------------------------------- #
    def _session_for(self, frame: RawFrame) -> Session:
        key = f"{frame.transport}:{frame.peer or frame.source or 'default'}"
        sess = self._sessions.get(key)
        if sess is None:
            sess = Session(id=key, transport=frame.transport, peer=frame.peer)
            self._sessions[key] = sess
        return sess

    def _pick_codec(self, sample: bytes) -> Codec:
        if self.codec_name and self.codec_name != "auto":
            return registry.get_codec(self.codec_name)
        best_cls, best_conf = None, 0.0
        for cls in registry.CODECS.values():
            try:
                conf = cls().detect(sample)
            except Exception:
                conf = 0.0
            if conf > best_conf:
                best_cls, best_conf = cls, conf
        if best_cls is None:
            return registry.get_codec("generic_hex")
        log.info("auto-detected codec '%s' (confidence %.2f)", best_cls.name, best_conf)
        return best_cls()

    # -- main loop --------------------------------------------------------- #
    async def run(self) -> int:
        registry.load_plugins()
        for sink in self.sinks:
            await sink.start()
        try:
            async for frame in self.source.frames():
                if frame.closed:
                    self._release(frame)
                    continue
                self.bytes_in += len(frame.data)
                pp = self.per_peer.get(frame.peer or frame.source or "?")
                if pp is None:                # first bytes on this connection = when it began
                    pp = {"records": 0, "bytes": 0, "first": time.time(), "last": 0.0}
                    self.per_peer[frame.peer or frame.source or "?"] = pp
                pp["bytes"] += len(frame.data)
                sess = self._session_for(frame)
                sess.buffer.extend(frame.data)

                codec = self._codecs.get(sess.id)
                if codec is None:
                    codec = self._pick_codec(bytes(sess.buffer))
                    sess.codec = codec.name
                    self._codecs[sess.id] = codec

                await self._drain(codec, sess, frame)
        finally:
            await self.source.close()
            for sink in self.sinks:
                await sink.close()
        log.info("pipeline done: %d bytes in, %d records out",
                 self.bytes_in, self.records_emitted)
        return self.records_emitted

    # A listener runs for weeks and devices re-dial every minute or so, each time from a new
    # source port: without this every connection's session, codec and stats lived forever.
    MAX_CLOSED_PEERS = 500

    def _release(self, frame: RawFrame) -> None:
        """The connection ended: free its session and codec; keep its stats (the Capture page
        lists a device's recent connections) for the last MAX_CLOSED_PEERS ended ones only."""
        key = f"{frame.transport}:{frame.peer or frame.source or 'default'}"
        self._sessions.pop(key, None)
        self._codecs.pop(key, None)
        self._identified.discard(key)
        peer = frame.peer or frame.source or "?"
        if peer in self.per_peer:
            self._closed.append(peer)
        while len(self._closed) > self.MAX_CLOSED_PEERS:
            self.per_peer.pop(self._closed.popleft(), None)

    async def _drain(self, codec: Codec, sess: Session, frame: RawFrame) -> None:
        while sess.buffer:
            try:
                out = codec.decode(bytes(sess.buffer), sess)
            except Exception:
                log.exception("codec %s crashed; dropping 1 byte to resync", codec.name)
                del sess.buffer[:1]
                continue

            if out.consumed <= 0:
                break  # needs more data
            del sess.buffer[: out.consumed]

            # Tell the source who is on this connection BEFORE the ACK goes out: a source that
            # relays per device decides from it where the bytes go — and so who ACKs them.
            if sess.id not in self._identified:
                first = next((r for r in out.records if _known_id(r.device_id)), None)
                if first is not None:
                    self._identified.add(sess.id)
                    hello = (bytes.fromhex(first.raw_hex)
                             if first.message_type == "login" and first.raw_hex else b"")
                    try:
                        await self.source.identified(frame.peer or "", first.device_id, hello)
                    except Exception:
                        log.exception("source failed to take the device id for %s", frame.peer)

            if out.ack:
                try:
                    await self.source.reply(frame, out.ack)
                except Exception:
                    log.exception("failed writing ACK back to %s", frame.peer)

            for rec in out.records:
                rec.transport = rec.transport or frame.transport
                rec.peer = rec.peer or frame.peer
                # Carry a device id learned earlier in the session (e.g. GT06 login)
                if rec.device_id in (None, "", "unknown"):
                    if sess.device_id:
                        rec.device_id = sess.device_id
                elif not sess.device_id:
                    sess.device_id = rec.device_id

                if self.enrich is not None:
                    try:
                        self.enrich(rec)
                    except Exception:      # enrichment is additive; never lose the record
                        log.exception("record enrichment failed for %s", rec.device_id)

                self.records_emitted += 1
                pp = self.per_peer.get(frame.peer or frame.source or "?")
                if pp is not None:
                    pp["records"] += 1
                    pp["last"] = time.time()
                    # who is on this connection, for the page: its own records of an old
                    # connection are long gone from its memory
                    if _known_id(rec.device_id):
                        pp["device_id"] = rec.device_id
                    if rec.protocol and (rec.protocol != "generic_hex" or "protocol" not in pp):
                        pp["protocol"] = rec.protocol
                for sink in self.sinks:
                    try:
                        await sink.handle(rec)
                    except Exception:
                        log.exception("sink %s failed on a record", sink.name)
