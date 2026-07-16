"""API tests: the Parser -> Dashboard ingest path.

POSTing hex to /api/ingest must decode it through the real codec into the process-wide
live store, so the device then shows on the Dashboard (which reads that store).
"""

from __future__ import annotations

from proinfo.core.crc import crc16_ibm, crc_itu


def _ttk(record: bytes, codec_id: int = 0x08, count: int = 1) -> bytes:
    data = bytes([codec_id, count]) + record + bytes([count])
    return (b"\x00\x00\x00\x00" + len(data).to_bytes(4, "big")
            + data + crc16_ibm(data).to_bytes(4, "big"))


def _gt06(proto: int, info: bytes, serial: int = 1) -> bytes:
    length = 1 + len(info) + 2 + 2
    body = bytes([length, proto]) + info + serial.to_bytes(2, "big")
    return b"\x78\x78" + body + crc_itu(body).to_bytes(2, "big") + b"\x0d\x0a"


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


async def test_tcp_listen_connect_send_stop():
    import asyncio
    import socket

    from httpx import ASGITransport, AsyncClient

    from proinfo.api.app import app

    def _free_port():
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        return p

    port = _free_port()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/listen", json={"codec": "teltonika", "host": "127.0.0.1", "port": port})
        assert r.status_code == 200
        jid = r.json()["job"]["id"]

        # wait for the listener to bind, then connect a fake device
        reader = writer = None
        for _ in range(40):
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                break
            except OSError:
                await asyncio.sleep(0.05)
        assert writer is not None, "listener did not bind"

        try:
            imei = b"356307042441013"
            writer.write(len(imei).to_bytes(2, "big") + imei)
            await writer.drain()
            assert await asyncio.wait_for(reader.read(1), 2) == b"\x01"     # handshake ACK

            # live job view reflects the connection + decoded record
            await asyncio.sleep(0.2)
            job = next(j for j in (await c.get("/api/jobs")).json() if j["id"] == jid)
            assert job["peers"] and job["records"] >= 1

            # downlink: raw hex -> device receives the bytes
            s = await c.post(f"/api/send/{jid}", json={"peer": job["peers"][0], "mode": "hex", "value": "68656c6c6f"})
            assert s.status_code == 200 and s.json()["sent"] == 5
            assert await asyncio.wait_for(reader.read(5), 2) == b"hello"
            # stop, then resume the SAME job id (no new job created)
            await c.post(f"/api/stop/{jid}")
            await asyncio.sleep(0.15)
            before = {j["id"] for j in (await c.get("/api/jobs")).json()}
            rr = await c.post(f"/api/resume/{jid}")
            assert rr.status_code == 200 and rr.json()["job"]["id"] == jid
            after = {j["id"] for j in (await c.get("/api/jobs")).json()}
            assert after == before                          # resumed in place, no extra card
        finally:
            writer.close()
            await c.post(f"/api/stop/{jid}")
            await asyncio.sleep(0.15)
            # can't remove while running; after stop it drops from the list
            assert (await c.delete(f"/api/jobs/{jid}")).status_code == 200
            assert jid not in {j["id"] for j in (await c.get("/api/jobs")).json()}


async def test_qc_start_identify_record_stop():
    import asyncio
    import socket
    from pathlib import Path

    from httpx import ASGITransport, AsyncClient

    from proinfo.api.app import app

    def _free_port():
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        return p

    port = _free_port()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        # Target 1: auto-detect protocol mode — no codec named up front.
        r = await c.post("/api/qc/start", json={"port": port, "host": "127.0.0.1",
                                                "protocol_mode": "auto", "operator": "tester"})
        assert r.status_code == 200
        qc = r.json()
        qc_id = qc["id"]
        assert qc["status"] == "running" and qc["protocol_mode"] == "auto"

        reader = writer = None
        for _ in range(40):
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                break
            except OSError:
                await asyncio.sleep(0.05)
        assert writer is not None, "QC listener did not bind"

        try:
            imei = b"356307042441013"
            writer.write(len(imei).to_bytes(2, "big") + imei)
            await writer.drain()
            assert await asyncio.wait_for(reader.read(1), 2) == b"\x01"

            writer.write(_ttk(_rec8()))
            await writer.drain()

            # identity + live count show up without a manual protocol pick
            await asyncio.sleep(0.3)
            got = (await c.get(f"/api/qc/{qc_id}")).json()
            assert got["identified_as"] == "teltonika"
            assert got["device_id"] == imei.decode()
            assert got["packet_count"] >= 1
            assert got["connected"] is True

            # listed among all QC sessions too
            listed = (await c.get("/api/qc")).json()
            assert any(s["id"] == qc_id for s in listed)
        finally:
            writer.close()
            stopped = (await c.post(f"/api/qc/{qc_id}/stop")).json()
            assert stopped["status"] == "stopped"
            await asyncio.sleep(0.15)

        # capture file was written to disk with the decoded packet's IMEI + raw hex
        cap_path = Path(stopped["capture_path"])
        assert cap_path.exists()
        text = cap_path.read_text(encoding="utf-8")
        assert imei.decode() in text

        # the file is also downloadable via the API
        dl = await c.get(f"/api/qc/{qc_id}/capture")
        assert dl.status_code == 200 and imei.decode() in dl.text

        # a QC report reflects the same verdict/checks + device identity as the session
        rep = (await c.get(f"/api/qc/{qc_id}/report")).json()
        assert rep["device_id"] == imei.decode()
        assert rep["verdict"] == stopped["verdict"]
        assert any(chk["key"] == "imei_present" and chk["result"] == "pass" for chk in rep["checks"])

        txt = await c.get(f"/api/qc/{qc_id}/report", params={"fmt": "txt"})
        assert txt.status_code == 200
        assert imei.decode() in txt.text and "Verdict:" in txt.text
        assert f"qc-report-{qc_id}.txt" in txt.headers["content-disposition"]


async def test_qc_resume_reuses_same_session():
    import asyncio
    import socket
    from pathlib import Path

    from httpx import ASGITransport, AsyncClient

    from proinfo.api.app import app

    def _free_port():
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        return p

    port = _free_port()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/qc/start", json={"port": port, "host": "127.0.0.1", "protocol_mode": "auto"})
        qc_id = r.json()["id"]

        async def _send_handshake_and_record():
            reader = writer = None
            for _ in range(40):
                try:
                    reader, writer = await asyncio.open_connection("127.0.0.1", port)
                    break
                except OSError:
                    await asyncio.sleep(0.05)
            imei = b"356307042441013"
            writer.write(len(imei).to_bytes(2, "big") + imei)
            await writer.drain()
            await asyncio.wait_for(reader.read(1), 2)
            writer.write(_ttk(_rec8()))
            await writer.drain()
            await asyncio.sleep(0.2)
            writer.close()

        await _send_handshake_and_record()
        first = (await c.get(f"/api/qc/{qc_id}")).json()
        assert first["packet_count"] >= 2 and first["identified_as"] == "teltonika"

        stopped = (await c.post(f"/api/qc/{qc_id}/stop")).json()
        assert stopped["status"] == "stopped"

        # Starting a genuinely NEW session on the same port is a distinct row (by design —
        # each device landing is its own QC record); this is what "Start" always does.
        r2 = await c.post("/api/qc/start", json={"port": port, "host": "127.0.0.1", "protocol_mode": "auto"})
        assert r2.status_code == 200 and r2.json()["id"] != qc_id
        await c.post(f"/api/qc/{r2.json()['id']}/stop")

        # Resume brings the ORIGINAL session back under the SAME id — no duplicate — and
        # the packet count keeps climbing rather than resetting to 0.
        resumed = (await c.post(f"/api/qc/{qc_id}/resume")).json()
        assert resumed["id"] == qc_id and resumed["status"] == "running"
        assert resumed["packet_count"] == first["packet_count"]     # carried over, not reset

        await _send_handshake_and_record()
        after = (await c.get(f"/api/qc/{qc_id}")).json()
        assert after["packet_count"] > first["packet_count"]         # kept accumulating

        await c.post(f"/api/qc/{qc_id}/stop")

        # still exactly one row for this id; capture file has both runs appended
        listing = [s for s in (await c.get("/api/qc")).json() if s["id"] == qc_id]
        assert len(listing) == 1
        text = Path(after["capture_path"]).read_text(encoding="utf-8")
        assert text.count("356307042441013") >= 4                   # 2 handshakes + 2 AVL records


async def test_qc_self_heals_status_on_bind_failure():
    """A QC session whose listener fails to bind (e.g. port already taken) must not show
    status="running" forever -- it should flip to "error" once the failure is noticed."""
    import asyncio
    import socket

    from httpx import ASGITransport, AsyncClient

    from proinfo.api.app import app

    def _free_port():
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        return p

    port = _free_port()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r1 = await c.post("/api/qc/start", json={"port": port, "host": "127.0.0.1", "protocol_mode": "auto"})
        qc1 = r1.json()["id"]
        # give the first listener time to actually bind before the second tries the same port
        for _ in range(40):
            try:
                probe = await asyncio.open_connection("127.0.0.1", port)
                probe[1].close()
                break
            except OSError:
                await asyncio.sleep(0.05)

        r2 = await c.post("/api/qc/start", json={"port": port, "host": "127.0.0.1", "protocol_mode": "auto"})
        qc2 = r2.json()["id"]
        assert r2.json()["status"] == "running"    # the bind failure hasn't surfaced yet (async)

        # poll until the self-heal in _qc_view notices the dead job and flips status
        healed = None
        for _ in range(40):
            got = (await c.get(f"/api/qc/{qc2}")).json()
            if got["status"] != "running":
                healed = got
                break
            await asyncio.sleep(0.05)
        assert healed is not None, "qc2 never self-healed out of 'running'"
        assert healed["status"] == "error"
        assert healed["ended_at"] is not None

        # the first session, which genuinely owns the port, is unaffected
        assert (await c.get(f"/api/qc/{qc1}")).json()["status"] == "running"

        await c.post(f"/api/qc/{qc1}/stop")


async def test_qc_manual_mode_and_gt06_device_id_not_placeholder():
    """Manual protocol_mode (never exercised above) + a regression guard for a real bug:
    QcCaptureSink used to only recognise Teltonika's "teltonika-unknown" placeholder, so a
    GT06 device's real IMEI could be masked by (or confused with) "gt06-unknown"."""
    import asyncio
    import socket

    from httpx import ASGITransport, AsyncClient

    from proinfo.api.app import app

    def _free_port():
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        return p

    port = _free_port()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/qc/start", json={
            "port": port, "host": "127.0.0.1", "protocol_mode": "manual", "protocol": "gt06"})
        qc_id = r.json()["id"]
        assert r.json()["protocol_mode"] == "manual" and r.json()["protocol"] == "gt06"

        # unknown codec must be rejected outright
        bad = await c.post("/api/qc/start", json={
            "port": _free_port(), "host": "127.0.0.1", "protocol_mode": "manual", "protocol": "not-a-codec"})
        assert bad.status_code == 400

        reader = writer = None
        for _ in range(40):
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                break
            except OSError:
                await asyncio.sleep(0.05)
        assert writer is not None

        writer.write(_gt06(0x01, bytes.fromhex("0351091270110476")))
        await writer.drain()
        await asyncio.wait_for(reader.read(64), 2)
        await asyncio.sleep(0.3)

        got = (await c.get(f"/api/qc/{qc_id}")).json()
        assert got["identified_as"] == "gt06"
        assert got["device_id"] == "351091270110476"      # the real IMEI ...
        assert got["device_id"] != "gt06-unknown"          # ... never the placeholder

        writer.close()
        await c.post(f"/api/qc/{qc_id}/stop")


async def test_qc_device_id_is_unique_identifier_across_sessions():
    """The IMEI (device_id), not the session id/port, is the device's real identity: a
    device that lands in a brand-new session must be recognised as having been QC'd
    before, via prior_sessions -- regardless of which port/session it uses this time."""
    import asyncio
    import socket

    from httpx import ASGITransport, AsyncClient

    from proinfo.api.app import app

    def _free_port():
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        return p

    # A distinct IMEI from the one other GT06 tests use (0351091270110476) -- this test
    # suite shares one SQLite DB for its whole run (see conftest.py), so qc_sessions rows
    # from other tests persist; reusing an IMEI would pollute the "first time seen" check.
    same_imei = bytes.fromhex("0499123456789012")

    async def _run_session(c, port):
        r = await c.post("/api/qc/start", json={"port": port, "host": "127.0.0.1", "protocol_mode": "auto"})
        qc_id = r.json()["id"]
        reader = writer = None
        for _ in range(40):
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                break
            except OSError:
                await asyncio.sleep(0.05)
        writer.write(_gt06(0x01, same_imei))
        await writer.drain()
        await asyncio.wait_for(reader.read(64), 2)
        await asyncio.sleep(0.3)
        writer.close()
        stopped = (await c.post(f"/api/qc/{qc_id}/stop")).json()
        return qc_id, stopped

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        qc1, done1 = await _run_session(c, _free_port())
        assert done1["device_id"] == "499123456789012"
        assert done1["prior_sessions"] == []          # first time this device has ever been seen

        # a DIFFERENT device (different IMEI) in between must not pollute the history
        other_imei_port = _free_port()
        r = await c.post("/api/qc/start", json={"port": other_imei_port, "host": "127.0.0.1", "protocol_mode": "auto"})
        other_id = r.json()["id"]
        await c.post(f"/api/qc/{other_id}/stop")

        # same physical device (same IMEI), a genuinely NEW session on a DIFFERENT port
        qc2, done2 = await _run_session(c, _free_port())
        assert qc2 != qc1
        assert done2["device_id"] == "499123456789012"
        prior_ids = {p["id"] for p in done2["prior_sessions"]}
        assert prior_ids == {qc1}                     # recognised as the same device as session 1
        assert other_id not in prior_ids               # the unrelated device never shows up

        # and querying the FIRST session again now also shows the second as prior history
        refreshed1 = (await c.get(f"/api/qc/{qc1}")).json()
        assert {p["id"] for p in refreshed1["prior_sessions"]} == {qc2}


def _rec8_now() -> bytes:
    """Same shape as _rec8() but stamped with the current time (_rec8()'s timestamp is a
    fixed historical constant) -- needed so the packet_cadence check sees a realistic gap
    to the IMEI handshake record instead of a multi-year one."""
    import time as _time

    ts = int(_time.time() * 1000).to_bytes(8, "big")
    gps = (int(88.3 * 1e7).to_bytes(4, "big", signed=True)
           + int(22.5 * 1e7).to_bytes(4, "big", signed=True)
           + (100).to_bytes(2, "big", signed=True) + (90).to_bytes(2, "big")
           + bytes([12]) + (60).to_bytes(2, "big"))
    io = bytes([0, 1, 1, 239, 1, 0, 0, 0])
    return ts + bytes([1]) + gps + io


def _rec8_bad_fix() -> bytes:
    """Same shape as _rec8() but 0 satellites and a 0,0 fix -- exercises the QC
    location_fix/gps_valid_flag "fail" branches against a real decoded packet."""
    ts = (1719318600000).to_bytes(8, "big")
    gps = ((0).to_bytes(4, "big", signed=True) + (0).to_bytes(4, "big", signed=True)
           + (0).to_bytes(2, "big", signed=True) + (0).to_bytes(2, "big")
           + bytes([0]) + (0).to_bytes(2, "big"))
    io = bytes([0, 1, 1, 239, 1, 0, 0, 0])
    return ts + bytes([1]) + gps + io


async def test_qc_checks_pass_with_imei_and_valid_fix():
    """QC Target 2: a session with a real IMEI and a valid GPS fix (12 sats, lat/lon in
    range) should show imei_present/location_fix/gps_valid_flag as pass, the not-yet-
    applicable checks as na, and an overall pass verdict -- live, and again after Stop
    (persisted, not just computed on the fly)."""
    import asyncio
    import socket

    from httpx import ASGITransport, AsyncClient

    from proinfo.api.app import app

    def _free_port():
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        return p

    port = _free_port()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/qc/start", json={"port": port, "host": "127.0.0.1", "protocol_mode": "auto"})
        qc_id = r.json()["id"]

        reader = writer = None
        for _ in range(40):
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                break
            except OSError:
                await asyncio.sleep(0.05)
        assert writer is not None

        imei = b"356307042441013"
        writer.write(len(imei).to_bytes(2, "big") + imei)
        await writer.drain()
        await asyncio.wait_for(reader.read(1), 2)
        writer.write(_ttk(_rec8_now()))
        await writer.drain()
        await asyncio.sleep(0.3)

        got = (await c.get(f"/api/qc/{qc_id}")).json()
        by_key = {c_["key"]: c_ for c_ in got["checks"]}
        assert by_key["imei_present"]["result"] == "pass"
        assert by_key["location_fix"]["result"] == "pass"
        assert by_key["gps_valid_flag"]["result"] == "pass"
        assert by_key["external_voltage"]["result"] == "na"    # no range configured
        # teltonika has no QC profile -> verdict falls back to the checks pass/fail
        assert got["verdict"] == "pass"

        writer.close()
        stopped = (await c.post(f"/api/qc/{qc_id}/stop")).json()
        assert stopped["verdict"] == "pass"                    # persisted, not just live
        assert {c_["key"]: c_["result"] for c_ in stopped["checks"]} == \
            {c_["key"]: c_["result"] for c_ in got["checks"]}


async def test_qc_checks_fail_with_no_gps_fix():
    """A device that never gets a GPS fix (0 satellites, 0,0 position) must fail
    location_fix and gps_valid_flag even though its IMEI is known -- fail beats pass in
    the overall verdict."""
    import asyncio
    import socket

    from httpx import ASGITransport, AsyncClient

    from proinfo.api.app import app

    def _free_port():
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        return p

    port = _free_port()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/qc/start", json={"port": port, "host": "127.0.0.1", "protocol_mode": "auto"})
        qc_id = r.json()["id"]

        reader = writer = None
        for _ in range(40):
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                break
            except OSError:
                await asyncio.sleep(0.05)
        assert writer is not None

        imei = b"356307042441099"
        writer.write(len(imei).to_bytes(2, "big") + imei)
        await writer.drain()
        await asyncio.wait_for(reader.read(1), 2)
        writer.write(_ttk(_rec8_bad_fix()))
        await writer.drain()
        await asyncio.sleep(0.3)

        got = (await c.get(f"/api/qc/{qc_id}")).json()
        by_key = {c_["key"]: c_ for c_ in got["checks"]}
        assert by_key["imei_present"]["result"] == "pass"
        assert by_key["location_fix"]["result"] == "fail"
        assert by_key["gps_valid_flag"]["result"] == "fail"
        assert got["verdict"] == "fail"

        writer.close()
        await c.post(f"/api/qc/{qc_id}/stop")


def _ais140_lgn(imei: bytes, firmware: str = "01.3.0") -> bytes:
    return f"$LGN,0000,{imei.decode()},{firmware},10.07757430,N,76.33675600,E*\r\n".encode()


_AIS140_PVT_TEMPLATE = (
    "LOCT14A,2.2.5,NR,{alert},H,{imei},0000000000,1,03072026,152735,"
    "28.550457,N,77.268852,E,000.0,132.84,33,221.064,0.83,0.48,airte ,1,1,13.7,4.1,0,C,"
    "31,404,10,092,9305,9304,092,18,96e3,06f,13,d410,092,11,0,0,31,0010,00,027631,BC")


def _ais140_pvt(imei: bytes, alert_id: int) -> bytes:
    return (b"$PVT," + _AIS140_PVT_TEMPLATE.format(alert=alert_id, imei=imei.decode()).encode() + b"*\r\n")


async def test_qc_device_summary_firmware_and_alarm_coverage():
    """QC device drill-down (click IMEI): firmware from the login packet + a checklist of
    every alarm the AIS-140 codec's alarm_catalog defines (16 total), seen vs never-seen,
    aggregated from proinfo.storage.live's process-wide per-device history -- not scoped
    to this one QC session, same cross-session identity model as prior_sessions."""
    import asyncio
    import socket

    from httpx import ASGITransport, AsyncClient

    from proinfo.api.app import app
    from proinfo.codecs.ais140 import Ais140Codec

    def _free_port():
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        return p

    imei = b"868329087399999"
    port = _free_port()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/qc/start", json={
            "port": port, "host": "127.0.0.1", "protocol_mode": "manual", "protocol": "ais140"})
        qc_id = r.json()["id"]

        reader = writer = None
        for _ in range(40):
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                break
            except OSError:
                await asyncio.sleep(0.05)
        assert writer is not None

        writer.write(_ais140_lgn(imei, "2.2.5"))   # match the PVT firmware so it's unambiguous
        await writer.drain()
        await asyncio.wait_for(reader.read(64), 2)

        writer.write(_ais140_pvt(imei, 4))      # low_battery
        await writer.drain()
        await asyncio.wait_for(reader.read(64), 2)

        writer.write(_ais140_pvt(imei, 13))     # harsh_braking
        await writer.drain()
        await asyncio.wait_for(reader.read(64), 2)
        await asyncio.sleep(0.3)

        got = (await c.get(f"/api/qc/device/{imei.decode()}")).json()
        assert got["protocol"] == "ais140"
        assert got["device_info"]["firmware_version"] == "2.2.5"
        assert len(got["alarms"]) == len(Ais140Codec.alarm_catalog)
        by_key = {a["key"]: a for a in got["alarms"]}
        assert by_key["low_battery"]["seen"] is True and by_key["low_battery"]["count"] == 1
        assert by_key["harsh_braking"]["seen"] is True and by_key["harsh_braking"]["count"] == 1
        assert by_key["device_tampered"]["seen"] is False and by_key["device_tampered"]["count"] == 0

        writer.close()
        await c.post(f"/api/qc/{qc_id}/stop")


async def test_qc_device_summary_404_for_unknown_device():
    from httpx import ASGITransport, AsyncClient

    from proinfo.api.app import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/qc/device/no-such-imei-ever")
        assert r.status_code == 404


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


async def test_qc_reconciles_stale_sessions_from_previous_process():
    """QC sessions persist in SQLite but job ids are per-process (job-1, job-2, ...).
    After a restart a row's job_id is dead — or COLLIDES with a new job that reuses the
    same id (an old tab would show the new job's devices/feed). And rows still marked
    "running" have nothing listening for them. Both must be reconciled:

    * _qc_reconcile_startup() (runs in the app lifespan) clears job_id everywhere and
      flips running -> stopped;
    * _qc_view self-heals a running row whose job this process doesn't know (covers a
      restart even if the startup hook were missed).
    """
    import time as _time

    from httpx import ASGITransport, AsyncClient

    from proinfo.api.app import _qc_reconcile_startup, app, db

    # simulate leftovers from a previous process: a "running" row + a stopped row, both
    # pointing at job ids this process never created (or could reuse for NEW jobs)
    stale_running = db.insert(
        "qc_sessions", port=64990, host="127.0.0.1", protocol_mode="auto", protocol="auto",
        identified_as="", device_id="", peer="", operator="", notes="", status="running",
        packet_count=7, capture_path="", capture_fmt="txt",
        started_at=_time.time() - 3600, ended_at=None, job_id="job-1",
    )
    stale_stopped = db.insert(
        "qc_sessions", port=64991, host="127.0.0.1", protocol_mode="auto", protocol="auto",
        identified_as="", device_id="", peer="", operator="", notes="", status="stopped",
        packet_count=3, capture_path="", capture_fmt="txt",
        started_at=_time.time() - 7200, ended_at=_time.time() - 7100, job_id="job-2",
    )
    try:
        # (a) the view-level self-heal alone: a running row whose job is unknown flips
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            got = (await c.get(f"/api/qc/{stale_running}")).json()
            assert got["status"] == "stopped"          # healed: nothing is listening for it
            assert got["ended_at"] is not None

        # (b) the startup reconcile clears every stale job_id (collision guard)
        _qc_reconcile_startup()
        assert db.qc_session(stale_running)["job_id"] == ""
        assert db.qc_session(stale_stopped)["job_id"] == ""
        assert db.qc_session(stale_stopped)["status"] == "stopped"   # untouched otherwise

        # (c) closing (DELETE) a reconciled session works — this was the user-visible bug
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            assert (await c.delete(f"/api/qc/{stale_running}")).status_code == 200
            assert (await c.get(f"/api/qc/{stale_running}")).status_code == 404
    finally:
        for qc_id in (stale_running, stale_stopped):
            try:
                db.delete("qc_sessions", qc_id)
            except Exception:
                pass


async def test_qc_stop_and_remove_while_device_connected():
    """The user-visible 'can't close the QC tab' bug: stopping a QC session while the
    device is STILL connected and sending must let the session be removed. The listener's
    close() used to hang on wait_closed() with a live socket, so the pipeline task never
    left _tasks and DELETE stayed 400 forever."""
    import asyncio
    import socket

    from httpx import ASGITransport, AsyncClient

    from proinfo.api.app import app

    def _free_port():
        s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

    def _pvt(imei):
        tail = "DL01,1,24032018,060122,28.75896,N,77.62778,E,22.5,320,4,183,1,0,INA,1,1,12.5,4.2,0,C,25"
        return f"$PVT,VENDOR,2.2.5,NR,1,L,{imei},{tail}*00\r\n".encode()

    port = _free_port()
    imei = "868329087318744"                              # distinct id for this test
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/qc/start", json={"port": port, "host": "127.0.0.1", "protocol_mode": "auto"})
        qc = r.json()["id"]

        writer = None
        for _ in range(60):
            try:
                _, writer = await asyncio.open_connection("127.0.0.1", port); break
            except OSError:
                await asyncio.sleep(0.05)
        assert writer is not None, "QC listener did not bind"
        writer.write(_pvt(imei)); await writer.drain()
        await asyncio.sleep(0.3)
        assert (await c.get(f"/api/qc/{qc}")).json()["packet_count"] >= 1

        try:
            # stop while the socket is STILL open + then remove — must succeed promptly
            assert (await c.post(f"/api/qc/{qc}/stop")).status_code == 200
            removed = False
            for _ in range(30):
                if (await c.delete(f"/api/qc/{qc}")).status_code == 200:
                    removed = True
                    break
                await asyncio.sleep(0.1)
            assert removed, "could not remove the QC session after stop (close() hung)"
            assert (await c.get(f"/api/qc/{qc}")).status_code == 404
        finally:
            try:
                writer.close()
            except Exception:
                pass


async def test_qc_coverage_and_verdict_states():
    """Phase A2: an AIS-140 session (which has a QC profile) surfaces packet coverage and a
    coverage-driven verdict — in_progress while required items are still missing, and
    conditional_pass once the operator skips the outstanding required items."""
    import asyncio
    import socket

    from httpx import ASGITransport, AsyncClient

    from proinfo.api.app import app

    def _free_port():
        s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

    imei = b"868329087355501"
    port = _free_port()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/qc/start", json={
            "port": port, "host": "127.0.0.1", "protocol_mode": "manual", "protocol": "ais140"})
        qc_id = r.json()["id"]

        writer = None
        for _ in range(40):
            try:
                _, writer = await asyncio.open_connection("127.0.0.1", port); break
            except OSError:
                await asyncio.sleep(0.05)
        assert writer is not None
        writer.write(_ais140_lgn(imei, "2.2.5")); await writer.drain()  # login only
        await asyncio.sleep(0.3)

        got = (await c.get(f"/api/qc/{qc_id}")).json()
        # per-device: one entry for this IMEI, with its own coverage
        devs = {d["device_id"]: d for d in got["devices"]}
        assert imei.decode() in devs, "device should appear in the per-device list"
        dev = devs[imei.decode()]
        cov = {c_["key"]: c_ for c_ in dev["coverage"]}
        assert cov, "coverage should be populated from the ais140 QC profile"
        assert cov["login"]["seen"] is True and cov["login"]["count"] == 1
        assert cov["live"]["seen"] is False and cov["live"]["required"] is True
        assert dev["verdict"] == "in_progress"           # required items still missing, device alive
        assert got["verdict"] == "in_progress"           # session aggregate

        # operator skips every still-missing required item for THIS device -> conditional pass
        missing_required = [k for k, v in cov.items() if v["required"] and not v["seen"]]
        skip = (await c.post(f"/api/qc/{qc_id}/skip",
                             json={"device_id": imei.decode(), "keys": missing_required})).json()
        sdev = {d["device_id"]: d for d in skip["devices"]}[imei.decode()]
        assert sdev["verdict"] == "conditional_pass"
        assert set(skip["skipped"][imei.decode()]) == set(missing_required)

        writer.close()
        stopped = (await c.post(f"/api/qc/{qc_id}/stop")).json()
        assert stopped["verdict"] == "conditional_pass"   # persisted with the skips
        sdev2 = {d["device_id"]: d for d in stopped["devices"]}[imei.decode()]
        assert {c_["key"] for c_ in sdev2["coverage"]}    # per-device coverage persisted too


async def test_qc_dedups_device_across_reconnect():
    """#2: one physical device (same IMEI) that reconnects on a new peer must appear ONCE
    in the per-device list — its two connections merge, not show as two entities."""
    import asyncio
    import socket

    from httpx import ASGITransport, AsyncClient

    from proinfo.api.app import app

    def _free_port():
        s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

    imei = b"868329087366602"
    port = _free_port()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/qc/start", json={
            "port": port, "host": "127.0.0.1", "protocol_mode": "manual", "protocol": "ais140"})
        qc_id = r.json()["id"]

        async def _one_connection():
            w = None
            for _ in range(40):
                try:
                    _, w = await asyncio.open_connection("127.0.0.1", port); break
                except OSError:
                    await asyncio.sleep(0.05)
            assert w is not None
            w.write(_ais140_lgn(imei, "2.2.5")); await w.drain(); await asyncio.sleep(0.15)
            w.write(_ais140_pvt(imei, 1)); await w.drain(); await asyncio.sleep(0.2)
            w.close()

        await _one_connection()          # first connection (peer A)
        await asyncio.sleep(0.2)
        await _one_connection()          # reconnect (peer B) — same IMEI
        await asyncio.sleep(0.3)

        got = (await c.get(f"/api/qc/{qc_id}")).json()
        mine = [d for d in got["devices"] if d["device_id"] == imei.decode()]
        assert len(mine) == 1, f"reconnect should merge into one device, got {len(mine)}"
        assert len(mine[0]["peers"]) == 2, "both connections should merge under the one device"

        await c.post(f"/api/qc/{qc_id}/stop")


async def test_qc_device_serial_and_prior_status_across_sessions():
    """#6/#7: the per-device registry survives across separate QC sessions for the SAME
    IMEI -- a reconnecting device keeps its serial number, and once one session's verdict
    finalizes, the NEXT session for that same IMEI immediately shows the prior result
    (serial + last-known status), without the operator having to search old sessions."""
    import asyncio
    import socket

    from httpx import ASGITransport, AsyncClient

    from proinfo.api.app import app

    def _free_port():
        s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

    imei = b"868329087355777"

    async def _run_session(c, port):
        r = await c.post("/api/qc/start", json={
            "port": port, "host": "127.0.0.1", "protocol_mode": "manual", "protocol": "ais140"})
        qc_id = r.json()["id"]
        writer = None
        for _ in range(40):
            try:
                _, writer = await asyncio.open_connection("127.0.0.1", port); break
            except OSError:
                await asyncio.sleep(0.05)
        assert writer is not None
        writer.write(_ais140_lgn(imei, "2.2.5")); await writer.drain()
        await asyncio.sleep(0.3)
        return qc_id, writer

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        # --- session 1: connect, then skip every required item so it FINALIZES as
        # conditional_pass (same pattern as test_qc_coverage_and_verdict_states) ---
        port1 = _free_port()
        qc1, w1 = await _run_session(c, port1)
        got = (await c.get(f"/api/qc/{qc1}")).json()
        dev1 = {d["device_id"]: d for d in got["devices"]}[imei.decode()]
        serial = dev1["serial"]
        assert isinstance(serial, int) and serial > 0
        assert dev1["prior_verdict"] == "", "brand-new device has no prior result yet"

        missing = [k for k, v in {c_["key"]: c_ for c_ in dev1["coverage"]}.items() if v["required"] and not v["seen"]]
        skip1 = (await c.post(f"/api/qc/{qc1}/skip",
                              json={"device_id": imei.decode(), "keys": missing})).json()
        dev1b = {d["device_id"]: d for d in skip1["devices"]}[imei.decode()]
        assert dev1b["verdict"] == "conditional_pass"
        w1.close()
        await c.post(f"/api/qc/{qc1}/stop")

        # --- session 2: a NEW session, SAME imei, different port -> same serial, and the
        # prior session's FINAL verdict is immediately visible before this one finalizes ---
        port2 = _free_port()
        qc2, w2 = await _run_session(c, port2)
        got2 = (await c.get(f"/api/qc/{qc2}")).json()
        dev2 = {d["device_id"]: d for d in got2["devices"]}[imei.decode()]
        assert dev2["serial"] == serial, "the same IMEI must keep the same serial across sessions"
        assert dev2["prior_qc_id"] == qc1
        assert dev2["prior_verdict"] == "conditional_pass"
        assert dev2["verdict"] == "in_progress", "this NEW session hasn't finalized yet"

        # the standalone device-info endpoint carries the same registry fields
        info = (await c.get(f"/api/qc/device/{imei.decode()}")).json()
        assert info["serial"] == serial
        assert info["last_verdict"] == "conditional_pass"
        assert info["last_qc_id"] == qc1

        w2.close()
        await c.post(f"/api/qc/{qc2}/stop")
