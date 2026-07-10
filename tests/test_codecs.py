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


# --------------------------------------------------------------------------- #
# AIS-140 (Locate 140B)
# --------------------------------------------------------------------------- #
def test_ais140_detect():
    assert codec("ais140").detect(b"$LGN,VENDOR,...") == 0.9
    assert codec("ais140").detect(b"\x78\x78\x0d\x01") == 0.0


def test_ais140_login_normal_health_emergency():
    c = codec("ais140")
    sess = Session(id="t")
    data = (FIX / "ais140_sample.log").read_bytes()

    login = c.decode(data, sess)
    assert login.ack == b"+##Received OK"
    rec = login.records[0]
    assert rec.message_type == "login"
    assert rec.device_id == "862631032208018" == sess.device_id
    assert abs(rec.position.latitude - 28.7589630) < 1e-6
    assert abs(rec.position.longitude - 77.6277844) < 1e-6
    data = data[login.consumed:]

    normal = c.decode(data, sess)
    assert normal.ack == b"+##Received OK"
    rec = normal.records[0]
    assert rec.device_id == "862631032208018"     # inherited from the login session
    assert rec.message_type == "normal"
    assert rec.speed_kph == 22.5
    assert rec.heading_deg == 320.55
    assert rec.satellites == 4
    assert rec.gps_valid is True
    assert rec.ignition is True
    assert rec.alarms == ["connect_main_battery"]          # alert_id 6
    assert rec.io["ota_update_details"] == "(192.168.1.1,SET:APN,iot)"
    assert rec.event_time.isoformat() == "2018-03-24T06:01:22+00:00"
    data = data[normal.consumed:]

    health = c.decode(data, sess)
    assert health.ack == b"+##Received OK"
    rec = health.records[0]
    assert rec.message_type == "health"
    assert rec.battery_pct == 99
    assert rec.io["digital_io_status"] == "001100"
    data = data[health.consumed:]

    emergency = c.decode(data, sess)
    assert emergency.ack is None                          # no ack required for EPB
    rec = emergency.records[0]
    assert rec.message_type == "emergency"
    assert rec.alarms == ["emergency"]
    assert abs(rec.position.latitude - 30.8825130) < 1e-6
    assert rec.io["vehicle_reg_no"] == "PB10CE0911"


def test_ais140_resync_on_garbage():
    c = codec("ais140")
    good = b"$LGN,VENDOR,DL01PC9821,862631032208018,1.6.5,AIS140,28.7589630,N,77.6277844,E*54"
    out = c.decode(b"\x00\x00junk" + good, Session(id="t"))
    assert 0 < out.consumed <= len(b"\x00\x00junk")        # drops junk to the next '$'


# -- real Locate 140B ("LOCT14A") wire traffic — headers/fields confirmed against a live
# capture, which diverges from the v1.9 doc: LGN has only 7 fields (IMEI one slot earlier),
# and PVT/HLM replace NMP/HLP with a different (but internally consistent) tail layout.
def test_ais140_real_login_short_layout():
    c = codec("ais140")
    sess = Session(id="t")
    # No vehicle-name / protocol-version literal, unlike the doc's 9-field LGN.
    login = b"$LGN,0000,860103062389003,01.3.0,10.07757430,N,76.33675600,E*"
    out = c.decode(login, sess)
    rec = out.records[0]
    assert rec.device_id == "860103062389003" == sess.device_id     # IMEI found by content, not position
    assert abs(rec.position.latitude - 10.07757430) < 1e-6
    assert abs(rec.position.longitude - 76.33675600) < 1e-6
    assert rec.io["firmware_version"] == "01.3.0"


def test_ais140_real_pvt_and_hlm():
    c = codec("ais140")
    sess = Session(id="t", device_id="868329087318766")
    pvt = (b"$PVT,LOCT14A,2.2.5,NR,2,H,868329087318766,0000000000,1,03072026,152735,"
           b"28.550457,N,77.268852,E,000.0,132.84,33,221.064,0.83,0.48,airte ,1,1,13.7,4.1,0,C,"
           b"31,404,10,092,9305,9304,092,18,96e3,06f,13,d410,092,11,0,0,31,0010,00,027631,BC*\r\n")
    out = c.decode(pvt, sess)
    rec = out.records[0]
    assert rec.message_type == "normal"                    # from packet_type NR, same as NMP
    assert abs(rec.position.latitude - 28.550457) < 1e-6
    assert abs(rec.position.longitude - 77.268852) < 1e-6
    assert rec.satellites == 33
    assert rec.altitude_m == 221.064
    assert rec.ignition is True
    assert rec.gsm_signal == 31
    assert rec.odometer_m == 27631.0                        # NOT frame_number — confirmed by field count (48)
    assert rec.io["nmr"][0] == {"cell_id": "9304", "lac": "092", "signal_strength": "18"}
    assert rec.io["trailer_checksum"] == "BC"               # this firmware's checksum, inline — nothing after '*'
    assert out.ack == b"+##Received OK"

    hlm = b"$HLM,,01.3.0,860103062389003,100,33,8,20,1,0000,11*"
    out2 = c.decode(hlm, sess)
    rec2 = out2.records[0]
    assert rec2.message_type == "health"                    # HLM maps onto the same builder as HLP
    assert rec2.battery_pct == 100
    assert out2.ack == b"+##Received OK"
