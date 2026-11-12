"""Device OTA API: saved profiles (create, list across environments, edit, delete) and the
local encode/decode path. No test here talks to the OTA cloud — /api/ota/send is exercised only
for its refusals, which happen before any network call.

State lives in the throwaway RUNWAY_DATA_DIR set by conftest.py, never the real data/ folder.
"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

KEYS = ["1DA549D716E26C4242CB566D12D58CE9",
        "7FF1A12051744ED16549D84C9C5F44FA",
        "DD99FF68F43C33D43583A77F481E3F86"]
IMEI = "866685088308300"


def _client():
    from runway.api.app import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _profiles(c) -> list[dict]:
    return (await c.get("/api/ota/profiles")).json()["profiles"]


async def test_profile_create_list_rename_delete():
    async with _client() as c:
        r = await c.put(f"/api/ota/devices/{IMEI}",
                        json={"env": "dev", "name": "Bench unit", "static_keys": KEYS})
        assert r.status_code == 200
        s = r.json()["summary"]
        assert s["name"] == "Bench unit" and s["static_count"] == 3 and s["has_comm"] is False
        assert s["endpoint"].startswith("http://intouchdev.")          # the dev preset

        mine = [p for p in await _profiles(c) if p["imei"] == IMEI and p["env"] == "dev"]
        assert len(mine) == 1 and mine[0]["name"] == "Bench unit"

        # rename keeps the keys
        r = await c.put(f"/api/ota/devices/{IMEI}",
                        json={"env": "dev", "name": "Desk unit", "static_keys": KEYS})
        assert r.json()["summary"]["name"] == "Desk unit"
        full = (await c.get(f"/api/ota/devices/{IMEI}", params={"env": "dev"})).json()
        assert full["static_keys"] == KEYS

        assert (await c.delete(f"/api/ota/devices/{IMEI}", params={"env": "dev"})).status_code == 200
        assert not [p for p in await _profiles(c) if p["imei"] == IMEI and p["env"] == "dev"]


async def test_device_record_carries_every_key_for_the_keys_dialog():
    """The keys dialog reads one record: the static keys, the comm keys, and when they expire."""
    from runway.api.app import _ota_load_device, _ota_store_device
    from runway.ota.keystore import now_ms

    async with _client() as c:
        await c.put("/api/ota/devices/KEYVIEW", json={"env": "dev", "static_keys": KEYS})
        dk = _ota_load_device("dev", "KEYVIEW")              # as a keys download leaves it
        dk.apply_comm_response({"comMap": {str(i): f"{i:02X}" * 16 for i in range(1, 9)},
                                "expireTimestamp": now_ms() + 3_600_000})
        _ota_store_device("dev", dk)

        d = (await c.get("/api/ota/devices/KEYVIEW", params={"env": "dev"})).json()
        assert d["static_keys"] == KEYS
        assert sorted(d["comm_map"], key=int) == [str(i) for i in range(1, 9)]
        assert d["comm_map"]["3"] == "03" * 16
        assert d["expire_ts"] > now_ms() and d["fetched_ms"] > 0
        assert d["summary"]["has_comm"] is True and d["summary"]["expired"] is False
        await c.delete("/api/ota/devices/KEYVIEW", params={"env": "dev"})


async def test_custom_profile_carries_its_own_endpoint():
    async with _client() as c:
        url = "https://staging.example.test/fota/"
        r = await c.put("/api/ota/devices/CUST1",
                        json={"env": "custom", "endpoint": url, "static_keys": KEYS})
        assert r.json()["summary"]["endpoint"] == url
        # a preset environment ignores a stray endpoint
        r = await c.put("/api/ota/devices/CUST1",
                        json={"env": "prod", "endpoint": url, "static_keys": KEYS})
        assert r.json()["summary"]["endpoint"] == "https://intouch.mapmyindia.in/fota/"
        await c.delete("/api/ota/devices/CUST1", params={"env": "custom"})
        await c.delete("/api/ota/devices/CUST1", params={"env": "prod"})


async def test_profile_rejects_bad_input():
    async with _client() as c:
        bad_key = await c.put("/api/ota/devices/X1", json={"env": "dev", "static_keys": ["XYZ"]})
        short_key = await c.put("/api/ota/devices/X1", json={"env": "dev", "static_keys": ["ABCD"]})
        bad_env = await c.put("/api/ota/devices/X1", json={"env": "staging", "static_keys": KEYS})
        assert bad_key.status_code == short_key.status_code == bad_env.status_code == 400


async def test_encode_then_decode_through_the_api():
    async with _client() as c:
        await c.put(f"/api/ota/devices/{IMEI}", json={"env": "prod", "static_keys": KEYS})
        enc = (await c.post("/api/ota/encode", json={
            "env": "prod", "imei": IMEI, "message_type": 1, "key_code": 1,
            "readable": '{"imei":"866685088308300"}'})).json()
        # the protocol document's own request, byte for byte
        assert enc["encrypted_request"] == (
            "$MMI,1,866685088308300,1,"
            "53209792C724CC82DA6540AFBCE37568032E97709D12825DE3F4BA5A5624AED7,47*")
        dec = (await c.post("/api/ota/decode", json={
            "env": "prod", "imei": IMEI, "sentence": enc["encrypted_request"]})).json()
        assert dec["decrypted"] == '{"imei":"866685088308300"}' and dec["checksum_ok"] is True
        await c.delete(f"/api/ota/devices/{IMEI}", params={"env": "prod"})


async def test_send_refuses_before_any_network_call():
    async with _client() as c:
        # a comm message with no comm keys and auto-keys off cannot be encrypted
        await c.put("/api/ota/devices/NOKEYS", json={"env": "dev", "static_keys": []})
        r = await c.post("/api/ota/send", json={"env": "dev", "imei": "NOKEYS", "message_type": 1})
        assert r.status_code == 400 and "static keys" in r.json()["detail"]
        # a custom profile with no URL says so
        await c.put("/api/ota/devices/NOURL", json={"env": "custom", "static_keys": KEYS})
        r = await c.post("/api/ota/send", json={"env": "custom", "imei": "NOURL", "message_type": 1})
        assert r.status_code == 400 and "endpoint" in r.json()["detail"]
        await c.delete("/api/ota/devices/NOKEYS", params={"env": "dev"})
        await c.delete("/api/ota/devices/NOURL", params={"env": "custom"})
