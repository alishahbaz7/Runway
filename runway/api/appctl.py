"""Usage reporting and self-update — the routes the page uses (the app-control and app-updates
skills). The work lives in ``runway/usage.py`` and ``runway/update/``; this only exposes it.

    GET  /api/usage            Settings → Usage reporting, and whether the first-start name is needed
    POST /api/usage/name       the name, asked once at the first start (or edited in Settings)
    POST /api/usage/send       Send now
    GET  /api/update           the update state the page shows (offer, chip, Settings)
    POST /api/update/check     Check now
    POST /api/update/offered   the offer was shown (once per run)
    POST /api/update/later     Later
    POST /api/update/install   download, verify, install beside, restart through the launcher
"""

from __future__ import annotations

import threading
from typing import Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import install, usage as usage_mod
from ..storage.db import DATA_DIR
from ..update.service import UpdateService

router = APIRouter()
updates: UpdateService | None = None


def start(running_listeners: Callable[[], int]) -> None:
    """At app start: open this run's usage session and check for an update, both in the
    background — nothing here waits for a network."""
    global updates
    usage_mod.usage.start()
    updates = UpdateService(data_dir=DATA_DIR, root=install.launcher_root() if install.FROZEN else None,
                            on_event=usage_mod.usage.record, running_listeners=running_listeners)
    updates.start()


def stop() -> None:
    usage_mod.usage.stop()                      # closes the session; waits 3 s at most
    from .. import desktop
    desktop.stop_tray()                         # take the icon away with the server, not after it


def _updates() -> UpdateService:
    if updates is None:
        raise HTTPException(503, "starting")
    return updates


class NameBody(BaseModel):
    name: str


@router.get("/api/usage")
async def usage_status():
    return usage_mod.status()


@router.post("/api/usage/name")
async def usage_name(body: NameBody):
    name = body.name.strip()
    if not name or len(name) > 80:
        raise HTTPException(400, "A name of 1 to 80 characters")
    usage_mod.usage.set_name(name)               # saved at once; sent in the background
    return usage_mod.status()


@router.post("/api/usage/send")
async def usage_send():
    threading.Thread(target=usage_mod.usage.flush, daemon=True).start()
    return {**usage_mod.status(), "sending": True}


@router.get("/api/update")
async def update_status():
    return _updates().status()


@router.post("/api/update/check")
async def update_check():
    u = _updates()
    u.check_async()
    return {**u.status(), "stage": "checking" if u.stage != "disabled" else "disabled"}


@router.post("/api/update/offered")
async def update_offered():
    u = _updates()
    u.mark_offered()
    return u.status()


@router.post("/api/update/later")
async def update_later():
    u = _updates()
    u.postpone()
    return u.status()


@router.post("/api/update/install")
async def update_install():
    return _updates().install_async()
