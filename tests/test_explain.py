"""Tests for the Teltonika field-by-field explainer + the /api/parse endpoint.

Packets are built with the real crc16_ibm helper (same approach as test_codecs), so the
explainer is exercised against valid framing, names and CRCs end-to-end.
"""

from __future__ import annotations

from runway.codecs.teltonika_explain import explain
from runway.core.crc import crc16_ibm


def _data(codec_id: int, count: int, record: bytes) -> bytes:
    data = bytes([codec_id, count]) + record + bytes([count])
    return (b"\x00\x00\x00\x00" + len(data).to_bytes(4, "big")
            + data + crc16_ibm(data).to_bytes(4, "big"))


def _rec8() -> bytes:
    ts = (1719318600000).to_bytes(8, "big")
    gps = (int(88.3 * 1e7).to_bytes(4, "big", signed=True)
           + int(22.5 * 1e7).to_bytes(4, "big", signed=True)
           + (100).to_bytes(2, "big", signed=True)
           + (90).to_bytes(2, "big") + bytes([12]) + (60).to_bytes(2, "big"))
    io = bytes([0, 1, 1, 239, 1, 0, 0, 0])     # event 0, total 1, one 1-byte IO: 239=1
    return ts + bytes([1]) + gps + io


def test_explain_codec8_resolves_names():
    out = explain(_data(0x08, 1, _rec8()), "tcp")
    assert out["ok"] and len(out["packets"]) == 1
    p = out["packets"][0]
    assert p["codec_name"] == "Codec 8"
    assert p["crc_ok"] is True
    names = [f["name"] for f in p["fields"]]
    assert "Preamble" in names and "Longitude" in names
    assert any("Ignition (AVL 239)" in n for n in names)   # the headline feature
    rec = out["records"][0]
    assert abs(rec["latitude"] - 22.5) < 1e-4
    assert abs(rec["longitude"] - 88.3) < 1e-4
    assert rec["speed_kph"] == 60


def test_explain_field_shape():
    p = explain(_data(0x08, 1, _rec8()), "tcp")["packets"][0]
    lon = next(f for f in p["fields"] if f["name"] == "Longitude")
    assert lon["size"] == 4
    assert lon["value"].startswith("88.3")
    assert lon["unit"] == "deg"
    assert len(lon["hex"]) == 8                            # 4 bytes -> 8 nibbles


def test_explain_multiple_packets_accumulate():
    imei = b"356307042441013"
    hs = len(imei).to_bytes(2, "big") + imei
    buf = hs + _data(0x08, 1, _rec8()) + _data(0x08, 1, _rec8())
    out = explain(buf, "tcp")
    assert len(out["packets"]) == 3
    assert out["packets"][0]["kind"] == "imei"
    assert len(out["records"]) == 2                        # two AVL packets -> two records


def test_explain_crc_mismatch_flagged():
    pkt = bytearray(_data(0x08, 1, _rec8()))
    pkt[-1] ^= 0xFF                                        # corrupt the CRC
    p = explain(bytes(pkt), "tcp")["packets"][0]
    assert p["crc_ok"] is False
    crc = next(f for f in p["fields"] if f["name"] == "CRC-16")
    assert "MISMATCH" in crc["value"]


def test_explain_gprs12_command():
    body = b"getinfo"
    data = bytes([0x0C, 1, 0x05]) + len(body).to_bytes(4, "big") + body + bytes([1])
    pkt = (b"\x00\x00\x00\x00" + len(data).to_bytes(4, "big")
           + data + crc16_ibm(data).to_bytes(4, "big"))
    p = explain(pkt, "tcp")["packets"][0]
    assert p["codec_name"].startswith("Codec 12")
    vals = {f["name"]: f["value"] for f in p["fields"]}
    assert vals["Command/Response"] == "getinfo"


def test_explain_io_900_930_shown_as_raw_hex():
    # Codec 8E (2-byte IO ids). One 2-byte IO with id 905 (in the raw range) + ignition.
    ts = (1719318600000).to_bytes(8, "big")
    gps = (int(88.3 * 1e7).to_bytes(4, "big", signed=True)
           + int(22.5 * 1e7).to_bytes(4, "big", signed=True)
           + (100).to_bytes(2, "big", signed=True) + (90).to_bytes(2, "big")
           + bytes([12]) + (60).to_bytes(2, "big"))
    event = (0).to_bytes(2, "big") + (2).to_bytes(2, "big")          # event id, total=2
    n1 = (1).to_bytes(2, "big") + b"\x00\xef" + b"\x01"              # 239 = 1 (decoded int)
    n2 = (1).to_bytes(2, "big") + (905).to_bytes(2, "big") + b"\x12\x34"   # 905 -> raw hex
    # N4=0, N8=0, NX=0 (Codec 8E has the trailing variable-length group count too)
    record = ts + bytes([1]) + gps + event + n1 + n2 + (0).to_bytes(2, "big") * 3
    data = bytes([0x8E, 1]) + record + bytes([1])
    pkt = (b"\x00\x00\x00\x00" + len(data).to_bytes(4, "big")
           + data + crc16_ibm(data).to_bytes(4, "big"))

    fields = {f["name"]: f for f in explain(pkt, "tcp")["packets"][0]["fields"]}
    assert fields["Ignition (AVL 239)"]["value"] == "1"             # normal IO still decoded
    assert fields["AVL ID 905 (AVL 905)"]["value"] == "0x1234"      # raw range -> hex
    assert fields["AVL ID 905 (AVL 905)"]["bit"] == ""             # no bit view for raw


def test_explain_udp_frame():
    inner = bytes([0x08, 1]) + _rec8() + bytes([1])
    imei = b"356307042441013"
    body = b"\x00\x01" + b"\x01" + b"\x00" + len(imei).to_bytes(2, "big") + imei + inner
    frame = len(body).to_bytes(2, "big") + body
    out = explain(frame, "udp")
    assert out["ok"]
    assert out["packets"][0]["kind"] == "udp_data"
    assert out["records"][0]["speed_kph"] == 60


def test_explain_field_offsets_map_to_bytes():
    # Every field's (at, size) must index exactly its hex in the packet (drives the
    # click-to-highlight feature). Validates the whole-packet byte map at once.
    pkt = _data(0x08, 1, _rec8())
    out = explain(pkt, "tcp")
    p = out["packets"][0]
    assert p["byte_offset"] == 0
    full = pkt.hex()
    for f in p["fields"]:
        assert full[f["at"] * 2:(f["at"] + f["size"]) * 2] == f["hex"], f["name"]

    # A second concatenated packet starts at the byte length of the first.
    out2 = explain(pkt + pkt, "tcp")
    assert out2["packets"][1]["byte_offset"] == len(pkt)


def test_explain_garbage_decodes_nothing():
    # pure garbage with no preamble -> nothing decoded, but no hard error (graceful)
    out = explain(b"\xde\xad\xbe\xef\x11\x22", "tcp")
    assert out["packets"] == []
    assert out["ok"] is True


def test_explain_skips_corrupt_and_continues():
    # good packet + garbage + good packet -> both good ones decoded, garbage skipped
    good = _data(0x08, 1, _rec8())
    buf = good + b"\xaa\xbb\xcc\xdd\xee" + good
    out = explain(buf, "tcp")
    assert len(out["packets"]) == 2          # the bad bytes didn't kill the decode
    assert out["skipped"] >= 1
    assert out["ok"] is True


async def test_parse_endpoint():
    from httpx import ASGITransport, AsyncClient

    from runway.api.app import app

    payload = {"hex": _data(0x08, 1, _rec8()).hex(), "transport": "tcp"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/parse", json=payload)
        bad = await c.post("/api/parse", json={"hex": "xyz", "transport": "tcp"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] and data["records"][0]["speed_kph"] == 60
    assert bad.status_code == 400                          # invalid hex rejected


# --------------------------------------------------------------------------- #
# Protocol-agnostic parsing: the Parser page is not a Teltonika tool.
#
# The endpoint decides two things from the data itself — is this a hex dump or the device's
# own text, and which registered codec reads it — and the field breakdown comes from the
# codec (its own explainer if it has one) plus the declarative grammar in the protocol
# database. These tests pin that contract for a text protocol, a binary one, and a protocol
# with no grammar published, so no caller can quietly go back to assuming one protocol.
# --------------------------------------------------------------------------- #
ZENITHRA_CAN = (
    "$MMI,V2.2.0,5.1.29C,NR,1,L,868221073948609,DL1CAB1234,1,03082026,130441,28.550492,N,"
    "77.268890,E,0.0,0,30,223,0.91,0.49,IND airtel,1,1,13.5,4.1,O,31,404,10,946,E28130C,"
    "0000,00,0.7,0.0,000000E1:000040A0010001E2;00000120:0002FEA200000000,(),000007*46"
)


async def _post(path: str, body: dict):
    from httpx import ASGITransport, AsyncClient

    from runway.api.app import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.post(path, json=body)


def _groups(packet: dict) -> dict:
    out: dict = {}
    for f in packet["fields"]:
        out[f["group"]] = out.get(f["group"], 0) + 1
    return out


async def test_parse_ascii_text_is_not_shredded_into_hex():
    """The regression that made the Parser useless for every ASCII protocol: the input was
    filtered down to its hex-looking characters, so a $-sentence decoded to nothing."""
    r = await _post("/api/parse", {"hex": ZENITHRA_CAN})
    assert r.status_code == 200
    d = r.json()
    assert d["protocol"] == "zenithra"            # detected from the bytes, not configured
    assert len(d["packets"]) == 1 and d["skipped"] == 0 and d["error"] is None

    pkt = d["packets"][0]
    named = {f["name"]: f for f in pkt["fields"]}
    assert named["imei"]["value"] == "868221073948609"
    assert named["speed_kph"]["unit"] == "km/h"                    # unit from the grammar
    assert "history" in named["packet_status"]["hint"]             # value table from the grammar
    # byte offsets let the UI highlight the exact characters of a field in the pasted text
    assert ZENITHRA_CAN[named["imei"]["at"]:][:15] == "868221073948609"

    # the CAN bus is expanded to one row per frame, not left as one unreadable blob
    can = [f for f in pkt["fields"] if f["group"].startswith("CAN frames")]
    assert [f["name"] for f in can] == ["CAN 0x0E1", "CAN 0x120"]
    assert can[0]["value"] == "000040A0010001E2" and can[0]["size"] == 8

    # and the normalized record still feeds the map/table/summary
    assert d["records"][0]["latitude"] == 28.550492
    assert d["records"][0]["speed_kph"] == 0.0


async def test_parse_keeps_the_codec_s_own_explainer():
    """A codec that has more to say than a generic walk (bit-level sizes, verified CRC) keeps
    saying it — the generic path must not flatten Teltonika's breakdown."""
    r = await _post("/api/parse", {"hex": _data(0x08, 1, _rec8()).hex()})
    d = r.json()
    assert d["protocol"] == "teltonika"
    pkt = d["packets"][0]
    assert pkt["crc_ok"] is True                                   # only its explainer verifies this
    assert any(f["bit"] for f in pkt["fields"])                    # bit column is populated
    assert any(g.startswith("AVL Record") for g in _groups(pkt))   # its own grouping


async def test_parse_protocol_override_and_unknown_name():
    forced = await _post("/api/parse", {"hex": ZENITHRA_CAN, "protocol": "ais140"})
    assert forced.status_code == 200
    assert forced.json()["protocol"] == "ais140"        # operator's choice wins over detection
    bad = await _post("/api/parse", {"hex": ZENITHRA_CAN, "protocol": "nope"})
    assert bad.status_code == 400


async def test_parse_without_a_published_grammar_still_breaks_fields_down():
    """No grammar file for this codec: the sentence's own tokens are shown positionally and the
    decoded record supplies the meaning, so a brand-new codec is inspectable immediately."""
    r = await _post("/api/parse", {"hex": "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"})
    d = r.json()
    assert d["protocol"] == "nmea"
    pkt = d["packets"][0]
    groups = _groups(pkt)
    assert any("no grammar published" in g for g in groups)
    assert groups["Decoded record"] >= 4
    named = {f["name"]: f["value"] for f in pkt["fields"]}
    assert named["message_type"] == "RMC" and named["latitude"].startswith("48.11")


async def test_parse_stream_reports_the_protocol_it_used():
    """The UI reads the final NDJSON line; it needs the resolved protocol to label the result
    and to sync the Dashboard through the same codec."""
    import json

    r = await _post("/api/parse-stream", {"hex": ZENITHRA_CAN})
    result = json.loads([ln for ln in r.text.splitlines() if ln.strip()][-1])
    assert result["type"] == "result" and result["protocol"] == "zenithra"
    assert len(result["records"]) == 1


async def test_ingest_accepts_text_and_auto_detects():
    """Dashboard sync takes the same input the Parser took, with no protocol assumed."""
    r = await _post("/api/ingest", {"hex": ZENITHRA_CAN, "peer": "test-parser"})
    assert r.status_code == 200
    assert r.json()["records"] == 1


async def test_parse_picks_the_grammar_variant_by_field_count():
    """$MMI comes in two layouts. The 50-field one (the custom IMU packet) must be named from
    its own grammar variant — read with the 38-field list, every field from 26 on was off by
    one (gsm_signal 'O', mcc '27') — and its samples listed one row each, not as one blob."""
    from pathlib import Path

    imu = (Path(__file__).parent / "fixtures" / "zenithra_imu_sample.log").read_text(encoding="ascii")
    d = (await _post("/api/parse", {"hex": imu})).json()
    assert d["protocol"] == "zenithra" and len(d["packets"]) == 1
    pkt = d["packets"][0]
    named = {f["name"]: f["value"] for f in pkt["fields"]}
    assert (named["emergency_status"], named["tamper_alert"]) == ("0", "O")
    assert (named["gsm_signal"], named["mcc"], named["frame_number"]) == ("27", "404", "001831")
    assert named["payload"].startswith("(1:0.06|0.11|9.82|")
    rows = [f for f in pkt["fields"] if f["group"] == "imu (20)"]
    assert len(rows) == 20 and rows[0]["value"].startswith("n=1  ax=0.06  ay=0.11  az=9.82")
    assert {f.get("list") for f in rows} == {"imu"}         # item rows are marked for the grid
    assert named["payload"] and "list" not in next(f for f in pkt["fields"] if f["name"] == "payload")
    assert not any(g.startswith("CAN frames") for g in _groups(pkt))

    # the 38-field layout keeps its default field list
    nr = {f["name"]: f["value"] for f in (await _post("/api/parse", {"hex": ZENITHRA_CAN})).json()["packets"][0]["fields"]}
    assert (nr["tamper_alert"], nr["gsm_signal"]) == ("O", "31")
