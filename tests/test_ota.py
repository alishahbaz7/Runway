"""InTouch OTA codec tests, pinned to real server-accepted vectors.

  * the protocol doc's encrypt example (plaintext <-> ciphertext under a static key),
  * a live keys-response ciphertext from the Postman capture (decrypt direction),
  * two server-accepted dataBody/checksum pairs (the CRC-8).
"""

import json

from runway.core.crc import crc8
from runway.ota import crypto, protocol
from runway.ota.keystore import DeviceKeys, EXPIRY_SKEW_MS, now_ms

# --- real vectors -----------------------------------------------------------
STATIC_KEY_1 = "1DA549D716E26C4242CB566D12D58CE9"
DOC_PLAINTEXT = '{"imei":"866685088308300"}'
DOC_CIPHERTEXT = "53209792C724CC82DA6540AFBCE37568032E97709D12825DE3F4BA5A5624AED7"
DOC_CHECKSUM = "47"

# A live type-1/2 response ciphertext (carries the comMap), decrypts with a static key.
RESP_KEY = "5CF5D8F6F1399A559A2667CCB46137C5"
RESP_CIPHERTEXT = (
    "A35B0DE772919DFF01CA6F2FF122D299BFA5E08C43D1E3A2F38D2271DD28F8A2"
    "5FF7AAAFA4E50B0617D75BE91CD52A0296BFDCBF5CF5EA106ECC3480CF102E63"
    "5A6F360400E9C68755BA9E9BA6680DE23EB07AD3A0DFAFE0607113DE78B48F224"
    "98C8666468F0F12E00F482F8894F30F211D8A23682B11CA12A7F39AA8E876CEC0"
    "6DB4CA8BB4A0429821820BEEEE05515E01896F7FCE93D321DBED1A321875879B4"
    "4623203232DDA46117FAB8FA9B022F440977D462BF16CA7704697898A77357AE5"
    "A6B2B5FE3B6201631C246A0FCE29545A90124D0F582BD5F7294C36400E3DA0AC2"
    "7C1B6617852FAD9CE9A9828F796A1B65611D70CEDF1E3620B4942DD190CD55C57"
    "AB17EA3792DB8E3359E24C92C3806172BAAE3261179E29E89B9089684BD9D2EE0"
    "122A91785D0071D75F96C3EC750AACC5411CA4A53151D142F22C6D4EF378AE341"
    "80D6870C37B72486D6A4604241546D37D40A7C34803AECE024A41526E7C78C9F1"
    "BAD3C48A98E3BA5B172C9B0661425DAC3E0A262BE3E46C5829F9F4D6991E8822D"
    "47149F2C0CBB5F48CB1B12"
)

# Two server-accepted (dataBody -> checksum) pairs.
CRC_SAMPLES = [
    ("BE88E0A39FC227F09F5A26C4163156F33CE4A5402EEB1924F37D510C2B8E473E", 0x49),
    (DOC_CIPHERTEXT, 0x47),
]


# --- crypto -----------------------------------------------------------------
def test_aes_ecb_encrypt_matches_doc_vector():
    assert crypto.encrypt_text(STATIC_KEY_1, DOC_PLAINTEXT) == DOC_CIPHERTEXT


def test_aes_ecb_decrypt_roundtrip():
    assert crypto.decrypt_text(STATIC_KEY_1, DOC_CIPHERTEXT) == DOC_PLAINTEXT


def test_aes_ecb_decrypt_live_keys_response():
    plain = crypto.decrypt_text(RESP_KEY, RESP_CIPHERTEXT)
    body = json.loads(plain)                       # valid JSON => PKCS7 unpad was correct
    assert body["status"] == 200
    assert len(body["comMap"]) == 8
    assert body["expireTimestamp"] == 1726115502778


def test_pkcs7_bad_padding_rejected():
    import pytest
    with pytest.raises(ValueError):
        crypto.decrypt_text("00" * 16, DOC_CIPHERTEXT)   # wrong key -> padding check fails


# --- checksum ---------------------------------------------------------------
def test_crc8_matches_server_accepted_samples():
    for body, want in CRC_SAMPLES:
        assert crc8(body.encode("ascii")) == want
        assert protocol.checksum(body) == f"{want:02X}"


# --- framing ----------------------------------------------------------------
def test_build_sentence_reproduces_doc_request():
    s = protocol.build_sentence(1, "866685088308300", 1, DOC_PLAINTEXT, STATIC_KEY_1)
    assert s.data_body == DOC_CIPHERTEXT
    assert s.check_sum == DOC_CHECKSUM
    assert s.render() == (
        "$MMI,1,866685088308300,1,"
        "53209792C724CC82DA6540AFBCE37568032E97709D12825DE3F4BA5A5624AED7,47*"
    )


def test_parse_sentence_roundtrip_and_checksum():
    text = "$MMI,6,2l,1,BE88E0A39FC227F09F5A26C4163156F33CE4A5402EEB1924F37D510C2B8E473E,49*"
    s = protocol.parse_sentence(text)
    assert s.message_type == 6
    assert s.device_serial == "2l"
    assert s.key_code == 1
    assert s.checksum_ok
    assert s.render() == text


def test_decode_body_roundtrip():
    s = protocol.build_sentence(3, "123", 1, '{"version":"7.5.1A"}', STATIC_KEY_1)
    assert protocol.decode_body(s, STATIC_KEY_1) == '{"version":"7.5.1A"}'


def test_key_source_by_type():
    assert protocol.key_source(1) == "static"
    assert protocol.key_source(2) == "static"
    for t in (3, 4, 5, 6):
        assert protocol.key_source(t) == "comm"


# --- key lifecycle ----------------------------------------------------------
def test_needs_keys_when_empty_expired_or_fresh():
    dk = DeviceKeys(imei="123", static_keys=[STATIC_KEY_1])
    assert dk.needs_keys()                                   # no comMap yet

    dk.apply_comm_response({"comMap": {"1": "AA" * 16}, "expireTimestamp": now_ms() - 1})
    assert dk.is_expired() and dk.needs_keys()               # already past expiry

    dk.apply_comm_response({"comMap": {"1": "AA" * 16},
                            "expireTimestamp": now_ms() + 10 * 60_000})
    assert not dk.needs_keys()                               # fresh, beyond the skew


def test_key_for_resolves_static_and_comm():
    dk = DeviceKeys(imei="123", static_keys=["11" * 16, "22" * 16, "33" * 16],
                    comm_map={"1": "AA" * 16, "2": "BB" * 16})
    assert dk.key_for("static", 2) == "22" * 16
    assert dk.key_for("comm", 1) == "AA" * 16


def test_expiry_skew_is_respected():
    dk = DeviceKeys(imei="123", comm_map={"1": "AA" * 16},
                    expire_ts=now_ms() + EXPIRY_SKEW_MS // 2)
    assert dk.is_expired()                                   # inside the skew window
