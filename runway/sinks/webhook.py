"""Relay each normalized record to a platform as an HTTP POST (JSON)."""

from __future__ import annotations

import logging

import httpx

from ..core.interfaces import Sink
from ..core.models import UnifiedRecord
from ..core.registry import register_sink

log = logging.getLogger("runway.webhook")


@register_sink
class WebhookSink(Sink):
    name = "webhook"

    def __init__(self, url: str, timeout: float = 5.0, headers: dict | None = None):
        self.url = url
        self.timeout = float(timeout)
        self.headers = headers or {}
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=self.timeout, headers=self.headers)

    async def handle(self, record: UnifiedRecord) -> None:
        if self._client is None:
            return
        try:
            await self._client.post(self.url, json=record.model_dump(mode="json"))
        except Exception:
            log.exception("webhook POST to %s failed", self.url)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
