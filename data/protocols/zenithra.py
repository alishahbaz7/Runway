"""Zenithra Ax1 sCAN protocol parser — self-contained, zero-dependency, transferable.

Drop this single file into any Python project and parse Zenithra ASCII sentences:

    from zenithra import parse

    records = parse(raw_text)            # raw_text = bytes/str off the TCP socket / a log file

Each record is a plain ``dict`` (JSON-serialisable). No third-party imports — only the Python
standard library — so it carries cleanly between utilities. See the companion ``zenithra.json``
for the declarative field grammar this mirrors.

The base packet is **AIS-140**; over and above it the sentence carries the vehicle **CAN bus**
inline. Sentences are ASCII, comma-delimited, framed as ``$MMI,<38 fields>*<checksum>``::

    $MMI,V2.2.0,5.1.29C,NR,1,L,868221073948609,DL1CAB1234,1,03082026,132621,28.550474,N,
    77.268893,E,0.0,0,29,225,0.98,0.51,IND airtel,1,1,13.5,4.1,O,31,404,10,946,E281316,
    0000,00,0.7,0.0,<can>,(),000123*A4

with ``<can>`` = ``;``-separated ``<hex id>:<hex payload>`` frames (empty when the bus has
nothing to report, so the field count is 38 either way). CAN frames are returned verbatim plus
their numeric id / dlc — signal scaling needs a per-vehicle DBC or CanPlus signature, not the
protocol, so none is attempted here.

Fields 1-25 match the AIS-140 v1.9 normal packet field for field (except field 1, a packet
version here). From field 26 the layouts diverge — see ``zenithra.json`` for the full mapping.

.. note::
   Derived from two live captures, not a vendor document. Two readings are provisional and
   flagged inline below: field 26 (``tamper_alert``) and field 38 (``frame_number``). The
   checksum after ``*`` is carried through as-is, not verified — real values match no standard
   XOR/additive checksum over the sentence.
"""

from __future__ import annotations

MESSAGE_ALERT_IDS = {
    1: "Location Update (default)",
    2: "Location Update (history)",
    3: "Alert - Disconnect from main battery",
    4: "Alert - Low battery",
    5: "Alert - Low battery removed",
    6: "Alert - Connect back to main battery",
    7: "Alert - Ignition ON",
    8: "Alert - Ignition OFF",
    9: "Alert - GPS box opened (optional)",
    10: "Alert - Emergency state ON",
    11: "Alert - Emergency state OFF",
    12: "Alert - Over-the-air parameter change",
    13: "Harsh Braking",
    14: "Harsh Acceleration",
    15: "Rash Turning",
    16: "Device Tampered",
}

# Inherited from the AIS-140 base packet; only NR has been observed on this model so far.
PACKET_TYPES = {
    "NR": "normal", "EA": "emergency_alert", "TA": "tamper_alert", "HP": "health_packet",
    "IN": "ignition_on", "IF": "ignition_off", "BD": "battery_disconnect",
    "BR": "battery_reconnect", "BL": "internal_battery_low", "OT": "ota_update_alert",
    "RT": "rash_turn", "HB": "harsh_braking", "HA": "harsh_acceleration",
}

MMI_FIELDS = 38                 # confirmed field count of the $MMI sentence


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def parse(text) -> list:
    """Parse a buffer that may hold one or more concatenated ``$...*CC`` sentences.

    Incomplete trailing data (no closing ``*`` yet) is left unparsed rather than guessed at —
    callers streaming live data should hold it and retry once more bytes arrive.
    """
    if isinstance(text, bytes):
        text = text.decode("ascii", errors="replace")
    out = []
    start = text.find("$")
    while start != -1:
        star = text.find("*", start)
        if star == -1:
            break
        j = star + 1
        while j < len(text) and j < star + 3 and text[j] not in ",$\r\n":
            j += 1
        checksum = text[star + 1:j] or None
        rec = parse_sentence(text[start + 1:star])
        if rec is not None:
            rec["checksum"] = checksum
            out.append(rec)
        start = text.find("$", j)
    return out


def parse_sentence(body: str) -> dict | None:
    """Parse a single sentence body (without the leading ``$`` / trailing ``*checksum``)."""
    header, sep, rest = body.partition(",")
    if not sep:
        return None
    if header == "MMI":
        return _parse_mmi(rest)
    if header == "LGN":
        return _parse_lgn(rest)
    # Health (HLP/HEL) and emergency (EPB) have not been captured on this model; parse those
    # with the companion ais140.py if a device turns out to send them.
    return {"type": "unknown", "header": header, "raw": body}


def parse_can(field: str) -> list:
    """``000000E1:000040A0010001E2;00000120:0002FEA200000000`` -> one dict per CAN frame.

    Keeps the id and payload exactly as sent (hex strings — a DBC/signature needs the raw
    bytes) and adds what a consumer would otherwise re-derive: the numeric id, whether it is a
    standard 11-bit or an extended 29-bit id, and the payload length in bytes.
    """
    out = []
    for part in (field or "").split(";"):
        part = part.strip()
        if not part:
            continue
        cid, sep, data = part.partition(":")
        if not sep:
            continue
        try:
            id_int = int(cid, 16)
        except ValueError:
            continue
        out.append({"id": cid, "id_int": id_int, "extended": id_int > 0x7FF,
                    "data": data, "dlc": len(data) // 2})
    return out


# --------------------------------------------------------------------------- #
# $MMI parser
# --------------------------------------------------------------------------- #
def _get(fields: list, index: int):
    """1-based field lookup — returns None (not an IndexError) past the end of a short sentence."""
    return fields[index - 1] if 0 < index <= len(fields) else None


def _signed(value, hemi, neg_hemi: str):
    if not value:
        return None
    try:
        v = float(value)
    except ValueError:
        return None
    return -v if hemi == neg_hemi else v


def _fold_ota(f: list) -> list:
    """Fold the parenthesized OTA field back into one token — it may itself contain commas.
    Unlike AIS-140's, it is not the last field (frame_number follows), so fold only up to the
    token holding the ``)``."""
    i = next((k for k, tok in enumerate(f) if "(" in tok), None)
    if i is None:
        return f
    j = next((k for k in range(i, len(f)) if ")" in f[k]), None)
    return f if j is None else f[:i] + [",".join(f[i:j + 1])] + f[j + 1:]


def _parse_lgn(rest: str) -> dict:
    """``$LGN,DL1CAB1234,868221073948609,5.1.29C,SCAN,28.550474,N,77.268893,E*5B``

    The AIS-140 login minus its vendor_id, so every field sits one slot earlier than the v1.9
    doc's 9-field layout. ``protocol_version`` (``SCAN``) is the only field that identifies this
    device family at login time — a tool that picks its parser from the first packet of a
    connection must key on it, or it locks onto AIS-140 and then cannot read any $MMI sentence.
    """
    f = rest.split(",")
    return {
        "type": "login",
        "vehicle_reg_no": _get(f, 1),
        "imei": _get(f, 2),
        "firmware_version": _get(f, 3),
        "protocol_version": _get(f, 4),
        "latitude": _signed(_get(f, 5), _get(f, 6), "S"),
        "longitude": _signed(_get(f, 7), _get(f, 8), "W"),
    }


def _parse_mmi(rest: str) -> dict:
    f = _fold_ota(rest.split(","))
    ptype = _get(f, 3)
    rec = {
        "type": PACKET_TYPES.get(ptype, ptype or "normal"),
        "packet_version": _get(f, 1),
        "firmware_version": _get(f, 2),
        "packet_type": ptype,
        "alert_id": _get(f, 4),
        "alert_name": MESSAGE_ALERT_IDS.get(_int(_get(f, 4))),
        "packet_status": _get(f, 5),
        "imei": _get(f, 6),
        "vehicle_reg_no": _get(f, 7),
        "gps_fix": _get(f, 8) == "1",
        "date": _get(f, 9),
        "time": _get(f, 10),
        "latitude": _signed(_get(f, 11), _get(f, 12), "S"),
        "longitude": _signed(_get(f, 13), _get(f, 14), "W"),
        "speed_kph": _float(_get(f, 15)),
        "heading_deg": _float(_get(f, 16)),
        "satellites": _int(_get(f, 17)),
        "altitude_m": _float(_get(f, 18)),
        "pdop": _float(_get(f, 19)),
        "hdop": _float(_get(f, 20)),
        "network_operator": _get(f, 21),
        "ignition": _get(f, 22) == "1",
        "main_power_status": _get(f, 23),
        "main_input_voltage": _float(_get(f, 24)),
        "internal_battery_voltage": _float(_get(f, 25)),
        # provisional: AIS-140's C/O tamper field, one slot earlier because this layout does
        # not send emergency_status (zenithra.json field 26 note)
        "tamper_alert": _get(f, 26),
        "gsm_signal": _int(_get(f, 27)),
        "mcc": _get(f, 28),
        "mnc": _get(f, 29),
        "lac": _get(f, 30),
        "cell_id": _get(f, 31),
    }
    if len(f) == MMI_FIELDS:
        rec.update({
            "digital_input_status": _get(f, 32),
            "digital_output_status": _get(f, 33),
            "analog_input_1": _float(_get(f, 34)),
            "analog_input_2": _float(_get(f, 35)),
            "ota_update_details": _get(f, 37),
            # provisional: a sequence counter, not an odometer (zenithra.json field 38 note)
            "frame_number": _get(f, 38),
        })
    elif len(f) > 31:
        # Unconfirmed variant — keep the tail verbatim rather than mislabel its fields.
        rec["tail_raw"] = ",".join(f[31:])
    # located by content, so a variant layout still yields its bus data
    can_field = next((tok for tok in f if _is_can(tok)), "")
    rec["can"] = parse_can(can_field)
    rec["can_frame_count"] = len(rec["can"])
    return rec


def _is_can(token: str) -> bool:
    """Is this CSV field the CAN payload? ``;``-separated ``<hex id>:<hex data>`` pairs. The
    only other field that can hold a ``:`` is the parenthesized OTA detail, which fails the
    leading-hex rule."""
    if ":" not in token:
        return False
    parts = [p for p in token.split(";") if p]
    if not parts:
        return False
    for p in parts:
        cid, sep, data = p.partition(":")
        if not sep or not (1 <= len(cid) <= 8) or not (2 <= len(data) <= 128):
            return False
        if not _is_hex(cid) or not _is_hex(data):
            return False
    return True


def _is_hex(s: str) -> bool:
    return bool(s) and all(c in "0123456789abcdefABCDEF" for c in s)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _int(v):
    try:
        return int(v) if v not in (None, "", "x") else None
    except ValueError:
        return None


def _float(v):
    try:
        return float(v) if v not in (None, "", "x") else None
    except ValueError:
        return None


if __name__ == "__main__":                  # CLI: feed raw text on argv or stdin
    import json
    import sys

    src = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else sys.stdin.read()
    print(json.dumps(parse(src), indent=2))
