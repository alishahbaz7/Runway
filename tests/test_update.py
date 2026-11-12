"""The updater (runway/update) — the app-updates skill's cases, ported from BluePulse's tests.

Real sockets for the network: a redirect like GitHub's latest/download, a 404, a connection
dropped halfway through the zip, a server that stops sending. Versions in here are 9.x so the
one-version test never sees a real release number.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from runway import install
from runway.update import installer, manifest as mf
from runway.update.semver import SemVer
from runway.update.service import MARKER, UpdateService
from runway.update.transport import UpdateError, download, get_text

SHA = "a" * 64


# ── versions ───────────────────────────────────────────────────────────────────────────────────

def test_semver_compares_as_numbers():
    assert SemVer.parse("9.1.9") < SemVer.parse("9.1.24")
    assert SemVer.parse("v9.2.0") == SemVer.parse("9.2.0+build.7")
    assert SemVer.try_parse(9.1) is None and SemVer.try_parse("9.1") is None


# ── latest.json ────────────────────────────────────────────────────────────────────────────────

def _manifest(**over):
    m = {"app": "Runway", "version": "9.1.0", "notes": "  What changed  ",
         "assets": {"windows": {"file": "Runway-v9.1.0.zip", "url": "Runway-v9.1.0.zip", "sha256": SHA, "size": 10}}}
    m.update(over)
    return json.dumps(m)


def test_manifest_reads_and_resolves_a_relative_url():
    m = mf.parse(_manifest(), base="https://example.test/x/latest.json", expected_app="Runway")
    assert str(m.version) == "9.1.0" and m.notes == "What changed"
    assert m.windows.url == "https://example.test/x/Runway-v9.1.0.zip" and m.windows.size == 10
    assert m.is_newer_than(SemVer.parse("9.0.9")) and not m.requires(SemVer.parse("9.0.9"))


@pytest.mark.parametrize("text, words", [
    ("not json", "not valid JSON"),
    (_manifest(app="BluePulse"), 'for "BluePulse"'),
    (_manifest(version=9.1), "no valid version"),                       # a number, not a string
    (_manifest(minimumVersion="soon"), "invalid minimumVersion"),
    (_manifest(assets={"windows": {"file": "a/b.zip", "sha256": SHA}}), "valid file name"),
    (_manifest(assets={"windows": {"file": "r.zip", "sha256": "xyz"}}), "valid sha256"),
    (_manifest(assets={"windows": {"file": "r.zip", "sha256": SHA, "url": "ftp://x/r.zip"}}), "not http"),
])
def test_a_bad_manifest_is_said_in_words(text, words):
    with pytest.raises(mf.ManifestError, match=words):
        mf.parse(text, base="https://example.test/latest.json", expected_app="Runway")


def test_minimum_version_makes_the_update_required():
    m = mf.parse(_manifest(minimumVersion="9.0.5"), base="https://e.test/l.json", expected_app="Runway")
    assert m.requires(SemVer.parse("9.0.4")) and not m.requires(SemVer.parse("9.0.5"))


# ── the network, with real sockets ──────────────────────────────────────────────────────────────

class _Server:
    """A local server: /latest/download/latest.json redirects like GitHub; /zip serves a file;
    /drop sends half and closes; /stall sends headers and goes quiet."""

    def __init__(self, payload: bytes, manifest: str = "{}"):
        self.payload, self.manifest = payload, manifest
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path == "/latest/download/latest.json":
                    self.send_response(302)
                    self.send_header("Location", "/download/v9/latest.json")
                    self.end_headers()
                elif self.path == "/download/v9/latest.json":
                    self._send(outer.manifest.encode())
                elif self.path == "/zip":
                    self._send(outer.payload)
                elif self.path == "/drop":
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(outer.payload)))
                    self.end_headers()
                    self.wfile.write(outer.payload[: len(outer.payload) // 2])
                    self.wfile.flush()
                    self.connection.shutdown(socket.SHUT_RDWR)
                elif self.path == "/stall":
                    self.send_response(200)
                    self.send_header("Content-Length", "1000")
                    self.end_headers()
                    self.wfile.flush()
                    time.sleep(2)
                else:
                    self.send_error(404)

            def _send(self, data: bytes):
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    s = _Server(os.urandom(300_000), _manifest())
    yield s
    s.close()


def test_get_text_follows_the_redirect(server):
    assert json.loads(get_text(server.url + "/latest/download/latest.json"))["app"] == "Runway"


def test_get_text_404_is_an_update_error(server):
    with pytest.raises(UpdateError, match="HTTP 404"):
        get_text(server.url + "/nothing")


def test_download_hashes_while_it_streams(server, tmp_path):
    dest = tmp_path / "r.zip.part"
    seen = []
    assert download(server.url + "/zip", str(dest), on_progress=lambda r, t: seen.append((r, t))) \
        == hashlib.sha256(server.payload).hexdigest()
    assert dest.read_bytes() == server.payload and seen[-1] == (len(server.payload), len(server.payload))


def test_a_connection_dropped_halfway_fails_and_the_next_try_works(server, tmp_path):
    dest = tmp_path / "r.zip.part"
    with pytest.raises(UpdateError):
        download(server.url + "/drop", str(dest))
    assert not dest.exists()                                  # the half file is gone
    assert download(server.url + "/zip", str(dest)) == hashlib.sha256(server.payload).hexdigest()


def test_a_server_that_stops_sending_fails(server, tmp_path):
    with pytest.raises(UpdateError, match="stopped sending|dropped|stopped after"):
        download(server.url + "/stall", str(tmp_path / "x.part"), stall=0.5)


# ── the install ───────────────────────────────────────────────────────────────────────────────

def _release_zip(path: Path, version: str, *, launcher: bytes = b"launcher-1", extra: dict | None = None) -> Path:
    name = install.release_name(version)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(f"Runway/{install.LAUNCHER_EXE}", launcher)
        z.writestr("Runway/README.txt", "read me")
        z.writestr(f"Runway/versions/{name}/{name}.exe", b"program " + version.encode())
        z.writestr(f"Runway/versions/{name}/_internal/base.dll", b"dll")
        for k, v in (extra or {}).items():
            z.writestr(k, v)
    return path


def test_install_unpacks_beside_and_refreshes_the_launcher(tmp_path):
    root = tmp_path / "Runway"
    (root / "versions" / "Runway-v9.0.0").mkdir(parents=True)
    (root / "versions" / "Runway-v9.0.0" / "Runway-v9.0.0.exe").write_bytes(b"old")
    (root / "Runway.exe").write_bytes(b"launcher-0")
    res = installer.install_from_zip(str(_release_zip(tmp_path / "r.zip", "9.1.0")), str(root), "9.1.0")
    new = root / "versions" / "Runway-v9.1.0"
    assert res.launcher_updated and (root / "Runway.exe").read_bytes() == b"launcher-1"
    assert (new / "Runway-v9.1.0.exe").read_bytes() == b"program 9.1.0"
    assert (new / "_internal" / "base.dll").exists()
    assert (root / "versions" / "Runway-v9.0.0" / "Runway-v9.0.0.exe").read_bytes() == b"old"   # untouched
    assert not any(p.name.endswith(".partial") for p in (root / "versions").iterdir())
    assert [str(v) for v, _ in installer.installed(str(root))] == ["9.1.0", "9.0.0"]
    # the same launcher again is left alone
    res = installer.install_from_zip(str(_release_zip(tmp_path / "r2.zip", "9.1.0")), str(root), "9.1.0")
    assert not res.launcher_updated


def test_an_unsafe_or_incomplete_package_installs_nothing(tmp_path):
    root = tmp_path / "Runway"
    bad = _release_zip(tmp_path / "bad.zip", "9.1.0", extra={"Runway/versions/Runway-v9.1.0/../../evil.txt": "x"})
    with pytest.raises(UpdateError, match="unsafe path"):
        installer.install_from_zip(str(bad), str(root), "9.1.0")
    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as z:
        z.writestr("Runway/README.txt", "no program")
    with pytest.raises(UpdateError, match="does not contain Runway-v9.1.0.exe"):
        installer.install_from_zip(str(empty), str(root), "9.1.0")
    assert not (root / "versions" / "Runway-v9.1.0").exists()
    assert not any(p.name.endswith(".partial") for p in (root / "versions").iterdir())


def test_cleanup_keeps_the_running_the_newest_and_the_way_back(tmp_path):
    root = tmp_path / "Runway"
    for v in ("9.0.0", "9.0.1", "9.0.2", "9.1.0"):
        d = root / "versions" / f"Runway-v{v}"
        d.mkdir(parents=True)
        (d / f"Runway-v{v}.exe").write_bytes(b"x")
    for leftover in ("Runway-v9.2.0.partial", "Runway-v9.0.2.old-123", ".download"):
        (root / "versions" / leftover).mkdir()
    removed = installer.cleanup(str(root), SemVer.parse("9.0.2"))
    assert sorted(removed) == sorted(["Runway-v9.0.0", "Runway-v9.2.0.partial", "Runway-v9.0.2.old-123", ".download"])
    assert [str(v) for v, _ in installer.installed(str(root))] == ["9.1.0", "9.0.2", "9.0.1"]


def test_the_launcher_install_is_recognised_from_the_program_path(tmp_path):
    exe = tmp_path / "Runway" / "versions" / "Runway-v9.1.0" / "Runway-v9.1.0.exe"
    assert install.launcher_root(str(exe)) == str(tmp_path / "Runway")
    assert install.launcher_root(str(tmp_path / "Runway-v9.1.0" / "Runway-v9.1.0.exe")) is None     # unzipped by hand
    assert install.launcher_root(str(tmp_path / "versions" / "Runway-v9.1.0" / "other.exe")) is None


# ── the flow ──────────────────────────────────────────────────────────────────────────────────

def _service(tmp_path, text=None, *, root=None, error=None, current="9.0.0", **kw):
    events = []

    def get(url):
        if error:
            raise error
        return text if text is not None else _manifest()

    s = UpdateService(data_dir=str(tmp_path / "data"), current=current, root=root, enabled=True,
                      manifest_url="https://example.test/latest.json", get_text=get,
                      on_event=lambda k, d: events.append((k, d)), **kw)
    return s, events


def test_check_ends_available_up_to_date_or_failed(tmp_path):
    s, _ = _service(tmp_path)
    s.check()
    assert s.stage == "available" and s.status()["latest"] == "9.1.0" and not s.status()["required"]
    s, _ = _service(tmp_path, current="9.1.0")
    s.check()
    assert s.stage == "up_to_date"
    s, events = _service(tmp_path, error=UpdateError("HTTP 404 from github.com"))
    s.check()
    assert s.stage == "failed" and s.error == "No release published yet"
    assert events == [("update_check_failed", {"reason": "No release published yet"})]
    s, _ = _service(tmp_path, text="<html>sign in to the Wi-Fi</html>")
    s.check()
    assert s.stage == "failed" and "not valid JSON" in s.error


def test_a_check_that_hangs_still_ends(tmp_path):
    def hang(url):
        time.sleep(5)
    s = UpdateService(data_dir=str(tmp_path), current="9.0.0", enabled=True, get_text=hang, check_timeout=0.3,
                      manifest_url="https://slow.test/latest.json")
    t0 = time.monotonic()
    s.check()
    assert time.monotonic() - t0 < 2 and s.stage == "failed" and "did not answer in time" in s.error


def test_the_offer_is_recorded_once_per_run(tmp_path):
    s, events = _service(tmp_path)
    s.check()
    s.mark_offered()
    s.mark_offered()
    s.postpone()
    assert [k for k, _ in events] == ["update_offered", "update_postponed"]


def test_install_downloads_verifies_installs_and_restarts(tmp_path):
    root = tmp_path / "Runway"
    root.mkdir()
    zbytes = _release_zip(tmp_path / "src.zip", "9.1.0").read_bytes()
    sha = hashlib.sha256(zbytes).hexdigest()
    text = _manifest(assets={"windows": {"file": "Runway-v9.1.0.zip", "sha256": sha, "size": len(zbytes)}})
    restarted = threading.Event()

    def fake_download(url, dest, on_progress=None):
        Path(dest).write_bytes(zbytes)
        if on_progress:
            on_progress(len(zbytes), len(zbytes))
        return hashlib.sha256(zbytes).hexdigest()

    s, events = _service(tmp_path, text, root=str(root), download=fake_download, restart=restarted.set)
    s.check()
    s.install_async()
    assert restarted.wait(5)
    assert s.stage == "restarting"
    assert (root / "versions" / "Runway-v9.1.0" / "Runway-v9.1.0.exe").exists()
    assert json.loads((tmp_path / "data" / MARKER).read_text())["to"] == "9.1.0"
    assert [k for k, _ in events] == ["update_started"]
    assert not list((root / "versions" / ".download").glob("*"))           # nothing left behind


def test_a_download_that_does_not_match_its_checksum_installs_nothing(tmp_path):
    root = tmp_path / "Runway"
    root.mkdir()
    zbytes = _release_zip(tmp_path / "src.zip", "9.1.0").read_bytes()

    def fake_download(url, dest, on_progress=None):
        Path(dest).write_bytes(zbytes)
        return hashlib.sha256(zbytes).hexdigest()

    s, events = _service(tmp_path, root=str(root), download=fake_download)     # the manifest says SHA = aaa…
    s.check()
    s.install_async()
    for _ in range(50):
        if s.stage == "failed":
            break
        time.sleep(0.05)
    assert s.stage == "failed" and "checksum" in s.error
    assert not (root / "versions" / "Runway-v9.1.0").exists()
    assert events[-1][0] == "update_failed"


def test_not_a_launcher_install_opens_the_release_page(tmp_path):
    s, _ = _service(tmp_path, root=None)
    s.check()
    assert s.install_async()["open"].startswith("https://github.com/")
    assert s.stage == "available"


def test_the_next_start_reports_the_update_it_landed_on(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / MARKER).write_text(json.dumps({"from": "9.0.0", "to": "9.1.0"}))
    s, events = _service(tmp_path, current="9.1.0")
    s._read_marker()
    assert events == [("update_installed", {"from": "9.0.0", "to": "9.1.0"})]
    assert not (data / MARKER).exists()


def test_a_development_run_does_not_check(tmp_path):
    s = UpdateService(data_dir=str(tmp_path), current="9.0.0", enabled=False)
    s.check()
    assert s.stage == "disabled" and s.status()["disabled_reason"]


# ── the release tooling and the app agree ─────────────────────────────────────────────────────

def test_the_release_manifest_reads_back_in_the_app():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))
    import build_release
    from runway import __version__

    sample = "# Changelog\n\n## [9.2.0] — Unreleased\n\n## [9.1.0] — 2026-11-20\n\n### Added\n- **A thing**\n\n## [9.0.0]\n- older\n"
    assert build_release.changelog_section(sample, "9.1.0") == "### Added\n- **A thing**"
    assert build_release.changelog_section(sample, "9.2.0") == ""          # a fresh cycle: nothing yet
    # the real CHANGELOG has a section for this version (its notes may be empty early in a cycle)
    build_release.changelog_section(
        (Path(__file__).resolve().parent.parent / "CHANGELOG.md").read_text(encoding="utf-8"), __version__)
    data = build_release.build_manifest(notes="What changed", sha="b" * 64, size=123, minimum=None)
    from runway.update import config
    m = mf.parse(json.dumps(data), base=config.MANIFEST_URL, expected_app=install.APP_NAME)
    assert str(m.version) == __version__ and m.windows.file == f"{install.release_name()}.zip"
    assert m.windows.url == config.asset_url(__version__)
