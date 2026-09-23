"""通过临时 FastAPI 配置与 dist 验证深链接及 404，不依赖浏览器夹具的路由回落。"""
import json
import re
import shutil
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import config

HTML = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
INDEX_MARKER = "<!doctype html><title>SPA fixture</title><div id=root></div>"

# 账号名可含点，扩展名判断只看路径末段。
SPA_ROUTES = ["/", "/review", "/history", "/calendar", "/runtime",
              "/review/fa_account/123", "/history/fa_account/123",
              "/review/in_neakasa.tech/9000000030", "/history/in_neakasa.tech/9000000030"]

_sandbox = None
_patch = None
_client = None


def setUpModule():
    global _sandbox, _patch, _client
    _sandbox = ROOT / "state" / f"spa-static-test-{uuid.uuid4().hex[:8]}"
    dist = _sandbox / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX_MARKER, encoding="utf-8")
    (dist / "assets" / "app.js").write_text("export const built = true\n", encoding="utf-8")
    (_sandbox / "archive").mkdir()

    text = (ROOT / "config.toml").read_text(encoding="utf-8")
    entry = f'web_dist = "{dist.relative_to(ROOT).as_posix()}"'
    text, replaced = re.subn(r"(?m)^\s*web_dist\s*=.*$", entry, text)
    if replaced > 1:
        raise AssertionError("config.toml 里出现多个 [paths].web_dist")
    if not replaced and re.search(r"(?m)^\s*\[paths\]", text):
        text = re.sub(r"(?m)^(\s*\[paths\][^\r\n]*\r?\n)", r"\1" + entry + "\n", text, count=1)
    elif not replaced:
        text = text.rstrip("\n") + "\n\n[paths]\n" + entry + "\n"
    cfg_path = _sandbox / "config.toml"
    cfg_path.write_text(text, encoding="utf-8")
    runtime = _sandbox / "runtime.toml"
    runtime.write_text("[paths]\narchive = " + json.dumps(str(_sandbox / "archive"))
                       + "\nstate = " + json.dumps(str(_sandbox / "state")) + "\n", encoding="utf-8")

    _patch = patch.object(config, "_cfg", config.Config(cfg_path, runtime_path=runtime))
    _patch.start()
    # 先替换配置再导入，静态目录在导入时确定。
    from fastapi.testclient import TestClient
    from web.api.app import DIST, app

    assert DIST == dist.resolve(), f"临时 dist 没有生效，量到的是 {DIST}"
    _client = TestClient(app, base_url='http://127.0.0.1:8765', client=('127.0.0.1', 41000))
    _client.__enter__()


def tearDownModule():
    if _client is not None:
        _client.__exit__(None, None, None)
    if _patch is not None:
        _patch.stop()
    if _sandbox is not None:
        shutil.rmtree(_sandbox, ignore_errors=True)


class SpaFallbackTests(unittest.TestCase):
    """Case 1：前端路由的每一个路径，刷新都要拿到 index.html。"""

    def test_every_front_end_route_serves_the_app_shell(self):
        for path in SPA_ROUTES:
            with self.subTest(path=path):
                response = _client.get(path, headers=HTML)
                self.assertEqual(response.status_code, 200)
                self.assertIn("text/html", response.headers["content-type"])
                self.assertEqual(response.text, INDEX_MARKER)

    def test_query_string_and_trailing_segments_do_not_change_it(self):
        # 详情页带着来源列表的全套筛选，刷新时这些都在 URL 上。
        for path in ["/review?queue=review&platform=facebook&tag=Riko",
                     "/history/fa_account/123?page=2&limit=20&tab=images",
                     "/review/fa_account/123?tab=localization"]:
            with self.subTest(path=path):
                self.assertEqual(_client.get(path, headers=HTML).text, INDEX_MARKER)


class StaticAssetTests(unittest.TestCase):
    """Case 2：漏掉的产物必须当场 404，不能被回落藏起来。"""

    def test_existing_asset_is_served_as_itself(self):
        response = _client.get("/assets/app.js")
        self.assertEqual(response.status_code, 200)
        self.assertIn("javascript", response.headers["content-type"])
        self.assertIn("built", response.text)

    def test_missing_asset_stays_404_even_when_the_client_accepts_html(self):
        # 缺资源须返回 404，不能用 HTML 回落掩盖。
        response = _client.get("/assets/does-not-exist.js", headers=HTML)
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(INDEX_MARKER, response.text)

    def test_any_path_with_an_extension_is_treated_as_an_asset(self):
        for path in ["/favicon.ico", "/nope.css", "/review/thing.map"]:
            with self.subTest(path=path):
                response = _client.get(path, headers=HTML)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn(INDEX_MARKER, response.text)


class ApiBoundaryTests(unittest.TestCase):
    """Case 3 与 Case 6：接口既不能被吞掉，它的 404 也必须还是 JSON。"""

    def test_unknown_api_path_keeps_json_404(self):
        response = _client.get("/api/does-not-exist", headers=HTML)
        self.assertEqual(response.status_code, 404)
        self.assertIn("application/json", response.headers["content-type"])
        self.assertEqual(response.json(), {"detail": "Not Found"})
        self.assertNotIn(INDEX_MARKER, response.text)

    def test_real_api_route_still_wins_over_the_static_mount(self):
        # 挂载点是 "/"，真实接口必须仍由 FastAPI 的路由处理。
        response = _client.get("/api/tasks", headers=HTML)
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/json", response.headers["content-type"])
        self.assertIn("tasks", response.json())

    def test_api_404_is_not_html_even_for_a_browser_navigation(self):
        # 浏览器直接敲一个不存在的接口地址时，Accept 也是 text/html。
        response = _client.get("/api/tasks/does/not/exist/at/all", headers=HTML)
        self.assertNotIn(INDEX_MARKER, response.text)


class NonHtmlClientTests(unittest.TestCase):
    """Case 4：不收 HTML 的请求不回落。"""

    def test_json_client_gets_404_not_the_app_shell(self):
        response = _client.get("/review", headers={"Accept": "application/json"})
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(INDEX_MARKER, response.text)

    def test_script_style_request_gets_404(self):
        # `<script src>` / `<link href>` 发的是 */* 或具体类型，不是 text/html。
        for accept in ["*/*", "application/javascript", "text/css", ""]:
            with self.subTest(accept=accept):
                response = _client.get("/review", headers={"Accept": accept})
                self.assertEqual(response.status_code, 404)
                self.assertNotIn(INDEX_MARKER, response.text)


class MethodAndPathSafetyTests(unittest.TestCase):
    """Case 5 与安全边界。断言的是实测到的行为，不是猜的。"""

    def test_head_on_a_front_end_route_matches_the_get(self):
        response = _client.head("/review", headers=HTML)
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertEqual(response.content, b"")

    def test_head_on_an_asset_keeps_the_asset_content_type(self):
        response = _client.head("/assets/app.js")
        self.assertEqual(response.status_code, 200)
        self.assertIn("javascript", response.headers["content-type"])
        self.assertEqual(response.content, b"")

    def test_wrong_method_is_405_not_the_app_shell(self):
        # 前端路径不接受写方法。
        for method in ["POST", "PUT", "DELETE", "PATCH"]:
            with self.subTest(method=method):
                response = _client.request(method, "/review", headers=HTML)
                self.assertEqual(response.status_code, 405)
                self.assertNotIn(INDEX_MARKER, response.text)

    def test_path_traversal_is_still_refused_by_staticfiles(self):
        # SPA 回落仍须满足静态路径边界。
        for path in ["/../config.toml", "/%2e%2e/config.toml",
                     "/assets/../../config.toml", "/....//config.toml"]:
            with self.subTest(path=path):
                response = _client.get(path, headers=HTML)
                self.assertNotIn("[paths]", response.text)
                self.assertNotIn("web_dist", response.text)


if __name__ == "__main__":
    unittest.main()
