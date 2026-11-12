"""Usage reporting for a Python app (app-control skill). Standard library only.

Copy this file into the app (for example `myapp/usage_client.py`) and use it like this:

    from .usage_client import UsageReporter
    usage = UsageReporter(app="InTouch OTA Analytics", version=__version__, data_dir=DATA_DIR,
                          enabled=(CHANNEL == "release"), build=CHANNEL)
    usage.start()                        # once at startup; sends in the background
    if usage.needs_name: ...             # ask the name once (first-start dialog) → usage.set_name(name)
    usage.page("jobs")                   # a page or view was opened
    usage.record("job_started", {"devices": 140})
    usage.record_error(exc)              # from the app's own unhandled-error hook
    usage.metrics = lambda: {"jobsStarted": jobs}   # optional: this app's own counters
    usage.status()                       # for Settings → Usage reporting

Reports go to the shared usage server — one Supabase project for every app — in the format of
references/report-format.md. Nothing here may slow the app or stop it: sending happens on a
background thread, a failure keeps the report for the next try, and closing waits 3 s at most.
"""
from __future__ import annotations

import atexit
import json
import os
import platform
import re
import secrets
import socket
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

# The shared server. The key is public by design: it can call usage_report and nothing else.
URL = "https://zjudpkovgkavarcjxmmp.supabase.co/rest/v1/rpc/usage_report"
KEY = "sb_publishable_s5NBH9RdIC7-avJsjHc8jQ_TNFWjhw9"

TICK = 60.0               # seconds between measurements
SEND_EVERY = 300.0        # seconds between sends while running
MAX_EVENTS = 500          # kept while offline, oldest out first
MAX_SESSIONS = 100
EVENTS_PER_SEND = 200
MAX_ERRORS = 10           # reported in full per session; all are counted

_ID = re.compile(r"[0-9a-f]{32}")
_KIND = re.compile(r"[a-z_]{1,40}")
_JWT = re.compile(r"eyJ[\w-]*\.[\w-]+\.[\w-]+")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def new_id() -> str:
    """A random 128-bit id as 32 lower-case hex digits."""
    return secrets.token_hex(16)


def is_id(value: Any) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None


class SendError(Exception):
    """A report that did not reach the server. `rejected`: the server refused the report itself
    (HTTP 400/422) — sending it again cannot succeed, so it is dropped."""

    def __init__(self, message: str, rejected: bool = False):
        super().__init__(message)
        self.message = message
        self.rejected = rejected


def send(url: str, key: str, report: dict, timeout: float = 20.0, user_agent: str = "usage-client") -> Any:
    """POSTs one report; returns the server's JSON answer or raises SendError."""
    req = urllib.request.Request(url, data=json.dumps(report).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": user_agent})
    if key:
        req.add_header("apikey", key)
        if _JWT.fullmatch(key):  # a legacy anon key also goes here; a publishable key must not
            req.add_header("Authorization", f"Bearer {key}")
    host = urllib.parse.urlsplit(url).hostname or url
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read().decode("utf-8", "replace")
        try:
            return json.loads(text) if text else None
        except ValueError:
            return None
    except urllib.error.HTTPError as e:
        reason = ""
        try:
            message = json.loads(e.read().decode("utf-8", "replace")).get("message")
            if message:
                reason = f": {message[:120]}"
        except Exception:
            pass  # not JSON: the status says enough — never echo the request, it holds the key
        raise SendError(f"HTTP {e.code} from {host}{reason}", rejected=e.code in (400, 422)) from None
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError):
        raise SendError(f"No connection to {host}") from None


# ── What the machine tells about itself, without asking the person ────────────────────────────────

def device_facts() -> dict:
    system = platform.system().lower() or "unknown"  # windows, linux, darwin
    facts: dict = {"platform": system, "timeZone": _time_zone()}
    if system == "windows":
        user, domain = os.environ.get("USERNAME"), os.environ.get("USERDOMAIN")
        computer = os.environ.get("COMPUTERNAME") or platform.node()
        # A domain account is named with its domain; a local one by the user name alone.
        facts["login"] = f"{domain}\\{user}" if user and domain and domain.upper() != computer.upper() else user
        facts["computer"] = computer
        facts["os"] = windows_name()
    else:
        try:
            import getpass
            facts["login"] = getpass.getuser()
        except Exception:
            facts["login"] = None
        facts["computer"] = platform.node() or None
        facts["os"] = platform.platform(terse=True)
    return facts


def windows_name() -> str:
    """'Windows 11 Pro (build 26300)'. Windows 11 still reports release 10 to older Pythons; the
    build number tells them apart (22000 and later is 11)."""
    try:
        build = int(platform.version().split(".")[-1])
    except ValueError:
        build = 0
    name = "Windows 11" if build >= 22000 else f"Windows {platform.release()}"
    raw = getattr(platform, "win32_edition", lambda: None)() or ""
    edition = {"Professional": "Pro", "Core": "Home"}.get(raw, raw)
    return f"{name} {edition}".strip() + (f" (build {build})" if build else "")


def _time_zone() -> str:
    offset = datetime.now().astimezone().utcoffset()
    minutes = int(offset.total_seconds() // 60) if offset else 0
    sign = "-" if minutes < 0 else "+"
    name = time.tzname[1 if time.daylight and time.localtime().tm_isdst else 0]
    return f"{name} (UTC{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d})"


# ── The reporter ──────────────────────────────────────────────────────────────────────────────────

class UsageReporter:
    """Reports who uses this copy and how: the install, each start (a session), and events.
    Reports wait in `<data_dir>/usage.json` until the server has them."""

    def __init__(self, app: str, version: str, data_dir: os.PathLike | str, *, enabled: bool = True,
                 build: str = "release", url: str = URL, key: str = KEY,
                 transport: Callable[..., Any] = send, clock: Callable[[], datetime] = _utcnow,
                 facts: Optional[dict] = None, tick: float = TICK, send_every: float = SEND_EVERY):
        self.app, self.version, self.build = app, version, build
        self.url, self.key = url, key
        self.enabled = bool(enabled and url)
        self.metrics: Optional[Callable[[], dict]] = None
        self.session: Optional[dict] = None
        self.last_sent: Optional[str] = None
        self.error: Optional[str] = None
        self.sending = False
        self._send, self._clock, self._facts = transport, clock, facts
        self._tick_s, self._send_every = tick, send_every
        self._path = Path(data_dir) / "usage.json"
        self._lock = threading.RLock()
        self._state: dict = {}
        self._install: dict = {}
        self._open = 0.0
        self._last_tick: Optional[datetime] = None
        self._last_attempt: Optional[datetime] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ── Life cycle ────────────────────────────────────────────────────────────────────────────

    def start(self, *, background: bool = True, at_exit: bool = True) -> None:
        """Opens this run's session. Sends at once and then every few minutes on a background
        thread; at exit closes the session and sends it."""
        if not self.enabled or self.session is not None:
            return
        try:
            facts = self._facts if self._facts is not None else device_facts()
        except Exception:
            facts = {"platform": "unknown"}
        with self._lock:
            self._state = self._load()
            first = not is_id(self._state.get("installId"))
            if first:
                self._state["installId"] = new_id()
            box = self._state.get("outbox")
            if not (isinstance(box, dict) and isinstance(box.get("sessions"), dict) and isinstance(box.get("events"), list)):
                self._state["outbox"] = {"sessions": {}, "events": []}
            self._install = {**facts, "id": self._state["installId"], "app": self.app,
                             "appVersion": self.version, "build": self.build}
            now = self._clock()
            self.session = {"id": new_id(), "appVersion": self.version, "startedAt": _iso(now),
                            "lastActiveAt": _iso(now), "endedAt": None, "openSeconds": 0,
                            "pages": {}, "errors": 0, "metrics": {}}
            self._last_tick = now
            if first:
                self.record("first_start")
            previous = self._state.get("lastVersion")
            if previous and previous != self.version:
                self.record("version_changed", {"from": previous, "to": self.version})
            self._state["lastVersion"] = self.version
            self.last_sent = self._state.get("lastSent")
            self._put_session()
            self._save()
        if at_exit:
            atexit.register(self.stop)
        if background:
            self._thread = threading.Thread(target=self._run, name="usage-reporter", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        self.flush()
        while not self._stop.wait(self._tick_s):
            self.tick()

    def tick(self) -> None:
        """Measures the session since the last tick, saves it, and sends when due."""
        if not self.enabled or self.session is None or self.session["endedAt"]:
            return
        with self._lock:
            self._measure()
            self._put_session()
            self._save()
        last = self._last_attempt
        if last is None or (self._clock() - last).total_seconds() >= self._send_every:
            self.flush()

    def stop(self) -> None:
        """The app is closing: ends the session and sends it, waiting 3 s at most."""
        if not self.enabled or self.session is None or self.session["endedAt"]:
            return
        self._stop.set()
        with self._lock:
            self._measure()
            self.session["endedAt"] = _iso(self._clock())
            self._put_session()
            self._save()
        self.flush(timeout=3.0)

    def _measure(self) -> None:
        now = self._clock()
        elapsed = max((now - (self._last_tick or now)).total_seconds(), 0.0)
        self._last_tick = now
        if elapsed > self._tick_s * 2:  # a gap far longer than a tick is a sleeping computer
            elapsed = self._tick_s
        self._open += elapsed
        s = self.session
        s["openSeconds"] = int(self._open)
        s["lastActiveAt"] = _iso(now)
        if self.metrics is not None:
            try:
                m = self.metrics()
                if isinstance(m, dict):
                    s["metrics"] = m
            except Exception:
                pass  # the app's counters failing must not stop the report

    # ── What the app tells it ─────────────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return (self._state.get("name") or "").strip()

    @property
    def needs_name(self) -> bool:
        """The person has not given a name yet: ask once, before anything else."""
        return self.enabled and self.session is not None and not self.name

    def set_name(self, name: str, *, send_now: bool = True) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._state["name"] = name.strip()[:120]
            self._save()
        if send_now:
            threading.Thread(target=self.flush, daemon=True).start()

    def page(self, name: str) -> None:
        """A page or view was opened."""
        if not self.enabled or self.session is None:
            return
        with self._lock:
            pages = self.session["pages"]
            pages[name] = pages.get(name, 0) + 1

    def record(self, kind: str, data: Optional[dict] = None) -> None:
        """Something happened — `kind` in lower case with underscores. Kept until sent."""
        if not self.enabled or self.session is None or not _KIND.fullmatch(kind):
            return
        with self._lock:
            events = self._state["outbox"]["events"]
            events.append({"id": new_id(), "sessionId": self.session["id"], "at": _iso(self._clock()),
                           "kind": kind, "data": data or {}})
            del events[:-MAX_EVENTS]
            self._save()

    def record_error(self, exc: BaseException) -> None:
        """An error the app did not handle: counted always, reported in full for the first few."""
        if not self.enabled or self.session is None:
            return
        with self._lock:
            self.session["errors"] += 1
            if self.session["errors"] > MAX_ERRORS:
                return
        message = f"{type(exc).__name__}: {exc}"
        stack = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip().splitlines()[-8:]
        self.record("error", {"message": message[:300], "stack": "\n".join(stack)})

    # ── Sending ───────────────────────────────────────────────────────────────────────────────

    def flush(self, timeout: Optional[float] = None) -> None:
        """Sends everything waiting, in batches. A failure keeps it for the next try, except a
        report the server refuses outright, which is dropped so it cannot block the rest."""
        if not self.enabled or self.session is None:
            return
        while True:
            with self._lock:
                if self.sending:
                    return
                self.sending = True
                self._last_attempt = self._clock()
                box = self._state["outbox"]
                sessions = {k: json.loads(json.dumps(v)) for k, v in box["sessions"].items()}
                events = [dict(e) for e in box["events"][:EVENTS_PER_SEND]]
                install = {**self._install, "name": self.name or None}
            more = False
            try:
                self._send(self.url, self.key, {"install": install, "sessions": list(sessions.values()), "events": events},
                           timeout=timeout or 20.0, user_agent=f"{self.app}/{self.version}")
                with self._lock:
                    self._acknowledge(sessions, events)
                    self.last_sent = _iso(self._clock())
                    self._state["lastSent"] = self.last_sent
                    self.error = None
                    more = bool(box["events"]) and len(events) == EVENTS_PER_SEND
            except SendError as e:
                with self._lock:
                    if e.rejected:
                        self._acknowledge(sessions, events)
                        self.error = f"{e.message} (report dropped)"
                    else:
                        self.error = e.message
            finally:
                with self._lock:
                    self.sending = False
                    self._save()
            if not more:
                return

    def _acknowledge(self, sessions: dict, events: list) -> None:
        box = self._state["outbox"]
        for sid, snapshot in sessions.items():
            if box["sessions"].get(sid) == snapshot:  # unchanged while it was on its way
                del box["sessions"][sid]
        sent = {e["id"] for e in events}
        box["events"] = [e for e in box["events"] if e["id"] not in sent]

    def _put_session(self) -> None:
        box = self._state["outbox"]["sessions"]
        box.pop(self.session["id"], None)  # re-inserted last: most recently changed last
        box[self.session["id"]] = json.loads(json.dumps(self.session))
        while len(box) > MAX_SESSIONS:
            del box[next(iter(box))]

    # ── For Settings → Usage reporting ───────────────────────────────────────────────────────

    def status(self) -> dict:
        with self._lock:
            box = self._state.get("outbox") or {"sessions": {}, "events": []}
            current = self.session["id"] if self.session else None
            waiting = len(box["events"]) + len([k for k in box["sessions"] if k != current])
            return {"enabled": self.enabled, "name": self.name, "install_id": self._state.get("installId"),
                    "reporting_to": urllib.parse.urlsplit(self.url).hostname, "last_sent": self.last_sent,
                    "sending": self.sending, "waiting": waiting, "error": self.error}

    # ── The file ──────────────────────────────────────────────────────────────────────────────

    def _load(self) -> dict:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}  # missing or damaged: start a fresh one rather than stop the app

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._state), encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError:
            pass  # a read-only folder must never take the app down; reports then last this run
