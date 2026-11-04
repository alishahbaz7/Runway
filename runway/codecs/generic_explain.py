"""Protocol-agnostic field-by-field breakdown for the Parser UI.

Nothing in here knows a protocol. It produces the same ``(kind, payload, offset)`` stream
:meth:`Codec.explain` defines, for **any** registered codec, from two sources that are already
part of the system:

1. **Framing and values come from the codec itself** — :meth:`Codec.decode` is called in a loop,
   so every protocol is split into messages by the same code that decodes it live. A message
   the codec cannot consume becomes a ``skip``, exactly as the Teltonika explainer reports a
   corrupt frame.
2. **Field names, units and notes come from the declarative grammar** in the protocol database
   (``data/protocols/<codec>.json``, passed in as ``grammar`` — this module stays framework-free
   and does no file I/O). The grammar's 1-based ``index`` per field is matched against the
   sentence's own delimited tokens, so the breakdown shows the vendor's field names, offsets
   and byte values, not a guess.

When a protocol has no grammar file yet, the walk still works: it falls back to the normalized
:class:`UnifiedRecord` the codec produced, so a brand-new codec is inspectable the moment it is
dropped into ``codecs/`` — with better output as soon as someone writes its grammar.

Two enrichments are applied on top of the grammar because they are properties of the data, not
of any one protocol:

* **CAN frames** (``io["can"]``) are expanded to one row per frame — a single ``<id>:<payload>``
  blob in one cell is unreadable, and the bus is the point of a CAN-carrying protocol.
* **Everything the codec decoded that the grammar did not name** is listed too (from ``io``),
  so a field the grammar has not caught up with is still visible rather than silently dropped.
"""

from __future__ import annotations

from ..core.interfaces import Codec
from ..core.models import Session, UnifiedRecord

# Delimited ASCII sentence protocols (AIS-140, Zenithra, NMEA) frame like $...*CC and are the
# case where per-field byte offsets can be derived. Anything else still gets a breakdown, just
# built from the decoded record rather than from token positions.
_START = ord("$")
_DELIM = ord(",")
_END = ord("*")


def walk(buffer: bytes, codec: Codec, grammar: dict | None = None, transport: str = "tcp",
         enrich=None):
    """Yield ``(kind, payload, offset)`` for every message in ``buffer``. See Codec.explain.

    ``enrich`` is the same optional ``(record) -> None`` hook the pipeline takes, applied before
    the fields are built — so a breakdown of pasted data shows exactly what a live record would
    carry (e.g. CAN signals decoded from the device's bound signature).
    """
    sess = Session(id="explain", transport=transport)
    off = 0
    n = len(buffer)
    while off < n:
        try:
            out = codec.decode(buffer[off:], sess)
        except Exception as exc:                      # a broken codec must not kill the view
            yield ("error", f"{type(exc).__name__}: {exc}", off)
            return
        if out.consumed <= 0:
            # The codec wants more bytes than the buffer holds — for a static paste that means
            # the tail is a partial message. Report it as skipped instead of looping forever.
            if n - off:
                yield ("skip", {"at": off, "bytes": n - off,
                                "reason": out.note or "incomplete message at end of input"}, n)
            return
        chunk = buffer[off:off + out.consumed]
        if out.records:
            for rec in out.records:
                if enrich is not None:
                    try:
                        enrich(rec)
                    except Exception:          # enrichment is additive, never fatal to a view
                        pass
                yield ("packet", _packet(rec, chunk, off, grammar), off + out.consumed)
        else:
            yield ("skip", {"at": off, "bytes": out.consumed,
                            "reason": out.note or "no record decoded"}, off + out.consumed)
        off += out.consumed


# --------------------------------------------------------------------------- #
# one message -> the Parser's packet shape
# --------------------------------------------------------------------------- #
def _packet(rec: UnifiedRecord, chunk: bytes, byte_offset: int, grammar: dict | None) -> dict:
    tokens = _tokens(chunk)
    covered, fields = _grammar_fields(tokens, grammar) if tokens else (set(), [])
    # Tokens the grammar didn't name (no grammar published yet, or a longer firmware variant)
    # are shown positionally — the operator sees the real structure either way.
    fields += _token_fields(tokens, covered)
    # With a grammar, the wire fields ARE the breakdown; without one, the codec's normalized
    # record is the only field-level truth there is, so show that instead of an empty table.
    fields += _record_fields(rec) if not covered else []
    fields += _io_fields(rec, {f["name"] for f in fields})
    fields += _signal_fields(rec)
    fields += _can_fields(rec)
    return {
        "kind": rec.message_type or "message",
        "codec_id": None,
        "codec_name": rec.protocol,
        # No generic protocol claims a verified checksum here: a codec that verifies one rejects
        # the frame outright (so it never reaches this point), and the ASCII families carry a
        # vendor checksum whose algorithm isn't documented. Left unknown rather than faked.
        "crc_ok": None,
        "record_count": 1,
        "byte_offset": byte_offset,
        "fields": fields,
        "records": [_record_row(rec)],
    }


def _tokens(chunk: bytes) -> list[tuple[str, int, int]]:
    """Split a ``$header,f1,f2,...*CC`` sentence into ``(text, offset, size)`` tokens.

    Offsets are relative to the message, so the UI can highlight the exact bytes of a field.
    Returns ``[]`` for anything that isn't a delimited ASCII sentence (binary protocols), which
    is the signal to build the breakdown from the decoded record instead.

    A parenthesized token that contains delimiters (AIS-140's OTA detail, ``(ip,SET:APN,val)``)
    is folded back into ONE token, since that is how the grammar counts it.
    """
    if not chunk or chunk[0] != _START or _DELIM not in chunk:
        return []
    end = chunk.find(_END)
    body = chunk[1:end if end != -1 else len(chunk)]
    out: list[tuple[str, int, int]] = []
    start = 0
    for i in range(len(body) + 1):
        if i == len(body) or body[i] == _DELIM:
            raw = body[start:i]
            out.append((raw.decode("ascii", "replace"), start + 1, len(raw)))
            start = i + 1
    # fold '(' .. ')' into one token, keeping the opening token's offset and the full byte span
    o = next((k for k, t in enumerate(out) if "(" in t[0]), None)
    if o is not None:
        c = next((k for k in range(o, len(out)) if ")" in out[k][0]), None)
        if c is not None and c > o:
            text = ",".join(t[0] for t in out[o:c + 1])
            out[o:c + 1] = [(text, out[o][1], len(text))]
    return out


def _grammar_fields(tokens: list[tuple[str, int, int]],
                    grammar: dict | None) -> tuple[set[int], list[dict]]:
    """Name each token from the grammar's field list for this message's header.

    Returns the token indexes the grammar accounted for, plus the field rows.
    """
    if not grammar:
        return set(), []
    header = tokens[0][0]
    spec = ((grammar.get("packet_types") or {}).get(header) or {})
    specs = spec.get("fields") or []
    if not specs:
        return set(), []
    group = spec.get("name") or header
    covered = {0}
    fields = [_field(group, "header", tokens[0], "", "sentence header")]
    for fs in specs:
        idx = fs.get("index")
        if not isinstance(idx, int) or idx >= len(tokens):
            continue                                  # grouped/ranged entries, or a short message
        covered.add(idx)
        fields.append(_field(group, fs.get("name") or f"field_{idx}", tokens[idx],
                             fs.get("unit") or "", _hint(fs)))
    return covered, fields


def _token_fields(tokens: list[tuple[str, int, int]], covered: set[int]) -> list[dict]:
    """Positional rows for tokens no grammar entry claimed."""
    group = ("Fields not in the grammar" if covered
             else "Sentence fields (no grammar published for this protocol)")
    return [_field(group, "header" if i == 0 and not covered else f"field_{i}", tok, "", "")
            for i, tok in enumerate(tokens) if i not in covered]


def _hint(fs: dict) -> str:
    """One-line meaning for the tooltip: the grammar's note, plus its value table if small."""
    bits = [fs.get("note") or ""]
    values = fs.get("values")
    if isinstance(values, dict) and len(values) <= 8:
        bits.append("; ".join(f"{k} = {v}" for k, v in values.items()))
    return " — ".join(b for b in bits if b)


def _field(group: str, name: str, token: tuple[str, int, int], unit: str, hint: str) -> dict:
    text, at, size = token
    return {"group": group, "name": name, "size": size, "value": text,
            "hex": text.encode("ascii", "replace").hex(), "bit": "", "unit": unit,
            "at": at, "hint": hint}


# The normalized record's own fields, in a stable order, with the unit the model documents.
# Protocol-agnostic by construction: this is UnifiedRecord, the contract every codec emits.
_RECORD_FIELDS = (
    ("device_id", ""), ("message_type", ""), ("event_time", ""), ("gps_valid", ""),
    ("speed_kph", "km/h"), ("heading_deg", "°"), ("altitude_m", "m"), ("satellites", ""),
    ("ignition", ""), ("battery_v", "V"), ("battery_pct", "%"), ("external_v", "V"),
    ("gsm_signal", ""), ("odometer_m", "m"),
)


def _record_fields(rec: UnifiedRecord) -> list[dict]:
    """The codec's decoded record as field rows — the breakdown for any protocol the database
    has no grammar for yet (including binary ones, where there are no token offsets at all)."""
    out = []
    pos = rec.position
    vals = {"latitude": pos.latitude if pos else None, "longitude": pos.longitude if pos else None}
    for name, unit in _RECORD_FIELDS:
        vals[name] = getattr(rec, name, None)
    if rec.alarms:
        vals["alarms"] = ", ".join(rec.alarms)
    for name in ("device_id", "message_type", "event_time", "gps_valid", "latitude", "longitude",
                 "speed_kph", "heading_deg", "altitude_m", "satellites", "ignition",
                 "battery_v", "battery_pct", "external_v", "gsm_signal", "odometer_m", "alarms"):
        val = vals.get(name)
        if val is None:
            continue
        unit = dict(_RECORD_FIELDS).get(name, "")
        out.append({"group": "Decoded record", "name": name, "size": "", "value": str(val),
                    "hex": "", "bit": "", "unit": unit, "at": 0, "hint": ""})
    return out


def _io_fields(rec: UnifiedRecord, named: set[str]) -> list[dict]:
    """Protocol IO elements the rows above didn't already cover — never silently dropped."""
    out = []
    for key, val in (rec.io or {}).items():
        if key in named or key == "can" or isinstance(val, (list, dict)):
            continue                                  # can[] has its own rows; nested io stays in the record
        out.append({"group": "IO elements", "name": key, "size": "",
                    "value": "" if val is None else str(val), "hex": "", "bit": "",
                    "unit": "", "at": 0, "hint": ""})
    return out


def _signal_fields(rec: UnifiedRecord) -> list[dict]:
    """Named engineering values decoded from the CAN frames by the bound vehicle signature —
    listed above the raw frames, since this is what the operator is actually after."""
    signals = (rec.io or {}).get("can_signals")
    if not isinstance(signals, list) or not signals:
        return []
    group = "CAN signals"
    name = (rec.io or {}).get("can_signature")
    if name:
        group += f" · {name}"
    out = []
    for sig in signals:
        if not isinstance(sig, dict):
            continue
        out.append({"group": group, "name": sig.get("name") or "?", "size": "",
                    "value": str(sig.get("value")), "hex": "", "bit": "", "unit": "", "at": 0,
                    "hint": f"CAN {sig.get('can_id')} · raw {sig.get('raw')}"})
    return out


def _can_fields(rec: UnifiedRecord) -> list[dict]:
    """One row per CAN frame — id, payload bytes and length, straight off the bus."""
    frames = (rec.io or {}).get("can")
    if not isinstance(frames, list):
        return []
    out = []
    for f in frames:
        if not isinstance(f, dict):
            continue
        n = f.get("id_int")
        label = f"0x{n:03X}" if isinstance(n, int) else str(f.get("id") or "?")
        data = str(f.get("data") or "")
        out.append({"group": f"CAN frames ({len(frames)})",
                    "name": f"CAN {label}" + (" ext" if f.get("extended") else ""),
                    "size": f.get("dlc") if f.get("dlc") is not None else "",
                    "value": data, "hex": data, "bit": "", "unit": "", "at": 0,
                    "hint": "raw bus bytes — scale with a DBC/CanPlus signature"})
    return out


def _record_row(rec: UnifiedRecord) -> dict:
    """The record shape the Parser's table/map/summary consume (same keys the Teltonika
    explainer emits, so one UI path serves every protocol)."""
    t = rec.event_time or rec.received_at
    pos = rec.position
    return {
        "timestamp": t.isoformat() if t else "",
        "latitude": pos.latitude if pos else None,
        "longitude": pos.longitude if pos else None,
        "altitude_m": rec.altitude_m,
        "angle_deg": rec.heading_deg,
        "satellites": rec.satellites,
        "speed_kph": rec.speed_kph,
    }
