"""API tests: the Parser -> Dashboard ingest path.

POSTing hex to /api/ingest must decode it through the real codec into the process-wide
live store, so the device then shows on the Dashboard (which reads that store).
"""

from __future__ import annotations

from proinfo.core.crc import crc16_ibm


def _ttk(record: bytes, codec_id: int = 0x08, count: int = 1) -> bytes:
    data = bytes([codec_id, count]) + record + bytes([count])
    return (b"\x00\x00\x00\x00" + len(data).to_bytes(4, "big")
            + data + crc16_ibm(data).to_bytes(4, "big"))


def _rec8() -> bytes:
    ts = (1719318600000).to_bytes(8, "big")
    gps = (int(88.3 * 1e7).to_bytes(4, "big", signed=True)
           + int(22.5 * 1e7).to_bytes(4, "big", signed=True)
           + (100).to_bytes(2, "big", signed=True) + (90).to_bytes(2, "big")
           + bytes([12]) + (60).to_bytes(2, "big"))
    io = bytes([0, 1, 1, 239, 1, 0, 0, 0])
    return ts + bytes([1]) + gps + io


async def test_params_lists_avl_names():
    from httpx import ASGITransport, AsyncClient

    from proinfo.api.app import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/params")
    assert r.status_code == 200
    avl = {p["id"]: p["name"] for p in r.json()["avl"]}
    assert avl[239] == "Ignition" and avl[240] == "Movement"


async def test_ingest_populates_live_store():
    from httpx import ASGITransport, AsyncClient

    from proinfo.api.app import app
    from proinfo.storage.live import store

    imei = b"356307042441099"                       # distinct id for this test
    handshake = len(imei).to_bytes(2, "big") + imei
    hexstr = (handshake + _ttk(_rec8())).hex()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/ingest", json={"hex": hexstr, "codec": "teltonika"})

    assert r.status_code == 200
    assert r.json()["records"] >= 1
    assert "356307042441099" in store.devices         # now visible to the Dashboard
    assert store.devices["356307042441099"]["protocol"] == "teltonika"
