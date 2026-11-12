"""HTTP transport + key-lifecycle orchestration for the InTouch OTA protocol.

Runway here plays the *device*: it dials out to the MMI OTA cloud, sends ``$MMI`` request
sentences and reads the cloud's responses - the mirror of Runway's usual "land packets from
devices" role.

One operator action can become a *chain*: sending a type-3 with no (or expired) comm key first
triggers a type-1/2 key download, then retries. Each hop is a :class:`Transaction` (the four
panes: readable request -> encrypted request -> encrypted response -> decrypted response), and
:meth:`OtaSession.send` returns the whole chain so the UI timeline can show it.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Optional

from . import protocol
from .keystore import DeviceKeys, now_ms

# Base-URL presets. They differ in scheme AND path, so this is a full base URL, not a host.
ENVIRONMENTS: dict[str, str] = {
    "dev": "http://intouchdev.mapmyindia.com/IntouchFOTACOTA/",
    "prod": "https://intouch.mapmyindia.in/fota/",
}

# A type-9 is a generic NACK. Only re-download keys when the error looks key-related, and only
# once, so a device erroring for some other reason can't spin in a fetch-keys loop.
_KEY_ERROR_HINTS = ("key", "decrypt", "comm", "expire")

_HEADERS = {"Content-Type": "application/json"}


def _mask(key_hex: str) -> str:
    if not key_hex or len(key_hex) < 8:
        return "****"
    return f"{key_hex[:4]}…{key_hex[-4:]}"


@dataclass
class Transaction:
    """One request/response hop - the four panes plus what happened."""

    step: str                                  # "keys" | "message"
    message_type: int
    type_label: str
    key_source: str                            # "static" | "comm"
    key_code: int
    key_masked: str
    endpoint: str
    readable_request: str                      # pane 1
    encrypted_request: str                     # pane 2 (the $MMI,...* we sent)
    encrypted_response: str = ""               # pane 3 (raw text from the cloud)
    decrypted_response: str = ""               # pane 4
    http_status: Optional[int] = None
    response_type: Optional[int] = None
    response_checksum_ok: Optional[bool] = None
    ok: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class OtaSession:
    """Drives one device's OTA exchange against one environment, holding its keys."""

    def __init__(self, base_url: str, keys: DeviceKeys, *, timeout: float = 20.0):
        self.base_url = base_url
        self.keys = keys
        self.timeout = timeout

    # ---- transport ----
    async def _post(self, sentence_text: str) -> tuple[int, str]:
        # httpx is imported here, not at module load, so the app (and local encode/decode)
        # work even where httpx is not installed; only a live send needs it.
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("httpx is required for live OTA sends — "
                               "run: pip install httpx") from exc
        async with httpx.AsyncClient(timeout=self.timeout) as http:
            r = await http.post(self.base_url, content=sentence_text.encode("utf-8"),
                                 headers=_HEADERS)
            return r.status_code, r.text

    # ---- one hop ----
    async def _exchange(self, step: str, message_type: int, key_source: str,
                        key_code: int, body_obj) -> Transaction:
        readable = body_obj if isinstance(body_obj, str) else json.dumps(body_obj)
        key_hex = self.keys.key_for(key_source, key_code)
        sent = protocol.build_sentence(message_type, self.keys.imei, key_code, readable, key_hex)
        tx = Transaction(
            step=step, message_type=message_type,
            type_label=protocol.MESSAGE_TYPES.get(message_type, f"type {message_type}"),
            key_source=key_source, key_code=key_code, key_masked=_mask(key_hex),
            endpoint=self.base_url, readable_request=readable,
            encrypted_request=sent.render(),
        )
        try:
            status, text = await self._post(sent.render())
        except Exception as exc:                       # network / DNS / TLS
            tx.note = f"send failed: {exc}"
            return tx
        tx.http_status = status
        tx.encrypted_response = text.strip()

        try:
            resp = protocol.parse_sentence(text)
        except ValueError:
            tx.note = "response is not an $MMI sentence (shown raw in pane 3)"
            return tx
        tx.response_type = resp.message_type
        tx.response_checksum_ok = resp.checksum_ok
        # The response's own keyCode selects the key that decrypts it; the key *set* follows
        # the response's message type (a type-9/keys reply rides a static key).
        resp_source = protocol.key_source(resp.message_type)
        try:
            resp_key = self.keys.key_for(resp_source, resp.key_code)
            tx.decrypted_response = protocol.decode_body(resp, resp_key)
            tx.ok = resp.message_type != 9
        except (KeyError, ValueError) as exc:
            tx.note = f"could not decrypt response: {exc}"
        return tx

    # ---- public flows ----
    async def download_keys(self, *, message_type: int = 1, key_code: int = 1) -> Transaction:
        """Send a type-1 (or type-2) keys request and fold the comMap into the key store."""
        tx = await self._exchange("keys", message_type, "static", key_code,
                                  {"ReqVersion": "1", "imei": self.keys.imei})
        if tx.ok and tx.decrypted_response:
            try:
                self.keys.apply_comm_response(json.loads(tx.decrypted_response))
                tx.note = (f"stored {len(self.keys.comm_map)} comm keys, "
                           f"expire {self.keys.expire_ts}")
            except (ValueError, json.JSONDecodeError) as exc:
                tx.ok = False
                tx.note = f"keys response not understood: {exc}"
        return tx

    async def send(self, message_type: int, body_obj, *, key_code: int = 1,
                   auto_keys: bool = True) -> list[Transaction]:
        """Send a message, downloading/refreshing comm keys first when needed.

        Returns the full chain of hops (one entry if no key download was needed).
        """
        chain: list[Transaction] = []
        source = protocol.key_source(message_type)

        if source == "comm" and auto_keys and self.keys.needs_keys():
            refresh_type = 2 if self.keys.has_comm_keys() else 1
            chain.append(await self.download_keys(message_type=refresh_type))
            if not chain[-1].ok:
                return chain                           # couldn't get keys; stop before the real send

        tx = await self._exchange("message", message_type, source, key_code, body_obj)
        chain.append(tx)

        # Type-9 that looks key-related: re-key once, then retry the message.
        if (tx.response_type == 9 and source == "comm" and auto_keys
                and _looks_key_related(tx.decrypted_response)):
            chain.append(await self.download_keys(message_type=2))
            if chain[-1].ok:
                chain.append(await self._exchange("message", message_type, source,
                                                  key_code, body_obj))
        return chain


def _looks_key_related(decrypted: str) -> bool:
    low = (decrypted or "").lower()
    return any(h in low for h in _KEY_ERROR_HINTS)
