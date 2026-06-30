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


async def test_signatures_crud():
    from httpx import ASGITransport, AsyncClient

    from proinfo.api.app import app

    name = "pytest_sig_zz"
    content = {"905": [{"fieldName": "Charger_status", "startingBit": 16, "size": 8}]}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        # save (content as a JSON string, as the UI sends it)
        import json as _json
        r = await c.post("/api/signatures", json={"name": name, "content": _json.dumps(content)})
        assert r.status_code == 200 and r.json()["keys"] == 1

        # it appears in the repository listing
        listing = (await c.get("/api/signatures")).json()
        assert any(s["name"] == name + ".json" for s in listing)

        # fetch its content back
        got = (await c.get(f"/api/signatures/{name}.json")).json()
        assert got["content"]["905"][0]["fieldName"] == "Charger_status"

        # bad name is rejected; non-object content is rejected
        assert (await c.post("/api/signatures", json={"name": "../evil", "content": {}})).status_code == 400
        assert (await c.post("/api/signatures", json={"name": "x", "content": "[]"})).status_code == 400

        # delete
        assert (await c.delete(f"/api/signatures/{name}.json")).status_code == 200
        listing = (await c.get("/api/signatures")).json()
        assert not any(s["name"] == name + ".json" for s in listing)


async def test_protocols_crud():
    from httpx import ASGITransport, AsyncClient

    from proinfo.api.app import app

    name = "pytest_proto_zz"
    content = {"profile": {"vendor": "ACME"},
               "fields": [{"name": "lat", "at": 0, "type": "int32_be"}]}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        import json as _json
        # save (content as a JSON string, as the UI sends it)
        r = await c.post("/api/protocols", json={"name": name, "content": _json.dumps(content)})
        assert r.status_code == 200 and r.json()["keys"] == 2

        listing = (await c.get("/api/protocols")).json()
        assert any(s["name"] == name + ".json" for s in listing)

        got = (await c.get(f"/api/protocols/{name}.json")).json()
        assert got["content"]["fields"][0]["name"] == "lat"

        # same repository mechanism as signatures: path-y names + non-object rejected
        assert (await c.post("/api/protocols", json={"name": "../evil", "content": {}})).status_code == 400
        assert (await c.post("/api/protocols", json={"name": "x", "content": "[]"})).status_code == 400

        assert (await c.delete(f"/api/protocols/{name}.json")).status_code == 200
        listing = (await c.get("/api/protocols")).json()
        assert not any(s["name"] == name + ".json" for s in listing)

        # format-agnostic: a .py parser file is stored verbatim and served as text
        py_name, src = "pytest_proto_zz.py", "def parse(b):\n    return []\n"
        r = await c.post("/api/protocols", json={"name": py_name, "content": src})
        assert r.status_code == 200 and r.json()["kind"] == "py"
        got = (await c.get(f"/api/protocols/{py_name}")).json()
        assert got["kind"] == "py" and got["content"] is None and got["text"] == src
        # unsupported extensions are rejected
        assert (await c.post("/api/protocols", json={"name": "x.exe", "content": "x"})).status_code == 400
        await c.delete(f"/api/protocols/{py_name}")


async def test_convert_canplus_to_dbc_and_json():
    from httpx import ASGITransport, AsyncClient

    from proinfo.api.app import app

    xml = (
        '<CanData><Variable_Descriptors>'
        '<VarDesc RawDataStr="00-AC-3D-80-13-0B-A3-03-01-00-0A-00-00-00-27-10-0D-F3-B2-03-8E-31" Name="SOC">'
        '<Variable Title="SOC-State OF Charge" VarID="803D" Var_Function="FuelTankLevel"'
        ' FW_Multiplier="1" FW_Divider="10" FW_Offset="0" FW_Units="%" Decimal_Places="0" />'
        '</VarDesc></Variable_Descriptors>'
        '<Config><VehicleLibraryInfo><Vehicle Brand="MG" ModelName="ZS EV" ProductionYear="2020" />'
        '</VehicleLibraryInfo></Config></CanData>'
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        m = (await c.post("/api/convert/parse", json={"fmt": "canplus", "text": xml})).json()
        assert m["vehicle"]["brand"] == "MG" and len(m["signals"]) == 1
        soc = m["signals"][0]
        assert soc["var_id"] == "803D" and soc["multiplier"] == 1 and soc["divider"] == 10
        assert soc["can_id_low"] == 0xAC                     # descriptor byte 1 -> PID 0x3AC low byte

        soc["can_id"] = 0x3AC                                # user edit
        dbc = (await c.post("/api/convert/export",
                            json={"fmt": "dbc", "vehicle": m["vehicle"], "signals": m["signals"]})).json()
        assert "BO_ 940 " in dbc["content"]                  # 0x3AC = 940
        assert "(0.1,0)" in dbc["content"] and '"%"' in dbc["content"]
        assert dbc["filename"].endswith(".dbc")

        js = (await c.post("/api/convert/export",
                           json={"fmt": "json", "vehicle": m["vehicle"], "signals": m["signals"]})).json()
        assert js["filename"].endswith(".json") and '"var_id": "803D"' in js["content"]


async def test_parse_stream_emits_result():
    import json as _json

    from httpx import ASGITransport, AsyncClient

    from proinfo.api.app import app

    # good + garbage + good -> streamed result decodes both, skips the junk
    good = _ttk(_rec8())
    hexstr = (good + b"\xaa\xbb\xcc\xdd\xee" + good).hex()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/parse-stream", json={"hex": hexstr, "transport": "tcp"})
    assert r.status_code == 200
    events = [_json.loads(ln) for ln in r.text.splitlines() if ln.strip()]
    result = [e for e in events if e["type"] == "result"]
    assert len(result) == 1
    assert len(result[0]["packets"]) == 2 and result[0]["skipped"] >= 1


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
