"""IP/ASN collector uses MockTransport only; no real public-IP requests."""
import contextlib
import io
import json
import logging
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.network_evidence import (NetworkEvidence, NetworkEvidenceSettings,
                                   network_evidence_status, detection_failure_kind)  # noqa: E402

NOW = datetime(2026, 9, 12, 10, tzinfo=timezone.utc)
TOKEN = "offline-fixture-secret-token"


def body(ip="1.1.1.1", asn="AS13335", kind=None):
    if kind is None:
        return {"ip": ip, "asn": asn, "as_name": "Name must not determine residential type"}
    return {"ip": ip, "as": {"asn": asn, "type": kind}, "is_hosting": kind == "hosting"}


class NetworkEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state" / "network_evidence.json"
        self.settings = NetworkEvidenceSettings(enabled=True)

    def collector(self, handler, *, settings=None, environ=None):
        http = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(http.close)
        return NetworkEvidence(self.path, settings or self.settings, http=http,
                               environ={"IPINFO_TOKEN": TOKEN} if environ is None else environ)

    def test_disabled_does_not_read_token_make_requests_or_create_state(self):
        class NoSecrets:
            def get(self, key, default=None):
                if key == "IPINFO_TOKEN":
                    raise AssertionError("read a secret while disabled")
                return default
        # 显式构造关闭状态：这里验的是关闭时的行为，不该由线上 config.toml 的取值决定。
        settings = NetworkEvidenceSettings(enabled=False)
        with patch("core.network_evidence.httpx.Client", side_effect=AssertionError("HTTP")):
            collector = NetworkEvidence(self.path, settings, environ=NoSecrets())
            self.assertEqual(collector.refresh(NOW)["status"], "disabled")
            collector.close()
        self.assertEqual(network_evidence_status(self.path, settings, NOW)["status"], "disabled")
        self.assertFalse(self.path.parent.exists())

    def test_lite_request_uses_header_auth_and_never_guesses_missing_asn_type(self):
        requests = []
        def handle(request):
            requests.append(request)
            self.assertEqual(str(request.url), "https://api.ipinfo.io/lite/me")
            self.assertEqual(request.headers["authorization"], "Bearer " + TOKEN)
            self.assertEqual(request.content, b"")
            self.assertTrue(all(value == 3 for value in request.extensions["timeout"].values()))
            return httpx.Response(200, json=body())
        self.assertEqual(self.collector(handle).refresh(NOW)["status"], "recorded")
        status = network_evidence_status(self.path, self.settings, NOW)
        self.assertEqual(status["latest"]["asn_type"], "unknown")
        self.assertEqual(status["stability"], "insufficient_samples")
        self.assertEqual(len(requests), 1)
        self.assertNotIn(TOKEN, self.path.read_text())
        self.assertNotIn("as_name", self.path.read_text())

    def test_recent_n_successes_and_restart_limit_do_not_hide_ip_changes(self):
        settings = NetworkEvidenceSettings(enabled=True, history_size=3)
        collector = self.collector(lambda request: httpx.Response(200, json=body()), settings=settings)
        for hour in range(3):
            collector.refresh(NOW + timedelta(hours=hour))
        stable = network_evidence_status(self.path, settings, NOW + timedelta(hours=2))
        self.assertEqual(stable["stability"], "stable_observed")
        restarted = self.collector(lambda request: httpx.Response(200, json=body("8.8.8.8", "AS15169")),
                                   settings=settings)
        self.assertEqual(restarted.refresh(NOW + timedelta(hours=2, minutes=59))["status"], "not_due")
        restarted.refresh(NOW + timedelta(hours=3))
        changed = network_evidence_status(self.path, settings, NOW + timedelta(hours=3))
        self.assertEqual(len(changed["history"]), 3)
        self.assertEqual(changed["stability"], "changed")
        self.assertEqual(changed["distinct_ips"], 2)
        self.assertEqual(changed["distinct_asns"], 2)

    def test_core_hosting_is_explicit_but_isp_is_not_proof_of_residential(self):
        settings = NetworkEvidenceSettings(enabled=True, tier="core")
        collector = self.collector(lambda request: httpx.Response(200, json=body(kind="hosting")),
                                   settings=settings)
        collector.refresh(NOW)
        self.assertEqual(network_evidence_status(self.path, settings, NOW)["network_type"], "hosting")
        self.collector(lambda request: httpx.Response(200, json=body(kind="isp")), settings=settings).refresh(
            NOW + timedelta(hours=1))
        result = network_evidence_status(self.path, settings, NOW + timedelta(hours=1))
        self.assertEqual(result["network_type"], "isp_unverified")
        self.assertFalse(result["residential_verified"])

    def test_timeout_keeps_success_and_is_not_classified_as_account_block(self):
        collector = self.collector(lambda request: httpx.Response(200, json=body()))
        collector.refresh(NOW)
        def timeout(request):
            raise httpx.ConnectTimeout(f"secret={TOKEN}", request=request)
        failed = self.collector(timeout)
        self.assertEqual(failed.refresh(NOW + timedelta(hours=1))["error_code"], "network_timeout")
        status = network_evidence_status(self.path, self.settings, NOW + timedelta(hours=1))
        self.assertEqual(status["last_success_at"], NOW.isoformat())
        self.assertEqual(len(status["history"]), 1)
        self.assertEqual(status["last_error"], "network_timeout")
        self.assertNotIn(TOKEN, self.path.read_text())

    def test_provider_auth_rate_limit_and_invalid_payload_are_separate_from_network(self):
        for http_status, error in ((401, "provider_auth"), (403, "provider_auth"),
                                   (429, "provider_rate_limited"), (500, "provider_error"),
                                   (302, "provider_redirect")):
            with self.subTest(status=http_status):
                path = self.path.with_name(f"network_{http_status}.json")
                collector = self.collector(lambda request, code=http_status: httpx.Response(code, text=TOKEN))
                collector.path = path
                self.assertEqual(collector.refresh(NOW)["error_code"], error)
                self.assertEqual(network_evidence_status(path, self.settings, NOW)["history"], [])
        collector = self.collector(lambda request: httpx.Response(200, json=body(ip="not-an-ip")))
        self.assertEqual(collector.refresh(NOW)["error_code"], "invalid_payload")

    def test_missing_token_does_not_make_a_request_or_echo_environment(self):
        requests = []
        collector = self.collector(lambda request: requests.append(request), environ={})
        self.assertEqual(collector.refresh(NOW)["error_code"], "missing_token")
        self.assertFalse(requests)

    def test_ipv6_normalization_unknown_provider_type_and_empty_asn(self):
        payload = body("2001:4860:4860:0:0:0:0:8888", kind="marketing-residential")
        self.collector(lambda request: httpx.Response(200, json=payload)).refresh(NOW)
        latest = network_evidence_status(self.path, self.settings, NOW)["latest"]
        self.assertEqual(latest["ip"], "2001:4860:4860::8888")
        self.assertEqual(latest["asn_type"], "unknown")
        self.collector(lambda request: httpx.Response(200, json={"ip": "1.1.1.1"})).refresh(NOW + timedelta(hours=1))
        self.assertEqual(network_evidence_status(self.path, self.settings, NOW + timedelta(hours=1))["latest"]["asn"], "unknown")

    def test_corrupt_or_hardlinked_state_is_not_overwritten_and_http_is_not_called(self):
        self.path.parent.mkdir()
        self.path.write_text("broken-json")
        requests = []
        collector = self.collector(lambda request: requests.append(request))
        self.assertEqual(collector.refresh(NOW)["error_code"], "state_unavailable")
        self.assertEqual(self.path.read_text(), "broken-json")
        self.path.unlink()
        outside = Path(self.temp.name) / "outside.json"
        outside.write_text("do not touch")
        os.link(outside, self.path)
        self.assertEqual(collector.refresh(NOW)["error_code"], "state_unavailable")
        self.assertEqual(outside.read_text(), "do not touch")
        self.assertFalse(requests)

    def test_preflight_only_reads_and_prints_checkpoint_separately_from_timeout(self):
        from pipeline.cli import _print_network_preflight
        self.collector(lambda request: httpx.Response(200, json=body())).refresh(NOW)
        (self.path.parent / "delta_state.json").write_text(json.dumps({
            "facebook": {"last_error": "页面被重定向到 https://facebook.com/checkpoint"},
            "instagram": {"last_error": "net::ERR_CONNECTION_RESET"}}))
        output = io.StringIO()
        before = self.path.read_bytes()
        with patch("pipeline.cli.NetworkEvidenceSettings.load", return_value=self.settings), \
                patch("core.network_evidence.httpx.Client", side_effect=AssertionError("preflight network")), \
                contextlib.redirect_stdout(output):
            _print_network_preflight(self.path.parent, NOW)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIn("checkpoint", output.getvalue())
        self.assertIn("连接", output.getvalue())
        self.assertIn("不能证明", output.getvalue())
        self.assertEqual(detection_failure_kind("HTTP 429"), "rate_limited")
        self.assertEqual(detection_failure_kind("HTTP 403"), "session_or_permission")

    def test_info_logs_never_include_auth_secret(self):
        collector = self.collector(lambda request: httpx.Response(200, json=body()))
        with self.assertLogs("httpx", level=logging.INFO) as captured:
            collector.refresh(NOW)
        self.assertNotIn(TOKEN, "\n".join(captured.output))


if __name__ == "__main__":
    unittest.main()
