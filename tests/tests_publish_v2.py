"""probe v2、G6 单击提交/G6c 回读与五态 journal 的离线验收。"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.console import force_utf8
from publish import business_suite as bs
from publish import evidence, journal, selectors
from publish.selectors import EvidenceSignal, Locator, SURFACE_COMPOSER
from tools.publish_post import _pending_blocks_force
from publish import workflow
from core.config import cfg
from publish_fixtures import verified_probe_config

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
# 详见 `docs/HANDOFF.md` 第 5 节。
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


# ==========================================================================
# [1] 已删除（2026-09-03）：probe v2 白名单是 tools/probe_publish.py 的内部
# 契约，且与 tests_publish.py 已删的 [6]-[9] 重叠。探针已移入
# tools/_scaffolding/，不参与生产链路。
#
# 生产侧对 v2 dump 的要求由 publish/evidence.py::validate_v2_dump 表达，
# 它的测试在 tests_publish.py [4]，保留。
# ==========================================================================

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

print("\n[S0-2] 长文案回读失败要留下可区分、有界的诊断")
long_caption = "Dies ist eine lange deutsche Produktbeschreibung. " * 24
truncated = long_caption[:250] + "…"
readback = asyncio.run(bs.verify_scheduled(
    PlannerPage(entries_for(truncated)), when, long_caption,
    ui_timezone="America/Los_Angeles", card_spec=card_spec))
diagnostics = getattr(readback, "diagnostics", {})
truncated_readback = readback
samples = diagnostics.get("samples", [])
check(not readback.found and diagnostics.get("caption_mismatch") == 2
      and "正文不匹配" in readback.error,
      "同一目标时刻的截断文案不算成功，并明确报告正文不匹配")
check(bool(samples) and samples[0].get("text_length") == len(
          truncated + " " + ENTRY_MOMENT)
      and samples[0].get("text_preview") == truncated[:200],
      "诊断保留实际回读文本长度与前200字符，供判断长文是否被截断")
readback = asyncio.run(bs.verify_scheduled(
    PlannerPage(entries_for(long_caption)), when, long_caption,
    ui_timezone="America/Los_Angeles", card_spec=card_spec))
check(readback.found, "完整长文案仍可正常核验，诊断不放宽或限制正文判据")

for moment, expected_reason, text in (
        ("unrecognized time", "datetime_parse_failed", "时刻无法解析"),
        ("September 09, 2026, 1:00 AM", "time_mismatch", "时刻不匹配")):
    # 一个无关但可解析的条目代表日历已经加载，避免等待真实20秒UI预算。
    rows = entries_for(moment=moment) + [("Other " + ENTRY_MOMENT, "")]
    readback = asyncio.run(bs.verify_scheduled(
        PlannerPage(rows), when, caption,
        ui_timezone="America/Los_Angeles", card_spec=card_spec))
    diagnostics = getattr(readback, "diagnostics", {})
    check(not readback.found and diagnostics.get(expected_reason) == 2
          and text in readback.error,
          "完整正文存在时区分%s，不再合并成找不到卡片" % text)

readback = asyncio.run(bs.verify_scheduled(
    PlannerPage([("Home", ""), ("Inbox", "")] + [
        ("Other %d %s" % (index, ENTRY_MOMENT), "") for index in range(30)]),
    when, caption, ui_timezone="America/Los_Angeles", card_spec=card_spec))
diagnostics = getattr(readback, "diagnostics", {})
check(diagnostics.get("datetime_parse_failed") == 0
      and diagnostics.get("caption_mismatch") == 30
      and len(diagnostics.get("samples", [])) == 20,
      "导航链接不会冒充时刻解析失败，大量卡片诊断最多保留20条样本")

with tempfile.TemporaryDirectory() as folder:
    attempt = journal.PublishAttempt(
        post_id="long-caption", platform="facebook",
        status=journal.STATUS_SUBMITTED_UNVERIFIED,
        scheduled_at=when.isoformat(), recorded_at=when.isoformat(),
        text_de_sha256="fixture")
    if "readback_diagnostics" in journal.PublishAttempt.__dataclass_fields__:
        attempt = journal.transition(
            attempt, journal.STATUS_SUBMITTED_UNVERIFIED,
            recorded_at=when.isoformat(), readback_diagnostics=diagnostics)
    journal.append(Path(folder), attempt)
    row = journal.load(Path(folder))[-1]
    check(row.get("readback_diagnostics") == diagnostics,
          "回读诊断进入追加式journal，后续审核可查到样本而不依赖控制台")

with tempfile.TemporaryDirectory() as folder:
    state = Path(folder) / "state"
    config = verified_probe_config(cfg(), state)
    image = state / "image.png"
    image.write_bytes(b"fixture-image")
    post = SimpleNamespace(
        post_id="long-caption-workflow", platform="facebook",
        text_de=long_caption, source_text="English source",
        original_text_de=long_caption, image_paths=(image,),
        image_sources=("media_de",), warnings=())
    page = PlannerPage([])
    session = SimpleNamespace(new_page=AsyncMock(return_value=page), stop=AsyncMock())
    account = bs.AccountContext(True, TARGET_FB, True, TARGET_IG, True, True)
    with patch.object(workflow, "cfg", return_value=config), \
            patch.object(workflow.snapshots, 'ensure', return_value=SimpleNamespace(**vars(post), snapshot_id='')), \
            patch.object(workflow.records, 'queue_approved'), \
            patch.object(workflow.records, 'project'), \
            patch.object(workflow, "attach", AsyncMock(return_value=(session, None, session))), \
            patch.object(workflow.channels, "require_independent_channel_evidence", return_value=None), \
            patch.object(workflow.capabilities, 'require', return_value=None), \
            patch.object(workflow.channels, 'select', AsyncMock(return_value={'channel': 'facebook', 'account': 'fixture'})), \
            patch.object(workflow.channels, 'verify_before_submit', AsyncMock()), \
            patch.object(workflow, "check_live_slot", AsyncMock()), \
            patch.object(workflow.month_readback, "baseline", AsyncMock(return_value=
                         bs.ScheduledBaseline(when.isoformat(), 0))), \
            patch.object(bs, "open_composer", AsyncMock(return_value=page)), \
            patch.object(bs, "upload_images", AsyncMock(return_value=())), \
            patch.object(bs, "fill_caption", AsyncMock()), \
            patch.object(bs, "set_schedule", AsyncMock(return_value="fixture-time")), \
            patch.object(bs, "submit", AsyncMock(return_value=
                         bs.SubmitResult(True, True, success_signal="fixture-success"))), \
            patch.object(workflow.month_readback, "verify", AsyncMock(return_value=truncated_readback)), \
            patch.object(workflow.media, 'verify_upload', AsyncMock(return_value={'image_count': 1})), \
            patch.object(workflow.channel_evidence, 'require', return_value={'context_ids': {'asset_id': '123', 'business_id': '456'}}):
        outcome = asyncio.run(workflow.execute(
            post, when, ui_timezone="America/Los_Angeles", timeout=0.1,
            stamp="fixture", submit_enabled=True))
    saved = journal.load(state)[-1]
    check(outcome.code == 5 and saved["status"] == journal.STATUS_SUBMITTED_UNVERIFIED
          and saved.get("readback_diagnostics", {}).get("caption_mismatch") == 2
          and saved.get("readback_diagnostics", {}).get("samples", [{}])[0].get(
              "text_preview") == truncated[:200],
          "真实状态机把失败诊断追加到journal，保持未核验态且不自动补提")

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

with_cards = asyncio.run(bs.read_remote_slot_inventory(
    PlannerPage(entries_for()), ui_timezone="America/Los_Angeles",
    business_timezone="Europe/Berlin", card_spec=card_spec, include_cards=True))
check(with_cards.channels_complete and len(with_cards.cards) == 2
      and with_cards.occupied_for_channel("facebook") == (when,)
      and with_cards.occupied_for_channel("instagram") == (when,),
      "月历可选读取真实详情渠道，保留不同渠道的两张原始卡片")
unknown_cards = asyncio.run(bs.read_remote_slot_inventory(
    PlannerPage([("%s %s" % (poison, ENTRY_MOMENT), "")]),
    ui_timezone="America/Los_Angeles", business_timezone="Europe/Berlin",
    card_spec=card_spec, include_cards=True))
check(len(unknown_cards.cards) == 1 and not unknown_cards.channels_complete
      and unknown_cards.cards[0].channels == (),
      "月历正文里提及FB/IG不能证明渠道；未知卡片仍保留但不能判为空档")
same_zone_fold = asyncio.run(bs.read_remote_slot_inventory(
    PlannerPage([("anything November 01, 2026, 1:00 AM", "")], month="November"),
    ui_timezone="America/Los_Angeles", business_timezone="America/Los_Angeles",
    card_spec=card_spec))
check(len(same_zone_fold.occupied) == 2,
      "展示时区与UI时区相同时也保留回拨小时的两个绝对占位")
try:
    asyncio.run(bs.read_remote_slot_inventory(
        PlannerPage([("anything March 08, 2026, 2:30 AM", "")], month="March"),
        ui_timezone="America/Los_Angeles", business_timezone="Europe/Berlin",
        card_spec=card_spec))
except bs.PublishStepError:
    nonexistent_card_blocked = True
else:
    nonexistent_card_blocked = False
check(nonexistent_card_blocked,
      "Planner卡片出现UI时区不存在的夏令时时刻时不可推算成真实排期")

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



print("\n[5] dump 校验结果缓存：命中要快，内容变了要失效")

# 一次 --submit 会沿 compose / workflow / business_suite 三条路径反复要同一份
# dump（实测 38 次、72 MB）。缓存按 (路径, mtime_ns, 大小) 命中。
# 这一段盯的是**失效**而不是命中：缓存住一份已经被换掉的 dump，等于让证据闸
# 对着旧事实放行。
import time as _time
from publish import evidence as _ev

with tempfile.TemporaryDirectory() as folder:
    dumps = Path(folder)
    name = "publish_probe_20260901_010101_000001.json"
    shots = dumps / (Path(name).stem + "_screenshots")
    shots.mkdir(parents=True)
    shot = shots / "semantic_001_final.png"
    shot.write_bytes(bytes([137, 80, 78, 71, 13, 10, 26, 10]) + b"0" * 64)

    def dump_payload(session):
        return {
            "schema_version": 2, "mode": "record-and-passive-evidence",
            "session_id": session,
            "started_at": "2026-09-01T01:01:00+00:00",
            "finished_at": "2026-09-01T01:02:00+00:00",
            "interactions": [],
            "snapshots": [{
                "sequence": 1, "page_id": "page-001", "evidence_order": 1,
                "recorded_at": "2026-09-01T01:01:30+00:00",
                "reason": "final", "semantic_items": [],
                "screenshot": str(shot), "screenshot_error": None,
            }],
        }

    target = dumps / name
    target.write_text(json.dumps(dump_payload("first")), encoding="utf-8")

    _ev.clear_dump_cache()
    first, _ = _ev.validate_v2_dump(name, dumps)
    check(first is not None and first["session_id"] == "first", "首次校验通过")

    cached, _ = _ev.validate_v2_dump(name, dumps)
    check(cached is first, "第二次直接命中缓存（返回同一个对象）")

    # 换内容 + 换 mtime：缓存必须失效
    _time.sleep(0.01)
    target.write_text(json.dumps(dump_payload("second")), encoding="utf-8")
    refreshed, _ = _ev.validate_v2_dump(name, dumps)
    check(refreshed is not None and refreshed["session_id"] == "second",
          "dump 内容变了之后重新校验，不会拿旧结论放行")

    # 把 dump 改坏：不缓存失败结论，且每次都要重新报出原因
    target.write_text("{}", encoding="utf-8")
    broken, detail = _ev.validate_v2_dump(name, dumps)
    check(broken is None and detail, f"坏 dump 每次都失败闭合，实得 {detail[:30]!r}")


print("\n" + ("全部通过" if not fails else "%d 项失败" % len(fails)))
raise SystemExit(1 if fails else 0)
