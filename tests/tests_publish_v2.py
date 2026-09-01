"""probe v2、G6 单击提交/G6c 回读与五态 journal 的离线验收。"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.console import force_utf8
from publish import business_suite as bs
from publish import evidence, journal, selectors
from publish.selectors import EvidenceSignal, Locator, SURFACE_COMPOSER
from tools.probe_publish import ProbeRecorder, _safe_payload, _safe_snapshot
from tools.publish_post import _pending_blocks_force

force_utf8()

fails = []


def check(condition, message):
    print(("  OK   " if condition else "  FAIL ") + message)
    if not condition:
        fails.append(message)


def button_spec():
    return Locator(
        key="composer_submit_button", step="G6", surface=SURFACE_COMPOSER,
        role="button", name="Schedule", name_source="visible-text",
        source_dump="fixture.json", sequences=(1,), breaks_when="fixture")


def success_spec(kind="semantic"):
    return EvidenceSignal(
        key="composer_success_signal", step="G6", kind=kind,
        surface="business.facebook.com/latest/content_calendar",
        source_dump="fixture.json", sequences=(2,),
        breaks_when="fixture", role="status" if kind == "semantic" else "",
        name="Scheduled" if kind == "semantic" else "",
        url_prefix=("https://business.facebook.com/latest/content_calendar"
                    if kind == "url" else ""))


def write_v2(root: Path, *, interactions: list[dict], snapshots: list[dict]) -> Path:
    shots = root / "fixture_screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    final_shot = shots / "final.png"
    final_shot.write_bytes(b"masked-png")
    for sequence, row in enumerate(interactions, 1):
        row.setdefault("sequence", sequence)
        row.setdefault("page_id", "page-001")
        row.setdefault("recorded_at", "2026-09-01T12:00:00+00:00")
    for sequence, row in enumerate(snapshots, 1):
        row.setdefault("sequence", sequence)
        row.setdefault("page_id", "page-001")
        row.setdefault("recorded_at", "2026-09-01T12:00:01+00:00")
    finals = [row for row in snapshots if row.get("reason") == "final"]
    if not finals:
        snapshots.append({
            "sequence": len(snapshots) + 1,
            "page_id": "page-001",
            "evidence_order": len(interactions) + len(snapshots) + 1,
            "recorded_at": "2026-09-01T12:00:03+00:00",
            "reason": "final",
            "page_url": "https://business.facebook.com/latest/content_calendar",
            "semantic_items": [],
        })
        finals = [snapshots[-1]]
    for row in finals:
        row["screenshot"] = str(final_shot)
        row["screenshot_error"] = None
    dump = {
        "schema_version": 2,
        "mode": "record-and-passive-evidence",
        "session_id": "fixture-session",
        "started_at": "2026-09-01T11:59:00+00:00",
        "finished_at": "2026-09-01T12:01:00+00:00",
        "interactions": interactions,
        "snapshots": snapshots,
    }
    path = root / "fixture.json"
    path.write_text(json.dumps(dump), encoding="utf-8")
    return path


TARGET_FB = "Neakasa Deutschland"
TARGET_IG = "neakasa.de"

# ⚠️ **2026-09-01 起，这一整段夹具照抄真实 Business Suite。**
# 上一版是照着"想象中的富卡片"写的，被
# `state/publish_probe_20260901_054226_378622.json` 逐条推翻：
# 真实 Planner 上没有"一张卡片带时刻/正文/两个渠道/图片数"这种东西，
# 有的是一条同时带正文与时刻的 link，加上**每个渠道各一个**详情弹窗。
# 详见 `docs/PROBE_FINDINGS_20260901.md`。
DATETIME_REGEX = (r"(?P<date>[A-Z][a-z]{2,8} \d{1,2}, \d{4})"
                  r"\D{0,10}?(?P<time>\d{1,2}:\d{2} [AaPp][Mm])")
PLANNER_ATTRIBUTES = {
    "date_format": "%B %d, %Y", "time_format": "%I:%M %p",
    "datetime_regex": DATETIME_REGEX,
    "entry_role": "link", "entry_probe_text": "Probe caption",
    "dialog_role": "dialog", "dialog_name": "Post details",
    "remote_id_regex": r"ID:\s*(?P<remote_id>\d{6,})",
    "facebook_marker": "Facebook's Feed", "facebook_account_token": TARGET_FB,
    "instagram_marker": "Instagram feed", "instagram_account_token": TARGET_IG,
    "visible_month_role": "heading", "visible_month_format": "%B",
    "visible_year_role": "heading", "visible_year_format": "%Y",
}
ENTRY_MOMENT = "September 08, 2026, 1:00 AM"


def dialog_text(channel, *, remote_id, account, caption) -> str:
    """详情弹窗的可访问名 —— 原样照抄真实形状（账号与正文只在这一整串里）。"""
    surface = ("Facebook's Feed" if channel == "facebook"
               else "your Instagram feed")
    return ("Post details ID: %s Close ​ Post overview This view of your "
            "post may not represent exactly how it appears on %s. Actions "
            "​ %s %s Boost Publish now"
            % (remote_id, surface, account, caption))


def planner_semantics(caption="Probe caption", *, instagram=True,
                      moment=ENTRY_MOMENT) -> list[dict]:
    rows = [
        {"tag": "div", "role": "heading", "accessible_name": "September",
         "visible_text": "September"},
        {"tag": "span", "role": "heading", "accessible_name": "2026",
         "visible_text": "2026"},
        {"tag": "div", "role": "link",
         "accessible_name": "%s %s" % (caption, moment),
         "visible_text": "%s %s" % (caption, moment)},
        {"tag": "div", "role": "dialog",
         "accessible_name": dialog_text(
             "facebook", remote_id="1887083152480681", account=TARGET_FB,
             caption=caption),
         "visible_text": ""},
    ]
    if instagram:
        rows.append({"tag": "div", "role": "dialog",
                     "accessible_name": dialog_text(
                         "instagram", remote_id="4378984725697354",
                         account=TARGET_IG, caption=caption),
                     "visible_text": ""})
    return rows


#: composer 上**只能**证明 FB —— 实测那里没有 IG 帐号名。
ACCOUNT_ATTRIBUTES = {
    "facebook_account_token": TARGET_FB,
    "facebook_account_regex": r"@?(?P<account>.+)",
}


def account_semantics(*, facebook=TARGET_FB, instagram=TARGET_IG) -> list[dict]:
    """composer 的 Facebook 预览：`article` 抬头那条 `heading h2`。

    ``instagram`` 参数保留只为兼容调用方 —— 真实 composer 上没有 IG 帐号名，
    传什么都不会出现在这里。
    """
    preview = "%s Just now · Hello Like Comment Share" % facebook
    return [
        {"tag": "div", "role": "article", "accessible_name": preview,
         "visible_text": preview},
        {"container_role": "article", "container_accessible_name": preview,
         "tag": "h2", "role": "heading", "accessible_name": facebook,
         "visible_text": facebook},
    ]


print("[1] probe v2 白名单与被动成功证据")
raw = {
    "page_url": "https://business.facebook.com/latest/content_calendar?token=secret",
    "document_title": "Planner",
    "cookie": "do-not-keep",
    "semantic_items": [
        {"tag": "div", "role": "status", "accessible_name": "Post scheduled",
         "visible_text": "Post scheduled", "aria_live": "polite",
         "class": "hashed", "css_path": "div:nth-child(7)", "value": "secret"},
        {"tag": "input", "role": "textbox", "accessible_name": "Email",
         "visible_text": "private@example.com", "value": "private@example.com"},
    ],
}
safe = _safe_snapshot(raw)
serialized = json.dumps(safe, ensure_ascii=False)
check(safe["page_url"].endswith("/latest/content_calendar")
      and "?" not in safe["page_url"], "被动 URL 去掉 query/fragment")
check("Post scheduled" in serialized and "private@example.com" not in serialized,
      "保留 alert/status 语义，但不被动采样 textbox/combobox 内容")
check(all(word not in serialized for word in ("cookie", "class", "css_path", "value")),
      "v2 不保存 cookie/class/CSS path/value")
editable = _safe_payload({
    "session_id": "s", "event_type": "input", "is_trusted": True,
    "page_url": "https://business.facebook.com/latest/composer/",
    "target": {"tag": "div", "role": "textbox",
               "is_contenteditable": True, "aria_label": "Write a post",
               "visible_text": "PRIVATE DRAFT", "accessible_name": "PRIVATE DRAFT"},
    "candidates": [{"tag": "div", "role": "textbox",
                    "is_contenteditable": True, "aria_label": "Write a post",
                    "visible_text": "PRIVATE DRAFT",
                    "accessible_name": "PRIVATE DRAFT"}],
}, "s")
check(editable is not None and "PRIVATE DRAFT" not in json.dumps(editable)
      and editable["target"]["accessible_name"] == "Write a post",
      "可信 contenteditable input 也只留结构名，不把正文当可见文本保存")
sibling = _safe_payload({
    "session_id": "s", "event_type": "click", "is_trusted": True,
    "page_url": "https://business.facebook.com/latest/composer/",
    "target": {"tag": "button", "role": "button",
               "visible_text": "Schedule", "accessible_name": "Schedule"},
    "candidates": [
        {"tag": "button", "role": "button", "ancestor_depth": 0,
         "visible_text": "Schedule", "accessible_name": "Schedule"},
        {"tag": "div", "role": "dialog", "ancestor_depth": 1,
         "contains_editable_descendant": True,
         "aria_label": "PRIVATE DRAFT", "visible_text": "PRIVATE DRAFT Schedule",
         "accessible_name": "PRIVATE DRAFT Schedule"},
    ],
}, "s")
check(sibling is not None
      and sibling["target"]["accessible_name"] == "Schedule"
      and "PRIVATE DRAFT" not in json.dumps(sibling),
      "点击按钮时含 editable sibling 的 composer 祖先也不会泄漏正文")
credential = _safe_payload({
    "session_id": "s", "event_type": "input", "is_trusted": True,
    "target": {"tag": "input", "role": "textbox", "input_type": "text",
               "autocomplete": "username", "aria_label": "private@example.com",
               "accessible_name": "private@example.com"},
}, "s")
check(credential is None,
      "email/username/password/OTP 凭据控件即使直接注入 payload 也整项丢弃")
from tools import probe_publish as probe_module
check("createTreeWalker" in probe_module.SEMANTIC_SNAPSHOT_EXPRESSION
      and "isVisible(parent)" in probe_module.SEMANTIC_SNAPSHOT_EXPRESSION
      and "safeText(container" in probe_module.SEMANTIC_SNAPSHOT_EXPRESSION
      and '[role="group"]' in probe_module.SEMANTIC_SNAPSHOT_EXPRESSION
      and "cardContainer || accountContainer" in
          probe_module.SEMANTIC_SNAPSHOT_EXPRESSION
      and "contenteditable" in probe_module._SENSITIVE_INPUT_SELECTOR,
      "被动祖先/卡片文本先剔除 editable 后代，截图也遮正文与有值控件")
check("textWithoutEditableDescendants" in probe_module.INSTALL_FUNCTION
      and "document.createTreeWalker" in probe_module.INSTALL_FUNCTION
      and "isVisible(parent)" in probe_module.INSTALL_FUNCTION
      and "const containsEditableDescendant =" in probe_module.INSTALL_FUNCTION
      and "contains_editable_descendant" in probe_module.INSTALL_FUNCTION,
      "交互候选链逐节点剔除 editable 后代，并把脱敏事实带到 Python 边界")


async def capture_button_with_editable_sibling() -> dict:
    """在真实 DOM 中证明 button 的 dialog 祖先不会夹带同级正文。"""
    captured: list[str] = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(
            '<div role="dialog" aria-label="Create post">'
            '<div contenteditable="true" role="textbox">PRIVATE SIBLING DRAFT</div>'
            '<section aria-hidden="true">HIDDEN BOOST SIBLING</section>'
            '<button type="button">Schedule</button>'
            '</div>')

        def receive(_source, payload):
            captured.append(payload)

        await page.expose_binding("__captureProbePrivacy", receive)
        await page.evaluate(probe_module.INSTALL_FUNCTION, {
            "sessionId": "dom-session",
            "bindingName": "__captureProbePrivacy",
        })
        await page.get_by_role("button", name="Schedule").click()
        await page.wait_for_timeout(50)
        await browser.close()
    if not captured:
        raise AssertionError("可信 click 没有触发 probe binding")
    return json.loads(captured[-1])


dom_click = asyncio.run(capture_button_with_editable_sibling())
check(dom_click["target"]["accessible_name"] == "Schedule"
      and any(row.get("contains_editable_descendant") is True
              for row in dom_click["candidates"])
      and "PRIVATE SIBLING DRAFT" not in json.dumps(dom_click)
      and "HIDDEN BOOST SIBLING" not in json.dumps(dom_click),
      "真实 DOM：Submit 点击祖先会移除 editable 与隐藏 sibling 文本")


async def capture_visible_semantic_transitions(folder: Path):
    """真实 Chromium：隐藏 Boost 不算证据，显示/取消分别触发截图。"""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        async def route_page(route):
            await route.fulfill(content_type="text/html", body=(
                '<main role="main">'
                '<div id="planner" role="status">Planner loaded</div>'
                '<section id="boost-shell" aria-hidden="true">'
                '<div id="boost" role="dialog">Boost your scheduled post</div>'
                '</section>'
                '<section style="opacity:0">'
                '<button>HIDDEN_BY_PARENT_OPACITY</button>'
                '</section>'
                '</main>'))

        await page.route("https://business.example.invalid/**", route_page)
        await page.goto("https://business.example.invalid/latest/content_calendar")
        session = await context.new_cdp_session(page)
        recorder = ProbeRecorder(folder, port=1, profile=folder / "profile")
        first = await recorder.snapshot(session, reason="periodic", page_id="page-boost")
        await page.evaluate(
            "document.getElementById('boost-shell').removeAttribute('aria-hidden')")
        shown = await recorder.snapshot(session, reason="periodic", page_id="page-boost")
        await page.evaluate("""() => {
          document.getElementById('boost-shell').setAttribute('aria-hidden', 'true');
          document.getElementById('planner').textContent = 'Planner card scheduled';
        }""")
        cancelled = await recorder.snapshot(
            session, reason="periodic", page_id="page-boost")
        duplicate = await recorder.snapshot(
            session, reason="periodic", page_id="page-boost")
        rows = list(recorder.data["snapshots"])
        await browser.close()
    return first, shown, cancelled, duplicate, rows


with tempfile.TemporaryDirectory() as folder:
    first, shown, cancelled, duplicate, transition_rows = asyncio.run(
        capture_visible_semantic_transitions(Path(folder)))
    transition_text = [
        " ".join(item.get("accessible_name", "")
                 for item in row.get("semantic_items", []))
        for row in transition_rows]
    check(first and shown and cancelled and not duplicate
          and "Boost your scheduled post" not in transition_text[0]
          and "Boost your scheduled post" in transition_text[1]
          and "Boost your scheduled post" not in transition_text[2]
          and "HIDDEN_BY_PARENT_OPACITY" not in " ".join(transition_text)
          and all(Path(row["screenshot"]).is_file() for row in transition_rows),
          "真实 DOM：隐藏祖先不向 main 泄漏，Boost 显示与取消后的 Planner 各自留图")


class SnapshotSession:
    async def send(self, method, params=None):
        expression = (params or {}).get("expression", "")
        if method == "Runtime.evaluate" and "semantic_items" in expression:
            return {"result": {"value": raw}}
        if method == "Runtime.evaluate" and "filter:blur" in expression:
            return {"result": {"value": True}}
        if method == "Page.captureScreenshot":
            return {"data": (
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4"
                "nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=")}
        return {"result": {"value": 0}}


with tempfile.TemporaryDirectory() as folder:
    recorder = ProbeRecorder(Path(folder), port=9223, profile=Path(folder) / "profile")
    check(recorder.data["schema_version"] == 2
          and recorder.data["mode"] == "record-and-passive-evidence",
          "新 probe 默认生成 v2 契约")
    check(asyncio.run(recorder.snapshot(SnapshotSession(), reason="periodic")),
          "v2 能被动落 URL/状态/页面语义")
    check(not asyncio.run(recorder.snapshot(SnapshotSession(), reason="periodic")),
          "页面语义未变化时不制造重复快照")

with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    write_v2(root, interactions=[], snapshots=[
        {"evidence_order": 1,
         "page_url": "https://business.facebook.com/latest/composer/",
         "semantic_items": []},
        {"evidence_order": 2,
         "page_url": "https://business.facebook.com/latest/content_calendar",
         "semantic_items": [{"tag": "div", "role": "status",
                              "accessible_name": "Scheduled", "visible_text": "Scheduled"}]},
    ])
    check(evidence.verify_signal(success_spec(), root)[0] is True,
          "成功信号只能从 v2 semantic snapshot 回查")
    check(evidence.verify_signal(success_spec("url"), root)[0] is True,
          "提交后的 URL 跳转同样可作为 v2 被动证据")

with tempfile.TemporaryDirectory() as folder:
    import core.config as config_module

    root = Path(folder)
    card = EvidenceSignal(
        key="planner_scheduled_card", step="G6c", kind="semantic",
        surface="business.facebook.com/latest/content_calendar",
        source_dump="fixture.json", sequences=(4,),
        breaks_when="fixture", role="link", name="Probe caption",
        attributes=dict(PLANNER_ATTRIBUTES))
    account_signal = EvidenceSignal(
        key="composer_account_context", step="G2", kind="semantic",
        surface=SURFACE_COMPOSER, source_dump="fixture.json", sequences=(1,),
        breaks_when="fixture", role="heading", name=TARGET_FB,
        attributes=dict(ACCOUNT_ATTRIBUTES))
    loaded_signal = EvidenceSignal(
        key="planner_loaded_signal", step="G6c", kind="semantic",
        surface="business.facebook.com/latest/content_calendar",
        source_dump="fixture.json", sequences=(4,), breaks_when="fixture",
        role="heading", name="September")
    dump_path = write_v2(root, interactions=[{
        "evidence_order": 2, "event_type": "click", "is_trusted": True,
        "page_url": "https://business.facebook.com/latest/composer/",
        "target": {"tag": "button", "role": "button",
                   "accessible_name": "Schedule", "visible_text": "Schedule"},
        "candidates": [],
    }], snapshots=[
        {"evidence_order": 1,
         "page_url": "https://business.facebook.com/latest/composer/",
         "semantic_items": account_semantics()},
        {"evidence_order": 3,
         "page_url": "https://business.facebook.com/latest/content_calendar",
         "semantic_items": [{"tag": "div", "role": "status",
                              "accessible_name": "Scheduled", "visible_text": "Scheduled"}]},
        {"evidence_order": 4,
         "page_url": "https://business.facebook.com/latest/content_calendar",
         "semantic_items": planner_semantics()},
        {"evidence_order": 5, "reason": "final",
         "page_url": "https://business.facebook.com/latest/content_calendar",
         "semantic_items": planner_semantics()},
    ])
    original_cfg = config_module.cfg
    original_composer = dict(selectors.COMPOSER)
    original_signals = dict(selectors.SIGNALS)
    try:
        class EvidenceCfg:
            state_dir = root

            def get(self, section, key, default=None):
                return {
                    "ui_probe_dump": "fixture.json",
                    "facebook_page_name": TARGET_FB,
                    "instagram_account": TARGET_IG,
                }.get(key, default)

        config_module.cfg = lambda: EvidenceCfg()
        selectors.COMPOSER["composer_submit_button"] = button_spec()
        selectors.SIGNALS["composer_account_context"] = account_signal
        selectors.SIGNALS["composer_success_signal"] = success_spec()
        selectors.SIGNALS["planner_loaded_signal"] = loaded_signal
        selectors.SIGNALS["planner_scheduled_card"] = card
        verified_runtime = bool(bs.require_submission_evidence()
                                and bs.require_readback_evidence())
        original_dump = json.loads(dump_path.read_text(encoding="utf-8"))
        partial = dict(original_dump)
        partial["finished_at"] = None
        dump_path.write_text(json.dumps(partial), encoding="utf-8")
        try:
            bs.require_submission_evidence()
        except bs.ProbeRequired:
            partial_blocked = True
        else:
            partial_blocked = False
        swapped = json.loads(json.dumps(original_dump))
        swapped["interactions"][0]["evidence_order"] = 1
        swapped["snapshots"][0]["evidence_order"] = 2
        dump_path.write_text(json.dumps(swapped), encoding="utf-8")
        try:
            bs.require_readback_evidence()
        except bs.ProbeRequired:
            causality_blocked = True
        else:
            causality_blocked = False
        wrong_account = json.loads(json.dumps(original_dump))
        wrong_account["snapshots"][0]["semantic_items"] = account_semantics(
            facebook="Wrong Page", instagram="wrong.ig")
        dump_path.write_text(json.dumps(wrong_account), encoding="utf-8")
        try:
            bs.require_submission_evidence()
        except bs.ProbeRequired:
            wrong_account_blocked = True
        else:
            wrong_account_blocked = False
        near_collision = json.loads(json.dumps(original_dump))
        near_collision["snapshots"][0]["semantic_items"] = account_semantics(
            facebook=TARGET_FB + " Test", instagram=TARGET_IG + "als")
        dump_path.write_text(json.dumps(near_collision), encoding="utf-8")
        try:
            bs.require_submission_evidence()
        except bs.ProbeRequired:
            near_collision_blocked = True
        else:
            near_collision_blocked = False
        dump_path.write_text(json.dumps(original_dump), encoding="utf-8")
        dump_path.unlink()
        try:
            bs.require_submission_evidence()
        except bs.ProbeRequired:
            missing_runtime_blocked = True
        else:
            missing_runtime_blocked = False
    finally:
        config_module.cfg = original_cfg
        selectors.COMPOSER.clear()
        selectors.COMPOSER.update(original_composer)
        selectors.SIGNALS.clear()
        selectors.SIGNALS.update(original_signals)
    check(verified_runtime,
          "生产 preflight 会现场回查提交/成功/日历三类 v2 证据")
    check(partial_blocked, "finished_at/final 不完整的 v2 dump 不能解锁生产提交")
    check(causality_blocked,
          "同名证据存在但顺序不是 account→submit→success→Planner 时仍失败闭合")
    check(wrong_account_blocked,
          "registry 手填正确 token 不能替代 dump 里真实出现正确 FB Page 与 IG 账号")
    check(near_collision_blocked,
          "IG .de/.deals 与 FB 同名前缀测试页不能靠子串冒充精确目标账号")
    check(missing_runtime_blocked,
          "登记表仍在但本机 dump 消失时，生产提交重新失败闭合")


print("\n[2] submit() 只点击一次，成功/超时/跳转都返回结构化结论")


class Element:
    def __init__(self, page, *, click_error=None, wait_error=None, on_click=None):
        self.page = page
        self.click_error = click_error
        self.wait_error = wait_error
        self.on_click = on_click
        self.clicks = 0

    @property
    def first(self):
        return self

    async def wait_for(self, **kwargs):
        if self.wait_error:
            raise self.wait_error

    async def click(self, **kwargs):
        self.clicks += 1
        if self.on_click:
            self.on_click()
        if self.click_error:
            raise self.click_error


class SubmitPage:
    def __init__(self, *, signal_error=None, redirect=False, click_error=None):
        self.url = "https://business.facebook.com/latest/composer/"
        self.signal = Element(self, wait_error=signal_error)
        self.button = Element(
            self, click_error=click_error,
            on_click=(lambda: setattr(
                self, "url", "https://business.facebook.com/latest/content_calendar"))
            if redirect else None)

    async def evaluate(self, expression):
        return 2

    def get_by_role(self, role, name=None, exact=True):
        return self.button if role == "button" else self.signal

    async def wait_for_url(self, predicate, timeout=None):
        if not predicate(self.url):
            raise TimeoutError("no redirect")


async def do_submit(page, signal):
    return await bs.submit(
        page, timeout=.01, button_spec=button_spec(), success_spec=signal)


page = SubmitPage()
result = asyncio.run(do_submit(page, success_spec()))
check(result.confirmed and page.button.clicks == 1,
      "semantic 成功信号出现时确认成功，提交按钮恰好点击一次")
page = SubmitPage(signal_error=TimeoutError("late"))
result = asyncio.run(do_submit(page, success_spec()))
check(result.ambiguous and page.button.clicks == 1 and "绝不自动重试" in result.error,
      "成功信号超时进入 submit_ambiguous，绝不第二次点击")
page = SubmitPage(redirect=True)
result = asyncio.run(do_submit(page, success_spec("url")))
check(result.confirmed and result.url_after.endswith("content_calendar")
      and page.button.clicks == 1,
      "已录证 URL 跳转可确认提交，仍只点击一次")
page = SubmitPage(click_error=RuntimeError("transport lost"))
result = asyncio.run(do_submit(page, success_spec()))
check(result.ambiguous and page.button.clicks == 1,
      "click 返回异常也视为可能已经提交，不重试")


class StaleSignal(Element):
    async def is_visible(self):
        return True


page = SubmitPage()
page.signal = StaleSignal(page)
try:
    asyncio.run(do_submit(page, success_spec()))
except bs.PublishStepError:
    stale_blocked = True
else:
    stale_blocked = False
check(stale_blocked and page.button.clicks == 0,
      "提交前已经可见的陈旧成功信号会在点击前失败闭合，不能冒充本次成功")


print("\n[3] G6c 只有目标时刻、完整正文、两个渠道的独立弹窗齐了才 scheduled")

# ⚠️ 这一节的假页面**照抄真实 Planner**（2026-09-01 dump 推翻了上一版）：
# 日历上只有一条同时带正文与时刻的 link；渠道与 remote id 要**点开**
# 各自的详情弹窗才读得到，FB 与 IG 是两个独立对象。


class Node:
    def __init__(self, role, text):
        self.role = role
        self.text = text

    @property
    def first(self):
        return self

    async def wait_for(self, **_kwargs):
        return None

    async def inner_text(self):
        return self.text

    async def get_attribute(self, _name):
        return ""


class MissingNode(Node):
    def __init__(self):
        super().__init__("dialog", "")

    async def wait_for(self, **_kwargs):
        raise TimeoutError("no dialog")


class RoleSet:
    def __init__(self, nodes):
        self.nodes = nodes

    @property
    def first(self):
        return self.nodes[0] if self.nodes else MissingNode()

    async def all(self):
        return self.nodes

    async def count(self):
        return len(self.nodes)


class Entry(Node):
    """一条日历条目。点它会打开它自己那个渠道的详情弹窗。"""

    def __init__(self, page, text, dialog=""):
        super().__init__("link", text)
        self.page = page
        self.dialog = dialog

    async def click(self, **_kwargs):
        self.page.open_dialog = self.dialog


class Keyboard:
    def __init__(self, page):
        self.page = page
        self.pressed = []

    async def press(self, key):
        self.pressed.append(key)
        if key == "Escape":
            self.page.open_dialog = ""


class PlannerPage:
    def __init__(self, entries, *, month="September", year="2026"):
        self.url = "https://business.facebook.com/latest/composer/"
        self.entries = [Entry(self, text, dialog) for text, dialog in entries]
        self.headings = [Node("heading", month), Node("heading", year)]
        self.open_dialog = ""
        self.keyboard = Keyboard(self)

    async def goto(self, url, **_kwargs):
        self.url = url

    async def evaluate(self, _expression):
        return 2

    def get_by_role(self, role, name=None, exact=True):
        if role == "heading":
            return RoleSet(self.headings)
        if role == "dialog":
            if self.open_dialog and (not name or name in self.open_dialog):
                return RoleSet([Node("dialog", self.open_dialog)])
            return RoleSet([])
        return RoleSet(self.entries)

    def locator(self, _selector):
        return RoleSet([])

    async def screenshot(self, *, path, **_kwargs):
        Path(path).write_bytes(b"masked")


card_spec = EvidenceSignal(
    key="planner_scheduled_card", step="G6c", kind="semantic",
    surface="business.facebook.com/latest/content_calendar",
    source_dump="fixture.json", sequences=(4,), breaks_when="fixture",
    role="link", name="Probe caption", attributes=dict(PLANNER_ATTRIBUTES))
when = datetime.fromisoformat("2026-09-08T10:00:00+02:00")
caption = "Hallo Berlin\nZweite Zeile"
FLAT = "Hallo Berlin Zweite Zeile"


def entries_for(text=FLAT, *, moment=ENTRY_MOMENT, facebook=True,
                instagram=True, fb_id="1887083152480681",
                ig_id="4378984725697354", fb_account=TARGET_FB,
                ig_account=TARGET_IG):
    """同一时刻两条条目 —— FB 一条、IG 一条，各自点开各自的弹窗。"""
    rows = []
    if facebook:
        rows.append(("%s %s" % (text, moment), dialog_text(
            "facebook", remote_id=fb_id, account=fb_account, caption=text)))
    if instagram:
        rows.append(("%s %s" % (text, moment), dialog_text(
            "instagram", remote_id=ig_id, account=ig_account, caption=text)))
    if not rows:
        rows.append(("%s %s" % (text, moment), ""))
    return rows


readback = asyncio.run(bs.verify_scheduled(
    PlannerPage(entries_for()), when, caption,
    ui_timezone="America/Los_Angeles", card_spec=card_spec))
check(readback.found and readback.channels == ("facebook", "instagram")
      and "1887083152480681" in readback.remote_id
      and "4378984725697354" in readback.remote_id,
      "目标/UI 时刻 + 完整正文 + FB/IG 各自的独立弹窗齐了才回读成功，"
      "并且两个渠道的 remote id 都留了下来")

baseline = asyncio.run(bs.snapshot_scheduled_matches(
    PlannerPage(entries_for()), when, caption,
    ui_timezone="America/Los_Angeles", card_spec=card_spec))
readback = asyncio.run(bs.verify_scheduled(
    PlannerPage(entries_for()), when, caption,
    ui_timezone="America/Los_Angeles", card_spec=card_spec,
    pre_submit_baseline=baseline))
check(baseline.match_count == 2 and not readback.found
      and "提交前已经存在" in readback.error,
      "提交前同槽同文案条目会被记成基线，旧卡不能冒充本次 submit 的回读")

pre = PlannerPage(entries_for())
asyncio.run(bs.snapshot_scheduled_matches(
    pre, when, caption, ui_timezone="America/Los_Angeles",
    card_spec=card_spec))
check(pre.keyboard.pressed == [],
      "提交前基线只读条目文本，**一次弹窗都不点开** —— 提交前在远端页面上"
      "点东西是没必要的风险")

try:
    asyncio.run(bs.snapshot_scheduled_matches(
        PlannerPage(entries_for("irrelevant")),
        datetime.fromisoformat("2026-12-15T10:00:00+01:00"), caption,
        ui_timezone="America/Los_Angeles", card_spec=card_spec))
except bs.PublishStepError as exc:
    baseline_outside_blocked = "不在本次 Planner 可见范围" in str(exc)
else:
    baseline_outside_blocked = False
check(baseline_outside_blocked,
      "目标日期在当前 Planner 可见月份之外时零 click 失败闭合"
      "（可见区间由 September + 2026 两条 heading 推出来）")

with tempfile.TemporaryDirectory() as folder:
    page = PlannerPage(entries_for(instagram=False))
    readback = asyncio.run(bs.verify_scheduled(
        page, when, caption, ui_timezone="America/Los_Angeles",
        card_spec=card_spec, screenshot_path=Path(folder) / "failed.png"))
    check(not readback.found and readback.missing_channels == ("instagram",)
          and readback.success_signal == ""
          and Path(readback.screenshot).is_file(),
          "只点得开 FB 弹窗时不记 scheduled；不伪造 readback signal，"
          "并保留失败截图")

readback = asyncio.run(bs.verify_scheduled(
    PlannerPage(entries_for(ig_account=TARGET_IG + "als")),
    when, caption, ui_timezone="America/Los_Angeles", card_spec=card_spec))
check(not readback.found and readback.missing_channels == ("instagram",),
      "弹窗里是 neakasa.deals 这种近碰撞账号时 IG 不算命中 —— "
      "整串文本里按**独立词**判，不是子串")
# ⚠️ 「目标名 + 后缀」（Neakasa Deutschland Test）在弹窗整串里挡不住，
# 空格是合法词边界。挡它的是**提交前**那道 composer 账号闸：
# 那里比的是独立元素的**完整值**，多一个词就不等。
account_signal = EvidenceSignal(
    key="composer_account_context", step="G2", kind="semantic",
    surface=SURFACE_COMPOSER, source_dump="fixture.json", sequences=(1,),
    breaks_when="fixture", role="heading", name=TARGET_FB,
    attributes=dict(ACCOUNT_ATTRIBUTES))


class ComposerPage:
    def __init__(self, shown):
        self.url = "https://business.facebook.com/latest/composer/"
        self.shown = shown

    async def evaluate(self, _expression):
        return 2

    async def content(self):
        return self.shown

    def get_by_text(self, value, exact=True):
        return RoleSet([Node("div", self.shown)] if value in self.shown else [])

    def get_by_role(self, role, name=None, exact=True):
        return RoleSet([Node("heading", self.shown)])

    def locator(self, _selector):
        return RoleSet([Node("div", self.shown)])


try:
    asyncio.run(bs.ensure_logged_in(
        ComposerPage(TARGET_FB + " Test"), page_name=TARGET_FB,
        account_spec=account_signal, timeout=.01))
except bs.PublishStepError as exc:
    suffix_blocked = "完整值" in str(exc)
else:
    suffix_blocked = False
check(suffix_blocked,
      "composer 上显示 'Neakasa Deutschland Test' 时提交前失败闭合 —— "
      "完整值比较，多一个词就是另一个主页")

readback = asyncio.run(bs.verify_scheduled(
    PlannerPage(entries_for(FLAT + " EXTRA CTA")), when, caption,
    ui_timezone="America/Los_Angeles", card_spec=card_spec))
check(readback.found,
      "条目文本是「完整正文 + 时刻」，正文被完整包含即命中"
      "（Planner 上正文本来就和时刻拼在一起）")
readback = asyncio.run(bs.verify_scheduled(
    PlannerPage(entries_for("Ganz andere Zeile")), when, caption,
    ui_timezone="America/Los_Angeles", card_spec=card_spec))
check(not readback.found,
      "正文对不上时不记 scheduled")

# 正文里塞满旧版整卡子串判据；渠道仍然只能由**点开的弹窗**证明。
poison = FLAT + " Facebook Instagram Neakasa Deutschland neakasa.de 5 photos"
readback = asyncio.run(bs.verify_scheduled(
    PlannerPage([("%s %s" % (poison, ENTRY_MOMENT), "")]), when, poison,
    ui_timezone="America/Los_Angeles", card_spec=card_spec))
check(not readback.found and set(readback.missing_channels) == {
          "facebook", "instagram"},
      "条目文本里写着 Facebook/Instagram/账号名也不能冒充渠道证据 —— "
      "渠道只认点开弹窗后读到的 `ID: <数字>` + 渠道标记")

no_id = dialog_text("facebook", remote_id="", account=TARGET_FB, caption=FLAT)
readback = asyncio.run(bs.verify_scheduled(
    PlannerPage([("%s %s" % (FLAT, ENTRY_MOMENT), no_id)]), when, caption,
    ui_timezone="America/Los_Angeles", card_spec=card_spec))
check(not readback.found and "facebook" in readback.missing_channels,
      "弹窗读不到 remote id 时该渠道不算命中")

ambiguous = datetime.fromisoformat("2026-11-01T10:00:00+01:00")
check(bs.ui_time_is_ambiguous(ambiguous, "America/Los_Angeles"),
      "美西回拨日德国 10:00 映射成重复的 1:00 AM，不能当成普通时刻")
occupied = asyncio.run(bs.read_remote_occupied_slots(
    PlannerPage([("anything November 01, 2026, 1:00 AM", "")],
                month="November"),
    ui_timezone="America/Los_Angeles", business_timezone="Europe/Berlin",
    card_spec=card_spec))
check(len(occupied) == 2 and {item.hour for item in occupied} == {9, 10},
      "日历条目不带 offset 时把回拨小时两种解释都视为占用，避免重复排期")

inventory = asyncio.run(bs.read_remote_slot_inventory(
    PlannerPage([("anything September 08, 2026, 1:00 AM", "")]),
    ui_timezone="America/Los_Angeles", business_timezone="Europe/Berlin",
    card_spec=card_spec))
check(inventory.covers([when])
      and not inventory.covers([
          datetime.fromisoformat("2026-12-15T10:00:00+01:00")]),
      "远端占位回读同时冻结当前 Planner 可见月份，区间外槽位整批失败闭合")

try:
    asyncio.run(bs.read_remote_occupied_slots(
        PlannerPage([("two September 08, 2026, 1:00 AM "
                      "September 09, 2026, 2:00 AM", "")]),
        ui_timezone="America/Los_Angeles",
        business_timezone="Europe/Berlin", card_spec=card_spec))
except bs.PublishStepError:
    multiple_times_blocked = True
else:
    multiple_times_blocked = False
check(multiple_times_blocked,
      "一条条目里解析出两个**不同**时刻时整批失败闭合")

nav_only = asyncio.run(bs.read_remote_occupied_slots(
    PlannerPage([("Home", ""), ("Inbox", ""), ("Planner", "")]),
    ui_timezone="America/Los_Angeles", business_timezone="Europe/Berlin",
    card_spec=card_spec))
check(nav_only == (),
      "日历上的导航 link 读不出时刻，跳过而不是报错"
      "（真实 Planner 上 link 有几十条，绝大多数是侧栏导航）")

try:
    asyncio.run(bs.read_remote_occupied_slots(
        PlannerPage([]), ui_timezone="America/Los_Angeles",
        business_timezone="Europe/Berlin", card_spec=card_spec))
except bs.PublishStepError:
    empty_blocked = True
else:
    empty_blocked = False
check(empty_blocked,
      "没有条目也没有已录证 empty state 时失败闭合，不把 React 未加载当零占用")


print("\n[4] 五态 journal、旧行兼容与禁止模糊状态自动重试")
with tempfile.TemporaryDirectory() as folder:
    state = Path(folder)
    base = journal.PublishAttempt(
        post_id="p1", platform="facebook", status=journal.STATUS_PREPARED,
        scheduled_at="2026-09-08T10:00:00+02:00",
        recorded_at="2026-09-01T12:00:00+00:00",
        text_de_sha256=journal.text_sha256("hallo"),
        source_refs=("facebook:p1", "instagram:i1"))
    rows = [base]
    for status in (journal.STATUS_SUBMIT_AMBIGUOUS,
                   journal.STATUS_SUBMITTED_UNVERIFIED,
                   journal.STATUS_SCHEDULED):
        rows.append(journal.transition(
            rows[-1], status, recorded_at="2026-09-01T12:01:00+00:00"))
    for row in rows:
        journal.append(state, row)
    check({row["status"] for row in journal.load(state)} == {
        journal.STATUS_PREPARED, journal.STATUS_SUBMIT_AMBIGUOUS,
        journal.STATUS_SUBMITTED_UNVERIFIED, journal.STATUS_SCHEDULED},
        "追加式转换保留每个中间状态，不重写历史")
    check(journal.scheduled_source_refs(state) == {
        "facebook:p1", "instagram:i1"},
        "只有最终 scheduled 才把全部 source_refs 视为已发布")

workflow_source = (ROOT / "publish" / "workflow.py").read_text(encoding="utf-8")
check(workflow_source.index("journal.append(c.state_dir, armed)")
      < workflow_source.index("result = await bs.submit(page, timeout=timeout)"),
      "任何可能点击提交前先耐久写 submit_ambiguous intent，崩溃后也不能 force 重试")
with tempfile.TemporaryDirectory() as folder:
    state = Path(folder)
    prepared = journal.PublishAttempt(
        post_id="armed", platform="facebook", status=journal.STATUS_PREPARED,
        scheduled_at="2026-09-08T10:00:00+02:00",
        recorded_at="2026-09-01T12:00:00+00:00",
        text_de_sha256="abc")
    journal.append(state, prepared)
    armed = journal.transition(
        prepared, journal.STATUS_SUBMIT_AMBIGUOUS,
        recorded_at="2026-09-01T12:00:01+00:00",
        note="submit intent armed before click")
    journal.append(state, armed)
    pending = journal.pending_record_for_refs(state, ("facebook:armed",))
    check(pending is not None
          and pending["status"] == journal.STATUS_SUBMIT_AMBIGUOUS
          and _pending_blocks_force(pending),
          "click 后进程中断只留下 armed intent 时，下一次 --force 仍被零浏览器阻断")

with tempfile.TemporaryDirectory() as folder:
    state = Path(folder)
    old = {"post_id": "old", "platform": "instagram", "status": "failed",
           "scheduled_at": "2026-09-08T10:00:00+02:00",
           "recorded_at": "2026-09-01T12:00:00+00:00",
           "text_de_sha256": "abc"}
    (state / journal.JOURNAL_NAME).write_text(json.dumps(old) + "\n", encoding="utf-8")
    loaded = journal.load(state)[0]
    check(loaded["status"] == journal.STATUS_FAILED_PRE_SUBMIT
          and loaded["source_refs"] == ["instagram:old"]
          and loaded["original_text_sha256"] == "abc",
          "旧 failed 行兼容成 failed_pre_submit，并补出 source_refs/原译文指纹")

for corrupt_payload, label in (
        (["legal-json-but-not-object"], "合法 JSON 数组行"),
        ({"post_id": "p", "platform": "facebook", "status": "scheduld",
          "scheduled_at": "2026-09-08T10:00:00+02:00",
          "recorded_at": "2026-09-01T12:00:00+00:00",
          "text_de_sha256": "abc", "source_refs": ["facebook:p"]},
         "拼错 status 的对象行")):
    with tempfile.TemporaryDirectory() as folder:
        state = Path(folder)
        (state / journal.JOURNAL_NAME).write_text(
            json.dumps(corrupt_payload) + "\n", encoding="utf-8")
        try:
            journal.load(state)
        except ValueError:
            blocked = True
        else:
            blocked = False
        check(blocked, "%s 会让 journal 整份失败闭合，不能被幂等检查静默跳过" % label)

with tempfile.TemporaryDirectory() as folder:
    state = Path(folder)
    automatic_failure = journal.transition(
        base, journal.STATUS_FAILED_PRE_SUBMIT,
        recorded_at="2026-09-01T12:01:00+00:00")
    journal.append(state, automatic_failure)
    check(journal.pending_draft_record(state, "p1", "facebook") is not None,
          "自动 failed_pre_submit 仍可能留有草稿，会阻止无确认重跑")
    manually_closed = journal.transition(
        automatic_failure, journal.STATUS_FAILED_PRE_SUBMIT,
        recorded_at="2026-09-01T12:01:30+00:00", manual_evidence=True)
    journal.append(state, manually_closed)
    check(journal.pending_draft_record(state, "p1", "facebook") is None,
          "人工确认未排期后可闭合 failed_pre_submit")

with tempfile.TemporaryDirectory() as folder:
    state = Path(folder)
    ambiguous = journal.transition(
        base, journal.STATUS_SUBMIT_AMBIGUOUS,
        recorded_at="2026-09-01T12:02:00+00:00")
    journal.append(state, ambiguous)
    check(journal.pending_draft_record(state, "p1", "facebook") is not None,
          "submit_ambiguous 会阻止自动重试")
    closed = journal.transition(
        ambiguous, journal.STATUS_FAILED_PRE_SUBMIT,
        recorded_at="2026-09-01T12:03:00+00:00", manual_evidence=True)
    journal.append(state, closed)
    check(journal.pending_draft_record(state, "p1", "facebook") is None,
          "人工确认未排期后关闭模糊状态，允许新尝试")
    check(journal.last_resolvable(state, "p1") is None,
          "人工结转后不会穿透到旧 ambiguous 再重复结转")

with tempfile.TemporaryDirectory() as folder:
    lock_path = Path(folder) / "publish.lock"
    try:
        with journal.PublishOperationLock(lock_path):
            with journal.PublishOperationLock(lock_path):
                pass
    except RuntimeError:
        publish_lock_blocked = True
    else:
        publish_lock_blocked = False
    check(publish_lock_blocked,
          "单帖 --submit 与 pipeline approve 共用发布锁，不能并发重复提交")

check(_pending_blocks_force({"status": journal.STATUS_SUBMIT_AMBIGUOUS})
      and _pending_blocks_force({"status": journal.STATUS_SUBMITTED_UNVERIFIED})
      and _pending_blocks_force({"status": journal.STATUS_FAILED_PRE_SUBMIT})
      and not _pending_blocks_force({"status": journal.STATUS_PREPARED}),
      "--force 也不能绕过模糊/未回读/自动 pre-submit 失败状态")


print("\n" + ("全部通过" if not fails else "%d 项失败" % len(fails)))
raise SystemExit(1 if fails else 0)
