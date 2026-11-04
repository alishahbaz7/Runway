"""CAN signature binding: signature -> named engineering values on every decode path.

The signature format is the one the repository already stores (``data/signatures/*.json``).
Its key decides where it can bind: an 8-hex-digit CAN id binds to a bus frame; a short decimal
key is a Teltonika AVL parameter id and must NOT bind to a frame, or an AVL id like 900 would
silently invent signals for CAN id 0x900.
"""

from __future__ import annotations

import json

from runway.convert.can_signals import can_id_of, decode_frames, is_can_signature

# One real frame from the Zenithra capture, with the real signature entry for it.
FRAMES = [
    {"id": "00000120", "id_int": 0x120, "extended": False, "data": "0002FEA200000000", "dlc": 8},
    {"id": "0000012A", "id_int": 0x12A, "extended": False, "data": "0004AE38000000EA", "dlc": 8},
    {"id": "000005B9", "id_int": 0x5B9, "extended": False, "data": "0000000058590000", "dlc": 8},
]
SIG = {
    "00000120": [{"fieldName": "Odometer", "startingBit": 1, "size": 32,
                  "multiplier": 0.015625, "offset": 0, "signed": 0, "reverse": 0}],
    "0000012A": [{"fieldName": "DoorDriver", "startingBit": 22, "size": 2,
                  "multiplier": 1, "offset": 0, "signed": 0, "reverse": 0}],
    "00000999": [{"fieldName": "NeverSent", "startingBit": 0, "size": 8, "multiplier": 1}],
}


def test_can_id_normalisation():
    assert can_id_of("000000E1") == can_id_of("0xE1") == can_id_of("e1") == 225
    assert can_id_of("") is None and can_id_of("not-hex") is None


def test_avl_keyed_signature_never_binds_to_a_frame():
    """The guard that keeps a Teltonika signature off the bus."""
    avl = {"900": [{"fieldName": "canOdometer", "startingBit": 0, "size": 24, "multiplier": 1}]}
    assert is_can_signature(SIG) is True
    assert is_can_signature(avl) is False
    # 900 decimal == 0x384, so without the guard this frame would decode a bogus signal
    assert decode_frames([{"id": "00000384", "id_int": 0x384, "data": "1122334455667788"}], avl) == []


def test_decode_frames_scales_and_reports_provenance():
    out = decode_frames(FRAMES, SIG)
    by_name = {s["name"]: s for s in out}
    assert set(by_name) == {"Odometer", "DoorDriver"}      # id 999 isn't on the bus: skipped
    odo = by_name["Odometer"]
    assert odo["raw"] == 392516
    assert odo["value"] == 392516 * 0.015625               # physical = raw * mult + offset
    assert odo["can_id"] == "00000120"                     # which frame it came from
    assert by_name["DoorDriver"]["value"] == 2


def test_offset_is_added_not_subtracted():
    """Canonical DBC convention, the same one CanSignal.decode and the DBC export use — so a
    signature that means "subtract" must store a negative offset."""
    sig = {"00000120": [{"fieldName": "Signed", "startingBit": 0, "size": 16,
                         "multiplier": 0.01, "offset": -327.68}]}
    assert decode_frames([{"id": "00000120", "id_int": 0x120, "data": "8000000000000000"}],
                         sig)[0]["value"] == 0.0


def test_a_bad_row_does_not_lose_the_others():
    """A signature is hand-tuned data: one field that overruns the payload must not blank the
    rest of the frame's signals."""
    sig = {"00000120": [{"fieldName": "TooBig", "startingBit": 60, "size": 32, "multiplier": 1},
                        {"fieldName": "Fine", "startingBit": 0, "size": 8, "multiplier": 1}]}
    out = decode_frames(FRAMES, sig)
    assert [s["name"] for s in out] == ["Fine"]


def test_no_frames_or_no_signature_is_a_no_op():
    assert decode_frames([], SIG) == []
    assert decode_frames(FRAMES, None) == []
    assert decode_frames(FRAMES, {}) == []


# --------------------------------------------------------------------------- #
# Binding: bound once, decoded everywhere
# --------------------------------------------------------------------------- #
ZEN_CAN = (
    "$MMI,V2.2.0,5.1.29C,NR,1,L,868221073948609,DL1CAB1234,1,03082026,130441,28.550492,N,"
    "77.268890,E,0.0,0,30,223,0.91,0.49,IND airtel,1,1,13.5,4.1,O,31,404,10,946,E28130C,"
    "0000,00,0.7,0.0,00000120:0002FEA200000000;0000012A:0004AE38000000EA,(),000007*46"
)


async def _client():
    from httpx import ASGITransport, AsyncClient

    from runway.api.app import SIGNATURES_DIR, app

    SIGNATURES_DIR.mkdir(parents=True, exist_ok=True)
    (SIGNATURES_DIR / "test_sig.json").write_text(json.dumps(SIG), encoding="utf-8")
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_signature_named_explicitly_decodes_a_paste():
    async with await _client() as c:
        r = await c.post("/api/parse", json={"hex": ZEN_CAN, "signature": "test_sig.json"})
        pkt = r.json()["packets"][0]
        rows = [f for f in pkt["fields"] if f["group"].startswith("CAN signals")]
        assert [f["name"] for f in rows] == ["Odometer", "DoorDriver"]
        assert "test_sig.json" in rows[0]["group"]
        assert "CAN 00000120" in rows[0]["hint"]            # traceable back to its frame
        bad = await c.post("/api/parse", json={"hex": ZEN_CAN, "signature": "nope.json"})
        assert bad.status_code == 400


async def test_binding_a_device_decodes_without_naming_the_signature():
    async with await _client() as c:
        assert (await c.post("/api/parse", json={"hex": ZEN_CAN})).json()["packets"][0]
        # bind, then the same paste (and any live packet from that device) decodes
        put = await c.put("/api/signature-bindings",
                          json={"bindings": {"868221073948609": "test_sig.json"}})
        assert put.status_code == 200 and put.json()["saved"] is True

        d = await c.post("/api/parse", json={"hex": ZEN_CAN})
        pkt = d.json()["packets"][0]
        assert [f["name"] for f in pkt["fields"] if f["group"].startswith("CAN signals")] \
            == ["Odometer", "DoorDriver"]

        # the live path: the record in the store carries the signals, so every view sees them
        ing = await c.post("/api/ingest", json={"hex": ZEN_CAN, "peer": "bind-test"})
        assert ing.json()["records"] == 1
        rec = [r for r in (await c.get("/api/packets?limit=50")).json()
               if r["device_id"] == "868221073948609"][-1]
        assert rec["io"]["can_signal_count"] == 2
        assert rec["io"]["can_signature"] == "test_sig.json"

        # a different device is untouched by this binding
        assert (await c.get("/api/signature-bindings")).json()["bindings"] \
            == {"868221073948609": "test_sig.json"}
        await c.put("/api/signature-bindings", json={"bindings": {}})
        d2 = await c.post("/api/parse", json={"hex": ZEN_CAN})
        assert not [f for f in d2.json()["packets"][0]["fields"]
                    if f["group"].startswith("CAN signals")]


async def test_binding_rejects_an_unknown_signature():
    async with await _client() as c:
        r = await c.put("/api/signature-bindings", json={"bindings": {"123": "ghost.json"}})
        assert r.status_code == 400
        r2 = await c.put("/api/signature-bindings", json={"bindings": "nonsense"})
        assert r2.status_code == 400
