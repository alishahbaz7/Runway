"""AES-128 (ECB) + PKCS7 for the MapmyIndia InTouch OTA protocol.

The OTA dataBody is AES-128-ECB with PKCS7 padding, key = one 16-byte static or
communication key. Verified both directions against real traffic:

  * encrypt: plaintext ``{"imei":"866685088308300"}`` under static key
    ``1DA549D716E26C4242CB566D12D58CE9`` -> ``53209792...5624AED7`` (protocol doc).
  * decrypt: a live keys-response ciphertext under static key
    ``5CF5D8F6F1399A559A2667CCB46137C5`` -> ``{"comMap":{...},"status":200}`` ending
    in ``\\x02\\x02`` PKCS7 padding (Postman capture).

Deliberately dependency-free (pure Python) so ``runway/ota`` ships with no crypto
dependency and runs anywhere the rest of Runway runs. OTA is low-volume and
interactive, so speed is a non-issue. If a hot path ever needs it, swap the three
``aes_*`` functions for ``cryptography``'s AES-ECB behind this same interface.
"""

from __future__ import annotations

# AES S-box and its inverse (built once).
_SBOX = bytes.fromhex(
    "637c777bf26b6fc53001672bfed7ab76ca82c97dfa5947f0add4a2af9ca472c0"
    "b7fd9326363ff7cc34a5e5f171d8311504c723c31896059a071280e2eb27b275"
    "09832c1a1b6e5aa0523bd6b329e32f8453d100ed20fcb15b6acbbe394a4c58cf"
    "d0efaafb434d338545f9027f503c9fa851a3408f929d38f5bcb6da2110fff3d2"
    "cd0c13ec5f974417c4a77e3d645d197360814fdc222a908846eeb814de5e0bdb"
    "e0323a0a4906245cc2d3ac629195e479e7c8376d8dd54ea96c56f4ea657aae08"
    "ba78252e1ca6b4c6e8dd741f4bbd8b8a703eb5664803f60e613557b986c11d9e"
    "e1f8981169d98e949b1e87e9ce5528df8ca1890dbfe6426841992d0fb054bb16"
)
_INV_SBOX = bytearray(256)
for _i, _v in enumerate(_SBOX):
    _INV_SBOX[_v] = _i
_INV_SBOX = bytes(_INV_SBOX)

_RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36)


def _xtime(a: int) -> int:
    return ((a << 1) ^ 0x1B) & 0xFF if (a & 0x80) else (a << 1) & 0xFF


def _mul(a: int, b: int) -> int:
    """Multiply in GF(2^8)."""
    r = 0
    while b:
        if b & 1:
            r ^= a
        a = _xtime(a)
        b >>= 1
    return r & 0xFF


def _key_expansion(key: bytes) -> list[list[int]]:
    w = [list(key[i * 4:i * 4 + 4]) for i in range(4)]
    for i in range(4, 44):
        t = list(w[i - 1])
        if i % 4 == 0:
            t = t[1:] + t[:1]
            t = [_SBOX[b] for b in t]
            t[0] ^= _RCON[i // 4 - 1]
        w.append([w[i - 4][j] ^ t[j] for j in range(4)])
    return w


def _add_round_key(s, w, rnd):
    for c in range(4):
        for r in range(4):
            s[r][c] ^= w[rnd * 4 + c][r]


def _encrypt_block(block: bytes, w) -> bytes:
    s = [[block[r + 4 * c] for c in range(4)] for r in range(4)]
    _add_round_key(s, w, 0)
    for rnd in range(1, 10):
        for r in range(4):                                   # SubBytes
            for c in range(4):
                s[r][c] = _SBOX[s[r][c]]
        for r in range(1, 4):                                # ShiftRows
            s[r] = s[r][r:] + s[r][:r]
        for c in range(4):                                   # MixColumns
            a = [s[r][c] for r in range(4)]
            s[0][c] = _mul(a[0], 2) ^ _mul(a[1], 3) ^ a[2] ^ a[3]
            s[1][c] = a[0] ^ _mul(a[1], 2) ^ _mul(a[2], 3) ^ a[3]
            s[2][c] = a[0] ^ a[1] ^ _mul(a[2], 2) ^ _mul(a[3], 3)
            s[3][c] = _mul(a[0], 3) ^ a[1] ^ a[2] ^ _mul(a[3], 2)
        _add_round_key(s, w, rnd)
    for r in range(4):
        for c in range(4):
            s[r][c] = _SBOX[s[r][c]]
    for r in range(1, 4):
        s[r] = s[r][r:] + s[r][:r]
    _add_round_key(s, w, 10)
    return bytes(s[r][c] for c in range(4) for r in range(4))


def _decrypt_block(block: bytes, w) -> bytes:
    s = [[block[r + 4 * c] for c in range(4)] for r in range(4)]
    _add_round_key(s, w, 10)
    for rnd in range(9, 0, -1):
        for r in range(1, 4):                                # InvShiftRows
            s[r] = s[r][-r:] + s[r][:-r]
        for r in range(4):                                   # InvSubBytes
            for c in range(4):
                s[r][c] = _INV_SBOX[s[r][c]]
        _add_round_key(s, w, rnd)
        for c in range(4):                                   # InvMixColumns
            a = [s[r][c] for r in range(4)]
            s[0][c] = _mul(a[0], 14) ^ _mul(a[1], 11) ^ _mul(a[2], 13) ^ _mul(a[3], 9)
            s[1][c] = _mul(a[0], 9) ^ _mul(a[1], 14) ^ _mul(a[2], 11) ^ _mul(a[3], 13)
            s[2][c] = _mul(a[0], 13) ^ _mul(a[1], 9) ^ _mul(a[2], 14) ^ _mul(a[3], 11)
            s[3][c] = _mul(a[0], 11) ^ _mul(a[1], 13) ^ _mul(a[2], 9) ^ _mul(a[3], 14)
    for r in range(1, 4):
        s[r] = s[r][-r:] + s[r][:-r]
    for r in range(4):
        for c in range(4):
            s[r][c] = _INV_SBOX[s[r][c]]
    _add_round_key(s, w, 0)
    return bytes(s[r][c] for c in range(4) for r in range(4))


# ---- padding ---------------------------------------------------------------

def pkcs7_pad(data: bytes, block: int = 16) -> bytes:
    n = block - (len(data) % block)
    return data + bytes([n]) * n


def pkcs7_unpad(data: bytes, block: int = 16) -> bytes:
    if not data or len(data) % block:
        raise ValueError("ciphertext is not block-aligned")
    n = data[-1]
    if n < 1 or n > block or data[-n:] != bytes([n]) * n:
        raise ValueError("bad PKCS7 padding (wrong key?)")
    return data[:-n]


# ---- public API ------------------------------------------------------------

def aes_ecb_encrypt(key: bytes, data: bytes) -> bytes:
    """Encrypt block-aligned ``data`` with AES-128-ECB. Caller pads."""
    if len(key) != 16:
        raise ValueError(f"OTA keys are 16 bytes (AES-128); got {len(key)}")
    w = _key_expansion(key)
    return b"".join(_encrypt_block(data[i:i + 16], w) for i in range(0, len(data), 16))


def aes_ecb_decrypt(key: bytes, data: bytes) -> bytes:
    """Decrypt block-aligned ``data`` with AES-128-ECB. Caller unpads."""
    if len(key) != 16:
        raise ValueError(f"OTA keys are 16 bytes (AES-128); got {len(key)}")
    w = _key_expansion(key)
    return b"".join(_decrypt_block(data[i:i + 16], w) for i in range(0, len(data), 16))


def encrypt_text(key_hex: str, plaintext: str) -> str:
    """plaintext (UTF-8) -> uppercase hex of AES-128-ECB(PKCS7(plaintext))."""
    key = bytes.fromhex(key_hex)
    ct = aes_ecb_encrypt(key, pkcs7_pad(plaintext.encode("utf-8")))
    return ct.hex().upper()


def decrypt_text(key_hex: str, cipher_hex: str) -> str:
    """uppercase/lowercase hex ciphertext -> plaintext (UTF-8)."""
    key = bytes.fromhex(key_hex)
    pt = pkcs7_unpad(aes_ecb_decrypt(key, bytes.fromhex(cipher_hex)))
    return pt.decode("utf-8")
