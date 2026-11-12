"""Checksum helpers shared by binary codecs.

Kept dependency-free: tables are built at import time from the polynomial.
"""

from __future__ import annotations


def _reflected_table(poly_reversed: int) -> list[int]:
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = (crc >> 1) ^ poly_reversed if (crc & 1) else (crc >> 1)
        table.append(crc)
    return table


# CRC-16/X-25 (a.k.a. CRC-ITU) - used by GT06/Concox family.
# poly 0x1021, init 0xFFFF, refin/refout true, xorout 0xFFFF.
_CRC_ITU_TABLE = _reflected_table(0x8408)


def crc_itu(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc = (crc >> 8) ^ _CRC_ITU_TABLE[(crc ^ b) & 0xFF]
    return (~crc) & 0xFFFF


# CRC-16/IBM (a.k.a. CRC-16/ARC) - used by Teltonika AVL packets.
# poly 0xA001 (reversed), init 0x0000.
def crc16_ibm(data: bytes) -> int:
    crc = 0x0000
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if (crc & 1) else (crc >> 1)
    return crc & 0xFFFF


# CRC-8/SMBUS (the canonical "CRC8") - used by the MapmyIndia InTouch OTA frame.
# poly 0x07, init 0x00, no reflection, no final XOR. The OTA checksum is this CRC
# over the ASCII characters of the dataBody field (the encrypted hex string), rendered
# as two uppercase hex digits. Verified against two server-accepted packets:
#   b"BE88...473E" -> 0x49   and   b"5320...AED7" -> 0x47
# (confirm against more captures if the platform ever rejects a frame on checksum).
_CRC8_TABLE = []
for _i in range(256):
    _c = _i
    for _ in range(8):
        _c = ((_c << 1) ^ 0x07) & 0xFF if (_c & 0x80) else (_c << 1) & 0xFF
    _CRC8_TABLE.append(_c)


def crc8(data: bytes) -> int:
    crc = 0x00
    for b in data:
        crc = _CRC8_TABLE[(crc ^ b) & 0xFF]
    return crc & 0xFF
