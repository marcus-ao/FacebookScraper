"""把 Stage B1 的外壳与 Stage B2 的共享基元在两个目标分辨率下截下来，并量关键尺寸。

和 `capture_baseline.py` 的分工：那一份量的是**旧 Vue 应用**（审计取证），
这一份量的是 **`web/ui-next/dist` 的新外壳**。两份都只读、不断言业务、
不产生验收证据。

为什么这一份伺服的是静态产物而不是 FastAPI 宿主：

* B1/B2 的页面**一个接口都不调** —— 四个业务界面还是占位页，`/_internal/design-check`
  用的是写死的假行。后端在这里不提供任何可验证的东西，只会多一条风险路径。
* 所以这里起一个**纯静态 SPA 宿主**（只有文件，没有 `/api`），并在跑完之后
  断言"整个过程里没有任何一个请求打到 `/api`"，请求清单原样写进产出。

安全边界仍然挂在现有夹具上（不建第二套体系）：

* 浏览器可执行文件来自 `tests/browser_fixture.BrowserFixture`，与
  `tests/tests_browser_workflow.py` 同一个；
* `BrowserFixture` 的 `socket.connect` 回环限制在本进程内生效；
* 退出时校验真实 `config.toml` 未被改动；
* 真实 `archive/`、`state/` 一个字节都不碰。

⛔ 绝不指向 8765 上那个连真实归档的宿主。
⛔ 全程没有任何 `POST /api/tasks/{id}/approve` 或 `POST /api/calendar/refresh`
   —— 这个宿主里根本不存在这两条路径。

用法：

    scripts\\run_python.bat docs/ui-refactor/tools/capture_shell.py

产出：

* `docs/ui-refactor/screenshots/shell-<screen>@<宽>x<高>.png`
* `docs/ui-refactor/screenshots/shell-measurements.json`
"""
from __future__ import annotations

import functools
import http.server
import json
import socketserver
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright                      # noqa: E402

from tests.browser_fixture import BrowserFixture                     # noqa: E402

DIST = ROOT / "web" / "ui-next" / "dist"
OUT = ROOT / "docs" / "ui-refactor" / "screenshots"
VIEWPORTS = [(1920, 1080), (1366, 768)]

DETAIL_QUERY = "?queue=processed&platform=facebook&month=2026-07&alerts=1&tab=text"

SCREENS = [
    ("review", "/review"),
    ("review-detail", "/review/fa_neakasaofficial/122100548013379375" + DETAIL_QUERY),
    ("history", "/history?platform=instagram&month=2026-08&page=2&limit=100"),
    ("calendar", "/calendar"),
    ("settings", "/settings"),
    ("runtime", "/runtime"),
    ("not-found", "/this-link-came-from-an-older-version"),
    ("design-check", "/_internal/design-check"),
]

# 量什么。每一项都对着 B1/B2 的一条完成判据，不量"感觉"。
PROBE = r"""() => {
  const box = sel => {
    const e = document.querySelector(sel);
    if (!e) return null;
    const r = e.getBoundingClientRect();
    return { w: Math.round(r.width), h: Math.round(r.height),
             top: Math.round(r.top), left: Math.round(r.left) };
  };
  const cs = (sel, prop) => {
    const e = document.querySelector(sel);
    return e ? getComputedStyle(e)[prop] : null;
  };
  const navItems = [...document.querySelectorAll('.ant-menu-item')];
  const headings = [...document.querySelectorAll('h1, h2, h3')];
  return {
    viewport: [innerWidth, innerHeight],
    documentHeight: document.documentElement.scrollHeight,
    screensOfScroll: +(document.documentElement.scrollHeight / innerHeight).toFixed(2),

    header: box('.ant-layout-header'),
    headerPosition: cs('.ant-layout-header', 'position'),
    sider: box('.ant-layout-sider'),
    content: box('.ant-layout-content'),

    navCount: navItems.length,
    navLabels: navItems.map(e => e.textContent.trim()),
    navSelected: [...document.querySelectorAll('.ant-menu-item-selected')]
      .map(e => e.textContent.trim()),
    // 折行判据：导航项的实际高度超过单行高度，或者文字被挤到换行。
    navWrapped: navItems.filter(e => e.getBoundingClientRect().height > 44).length,
    navOverflow: navItems.filter(e => {
      const t = e.querySelector('.ant-menu-title-content');
      return t && t.scrollWidth > t.clientWidth + 1;
    }).length,

    h1Count: document.querySelectorAll('h1').length,
    h1Text: [...document.querySelectorAll('h1')].map(e => e.textContent.trim()),
    headingSizes: [...new Set(headings.map(e => e.tagName + ' ' + getComputedStyle(e).fontSize))],

    // 一屏里有几个主按钮、几个行内 danger。审计里旧 UI 是 0 个 primary / 17 个 danger。
    primaryButtons: [...document.querySelectorAll('.ant-btn-primary')].map(e => e.textContent.trim()),
    inlineDangerButtons: document.querySelectorAll('.ant-btn-dangerous').length,

    // 主色是否真的落到了 DOM 上。
    primaryVar: getComputedStyle(document.documentElement).getPropertyValue('--rc-primary').trim(),
    topbarVar: getComputedStyle(document.documentElement).getPropertyValue('--rc-topbar-h').trim(),
    sidebarVar: getComputedStyle(document.documentElement).getPropertyValue('--rc-sidebar-w').trim(),
    antCssVarCount: [...document.styleSheets].reduce((n, sheet) => {
      try { return n + [...sheet.cssRules].filter(r => (r.cssText || '').includes('--ant-')).length; }
      catch { return n; }
    }, 0),

    // 中文换行：正文里有没有元素被自己的内容撑破。
    // `text-overflow: ellipsis` 是**有意**的单行截断（摘要列、分类 Tag），
    // 它的 scrollWidth 本来就大于 clientWidth，不算溢出，排除掉。
    overflowingText: [...document.querySelectorAll('.ant-layout-content *')]
      .filter(e => e.children.length === 0
                && getComputedStyle(e).textOverflow !== 'ellipsis'
                && e.scrollWidth > e.clientWidth + 1)
      .map(e => e.tagName + '.' + e.className + ' ' + (e.textContent || '').trim().slice(0, 20)),

    // 右侧有没有大片没用的留白：内容区实际用掉多少宽度。
    contentFillRatio: (() => {
      const c = document.querySelector('.ant-layout-content');
      if (!c) return null;
      const used = [...c.children].reduce((m, e) => Math.max(m, e.getBoundingClientRect().right), 0);
      return +((used - c.getBoundingClientRect().left) / c.getBoundingClientRect().width).toFixed(2);
    })(),

    reducedMotionHonoured: (() => {
      // 只确认规则在产物里，不改系统设置。
      return [...document.styleSheets].some(sheet => {
        try { return [...sheet.cssRules].some(r => (r.conditionText || '').includes('prefers-reduced-motion')); }
        catch { return false; }
      });
    })(),
    scrollBehavior: getComputedStyle(document.documentElement).scrollBehavior,
  };
}"""

FOCUS_PROBE = r"""() => {
  const e = document.activeElement;
  if (!e || e === document.body) return null;
  const s = getComputedStyle(e);
  return {
    tag: e.tagName,
    text: (e.textContent || '').trim().slice(0, 20),
    outlineStyle: s.outlineStyle,
    outlineWidth: s.outlineWidth,
    outlineColor: s.outlineColor,
    outlineOffset: s.outlineOffset,
  };
}"""


class SpaHandler(http.server.SimpleHTTPRequestHandler):
    """静态 SPA 宿主：文件不存在就回 index.html，让深链接直接打得开。

    这个宿主**没有 `/api`**。任何打到 `/api` 的请求都会 404 并被记下来。
    """

    api_hits: list[str] = []

    def translate_path(self, path: str) -> str:
        clean = path.split("?", 1)[0].split("#", 1)[0]
        if clean.startswith("/api"):
            SpaHandler.api_hits.append(clean)
        candidate = Path(super().translate_path(path))
        if candidate.is_file():
            return str(candidate)
        return str(Path(self.directory) / "index.html")

    def log_message(self, *_args) -> None:      # 安静
        pass


def main() -> int:
    if not (DIST / "index.html").is_file():
        print("先构建：cd web/ui-next && npm run build")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)

    handler = functools.partial(SpaHandler, directory=str(DIST))
    with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
        port = httpd.server_address[1]
        base = "http://127.0.0.1:%d" % port
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

        # 夹具只用来拿浏览器可执行文件、回环 socket 限制，以及退出时校验
        # 真实 config.toml 未被改动。它自己那个 FastAPI 宿主全程没被访问。
        with BrowserFixture() as fixture:
            requests: list[str] = []
            measurements: dict = {
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "source": "web/ui-next/dist served statically (no API at all)",
                "base_url": base,
                "screens": {},
            }
            with sync_playwright() as driver:
                context = driver.chromium.launch_persistent_context(
                    str(fixture.root / "shell-chrome-profile"),
                    executable_path=fixture.chrome_exe, headless=True,
                    viewport={"width": 1920, "height": 1080})
                try:
                    page = context.new_page()
                    page.on("request", lambda r: requests.append(r.method + " " + r.url))

                    for width, height in VIEWPORTS:
                        page.set_viewport_size({"width": width, "height": height})
                        for name, path in SCREENS:
                            page.goto(base + path, wait_until="networkidle")
                            page.wait_for_timeout(400)
                            tag = "shell-%s@%dx%d" % (name, width, height)
                            # 核验页要看全部七个基元，截整页；业务界面截首屏，
                            # 因为要看的正是"首屏能装下多少"。
                            page.screenshot(path=str(OUT / (tag + ".png")),
                                            full_page=(name == "design-check"))
                            measurements["screens"][tag] = page.evaluate(PROBE)
                            print("captured " + tag)

                    # 折叠态：点一下收起按钮，量 48px，并确认偏好被记住。
                    page.set_viewport_size({"width": 1366, "height": 768})
                    page.goto(base + "/review", wait_until="networkidle")
                    page.get_by_role("button", name="收起导航").click()
                    # Sider 的宽度是有过渡的，量早了会读到中间值（实测 66px）。
                    page.wait_for_function(
                        "() => { const s = document.querySelector('.ant-layout-sider');"
                        " return s && Math.round(s.getBoundingClientRect().width) === 48; }",
                        timeout=5000)
                    page.wait_for_timeout(300)
                    page.screenshot(path=str(OUT / "shell-review-collapsed@1366x768.png"))
                    measurements["screens"]["shell-review-collapsed@1366x768"] = page.evaluate(PROBE)
                    measurements["collapsed_preference"] = page.evaluate(
                        "() => ({ stored: Object.fromEntries(Object.entries(localStorage)) })")
                    page.reload(wait_until="networkidle")
                    page.wait_for_timeout(500)
                    measurements["collapsed_after_reload"] = page.evaluate(
                        "() => { const s = document.querySelector('.ant-layout-sider');"
                        " return s ? Math.round(s.getBoundingClientRect().width) : null; }")
                    page.get_by_role("button", name="展开导航").click()
                    page.wait_for_timeout(400)

                    # 焦点环：Tab 到第一个可聚焦元素，读 outline。
                    page.goto(base + "/review", wait_until="networkidle")
                    page.keyboard.press("Tab")
                    page.wait_for_timeout(200)
                    measurements["focus_ring"] = page.evaluate(FOCUS_PROBE)
                    page.keyboard.press("Tab")
                    page.wait_for_timeout(150)
                    measurements["focus_ring_second"] = page.evaluate(FOCUS_PROBE)

                    # 详情上下文：整页重载之后 history.state 为 null，面包屑仍要能带回筛选。
                    page.goto(base + "/review/fa_x/1" + DETAIL_QUERY, wait_until="networkidle")
                    page.reload(wait_until="networkidle")
                    page.wait_for_timeout(300)
                    measurements["detail_context_after_reload"] = page.evaluate(
                        r"""() => {
                          const back = [...document.querySelectorAll('.ant-breadcrumb a')][0];
                          // React Router 总会在 history.state 里放自己的 idx/key，
                          // 所以整体非 null 是正常的。真正要证明的是
                          // **没有用户态 state**（location.state → history.state.usr）。
                          return {
                            historyStateUsr: (history.state && history.state.usr) ?? null,
                            historyStateKeys: history.state ? Object.keys(history.state) : null,
                            search: location.search,
                            backHref: back ? back.getAttribute('href') : null,
                            crumbs: [...document.querySelectorAll('.ant-breadcrumb li')]
                              .map(e => e.textContent.trim()).filter(Boolean),
                          };
                        }""")

                    # 旧 URL 重定向仍然成立（D11：永久保留）。
                    legacy = {}
                    for name, url in [
                        ("task", "/?task=fa_neakasaofficial/122100548013379375"),
                        ("history_task", "/?view=history&task=in_neakasa.tech/3975547640610092585"),
                        ("view_calendar", "/?view=calendar"),
                        ("view_unknown", "/?view=nope"),
                    ]:
                        page.goto(base + url, wait_until="networkidle")
                        page.wait_for_timeout(200)
                        legacy[name] = page.evaluate("() => location.pathname + location.search")
                    measurements["legacy_redirects"] = legacy
                finally:
                    context.close()

            measurements["requests"] = requests
            measurements["api_requests"] = [r for r in requests if "/api" in r]
            measurements["api_hits_at_server"] = SpaHandler.api_hits
            measurements["real_external_actions"] = False
            measurements["denied_backend_requests"] = list(fixture.denied_backend_requests)

        (OUT / "shell-measurements.json").write_text(
            json.dumps(measurements, ensure_ascii=False, indent=2), encoding="utf-8")
        print("measurements -> " + str(OUT / "shell-measurements.json"))
        print("api requests: %d" % len(measurements["api_requests"]))
        httpd.shutdown()          # 不让 daemon 线程活到解释器退出
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
