"""使用隔离数据测量界面与交互；外部动作均由夹具阻断。"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright  # noqa: E402
from ui_fixture import BrowserFixture, UIFixture  # noqa: E402

SMALL = {"width": 1366, "height": 768}
LARGE = {"width": 1920, "height": 1080}


def image_requests(ui):
    return [row for row in ui.requests if "/image/" in row["path"]]


def probe_images(page, ui, out):
    """正文页不得提前下载未查看的完整图片。"""
    ui.requests.clear()
    page.goto(f"{ui.fx.base_url}/review/{ui.fx.fb_id}", wait_until="networkidle")
    page.wait_for_timeout(600)
    before = image_requests(ui)
    out["images_requested_on_text_tab"] = len(before)
    out["images_paths_on_text_tab"] = sorted({row["path"] for row in before})
    out["image_count_in_detail"] = len(ui.fx.client.get(f"/api/tasks/{ui.fx.fb_id}").json()["images"])
    assert not before, f"正文页就请求了图片：{out['images_paths_on_text_tab']}"

    page.get_by_role("tab", name=re.compile("^图片")).first.click()
    page.wait_for_timeout(600)
    after = image_requests(ui)
    out["images_requested_after_opening_tab"] = len(after)
    assert after, "打开图片页之后仍然没有请求图片"


def probe_localization_state(page, ui, out):
    """已打开的标签页保留 DOM，避免丢失付费生成的候选。"""
    ui.requests.clear()
    detail_url = f"{ui.fx.base_url}/review/{ui.fx.fb_id}"
    page.goto(detail_url, wait_until="networkidle")
    page.wait_for_timeout(400)
    tags = page.locator('section[aria-label="话题标签选择"]')
    out["localization_mounted_before_visit"] = tags.count()
    assert tags.count() == 0, "没打开过标签页就已经挂载"

    page.get_by_role("tab", name="话题标签与链接", exact=True).click()
    page.wait_for_timeout(300)
    out["localization_mounted_after_visit"] = tags.count()
    assert tags.count() == 1, "打开标签页后没有挂载"

    page.get_by_role("tab", name="正文对照", exact=True).click()
    page.wait_for_timeout(300)
    out["localization_still_in_dom_after_switch"] = tags.count()
    out["images_still_in_dom_on_text_tab"] = page.locator('section[aria-label="图片对照"]').count()
    assert tags.count() == 1, "切回正文后标签页被卸载，已生成的付费候选会丢"


def probe_density(page, ui, out):
    """按 web/ui/DESIGN.md 的密度口径测量当前页面。"""
    result = {}
    for name, url in [("review", "/review"), ("history", "/history"),
                      ("detail", f"/review/{ui.fx.fb_id}"), ("calendar", "/calendar"),
                      ("settings", "/settings"), ("runtime", "/runtime")]:
        for label, size in [("1366x768", SMALL), ("1920x1080", LARGE)]:
            page.set_viewport_size(size)
            page.goto(ui.fx.base_url + url, wait_until="networkidle")
            page.wait_for_timeout(500)
            result[f"{name}@{label}"] = page.evaluate(
                """() => {
                  const vh = window.innerHeight;
                  const rows = [...document.querySelectorAll('tr[data-task-id]')];
                  const primary = [...document.querySelectorAll('.ant-btn-primary')]
                    .filter(el => el.offsetParent !== null);
                  const danger = [...document.querySelectorAll('.ant-btn-dangerous')]
                    .filter(el => el.offsetParent !== null);
                  return {
                    rowsAboveFold: rows.filter(r => r.getBoundingClientRect().bottom <= vh).length,
                    rowHeights: [...new Set(rows.map(r => Math.round(r.getBoundingClientRect().height)))],
                    documentHeight: document.documentElement.scrollHeight,
                    screensOfScroll: +(document.documentElement.scrollHeight / vh).toFixed(2),
                    h1: document.querySelectorAll('h1').length,
                    primaryVisible: primary.length,
                    primaryLabels: primary.map(el => el.textContent.trim()),
                    dangerInline: danger.length,
                    horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
                    headingSizes: [...document.querySelectorAll('h1,h2,h3')].map(
                      el => el.tagName + ':' + getComputedStyle(el).fontSize),
                  };
                }""")
    page.set_viewport_size(SMALL)
    out["density"] = result


def probe_marks_keyboard(page, ui, out):
    ui.requests.clear()
    page.goto(f"{ui.fx.base_url}/review/{ui.fx.fb_id}", wait_until="networkidle")
    page.wait_for_timeout(400)
    out["mark_count_text"] = page.locator('[data-testid="english-prose"] mark').count()
    page.keyboard.press("n")
    page.wait_for_timeout(200)
    out["active_after_n"] = page.locator("mark.mk-active").count()
    out["explanation_role_status"] = page.locator('p[role="status"]').count()


def probe_calendar_semantics(page, ui, out):
    """检查日历语义及柏林“今天”，夹具覆盖当前月份。"""
    berlin_now = datetime.now(ZoneInfo("Europe/Berlin"))
    start = berlin_now.replace(day=1).date()
    end = (start + timedelta(days=32)).replace(day=1)
    payload = ui.fx.client.get("/api/calendar").json()
    payload.update(refresh_available=True, cached_at="2026-09-13T08:00:00Z", status="cached",
                   display_start=f"{start}T00:00:00+02:00",
                   display_end_exclusive=f"{end}T00:00:00+02:00",
                   month_ui=start.strftime("%Y-%m"))
    payload["coverage"]["matches_current_month"] = True
    payload["cards"] = []
    ui.overrides[("GET", "/api/calendar")] = (200, payload)
    page.goto(ui.fx.base_url + "/calendar", wait_until="networkidle")
    page.wait_for_timeout(400)
    out["calendar_says_published"] = page.get_by_text("已发布", exact=True).count()

    out["calendar_invalid_list_roles"] = page.evaluate(
        """() => ({ lists: document.querySelectorAll('[role="list"]').length,
                    listitems: document.querySelectorAll('[role="listitem"]').length })""")
    assert out["calendar_invalid_list_roles"] == {"lists": 0, "listitems": 0}, \
        f'月历仍留着无效的 list 语义：{out["calendar_invalid_list_roles"]}'

    days = page.locator("[data-day]")
    out["calendar_day_cells"] = days.count()
    assert out["calendar_day_cells"] > 0, "data-day 定位不到日期格，浏览器断言会失去抓手"

    berlin_today = datetime.now(ZoneInfo("Europe/Berlin")).date().isoformat()
    in_range = payload["display_start"][:10] <= berlin_today < payload["display_end_exclusive"][:10]
    marked = page.locator("[data-today]")
    out["calendar_berlin_today"] = berlin_today
    out["calendar_today_in_displayed_month"] = in_range
    out["calendar_today_marked_cells"] = marked.count()
    assert marked.count() == (1 if in_range else 0), \
        f"「今天」标记数不对：柏林今天 {berlin_today}，在范围内={in_range}"
    if in_range:
        out["calendar_today_cell_day"] = marked.first.get_attribute("data-day")
        assert out["calendar_today_cell_day"] == berlin_today, "标到了别的日期格上"


def probe_approval_time(page, ui, out):
    """R-15 / FIX B：清空排期时间之后不许自动填回来，灰按钮的原因键盘要够得到。"""
    row = next(row for row in ui.list_data["tasks"]
               if row["status"] == "pending_review" and not row["hard_alerts"])
    task_id = row["id"]
    options = ui.fx.client.get(f"/api/tasks/{task_id}/approval-options").json()
    options.update(available=True, reason="", fingerprint="probe-fingerprint",
                   earliest="2026-09-14T08:00:00+02:00", latest="2026-10-28T23:00:00+01:00",
                   default_times=["09:00", "17:00"])
    ui.overrides[("GET", f"/api/tasks/{task_id}/approval-options")] = (200, options)
    page.goto(f"{ui.fx.base_url}/review/{task_id}", wait_until="networkidle")
    page.wait_for_timeout(500)

    field = page.get_by_role("textbox", name="发布时间（柏林当地时间）", exact=True)
    out["approval_seeded_value"] = field.input_value()
    assert out["approval_seeded_value"], "第一次进来没有填默认排期时刻"

    field.fill("")
    page.wait_for_timeout(200)
    # 重新核对排期条件 = 重读这一篇 + 重取 approval-options，两个都是 GET。
    page.get_by_role("button", name="重新核对排期条件", exact=True).click()
    page.wait_for_timeout(800)
    out["approval_value_after_refetch"] = field.input_value()
    assert out["approval_value_after_refetch"] == "", \
        f'她清空之后又被填回去了：{out["approval_value_after_refetch"]!r}'

    # 清空之后主动作应当灰着，而「为什么」要能用键盘走到。
    note = page.get_by_role("note", name=re.compile("^通过并创建排期。当前不能操作："))
    out["approval_disabled_reason_nodes"] = note.count()
    assert note.count() == 1, "灰着的主动作旁边没有可聚焦的原因节点"
    out["approval_disabled_reason_text"] = note.first.get_attribute("aria-label")
    assert "请先填写柏林发布时间" in (out["approval_disabled_reason_text"] or ""), \
        f'原因不对：{out["approval_disabled_reason_text"]!r}'

    page.evaluate("() => document.activeElement && document.activeElement.blur()")
    reached = 0
    for press in range(1, 161):
        page.keyboard.press("Tab")
        if page.evaluate(
                """() => document.activeElement?.getAttribute('aria-label')
                         ?.startsWith('通过并创建排期。当前不能操作：') === true"""):
            reached = press
            break
    out["approval_reason_tab_presses"] = reached
    assert reached, "纯键盘 Tab 到不了「为什么不能点」"


def probe_keyboard_focus(page, ui, out):
    """纯键盘：模态框接得住焦点，Esc 关得掉，关掉以后焦点回到原来那个按钮。"""
    page.goto(f"{ui.fx.base_url}/review/{ui.fx.fb_id}", wait_until="networkidle")
    page.wait_for_timeout(400)

    trigger = page.get_by_role("button", name="编辑分类", exact=True)
    trigger.focus()
    out["focus_before_modal"] = page.evaluate("() => document.activeElement?.textContent?.trim()")
    page.keyboard.press("Enter")
    page.wait_for_timeout(400)
    dialog = page.get_by_role("dialog")
    out["modal_opened"] = dialog.count()
    assert dialog.count() == 1, "回车打不开编辑分类"

    out["focus_moved_into_modal"] = page.evaluate(
        "() => !!document.activeElement?.closest('.ant-modal')")
    assert out["focus_moved_into_modal"], "模态框打开后焦点还留在外面"

    page.keyboard.press("Shift+Tab")
    page.wait_for_timeout(150)
    out["shift_tab_stays_in_modal"] = page.evaluate(
        "() => !!document.activeElement?.closest('.ant-modal')")

    page.keyboard.press("Escape")
    page.wait_for_timeout(400)
    out["modal_closed_by_escape"] = page.get_by_role("dialog").count() == 0
    assert out["modal_closed_by_escape"], "Esc 关不掉模态框"
    out["focus_after_close"] = page.evaluate("() => document.activeElement?.textContent?.trim()")
    assert out["focus_after_close"] == out["focus_before_modal"], \
        f'关掉之后焦点没回到原处：{out["focus_after_close"]!r}'

    # 付费动作灰着时，原因同样要能用键盘读到（和主动作走同一个 DisabledReason）。
    notes = page.get_by_role("note", name=re.compile("当前不能操作："))
    out["disabled_reason_notes_on_detail"] = notes.count()
    out["disabled_reason_labels_on_detail"] = [
        notes.nth(index).get_attribute("aria-label") for index in range(min(notes.count(), 4))]


def probe_external_writes(ui, out):
    writes = [row for row in ui.requests if row["method"] != "GET"]
    out["non_get_requests"] = [f'{row["method"]} {row["path"]}' for row in writes]
    out["approve_calls"] = sum("/approve" in row["path"] for row in writes)
    out["calendar_refresh_calls"] = sum("/calendar/refresh" in row["path"] for row in writes)
    out["paid_model_calls"] = sum(
        any(part in row["path"] for part in ("/refinements/task/", "/initial-translation/task/",
                                             "/hashtags/task/"))
        for row in writes)
    out["feishu_calls"] = sum("/notifications/" in row["path"] for row in writes)


PROBES = {
    "images": probe_images,
    "localization": probe_localization_state,
    "density": probe_density,
    "marks": probe_marks_keyboard,
    "calendar": probe_calendar_semantics,
    "approval": probe_approval_time,
    "keyboard": probe_keyboard_focus,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*", choices=sorted(PROBES), default=sorted(PROBES))
    parser.add_argument("--out", default="state/review-probe.json")
    args = parser.parse_args()

    out: dict = {}
    with BrowserFixture() as fx:
        ui = UIFixture(fx)
        with sync_playwright() as play:
            browser = play.chromium.launch()
            page = browser.new_page(viewport=SMALL)
            ui.attach(page)
            all_requests = []
            for name in args.only:
                PROBES[name](page, ui, out)
                all_requests.extend(ui.requests)
            ui.requests = all_requests
            probe_external_writes(ui, out)
            out["page_errors"] = ui.errors
            browser.close()

    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
