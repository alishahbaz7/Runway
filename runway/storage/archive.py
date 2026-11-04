"""Packet archive — every packet a storing listener receives, one file per device per day.

``data/archive/<device>/<device>_<YYYY-MM-DD>.log`` (local date), one line per packet::

    2026-11-04T16:40:39.123+05:30<TAB>imu<TAB>$MMI,V2.2.0,5.1.30I,IMU,200,L,...*61

received time, the decoded message type, and **the packet exactly as received** — ASCII as it
is with its CRLF left off, anything else as ``0x<hex>``. A device that reconnects the same day
keeps appending to its file; a finished day is gzipped (``….log.gz``, ~7.7x smaller on real
IMU traffic: 20 devices x 30 days in 1-3.5 GB instead of 19-96 GB of decoded rows).

The packet is the record. Every field — time, position, speed, satellites, voltages, ignition,
IMU samples — is extracted from it by the device's codec when it is needed, so nothing decoded is
stored twice, and the same bytes can be re-sent to a platform (:func:`replay_lines`).

Synchronous and thread-safe; async callers run it in an executor (``sinks/archive_store.py``).
"""

from __future__ import annotations

import gzip
import os
import re
import shutil
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator, NamedTuple

from ..core.models import UnifiedRecord
from .db import DATA_DIR

DEFAULT_ROOT = os.path.join(DATA_DIR, "archive")
_SAFE = re.compile(r"[^A-Za-z0-9._-]")
_DAY = re.compile(r"_(\d{4}-\d{2}-\d{2})\.log(\.gz)?$")


class Line(NamedTuple):
    received: str          # ISO 8601, local time with offset
    kind: str              # decoded message type ("login", "imu", "normal", ...)
    raw: bytes             # the packet exactly as received


def encode_raw(raw: bytes) -> str:
    """Printable ASCII ending in CRLF is kept as text (CRLF implied); anything else as hex, so
    the bytes come back exactly."""
    body = raw[:-2] if raw.endswith(b"\r\n") else None
    if body is not None and body and all(32 <= b < 127 for b in body):
        return body.decode("ascii")
    return "0x" + raw.hex()


def decode_raw(field: str) -> bytes:
    return bytes.fromhex(field[2:]) if field.startswith("0x") else field.encode("ascii") + b"\r\n"


def parse_line(text: str) -> Line | None:
    parts = text.rstrip("\n").split("\t", 2)
    if len(parts) != 3 or not parts[2]:
        return None
    try:
        return Line(parts[0], parts[1], decode_raw(parts[2]))
    except ValueError:
        return None


class PacketArchive:
    def __init__(self, root: str = DEFAULT_ROOT):
        self.root = Path(root)
        self._lock = threading.Lock()
        self._open: dict[str, tuple[str, object]] = {}      # device -> (day, file handle)

    # -- write --------------------------------------------------------------- #
    def _path(self, device: str, day: str, gz: bool = False) -> Path:
        d = _SAFE.sub("_", device)
        return self.root / d / f"{d}_{day}.log{'.gz' if gz else ''}"

    def write(self, rec: UnifiedRecord) -> None:
        if not rec.device_id or not rec.raw_hex or rec.device_id.endswith("-unknown"):
            return
        when = (rec.received_at or datetime.now().astimezone()).astimezone()
        day = when.date().isoformat()
        line = (f"{when.isoformat(timespec='milliseconds')}\t{rec.message_type or ''}\t"
                f"{encode_raw(bytes.fromhex(rec.raw_hex))}\n")
        with self._lock:
            cur = self._open.get(rec.device_id)
            if cur is None or cur[0] != day:
                if cur is not None:
                    cur[1].close()
                path = self._path(rec.device_id, day)
                path.parent.mkdir(parents=True, exist_ok=True)
                cur = (day, open(path, "a", encoding="ascii", newline="\n"))
                self._open[rec.device_id] = cur
            cur[1].write(line)
            cur[1].flush()

    def close(self) -> None:
        with self._lock:
            for _, fh in self._open.values():
                fh.close()
            self._open.clear()

    def compress_finished(self, today: date | None = None) -> int:
        """Gzip every day file older than today (the current day is still being written)."""
        today_s = (today or date.today()).isoformat()
        done = 0
        with self._lock:
            open_now = {str(self._path(dev, d)) for dev, (d, _) in self._open.items()}
            for path in self.root.glob("*/*.log"):
                m = _DAY.search(path.name)
                if not m or m.group(1) >= today_s or str(path) in open_now:
                    continue
                gz = path.with_name(path.name + ".gz")
                with open(path, "rb") as src, gzip.open(gz, "wb", compresslevel=6) as dst:
                    shutil.copyfileobj(src, dst)
                path.unlink()
                done += 1
        return done

    def purge(self, keep_days: int, today: date | None = None) -> int:
        """Delete finished day files older than ``keep_days`` days; returns how many went."""
        cut = ((today or date.today()) - timedelta(days=keep_days)).isoformat()
        gone = 0
        with self._lock:
            open_now = {str(self._path(dev, d)) for dev, (d, _) in self._open.items()}
            for path in self.root.glob("*/*.log*"):
                m = _DAY.search(path.name)
                if m and m.group(1) < cut and str(path) not in open_now:
                    path.unlink()
                    gone += 1
        return gone

    def size(self) -> int:
        return sum(p.stat().st_size for p in self.root.glob("*/*.log*")) if self.root.is_dir() else 0

    # -- read ---------------------------------------------------------------- #
    def days(self) -> list[dict]:
        """Every archived device-day: size on disk, whether it is compressed yet."""
        out = []
        if not self.root.is_dir():
            return out
        for dev_dir in sorted(p for p in self.root.iterdir() if p.is_dir()):
            for path in dev_dir.iterdir():
                m = _DAY.search(path.name)
                if m:
                    out.append({"device_id": dev_dir.name, "day": m.group(1), "bytes": path.stat().st_size,
                                "compressed": bool(m.group(2)), "file": path.name})
        return sorted(out, key=lambda d: (d["device_id"], d["day"]), reverse=True)

    def file_for(self, device: str, day: str) -> Path | None:
        for gz in (False, True):
            p = self._path(device, day, gz)
            if p.is_file():
                return p
        return None

    def lines(self, device: str, day: str) -> Iterator[Line]:
        path = self.file_for(device, day)
        if path is None:
            return
        if path.suffix == ".log":
            with self._lock:                  # make sure the open day's last line is on disk
                cur = self._open.get(device)
                if cur is not None and cur[0] == day:
                    cur[1].flush()
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="ascii", newline="\n") as fh:
            for text in fh:
                line = parse_line(text)
                if line is not None:
                    yield line

    def last_login_before(self, device: str, day: str, look_back: int = 7) -> Line | None:
        """The device's most recent login before ``day`` (a replay that starts mid-session
        sends it first, so the platform sees a normal session start)."""
        earlier = sorted((d["day"] for d in self.days() if d["device_id"] == _SAFE.sub("_", device)
                          and d["day"] < day), reverse=True)[:look_back]
        for d in earlier:
            last = None
            for line in self.lines(device, d):
                if line.kind == "login":
                    last = line
            if last is not None:
                return last
        return None


def replay_lines(arch: PacketArchive, device: str, day: str) -> list[Line]:
    """A day's packets in order, preceded by the device's last login when the day does not open
    with one."""
    lines = list(arch.lines(device, day))
    if lines and lines[0].kind != "login":
        login = arch.last_login_before(device, day) or next((x for x in lines if x.kind == "login"), None)
        if login is not None:
            lines.insert(0, login)
    return lines


archive = PacketArchive()
