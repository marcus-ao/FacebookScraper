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


TARGET_FB = "Neakasa Deutschland"
TARGET_IG = "neakasa.de"

# Planner 夹具由正文时刻 link 和独立渠道详情组成。
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


# 此夹具的编辑器只提供 FB 账号证据。
ACCOUNT_ATTRIBUTES = {
    "facebook_account_token": TARGET_FB,
    "facebook_account_regex": r"@?(?P<account>.+)",
}


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

# 渠道与 remote ID 只在独立详情弹窗中可读。


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


from publish import month_inventory, month_readback

long_caption = "Dies ist eine lange deutsche Produktbeschreibung. " * 24
truncated = long_caption[:250] + "…"
cards = tuple(bs.RemotePlannerCard(when, ("facebook",), (("facebook", str(12345678+i)),),
    truncated, "hash", "scheduled", placement="feed", caption_status='present', time_verified=True,
    accounts=(('facebook', TARGET_FB),)) for i in range(2))
inventory = bs.RemoteSlotInventory((when,), "Europe/Berlin", when.date(), when.date(), cards, True)
with patch.object(month_inventory, "read", AsyncMock(return_value=inventory)), \
        patch.object(bs, "_readback_screenshot", AsyncMock(return_value="")):
    truncated_readback = asyncio.run(month_readback.verify(None, when, long_caption,
        ui_timezone="Europe/Berlin", target_channels=("facebook",)))
diagnostics = truncated_readback.diagnostics

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
    async def submit_with_durable_intent(*args, **kwargs):
        check(journal.load(state)[-1]['status'] == journal.STATUS_SUBMIT_AMBIGUOUS,
              '提交点击前必须已经耐久写入未决意图')
        return bs.SubmitResult(True, True, success_signal='fixture-success')
    with patch.object(workflow, "cfg", return_value=config), \
            patch.object(workflow.snapshots, 'ensure', return_value=SimpleNamespace(**vars(post), snapshot_id='')), \
            patch.object(workflow.records, 'queue_approved'), \
            patch.object(workflow.records, 'project'), \
            patch.object(workflow, "attach", AsyncMock(return_value=(session, None, session))), \
            patch.object(workflow.channels, "require_independent_channel_evidence", return_value=None), \
            patch.object(workflow.capabilities, 'require', return_value=None) as capability_check, \
            patch.object(workflow.channels, 'select', AsyncMock(return_value={'channel': 'facebook', 'account': 'fixture'})), \
            patch.object(workflow.channels, 'verify_before_submit', AsyncMock()), \
            patch.object(workflow, "check_live_slot", AsyncMock()), \
            patch.object(workflow.month_readback, "baseline", AsyncMock(return_value=
                         bs.ScheduledBaseline(when.isoformat(), 0))), \
            patch.object(bs, "open_composer", AsyncMock(return_value=page)), \
            patch.object(bs, "upload_images", AsyncMock(return_value=())), \
            patch.object(bs, "fill_caption", AsyncMock()), \
            patch.object(bs, "set_schedule", AsyncMock(return_value="fixture-time")), \
            patch.multiple(bs, verify_form=AsyncMock(), wait_submit_ready=AsyncMock()), \
            patch.object(bs, "submit", AsyncMock(side_effect=submit_with_durable_intent)), \
            patch.object(workflow.month_readback, "verify", AsyncMock(return_value=truncated_readback)), \
            patch.object(workflow.media, 'verify_upload', AsyncMock(return_value={'image_count': 1})), \
            patch.object(workflow.channel_evidence, 'require', return_value={'context_ids': {'asset_id': '123', 'business_id': '456'}}):
        outcome = asyncio.run(workflow.execute(
            post, when, ui_timezone="America/Los_Angeles", timeout=0.1,
            stamp="fixture", submit_enabled=True))
    check(capability_check.call_count == 1,
          "锁内静态能力核验执行一次，内部浏览器流程不重复执行")
    saved = journal.load(state)[-1]
    check(outcome.code == 5 and saved["status"] == journal.STATUS_SUBMITTED_UNVERIFIED
          and saved.get("readback_diagnostics", {}).get("caption_mismatch") == 2
          and saved.get("readback_diagnostics", {}).get("samples", [{}])[0].get(
              "text_preview") == truncated[:200],
          "真实状态机把失败诊断追加到journal，保持未核验态且不自动补提")

# 显示名后缀由提交前完整值比较拦截，token 边界检查不能代替。
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

# 替换 dump 后必须失效缓存，不能沿用旧证据放行。
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
