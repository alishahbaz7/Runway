"""The update flow the API and the page drive — BluePulse's UpdateService, in Python.

    disabled      updating does not apply here (a development run with no test channel)
    idle          not checked yet this run
    checking      reading latest.json (60 s at most, whatever the network does)
    up_to_date    nothing newer
    available     a newer version: offered once per run, then a chip
    downloading   streaming the zip into versions/.download, hashing as it goes
    installing    unpacking beside the running version
    restarting    starting the new version through the launcher, then this one shuts down
    failed        said only in Settings; a check or an install can be tried again

Silent: checking, downloading and failing never interrupt anyone. The running version is never
touched; nothing is installed unless the SHA-256 matches latest.json.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import threading
from datetime import datetime, timezone
from typing import Callable

from .. import __version__, install
from . import config, installer, manifest as mf, transport
from .semver import SemVer
from .transport import UpdateError

log = logging.getLogger("runway.update")

CHECK_TIMEOUT = 60.0
MARKER = "update.json"        # written before a restart; read by the next start


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class UpdateService:
    def __init__(self, *, data_dir: str, current: str = __version__, root: str | None = None,
                 manifest_url: str = config.MANIFEST_URL, enabled: bool | None = None,
                 get_text: Callable[[str], str] = transport.get_text,
                 download: Callable[..., str] = transport.download,
                 on_event: Callable[[str, dict], None] | None = None,
                 running_listeners: Callable[[], int] | None = None,
                 restart: Callable[[], None] | None = None,
                 check_timeout: float = CHECK_TIMEOUT):
        self.data_dir = data_dir
        self.current = SemVer.parse(current)
        self.root = root                           # the launcher install, or None
        self.manifest_url = manifest_url
        self._get_text, self._download = get_text, download
        self._on_event = on_event or (lambda kind, data: None)
        self._running_listeners = running_listeners or (lambda: 0)
        self._restart = restart or self._restart_through_launcher
        self.check_timeout = check_timeout
        on = config.enabled() if enabled is None else enabled
        self.disabled_reason = None if on else (config.disabled_reason() or "Updates are off")
        self.stage = "idle" if on else "disabled"
        self.manifest: mf.Manifest | None = None
        self.error: str | None = None
        self.last_checked: str | None = None
        self.received = 0
        self.total: int | None = None
        self.offered = False                       # the offer is shown once per run
        self.launcher_updated = False
        self._lock = threading.RLock()

    # ── what the page reads ───────────────────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        return self.manifest is not None and self.manifest.is_newer_than(self.current)

    @property
    def required(self) -> bool:
        return self.available and self.manifest.requires(self.current)

    @property
    def can_install_here(self) -> bool:
        """Only a launcher install updates in place; anywhere else Update opens the release page."""
        return self.root is not None

    @property
    def busy(self) -> bool:
        return self.stage in ("checking", "downloading", "installing", "restarting")

    def status(self) -> dict:
        with self._lock:
            m = self.manifest
            return {
                "stage": self.stage,
                "current": str(self.current),
                "latest": str(m.version) if m else None,
                "available": self.available,
                "required": self.required,
                "notes": m.notes if m and self.available else "",
                "published": m.published.isoformat() if m and m.published else None,
                "size": m.windows.size if m and m.windows else None,
                "received": self.received,
                "total": self.total,
                "error": self.error,
                "last_checked": self.last_checked,
                "offered": self.offered,
                "can_install_here": self.can_install_here,
                "releases_page": config.RELEASES_PAGE,
                "channel": self.manifest_url,
                "listeners": self._listeners(),
                "disabled_reason": self.disabled_reason,
            }

    def _listeners(self) -> int:
        try:
            return int(self._running_listeners())
        except Exception:
            return 0

    # ── start ─────────────────────────────────────────────────────────────────────────────────

    def start(self) -> None:
        """On app start: read the marker a restart left, tidy old versions, check in the background."""
        self._read_marker()
        if self.stage == "disabled":
            return
        if self.root:
            try:
                removed = installer.cleanup(self.root, self.current)
                if removed:
                    log.info("update: removed old versions %s", ", ".join(removed))
            except Exception:
                log.exception("update: cleanup failed")     # never let housekeeping stop the app
        self.check_async()

    def _read_marker(self) -> None:
        path = os.path.join(self.data_dir, MARKER)
        try:
            with open(path, encoding="utf-8") as f:
                marker = json.load(f)
        except (OSError, ValueError):
            return
        try:
            os.remove(path)
        except OSError:
            pass
        if not isinstance(marker, dict):
            return
        to, frm = marker.get("to"), marker.get("from")
        if to == str(self.current):
            self._on_event("update_installed", {"from": frm, "to": to})
            log.info("update: now running %s (was %s)", to, frm)
        else:
            self._on_event("update_failed", {"from": frm, "to": to, "reason": f"started {self.current}, not {to}"})
            log.warning("update: expected to start %s but %s started", to, self.current)

    # ── check ─────────────────────────────────────────────────────────────────────────────────

    def check_async(self) -> None:
        threading.Thread(target=self.check, name="update-check", daemon=True).start()

    def check(self) -> None:
        """Asks the channel for the latest version. Whatever happens it ends — up to date,
        available or failed — within CHECK_TIMEOUT, and this version keeps running."""
        with self._lock:
            if self.stage == "disabled" or self.busy:
                return
            self.stage, self.error = "checking", None
        result: dict = {}

        def fetch() -> None:
            try:
                result["text"] = self._get_text(self.manifest_url)
            except BaseException as e:      # noqa: BLE001 — everything ends as a failure
                result["error"] = e

        worker = threading.Thread(target=fetch, daemon=True)
        worker.start()
        worker.join(self.check_timeout)
        failure = None
        parsed = None
        if worker.is_alive():
            failure = f"{_host(self.manifest_url)} did not answer in time"
        elif "error" in result:
            e = result["error"]
            if isinstance(e, UpdateError):
                failure = "No release published yet" if "HTTP 404" in str(e) else str(e)
            else:
                failure = f"Could not check for updates ({type(e).__name__})"
        else:
            try:
                parsed = mf.parse(result.get("text", ""), base=self.manifest_url, expected_app=install.APP_NAME)
            except mf.ManifestError as e:
                failure = str(e)
            except Exception as e:
                failure = f"latest.json could not be read ({type(e).__name__})"
        with self._lock:
            self.last_checked = _now()
            if failure:
                self.stage, self.error = "failed", failure
            else:
                self.manifest = parsed
                self.stage = "available" if self.available else "up_to_date"
        if failure:
            log.info("update: check failed — %s", failure)
            self._on_event("update_check_failed", {"reason": failure[:200]})

    # ── the offer ─────────────────────────────────────────────────────────────────────────────

    def mark_offered(self) -> None:
        with self._lock:
            if self.offered or not self.available:
                return
            self.offered = True
        self._on_event("update_offered", {"version": str(self.manifest.version), "required": self.required})

    def postpone(self) -> None:
        if self.available and not self.required:
            self._on_event("update_postponed", {"version": str(self.manifest.version)})

    # ── install and restart ───────────────────────────────────────────────────────────────────

    def install_async(self) -> dict:
        """Starts the download and install in the background. Not a launcher install: nothing to
        do here — the page opens the release page instead."""
        with self._lock:
            if not self.available or self.busy:
                return self.status()
            if not self.can_install_here:
                return {**self.status(), "open": config.RELEASES_PAGE}
            self.stage, self.error = "downloading", None
            self.received, self.total = 0, (self.manifest.windows.size if self.manifest.windows else None)
        threading.Thread(target=self._install, name="update-install", daemon=True).start()
        return self.status()

    def _install(self) -> None:
        m = self.manifest
        a = m.windows if m else None
        version = str(m.version) if m else "?"
        self._on_event("update_started", {"from": str(self.current), "to": version})
        part = None
        try:
            if a is None:
                raise UpdateError(f"Release {version} has no Windows package")
            staging = installer.staging_dir(self.root)
            os.makedirs(staging, exist_ok=True)
            part = os.path.join(staging, a.file + ".part")

            def progress(received: int, total: int | None) -> None:
                self.received, self.total = received, total or a.size

            sha = self._download(a.url, part, on_progress=progress)
            if sha != a.sha256:
                raise UpdateError("The download does not match its checksum; nothing was installed")
            with self._lock:
                self.stage = "installing"
            result = installer.install_from_zip(part, self.root, version)
            self.launcher_updated = result.launcher_updated
            log.info("update: %s installed beside %s%s", version, self.current,
                     " (launcher refreshed)" if result.launcher_updated else "")
        except UpdateError as e:
            self._fail(str(e), version)
            return
        except OSError as e:
            self._fail(f"Could not write the update ({e.strerror or type(e).__name__}); this version is unchanged", version)
            return
        except Exception as e:
            log.exception("update: install failed")
            self._fail(f"The update stopped ({type(e).__name__}); this version is unchanged", version)
            return
        finally:
            if part and os.path.exists(part):
                try:
                    os.remove(part)
                except OSError:
                    pass
        self.restart_into(version)

    def _fail(self, message: str, version: str) -> None:
        with self._lock:
            self.stage, self.error = "failed", message
        log.warning("update: %s", message)
        self._on_event("update_failed", {"from": str(self.current), "to": version, "reason": message[:200]})

    def restart_into(self, version: str) -> None:
        """Leaves a marker for the next start, starts the launcher (which picks the newest version)
        and shuts this server down so the new one can take the port."""
        with self._lock:
            self.stage = "restarting"
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            with open(os.path.join(self.data_dir, MARKER), "w", encoding="utf-8") as f:
                json.dump({"from": str(self.current), "to": version, "at": _now()}, f)
            self._restart()
        except Exception as e:
            log.exception("update: restart failed")
            with self._lock:
                self.stage = "failed"
                self.error = f"Could not restart ({type(e).__name__}); the new version starts next time"

    def _restart_through_launcher(self) -> None:
        launcher = os.path.join(self.root, install.LAUNCHER_EXE)
        if not os.path.isfile(launcher):
            raise UpdateError(f"{install.LAUNCHER_EXE} is not in {self.root}")
        args = install.SERVE_ARGS or ["serve"]
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen([launcher, "--after-update", *args, "--wait-port", "60"],
                         cwd=self.root, creationflags=flags, close_fds=True)
        log.info("update: launcher started; shutting down for the new version")
        _daemon_timer(0.8, shut_down)


def _daemon_timer(seconds: float, fn: Callable[[], None]) -> None:
    t = threading.Timer(seconds, fn)
    t.daemon = True                    # never holds the process open
    t.start()


def shut_down() -> None:
    """Asks the server to stop as Ctrl+C would — listeners close, reports flush — and makes sure
    the process ends even if something hangs. Also what the tray's Quit Runway does."""
    _daemon_timer(20.0, lambda: os._exit(0))
    try:
        signal.raise_signal(signal.SIGINT)
    except Exception:
        os._exit(0)


def _host(url: str) -> str:
    from urllib.parse import urlsplit
    return urlsplit(url).hostname or url
