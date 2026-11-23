"""Locate 140 4G — its login (8 fields) and PVT (50 fields), from the device's login and PVT
sheets: decoded by the AIS-140 codec, every field named in the Packet parser, and the named
definition reaching a copy that already holds the old one.

Packets are built here from the sheets' values with a made-up IMEI — never a real device's.
"""

import json
from pathlib import Path

from runway.codecs import generic_explain
from runway.core import registry
from runway.core.models import Session
from runway.install import definition_hash, seed_definitions

ROOT = Path(__file__).resolve().parent.parent
IMEI = "356938035643809"
LOGIN = f"$LGN,DL1CAB1234,{IMEI},V7.6.0,AIS140,28.550455,N,77.268884,E*10\r\n".encode()
PVT = (f"$PVT,GTRP,V7.6.0,NR,1,L,{IMEI},DL1CAB1234,1,29072026,035636,28.550455,N,77.268884,E,0,300,26,"
       "221,1.05,0.57,IND airtel,1,1,12.8,4.1,0,O,25,404,10,946,E281316,29,946,E281315,15,946,E6E2C2A,"
       "17,946,DFEF434,15,946,9DA4C34,1100,1,138,1.1,0.9,()*85\r\n").encode()


def _grammar():
    return json.loads((ROOT / "data" / "protocols" / "ais140.json").read_text(encoding="utf-8"))


def _fields(raw: bytes) -> list[dict]:
    """The Packet parser's rows for one sentence, as the page gets them."""
    rows = [p for kind, p, _ in generic_explain.walk(raw, registry.get_codec("ais140"), _grammar())
            if kind == "packet"]
    assert len(rows) == 1
    return rows[0]["fields"]


def test_login_is_read_by_position():
    sess = Session(id="t")
    rec = registry.get_codec("ais140").decode(LOGIN, sess).records[0]
    assert rec.message_type == "login"
    assert rec.device_id == IMEI == sess.device_id
    assert rec.io["layout"] == "ais140_no_vendor"
    assert rec.io["firmware_version"] == "V7.6.0"          # the "V" is part of what it sends
    assert rec.io["vehicle_reg_no"] == "DL1CAB1234"
    assert rec.io["protocol_version"] == "AIS140"
    assert (rec.position.latitude, rec.position.longitude) == (28.550455, 77.268884)


def test_a_misplaced_imei_still_names_the_device():
    """The IMEI decides a device's relay route: a login with its fields out of place must not make
    the device someone else (or nobody)."""
    swapped = LOGIN.replace(f"DL1CAB1234,{IMEI}".encode(), f"{IMEI},DL1CAB1234".encode())
    rec = registry.get_codec("ais140").decode(swapped, Session(id="t")).records[0]
    assert rec.device_id == IMEI


def test_a_broken_sentence_makes_no_device_of_its_own():
    """A sentence cut off and glued to the next (the device sent "$PVT,GTRP,V7.06.11,LB$ ," and the next
    packet on one line) has its firmware where the IMEI goes: it stays the connection's device, and a
    first sentence like that is a device not known yet — never one named "V7.06.11"."""
    codec = registry.get_codec("ais140")
    sess = Session(id="t")
    codec.decode(LOGIN, sess)
    broken = b"$PVT,GTRP,V7.06.11,LB$ ,\r\n" + PVT
    recs = []
    buf = broken
    while buf:
        out = codec.decode(buf, sess)
        if not out.consumed:
            break
        recs += out.records
        buf = buf[out.consumed:]
    assert recs and {r.device_id for r in recs} == {IMEI} and sess.device_id == IMEI
    lone = codec.decode(b"$PVT,GTRP,V7.06.11,LB$ ,a,V7.06.11,x*00\r\n", Session(id="u")).records
    assert all(r.device_id == "ais140-unknown" for r in lone)


def test_pvt_decodes_every_field():
    rec = registry.get_codec("ais140").decode(PVT, Session(id="t")).records[0]
    io = rec.io
    assert io["layout"] == "ais140_vb"
    assert (io["vendor_id"], io["firmware_version"], io["packet_type"], io["alert_id"]) == ("GTRP", "V7.6.0", "NR", 1)
    assert rec.event_time.isoformat() == "2026-07-29T03:56:36+00:00"
    assert (rec.speed_kph, rec.heading_deg, rec.satellites, rec.altitude_m) == (0, 300, 26, 221)
    assert (rec.external_v, rec.battery_v, rec.gsm_signal) == (12.8, 4.1, 25)
    assert (io["emergency_status"], io["tamper_alert"]) == ("0", "O")
    assert io["nmr"][3] == {"signal_strength": "15", "lac": "946", "cell_id": "9DA4C34"}
    assert (io["digital_input_status"], io["digital_output_status"], io["frame_number"]) == ("1100", "1", "138")
    assert (io["analog_input_1"], io["analog_input_2"], io["ota_update_details"]) == (1.1, 0.9, "()")


def test_parser_names_every_login_field():
    rows = _fields(LOGIN)
    named = {r["name"]: r["value"] for r in rows if r["group"] == "Locate 140 4G · Login packet"}
    assert named == {"header": "LGN", "vehicle_reg_no": "DL1CAB1234", "imei": IMEI,
                     "firmware_version": "V7.6.0", "protocol_version": "AIS140",
                     "latitude": "28.550455", "latitude_dir": "N",
                     "longitude": "77.268884", "longitude_dir": "E"}


def test_parser_names_every_pvt_field():
    rows = [r for r in _fields(PVT) if r["group"] == "Locate 140 4G · PVT packet"]
    assert len(rows) == 51                                # the header + 50 fields, none positional
    named = {r["name"]: r["value"] for r in rows}
    assert named["time"] == "035636"
    assert named["nmr4_cell_id"] == "9DA4C34"
    assert named["frame_number"] == "138"
    assert named["ota_update_details"] == "()"
    # each row's bytes are where the value sits in the sentence
    for r in rows:
        assert PVT[r["at"]:r["at"] + r["size"]].decode() == r["value"]


def test_parser_names_the_51_field_pvt_the_device_sends():
    """Firmware V7.06.10 sends a field 50 the sheet does not list, before the OTA response."""
    pvt51 = PVT.replace(b",0.9,()*85", b",0.9,29.1,()*85")
    rows = [r for r in _fields(pvt51) if r["group"] == "Locate 140 4G · PVT packet"]
    named = {r["name"]: r["value"] for r in rows}
    assert len(rows) == 52
    assert (named["frame_number"], named["odometer_m"], named["ota_update_details"]) == ("138", "29.1", "()")


def test_the_doc_login_keeps_its_own_names():
    doc = b"$LGN,VENDOR,DL01PC9821,862631032208018,1.6.5,AIS140,28.7589630,N,77.6277844,E*54\r\n"
    rows = _fields(doc)
    assert {r["name"] for r in rows if r["group"] == "Login Packet"} >= {"vendor_id", "device_name", "imei"}
    rec = registry.get_codec("ais140").decode(doc, Session(id="t")).records[0]
    assert (rec.io["layout"], rec.io["vendor_id"], rec.io["firmware_version"]) == ("ais140_v1.9", "VENDOR", "1.6.5")


# ── a release's definitions reach a copy that already has older ones ───────────────────────────────
def _tree(tmp_path):
    shipped = tmp_path / "shipped"
    shipped.mkdir()
    (shipped / "ais140.json").write_text("new grammar\n")
    (shipped / "teltonika.json").write_text("teltonika\n")
    data = tmp_path / "data"
    return shipped, data


def test_first_start_copies_every_definition(tmp_path):
    shipped, data = _tree(tmp_path)
    assert seed_definitions(shipped, data, {}) == ["ais140.json", "teltonika.json"]
    assert (data / "ais140.json").read_text() == "new grammar\n"


def test_an_untouched_copy_is_brought_up_to_date(tmp_path):
    shipped, data = _tree(tmp_path)
    data.mkdir()
    (data / "ais140.json").write_bytes(b"old grammar\r\n")       # a release's copy, written with CRLF
    (data / "teltonika.json").write_text("teltonika\n")
    history = {"ais140.json": [definition_hash(b"old grammar\n"), definition_hash(b"new grammar\n")]}
    assert seed_definitions(shipped, data, history) == ["ais140.json"]
    assert (data / "ais140.json").read_text() == "new grammar\n"


def test_the_persons_own_edit_is_kept(tmp_path):
    shipped, data = _tree(tmp_path)
    data.mkdir()
    (data / "ais140.json").write_text("my own field names\n")
    history = {"ais140.json": [definition_hash(b"old grammar\n")]}
    assert seed_definitions(shipped, data, history) == []
    assert (data / "ais140.json").read_text() == "my own field names\n"


def test_a_removed_definition_stays_removed(tmp_path):
    shipped, data = _tree(tmp_path)
    data.mkdir()
    (data / "ais140.json").write_text("new grammar\n")           # teltonika.json deleted by the person
    assert seed_definitions(shipped, data, {}) == []
    assert not (data / "teltonika.json").exists()


def test_a_run_from_source_seeds_nothing(tmp_path):
    shipped, _ = _tree(tmp_path)
    assert seed_definitions(shipped, shipped, {}) == []
