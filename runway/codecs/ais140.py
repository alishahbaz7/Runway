"""AIS-140 codec (Locate 140B and compatible India-VLT-mandate trackers).

ASCII/CSV sentences, one message per line: ``$<HEADER>,<fields...>*<checksum>``.

Two header vocabularies are handled, both mapped onto the same builders:

  * The **documented AIS-140 v1.9 spec**: LGN (login), NMP (normal + alerts), HLP (health),
    EPB (emergency) — as in the reference doc's own examples.
  * **Real Locate 140B (model "LOCT14A") wire traffic**, confirmed against a live capture:
    same LGN header, but PVT instead of NMP and HLM instead of HLP. Concretely different
    from the doc, not just a naming variant:

    - **LGN** on the wire carries only 7 fields (no vehicle name / protocol-version literal),
      e.g. ``$LGN,0000,860103062389003,01.3.0,10.07757430,N,76.33675600,E*`` — IMEI and
      firmware version sit one slot earlier than the doc's 9-field layout. IMEI is therefore
      located by content (a 14-16 digit run), not by fixed position, so both layouts parse.
    - **PVT** matches the doc's NMP field-for-field through field 32 (cell_id), then diverges:
      no frame number / analog inputs / OTA field, the 4 neighbour-cell triples are ordered
      ``(cell_id, lac, signal_strength)`` — reversed from the doc's ``(ss, lac, cellid)`` — and
      the last CSV field is this firmware's own checksum, with nothing following ``*`` at all.
      Confirmed stable (exactly 48 fields, every field) across 30 consecutive live records.
    - **HLM** matches the doc's HLP layout exactly, field for field — only the header name and
      the (blank) vendor slot differ.
    - **NRM**: a 3rd variant seen on the same fleet (different firmware, still "01.3.0"), 56
      fields. Head fields 1-32 match PVT/NMP exactly, so those still map correctly, but its
      tail (neighbour cells / digital IO / odometer / checksum) hasn't been reverse-engineered
      yet — kept verbatim in ``io["tail_raw"]`` rather than guessed at.

  * The **Locate 140 VB** ``$PVT`` (vendor ``GTRP``), per its AIS-140 compliance sheet: the
    doc's NMP layout without the odometer, so the parenthesized OTA response is field 50.

The normal/alert sentence therefore comes in several layouts that share fields 1-25 and
diverge after. Each is a row in :data:`NMP_LAYOUTS` (an :class:`NmpLayout`), picked by the
sentence's shape — field count, and whether the last field is the parenthesized one — so a new
firmware layout is a new table entry, not a new branch in the builder.

Our own **custom packets** (e.g. ``IMU``, alert id 200 — 6-axis sensor samples) ride those
same layouts and carry their data in the parenthesized field. They are recognised by packet
type + alert id, and their payload parsers live in ``ais140_custom.py``; see that module.

  $LGN,VENDOR,DL01PC9821,862631032208018,1.6.5,AIS140,28.7589630,N,77.6277844,E*54    (doc)
  $PVT,LOCT14A,2.2.5,NR,2,H,868329087318766,0000000000,1,03072026,152735,...*        (real)
  $HLM,,01.3.0,860103062389003,100,33,8,20,1,0000,11*                                (real)

Server responses per the spec are literal ACK strings, not a CRC-checked frame — login/
normal/health get ``+##Received OK``; emergency gets no ack.

The declarative field grammar this mirrors lives in ``data/protocols/ais140.json`` (with a
zero-dependency reference parser in ``data/protocols/ais140.py``) for transfer between tools;
this file is the pipeline-integrated copy, kept independent on purpose (same pattern as
``teltonika.py`` / ``data/protocols/teltonika.py``). It documents the v1.9 spec only — the
real-wire deviations above are specific to this codec until the JSON grammar is updated too.

.. note::
   EPB (emergency) is still parsed strictly per the v1.9 doc — no real emergency capture has
   been seen yet to confirm it the way LGN/PVT/HLM have been.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from ..core.interfaces import Codec
from ..core.models import DecodeOutput, Position, Session, UnifiedRecord
from ..core.registry import register_codec
from .ais140_custom import CUSTOM_PACKETS

_ACK_OK = b"+##Received OK"

# Safety valve: a plain sentence is a few hundred bytes. If no '*' terminator has turned up
# within this many bytes of a '$', the stream isn't what we think it is (wrong codec picked by
# auto-detect, a firmware that doesn't send '*', or corrupted traffic) -- without this,
# decode() would return consumed=0 forever: buffer grows unbounded, zero records ever
# produced, connection looks "stuck" even though bytes are still arriving.
# Sized for custom packets: an IMU packet is ~240 bytes of head plus ~35 per sample (930 bytes
# at today's 20 samples), so 4 KB leaves room for ~100. Exposed as Codec.max_sentence so a
# derived family with bigger packets raises it further (zenithra.py: an inline CAN dump).
_MAX_SENTENCE = 4096

# $HEL is the health header real Locate 140B firmware (2.2.5) sends on the wire — the v1.9
# doc only names $HLP/$HLM, so both are accepted (see _parse/_ack dispatch).
_HEADERS = (b"$LGN,", b"$NMP,", b"$HLP,", b"$HEL,", b"$EPB,", b"$PVT,", b"$HLM,", b"$NRM,")

_IMEI_RE = re.compile(r"^\d{14,16}$")
# x.y.z, with an optional build letter — real firmware strings include both "01.3.0" (Locate
# 140B) and "5.1.29C" (Zenithra Ax1 sCAN), and the login locates this field by content.
_FW_RE = re.compile(r"^\d+\.\d+\.\d+[A-Za-z]?$")

_NMP_TYPES = {
    "NR": "normal", "EA": "emergency_alert", "TA": "tamper_alert", "HP": "health_packet",
    "IN": "ignition_on", "IF": "ignition_off", "BD": "battery_disconnect",
    "BR": "battery_reconnect", "BL": "internal_battery_low", "OT": "ota_update_alert",
    "RT": "rash_turn", "HB": "harsh_braking", "HA": "harsh_acceleration",
}
_EPB_TYPES = {"EMR": "emergency", "SEM": "stop_message"}
_ALERT_NAMES = {
    3: "disconnect_main_battery", 4: "low_battery", 5: "low_battery_removed",
    6: "connect_main_battery", 7: "ignition_on", 8: "ignition_off", 9: "gps_box_opened",
    10: "emergency_on", 11: "emergency_off", 12: "ota_param_change", 13: "harsh_braking",
    14: "harsh_acceleration", 15: "rash_turning", 16: "device_tampered",
}
# Every alarm string this codec can ever append to UnifiedRecord.alarms (the 14 real
# alert_id codes 3-16 from data/protocols/ais140.json's message_alert_ids -- 1/2 are
# routine location updates, not alarms -- plus EPB's 2 emergency types: 16 total).
_ALARM_CATALOG = {
    "disconnect_main_battery": "Disconnect from main battery", "low_battery": "Low battery",
    "low_battery_removed": "Low battery removed", "connect_main_battery": "Connect back to main battery",
    "ignition_on": "Ignition ON", "ignition_off": "Ignition OFF", "gps_box_opened": "GPS box opened",
    "emergency_on": "Emergency state ON", "emergency_off": "Emergency state OFF",
    "ota_param_change": "OTA parameter change", "harsh_braking": "Harsh braking",
    "harsh_acceleration": "Harsh acceleration", "rash_turning": "Rash turning",
    "device_tampered": "Device tampered (emergency button wire cut)",
    "emergency": "Emergency (panic button)", "emergency_stop": "Emergency stop message",
}


@dataclass(frozen=True)
class NmpLayout:
    """Where one layout of the normal/alert sentence puts its fields (1-based, after the header).

    Fields 1-25 (vendor slot ... battery voltage) are the same in every layout and are read by
    the builder directly; everything from field 26 on is declared here.
    """
    name: str
    text: dict[str, int]                                    # io key -> index, kept as sent
    numbers: dict[str, int] = field(default_factory=dict)   # io key -> index, read as float
    gsm_signal: int | None = None
    nmr_at: int | None = None                               # first of the 4 neighbour-cell triples
    nmr_order: tuple[str, str, str] = ("signal_strength", "lac", "cell_id")
    odometer: int | None = None
    payload: int | None = None   # the parenthesized field: OTA response, or a custom packet's data
    tail_from: int | None = None  # unmapped layout: fields from here on, verbatim, in tail_raw


_SERVING = {"emergency_status": 26, "tamper_alert": 27, "mcc": 29, "mnc": 30, "lac": 31,
            "cell_id": 32}
_DIO = {"digital_input_status": 45, "digital_output_status": 46}
_ANALOG = {"analog_input_1": 48, "analog_input_2": 49}

# The documented AIS-140 v1.9 schema: neighbour triples as (ss, lac, cellid), digital in/out,
# frame number, both analog inputs, odometer, then the OTA field.
DOC_V19 = NmpLayout("ais140_v1.9", text={**_SERVING, **_DIO, "frame_number": 47},
                    numbers=_ANALOG, gsm_signal=28, nmr_at=33, odometer=50, payload=51)
# Locate 140 VB — the AIS-140 compliance sheet: the doc layout without the odometer, so the OTA
# response (or a custom packet's payload) is field 50. Zenithra's 50-field $MMI is this too.
AIS140_VB = NmpLayout("ais140_vb", text={**_SERVING, **_DIO, "frame_number": 47},
                      numbers=_ANALOG, gsm_signal=28, nmr_at=33, payload=50)
# Real Locate 140B (LOCT14A) PVT, confirmed against a live capture: no frame number / analog
# inputs / OTA field, neighbour triples as (cellid, lac, ss) — reversed from the doc — and this
# firmware's own checksum inline as the last field (nothing follows '*' on the wire).
LOCT14A_PVT = NmpLayout("locate140b_pvt", text={**_SERVING, **_DIO, "trailer_checksum": 48},
                        gsm_signal=28, nmr_at=33,
                        nmr_order=("cell_id", "lac", "signal_strength"), odometer=47)
# Any other shape — e.g. "NRM" (56 fields, different firmware on the same fleet). Fields 26-32
# still match, but one live sample isn't enough to name the tail: keep it verbatim rather than
# guess (and risk mislabeling) until it is reverse-engineered like PVT was.
NMP_UNMAPPED = NmpLayout("unmapped", text=_SERVING, gsm_signal=28, tail_from=33)

# Keyed by (field count, last field parenthesized). The second half is what separates a doc
# sentence sent without its OTA field from a VB sentence — both 50 fields.
NMP_LAYOUTS: dict[tuple[int, bool], NmpLayout] = {
    (51, True): DOC_V19,
    (50, False): replace(DOC_V19, name="ais140_v1.9_no_ota", payload=None),
    (50, True): AIS140_VB,
    (48, False): LOCT14A_PVT,
}


@register_codec
class Ais140Codec(Codec):
    name = "ais140"
    display_name = "AIS-140 (Locate 140B)"
    description = ("AIS-140 / India VLT mandate ASCII-CSV protocol (Locate 140B and compatible "
                   "devices) — login, normal/alert, health and emergency sentences, server ACK.")
    alarm_catalog = _ALARM_CATALOG
    max_sentence = _MAX_SENTENCE

    def detect(self, sample: bytes) -> float:
        return 0.9 if sample[:5] in _HEADERS else 0.0

    # -- framing: one $...*checksum[\r\n] sentence at a time -------------- #
    def decode(self, buffer: bytes, session: Session) -> DecodeOutput:
        b = buffer
        if not b:
            return DecodeOutput(consumed=0, note="need more data")
        if b[0:1] != b"$":
            idx = b.find(b"$", 1)
            return DecodeOutput(consumed=idx if idx != -1 else len(b), note="resync")

        star = b.find(b"*")
        if star == -1:
            if len(b) > self.max_sentence:
                idx = b.find(b"$", 1)
                return DecodeOutput(consumed=idx if idx != -1 else len(b),
                                    note=f"resync: no '*' within {self.max_sentence} bytes")
            return DecodeOutput(consumed=0, note="need full sentence")

        j = star + 1
        while j < len(b) and j < star + 3 and b[j:j + 1] not in (b",", b"$", b"\r", b"\n"):
            j += 1
        checksum = b[star + 1:j].decode("ascii", errors="ignore") or None
        consumed = j + 2 if b[j:j + 2] == b"\r\n" else j + 1 if b[j:j + 1] == b"\n" else j

        body = b[1:star].decode("ascii", errors="replace")
        rec = self._parse(body, checksum, session, raw_hex=b[:consumed].hex())
        ack = self._ack(body)
        return DecodeOutput(records=[rec] if rec else [], consumed=consumed, ack=ack)

    def _ack(self, body: str) -> bytes | None:
        header = body.split(",", 1)[0]
        return _ACK_OK if header in ("LGN", "NMP", "HLP", "HEL", "PVT", "HLM", "NRM") else None

    # -- dispatch ------------------------------------------------------------ #
    def _parse(self, body: str, checksum: str | None, session: Session, raw_hex: str) -> UnifiedRecord | None:
        header, sep, rest = body.partition(",")
        if not sep:
            return None
        builder = {"LGN": self._lgn, "NMP": self._nmp, "PVT": self._nmp, "NRM": self._nmp,
                   "HLP": self._hlp, "HEL": self._hlp, "HLM": self._hlp, "EPB": self._epb}.get(header)
        if builder is None:
            return UnifiedRecord(device_id=session.device_id or f"{self.name}-unknown", protocol=self.name,
                                  message_type="unknown", io={"header": header, "checksum": checksum},
                                  raw_hex=raw_hex)
        return builder(rest.split(","), checksum, session, raw_hex)

    # -- field helpers -------------------------------------------------------- #
    @staticmethod
    def _get(f: list[str], index: int) -> str | None:
        return f[index - 1] if 0 < index <= len(f) else None

    @classmethod
    def _signed(cls, f: list[str], val_i: int, hemi_i: int, neg: str) -> float | None:
        v = cls._get(f, val_i)
        if not v:
            return None
        try:
            x = float(v)
        except ValueError:
            return None
        return -x if cls._get(f, hemi_i) == neg else x

    @staticmethod
    def _float(v: str | None) -> float | None:
        try:
            return float(v) if v not in (None, "", "x") else None
        except ValueError:
            return None

    @staticmethod
    def _int(v: str | None) -> int | None:
        try:
            return int(v) if v not in (None, "", "x") else None
        except ValueError:
            return None

    # -- LGN: login -------------------------------------------------------------- #
    def _lgn(self, f: list[str], checksum, session: Session, raw_hex: str) -> UnifiedRecord:
        # Field count differs between the doc's 9-field layout and the real 7-field one seen
        # on the wire (no vehicle name / protocol-version literal) — locate IMEI and the GPS
        # block by content instead of trusting either fixed position, so both parse correctly.
        imei = next((t for t in f if _IMEI_RE.match(t)), None) or self._get(f, 3) or session.device_id or f"{self.name}-unknown"
        session.device_id = imei
        lat, lon = self._find_latlon(f)
        firmware = next((t for t in f if _FW_RE.match(t)), None)
        return UnifiedRecord(
            device_id=imei, protocol=self.name, message_type="login",
            position=Position(latitude=lat, longitude=lon),
            io={"firmware_version": firmware, "fields": f, "checksum": checksum},
            raw_hex=raw_hex,
        )

    @staticmethod
    def _find_latlon(f: list[str]) -> tuple[float | None, float | None]:
        """Scan for a (lat, 'N'/'S', lon, 'E'/'W') run rather than trusting a fixed position —
        the login sentence's field count varies between the doc's layout and the real one."""
        for i in range(len(f) - 3):
            if f[i + 1] in ("N", "S") and f[i + 3] in ("E", "W"):
                try:
                    lat, lon = float(f[i]), float(f[i + 2])
                except ValueError:
                    continue
                return (-lat if f[i + 1] == "S" else lat), (-lon if f[i + 3] == "W" else lon)
        return None, None

    # -- NMP/PVT: normal + every alert subtype ------------------------------------ #
    def _nmp(self, f: list[str], checksum, session: Session, raw_hex: str) -> UnifiedRecord:
        # The parenthesized field (OTA response, or a custom packet's payload) may itself
        # contain commas — find it by content and fold it back into one field before indexing.
        # In every AIS-140 layout it is the last field.
        paren_at = next((i for i, tok in enumerate(f) if "(" in tok), None)
        if paren_at is not None:
            f = f[:paren_at] + [",".join(f[paren_at:])]
        return self._nmp_record(f, self._layout(f, NMP_LAYOUTS, NMP_UNMAPPED),
                                checksum, session, raw_hex)

    @staticmethod
    def _layout(f: list[str], layouts: dict[tuple[int, bool], NmpLayout],
                fallback: NmpLayout) -> NmpLayout:
        """Pick a layout by the sentence's shape: field count + parenthesized last field."""
        return layouts.get((len(f), bool(f) and f[-1].startswith("(")), fallback)

    def _nmp_record(self, f: list[str], layout: NmpLayout, checksum, session: Session,
                    raw_hex: str, **head) -> UnifiedRecord:
        """One normal/alert sentence -> UnifiedRecord, for any layout and any header.

        ``head`` overrides the vendor-slot io keys (Zenithra: the slot is a packet-format
        version, and the vendor is the $MMI header itself).
        """
        imei = self._get(f, 6) or session.device_id or f"{self.name}-unknown"
        session.device_id = imei
        ptype = self._get(f, 3) or ""
        alert_id = self._int(self._get(f, 4))
        custom = CUSTOM_PACKETS.get((ptype, alert_id))

        io = {
            # vendor_id (LOCT14A) + firmware from every sentence, plus the raw packet-type code
            # and numeric alert id — the QC coverage layer keys on these + device-info reads them.
            "vendor_id": self._get(f, 1), **head, "firmware_version": self._get(f, 2),
            "packet_type": ptype, "alert_id": alert_id,
            "packet_status": self._get(f, 5), "vehicle_reg_no": self._get(f, 7),
            "pdop": self._float(self._get(f, 19)), "hdop": self._float(self._get(f, 20)),
            "network_operator": self._get(f, 21), "main_power_status": self._get(f, 23),
            "layout": layout.name,
        }
        io.update({key: self._get(f, i) for key, i in layout.text.items()})
        io.update({key: self._float(self._get(f, i)) for key, i in layout.numbers.items()})
        if layout.nmr_at:
            io["nmr"] = [{key: self._get(f, layout.nmr_at + 3 * i + j)
                          for j, key in enumerate(layout.nmr_order)} for i in range(4)]
        if layout.tail_from and len(f) >= layout.tail_from:
            io["tail_raw"] = ",".join(f[layout.tail_from - 1:])
        payload = self._get(f, layout.payload) if layout.payload else None
        if custom:
            io.update(custom.parse(payload or ""))
        elif payload is not None:
            io["ota_update_details"] = payload
        io["checksum"] = checksum

        rec = UnifiedRecord(
            device_id=imei, protocol=self.name,
            message_type=custom.message_type if custom else _NMP_TYPES.get(ptype, ptype or "normal"),
            event_time=self._datetime(self._get(f, 9), self._get(f, 10)),
            gps_valid=self._get(f, 8) == "1",
            position=Position(latitude=self._signed(f, 11, 12, "S"), longitude=self._signed(f, 13, 14, "W"),
                               altitude_m=self._float(self._get(f, 18))),
            speed_kph=self._float(self._get(f, 15)),
            heading_deg=self._float(self._get(f, 16)),
            altitude_m=self._float(self._get(f, 18)),
            satellites=self._int(self._get(f, 17)),
            ignition=self._get(f, 22) == "1",
            battery_v=self._float(self._get(f, 25)),
            external_v=self._float(self._get(f, 24)),
            gsm_signal=self._int(self._get(f, layout.gsm_signal)) if layout.gsm_signal else None,
            odometer_m=self._float(self._get(f, layout.odometer)) if layout.odometer else None,
            io=io,
            raw_hex=raw_hex,
        )
        if custom:
            if custom.alarm:
                rec.alarms.append(custom.alarm)
        elif alert_id and alert_id >= 3:
            rec.alarms.append(_ALERT_NAMES.get(alert_id, f"alert_{alert_id}"))
        return rec

    # -- HLP: health -------------------------------------------------------------- #
    def _hlp(self, f: list[str], checksum, session: Session, raw_hex: str) -> UnifiedRecord:
        imei = self._get(f, 3) or session.device_id or f"{self.name}-unknown"
        session.device_id = imei
        return UnifiedRecord(
            device_id=imei, protocol=self.name, message_type="health",
            battery_pct=self._float(self._get(f, 4)),
            io={"vendor_id": self._get(f, 1), "firmware_version": self._get(f, 2),
                "low_battery_threshold_pct": self._get(f, 5), "memory_pct": self._get(f, 6),
                "update_rate_ignition_on_s": self._get(f, 7), "update_rate_ignition_off_s": self._get(f, 8),
                "digital_io_status": self._get(f, 9), "analog_io_status": self._get(f, 10),
                "checksum": checksum},
            raw_hex=raw_hex,
        )

    # -- EPB: emergency ------------------------------------------------------------ #
    def _epb(self, f: list[str], checksum, session: Session, raw_hex: str) -> UnifiedRecord:
        imei = self._get(f, 2) or session.device_id or f"{self.name}-unknown"
        session.device_id = imei
        ptype = self._get(f, 1) or ""
        rec = UnifiedRecord(
            device_id=imei, protocol=self.name, message_type=_EPB_TYPES.get(ptype, ptype or "emergency"),
            event_time=self._datetime14(self._get(f, 4)),
            gps_valid=self._get(f, 5) == "A",
            position=Position(latitude=self._signed(f, 6, 7, "S"), longitude=self._signed(f, 8, 9, "W"),
                               altitude_m=self._float(self._get(f, 10))),
            altitude_m=self._float(self._get(f, 10)),
            speed_kph=self._float(self._get(f, 11)),
            io={"packet_status": self._get(f, 3), "provider": self._get(f, 12),
                "distance_m": self._float(self._get(f, 13)), "vehicle_reg_no": self._get(f, 14),
                "reply_number": (self._get(f, 15) or "").strip() or None, "checksum": checksum},
            raw_hex=raw_hex,
        )
        rec.alarms.append("emergency" if ptype == "EMR" else "emergency_stop")
        return rec

    # -- date/time -------------------------------------------------------------- #
    @staticmethod
    def _datetime(date: str | None, time: str | None):
        if not date or not time or len(date) != 8 or len(time) < 6:
            return None
        try:
            return datetime(int(date[4:8]), int(date[2:4]), int(date[0:2]),
                             int(time[0:2]), int(time[2:4]), int(time[4:6]), tzinfo=timezone.utc)
        except ValueError:
            return None

    @staticmethod
    def _datetime14(value: str | None):
        if not value or len(value) != 14:
            return None
        try:
            return datetime(int(value[4:8]), int(value[2:4]), int(value[0:2]),
                             int(value[8:10]), int(value[10:12]), int(value[12:14]), tzinfo=timezone.utc)
        except ValueError:
            return None
