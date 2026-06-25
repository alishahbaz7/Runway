"""Codec unit tests.

GT06/Teltonika packets are built with the (independently CRC-check-vector-verified)
crc helpers, so the tests exercise framing + field parsing without depending on a
hand-copied capture. The NMEA test uses a real fixture file.
"""

from __future__ import annotations

from pathlib import Path

from proinfo.core import registry
from proinfo.core.crc import crc16_ibm, crc_itu
from proinfo.core.models import Session

FIX = Path(__file__).parent / "fixtures"


def codec(name):
    return registry.get_codec(name)


# --------------------------------------------------------------------------- #
# NMEA
# --------------------------------------------------------------------------- #
def test_nmea_detect():
    assert codec("nmea").detect(b"$GPRMC,123519,A,4807.038,N") > 0.5
    assert codec("nmea").detect(b"\x78\x78\x0d\x01") == 0.0


def test_nmea_rmc_and_gga():
    data = (FIX / "nmea_sample.log").read_bytes()
    out = codec("nmea").decode(data, Session(id="t"))
    assert out.consumed == len(data)
    rmc = next(r for r in out.records if r.message_type == "RMC")
    assert abs(rmc.position.latitude - 48.1173) < 1e-3
    assert abs(rmc.position.longitude - 11.51667) < 1e-3
    assert abs(rmc.speed_kph - 22.4 * 1.852) < 0.1
    assert rmc.gps_valid is True
    gga = next(r for r in out.records if r.message_type == "GGA")
    assert gga.satellites == 8


# --------------------------------------------------------------------------- #
# Generic hex
# --------------------------------------------------------------------------- #
def test_generic_hex_passthrough():
    out = codec("generic_hex").decode(b"\x01\x02\x03", Session(id="t"))
    assert out.consumed == 3
    assert out.records[0].raw_hex == "010203"


# --------------------------------------------------------------------------- #
# GT06 / Concox
# --------------------------------------------------------------------------- #
def _gt06(proto: int, info: bytes, serial: int = 1) -> bytes:
    length = 1 + len(info) + 2 + 2
    body = bytes([length, proto]) + info + serial.to_bytes(2, "big")
    crc = crc_itu(body).to_bytes(2, "big")
    return b"\x78\x78" + body + crc + b"\x0d\x0a"


def test_gt06_detect():
    assert codec("gt06").detect(b"\x78\x78\x0d\x01") == 0.9
    assert codec("gt06").detect(b"$GPRMC") == 0.0


def test_gt06_login_then_location():
    c = codec("gt06")
    sess = Session(id="t")

    # Login: terminal id -> IMEI, plus a server ACK.
    login = _gt06(0x01, bytes.fromhex("0351091270110476"))
    out = c.decode(login, sess)
    assert out.consumed == len(login)
    assert out.records[0].device_id == "351091270110476"
    assert sess.device_id == "351091270110476"
    assert out.ack and out.ack[:2] == b"\x78\x78" and out.ack[-2:] == b"\x0d\x0a"

    # Location (0x12): N 22.5, E 88.3, 60 km/h, course 90, valid.
    flags = 90 | 0x1000 | 0x0400          # course | positioned | north
    info = (bytes([24, 6, 25, 12, 30, 0])
            + bytes([0xCA])               # 10 satellites (low nibble)
            + round(22.5 * 1800000).to_bytes(4, "big")
            + round(88.3 * 1800000).to_bytes(4, "big")
            + bytes([60])
            + flags.to_bytes(2, "big"))
    out = c.decode(_gt06(0x12, info), sess)
    rec = out.records[0]
    assert rec.device_id == "351091270110476"     # inherited from login session
    assert abs(rec.position.latitude - 22.5) < 1e-4
    assert abs(rec.position.longitude - 88.3) < 1e-4
    assert rec.speed_kph == 60
    assert rec.heading_deg == 90
    assert rec.satellites == 10
    assert rec.gps_valid is True


def test_gt06_resync_on_garbage():
    c = codec("gt06")
    good = _gt06(0x01, bytes.fromhex("0351091270110476"))
    out = c.decode(b"\xffgarbage" + good, Session(id="t"))
    assert 0 < out.consumed <= len(b"\xffgarbage")   # drops junk to the next 0x7878


# --------------------------------------------------------------------------- #
# Teltonika Codec 8
# --------------------------------------------------------------------------- #
def _teltonika_data(record: bytes, count: int = 1, codec_id: int = 0x08) -> bytes:
    data = bytes([codec_id, count]) + record + bytes([count])
    return (b"\x00\x00\x00\x00" + len(data).to_bytes(4, "big")
            + data + crc16_ibm(data).to_bytes(4, "big"))


def _avl_record() -> bytes:
    ts = (1719318600000).to_bytes(8, "big")
    gps = (int(88.3 * 1e7).to_bytes(4, "big", signed=True)
           + int(22.5 * 1e7).to_bytes(4, "big", signed=True)
           + (100).to_bytes(2, "big", signed=True)   # altitude
           + (90).to_bytes(2, "big")                 # angle
           + bytes([12])                             # satellites
           + (60).to_bytes(2, "big"))                # speed km/h
    io = bytes([0, 2, 2, 239, 1, 240, 1, 0, 0, 0])   # ignition=1, movement=1
    return ts + bytes([1]) + gps + io


def test_teltonika_detect():
    assert codec("teltonika").detect(b"\x00\x00\x00\x00\x00\x00\x00\x10") > 0.8


def test_teltonika_imei_then_data():
    c = codec("teltonika")
    sess = Session(id="t")

    imei = b"356307042441013"
    out = c.decode(len(imei).to_bytes(2, "big") + imei, sess)
    assert out.ack == b"\x01"
    assert sess.device_id == "356307042441013"

    out = c.decode(_teltonika_data(_avl_record()), sess)
    rec = out.records[0]
    assert rec.device_id == "356307042441013"
    assert abs(rec.position.latitude - 22.5) < 1e-4
    assert abs(rec.position.longitude - 88.3) < 1e-4
    assert rec.speed_kph == 60
    assert rec.heading_deg == 90
    assert rec.satellites == 12
    assert rec.ignition is True
    assert rec.moving is True
    assert out.ack == (1).to_bytes(4, "big")
