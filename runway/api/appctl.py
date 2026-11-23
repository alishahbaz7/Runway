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
    POST /api/desktop/titlebar the banner's colour for Runway's own window's title bar
    GET  /api/features         which pages this copy shows: the owner's policy, defaults, IMU by data
    POST /api/app/quit         stop this Runway, as the tray's Quit does — from this computer only (main.py,
                               starting the latest code, stops the older copy on its port this way)
"""

from __future__ import annotations

import asyncio
import re
import threading
from typing import Callable

from fastapi import APIRouter, HTTPException, Request
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


@router.get("/api/features")
async def features_status():
    from .. import features
    return await asyncio.to_thread(features.status)


_HEX = re.compile(r"#[0-9a-fA-F]{6}")


class TitleBarBody(BaseModel):
    background: str
    text: str
    dark: bool
    app: bool = False          # the page is Runway's own window (no toolbar), not a browser tab
    url: str = ""              # where it is, to reopen on


@router.post("/api/app/quit")
async def app_quit(request: Request):
    """Stop this Runway: listeners close, reports flush, the port frees. Only from this computer."""
    if not request.client or request.client.host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(403, "only from this computer")
    from ..update.service import shut_down
    asyncio.get_running_loop().call_later(0.3, shut_down)      # answer first, then stop
    return {"stopping": True}


@router.post("/api/desktop/titlebar")
async def desktop_titlebar(body: TitleBarBody, request: Request):
    """The page's banner colour and theme, for Runway's own window. The theme is remembered: the
    window opens in Edge's dark mode while it is dark (that is what darkens Edge's title bar); the
    colour goes to the title bar through Windows where Windows draws it. Only a page on this
    computer changes this computer's window."""
    if not (_HEX.fullmatch(body.background) and _HEX.fullmatch(body.text)):
        raise HTTPException(400, "Colours as #rrggbb")
    host = request.client.host if request.client else ""
    if host not in ("127.0.0.1", "::1", "localhost"):
        return {"windows": 0}
    from .. import desktop
    from ..storage.live import config
    theme = "dark" if body.dark else "light"
    try:
        if config.get("ui_theme", "dark") != theme:
            config.set("ui_theme", theme)
    except Exception:
        pass                                     # remembering it is a nicety, never an error
    n = await asyncio.to_thread(desktop.color_title_bars, body.background, body.text, body.dark)
    # the window's own dark mode is fixed when it opens: a different theme reopens it on the same page
    reopen = False
    if body.app and body.url.startswith(("http://127.0.0.1", "http://localhost")):
        from ..cli import _window
        w = _window()
        if w.get("profile_dir"):
            mode = await asyncio.to_thread(desktop.window_dark, w["profile_dir"])
            if mode is not None and mode != body.dark:
                reopen = True
                import threading
                threading.Thread(target=desktop.reopen_window, args=(body.url, w["profile_dir"], body.dark),
                                 daemon=True, name="window-reopen").start()
    return {"windows": n, "reopen": reopen}


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
