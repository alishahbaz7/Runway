"""Tests for runway/usage_client.py — the app-control skill's client, copied as is."""
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from runway import usage_client as uc

FACTS = {"platform": "windows", "os": "Windows 11 Pro (build 26300)", "login": "MMI\\shahbaz", "computer": "GT2575"}


class FakeSend:
    """Records each report as the wire would carry it; fails while `fail` is set."""

    def __init__(self):
        self.reports = []
        self.fail = None

    def __call__(self, url, key, report, timeout=20.0, user_agent=""):
        copy = json.loads(json.dumps(report))
        if self.fail:
            raise self.fail
        self.reports.append(copy)
        return {"ok": True}

    def kinds(self):
        return [e["kind"] for r in self.reports for e in r["events"]]


class Rig:
    def __init__(self, folder=None, version="1.0.0"):
        self.folder = folder or tempfile.mkdtemp()
        self.now = datetime(2026, 11, 10, 10, tzinfo=timezone.utc)
        self.send = FakeSend()
        self.usage = uc.UsageReporter("InTouch OTA Analytics", version, self.folder, transport=self.send,
                                      clock=lambda: self.now, facts=FACTS)

    def start(self):
        self.usage.start(background=False, at_exit=False)
        self.usage.flush()

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)

    def stored(self):
        return json.loads((Path(self.folder) / "usage.json").read_text(encoding="utf-8"))


class ReporterTest(unittest.TestCase):
    def test_first_start_sends_install_session_and_first_start(self):
        rig = Rig()
        rig.start()
        r = rig.send.reports[-1]
        self.assertTrue(uc.is_id(r["install"]["id"]))
        self.assertEqual(r["install"]["app"], "InTouch OTA Analytics")
        self.assertEqual(r["install"]["appVersion"], "1.0.0")
        self.assertEqual(r["install"]["login"], "MMI\\shahbaz")
        self.assertIsNone(r["install"]["name"])
        self.assertEqual(len(r["sessions"]), 1)
        self.assertEqual(rig.send.kinds(), ["first_start"])
        self.assertTrue(rig.usage.needs_name)
        self.assertEqual(rig.usage.status()["waiting"], 0)

    def test_the_name_is_asked_once_and_sent(self):
        rig = Rig()
        rig.start()
        rig.usage.set_name("  Shahbaz ", send_now=False)
        rig.usage.flush()
        self.assertEqual(rig.send.reports[-1]["install"]["name"], "Shahbaz")
        again = Rig(rig.folder)
        again.start()
        self.assertFalse(again.usage.needs_name)
        self.assertEqual(again.usage.name, "Shahbaz")

    def test_ticks_measure_time_open_and_the_apps_own_metrics(self):
        rig = Rig()
        jobs = {"n": 0}
        rig.usage.metrics = lambda: {"jobsStarted": jobs["n"]}
        rig.start()
        for i in range(3):
            jobs["n"] = i + 1
            rig.advance(60)
            rig.usage.tick()
        rig.advance(3 * 3600)  # the computer slept
        rig.usage.tick()
        s = rig.usage.session
        self.assertEqual(s["openSeconds"], 240)
        self.assertEqual(s["metrics"], {"jobsStarted": 3})
        rig.usage.page("jobs")
        rig.usage.page("jobs")
        rig.usage.page("devices")
        self.assertEqual(s["pages"], {"jobs": 2, "devices": 1})

    def test_offline_reports_wait_and_go_out_with_the_next_start(self):
        rig = Rig()
        rig.send.fail = uc.SendError("No connection to example.supabase.co")
        rig.start()
        self.assertEqual(rig.usage.error, "No connection to example.supabase.co")
        offline_session = rig.usage.session["id"]
        self.assertIn(offline_session, rig.stored()["outbox"]["sessions"])

        later = Rig(rig.folder, version="1.1.0")
        later.start()
        sent = {s["id"] for s in later.send.reports[-1]["sessions"]}
        self.assertEqual(sent, {offline_session, later.usage.session["id"]})
        self.assertEqual(later.send.kinds(), ["first_start", "version_changed"])
        self.assertEqual(later.usage.status()["waiting"], 0)

    def test_a_refused_report_is_dropped(self):
        rig = Rig()
        rig.send.fail = uc.SendError("HTTP 400 from x", rejected=True)
        rig.start()
        self.assertIn("dropped", rig.usage.error)
        self.assertEqual(rig.usage.status()["waiting"], 0)

    def test_a_long_offline_spell_goes_out_in_batches(self):
        rig = Rig()
        rig.send.fail = uc.SendError("No connection")
        rig.start()
        for i in range(uc.EVENTS_PER_SEND + 30):
            rig.usage.record("export_done", {"n": i})
        rig.send.fail = None
        rig.usage.flush()
        self.assertEqual(len(rig.send.reports), 2)
        self.assertEqual(len(rig.send.reports[0]["events"]), uc.EVENTS_PER_SEND)

    def test_errors_all_counted_first_few_in_full(self):
        rig = Rig()
        rig.start()
        for i in range(uc.MAX_ERRORS + 4):
            try:
                raise ValueError(f"boom {i}")
            except ValueError as e:
                rig.usage.record_error(e)
        rig.usage.flush()
        errors = [e for r in rig.send.reports for e in r["events"] if e["kind"] == "error"]
        self.assertEqual(len(errors), uc.MAX_ERRORS)
        self.assertEqual(errors[0]["data"]["message"], "ValueError: boom 0")
        self.assertEqual(rig.usage.session["errors"], uc.MAX_ERRORS + 4)

    def test_bad_event_kinds_are_not_recorded(self):
        rig = Rig()
        rig.start()
        rig.usage.record("Bad Kind!")
        self.assertEqual(rig.stored()["outbox"]["events"], [])

    def test_closing_ends_the_session_and_sends_it(self):
        rig = Rig()
        rig.start()
        rig.advance(90)
        rig.usage.stop()
        last = rig.send.reports[-1]["sessions"][-1]
        self.assertEqual(last["endedAt"], "2026-11-10T10:01:30Z")
        self.assertEqual(last["openSeconds"], 90)

    def test_switched_off_does_nothing(self):
        folder = tempfile.mkdtemp()
        usage = uc.UsageReporter("X", "1.0.0", folder, enabled=False, transport=FakeSend(), facts=FACTS)
        usage.start(background=False, at_exit=False)
        usage.page("a")
        usage.record("first_start")
        usage.flush()
        self.assertFalse(usage.needs_name)
        self.assertFalse((Path(folder) / "usage.json").exists())

    def test_a_damaged_file_starts_fresh(self):
        folder = tempfile.mkdtemp()
        (Path(folder) / "usage.json").write_text("{not json", encoding="utf-8")
        rig = Rig(folder)
        rig.start()
        self.assertEqual(rig.send.kinds(), ["first_start"])


class Server:
    """A local stand-in for the usage server, answering with a chosen status."""

    def __init__(self):
        self.status, self.answer, self.seen = 200, {"ok": True}, []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                outer.seen.append(({k.lower(): v for k, v in self.headers.items()}, json.loads(body)))  # names are case-insensitive
                data = json.dumps(outer.answer).encode()
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/rest/v1/rpc/usage_report"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class SendTest(unittest.TestCase):
    def setUp(self):
        self.server = Server()

    def tearDown(self):
        self.server.close()

    def test_posts_json_with_the_publishable_key_in_apikey_only(self):
        self.assertEqual(uc.send(self.server.url, "sb_publishable_x", {"install": {"id": "a" * 32}}), {"ok": True})
        headers, body = self.server.seen[-1]
        self.assertEqual(headers["apikey"], "sb_publishable_x")
        self.assertNotIn("authorization", headers)
        self.assertEqual(body, {"install": {"id": "a" * 32}})

    def test_a_legacy_jwt_key_also_goes_in_authorization(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoiYW5vbiJ9.c2ln"
        uc.send(self.server.url, jwt, {})
        self.assertEqual(self.server.seen[-1][0]["authorization"], f"Bearer {jwt}")

    def test_a_refused_report_is_marked_rejected_with_the_servers_reason(self):
        self.server.status, self.server.answer = 400, {"code": "22023", "message": "install.id is missing or not 32 hex digits"}
        with self.assertRaises(uc.SendError) as e:
            uc.send(self.server.url, "secret-ish", {})
        self.assertTrue(e.exception.rejected)
        self.assertEqual(e.exception.message, "HTTP 400 from 127.0.0.1: install.id is missing or not 32 hex digits")
        self.assertNotIn("secret-ish", e.exception.message)

    def test_a_bad_key_or_missing_function_is_kept_for_later(self):
        for code in (401, 404, 500, 503):
            self.server.status, self.server.answer = code, {"message": "nope"}
            with self.assertRaises(uc.SendError) as e:
                uc.send(self.server.url, "k", {})
            self.assertFalse(e.exception.rejected, code)

    def test_no_server_is_a_short_message(self):
        url = self.server.url
        self.server.close()
        with self.assertRaises(uc.SendError) as e:
            uc.send(url, "k", {}, timeout=3)
        self.assertEqual(e.exception.message, "No connection to 127.0.0.1")
        self.server = Server()  # for tearDown

    def test_started_in_the_background_it_reports_at_once_and_on_close(self):
        folder = tempfile.mkdtemp()
        usage = uc.UsageReporter("uCANdo", "3.1.0", folder, url=self.server.url, key="k", facts=FACTS)
        usage.start(at_exit=False)
        for _ in range(50):
            if self.server.seen:
                break
            threading.Event().wait(0.05)
        self.assertEqual(self.server.seen[0][1]["install"]["app"], "uCANdo")
        usage.stop()
        self.assertIsNotNone(self.server.seen[-1][1]["sessions"][-1]["endedAt"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
