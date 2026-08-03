"""Codec unit tests.

GT06/Teltonika packets are built with the (independently CRC-check-vector-verified)
crc helpers, so the tests exercise framing + field parsing without depending on a
hand-copied capture. The NMEA test uses a real fixture file.
"""

from __future__ import annotations

from pathlib import Path

from runway.core import registry
from runway.core.crc import crc16_ibm, crc_itu
from runway.core.models import Session

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
    from runway.codecs.teltonika import TeltonikaCodec

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


def test_ais140_hel_header_and_pvt_io_fields():
    """Real Locate 140B firmware 2.2.5 sends the health packet as $HEL (not the doc's $HLP);
    and QC coverage/device-info need alert_id + vendor_id + firmware on every PVT."""
    c = codec("ais140")
    sess = Session(id="t")

    # $HEL health header (from the real QC-report sample) must parse as health, not "unknown",
    # and carry vendor_id (LOCT14A) + firmware for the device-details panel.
    hel = b"$HEL,LOCT14A,2.2.5,861329080405658,68,30,0,10,600,0011,00*"
    out = c.decode(hel, sess)
    assert out.ack == b"+##Received OK"                     # $HEL is acknowledged like $HLP
    rec = out.records[0]
    assert rec.message_type == "health"                     # was "unknown" before the fix
    assert rec.device_id == "861329080405658"
    assert rec.io["vendor_id"] == "LOCT14A"
    assert rec.io["firmware_version"] == "2.2.5"

    # PVT now exposes the numeric alert id + packet-type code + vendor/firmware in io.
    pvt = (b"$PVT,LOCT14A,2.2.5,BD,3,L,861329080405658,0000000000,1,13072026,112929,"
           b"28.550419,N,77.268768,E,000.0,246.88,08,216.250,2.37,2.17,airtel,1,0,04.5,4.0,0,C,"
           b"28,404,10,092,9305,9304,092,21,d410,092,15,96e3,06f,14,d40f,092,14,0011,00,000127,8A*\r\n")
    rec = c.decode(pvt, sess).records[0]
    assert rec.io["alert_id"] == 3                          # Disconnect from main battery
    assert rec.io["packet_type"] == "BD"
    assert rec.io["vendor_id"] == "LOCT14A"
    assert rec.io["firmware_version"] == "2.2.5"
    assert rec.alarms == ["disconnect_main_battery"]        # alert_id 3


# --------------------------------------------------------------------------- #
# Zenithra Ax1 sCAN — the AIS-140 base packet plus an inline CAN field.
# Both sample sentences in the fixture are real captures from IMEI 868221073948609
# (one with CAN data, one without), so these tests pin the confirmed 38-field layout.
# --------------------------------------------------------------------------- #
def test_zenithra_detect():
    assert codec("zenithra").detect(b"$MMI,V2.2.0,5.1.29C,NR") == 0.95
    assert codec("ais140").detect(b"$MMI,V2.2.0,5.1.29C,NR") == 0.0     # not the reverse


def test_zenithra_detect_wins_on_its_login_packet():
    """Auto-detect picks a codec ONCE, from the first sample — and this device logs in with a
    plain AIS-140 $LGN before it ever sends $MMI. Its login must therefore outscore ais140
    (which reads $LGN at 0.9), or the whole session decodes $MMI as "unknown". The sCAN
    protocol-version literal is what separates it from a genuine Locate 140B login."""
    zen, ais = codec("zenithra"), codec("ais140")
    real = b"$LGN,DL1CAB1234,868221073948609,5.1.29C,SCAN,28.550474,N,77.268893,E*5B\r\n"
    assert zen.detect(real) == 0.95 > ais.detect(real) == 0.9
    # a real AIS-140 login is left alone (its protocol-version literal is AIS140, not SCAN)
    locate = b"$LGN,VENDOR,DL01PC9821,862631032208018,1.6.5,AIS140,28.7589630,N,77.6277844,E*54"
    assert zen.detect(locate) == 0.0
    assert ais.detect(locate) == 0.9
    # "SCAN" only counts as a whole field, not as a substring of some other value
    assert zen.detect(b"$LGN,SCANIA1234,868221073948609,5.1.29C,AIS140,28.5,N,77.2,E*11") == 0.0


def test_zenithra_auto_detect_end_to_end_from_a_real_session():
    """The regression the live bench hit: login-then-data over one connection, codec "auto".
    Every sentence — the AIS-140 login and both $MMI packets — must decode under one codec."""
    from runway.core.pipeline import Pipeline

    data = (FIX / "zenithra_sample.log").read_bytes()
    picked = Pipeline(source=None, codec="auto", sinks=[])._pick_codec(data)
    assert picked.name == "zenithra"

    sess = Session(id="t")
    types = []
    while data:
        out = picked.decode(data, sess)
        assert out.consumed > 0
        types += [r.message_type for r in out.records]
        data = data[out.consumed:]
    assert types == ["login", "normal", "normal"]      # no "unknown" anywhere
    assert sess.device_id == "868221073948609"


def test_zenithra_normal_with_and_without_can():
    c = codec("zenithra")
    sess = Session(id="t")
    data = (FIX / "zenithra_sample.log").read_bytes()

    # the session opens with the device's real AIS-140 login (see the auto-detect test)
    login = c.decode(data, sess)
    assert login.records[0].message_type == "login"
    assert login.records[0].io["firmware_version"] == "5.1.29C"
    data = data[login.consumed:]

    plain = c.decode(data, sess)
    assert plain.ack == b"+##Received OK"
    rec = plain.records[0]
    assert rec.protocol == "zenithra"
    assert rec.device_id == "868221073948609" == sess.device_id
    assert rec.message_type == "normal"                      # packet_type NR
    assert rec.event_time.isoformat() == "2026-08-03T13:26:21+00:00"
    assert abs(rec.position.latitude - 28.550474) < 1e-6
    assert abs(rec.position.longitude - 77.268893) < 1e-6
    assert rec.speed_kph == 0.0
    assert rec.satellites == 29
    assert rec.altitude_m == 225
    assert rec.gps_valid is True
    assert rec.ignition is True
    assert rec.external_v == 13.5
    assert rec.battery_v == 4.1
    assert rec.gsm_signal == 31                 # field 27 — one slot earlier than AIS-140's 28
    assert rec.odometer_m is None               # this layout doesn't carry an odometer
    assert rec.alarms == []                     # alert_id 1 = routine location update
    io = rec.io
    assert io["packet_version"] == "V2.2.0"
    assert io["firmware_version"] == "5.1.29C"
    assert (io["packet_type"], io["alert_id"], io["packet_status"]) == ("NR", 1, "L")
    assert io["vehicle_reg_no"] == "DL1CAB1234"
    assert io["network_operator"] == "IND airtel"
    assert (io["mcc"], io["mnc"], io["lac"], io["cell_id"]) == ("404", "10", "946", "E281316")
    assert io["tamper_alert"] == "O"
    assert (io["analog_input_1"], io["analog_input_2"]) == (0.7, 0.0)
    assert io["ota_update_details"] == "()"
    assert io["frame_number"] == "000123"
    assert io["can_frame_count"] == 0 and "can" not in io    # CAN slot present but empty
    assert io["checksum"] == "A4"                            # carried through, not verified
    data = data[plain.consumed:]

    withcan = c.decode(data, sess)
    rec = withcan.records[0]
    assert withcan.consumed == len(data)                     # whole sentence, CRLF included
    assert rec.io["can_frame_count"] == 23
    assert rec.io["can"][0] == {"id": "000000E1", "id_int": 0xE1, "extended": False,
                                "data": "000040A0010001E2", "dlc": 8}
    assert rec.io["can"][-1] == {"id": "000005B9", "id_int": 0x5B9, "extended": False,
                                 "data": "0000000058590000", "dlc": 8}
    # the CAN payload must not shift the tail — the fields after it still map
    assert rec.io["frame_number"] == "000007"
    assert rec.io["ota_update_details"] == "()"
    assert (rec.io["digital_input_status"], rec.io["digital_output_status"]) == ("0000", "00")
    assert rec.io["cell_id"] == "E28130C"


def test_zenithra_alert_subtype_and_ota_field_with_commas():
    """An alert rides the same layout (packet_type + alert_id), and the OTA field — which is
    NOT the last field here — may itself contain commas without shifting frame_number."""
    c = codec("zenithra")
    s = (b"$MMI,V2.2.0,5.1.29C,BD,3,L,868221073948609,DL1CAB1234,1,03082026,132621,28.550474,N,"
         b"77.268893,E,0.0,0,29,225,0.98,0.51,IND airtel,1,0,0.0,4.1,C,31,404,10,946,E281316,"
         b"0000,00,0.7,0.0,1FFFFFFF:0011223344556677,(192.168.1.1,SET:APN,iot),000124*7F\r\n")
    rec = c.decode(s, Session(id="t")).records[0]
    assert rec.message_type == "battery_disconnect"
    assert rec.io["alert_id"] == 3
    assert rec.alarms == ["disconnect_main_battery"]
    assert rec.io["main_power_status"] == "0"
    assert rec.io["tamper_alert"] == "C"
    assert rec.io["ota_update_details"] == "(192.168.1.1,SET:APN,iot)"
    assert rec.io["frame_number"] == "000124"          # still last after the OTA fold
    assert rec.io["can"][0]["extended"] is True        # 29-bit id (> 0x7FF)
    assert rec.io["can"][0]["dlc"] == 8


def test_zenithra_base_ais140_headers_still_parse():
    """A base-protocol sentence on the same connection (login/health) is handed to the AIS-140
    builders but tagged with this protocol, so QC/the UI keep one identity for the device."""
    c = codec("zenithra")
    sess = Session(id="t")
    login = c.decode(b"$LGN,0000,868221073948609,5.1.29,28.550474,N,77.268893,E*", sess)
    rec = login.records[0]
    assert rec.message_type == "login"
    assert rec.protocol == "zenithra"
    assert rec.device_id == "868221073948609" == sess.device_id
    assert login.ack == b"+##Received OK"

    health = c.decode(b"$HEL,,5.1.29,868221073948609,100,33,8,20,1,0000,11*", sess)
    assert health.records[0].message_type == "health"
    assert health.records[0].battery_pct == 100


def test_zenithra_long_can_dump_exceeds_ais140_sentence_cap():
    """A busy bus can push the sentence past the AIS-140 resync cap, so this codec raises
    max_sentence: a long sentence that has only partly arrived must keep waiting for the
    rest instead of being dropped as garbage the way the 2 KB base limit would."""
    c = codec("zenithra")
    assert c.max_sentence > codec("ais140").max_sentence
    partial = b"$MMI,V2.2.0,5.1.29C,NR,1,L,868221073948609,DL1CAB1234," + b"0" * 3000
    assert c.decode(partial, Session(id="t")).consumed == 0            # need full sentence
    assert codec("ais140").decode(partial, Session(id="t")).consumed > 0   # base would resync
