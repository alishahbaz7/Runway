"""InTouch OTA device emulator — Runway talks to the MapmyIndia OTA cloud as if it were a TCU.

Layers:
  * :mod:`runway.ota.crypto`    - AES-128-ECB / PKCS7 (pure Python, no deps).
  * :mod:`runway.ota.protocol`  - the ``$MMI`` frame, CRC-8, message-type table (pure).
  * :mod:`runway.ota.keystore`  - per-(environment, IMEI) static keys + comMap + expiry.
  * :mod:`runway.ota.client`    - HTTP transport + the key-lifecycle orchestration.
"""

from __future__ import annotations

from . import crypto, protocol
from .client import ENVIRONMENTS, OtaSession, Transaction
from .keystore import DeviceKeys

__all__ = [
    "crypto", "protocol",
    "ENVIRONMENTS", "OtaSession", "Transaction", "DeviceKeys",
]
