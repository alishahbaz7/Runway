"""The MapmyIndia InTouch OTA ``$MMI`` message frame.

Wire form (device <-> OTA cloud, carried as the raw HTTP body):

    $MMI,messageType,deviceSerial,keyCode,dataBody,checkSum*

  * ``messageType`` - the task (see :data:`MESSAGE_TYPES`).
  * ``deviceSerial`` - the device IMEI.
  * ``keyCode``      - which key encrypted ``dataBody`` (1-based). For the key-exchange
                       messages (types 1 & 2) that is one of the 3 *static* keys; for every
                       other message it is one of the 8 *communication* keys (``comMap``).
  * ``dataBody``     - AES-128-ECB/PKCS7 ciphertext of a JSON object, as uppercase hex.
  * ``checkSum``     - CRC-8 (poly 0x07) over the ASCII characters of ``dataBody``.

This module is pure (no I/O): it frames/deframes and encrypts/decrypts one sentence.
The HTTP transport and the key lifecycle live in :mod:`runway.ota.client`.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.crc import crc8
from . import crypto

HEADER = "$MMI"

# messageType -> human label. The task each type performs (protocol doc, section 5.b.2).
MESSAGE_TYPES: dict[int, str] = {
    1: "Keys request",         # device has no comm key -> cloud returns comMap
    2: "Keys update",          # comm key expired -> cloud returns a fresh comMap
    3: "Unit details",         # version/identity info -> cloud returns update flags
    4: "Version status",       # report post-update version/config status
    5: "COTA (config)",        # configuration update -> cloud returns a config URL
    6: "FOTA (firmware)",      # firmware update -> cloud returns a firmware URL
    9: "Error response",       # NACK with error code
}

# The key-exchange messages are signed with a STATIC key (the device has no comm key yet);
# everything else uses a COMMUNICATION key from the comMap. This drives which key set the
# keyCode indexes into, for both the request we send and the response we decrypt.
_STATIC_TYPES = {1, 2}


def key_source(message_type: int) -> str:
    """Which key set ``keyCode`` indexes: ``"static"`` or ``"comm"``."""
    return "static" if message_type in _STATIC_TYPES else "comm"


# Readable request-body templates, so the UI can pre-fill a sensible body per type
# (protocol doc, "Packet Type" table). Values are placeholders the operator edits.
BODY_TEMPLATES: dict[int, dict] = {
    1: {"ReqVersion": "1", "imei": "<imei>"},
    2: {"ReqVersion": "1", "imei": "<imei>"},
    3: {
        "ReqVersion": "1", "imei": "<imei>", "version": "<fw>", "configVersion": "<cfg>",
        "ICCID": "", "hwVer": "", "model": "", "vin": "",
        "Lat": "0.000000", "Long": "0.000000",
        "Server1": "", "Server2": "", "TIMERS": "", "PHONENO": "",
    },
    4: {"ReqVersion": "1", "imei": "<imei>", "version": "<fw>", "configVersion": "<cfg>",
        "versionStatus": 0, "configStatus": 0},
    5: {"ReqVersion": "1", "configVersion": "<cfg>"},
    6: {"reqVersion": "1", "version": "<fw>"},
}


def checksum(data_body: str) -> str:
    """CRC-8 of the ``dataBody`` field, as two uppercase hex digits."""
    return f"{crc8(data_body.encode('ascii')):02X}"


@dataclass
class Sentence:
    """One parsed ``$MMI`` sentence (the encrypted wire form)."""

    message_type: int
    device_serial: str
    key_code: int
    data_body: str          # ciphertext as hex (uppercase on the wire)
    check_sum: str          # as it appeared / as computed, 2 hex digits

    def render(self) -> str:
        return (f"{HEADER},{self.message_type},{self.device_serial},"
                f"{self.key_code},{self.data_body},{self.check_sum}*")

    @property
    def checksum_ok(self) -> bool:
        return self.check_sum.upper().zfill(2) == checksum(self.data_body)

    @property
    def type_label(self) -> str:
        return MESSAGE_TYPES.get(self.message_type, f"type {self.message_type}")


def parse_sentence(text: str) -> Sentence:
    """Parse an encrypted ``$MMI,...,*`` wire sentence.

    The body is ciphertext hex (never contains a comma), so a plain split is safe for
    the wire form this protocol actually transmits.
    """
    s = text.strip()
    if not s.startswith(HEADER + ","):
        raise ValueError(f"not an $MMI sentence: {s[:16]!r}")
    if not s.endswith("*"):
        raise ValueError("$MMI sentence must end with '*'")
    core = s[len(HEADER) + 1:-1]            # drop "$MMI," and trailing "*"
    parts = core.split(",")
    if len(parts) != 5:
        raise ValueError(f"expected 5 fields after $MMI, got {len(parts)}: {parts!r}")
    mtype, serial, kcode, body, chk = parts
    return Sentence(
        message_type=int(mtype),
        device_serial=serial,
        key_code=int(kcode),
        data_body=body,
        check_sum=chk,
    )


def build_sentence(message_type: int, device_serial: str, key_code: int,
                   plaintext: str, key_hex: str) -> Sentence:
    """Encrypt ``plaintext`` under ``key_hex`` and assemble the wire sentence."""
    body = crypto.encrypt_text(key_hex, plaintext)
    return Sentence(
        message_type=message_type,
        device_serial=device_serial,
        key_code=key_code,
        data_body=body,
        check_sum=checksum(body),
    )


def decode_body(sentence: Sentence, key_hex: str) -> str:
    """Decrypt a sentence's ``dataBody`` back to readable plaintext."""
    return crypto.decrypt_text(key_hex, sentence.data_body)
