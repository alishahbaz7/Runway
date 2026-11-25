"""Firmware test runs on disk — ``data/fwtest/<id>/``:

    meta.json     the run: spec, title, source (live on a port, or a file), device, what it pins, state
    packets.log   each packet as it came — received time, connection, the packet — one per line

The packet is written as it came: its own text, with only a byte that cannot stand as text written
as an escape (``\\r \\n \\t \\\\ \\xNN``), so a developer opening the file reads what the device sent,
CR LF and a stray byte included. A packet that is mostly not text (a binary protocol) is
``0x<hex>``. The file's first line (``HEADER``) says so. A log from before 1.1.29 held every packet
in hex: it still reads, and is rewritten as text when Runway loads it (``to_text``).

Nothing judged is stored: the gate is rebuilt from ``packets.log`` when Runway starts, so a run
survives a restart and a corrected spec judges an old run again. A live run is fed by a sink the
API attaches to the listener on its port; a file run is fed once.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

from ..core.interfaces import Sink
from ..core.models import UnifiedRecord
from . import spec as specs
from .gate import Gate
from .insights import insights

UNKNOWN_IDS = {"", "unknown", "ais140-unknown", "zenithra-unknown"}
log = logging.getLogger(__name__)

# ── packets.log: the packet as it came ──────────────────────────────────────────────────────────────
HEADER = ("# Runway firmware test - one packet per line, columns split by a tab: received (Unix time in s, "
          "empty for a file) | connection | the packet exactly as the device sent it | S when the bytes were "
          "not a packet. The packet is its own text; only a byte that is not printable is written as an "
          "escape: \\r \\n \\t \\\\ \\xNN. A packet that is mostly not text (a binary protocol) is 0x<hex>.")
TEXT_SHARE = 0.9                                   # this much of it printable: written as text
_PLAIN = frozenset(range(32, 127)) - {0x5C}        # printable ASCII, the backslash escaped
_ESC = {0x0D: "\\r", 0x0A: "\\n", 0x09: "\\t", 0x5C: "\\\\"}
_CHR = {"r": "\r", "n": "\n", "t": "\t", "\\": "\\"}
_UNESC = re.compile(r"\\(x[0-9a-f]{2}|.?)")
_HEX = re.compile(r"(?:[0-9a-f]{2})+")           # a line from before 1.1.29: the packet in hex


def packet_text(raw: bytes) -> str:
    """The packet for the log: its own text, a byte that is not printable as an escape; mostly not text,
    ``0x<hex>``. Text that would read back as hex (``0x…``, or hex digits only) is written as hex."""
    printable = sum(1 for b in raw if 32 <= b < 127 or b in (9, 10, 13))
    if not raw or printable < TEXT_SHARE * len(raw):
        return "0x" + raw.hex()
    text = "".join(chr(b) if b in _PLAIN else _ESC.get(b, f"\\x{b:02x}") for b in raw)
    if text.startswith("0x") or _HEX.fullmatch(text):
        return "0x" + raw.hex()
    return text


def _unescape(m: re.Match) -> str:
    e = m.group(1)
    if len(e) == 3:
        return chr(int(e[1:], 16))
    if e in _CHR:
        return _CHR[e]
    raise ValueError(f"not an escape: \\{e}")


def packet_bytes(field: str) -> bytes:
    """The bytes back from the log's packet column — text, ``0x<hex>``, or a line before 1.1.29 (hex)."""
    if field.startswith("0x"):
        return bytes.fromhex(field[2:])
    if _HEX.fullmatch(field):
        return bytes.fromhex(field)
    return _UNESC.sub(_unescape, field).encode("latin-1")


def log_line(raw: bytes, received: float | None, conn: str | None, skipped: bool = False) -> str:
    return (f"{'' if received is None else f'{received:.3f}'}\t{conn or ''}\t{packet_text(raw)}"
            + ("\tS" if skipped else "") + "\n")


def read_line(line: str) -> tuple[bytes, float | None, str | None, bool] | None:
    """A log line → (packet, received, connection, skipped); None for the header or a line it cannot read."""
    if not line or line.startswith("#"):
        return None
    parts = line.split("\t")
    if len(parts) not in (3, 4):
        return None
    try:
        received = float(parts[0]) if parts[0] else None
        return packet_bytes(parts[2]), received, parts[1] or None, parts[3:] == ["S"]
    except ValueError:
        return None


def to_text(path: Path) -> int:
    """A log from before 1.1.29 (every packet in hex) rewritten with each packet as text — the same bytes,
    checked packet by packet before the file is replaced. Left as it is when there is nothing to convert
    or the file is busy (it still reads). How many packets were converted."""
    if not path.is_file():
        return 0
    lines = path.read_text(encoding="ascii", errors="replace").splitlines()
    out, converted = [HEADER], 0
    for line in lines:
        if line.startswith("#"):
            continue
        parts = line.split("\t")
        got = read_line(line)
        if got is None or not _HEX.fullmatch(parts[2]):
            out.append(line)                       # already text, or a line it cannot read: kept as it is
            continue
        out.append("\t".join([parts[0], parts[1], packet_text(got[0])] + parts[3:]))
        converted += 1
    if not converted:
        return 0
    before = [read_line(x) for x in lines]
    after = [read_line(x) for x in out[1:]]
    if [x for x in before if x] != [x for x in after if x]:
        log.error("%s: not converted to text — the packets would not read back the same", path)
        return 0
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="ascii") as f:
            f.write("\n".join(out) + "\n")
        check = [read_line(x) for x in tmp.read_text(encoding="ascii").splitlines()]
        if [x for x in check if x] != [x for x in before if x]:
            raise OSError("the written copy does not read back the same")
        os.replace(tmp, path)
    except OSError as exc:                         # busy (another Runway still writes it): next start
        log.warning("%s: left in hex for now (%s)", path, exc)
        tmp.unlink(missing_ok=True)
        return 0
    log.info("%s: %d packets now written as text", path, converted)
    return converted


class Run:
    def __init__(self, folder: Path, meta: dict, spec: specs.Spec):
        self.folder = folder
        self.meta = meta
        self.spec = spec
        self.gate = self._gate()
        self.lock = threading.Lock()
        self._log = None

    def _gate(self) -> Gate:
        g = Gate(self.spec, self.meta.get("expect"), wire=(self.meta.get("source") or {}).get("kind") == "live",
                 config=self.meta.get("config"))
        g.platform = self.meta.get("platform")           # the platform's record compared (23.06), if it was
        return g

    def reconfigure(self, config: dict | None = None, expect: dict | None = None) -> None:
        """The device's settings (or the values pinned) corrected: judged again from the packets as received."""
        with self.lock:
            if config is not None:
                self.meta["config"] = config
            if expect is not None:
                self.meta["expect"] = {k: v for k, v in expect.items() if v}
            self.gate = self._gate()
            if self._log is not None:
                self._log.flush()
            self._replay()
            self.save()

    @property
    def id(self) -> str:
        return self.meta["id"]

    def _replay(self) -> None:
        p = self.folder / "packets.log"
        if not p.is_file():
            return
        for line in p.read_text(encoding="ascii", errors="replace").splitlines():
            got = read_line(line)
            if got is not None:
                raw, received, conn, skipped = got
                self.gate.feed(raw, received, conn, skipped=skipped)

    def add(self, raw: bytes, received: float | None, conn: str | None, skipped: bool = False) -> dict:
        """A packet as it came — or (``skipped``) bytes the protocol could not read, kept and failed."""
        with self.lock:
            if self._log is None:
                p = self.folder / "packets.log"
                fresh = not p.is_file() or p.stat().st_size == 0
                self._log = open(p, "a", encoding="ascii")
                if fresh:
                    self._log.write(HEADER + "\n")
            self._log.write(log_line(raw, received, conn, skipped))
            self._log.flush()
            return self.gate.feed(raw, received, conn, skipped=skipped)

    def save(self) -> None:
        tmp = self.folder / "meta.json.tmp"
        tmp.write_text(json.dumps(self.meta, indent=1), encoding="utf-8")
        tmp.replace(self.folder / "meta.json")

    def close(self) -> None:
        with self.lock:
            if self._log is not None:
                self._log.close()
                self._log = None

    def view(self) -> dict:
        """The run and its summary. While it takes packets its verdict is "in_progress" — what the
        packets so far would give is ``outcome``; the verdict is given when the test stops."""
        live = self.meta.get("state") == "live"
        with self.lock:
            s = self.gate.summary()
            found = insights(self.gate, live=live)
        s["outcome"] = s["verdict"]
        if live:
            s["verdict"] = "in_progress"
        return {**self.meta, "summary": s, "insights": found}


class Runs:
    def __init__(self, root: Path, own_specs: Path | None = None):
        self.root = Path(root)
        self.own_specs = own_specs
        self.runs: dict[str, Run] = {}
        self._loaded = False

    def load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.root.is_dir():
            return
        for d in sorted(self.root.iterdir()):
            meta_p = d / "meta.json"
            if not meta_p.is_file():
                continue
            try:
                meta = json.loads(meta_p.read_text(encoding="utf-8"))
                to_text(d / "packets.log")             # a log from before 1.1.29: its packets as text
                run = Run(d, meta, specs.load(meta["spec"], self.own_specs))
                run._replay()
                self.runs[run.id] = run
            except (ValueError, OSError, KeyError):
                continue

    def create(self, spec_name: str, source: dict, expect: dict | None = None, device: str = "",
               title: str = "", config: dict | None = None) -> Run:
        self.load()
        spec = specs.load(spec_name, self.own_specs)
        rid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
        folder = self.root / rid
        folder.mkdir(parents=True, exist_ok=True)
        meta = {"id": rid, "spec": spec.name, "title": title or spec.name, "source": source,
                "device": (device or "").strip(), "expect": {k: v for k, v in (expect or {}).items() if v},
                "config": config or {},
                "created": time.time(), "state": "live" if source.get("kind") == "live" else "done",
                "stopped": None}
        run = Run(folder, meta, spec)
        run.save()
        self.runs[rid] = run
        return run

    def get(self, rid: str) -> Run | None:
        self.load()
        return self.runs.get(rid)

    def all(self) -> list[Run]:
        self.load()
        return sorted(self.runs.values(), key=lambda r: r.meta.get("created") or 0)

    def delete(self, rid: str) -> bool:
        run = self.runs.pop(rid, None)
        if run is None:
            return False
        run.close()
        shutil.rmtree(run.folder, ignore_errors=True)
        return True


class FirmwareTestSink(Sink):
    """Feeds one live run from a listener's pipeline: the run's device only, every packet as it came.
    A run started without a device takes the first one that identifies itself."""
    name = "fwtest"

    def __init__(self, run: Run):
        self.run = run
        self.conns: set[str] = set()       # the device's connections: all they carry is tested

    async def handle(self, record: UnifiedRecord) -> None:
        if not record.raw_hex:
            return
        meta = self.run.meta
        dev = meta.get("device")
        rid = record.device_id or ""
        if not dev:
            if rid in UNKNOWN_IDS:
                return
            meta["device"] = dev = rid
            self.run.save()
        if rid == dev:
            self.conns.add(record.peer or "")
        elif (record.peer or "") not in self.conns:
            return                                         # another device on the listener
        # on the device's own connection a packet with another IMEI is still its — and is flagged
        received = record.received_at.timestamp() if record.received_at else time.time()
        self.run.add(bytes.fromhex(record.raw_hex), received, record.peer)

    def skipped(self, frame, chunk: bytes, note: str) -> None:
        """Bytes the codec could not read on the device's connection: kept, and failed."""
        if (frame.peer or "") in self.conns and chunk.strip():
            self.run.add(chunk, time.time(), frame.peer, skipped=True)
