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

from . import registry
from .interfaces import Codec, Sink, Source
from .models import RawFrame, Session

log = logging.getLogger("proinfo.pipeline")


class Pipeline:
    def __init__(self, source: Source, codec: str, sinks: list[Sink]):
        self.source = source
        self.codec_name = codec or "auto"
        self.sinks = sinks
        self._sessions: dict[str, Session] = {}
        self._codecs: dict[str, Codec] = {}
        self.records_emitted = 0
        self.bytes_in = 0

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
                self.bytes_in += len(frame.data)
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

                self.records_emitted += 1
                for sink in self.sinks:
                    try:
                        await sink.handle(rec)
                    except Exception:
                        log.exception("sink %s failed on a record", sink.name)
