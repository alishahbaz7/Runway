"""Replay a captured log / .loc / dump file as if it were a live device.

Supported on-disk formats (auto-detected, or force with ``fmt``):

* ``raw``      - the file *is* the raw bytes (binary capture, or NMEA text).
* ``hex``      - one big hex blob (whitespace/newlines ignored), e.g. "787811010..."
* ``hexlines`` - one hex-encoded packet per line.
* ``log_export`` - a platform message-log export (``<time> | <imei> | ,"MMI,...*37 | null"``
  rows), unwrapped back into the stream the server received — see ``convert/log_export.py``.

The file is streamed in chunks so binary codecs exercise their real framing logic,
and an optional ``delay`` lets you replay at a realistic pace for the live dashboard.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncIterator

from ..convert import log_export
from ..core.interfaces import Source
from ..core.models import RawFrame
from ..core.registry import register_source

_HEX_CHARS = set("0123456789abcdefABCDEF")
_HEX_WS = _HEX_CHARS | set(" \r\n\t")


@register_source
class FileReplaySource(Source):
    name = "file_replay"

    def __init__(self, path: str, fmt: str = "auto", chunk_size: int = 1024,
                 delay: float = 0.0):
        self.path = Path(path)
        self.fmt = fmt
        self.chunk_size = int(chunk_size)
        self.delay = float(delay)

    # -- format handling --------------------------------------------------- #
    @staticmethod
    def _sniff(data: bytes) -> str:
        # An export row runs to ~1 KB (an IMU sentence), so look at more than the first 512
        # bytes; a cut last line in the sample is tolerated by unwrap().
        try:
            if log_export.unwrap(data[:65536].decode("utf-8")) is not None:
                return "log_export"
        except UnicodeDecodeError:
            pass
        sample = data[:512]
        try:
            text = sample.decode("ascii")
        except UnicodeDecodeError:
            return "raw"  # contains non-ascii bytes -> binary capture
        stripped = text.strip()
        if stripped.startswith("$G") or stripped.startswith("$P"):
            return "raw"  # NMEA text - codec parses it directly
        if stripped and all(c in _HEX_WS for c in text):
            # Looks like hex. Multiple lines that each parse as hex -> hexlines.
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            if len(lines) > 1 and all(len(ln.replace(" ", "")) % 2 == 0 for ln in lines):
                return "hexlines"
            return "hex"
        return "raw"

    def _load_bytes(self) -> bytes:
        data = self.path.read_bytes()
        fmt = self._sniff(data) if self.fmt == "auto" else self.fmt
        if fmt == "raw":
            return data
        text = data.decode("utf-8", errors="ignore")
        if fmt == "hex":
            cleaned = "".join(ch for ch in text if ch in _HEX_CHARS)
            if len(cleaned) % 2:
                cleaned = cleaned[:-1]
            return bytes.fromhex(cleaned)
        if fmt == "hexlines":
            out = bytearray()
            for line in text.splitlines():
                token = line.strip().replace(" ", "")
                if not token:
                    continue
                try:
                    out.extend(bytes.fromhex(token))
                except ValueError:
                    continue  # skip non-hex lines (comments, timestamps)
            return bytes(out)
        if fmt == "log_export":
            out = log_export.unwrap(text)
            if out is None:
                raise ValueError(f"{self.path.name} is not a message-log export")
            return out
        raise ValueError(f"Unknown file format '{fmt}'")

    # -- Source contract --------------------------------------------------- #
    async def frames(self) -> AsyncIterator[RawFrame]:
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        data = self._load_bytes()
        name = self.path.name
        step = max(1, self.chunk_size)
        for i in range(0, len(data), step):
            yield RawFrame(
                data=data[i : i + step],
                transport="file",
                peer=name,
                source=self.name,
            )
            if self.delay:
                await asyncio.sleep(self.delay)
