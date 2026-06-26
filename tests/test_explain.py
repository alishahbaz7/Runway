"""Tests for the Teltonika field-by-field explainer + the /api/parse endpoint.

Packets are built with the real crc16_ibm helper (same approach as test_codecs), so the
explainer is exercised against valid framing, names and CRCs end-to-end.
"""

from __future__ import annotations

from proinfo.codecs.teltonika_explain import explain
from proinfo.core.crc import crc16_ibm


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


def test_explain_udp_frame():
    inner = bytes([0x08, 1]) + _rec8() + bytes([1])
    imei = b"356307042441013"
    body = b"\x00\x01" + b"\x01" + b"\x00" + len(imei).to_bytes(2, "big") + imei + inner
    frame = len(body).to_bytes(2, "big") + body
    out = explain(frame, "udp")
    assert out["ok"]
    assert out["packets"][0]["kind"] == "udp_data"
    assert out["records"][0]["speed_kph"] == 60


def test_explain_garbage_reports_error():
    out = explain(b"\xde\xad\xbe\xef\x11\x22", "tcp")
    assert out["ok"] is False
    assert out["error"]


async def test_parse_endpoint():
    from httpx import ASGITransport, AsyncClient

    from proinfo.api.app import app

    payload = {"hex": _data(0x08, 1, _rec8()).hex(), "transport": "tcp"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/parse", json=payload)
        bad = await c.post("/api/parse", json={"hex": "xyz", "transport": "tcp"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] and data["records"][0]["speed_kph"] == 60
    assert bad.status_code == 400                          # invalid hex rejected
