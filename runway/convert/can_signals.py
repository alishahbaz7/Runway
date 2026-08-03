"""Decode CAN frames into named engineering values using a vehicle signature.

A **signature** is the repository JSON the Converter produces and the Signatures page stores
(``data/signatures/*.json``): a map of *key* -> list of sub-fields::

    { "00000120": [ {"fieldName": "Odometer", "startingBit": 1, "size": 32,
                     "multiplier": 0.015625, "offset": 0, "signed": 0, "reverse": 0} ] }

The same file format serves two different keys, and which one it is decides where it can bind:

* **CAN-id keyed** — the key is the frame id as the device sends it (8 hex digits). These bind
  to any protocol that carries CAN frames (``io["can"]``), which is what this module does.
* **AVL-id keyed** — short decimal keys (``"903"``) naming a Teltonika AVL parameter whose
  *value bytes* the sub-fields slice. Those are applied by the Parser against AVL parameters,
  not against a bus frame, so they are deliberately ignored here (see :func:`is_can_signature`) —
  otherwise an AVL id like ``900`` would silently bind to CAN frame ``0x900`` and invent signals.

Scaling is the canonical DBC convention, the same one ``CanSignal.decode`` and the DBC export
use::

    physical = raw * multiplier + offset

so a signature authored for this tool expresses "subtract 1000" as ``offset: -1000``. A signature
whose offsets are positive magnitudes will read high by exactly ``2 * offset``; that is a data
issue in the signature, not something to paper over here with a second convention.
"""

from __future__ import annotations

import re

from .canplus import extract_bits

# A signature key that could be a CAN id: hex, optionally 0x-prefixed, up to 29 bits (8 digits).
_CAN_KEY = re.compile(r"^(?:0[xX])?([0-9A-Fa-f]{1,8})$")


def can_id_of(key: str) -> int | None:
    """``"000000E1"`` / ``"0xE1"`` -> 225. ``None`` when the key isn't a hex id at all."""
    m = _CAN_KEY.match((key or "").strip())
    return int(m.group(1), 16) if m else None


def is_can_signature(signature: dict | None) -> bool:
    """Is this signature keyed by CAN frame ids (rather than Teltonika AVL parameter ids)?

    Every key must be hex, and at least one must *look* like a frame id rather than a small
    decimal AVL id — 5+ digits (the exports zero-pad to 8) or containing a hex letter.
    """
    if not isinstance(signature, dict):
        return False
    keys = [k for k, v in signature.items() if isinstance(v, list)]
    if not keys or any(can_id_of(k) is None for k in keys):
        return False
    # Strip only a "0x" prefix — NOT leading zeros: "00000120" is a padded frame id, and
    # measuring it after stripping zeros ("120") would read as a short AVL-style key.
    bare = [k[2:] if k[:2].lower() == "0x" else k for k in keys]
    return any(len(k) >= 5 or any(c in "abcdefABCDEF" for c in k) for k in bare)


def decode_frames(frames: list[dict], signature: dict | None) -> list[dict]:
    """Decode ``io["can"]`` frames with ``signature``; returns one entry per decoded signal.

    Each entry is ``{name, value, can_id, raw}`` — the physical value plus the raw integer and
    the frame it came from, so an operator correcting a start bit can see what was read. Frames
    with no signature entry are skipped (a bus carries far more ids than any signature names),
    and a sub-field that doesn't fit its payload is skipped rather than raising: a signature is
    hand-tunable data, and one bad row must not cost the other signals.
    """
    if not frames or not is_can_signature(signature):
        return []
    by_id: dict[int, list] = {}
    for key, subs in signature.items():
        cid = can_id_of(key)
        if cid is not None and isinstance(subs, list):
            by_id.setdefault(cid, []).extend(subs)

    out: list[dict] = []
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        cid = frame.get("id_int")
        if cid is None:
            cid = can_id_of(str(frame.get("id") or ""))
        subs = by_id.get(cid)
        if not subs:
            continue
        try:
            data = bytes.fromhex(str(frame.get("data") or ""))
        except ValueError:
            continue
        for sub in subs:
            sig = _signal(data, sub)
            if sig is not None:
                out.append({**sig, "can_id": frame.get("id") or f"{cid:08X}"})
    return out


def _signal(data: bytes, sub: dict) -> dict | None:
    name = sub.get("fieldName")
    size = int(sub.get("size") or 0)
    if not name or size <= 0:
        return None
    try:
        raw = extract_bits(data, int(sub.get("startingBit") or 0), size,
                           "little" if sub.get("reverse") else "big", bool(sub.get("signed")))
    except (ValueError, TypeError):
        return None                       # field doesn't fit this payload — skip, don't raise
    mult = sub.get("multiplier")
    mult = 1 if mult in (None, 0) else mult
    value = raw * mult + (sub.get("offset") or 0)
    # Trim binary-float noise (0.015625 * 392516 style products) without hiding real precision.
    if isinstance(value, float):
        value = round(value, 6)
        if value == int(value):
            value = int(value)
    return {"name": name, "value": value, "raw": raw}
