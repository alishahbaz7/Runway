"""Platform message-log exports -> the byte stream the server actually received.

The tracking platform exports a device's traffic as text, one row per message received::

    message,
    Wed Nov 04 12:05:17 IST 2026 | 865577071515754 | ,"MMI,V2.2.0,5.1.30I,NR,...,001071*37 | null"

server receive time | device id | the message as received — minus its leading ``$`` and its
line terminator — quoted, then a status column. A codec frames on exactly those two things
(``$`` opens a sentence, the terminator closes it), so the export as-is decodes to nothing.

:func:`unwrap` recognises an export by content and rebuilds the stream: one message per row,
``$`` restored on a ``HDR,...`` sentence, CRLF-terminated (a hex message — a binary protocol —
becomes its bytes), and the rows put back in **arrival order**. The export lists newest first,
and history packets arrive late, so the rows are sorted by receive time rather than reversed.
A line that isn't a row is skipped, so a pasted excerpt with a cut first or last line still
reads.

Each export layout is one regex in ``_FORMATS``; another platform's export is one more entry.
Pure on purpose (no I/O): the Parser endpoint, ``FileReplaySource`` and the CLI all call it.
"""

from __future__ import annotations

import re
from datetime import datetime

# Named groups every format provides: received (server time), device, message.
_FORMATS = (
    # <received> | <device id> | ,"<message> | <status>"   (status is e.g. "null")
    re.compile(r'^(?P<received>[^|"]+?) \| (?P<device>[^|"]+?) \| ,"(?P<message>.*) \| [^|"]*"\s*$'),
)
_SENTENCE = re.compile(r"[A-Z]{3,4},")       # an ASCII sentence header, e.g. MMI, / PVT, / LGN,
_HEX = re.compile(r"(?:[0-9A-Fa-f]{2})+")


def unwrap(text: str) -> bytes | None:
    """The stream inside a message-log export, or ``None`` if ``text`` isn't one.

    It is an export when at least half of its non-empty lines are rows of one known format
    (the header line and a cut first/last line are the usual non-rows).
    """
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return None
    for fmt in _FORMATS:
        rows = [m for m in map(fmt.match, lines) if m]
        if rows and 2 * len(rows) >= len(lines):
            return b"".join(_message_bytes(m["message"]) for m in _arrival_order(rows)
                            if m["message"].strip())
    return None


def _arrival_order(rows: list[re.Match]) -> list[re.Match]:
    whens = [_when(m["received"]) for m in rows]
    if any(w is None for w in whens):
        return rows                               # can't tell — keep the file's order
    if whens[0] > whens[-1]:                      # newest first: flip, so equal times stay in
        rows, whens = rows[::-1], whens[::-1]     # arrival order through the stable sort
    return [m for _, m in sorted(zip(whens, rows), key=lambda p: p[0])]


def _when(received: str) -> datetime | None:
    """Server receive time: Java's ``Date.toString()`` (``Wed Nov 04 12:05:17 IST 2026``,
    zone dropped — one export, one zone) or ISO-8601."""
    parts = received.split()
    if len(parts) == 6:
        try:
            return datetime.strptime(" ".join(parts[:4] + parts[5:]), "%a %b %d %H:%M:%S %Y")
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(received.strip())
    except ValueError:
        return None


def _message_bytes(message: str) -> bytes:
    msg = message.strip().replace('""', '"')      # CSV-escaped quotes
    if _HEX.fullmatch(msg):
        return bytes.fromhex(msg)                 # a binary protocol's message, exported as hex
    if _SENTENCE.match(msg):
        msg = "$" + msg
    return msg.encode("utf-8", "surrogateescape") + b"\r\n"
