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


# -- shared AVL building blocks --------------------------------------------- #
def _gps_block() -> bytes:
    return (int(88.3 * 1e7).to_bytes(4, "big", signed=True)     # longitude
            + int(22.5 * 1e7).to_bytes(4, "big", signed=True)   # latitude
            + (100).to_bytes(2, "big", signed=True)             # altitude
            + (90).to_bytes(2, "big")                           # angle
            + bytes([12])                                       # satellites
            + (60).to_bytes(2, "big"))                          # speed km/h


def _avl_record_16() -> bytes:
    # Codec 16: 2-byte IO ids, 1-byte counts, a generation-type byte after the event id.
    ts = (1719318600000).to_bytes(8, "big")
    head = ts + bytes([1]) + _gps_block()
    event_id = (1).to_bytes(2, "big")
    gen_type = bytes([5])           # "on change"
    n1 = bytes([2]) + b"\x00\xef\x01" + b"\x00\xf0\x01"   # ignition=1, movement=1 (2-byte ids)
    io = event_id + gen_type + bytes([2]) + n1 + bytes([0, 0, 0])
    return head + io


def _avl_record_8e() -> bytes:
    # Codec 8 Extended: 2-byte ids + counts, plus an NX variable-length element.
    ts = (1719318600000).to_bytes(8, "big")
    head = ts + bytes([1]) + _gps_block()
    event_id = (0).to_bytes(2, "big")
    total = (2).to_bytes(2, "big")
    n1 = (1).to_bytes(2, "big") + b"\x00\xef" + b"\x01"          # ignition=1
    n248 = (0).to_bytes(2, "big") * 3
    nx = (1).to_bytes(2, "big") + b"\x01\x2c" + (3).to_bytes(2, "big") + b"\xaa\xbb\xcc"
    io = event_id + total + n1 + n248 + nx
    return head + io


def test_teltonika_codec16():
    c = codec("teltonika")
    sess = Session(id="t", device_id="356307042441013")
    out = c.decode(_teltonika_data(_avl_record_16(), codec_id=0x10), sess)
    rec = out.records[0]
    assert abs(rec.position.latitude - 22.5) < 1e-4
    assert abs(rec.position.longitude - 88.3) < 1e-4
    assert rec.speed_kph == 60
    assert rec.satellites == 12
    assert rec.ignition is True
    assert rec.moving is True
    assert rec.extra["codec_id"] == 0x10
    assert rec.extra["generation_type"] == 5
    assert rec.extra["event_io_id"] == 1
    assert out.ack == (1).to_bytes(4, "big")


def test_teltonika_codec8e_nx():
    c = codec("teltonika")
    sess = Session(id="t", device_id="356307042441013")
    out = c.decode(_teltonika_data(_avl_record_8e(), codec_id=0x8E), sess)
    rec = out.records[0]
    assert rec.ignition is True
    assert rec.io["io_300"] == "aabbcc"     # NX variable-length element preserved as hex
    assert abs(rec.position.latitude - 22.5) < 1e-4


# -- GPRS command / response codecs (12 / 13 / 14) -------------------------- #
def _teltonika_gprs(codec_id: int, ctype: int, body: bytes, prefix: bytes = b"") -> bytes:
    payload = prefix + body
    data = bytes([codec_id, 1, ctype]) + len(payload).to_bytes(4, "big") + payload + bytes([1])
    return (b"\x00\x00\x00\x00" + len(data).to_bytes(4, "big")
            + data + crc16_ibm(data).to_bytes(4, "big"))


def test_teltonika_codec12_command_response():
    c = codec("teltonika")
    sess = Session(id="t", device_id="356307042441013")

    out = c.decode(_teltonika_gprs(0x0C, 0x05, b"getinfo"), sess)
    rec = out.records[0]
    assert rec.message_type == "command"
    assert rec.extra["command_text"] == "getinfo"
    assert out.ack is None     # GPRS frames are not record-count-acked

    out = c.decode(_teltonika_gprs(0x0C, 0x06, b"INFO:OK"), sess)
    rec = out.records[0]
    assert rec.message_type == "response"
    assert rec.extra["response_text"] == "INFO:OK"


def test_teltonika_codec13():
    c = codec("teltonika")
    sess = Session(id="t", device_id="356307042441013")
    ts = (1719318600).to_bytes(4, "big")
    out = c.decode(_teltonika_gprs(0x0D, 0x06, b"DATA", prefix=ts), sess)
    rec = out.records[0]
    assert rec.message_type == "response"
    assert rec.extra["response_text"] == "DATA"
    assert rec.event_time is not None


def test_teltonika_codec14():
    c = codec("teltonika")
    sess = Session(id="t", device_id="356307042441013")
    imei8 = bytes.fromhex("0356307042441013")  # 8-byte addressed IMEI
    out = c.decode(_teltonika_gprs(0x0E, 0x05, b"getgps", prefix=imei8), sess)
    rec = out.records[0]
    assert rec.message_type == "command"
    assert rec.extra["command_text"] == "getgps"
    assert rec.extra["imei_hex"] == "0356307042441013"


def test_teltonika_build_command_roundtrip():
    from proinfo.codecs.teltonika import TeltonikaCodec

    frame = TeltonikaCodec.build_command("setdigout 1")
    out = codec("teltonika").decode(frame, Session(id="t"))
    rec = out.records[0]
    assert rec.message_type == "command"
    assert rec.extra["command_text"] == "setdigout 1"
