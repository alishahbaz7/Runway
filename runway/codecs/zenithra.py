"""Zenithra Ax1 sCAN codec — the AIS-140 base packet plus an inline CAN-data field.

The Ax1 sCAN sends one ASCII/CSV sentence per message in the same
``$<HEADER>,<fields...>*<checksum>`` family AIS-140 uses, under the vendor header ``$MMI``.
38 fields, with the CAN payload carried as **one** field (empty when the vehicle bus has
nothing to report), so a packet with and without CAN has the identical field count::

  $MMI,V2.2.0,5.1.29C,NR,1,L,868221073948609,DL1CAB1234,1,03082026,132621,28.550474,N,
  77.268893,E,0.0,0,29,225,0.98,0.51,IND airtel,1,1,13.5,4.1,O,31,404,10,946,E281316,
  0000,00,0.7,0.0,,(),000123*A4                                            <- CAN slot empty

  ...,0000,00,0.7,0.0,000000E1:000040A0010001E2;00000120:0002FEA200000000;...,(),000007*46

Because the base packet *is* AIS-140, this codec derives from :class:`Ais140Codec` — framing,
the field/date helpers, the packet-type and alert-id vocabularies, and the LGN/HLP/EPB
builders all come from there. What it adds is the ``MMI`` layout, which is **not** just
another field-count branch inside ``ais140.py``: from field 26 onwards the two diverge.

  ============  ==================  ================================================
  $MMI field    AIS-140 v1.9 NMP    note
  ============  ==================  ================================================
  1             1 (vendor_id)       a packet-format version (``V2.2.0``) instead — the
                                    only vendor token on the wire is the $MMI header
  2 - 25        2 - 25              identical, field for field
  26            27 (tamper_alert)    AIS-140's ``emergency_status`` (26) is not sent, so
                                    everything below it shifts one slot earlier
  27 - 31       28 - 32             gsm_signal, mcc, mnc, lac, cell_id
  32 - 33       45 - 46             digital in/out — the 4 neighbour-cell triples
                                    (AIS-140 33-44) are not sent
  34 - 35       48 - 49             analog inputs 1 / 2
  36            —                   **CAN data** (this protocol's addition)
  37            51                  OTA details, parenthesized; NOT last here
  38            47                  frame_number (AIS-140's odometer, 50, is not sent)
  ============  ==================  ================================================

The CAN field is ``;``-separated ``<hex id>:<hex payload>`` frames, e.g.
``000000E1:000040A0010001E2`` — an 8-hex-digit CAN id and an 8-byte payload. It is located
**by content** rather than by index, so a firmware that shifts the tail still gets its bus
data decoded. Frames are exposed structurally in ``io["can"]`` (id / numeric id / 11-vs-29-bit
class / payload / dlc) so a DBC or CanPlus signature (``runway/convert/canplus.py``) can turn
them into engineering values downstream; this codec deliberately does no signal scaling —
that needs a per-vehicle signature, not a protocol.

The declarative field grammar lives in ``data/protocols/zenithra.json`` (with a zero-dependency
reference parser in ``data/protocols/zenithra.py``) for transfer between tools.

.. note::
   Confirmed against two live ``$MMI`` NR samples (with and without CAN), 38 fields each.
   Three things are **provisional** until the vendor doc or more captures land:

   * Field 26 (``O``) is read as AIS-140's ``tamper_alert`` (documented values ``C``/``O``,
     which the letter matches) on the assumption that ``emergency_status`` is the dropped
     field, not the other way round.
   * Field 38 (``000123``) is read as ``frame_number`` — 6-digit zero-padded, exactly the
     AIS-140 frame-number format, and it advanced 7 -> 123 between two samples 22 minutes
     apart while the device sat stationary at 0.0 km/h, which an odometer would not do.
     No ``odometer_m`` is therefore reported for this protocol.
   * Only the ``NR`` packet type has been captured. Alert subtypes are assumed to reuse the
     same layout via packet_type + alert_id (as AIS-140 does).

   The **login** is confirmed from live traffic: a plain AIS-140 ``$LGN`` (see ``_LOGIN_MARK``
   below), so it — like ``$HLP``/``$HEL`` health and ``$EPB`` emergency, both still unconfirmed
   on this model — is handed to the AIS-140 builders, tagged with this protocol. Login is
   layout-agnostic there (IMEI/lat-lon found by content); the other two assume the doc layout.

   The trailer checksum after ``*`` is carried through verbatim, not verified: as with
   AIS-140, the values in real traffic match no standard XOR/additive checksum over the
   sentence (``A4``/``46`` in the two samples), so treating it as authoritative would reject
   good packets.
"""

from __future__ import annotations

import re

from ..core.models import Position, Session, UnifiedRecord
from ..core.registry import register_codec
from .ais140 import _ACK_OK, _ALERT_NAMES, _NMP_TYPES, Ais140Codec

_HEADER = b"$MMI,"

# The device logs in with a plain AIS-140 $LGN before it ever sends $MMI:
#
#   $LGN,DL1CAB1234,868221073948609,5.1.29C,SCAN,28.550474,N,77.268893,E*5B
#
# A session's codec is picked ONCE, from the first sample (core/pipeline.py:_pick_codec), so
# detect() has to recognise that login as well — otherwise auto-detect scores ais140 0.9 on it,
# locks the session onto the base codec, and every later $MMI sentence decodes as "unknown".
# What distinguishes it from a genuine Locate 140B login is the protocol-version literal it
# carries in place of AIS-140's "AIS140": SCAN, this device's Ax1 sCAN mode.
_LOGIN_HEADER = b"$LGN,"
_LOGIN_MARK = b"SCAN"

# One sentence carries the ~200-char AIS-140 head plus a CAN dump — 23 frames x 26 chars in
# the sample capture, and a busier bus can send more. The resync valve therefore sits well
# above the base codec's 2 KB (see Ais140Codec.max_sentence for what it protects against).
_MAX_SENTENCE = 8192

# Field count of the confirmed $MMI layout. Only this exact count is field-mapped; any other
# count keeps its tail verbatim in io["tail_raw"] rather than risk mislabeling fields (the
# same rule ais140.py applies to its unconfirmed NRM variant).
_MMI_FIELDS = 38

# One CAN frame as sent: 1-8 hex id digits, ':', then the payload as hex (8 bytes = 16 chars
# in every sample so far; the bound is loose so an oversized/CAN-FD-ish payload still parses).
_CAN_FRAME_RE = re.compile(r"^[0-9A-Fa-f]{1,8}:[0-9A-Fa-f]{2,128}$")


def _is_can_field(token: str) -> bool:
    """Is this CSV field the CAN payload? ``;``-separated ``<hex id>:<hex data>`` pairs.

    Content-based so the field is found wherever a firmware puts it. Nothing else in the
    sentence can match: the only other fields that contain a ``:`` are the OTA details
    (parenthesized, e.g. ``(192.168.1.1,SET:APN,iot)``) and those fail the leading-hex rule.
    """
    if ":" not in token:
        return False
    parts = [p for p in token.split(";") if p]
    return bool(parts) and all(_CAN_FRAME_RE.match(p) for p in parts)


def _can_frames(token: str) -> list[dict]:
    """``000000E1:000040A0010001E2;00000120:0002FEA200000000`` -> one dict per frame.

    Keeps the id and payload exactly as sent (hex strings — a downstream DBC/signature needs
    the raw bytes) and adds what a CAN consumer would otherwise have to re-derive: the numeric
    id, whether it is a standard 11-bit or extended 29-bit id, and the payload length.
    """
    out: list[dict] = []
    for part in token.split(";"):
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


@register_codec
class ZenithraCodec(Ais140Codec):
    name = "zenithra"
    display_name = "Zenithra Ax1 sCAN"
    description = ("Zenithra Ax1 sCAN — AIS-140 base packet ($MMI sentence) with the vehicle "
                   "CAN bus carried inline as <id>:<payload> frames.")
    max_sentence = _MAX_SENTENCE

    def detect(self, sample: bytes) -> float:
        if sample[:5] == _HEADER:
            return 0.95
        # Its login only counts when the sCAN protocol-version literal is there, as a whole
        # field — a genuine AIS-140 login stays with the ais140 codec (0.9). Scored above 0.9
        # so a Zenithra device that logs in first still lands on this codec.
        if sample[:5] == _LOGIN_HEADER:
            head = sample.split(b"*", 1)[0].splitlines()[0]
            if any(tok.strip().upper() == _LOGIN_MARK for tok in head.split(b",")):
                return 0.95
        return 0.0

    def _ack(self, body: str) -> bytes | None:
        # Same server ACK as the base protocol. Unconfirmed for $MMI (no server-side capture
        # yet) — but an AIS-140-derived firmware expects it, and the base codec already sends
        # it for every other sentence this device may open the connection with.
        return _ACK_OK if body.startswith("MMI,") else super()._ack(body)

    # -- dispatch: $MMI here, every AIS-140 header via the base codec ---------- #
    def _parse(self, body: str, checksum: str | None, session: Session,
               raw_hex: str) -> UnifiedRecord | None:
        header, sep, rest = body.partition(",")
        if not sep:
            return None
        if header != "MMI":
            return super()._parse(body, checksum, session, raw_hex)
        return self._mmi(self._fold_ota(rest.split(",")), checksum, session, raw_hex)

    @staticmethod
    def _fold_ota(f: list[str]) -> list[str]:
        """Fold the parenthesized OTA field back into one token — it may itself contain
        commas. Unlike AIS-140's, it is not the last field (frame_number follows), so fold
        only up to the token holding the ``)`` instead of to the end of the sentence."""
        i = next((k for k, tok in enumerate(f) if "(" in tok), None)
        if i is None:
            return f
        j = next((k for k in range(i, len(f)) if ")" in f[k]), None)
        return f if j is None else f[:i] + [",".join(f[i:j + 1])] + f[j + 1:]

    # -- MMI: normal + every alert subtype, with the CAN payload ---------------- #
    def _mmi(self, f: list[str], checksum, session: Session, raw_hex: str) -> UnifiedRecord:
        imei = self._get(f, 6) or session.device_id or f"{self.name}-unknown"
        session.device_id = imei
        ptype = self._get(f, 3) or ""
        alert_id = self._int(self._get(f, 4))

        io = {
            # vendor_id is the sentence header (the only vendor token on the wire) so the QC
            # ledger/report show something meaningful; field 1 is a packet-format version.
            "vendor_id": "MMI", "packet_version": self._get(f, 1),
            "firmware_version": self._get(f, 2),
            "packet_type": ptype, "alert_id": alert_id,
            "packet_status": self._get(f, 5), "vehicle_reg_no": self._get(f, 7),
            "pdop": self._float(self._get(f, 19)), "hdop": self._float(self._get(f, 20)),
            "network_operator": self._get(f, 21), "main_power_status": self._get(f, 23),
            # no emergency_status: this layout drops it (see the module docstring's note)
            "tamper_alert": self._get(f, 26),
            "mcc": self._get(f, 28), "mnc": self._get(f, 29), "lac": self._get(f, 30),
            "cell_id": self._get(f, 31), "checksum": checksum,
        }
        if len(f) == _MMI_FIELDS:
            io.update({
                "digital_input_status": self._get(f, 32),
                "digital_output_status": self._get(f, 33),
                "analog_input_1": self._float(self._get(f, 34)),
                "analog_input_2": self._float(self._get(f, 35)),
                "ota_update_details": self._get(f, 37),
                "frame_number": self._get(f, 38),
            })
        elif len(f) > 31:
            io["tail_raw"] = ",".join(f[31:])

        # By content, not by index — so an unconfirmed variant still yields its bus data.
        can_at = next((i for i, tok in enumerate(f) if _is_can_field(tok)), None)
        frames = _can_frames(f[can_at]) if can_at is not None else []
        io["can_frame_count"] = len(frames)
        if frames:
            io["can"] = frames

        rec = UnifiedRecord(
            device_id=imei, protocol=self.name,
            message_type=_NMP_TYPES.get(ptype, ptype or "normal"),
            event_time=self._datetime(self._get(f, 9), self._get(f, 10)),
            gps_valid=self._get(f, 8) == "1",
            position=Position(latitude=self._signed(f, 11, 12, "S"),
                              longitude=self._signed(f, 13, 14, "W"),
                              altitude_m=self._float(self._get(f, 18))),
            speed_kph=self._float(self._get(f, 15)),
            heading_deg=self._float(self._get(f, 16)),
            altitude_m=self._float(self._get(f, 18)),
            satellites=self._int(self._get(f, 17)),
            ignition=self._get(f, 22) == "1",
            battery_v=self._float(self._get(f, 25)),
            external_v=self._float(self._get(f, 24)),
            gsm_signal=self._int(self._get(f, 27)),
            io=io,
            raw_hex=raw_hex,
        )
        if alert_id and alert_id >= 3:
            rec.alarms.append(_ALERT_NAMES.get(alert_id, f"alert_{alert_id}"))
        return rec
