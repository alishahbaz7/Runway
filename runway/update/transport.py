"""HTTP for the updater — standard library only, every step time-limited, every failure an
``UpdateError`` in words.

Traps already paid for in BluePulse: a connection dropped halfway is its own exception type (catch
it, and anything else, as a failure — never hang at "Downloading…"); a proxy or Wi-Fi sign-in page
can answer with bytes that are not UTF-8; a short file (fewer bytes than Content-Length) is a
failure, not a finished download; reading an error response can itself throw.
"""

from __future__ import annotations

import hashlib
import http.client
import os
import socket
import urllib.error
import urllib.request
from typing import Callable
from urllib.parse import urlsplit

from .. import __version__, install

STEP_TIMEOUT = 20.0          # connect / each read while checking
STALL_TIMEOUT = 30.0         # a download fails after this long without data
MAX_MANIFEST = 1_000_000     # latest.json is small; anything bigger is not it
CHUNK = 64 * 1024


class UpdateError(Exception):
    """A step that did not work, said so a person can act on it."""


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": f"{install.APP_NAME}/{__version__}",
                                                "Accept": "*/*"})


def _host(url: str) -> str:
    return urlsplit(url).hostname or url


def get_text(url: str, timeout: float = STEP_TIMEOUT) -> str:
    """GET a small text file, following redirects (GitHub's latest/download is one)."""
    try:
        with urllib.request.urlopen(_request(url), timeout=timeout) as r:
            data = r.read(MAX_MANIFEST + 1)
        if len(data) > MAX_MANIFEST:
            raise UpdateError(f"{_host(url)} sent more than a latest.json")
        return data.decode("utf-8", "replace")
    except UpdateError:
        raise
    except urllib.error.HTTPError as e:
        raise UpdateError(f"HTTP {e.code} from {_host(url)}") from None
    except (socket.timeout, TimeoutError):
        raise UpdateError(f"{_host(url)} did not answer in time") from None
    except (urllib.error.URLError, http.client.HTTPException, ConnectionError, OSError):
        raise UpdateError(f"No connection to {_host(url)}") from None
    except Exception as e:  # anything else ends the check too, quietly
        raise UpdateError(f"Could not read {_host(url)} ({type(e).__name__})") from None


def download(url: str, dest: str, *, on_progress: Callable[[int, int | None], None] | None = None,
             cancelled: Callable[[], bool] | None = None, stall: float = STALL_TIMEOUT) -> str:
    """Streams ``url`` into ``dest`` and returns its SHA-256 (lower-case hex). A failure removes
    the partial file."""
    sha = hashlib.sha256()
    received = 0
    total: int | None = None
    try:
        with urllib.request.urlopen(_request(url), timeout=stall) as r, open(dest, "wb") as out:
            length = r.headers.get("Content-Length")
            total = int(length) if length and length.isdigit() else None
            while True:
                if cancelled and cancelled():
                    raise UpdateError("The download was cancelled")
                chunk = r.read(CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                sha.update(chunk)
                received += len(chunk)
                if on_progress:
                    on_progress(received, total)
        if total is not None and received < total:
            raise UpdateError(f"The download stopped after {received:,} of {total:,} bytes")
        return sha.hexdigest()
    except UpdateError:
        _remove(dest)
        raise
    except urllib.error.HTTPError as e:
        _remove(dest)
        raise UpdateError(f"HTTP {e.code} from {_host(url)}") from None
    except (socket.timeout, TimeoutError):
        _remove(dest)
        raise UpdateError(f"{_host(url)} stopped sending") from None
    except (urllib.error.URLError, http.client.HTTPException, ConnectionError) as e:
        _remove(dest)
        raise UpdateError(f"The connection to {_host(url)} dropped ({type(e).__name__})") from None
    except OSError as e:
        _remove(dest)
        raise UpdateError(f"Could not write the download ({e.strerror or type(e).__name__})") from None
    except Exception as e:
        _remove(dest)
        raise UpdateError(f"The download stopped ({type(e).__name__})") from None


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
