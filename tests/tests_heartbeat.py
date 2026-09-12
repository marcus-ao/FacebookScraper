"""External heartbeat tests use MockTransport only; no configured endpoint is contacted."""
import contextlib
import io
import json
import logging
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.config import Config  # noqa: E402
from core.heartbeat import Heartbeat, HeartbeatSettings, heartbeat_status  # noqa: E402

NOW = datetime(2026, 9, 12, 10, tzinfo=timezone.utc)
URL = "https://heartbeat.example.invalid/private-token?ping=another-private-token"


class HeartbeatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state" / "heartbeat.json"
        self.settings = HeartbeatSettings(enabled=True)

    def heartbeat(self, handler, **kwargs):
        http = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(http.close)
        return Heartbeat(self.path, self.settings, http=http,
                         environ={"HEARTBEAT_URL": URL}, **kwargs)

    def test_disabled_never_reads_environment_constructs_http_or_creates_state(self):
        class UnreadableEnvironment:
            def get(self, *_args):
                raise AssertionError("disabled heartbeat read its URL")
        with patch("core.heartbeat.httpx.Client", side_effect=AssertionError("unexpected HTTP client")):
            client = Heartbeat(self.path, HeartbeatSettings(), environ=UnreadableEnvironment())
            self.assertEqual(client.tick(NOW)["status"], "disabled")
            client.close()
        self.assertFalse(self.path.parent.exists())
        self.assertFalse(HeartbeatSettings.load().enabled)

    def test_success_is_durable_throttled_across_restart_and_sends_no_content(self):
        requests = []
        def handler(request):
            requests.append(request)
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.content, b"")
            self.assertNotIn("authorization", request.headers)
            self.assertTrue(all(value == self.settings.timeout_seconds
                                for value in request.extensions["timeout"].values()))
            return httpx.Response(204)
        client = self.heartbeat(handler)
        self.assertEqual(client.tick(NOW)["status"], "sent")
        restarted = self.heartbeat(handler)
        self.assertEqual(restarted.tick(NOW + timedelta(minutes=1))["status"], "not_due")
        self.assertEqual(restarted.tick(NOW + timedelta(minutes=5))["status"], "sent")
        self.assertEqual(len(requests), 2)
        record = json.loads(self.path.read_text())
        self.assertEqual(record["last_success_at"], (NOW + timedelta(minutes=5)).isoformat())
        self.assertNotIn("private-token", self.path.read_text())

    def test_timeout_is_sanitized_keeps_previous_success_and_does_not_retry_immediately(self):
        client = self.heartbeat(lambda request: httpx.Response(200))
        client.tick(NOW)
        calls = []
        def timeout(request):
            calls.append(request)
            raise httpx.ReadTimeout(f"timed out at {URL}", request=request)
        failed = self.heartbeat(timeout)
        capture = io.StringIO()
        with contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture):
            result = failed.tick(NOW + timedelta(minutes=5))
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["error_code"], "timeout")
            self.assertEqual(failed.tick(NOW + timedelta(minutes=6))["status"], "not_due")
        self.assertEqual(len(calls), 1)
        self.assertEqual(json.loads(self.path.read_text())["last_success_at"], NOW.isoformat())
        self.assertNotIn("private-token", self.path.read_text() + capture.getvalue() + repr(result))

    def test_redirect_is_not_followed_or_counted_as_success(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(302, headers={"Location": "https://elsewhere.invalid/token"})
        result = self.heartbeat(handler).tick(NOW)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "http_302")
        self.assertEqual(len(requests), 1)
        self.assertIsNone(json.loads(self.path.read_text())["last_success_at"])

    def test_preflight_status_is_read_only_and_derives_age(self):
        snapshot = heartbeat_status(self.path, self.settings, NOW)
        self.assertEqual(snapshot["status"], "never")
        self.assertFalse(self.path.parent.exists())
        self.heartbeat(lambda request: httpx.Response(200)).tick(NOW)
        self.assertEqual(heartbeat_status(self.path, self.settings, NOW + timedelta(minutes=44))["status"], "healthy")
        stale = heartbeat_status(self.path, self.settings, NOW + timedelta(minutes=46))
        self.assertEqual(stale["status"], "stale")
        self.assertEqual(stale["age_seconds"], 46 * 60)
        self.assertNotIn("private-token", repr(stale))

    def test_missing_or_unsafe_url_fails_without_http_and_does_not_escape_to_caller(self):
        for url in ("", "http://heartbeat.invalid/token", "https://user:password@heartbeat.invalid/token"):
            with self.subTest(url=url):
                client = Heartbeat(self.path, self.settings, environ={"HEARTBEAT_URL": url})
                with patch("core.heartbeat.httpx.Client", side_effect=AssertionError("invalid URL reached HTTP")):
                    result = client.tick(NOW)
                self.assertEqual(result["status"], "failed")
                self.assertIn(result["error_code"], {"missing_url", "invalid_url"})
                self.assertNotIn(url, repr(result)) if url else None
                # Each invalid configuration is independently evaluated in this fixture.
                if self.path.exists():
                    self.path.unlink()

    def test_bad_timing_config_cannot_create_unbounded_http_wait(self):
        c = Config()
        c._d["heartbeat"] = {"enabled": True, "timeout_seconds": float("inf")}
        with self.assertRaises(ValueError):
            HeartbeatSettings.load(c)

    def test_preflight_prints_elapsed_success_without_connecting_or_printing_url(self):
        from pipeline import cli
        self.heartbeat(lambda request: httpx.Response(200)).tick(NOW)
        config = Config()
        config._d["heartbeat"] = {"enabled": True}
        output = io.StringIO()
        with patch.object(cli, "cfg", return_value=config), \
                patch("core.heartbeat.httpx.Client", side_effect=AssertionError("preflight contacted heartbeat")), \
                contextlib.redirect_stdout(output):
            cli._print_heartbeat_preflight(self.path.parent, NOW + timedelta(minutes=46))
        self.assertIn("46", output.getvalue())
        self.assertIn("过期", output.getvalue())
        self.assertIn("外部服务", output.getvalue())
        self.assertNotIn("private-token", output.getvalue())

    def test_corrupt_record_or_busy_lock_does_not_send(self):
        from core.paid_model import FileLock
        client = self.heartbeat(lambda request: self.fail("unexpected send"))
        self.path.parent.mkdir()
        self.path.write_text("corrupt record")
        self.assertEqual(client.tick(NOW)["error_code"], "state_unavailable")
        self.assertEqual(self.path.read_text(), "corrupt record")
        with FileLock(self.path.with_suffix(".lock"), busy_message="fixture lock"):
            self.assertEqual(client.tick(NOW)["status"], "busy")

    def test_http_client_info_logging_cannot_expose_anonymous_ping_url(self):
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        logger = logging.getLogger("httpx")
        previous = logger.level
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        try:
            self.heartbeat(lambda request: httpx.Response(200)).tick(NOW)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous)
        self.assertNotIn("private-token", output.getvalue())


if __name__ == "__main__":
    unittest.main()
