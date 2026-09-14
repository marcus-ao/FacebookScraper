"""把当前 Vue 审校台的六个界面在两个目标分辨率下截下来，并量出关键尺寸。

这是审计的取证脚本，不是回归测试：它只读、不断言、不产生验收证据。数据来自
`audit_fixture_host` 的隔离夹具（真实 `archive/`、`state/` 一个字节都不碰），
所以截出来的是"有信号时 UI 长什么样"——真实归档当前 26 篇全是 `not_ready`，
截不出告警、风险、人工稿这些形态。

产出：

* `docs/ui-refactor/screenshots/<screen>@<宽>x<高>.png`
* `docs/ui-refactor/screenshots/measurements.json`（行高、首屏条目数、按钮层级计数）

用法（需要已安装的 Python Playwright 与 `Config.chrome_exe`，与
`tests/tests_browser_workflow.py` 同一套）：

    scripts\\run_python.bat docs/ui-refactor/tools/capture_baseline.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright                      # noqa: E402

from tests.browser_fixture import BrowserFixture                     # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit_fixture_host import SAMPLES, AuditHost                    # noqa: E402

OUT = ROOT / "docs" / "ui-refactor" / "screenshots"
VIEWPORTS = [(1920, 1080), (1366, 768)]

# 每个界面量什么。选的都是"能不能扫得动、能不能一眼看出主行动"这两件事的依据。
PROBE = """() => {
  const px = e => e ? Math.round(e.getBoundingClientRect().height) : null;
  const rows = [...document.querySelectorAll('.row, .history-rows li')];
  const bar = [...document.querySelectorAll('header.bar button')].map(b => b.textContent.trim());
  return {
    viewport: [innerWidth, innerHeight],
    documentHeight: document.documentElement.scrollHeight,
    screensOfScroll: +(document.documentElement.scrollHeight / innerHeight).toFixed(2),
    chromeBeforeContent: rows.length ? Math.round(rows[0].getBoundingClientRect().top) : null,
    rowCount: rows.length,
    rowHeights: [...new Set(rows.map(px))].sort((a, b) => a - b),
    rowsAboveFold: rows.filter(r => r.getBoundingClientRect().bottom <= innerHeight).length,
    primaryButtons: [...document.querySelectorAll('.btn-primary')].map(b => b.textContent.trim()),
    dangerButtons: [...document.querySelectorAll('.btn-danger')].map(b => b.textContent.trim()),
    dangerButtonCount: document.querySelectorAll('.btn-danger').length,
    statusChips: document.querySelectorAll('.tag').length,
    borderedRegions: [...document.querySelectorAll('main *')].filter(e => {
      const s = getComputedStyle(e);
      return s.borderStyle !== 'none' && parseFloat(s.borderWidth) >= 1
             && e.getBoundingClientRect().height > 60;
    }).length,
    sectionHeadingSizes: [...document.querySelectorAll('main h2, main h3, main h4')]
      .map(h => h.tagName + ' ' + getComputedStyle(h).fontSize + ' — ' + h.textContent.trim().slice(0, 18)),
    stickyBarButtons: bar,
    nativeSelects: document.querySelectorAll('main select').length,
  };
}"""


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with BrowserFixture() as fixture:
        host = AuditHost(fixture)
        for index, row in enumerate(SAMPLES):
            host.add(*row, age_days=index + 2)
        host.act("snoozed", "snoozed", reason="等运营确认德国站落地页", snooze_days=3)
        host.act("skipped", "skipped", reason="美国限定活动，德国站不发")
        host.act("handed", "handed_off", handoff_url="https://example.invalid/manual/post")

        screens = [
            ("review-list", "/"),
            ("review-detail-risk", "/?task=" + host.ids["risk"]),
            ("review-detail-money", "/?task=" + host.ids["money"]),
            ("review-detail-carousel", "/?task=" + host.ids["carousel"]),
            ("history", "/?view=history"),
            ("calendar", "/?view=calendar"),
            ("settings", "/?view=settings"),
            ("runtime", "/?view=runtime"),
        ]
        measurements: dict = {"captured_at": datetime.now(timezone.utc).isoformat(),
                              "source": "offline audit fixture (isolated archive/state)",
                              "screens": {}}
        with sync_playwright() as driver:
            context = driver.chromium.launch_persistent_context(
                str(fixture.root / "audit-chrome-profile"),
                executable_path=fixture.chrome_exe, headless=True,
                viewport={"width": 1920, "height": 1080})
            try:
                page = context.new_page()
                for width, height in VIEWPORTS:
                    page.set_viewport_size({"width": width, "height": height})
                    for name, path in screens:
                        page.goto(fixture.base_url + path, wait_until="networkidle")
                        page.wait_for_timeout(900)
                        tag = "%s@%dx%d" % (name, width, height)
                        page.screenshot(path=str(OUT / (tag + ".png")))
                        measurements["screens"][tag] = page.evaluate(PROBE)
                        print("captured " + tag)
                # 编辑态与决定对话框只在 1366 下取一次：它们的问题跟宽度无关。
                page.set_viewport_size({"width": 1366, "height": 768})
                page.goto(fixture.base_url + "/?task=" + host.ids["risk"], wait_until="networkidle")
                page.get_by_role("button", name="编辑德语").click()
                page.wait_for_timeout(900)
                page.screenshot(path=str(OUT / "review-detail-editing@1366x768.png"))
                measurements["screens"]["review-detail-editing@1366x768"] = page.evaluate(PROBE)
                page.get_by_role("button", name="放弃修改").click()
                page.wait_for_timeout(400)
                page.get_by_role("button", name="这篇不发").click()
                page.wait_for_timeout(600)
                page.screenshot(path=str(OUT / "decision-dialog@1366x768.png"))
                measurements["screens"]["decision-dialog@1366x768"] = page.evaluate(
                    """() => {
                      const dlg = document.querySelector('[role="dialog"]');
                      return {
                        dialogSize: dlg && [Math.round(dlg.getBoundingClientRect().width),
                                            Math.round(dlg.getBoundingClientRect().height)],
                        activeElementAfterOpen: document.activeElement.tagName,
                        confirmButtonClass: dlg && [...dlg.querySelectorAll('footer button')]
                          .map(b => b.textContent.trim() + ':' + b.className),
                      };
                    }""")
                print("captured editing + dialog")
            finally:
                context.close()
        (OUT / "measurements.json").write_text(
            json.dumps(measurements, ensure_ascii=False, indent=2), encoding="utf-8")
        print("measurements -> " + str(OUT / "measurements.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
