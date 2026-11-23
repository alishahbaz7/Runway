"""Runway on the Windows desktop — so it opens like an app, though it is a server underneath.

    open_app_window(url)   Runway in its own window (Edge's app mode: no address bar, no tabs,
                           Runway's title and icon), not a browser tab; a browser tab if no Edge
    focus_window()         bring an open Runway window to the front (a second start does this)
    Tray                   the icon by the clock: Open Runway · Logs · Quit Runway. A release build
                           has no console window, so this is how Runway is seen running and stopped
    message_box(...)       a Windows message box (the quit confirmation, a start-up failure)

Why a server in a window and not a native app: listeners, relays and storage run in the server,
so closing the window leaves them running (24x7 relays), other PCs on the LAN can open it, and it
can run with no screen at all. The window and the tray make it feel like BluePulse anyway.

Standard library only (ctypes, winreg). Off Windows every function quietly does nothing useful:
the URL is opened in a browser tab and there is no tray.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import webbrowser
from typing import Callable

IS_WINDOWS = sys.platform == "win32"
WINDOW_TITLE_PREFIX = "Runway · "          # every page's <title>: "Runway · Dashboard", …


# ── the app window ────────────────────────────────────────────────────────────────────────────────

def find_browser() -> str | None:
    """Edge (on every Windows 10/11), else Chrome — both have an app mode."""
    if not IS_WINDOWS:
        return None
    import winreg

    for exe in ("msedge.exe", "chrome.exe"):
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as k:
                    path = winreg.QueryValue(k, None)
                if path and os.path.isfile(path.strip('"')):
                    return path.strip('"')
            except OSError:
                pass
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        for rel in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
            if base and os.path.isfile(os.path.join(base, rel)):
                return os.path.join(base, rel)
    return None


def app_window_command(browser: str, url: str, profile_dir: str | None = None, dark: bool | None = None) -> list[str]:
    """Edge's app mode. With profile_dir, Runway's window runs in its own Edge profile (its own
    process), so its options apply even while the person's Edge is open — a window opened by a
    running Edge takes that Edge's options. dark: Edge's dark mode, which is what gives the window
    a dark title bar (an app window ignores the page's theme-color)."""
    cmd = [browser, f"--app={url}", "--window-size=1440,900"]
    if profile_dir:
        # its own profile never signs in or syncs: a new profile otherwise opens Edge's "We are now
        # syncing your browsing data" over Runway's first window
        cmd += [f"--user-data-dir={profile_dir}", "--no-first-run", "--no-default-browser-check", "--disable-sync"]
    if dark:
        cmd.append("--force-dark-mode")
    return cmd


def open_app_window(url: str, profile_dir: str | None = None, dark: bool | None = None) -> bool:
    """Opens Runway in its own window. Returns False when it fell back to a browser tab."""
    _window_dark.pop(profile_dir or "", None)           # a window already open keeps its own mode: ask again
    browser = find_browser()
    if browser:
        try:
            flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            subprocess.Popen(app_window_command(browser, url, profile_dir, dark), creationflags=flags, close_fds=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except OSError:
            pass
    try:
        webbrowser.open(url)
    except Exception:
        pass          # no browser at all (a headless server): the URL is in the log
    return False


# ── the window's dark mode follows the theme ────────────────────────────────────────────────────────
# Edge fixes --force-dark-mode for the whole process of a profile: a theme switched in the page cannot
# change it, and another window of that profile joins the same process. So when the theme and the
# window's mode differ, the window is closed and opened again on the same page (the user, 17-11-2026:
# "in light theme it is showing dark").
_window_dark: dict[str, bool] = {}       # profile → the mode its window was started with (this run's knowledge)
_last_reopen = [0.0]
REOPEN_GAP_S = 20                        # never twice in a row: a page that loads reports its theme again


def window_process(profile_dir: str) -> tuple[int, bool] | None:
    """The Edge process showing Runway's own window (started with this profile) and whether it was
    started in dark mode — read from its command line."""
    if not IS_WINDOWS or not profile_dir:
        return None
    import json as _json
    ps = ("Get-CimInstance Win32_Process -Filter \"name='msedge.exe'\" | "
          "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True,
                             text=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        rows = _json.loads(out or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if isinstance(rows, dict):
        rows = [rows]
    want = os.path.normcase(os.path.abspath(profile_dir))
    for r in rows:
        cmd = r.get("CommandLine") or ""
        if "--type=" in cmd:                         # a renderer or helper, not the browser itself
            continue
        if want in os.path.normcase(cmd.replace('"', "")):
            return int(r.get("ProcessId") or 0), "--force-dark-mode" in cmd
    return None


def window_dark(profile_dir: str) -> bool | None:
    """The mode Runway's window was started with — asked of Windows once per run, then remembered."""
    if profile_dir not in _window_dark:
        found = window_process(profile_dir)
        if found is None:
            return None
        _window_dark[profile_dir] = found[1]
    return _window_dark[profile_dir]


def _wait_exit(pid: int, seconds: float) -> bool:
    import ctypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x00100000, False, pid)          # SYNCHRONIZE
    if not h:
        return True                                      # gone already
    try:
        return k32.WaitForSingleObject(h, int(seconds * 1000)) == 0
    finally:
        k32.CloseHandle(h)


def reopen_window(url: str, profile_dir: str, dark: bool) -> bool:
    """Runway's window again, on `url`, in dark mode or not. Returns whether it reopened."""
    import time
    if not IS_WINDOWS or time.monotonic() - _last_reopen[0] < REOPEN_GAP_S:
        return False
    found = window_process(profile_dir)
    if not found or found[1] == dark:
        if found:
            _window_dark[profile_dir] = found[1]
        return False
    _last_reopen[0] = time.monotonic()
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    pid = found[0]
    for hwnd in runway_windows(app_only=True):            # only this profile's windows — another Runway
        owner = wintypes.DWORD()                           # (a release beside a dev copy) keeps its own
        user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(owner))
        if owner.value == pid:
            user32.PostMessageW(hwnd, 0x0010, 0, 0)        # WM_CLOSE
    if not _wait_exit(pid, 6):                           # Edge may stay on in the background: end it
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        _wait_exit(pid, 4)
    time.sleep(0.4)
    _window_dark[profile_dir] = bool(dark)
    return open_app_window(url, profile_dir, dark)


def runway_windows(app_only: bool = False) -> list[int]:
    """Visible top-level windows showing a Runway page (their title starts "Runway · "). With
    app_only, Runway's own app windows only — a browser window holding a Runway tab is titled
    "Runway · Dashboard - Microsoft Edge"; an app window carries the page's title alone."""
    if not IS_WINDOWS:
        return []
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found: list[int] = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def each(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                if buf.value.startswith(WINDOW_TITLE_PREFIX) and not (app_only and " - " in buf.value):
                    found.append(hwnd)
        return True

    user32.EnumWindows(proto(each), 0)
    return found


# ── the title bar ─────────────────────────────────────────────────────────────────────────────────
# Edge's app window draws a plain Windows title bar (light on a light Windows) and ignores the
# page's theme-color, so Runway colours its own windows' title bars through the window manager:
# Windows 11 takes the exact colour (the banner's), Windows 10 at least the dark or light caption.
DWMWA_USE_IMMERSIVE_DARK_MODE, DWMWA_CAPTION_COLOR, DWMWA_TEXT_COLOR = 20, 35, 36


def _colorref(hex_rgb: str) -> int:
    """'#171918' → Windows' COLORREF (0x00BBGGRR)."""
    h = hex_rgb.strip().lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return r | (g << 8) | (b << 16)


def color_title_bars(background: str, text: str, dark: bool) -> int:
    """Runway's app windows get a title bar in `background` with `text`; returns how many."""
    if not IS_WINDOWS:
        return 0
    import ctypes
    from ctypes import wintypes

    try:
        dwm = ctypes.windll.dwmapi
    except OSError:
        return 0
    done = 0
    for hwnd in runway_windows(app_only=True):
        took = False
        for attr, value in ((DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if dark else 0),
                            (DWMWA_CAPTION_COLOR, _colorref(background)),
                            (DWMWA_TEXT_COLOR, _colorref(text))):
            v = wintypes.DWORD(value)
            try:
                took = dwm.DwmSetWindowAttribute(wintypes.HWND(hwnd), attr, ctypes.byref(v), ctypes.sizeof(v)) == 0 or took
            except OSError:
                pass
        done += took
    return done


def focus_window() -> bool:
    """Brings an open Runway window to the front; False when there is none."""
    wins = runway_windows()
    if not wins:
        return False
    import ctypes

    user32 = ctypes.windll.user32
    hwnd = wins[0]
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)                 # SW_RESTORE
    user32.keybd_event(0x12, 0, 0, 0)               # a tap of Alt lets a background process
    user32.keybd_event(0x12, 0, 2, 0)               # hand the focus over (Windows' focus rule)
    user32.SetForegroundWindow(hwnd)
    return True


def show(url: str, **window) -> None:
    """Runway's window to the front, or a new one (window: open_app_window's options)."""
    if not focus_window():
        open_app_window(url, **window)


# ── a message box ─────────────────────────────────────────────────────────────────────────────────

MB_OK, MB_YESNO, MB_ICONWARNING, MB_ICONERROR = 0x0, 0x4, 0x30, 0x10
IDYES = 6


def message_box(text: str, title: str = "Runway", flags: int = MB_OK | MB_ICONWARNING) -> int:
    if not IS_WINDOWS:
        print(f"{title}: {text}", file=sys.stderr)
        return IDYES
    import ctypes

    return ctypes.windll.user32.MessageBoxW(None, text, title, flags | 0x40000 | 0x10000)   # topmost, foreground


def quit_question(listeners: int) -> str | None:
    """What Quit asks first; None when nothing is running and Runway can simply stop."""
    if listeners <= 0:
        return None
    what = "1 listener is" if listeners == 1 else f"{listeners} listeners are"
    return (f"{what} running. If Runway quits, devices lose their connection and relaying stops "
            "until Runway is started again.\n\nQuit Runway?")


def tray_tip(version: str, listeners: int) -> str:
    state = "idle" if listeners <= 0 else ("1 listener running" if listeners == 1 else f"{listeners} listeners running")
    return f"Runway {version} · {state}"[:127]


# ── the tray icon (Win32 Shell_NotifyIcon, through ctypes) ────────────────────────────────────────

class Tray:
    """The icon by the clock while Runway runs. Left-click opens Runway; right-click: Open Runway,
    Logs, Quit Runway. Runs its own message loop on a thread of its own."""

    CLASS = "RunwayTray"
    _CALLBACK = 0x8000 + 1          # WM_APP + 1: the icon's mouse events
    QUIT = 0x8000 + 2               # WM_APP + 2: quit, as the menu does (also for tests)
    _OPEN, _LOGS, _EXIT = 1, 2, 3

    def __init__(self, *, icon_path: str, on_open: Callable[[], None], on_logs: Callable[[], None],
                 on_quit: Callable[[], None], tip: Callable[[], str]):
        self.icon_path, self.on_open, self.on_logs, self.on_quit, self.tip = icon_path, on_open, on_logs, on_quit, tip
        self.hwnd = None
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not IS_WINDOWS or self._thread:
            return
        self._thread = threading.Thread(target=self._run, name="runway-tray", daemon=True)
        self._thread.start()
        self._ready.wait(5)

    def stop(self) -> None:
        if self.hwnd:
            import ctypes

            ctypes.windll.user32.PostMessageW(self.hwnd, 0x0010, 0, 0)       # WM_CLOSE
            if self._thread:
                self._thread.join(2)

    def _run(self) -> None:
        try:
            self._loop()
        except Exception:
            import logging

            logging.getLogger("runway.desktop").exception("tray icon failed")
        finally:
            self._ready.set()

    def _loop(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32, shell32, kernel32 = ctypes.windll.user32, ctypes.windll.shell32, ctypes.windll.kernel32
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = LRESULT
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                           wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int,
                                      ctypes.c_int, wintypes.UINT]
        user32.CreatePopupMenu.restype = wintypes.HMENU
        user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                          wintypes.HWND, wintypes.LPVOID]
        user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                        ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
                        ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                        ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                        ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD),
                        ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
                        ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
                        ("dwInfoFlags", wintypes.DWORD), ("guidItem", ctypes.c_byte * 16),
                        ("hBalloonIcon", wintypes.HICON)]

        NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
        NIF_MESSAGE, NIF_ICON, NIF_TIP = 1, 2, 4
        WM_DESTROY, WM_CLOSE, WM_TIMER = 0x0002, 0x0010, 0x0113
        WM_LBUTTONUP, WM_RBUTTONUP, WM_CONTEXTMENU = 0x0202, 0x0205, 0x007B
        taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
        hinst = kernel32.GetModuleHandleW(None)
        size = user32.GetSystemMetrics(49) or 16                  # SM_CXSMICON
        hicon = user32.LoadImageW(None, self.icon_path, 1, size, size, 0x10)    # IMAGE_ICON, LR_LOADFROMFILE

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = self._CALLBACK
        nid.hIcon = hicon

        def set_tip() -> None:
            try:
                nid.szTip = self.tip()
            except Exception:
                nid.szTip = "Runway"

        def menu() -> None:
            m = user32.CreatePopupMenu()
            user32.AppendMenuW(m, 0, self._OPEN, "Open Runway")
            user32.AppendMenuW(m, 0, self._LOGS, "Logs")
            user32.AppendMenuW(m, 0x800, 0, None)                  # MF_SEPARATOR
            user32.AppendMenuW(m, 0, self._EXIT, "Quit Runway")
            user32.SetMenuDefaultItem(m, self._OPEN, 0)
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            user32.SetForegroundWindow(self.hwnd)                  # so the menu closes on a click elsewhere
            choice = user32.TrackPopupMenu(m, 0x0100 | 0x0002, pt.x, pt.y, 0, self.hwnd, None)   # RETURNCMD | RIGHTBUTTON
            user32.PostMessageW(self.hwnd, 0, 0, 0)
            user32.DestroyMenu(m)
            act(choice)

        def act(choice: int) -> None:
            fn = {self._OPEN: self.on_open, self._LOGS: self.on_logs, self._EXIT: self.on_quit}.get(choice)
            if fn:
                threading.Thread(target=fn, daemon=True).start()     # never block the icon's loop

        def proc(hwnd, msg, wparam, lparam):
            if msg == self._CALLBACK:
                if lparam == WM_LBUTTONUP:
                    act(self._OPEN)
                elif lparam in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    menu()
                return 0
            if msg == self.QUIT:
                act(self._EXIT)
                return 0
            if msg == WM_TIMER:
                set_tip()
                shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
                return 0
            if msg == taskbar_created:                             # Explorer restarted: put the icon back
                shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
                return 0
            if msg == WM_CLOSE:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._proc = WNDPROC(proc)                                  # kept alive for the window's life
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.lpszClassName = self.CLASS
        user32.RegisterClassExW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(0, self.CLASS, "Runway", 0, 0, 0, 0, 0, None, None, hinst, None)
        nid.hWnd = self.hwnd
        set_tip()
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
        user32.SetTimer(self.hwnd, 1, 5000, None)                   # the tip follows the listener count
        self._ready.set()

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        self.hwnd = None


_tray: Tray | None = None


def start_tray(**kw) -> Tray | None:
    global _tray
    if not IS_WINDOWS or _tray is not None:
        return _tray
    _tray = Tray(**kw)
    _tray.start()
    return _tray


def stop_tray() -> None:
    global _tray
    if _tray is not None:
        _tray.stop()
        _tray = None
