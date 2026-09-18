"""Image catalog preflight through the real SDK; isolated ledger and no network."""
from __future__ import annotations

import base64
import sys
import tempfile
import unittest
from pathlib import Path

import httpx
from openai import OpenAI
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import paid_requests as P
from localize import images as L


class CatalogTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / "state"
        self.source = self.root / "source.png"
        Image.new("RGB", (816, 816), "white").save(self.source)
        self.settings = L.Settings()
        self.settings.gap = 0
        self.requests = []
        self.list_reply = {"object": "list", "data": []}
        self.detail_reply = {
            "id": "gpt-image-2", "object": "model", "created": 1,
            "owned_by": "OpenAI",
        }
        self.edit_reply = {
            "created": 1,
            "data": [{"b64_json": base64.b64encode(self.source.read_bytes()).decode()}],
            "usage": {"input_tokens": 12, "output_tokens": 7,
                      "input_tokens_details": {"text_tokens": 3, "image_tokens": 9}},
        }
        self.budget_blocked = False
        self.preflights = 0
        self.client = OpenAI(
            api_key="offline-placeholder", base_url=L.INFERERA_API_URL,
            max_retries=2, http_client=httpx.Client(transport=httpx.MockTransport(self.respond)))
        self.addCleanup(self.client.close)
        self.editor = L.ImageEditor(self.settings, client=self.client,
            paid_controller=P.RequestController(self.state, preflight=self.preflight))

    def preflight(self):
        self.preflights += 1
        if self.budget_blocked:
            raise P.PaidRequestBlocked("offline budget exhausted")

    def respond(self, request):
        self.assertEqual(request.url.host, "api.inferera.com")
        self.assertEqual(request.headers["authorization"], "Bearer offline-placeholder")
        self.requests.append((request.method, request.url.path))
        if request.method == "GET":
            self.assertEqual(P.load_events(self.state), [])
            if request.url.path == "/v1/models":
                reply = self.list_reply
            else:
                self.assertEqual(request.url.path, "/v1/models/gpt-image-2")
                reply = self.detail_reply
            if reply == "timeout":
                raise httpx.ReadTimeout("offline timeout", request=request)
            if isinstance(reply, int):
                return httpx.Response(reply, json={"error": {"message": "secret-sentinel"}})
            return httpx.Response(200, json=reply)
        self.assertEqual((request.method, request.url.path), ("POST", "/v1/images/edits"))
        self.assertEqual(P.load_events(self.state)[-1]["event"], P.EVENT_STARTED)
        self.assertIn(b'\r\n\r\ngpt-image-2\r\n', request.read())
        return httpx.Response(200, json=self.edit_reply)

    def edit(self):
        return self.editor.edit(self.source, "Translate TEST into German", "816x816")

    def assert_preflight_stops(self, error_type, paths):
        for _ in range(2):
            with self.assertRaises(error_type) as raised:
                self.edit()
            self.assertNotIn("secret-sentinel", str(raised.exception))
            self.assertTrue(L._is_fatal_api_error(raised.exception))
        self.assertEqual(self.requests, [("GET", path) for path in paths])
        self.assertEqual(P.load_events(self.state), [])
        self.assertEqual(self.preflights, 0)

    def test_empty_list_uses_exact_detail_before_one_paid_edit(self):
        result = self.edit()
        self.assertEqual((result.model, result.model_verification), ("gpt-image-2", "catalog"))
        self.assertEqual(L.decode_image_payload(result.b64_json), self.source.read_bytes())
        self.editor.finalize_paid(True)
        self.editor.verify_model_available()
        self.assertEqual(self.requests, [
            ("GET", "/v1/models"), ("GET", "/v1/models/gpt-image-2"),
            ("POST", "/v1/images/edits"),
        ])
        events = P.load_events(self.state)
        self.assertEqual(sum(row["event"] == P.EVENT_STARTED for row in events), 1)
        self.assertEqual(events[-1]["event"], "accepted")

    def test_exact_list_never_needs_detail(self):
        self.list_reply["data"] = [self.detail_reply]
        self.detail_reply = 503
        result = self.edit()
        self.assertEqual(result.model, "gpt-image-2")
        self.assertEqual(self.requests, [("GET", "/v1/models"), ("POST", "/v1/images/edits")])

    def test_nonempty_list_missing_model_still_stops(self):
        self.list_reply["data"] = [{"id": "gpt-image-2-free", "object": "model"}]
        self.assert_preflight_stops(L.ModelUnavailableError, ["/v1/models"])

    def test_malformed_or_error_list_is_not_an_empty_catalog(self):
        for reply in ({"object": "list"}, {"data": None}, {"data": {}},
                      {"data": [{"model_id": "gpt-image-2"}]},
                      {"data": [], "error": {"message": "secret-sentinel"}},
                      {"data": [], "success": False}):
            with self.subTest(reply=reply):
                self.list_reply = reply
                self.requests.clear()
                self.editor = L.ImageEditor(self.settings, client=self.client,
                    paid_controller=P.RequestController(self.state, preflight=self.preflight))
                self.assert_preflight_stops(L.ModelCatalogPreflightError, ["/v1/models"])

    def test_list_http_failure_never_uses_detail_or_retries(self):
        for reply in (401, 403, 503, "timeout"):
            with self.subTest(reply=reply):
                self.list_reply = reply
                self.requests.clear()
                self.editor = L.ImageEditor(self.settings, client=self.client,
                    paid_controller=P.RequestController(self.state, preflight=self.preflight))
                self.assert_preflight_stops(L.ModelCatalogPreflightError, ["/v1/models"])

    def test_empty_list_detail_failure_stops_before_paid_ledger(self):
        for reply in (401, 403, 404, 503, "timeout",
                      {"error": {"message": "secret-sentinel"}},
                      {"id": "gpt-image-2", "success": False}):
            with self.subTest(reply=reply):
                self.detail_reply = reply
                self.requests.clear()
                self.editor = L.ImageEditor(self.settings, client=self.client,
                    paid_controller=P.RequestController(self.state, preflight=self.preflight))
                self.assert_preflight_stops(L.ModelCatalogPreflightError,
                    ["/v1/models", "/v1/models/gpt-image-2"])

    def test_detail_requires_exact_id_not_free_alias_or_nested_payload(self):
        for reply in ({"id": "gpt-image-2-free"}, {"id": "GPT-IMAGE-2"},
                      {"id": "gpt-image-2.5"}, {"id": " gpt-image-2 "}, {},
                      {"data": {"id": "gpt-image-2"}}):
            with self.subTest(reply=reply):
                self.detail_reply = reply
                self.requests.clear()
                self.editor = L.ImageEditor(self.settings, client=self.client,
                    paid_controller=P.RequestController(self.state, preflight=self.preflight))
                self.assert_preflight_stops(L.ModelUnavailableError,
                    ["/v1/models", "/v1/models/gpt-image-2"])

    def test_detail_success_does_not_bypass_budget(self):
        self.budget_blocked = True
        with self.assertRaises(P.PaidRequestBlocked):
            self.edit()
        self.assertEqual(self.requests, [("GET", "/v1/models"), ("GET", "/v1/models/gpt-image-2")])
        self.assertEqual(P.load_events(self.state), [])
        self.assertEqual(self.preflights, 1)

    def test_detail_success_still_rejects_wrong_edit_model_and_keeps_cost(self):
        self.edit_reply["model"] = "gpt-image-2-free"
        with self.assertRaises(L.ModelMismatchError):
            self.edit()
        events = P.load_events(self.state)
        self.assertEqual(events[-1]["event"], "output_rejected")
        self.assertTrue(any(row.get("usage", {}).get("output_tokens") == 7 for row in events))
        self.assertEqual(sum(method == "POST" for method, _ in self.requests), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
