"""Business Suite 操作与回读；定位须有录证，提交只点一次，失败保留现场供人工核查。"""
from __future__ import annotations

import asyncio
import time
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.config import cfg
from publish import evidence
from publish import selectors
from publish.evidence import token_present as evidence_token_present
from publish.selectors import (COMPOSER, SIGNALS, EvidenceSignal, Locator,
                               describe_gap)

# 通用等待预算由 ui_timeout_seconds 配置。
DEFAULT_UI_TIMEOUT = 30.0
# 日历条目加载预算，单位秒。
_PLANNER_ENTRY_BUDGET = 20.0
# 等待受控开关的异步状态更新，单位秒。
_SWITCH_ON_BUDGET = 8.0
# 页面可用性自检要短，因为它就是用来快速判定"这一页是不是 那种坏页"的。
PAGE_HEALTH_TIMEOUT = 8.0
# 上传后给 UI 的沉淀窗口。**不是固定 sleep**：到点就走，不阻塞成功路径。
UPLOAD_SETTLE_TIMEOUT = 15.0

# 发布截图只遮凭据，保留正文供核查；probe 截图另须遮所有输入值。
SENSITIVE_INPUT_SELECTOR = (
    'input[type="password"], input[type="email"], '
    'input[autocomplete~="username"], input[autocomplete~="current-password"], '
    'input[autocomplete~="new-password"], input[autocomplete~="one-time-code"]'
)

_ZWSP = selectors.ZERO_WIDTH_SPACE
# Time input 容器文本形如 12 : 30 AM。
_RENDERED_TIME = re.compile(r"(\d{1,2})\s*:\s*(\d{2})\s*([AP]M)", re.IGNORECASE)
_RENDERED_DATE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")


class ProbeRequired(RuntimeError):
    """这一步需要 G1 还没录到的东西；不得凭猜补上。"""


class PublishStepError(RuntimeError):
    """某一步在真实 UI 上失败了（定位没找到、回读对不上、页面不可用）。"""


@dataclass(frozen=True)
class AccountContext:
    """会话与账号核验结果；selection_verified=False 不表示目标账号已确认。"""

    logged_in: bool
    page_name: str
    page_name_seen: bool
    instagram_account: str
    instagram_seen: bool
    selection_verified: bool
    notes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class StepFailure:
    """G7 的失败留痕。**不含任何"已自动清理"的语义。**"""

    step: str
    url: str
    message: str
    screenshot: Path | None

    def lines(self) -> tuple[str, ...]:
        return (
            "发布失败：%s" % self.step,
            "当前 URL：%s" % (self.url or "(读不到)"),
            "原因：%s" % self.message,
            "截图：%s" % (self.screenshot or "(没截到)"),
            "⚠️ 程序**没有**删除任何东西。请到 Business Suite 里看一眼有没有"
            "留下半成品草稿，需要的话手工删。",
        )


@dataclass(frozen=True)
class SubmitResult:
    """一次且仅一次提交点击的结论；``confirmed=False`` 一律视为模糊。"""

    clicked: bool
    confirmed: bool
    clicked_at: str = ""
    observed_at: str = ""
    url_before: str = ""
    url_after: str = ""
    success_signal: str = ""
    remote_id: str = ""
    error: str = ""

    @property
    def ambiguous(self) -> bool:
        return self.clicked and not self.confirmed


@dataclass(frozen=True)
class ScheduledReadback:
    """Planner/内容日历回读结果；只有 ``found`` 才能写 ``scheduled``。"""

    found: bool
    observed_at: str
    target_at: str
    ui_at: str
    final_text_sha256: str
    channels: tuple[str, ...] = ()
    missing_channels: tuple[str, ...] = ()
    image_count: int | None = None
    expected_image_count: int | None = None
    remote_id: str = ""
    success_signal: str = ""
    card_sha256: str = ""
    screenshot: str = ""
    error: str = ""
    diagnostics: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ScheduledBaseline:
    """提交前同条件卡片集合；G6c 必须证明提交后出现的是新结果。"""

    observed_at: str
    match_count: int
    remote_ids: tuple[str, ...] = ()
    card_sha256: tuple[str, ...] = ()


@dataclass(frozen=True)
class RemotePlannerCard:
    """Planner 卡片的原始展示及详情弹窗证明的渠道；空渠道表示未识别。"""

    at: datetime
    channels: tuple[str, ...] = ()
    remote_ids: tuple[tuple[str, str], ...] = ()
    rendered: str = ""
    card_sha256: str = ""
    delivery: str = 'unknown'
    placement: str = 'unknown'
    media_kind: str = 'unknown'
    caption_status: str = 'unknown'
    accounts: tuple[tuple[str, str], ...] = ()
    relationships: tuple[str, ...] = ()
    read_status: str = 'complete'
    source_content_id: str = ''


@dataclass(frozen=True)
class RemoteSlotInventory:
    """一次 Planner 读取的槽位与它实际覆盖的 UI 日期范围。"""

    occupied: tuple[datetime, ...]
    ui_timezone: str
    visible_start: date | None = None
    visible_end: date | None = None
    cards: tuple[RemotePlannerCard, ...] = ()
    cards_loaded: bool = False
    diagnostics: tuple[dict, ...] = ()

    @property
    def classification_complete(self) -> bool:
        return self.cards_loaded and all(card.placement != 'unknown' for card in self.cards)

    @property
    def decision_complete(self) -> bool:
        # The existing policy counts every same-channel card, regardless of placement.
        # Unknown/unreadable entries still occupy their observed time and block decisions.
        return self.channels_complete and self.classification_complete and not self.diagnostics and all(
            card.read_status == 'complete' for card in self.cards)

    @property
    def channels_complete(self) -> bool:
        """旧式仅时刻读取不能充当已核对过渠道的空日历。"""
        return (self.cards_loaded
                and all(card.channels and set(card.channels) <= {"facebook", "instagram"}
                        for card in self.cards)
                and {card.at.timestamp() for card in self.cards}
                == {item.timestamp() for item in self.occupied})

    def occupied_for_channel(self, channel: str) -> tuple[datetime, ...]:
        if channel not in {"facebook", "instagram"}:
            raise ValueError("未知目标渠道：%s" % channel)
        if not self.decision_complete:
            raise ProbeRequired("Planner 渠道信息不完整，不能把未识别卡片当作空档")
        # UTC 时间戳去重，避免同一 ZoneInfo 的 fold 比较吞掉回拨时刻。
        selected = {card.at.timestamp(): card.at for card in self.cards
                    if channel in card.channels}
        return tuple(selected[key] for key in sorted(selected))

    def covers(self, slots: tuple[datetime, ...] | list[datetime]) -> bool:
        if any(slot.tzinfo is None or slot.utcoffset() is None for slot in slots):
            raise ValueError("Planner 覆盖范围只能判断带时区的时刻")
        if self.visible_start is None or self.visible_end is None:
            return False
        zone = resolve_ui_timezone(self.ui_timezone)
        return all(self.visible_start <= slot.astimezone(zone).date()
                   <= self.visible_end for slot in slots)


@dataclass(frozen=True)
class _PlannerMatch:
    schedule_rendered: str
    channels: tuple[str, ...]
    image_count: int | None
    remote_id: str
    card_sha256: str


# 定位

def locator_for(page, key: str):
    """按注册表获取 composer 定位；其它界面定位不可复用。"""
    spec = COMPOSER.get(key)
    if spec is None:
        known = "、".join(sorted(COMPOSER))
        raise KeyError(
            "%r 不是 composer 界面上已验证的定位（已登记：%s）" % (key, known))
    return _build(page, spec)


def _build(page, spec: Locator, root=None):
    name, exact = spec.match()
    target = page if root is None else root
    if not name:
        raise KeyError("%s 没有可访问名，不能用 get_by_role 定位" % spec.key)
    return target.get_by_role(spec.role, name=name, exact=exact)


def _ms(timeout: float) -> float:
    return float(timeout) * 1000.0


async def assert_page_usable(page, *, timeout: float = PAGE_HEALTH_TIMEOUT) -> None:
    """操作前确认 Playwright 页面可用，避免 CDP 附着后帧树未就绪。"""
    try:
        value = await asyncio.wait_for(page.evaluate("1 + 1"), timeout)
    except asyncio.TimeoutError as exc:
        raise PublishStepError(
            "这个标签页 Playwright 驱动不了（page.evaluate 超时 %.0fs）。\n"
            "  这就是 那种坏页：附着到**连接之前就已经打开**的标签页时，\n"
            "  Playwright 可能永远拿不到帧树，而底层 CDP 是好的。\n"
            "  处理：让本工具自己开新标签页（open_composer 就是这么做的），\n"
            "  或者在那一页按 F5 刷新之后重试。" % timeout) from exc
    except Exception as exc:                      # noqa: BLE001 - 要原样报出来
        raise PublishStepError(
            "页面可用性自检失败：%s" % exc) from exc
    if value != 2:
        raise PublishStepError("页面可用性自检返回了 %r，不是 2" % (value,))
    if not (page.url or "").strip():
        raise PublishStepError(
            "page.url 是空的——典型的 坏页，先刷新那一页再重试。")


# 进入编辑器

async def open_composer(context, *, asset_context: dict, timeout: float = DEFAULT_UI_TIMEOUT):
    """新建标签页，沿录证路径进入 composer，避免复用失效的 CDP 页面对象。"""
    from urllib.parse import urlencode
    # 延迟导入：资产导航只在真实 composer 组装时需要，纯文案/时区检查不依赖它。
    from publish.asset_context import context_ids
    calendar_url = selectors.CONTENT_CALENDAR_URL + '?' + urlencode(asset_context)
    if context_ids(calendar_url) != asset_context:
        raise ProbeRequired('打开编辑器前缺少唯一的已录证资产标识')
    page = await context.new_page()
    await page.goto(calendar_url,
                    wait_until="domcontentloaded", timeout=_ms(timeout))
    await assert_page_usable(page)
    if context_ids(page.url) != asset_context:
        raise ProbeRequired('月历跳转后的业务资产与录证不一致')

    create = locator_for(page, "create_post_button")
    try:
        await create.first.click(timeout=_ms(timeout))
    except Exception as exc:                      # noqa: BLE001
        raise PublishStepError(
            "内容日历上找不到 %r（%s）。\n"
            "  可能是没登录、当前不是那个 Page、或者 Meta 改了这个按钮。\n"
            "  ❌ 不得自动登录（全局红线 1）：请在发布专用 Chrome 里人工登录后重试。"
            % (COMPOSER["create_post_button"].name, exc)) from exc

    caption = locator_for(page, "caption_box")
    try:
        await caption.first.wait_for(state="visible", timeout=_ms(timeout))
    except Exception:                             # noqa: BLE001
        # 录证中的 Done 弹层是可选步骤，仅出现时处理。
        await _click_if_present(page, "composer_done_button", timeout=timeout)
        await caption.first.wait_for(state="visible", timeout=_ms(timeout))
    if context_ids(page.url) != asset_context:
        raise ProbeRequired('编辑器跳转后的业务资产与录证不一致')
    return page


async def _click_if_present(page, key: str, *,
                            timeout: float = DEFAULT_UI_TIMEOUT) -> bool:
    target = locator_for(page, key).first
    try:
        if await target.count() == 0:
            return False
        await target.click(timeout=_ms(timeout))
    except Exception:                             # noqa: BLE001 - 可选步骤
        return False
    return True


# 会话与账号

async def ensure_logged_in(page, *, page_name: str, instagram_account: str = "",
                           account_spec: EvidenceSignal | None = None,
                           timeout: float = DEFAULT_UI_TIMEOUT
                           ) -> AccountContext:
    """检查会话及已录证账号上下文；account_spec 核验在上传后执行，会话过期交人工登录。"""
    if not (page_name or "").strip():
        raise ValueError("[publish].facebook_page_name 为空，无法核对目标主页")

    await assert_page_usable(page)
    caption = locator_for(page, "caption_box").first
    try:
        await caption.wait_for(state="visible", timeout=_ms(timeout))
        logged_in = True
    except Exception as exc:                      # noqa: BLE001
        raise PublishStepError(
            "composer 的正文框没出现，判定为**未登录或会话已过期**。\n"
            "  当前 URL：%s\n"
            "  ❌ 程序不会替你登录（全局红线 1）。请在发布专用 Chrome"
            "（%s）里人工登录持有 DE 发布权的账号，然后重跑。\n"
            "  原始错误：%s"
            % (page.url, r"scripts\start_chrome_publish.bat", exc)) from exc

    page_name_seen = await _text_present(page, page_name)
    instagram_seen = (await _text_present(page, instagram_account)
                      if (instagram_account or "").strip() else False)
    if not page_name_seen:
        raise PublishStepError(
            "页面上找不到目标主页显示名 %r。\n"
            "  **这比没登录严重**：登录态在、但上下文可能是别的主页，"
            "德语内容会发到错误的地方去。\n"
            "  请在 Business Suite 里手工切到该主页后重跑；"
            "若显示名本身变了，改 [publish].facebook_page_name。" % page_name)

    selection_verified = False
    notes: list[str] = []
    if account_spec is not None:
        target = page.get_by_role(
            account_spec.role, name=account_spec.name, exact=False).first
        try:
            await target.wait_for(state="visible", timeout=_ms(timeout))
        except Exception as exc:                  # noqa: BLE001
            raise PublishStepError(
                "找不到 v2 已录证的当前发布账号上下文；为避免发错主页，"
                "自动提交在点提交之前停止：%s"
                "  ⚠️ 若这一步是在**空 composer** 上调用的，那是调用顺序错了："
                "这条定位是 FB 预览里的 heading，预览要有内容才渲染。"
                % exc) from exc
        # 此定位只证明 FB，不能代替 IG 账号回读。
        attrs = account_spec.attributes
        extracted: list[str] = []
        for value in await _node_text_values(target):
            account = selectors.account_value_from_text(
                value, attrs["facebook_account_regex"])
            if account is not None:
                extracted.append(account)
        expected = selectors.normalize_account_value(page_name)
        if (not extracted or any(
                selectors.normalize_account_value(value) != expected
                for value in extracted)):
            raise PublishStepError(
                "composer 上读到的目标主页**完整值**不等于 %r（读到 %s）；"
                "自动提交在点提交之前停止 —— 多一个词就是另一个主页"
                % (page_name, extracted or "空"))
        notes.append(
            "已核对 composer 目标主页 = %s；IG 帐号 composer 上不显示，"
            "由提交后的内容日历回读证明" % page_name)
        selection_verified = True
        instagram_seen = bool(instagram_account)
        notes.append("✓ 已从 v2 录证的只读账号上下文核对目标 FB Page 与 IG 帐号。")
    else:
        notes.append(
            "⚠️ 只能确认页面上出现了 %r，**不能确认它就是当前选中的发布目标**："
            "composer 界面上没录到主页切换器（G1 缺口 composer_account_context）。"
            % page_name)
    # 缺渠道控件证据时只提示人工核查，不能视为已验证。
    notes.append(
        "ℹ️ 渠道（FB Page / IG）沿用进入 composer 时默认全勾选；程序不点击勾选控件，"
        "提交后仍按账号与渠道卡片语义回读。")
    if (instagram_account or "").strip() and not instagram_seen:
        notes.append(
            "⚠️ 页面上没看到 IG 帐号 %r —— 可能只是没渲染到可访问文本里，"
            "也可能这一次 IG 那一路真的没勾上。**提交前重点看这一项。**"
            % instagram_account)
    return AccountContext(
        logged_in=logged_in,
        page_name=page_name,
        page_name_seen=page_name_seen,
        instagram_account=instagram_account,
        instagram_seen=instagram_seen,
        selection_verified=selection_verified,
        notes=tuple(notes),
    )


async def _text_present(page, needle: str) -> bool:
    """通过可访问文本检查页面内容。"""
    value = (needle or "").strip()
    if not value:
        return False
    try:
        return await page.get_by_text(value, exact=False).count() > 0
    except Exception:                             # noqa: BLE001
        return False


# 图片上传

async def upload_images(page, paths: list[Path], *,
                        timeout: float = DEFAULT_UI_TIMEOUT) -> tuple[str, ...]:
    """通过 file chooser 上传并核验多选能力；缩略图数量与顺序由后续 media.verify_upload 验证。"""
    files = [Path(item) for item in paths]
    if not files:
        raise ValueError("upload_images 至少要有一张图")
    for item in files:
        if not item.is_file() or item.stat().st_size <= 0:
            raise PublishStepError("待上传图片不存在或是空文件：%s" % item)

    await assert_page_usable(page)
    button = locator_for(page, "add_media_button").first
    try:
        async with page.expect_file_chooser(timeout=_ms(timeout)) as info:
            await button.click(timeout=_ms(timeout))
        chooser = await info.value
    except Exception as exc:                      # noqa: BLE001
        raise PublishStepError(
            "点了 %r 但没有等到文件选择器（%s）。\n"
            "  两种可能，处理方式完全不同：\n"
            "    a) 上传区是**拖拽区**而不是文件输入 —— 那就要重录一次 G1，"
            "把拖拽区的定位记下来；\n"
            "    b) 按钮定位失效 —— 见 selectors.py 里 add_media_button 的"
            "「什么信号说明它失效了」。\n"
            "  ⛔ 不得改成对着页面盲点或模拟拖拽（全局红线 5）。"
            % (COMPOSER["add_media_button"].name, exc)) from exc

    if len(files) > 1 and not chooser.is_multiple():
        raise PublishStepError(
            "这个上传控件只收 1 个文件，但要传 %d 张。"
            "别默默只传一张——这一篇先停下。" % len(files))
    await chooser.set_files([str(item) for item in files])

    # SPA 长连接可能始终不 idle；只等待有限窗口。
    try:
        await asyncio.wait_for(
            page.wait_for_load_state("networkidle",
                                     timeout=_ms(UPLOAD_SETTLE_TIMEOUT)),
            UPLOAD_SETTLE_TIMEOUT + 2.0)
    except Exception:                             # noqa: BLE001 - 预期会超时
        pass

    return (
        "已把 %d 张图交给上传控件（%s）。" % (
            len(files), "、".join(item.name for item in files)),
        "⚠️ 此上传入口尚未核对缩略图数量；调用方需完成 media.verify_upload 或人工核验。",
    )


# 正文

def normalize_caption(value: str) -> str:
    """仅去除 U+200B 并统一换行，保留正文其它字符用于精确回读。"""
    return (value or "").replace(_ZWSP, "").replace("\r\n", "\n").replace("\r", "\n")


async def _write_caption(page, box, text: str, *, per_line: bool,
                         timeout: float) -> str:
    """用 insert_text 写正文，避免逐键输入触发 @/# 自动补全而扰乱光标。

    ⛔ **不要改回 ``keyboard.type()``。** 那个 API 逐字符派发真实按键事件，
    正文里的 ``@`` 和 ``#`` 会唤起 typeahead 浮层并改写已输入内容 ——
    发出去的文案会和审校时看到的不一样，而这在提交前没有任何提示。
    ⛔ 也不要退回"只按 Ctrl+A 然后直接打字"：Ctrl+A 未必选中富文本编辑器的
    全部内容，残留的旧正文会拼到新文案前面。
    """
    await box.click(timeout=_ms(timeout))
    # 清空已有内容（重跑时 composer 里可能残留上一次的字）
    await page.keyboard.press("Control+A")
    await page.keyboard.press("Delete")
    if per_line:
        # 整段换行无效时，退回逐行 insert_text 与 Shift+Enter。
        for index, line in enumerate(text.split("\n")):
            if index:
                await page.keyboard.press("Shift+Enter")
            if line:
                await page.keyboard.insert_text(line)
    else:
        await page.keyboard.insert_text(text)
    return normalize_caption(await box.inner_text())


async def fill_caption(page, text: str, *,
                       timeout: float = DEFAULT_UI_TIMEOUT) -> None:
    """先整段、后逐行插入并精确回读；两种方式均不匹配时禁止提交。"""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("正文不能为空")
    await assert_page_usable(page)

    box = locator_for(page, "caption_box").first
    await box.wait_for(state="visible", timeout=_ms(timeout))

    expected = normalize_caption(text)
    attempts: list[tuple[str, str]] = []
    for label, per_line in (("整段插入", False), ("逐行插入 + Shift+Enter", True)):
        actual = await _write_caption(
            page, box, expected, per_line=per_line, timeout=timeout)
        if actual == expected:
            return
        attempts.append((label, actual))

    raise PublishStepError(
        "正文回读与要填的不一致，**没有继续**（两种写法都试过了）。\n%s"
        % "\n".join(_diff_hint(expected, actual, label)
                    for label, actual in attempts))


def _diff_hint(expected: str, actual: str, label: str = "") -> str:
    """报告首处差异及两份完整正文。"""
    limit = min(len(expected), len(actual))
    index = next((i for i in range(limit) if expected[i] != actual[i]), limit)
    window = slice(max(0, index - 30), index + 30)
    head = ("  【%s】" % label) if label else "  "
    return ("%s第 %d 个字符起不同（期望 %d 字 / 实际 %d 字）\n"
            "  差异处 期望…%r…\n"
            "  差异处 实际…%r…\n"
            "  ── 完整期望 ──\n  %r\n"
            "  ── 完整实际 ──\n  %r\n"
            "  常见原因：话题标签/@提及的自动补全改写了正文，"
            "或者换行被编辑器合并了。"
            % (head, index, len(expected), len(actual),
               expected[window], actual[window], expected, actual))


# 排期

def resolve_ui_timezone(name: str) -> ZoneInfo:
    """把 UI 时区名变成 ``ZoneInfo``，缺 tzdata 时给可照做的提示。"""
    value = (name or "").strip()
    if not value:
        raise ProbeRequired(describe_gap("ui_timezone"))
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise PublishStepError(
            "找不到时区 %r。Windows 需安装 tzdata：\n    uv pip install --python .venv\\Scripts\\python.exe tzdata\n固定 UTC 偏移无法处理夏令时。" % value) from exc
    except ValueError as exc:
        raise PublishStepError("不是有效的 IANA 时区名：%r" % value) from exc


def business_timezone() -> str:
    """运营选择发布时刻用的时区。⚠️ 与 ``ui_timezone`` 是两件事：这个决定她看到和
    填写的墙上时刻，``ui_timezone`` 是 Business Suite 那台设备的时区，决定能选到哪个月。"""
    value = str(cfg().get("publish", "timezone", "") or "").strip()
    if not value:
        raise PublishStepError("[publish].timezone 为空；发布时刻没有可解释的时区")
    resolve_ui_timezone(value)
    return value


def resolve_business_timezone() -> ZoneInfo:
    return resolve_ui_timezone(business_timezone())


def ui_time_is_ambiguous(when: datetime, ui_timezone: str) -> bool:
    """目标绝对时刻落在 UI 时区回拨的重复墙上时间时返回 True。"""
    if not isinstance(when, datetime) or when.tzinfo is None or when.utcoffset() is None:
        raise ValueError("排期时刻必须显式带时区")
    zone = resolve_ui_timezone(ui_timezone)
    local = when.astimezone(zone)
    naive = local.replace(tzinfo=None)
    return (naive.replace(tzinfo=zone, fold=0).utcoffset()
            != naive.replace(tzinfo=zone, fold=1).utcoffset())


def assert_ui_time_unambiguous(when: datetime, ui_timezone: str) -> None:
    if ui_time_is_ambiguous(when, ui_timezone):
        shown = when.astimezone(resolve_ui_timezone(ui_timezone))
        raise PublishStepError(
            "目标时刻换算到 %s 后是重复的墙上时间 %s（夏令时回拨 fold=%d）；"
            "Business Suite 输入框没有 UTC offset，无法证明会选中哪一次。"
            "请改用当天另一个德国槽位，程序不会冒险早/晚一小时。"
            % (ui_timezone, shown.strftime("%Y-%m-%d %I:%M %p"), shown.fold))


def assert_ui_timezone_is_device(zone: ZoneInfo, when: datetime) -> None:
    """比较 UI 配置与设备在目标时刻的时区偏移，避免两地 DST 切换日期不同造成误判。"""
    ui_offset = when.astimezone(zone).utcoffset()
    device_offset = when.astimezone().utcoffset()      # 无参 = 本机时区
    if ui_offset == device_offset:
        return
    raise PublishStepError(
        "[publish].ui_timezone = %r 与**这台机器**的时区对不上。\n"
        "  目标时刻 %s 上：配置时区偏移 %s，本机偏移 %s。\n"
        "  Business Suite 显示的时刻跟**设备本机时间**走，所以这两者必须一致。\n"
        "  两条修法，选一条：\n"
        "    a) 这台机器的时区才是对的 → 把 ui_timezone 改成它的 IANA 名；\n"
        "    b) 机器时区被人改过 → 先把 Windows 时区改回去。\n"
        "  ⛔ 不要「反正差几小时我自己减一下」——两地夏令时切换日不同，"
        "一年里有两段一周左右的窗口时差会变，手算必错。"
        % (zone.key, when.isoformat(), ui_offset, device_offset))


async def _switch_is_on(switch, *, timeout: float, attempts: int = 2) -> bool:
    """点击后等待开关状态更新，再决定是否补点，避免连点将其关闭。"""
    for attempt in range(max(1, attempts)):
        if await switch.is_checked():
            return True
        await switch.click(timeout=_ms(timeout))
        deadline = time.monotonic() + min(float(timeout), _SWITCH_ON_BUDGET)
        while True:
            if await switch.is_checked():
                return True
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.25)
    return await switch.is_checked()


async def _settle_pairs(date_locator, group_locator, *, timeout: float) -> None:
    """等待两组定位数量稳定且相等；配对失败由调用方报告。"""
    deadline = time.monotonic() + timeout
    previous = None
    while True:
        counts = (await date_locator.count(), await group_locator.count())
        if previous == counts and counts[0] == counts[1] and counts[0] > 0:
            return
        if time.monotonic() > deadline:
            return
        previous = counts
        await asyncio.sleep(0.25)


async def set_schedule(page, when: datetime, *, ui_timezone: str,
                       timeout: float = DEFAULT_UI_TIMEOUT,
                       verify_device: bool = True,
                       target_channels: tuple[str, ...] | None = None) -> str:
    """显式换算到 UI 时区，核验设备偏移并回读各渠道日期时间。"""
    if not isinstance(when, datetime) or when.tzinfo is None or (
            when.utcoffset() is None):
        raise ValueError("排期时刻必须显式带时区")
    assert_ui_time_unambiguous(when, ui_timezone)
    zone = resolve_ui_timezone(ui_timezone)
    if verify_device:
        assert_ui_timezone_is_device(zone, when)
    local = when.astimezone(zone)
    await assert_page_usable(page)

    switch = locator_for(page, "schedule_switch").first
    await switch.wait_for(state="visible", timeout=_ms(timeout))
    if not await _switch_is_on(switch, timeout=timeout, attempts=2):
        raise PublishStepError(
            "点了 %r 但它没有被打开（等了 %.0f 秒，回读仍是关，重试过一次）。"
            "\n  aria-checked=%r"
            "\n  ⚠️ 先怀疑**点到的不是那个开关**：Business Suite 的 composer "
            "有多个变体，`Schedule` 这一块下面还有 `Share to`（Facebook story / "
            "Threads）等别的开关。确认 role=switch 且名字仍是这一个。"
            % (COMPOSER["schedule_switch"].name, _SWITCH_ON_BUDGET,
               await switch.get_attribute("aria-checked")))

    # 每个渠道各有日期时间控件；异步渲染稳定后再枚举，不能只取 first。
    date_locator = locator_for(page, "schedule_date_input")
    group_locator = locator_for(page, "schedule_time_group")
    await date_locator.first.wait_for(state="visible", timeout=_ms(timeout))
    await group_locator.first.wait_for(state="visible", timeout=_ms(timeout))
    await _settle_pairs(date_locator, group_locator, timeout=timeout)
    date_inputs = await date_locator.all()
    groups = await group_locator.all()
    if target_channels is not None and (len(target_channels) != 1 or len(date_inputs) != 1 or len(groups) != 1):
        raise PublishStepError('单渠道必须且只能有一套排期控件；已停止，未提交')
    if not date_inputs or not groups or len(date_inputs) != len(groups):
        raise PublishStepError(
            "排期控件配不成对：日期框 %d 个、时间控件 %d 个。"
            "\n  每个渠道应当各有一套（Facebook 一套、Instagram 一套）。"
            "\n  **在这里停下**，不去猜哪个日期框配哪个时间控件 ——"
            "配错的后果是某个渠道被排到别的时刻，而那种错没人会立刻发现。"
            % (len(date_inputs), len(groups)))

    date_text = local.strftime("%m/%d/%Y")
    hour12 = local.hour % 12 or 12
    meridiem = "AM" if local.hour < 12 else "PM"
    readbacks: list[str] = []
    for index, (date_input, group) in enumerate(zip(date_inputs, groups), 1):
        await date_input.wait_for(state="visible", timeout=_ms(timeout))
        await group.wait_for(state="visible", timeout=_ms(timeout))
        where = "第 %d/%d 组排期控件" % (index, len(groups))

        # ---- 日期。placeholder 是 mm/dd/yyyy（dump 第 26 条），所以是美式格式 ----
        await _type_into(page, date_input, date_text, timeout=timeout)
        actual_date = (await date_input.input_value()).strip()
        if not _same_date(actual_date, local):
            raise PublishStepError(
                "%s的日期回读对不上：期望 %s，UI 上是 %r。"
                "\n  ⚠️ 先怀疑**日期格式变了**（placeholder 应当仍是 mm/dd/yyyy）；"
                "格式变了而代码照旧 strftime，会静默地排到**另一个日子**。"
                % (where, date_text, actual_date))

        rendered = await _set_one_time(
            page, group, hour12, local.minute, meridiem,
            timeout=timeout, where=where)
        readbacks.append("%s %s" % (actual_date, rendered))

    # 同时显示业务时刻和 UI 时刻，便于对照。
    return "UI 显示 %s（%s）＝ 目标 %s" % (
        " ／ ".join(readbacks), zone.key, when.isoformat())


async def _set_one_time(page, group, hour12: int, minute: int, meridiem: str,
                        *, timeout: float, where: str) -> str:
    """写入并回读时分与 AM/PM；小时尝试一位和两位格式。"""
    # ---- 三个 spinbutton：minutes / meridiem 有名字，小时靠排除法 ----
    spins = await group.get_by_role("spinbutton").all()
    labels = [(await item.get_attribute("aria-label") or "").strip()
              for item in spins]
    known = {COMPOSER["schedule_minutes"].name,
             COMPOSER["schedule_meridiem"].name}
    unnamed = [item for item, label in zip(spins, labels) if label not in known]
    # 要求三个控件、一个无名控件及两个已知名称，避免错配时分。
    if len(spins) != 3 or len(unnamed) != 1 or not known.issubset(set(labels)):
        raise PublishStepError(
            "%s里的 spinbutton 不是「小时 + minutes + meridiem」三个"
            "（实际 %d 个，aria-label = %r）。"
            "\n  小时那个 G1 没录到名字，本来是靠排除法定位的"
            "（缺口 composer_hours_spinbutton）；"
            "\n  个数一变这条推断就不成立，**所以在这里停下**，"
            "不去猜哪个是小时。" % (where, len(spins), labels))

    hours = unnamed[0]
    minutes_input = spins[labels.index(COMPOSER["schedule_minutes"].name)]
    meridiem_input = spins[labels.index(COMPOSER["schedule_meridiem"].name)]
    rendered = ""
    for hour_text in (str(hour12), "%02d" % hour12):
        await _type_into(page, hours, hour_text, timeout=timeout)
        await _type_into(page, minutes_input, "%02d" % minute, timeout=timeout)
        await _type_into(page, meridiem_input, meridiem, timeout=timeout)
        rendered = (await group.inner_text() or "").replace(_ZWSP, "").strip()
        if _same_time(rendered, hour12, minute, meridiem):
            return rendered

    # 报告各输入值，便于定位时分或 AM/PM 的错误。
    detail = []
    for name, item in (("小时", hours), ("分钟", minutes_input),
                       ("AM/PM", meridiem_input)):
        detail.append("%s=%r" % (name, await _field_value(item)))
    raise PublishStepError(
        "%s的时刻回读对不上：期望 %d : %02d %s，UI 上是 %r（1 位与 2 位写法都试过了）。"
        "\n  逐字段当前值：%s"
        % (where, hour12, minute, meridiem, rendered, "、".join(detail)))


async def _field_value(target) -> str | None:
    """读受控输入的当前值；不是 input 元素就返回 None（表示"读不到"）。"""
    try:
        return (await target.input_value()).strip()
    except Exception:                             # noqa: BLE001 - 非 input 元素
        return None


async def _clear_field(page, target, *, timeout: float) -> None:
    """清空后回读；Ctrl+A 不生效时用 End 加退格，避免新旧数字拼接。"""
    await target.click(timeout=_ms(timeout))
    await page.keyboard.press("Control+A")
    await page.keyboard.press("Delete")
    current = await _field_value(target)
    if current is None or not current:
        return
    # Ctrl+A 被页面自己的快捷键吃掉时的退路：光标移到末尾逐个退格。
    await page.keyboard.press("End")
    for _ in range(12):
        current = await _field_value(target)
        if current is None or not current:
            return
        await page.keyboard.press("Backspace")


async def _type_into(page, target, value: str, *,
                     timeout: float = DEFAULT_UI_TIMEOUT) -> str | None:
    """清空后输入并回读，失败再尝试 fill；语义比对由调用方完成。"""
    want = value.strip().upper()
    await _clear_field(page, target, timeout=timeout)
    await page.keyboard.type(value)
    got = await _field_value(target)
    if got is None or got.upper() == want:
        # None = 不是 input，读不到值。这里不假装验过，交给调用方的回读闸。
        return got
    await target.fill(value, timeout=_ms(timeout))
    return await _field_value(target)


def _same_date(rendered: str, local: datetime) -> bool:
    match = _RENDERED_DATE.search(rendered or "")
    if not match:
        return False
    month, day, year = (int(item) for item in match.groups())
    return (month, day, year) == (local.month, local.day, local.year)


def _same_time(rendered: str, hour12: int, minute: int, meridiem: str) -> bool:
    """按语义比对，不按字符串比——`12 : 30 AM` 的补零形态没被实测过。"""
    match = _RENDERED_TIME.search(rendered or "")
    if not match:
        return False
    return (int(match.group(1)), int(match.group(2)),
            match.group(3).upper()) == (hour12, minute, meridiem.upper())


# 提交与日历回读

def _verified_locator(spec: Locator) -> tuple[bool | None, str]:
    return evidence.verify(spec, cfg().state_dir)


def _verified_signal(spec: EvidenceSignal) -> tuple[bool | None, str]:
    return evidence.verify_signal(spec, cfg().state_dir)


def submission_evidence_ready() -> bool:
    try:
        require_submission_evidence()
    except ProbeRequired:
        return False
    return True


def readback_evidence_ready() -> bool:
    try:
        require_readback_evidence()
    except ProbeRequired:
        return False
    return True


def _require_reviewed_dump(*specs) -> None:
    configured = str(cfg().get("publish", "ui_probe_dump", "") or "").strip()
    if not configured:
        raise ProbeRequired("[publish].ui_probe_dump 为空；验收证据没有审核边界")
    expected = Path(configured).name
    wrong = [spec.key for spec in specs
             if getattr(spec, "source_dump", "") != expected]
    if wrong:
        raise ProbeRequired(
            "验收证据没有全部来自 config 审核的同一份 v2 dump（%s）：%s"
            % (expected, "、".join(wrong)))


def require_account_context_evidence() -> EvidenceSignal:
    """核验已录证的 FB 账号上下文；所需渠道仍须独立回读确认。"""
    spec = SIGNALS.get("composer_account_context")
    if spec is None:
        raise ProbeRequired(describe_gap("composer_account_context"))
    if spec.kind != "semantic" or not spec.role or not spec.name:
        raise ProbeRequired(
            "composer_account_context 必须是可定位的 v2 semantic 账号语义，"
            "不能用 URL 或手填 token 代替")
    required = ("facebook_account_token", "facebook_account_regex")
    missing = [key for key in required if not spec.attributes.get(key)]
    if missing:
        raise ProbeRequired(
            "composer_account_context 缺少目标账号 token：%s" % "、".join(missing))
    wanted = str(cfg().get("publish", "facebook_page_name", "") or "").strip()
    if (not wanted or spec.attributes.get(
            "facebook_account_token", "").casefold() != wanted.casefold()):
        raise ProbeRequired(
            "账号上下文证据的 FB 主页 token 与当前 [publish].facebook_page_name"
            " 不一致（证据 %r / 配置 %r）"
            % (spec.attributes.get("facebook_account_token"), wanted))
    _require_reviewed_dump(spec)
    passed, detail = _verified_signal(spec)
    if passed is not True:
        raise ProbeRequired("账号上下文不能从本机 v2 dump 回查：%s" % detail)
    return spec


def require_submission_evidence() -> tuple[Locator, EvidenceSignal]:
    button = COMPOSER.get("composer_submit_button")
    success = SIGNALS.get("composer_success_signal")
    if button is None or success is None:
        raise ProbeRequired(
            describe_gap("composer_submit_button") + "\n\n"
            + describe_gap("composer_success_signal"))
    account = require_account_context_evidence()
    _require_reviewed_dump(button, account, success)
    if button.evidence_kind != "interaction":
        raise ProbeRequired("提交按钮必须来自同一 v2 dump 的可信交互，不能来自被动快照")
    for label, result in (
            ("提交按钮", _verified_locator(button)),
            ("提交成功信号", _verified_signal(success))):
        passed, detail = result
        if passed is not True:
            raise ProbeRequired(
                "%s不能从本机 v2 probe dump 回查：%s。"
                "\n⛔ 发布提交保持关闭；不得把缺失证据当作通过。"
                % (label, detail))
    return button, success


def require_readback_evidence() -> EvidenceSignal:
    spec = SIGNALS.get("planner_scheduled_card")
    loaded = SIGNALS.get("planner_loaded_signal")
    if spec is None:
        raise ProbeRequired(describe_gap("planner_scheduled_card"))
    if spec.kind != "semantic" or not spec.role or not spec.name:
        raise ProbeRequired(
            "planner_scheduled_card 必须是可定位的 v2 semantic 卡片语义")
    if loaded is None:
        raise ProbeRequired(
            "v2 dump 尚未回填 planner_loaded_signal；不能区分 React 仍在加载与空日历")
    if loaded.kind != "semantic" or not loaded.role or not loaded.name:
        raise ProbeRequired(
            "planner_loaded_signal 必须是数据完成后的 v2 semantic 语义，"
            "URL/页面骨架不能证明 Planner 数据已就绪")
    missing = [key for key in evidence.PLANNER_REQUIRED
               if not spec.attributes.get(key)]
    if missing:
        raise ProbeRequired(
            "planner_scheduled_card 缺少从 v2 dump 回填的属性：%s。"
            "\n⛔ 无法完整回读排期或远端占用槽，G6c 保持关闭。"
            % "、".join(missing))
    current_targets = {
        "facebook_account_token": str(
            cfg().get("publish", "facebook_page_name", "") or "").strip(),
        "instagram_account_token": str(
            cfg().get("publish", "instagram_account", "") or "").strip(),
    }
    wrong_targets = [key for key, value in current_targets.items()
                     if not value or spec.attributes.get(key, "").casefold()
                     != value.casefold()]
    if wrong_targets:
        raise ProbeRequired(
            "Planner 卡片账号 token 与当前目标配置不一致：%s"
            % "、".join(wrong_targets))
    # 此结构检查不证明远端图片；完整媒体能力另由 capabilities 核验。
    button, success = require_submission_evidence()
    account = require_account_context_evidence()
    _require_reviewed_dump(button, account, success, loaded, spec)
    for label, signal in (("Planner 加载信号", loaded), ("排期卡片", spec)):
        passed, detail = _verified_signal(signal)
        if passed is not True:
            raise ProbeRequired(
                "%s不能从本机 v2 probe dump 回查：%s。"
                "\n⛔ G6c 保持关闭，不能写 scheduled。" % (label, detail))
    empty = SIGNALS.get("planner_empty_state")
    if empty is not None:
        if empty.kind != "semantic" or not empty.role or not empty.name:
            raise ProbeRequired(
                "planner_empty_state 必须是数据完成后的 v2 semantic 空态语义")
        _require_reviewed_dump(empty)
        passed, detail = _verified_signal(empty)
        if passed is not True:
            raise ProbeRequired("Planner 空态证据不能从 v2 dump 回查：%s" % detail)
    passed, detail = evidence.verify_publish_chain(
        button, account, success, loaded, spec, cfg().state_dir)
    if passed is not True:
        raise ProbeRequired(
            "v2 证据不能证明同页 account → submit → success → Planner → final：%s"
            % detail)
    return spec


def _signal_name(spec: EvidenceSignal) -> str:
    if spec.kind == "url":
        return "url:%s" % spec.url_prefix
    return "semantic:%s:%s" % (spec.role, spec.name)


async def _wait_for_signal(page, spec: EvidenceSignal, timeout: float) -> None:
    if spec.kind == "url":
        await page.wait_for_url(
            lambda value: str(value).startswith(spec.url_prefix),
            timeout=_ms(timeout))
        return
    target = page.get_by_role(spec.role, name=spec.name, exact=False).first
    await target.wait_for(state="visible", timeout=_ms(timeout))


async def _signal_already_visible(page, spec: EvidenceSignal) -> bool:
    """点击前排除陈旧成功信号，避免把上一次 toast 当成本次确认。"""
    if spec.kind == "url":
        return _safe_page_url(page).startswith(spec.url_prefix)
    target = page.get_by_role(spec.role, name=spec.name, exact=False).first
    try:
        return bool(await target.is_visible())
    except Exception:                             # noqa: BLE001
        return False


async def submit(page, *, timeout: float = DEFAULT_UI_TIMEOUT,
                 button_spec: Locator | None = None,
                 success_spec: EvidenceSignal | None = None) -> SubmitResult:
    """点击一次并等待录证成功信号；超时不重试，测试外使用证据注册表。"""
    if button_spec is None or success_spec is None:
        registered_button, registered_success = require_submission_evidence()
        button_spec = button_spec or registered_button
        success_spec = success_spec or registered_success
    await assert_page_usable(page)
    target = _build(page, button_spec).first
    # 可见性检查发生在点击之前；失败属于 failed_pre_submit，而不是模糊提交。
    await target.wait_for(state="visible", timeout=_ms(timeout))
    if await _signal_already_visible(page, success_spec):
        raise PublishStepError(
            "提交前成功信号已经可见，无法证明它属于本次尝试；没有点击提交")
    url_before = _safe_page_url(page)
    clicked_at = datetime.now().astimezone().isoformat()
    try:
        # 这一行是整个函数唯一一次 click()。任何异常都不能再点第二下。
        await target.click(timeout=_ms(timeout))
    except Exception as exc:                      # noqa: BLE001
        return SubmitResult(
            clicked=True, confirmed=False, clicked_at=clicked_at,
            observed_at=datetime.now().astimezone().isoformat(),
            url_before=url_before, url_after=_safe_page_url(page),
            error="提交点击返回异常，是否已被页面接收无法判断：%s" % exc)
    try:
        await _wait_for_signal(page, success_spec, timeout)
    except Exception as exc:                      # noqa: BLE001
        return SubmitResult(
            clicked=True, confirmed=False, clicked_at=clicked_at,
            observed_at=datetime.now().astimezone().isoformat(),
            url_before=url_before, url_after=_safe_page_url(page),
            error="点击后没有等到已录证的成功信号；绝不自动重试：%s" % exc)
    after = _safe_page_url(page)
    remote_id = ""
    pattern = success_spec.attributes.get("remote_id_regex", "")
    if pattern:
        match = re.search(pattern, after)
        if match:
            remote_id = _regex_remote_id(match)
    return SubmitResult(
        clicked=True, confirmed=True, clicked_at=clicked_at,
        observed_at=datetime.now().astimezone().isoformat(),
        url_before=url_before, url_after=after,
        success_signal=_signal_name(success_spec), remote_id=remote_id)


def _safe_page_url(page) -> str:
    try:
        return str(page.url or "")
    except Exception:                             # noqa: BLE001
        return ""


def _regex_remote_id(match: re.Match[str]) -> str:
    """读取可选远端 ID；无命名组/捕获组的证据正则同样应当合法。"""
    named = match.groupdict().get("remote_id", "")
    if named:
        return named
    return match.group(1) if match.lastindex else ""


def _card_text(value: str) -> str:
    return " ".join(normalize_caption(value).split())


async def _node_text_values(node) -> tuple[str, ...]:
    """分别读取 inner text/aria-label，去重后保留可做精确比较的值。"""
    chunks: list[str] = []
    try:
        chunks.append(str(await node.inner_text() or ""))
    except Exception:                             # noqa: BLE001
        pass
    try:
        chunks.append(str(await node.get_attribute("aria-label") or ""))
    except Exception:                             # noqa: BLE001
        pass
    return tuple(dict.fromkeys(
        value for value in (_card_text(chunk) for chunk in chunks) if value))


async def _node_text(node) -> str:
    """读取一个已录证语义节点；不把整张卡片当成任何元数据。"""
    return " ".join(await _node_text_values(node))


async def _readback_screenshot(page, path: Path | None,
                               timeout: float) -> str:
    if path is None:
        return ""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        await page.screenshot(
            path=str(path), full_page=False,
            mask=[page.locator(SENSITIVE_INPUT_SELECTOR)],
            timeout=_ms(timeout))
    except Exception:                             # noqa: BLE001
        return ""
    return str(path)


async def _entries_when_ready(page, spec: EvidenceSignal, role: str, *,
                              timeout: float) -> list:
    """等待可解析时刻的条目；月份标题不能证明数据已加载，超时交调用方判定。"""
    try:
        pattern = re.compile(spec.attributes.get("datetime_regex") or "")
    except re.error:
        pattern = None
    budget = min(float(timeout), _PLANNER_ENTRY_BUDGET)
    deadline = time.monotonic() + budget
    cards: list = []
    while True:
        cards = await page.get_by_role(role).all()
        if pattern is None:
            if cards:
                return cards
        else:
            for card in cards:
                if pattern.search(await _node_text(card) or ""):
                    return cards
        if time.monotonic() >= deadline:
            return cards
        await asyncio.sleep(0.5)


async def _planner_cards(page, spec: EvidenceSignal, *, timeout: float,
                         loaded_spec: EvidenceSignal | None,
                         empty_spec: EvidenceSignal | None):
    """按角色枚举后，以完整正文和时刻识别日历条目。"""
    await page.goto(selectors.CONTENT_CALENDAR_URL,
                    wait_until="domcontentloaded", timeout=_ms(timeout))
    await assert_page_usable(page)
    if loaded_spec is not None:
        await _wait_for_signal(page, loaded_spec, timeout)
    role = spec.attributes.get("entry_role") or spec.role
    cards = await _entries_when_ready(page, spec, role, timeout=timeout)
    if cards:
        return cards
    if empty_spec is not None:
        await _wait_for_signal(page, empty_spec, timeout)
        return []
    if loaded_spec is not None:
        # 数据就绪须有录证，不能将页面标题或骨架当作零占用依据。
        return []
    raise PublishStepError(
        "Planner 数据就绪后没有排期卡片，也没有已录证的 empty state；"
        "不能把 React 尚未加载误读成远端零占用")


def _planner_runtime_signals(card_spec: EvidenceSignal | None
                            ) -> tuple[EvidenceSignal | None,
                                       EvidenceSignal | None]:
    if card_spec is not None:                    # 离线单元测试显式注入
        return None, None
    return (SIGNALS.get("planner_loaded_signal"),
            SIGNALS.get("planner_empty_state"))


def _entry_naive(rendered: str, spec: EvidenceSignal) -> datetime | None:
    """从条目完整可访问名解析唯一时刻。"""
    return evidence.parse_entry_moment(rendered, spec.attributes)


async def _open_channel_dialogs(
        page, entry, spec: EvidenceSignal, *, timeout: float
        ) -> dict[str, str]:
    """只读详情的渠道与 remote ID；仅用 Escape 关闭，不操作 Publish now 或 Boost。"""
    attrs = spec.attributes
    try:
        pattern = re.compile(str(attrs.get("remote_id_regex") or ""))
    except re.error as exc:
        raise ProbeRequired("remote_id_regex 无效：%s" % exc) from exc
    found: dict[str, str] = {}
    try:
        await entry.click(timeout=_ms(timeout))
    except Exception as exc:                          # noqa: BLE001
        raise PublishStepError("点不开日历条目的详情弹窗：%s" % exc) from exc
    try:
        dialog = page.get_by_role(
            str(attrs.get("dialog_role") or "dialog"),
            name=str(attrs.get("dialog_name") or ""), exact=False).first
        # 无详情弹窗时保留渠道未知，由调用方决定是否完整。
        await dialog.wait_for(state="visible", timeout=_ms(timeout))
        deadline = time.monotonic() + timeout
        previous, stable_since = None, time.monotonic()
        while True:
            rendered = await _node_text(dialog)
            remotes = {_regex_remote_id(match) for match in pattern.finditer(rendered)} - {''}
            remote = next(iter(remotes)) if len(remotes) == 1 else ''
            markers = [channel for channel in ('facebook', 'instagram')
                       if attrs.get('%s_marker' % channel) and attrs['%s_marker' % channel] in rendered]
            # One dialog-wide ID cannot establish two independent channel IDs.
            # Unknown aggregate layouts must not hide the other channel's occupancy.
            if len(markers) > 1 or len(remotes) > 1:
                break
            current = {}
            for channel in ("facebook", "instagram"):
                marker = str(attrs.get("%s_marker" % channel) or "")
                token = str(attrs.get("%s_account_token" % channel) or "")
                if marker and marker in rendered and remote and evidence_token_present(rendered, token):
                    current[channel] = remote
            # Recorded snapshots 181-184 show ID/channel before the preview account.
            # Metrics outside this preview do not determine post readiness.
            ready = current if 'Loading preview' not in rendered else {}
            if ready != previous:
                previous, stable_since = ready, time.monotonic()
            if ready and time.monotonic() - stable_since >= .4:
                found = ready
                break
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(min(.2, max(0, deadline - time.monotonic())))
    except Exception:                                 # noqa: BLE001
        pass
    finally:
        try:
            await page.keyboard.press("Escape")
        except Exception:                             # noqa: BLE001
            pass
    return found


async def _collect_planner_matches(
        cards, spec: EvidenceSignal, *, expected_naive: datetime,
        expected_caption: str, target_channels: tuple[str, ...],
        expected_image_count: int | None, page=None, timeout: float = 30.0,
        open_dialogs: bool = False, diagnostics: dict | None = None
        ) -> tuple[list[_PlannerMatch], tuple[str, ...], tuple[str, ...],
                   int | None, bool]:
    """匹配日历条目，可展开详情核验渠道与 ID；本函数不验证远端图片。"""
    for channel in target_channels:
        if channel not in {"facebook", "instagram"}:
            raise ProbeRequired("未知目标渠道：%s" % channel)
    matches: list[_PlannerMatch] = []
    missing_match: tuple[str, ...] = ()
    last_seen: tuple[str, ...] = ()
    hits: list[tuple[object, str]] = []
    observations = {
        "links_read": 0, "datetime_parse_failed": 0, "time_mismatch": 0,
        "caption_mismatch": 0, "matched_entries": 0, "unclassified_links": 0,
    }
    relevant_samples: list[dict] = []
    other_samples: list[dict] = []
    for card in cards:
        rendered = await _node_text(card)
        parsed = _entry_naive(rendered, spec)
        caption_matches = expected_caption in rendered
        time_matches = parsed == expected_naive
        observations["links_read"] += 1
        if caption_matches and parsed is None:
            observations["datetime_parse_failed"] += 1
        elif caption_matches and not time_matches:
            observations["time_mismatch"] += 1
        elif time_matches and not caption_matches:
            observations["caption_mismatch"] += 1
        elif caption_matches and time_matches:
            observations["matched_entries"] += 1
            hits.append((card, rendered))
        else:
            # role=link 还包含导航，无法证明是卡片时不归咎于日期解析。
            observations["unclassified_links"] += 1
        samples = relevant_samples if caption_matches or time_matches else other_samples
        if len(samples) < 20:
            samples.append({
                "text_length": len(rendered), "text_preview": rendered[:200],
                "parsed_at": parsed.isoformat() if parsed is not None else "",
                "caption_matches": caption_matches, "time_matches": time_matches,
            })
    if diagnostics is not None:
        diagnostics.update(observations)
        diagnostics["samples"] = (relevant_samples + other_samples)[:20]
    if not hits:
        return matches, missing_match, last_seen, None, False
    if not open_dialogs:
        # 提交前先比对同槽正文基线，无需展开详情。
        for card, rendered in hits:
            matches.append(_PlannerMatch(
                schedule_rendered=rendered, channels=(), image_count=None,
                remote_id="", card_sha256=hashlib.sha256(
                    rendered.encode("utf-8")).hexdigest()))
        return matches, missing_match, last_seen, None, False

    remote_ids: dict[str, str] = {}
    for card, _rendered in hits:
        remote_ids.update(await _open_channel_dialogs(
            page, card, spec, timeout=timeout))
        if all(channel in remote_ids for channel in target_channels):
            break
    seen = tuple(c for c in target_channels if c in remote_ids)
    missing = tuple(c for c in target_channels if c not in remote_ids)
    if missing:
        return matches, missing, seen, None, False
    rendered = hits[0][1]
    matches.append(_PlannerMatch(
        schedule_rendered=rendered, channels=seen, image_count=None,
        remote_id=";".join(
            "%s=%s" % (channel, remote_ids[channel]) for channel in seen),
        card_sha256=hashlib.sha256(
            (rendered + "\n" + expected_caption).encode("utf-8")).hexdigest()))
    return matches, missing_match, last_seen, None, False


async def snapshot_scheduled_matches(
        page, when: datetime, final_text: str, *, ui_timezone: str,
        target_channels: tuple[str, ...] = ("facebook", "instagram"),
        expected_image_count: int | None = None,
        timeout: float = DEFAULT_UI_TIMEOUT,
        card_spec: EvidenceSignal | None = None) -> ScheduledBaseline:
    """读取提交前同槽同内容基线；仅 match_count 为零时可继续提交。"""
    spec = card_spec or require_readback_evidence()
    zone = resolve_ui_timezone(ui_timezone)
    expected_naive = when.astimezone(zone).replace(
        tzinfo=None, second=0, microsecond=0)
    expected_caption = _card_text(final_text)
    loaded_spec, empty_spec = _planner_runtime_signals(card_spec)
    cards = await _planner_cards(
        page, spec, timeout=timeout, loaded_spec=loaded_spec,
        empty_spec=empty_spec)
    visible = await _visible_calendar_range(page, spec)
    target_ui_date = when.astimezone(zone).date()
    if visible is None and card_spec is None:
        raise ProbeRequired(
            "提交前基线缺少 Planner 可见日期范围证据；不能把视图外旧卡当不存在")
    if visible is not None and not visible[0] <= target_ui_date <= visible[1]:
        raise PublishStepError(
            "目标 UI 日期 %s 不在本次 Planner 可见范围 %s 至 %s；"
            "未点击提交，避免漏掉视图外旧卡"
            % (target_ui_date, visible[0], visible[1]))
    matches, _missing, _seen, _images, _image_mismatch = (
        await _collect_planner_matches(
            cards, spec, expected_naive=expected_naive,
            expected_caption=expected_caption,
            target_channels=target_channels,
            expected_image_count=expected_image_count))
    return ScheduledBaseline(
        observed_at=datetime.now().astimezone().isoformat(),
        match_count=len(matches),
        remote_ids=tuple(match.remote_id for match in matches
                         if match.remote_id),
        card_sha256=tuple(match.card_sha256 for match in matches))


async def verify_scheduled(
        page, when: datetime, final_text: str, *, ui_timezone: str,
        target_channels: tuple[str, ...] = ("facebook", "instagram"),
        expected_image_count: int | None = None,
        pre_submit_baseline: ScheduledBaseline | None = None,
        expected_remote_id: str = "",
        timeout: float = DEFAULT_UI_TIMEOUT,
        card_spec: EvidenceSignal | None = None,
        screenshot_path: Path | None = None) -> ScheduledReadback:
    """重新进入内容日历，按目标时刻、完整正文和渠道回读排期卡片。"""
    spec = card_spec or require_readback_evidence()
    if spec.kind != "semantic":
        raise ProbeRequired("planner_scheduled_card 必须是 v2 semantic 证据")
    for required in (
            "date_format", "time_format", "datetime_regex", "entry_role",
            "dialog_role", "dialog_name", "remote_id_regex",
            "facebook_marker", "instagram_marker"):
        if not spec.attributes.get(required):
            raise ProbeRequired(
                "planner_scheduled_card 缺少从 v2 dump 回填的 %s" % required)
    zone = resolve_ui_timezone(ui_timezone)
    local = when.astimezone(zone)
    expected_naive = local.replace(tzinfo=None, second=0, microsecond=0)
    expected_caption = _card_text(final_text)
    expected_hash = hashlib.sha256(final_text.encode("utf-8")).hexdigest()

    loaded_spec, empty_spec = _planner_runtime_signals(card_spec)
    try:
        cards = await _planner_cards(
            page, spec, timeout=timeout, loaded_spec=loaded_spec,
            empty_spec=empty_spec)
    except Exception as exc:                      # noqa: BLE001
        shot = await _readback_screenshot(page, screenshot_path, timeout)
        return ScheduledReadback(
            found=False, observed_at=datetime.now().astimezone().isoformat(),
            target_at=when.isoformat(), ui_at=local.isoformat(),
            final_text_sha256=expected_hash,
            expected_image_count=expected_image_count,
            screenshot=shot, error=str(exc))
    diagnostics: dict = {}
    (matches, missing_match, last_seen, last_image_count,
     image_mismatch) = await _collect_planner_matches(
        cards, spec, expected_naive=expected_naive,
        expected_caption=expected_caption, target_channels=target_channels,
        expected_image_count=expected_image_count,
        page=page, timeout=timeout, open_dialogs=True, diagnostics=diagnostics)

    causal_error = ""
    selected: _PlannerMatch | None = None
    if pre_submit_baseline is not None and pre_submit_baseline.match_count != 0:
        causal_error = (
            "提交前已经存在 %d 张同槽/同文案/同素材/同渠道卡片；"
            "旧卡不能冒充本次提交" % pre_submit_baseline.match_count)
    else:
        for match in matches:
            # 成功信号与回读均有 ID 时必须一致。
            if (expected_remote_id and match.remote_id
                    and expected_remote_id != match.remote_id):
                causal_error = (
                    "提交成功信号 remote_id=%s 与日历卡片 remote_id=%s 不一致"
                    % (expected_remote_id, match.remote_id))
                continue
            if (pre_submit_baseline is not None
                    and match.remote_id
                    and match.remote_id in pre_submit_baseline.remote_ids):
                causal_error = "日历命中的是提交前已经存在的 remote_id=%s" % match.remote_id
                continue
            selected = match
            break
    if selected is not None:
        shot = await _readback_screenshot(page, screenshot_path, timeout)
        return ScheduledReadback(
            found=True, observed_at=datetime.now().astimezone().isoformat(),
            target_at=when.isoformat(), ui_at=local.isoformat(),
            final_text_sha256=expected_hash, channels=selected.channels,
            image_count=selected.image_count,
            expected_image_count=expected_image_count,
            remote_id=selected.remote_id,
            success_signal=_signal_name(spec),
            card_sha256=selected.card_sha256, screenshot=shot,
            diagnostics=diagnostics)
    mismatch_details = []
    for key, label in (
            ("datetime_parse_failed", "含完整正文的链接时刻无法解析"),
            ("time_mismatch", "含完整正文的链接时刻不匹配"),
            ("caption_mismatch", "目标时刻的链接正文不匹配")):
        if diagnostics[key]:
            mismatch_details.append("%s：%d 条" % (label, diagnostics[key]))
    detail = (
        "排期卡片图片数不符：实得 %s，期望 %s"
        % (last_image_count, expected_image_count)
        if image_mismatch else
        (causal_error if causal_error else
         "排期卡片明确缺少渠道：%s" % "、".join(missing_match)
         if missing_match else
         "；".join(mismatch_details) if mismatch_details else
         "内容日历里找不到同时匹配目标时刻与完整最终正文的卡片"))
    shot = await _readback_screenshot(page, screenshot_path, timeout)
    return ScheduledReadback(
        found=False, observed_at=datetime.now().astimezone().isoformat(),
        target_at=when.isoformat(), ui_at=local.isoformat(),
        final_text_sha256=expected_hash, channels=last_seen,
        missing_channels=missing_match,
        image_count=last_image_count,
        expected_image_count=expected_image_count,
        success_signal="", screenshot=shot, error=detail,
        diagnostics=diagnostics)


async def _visible_calendar_range(page, spec: EvidenceSignal
                                  ) -> tuple[date, date] | None:
    """从独立的月份、年份 heading 解析可见范围。"""
    attrs = spec.attributes
    required = ("visible_month_role", "visible_month_format",
                "visible_year_role", "visible_year_format")
    if any(not attrs.get(key) for key in required):
        return None
    parsed: dict[str, datetime] = {}
    for key in ("month", "year"):
        role = attrs["visible_%s_role" % key]
        fmt = attrs["visible_%s_format" % key]
        for node in await page.get_by_role(role).all():
            # 逐个**单值**试；拼接后的 "September September" 解析不出来。
            for rendered in await _node_text_values(node):
                try:
                    parsed[key] = datetime.strptime(rendered, fmt)
                except ValueError:
                    continue
                break
            if key in parsed:
                break
    if "month" not in parsed or "year" not in parsed:
        raise PublishStepError(
            "Planner 上读不到可见月份/年份 heading；不能把视图外的旧卡当不存在")
    start = date(parsed["year"].year, parsed["month"].month, 1)
    end = date(start.year + (start.month == 12),
               start.month % 12 + 1, 1) - timedelta(days=1)
    return start, end


async def read_remote_slot_inventory(
        page, *, ui_timezone: str, business_timezone: str,
        timeout: float = DEFAULT_UI_TIMEOUT,
        card_spec: EvidenceSignal | None = None,
        include_cards: bool = False) -> RemoteSlotInventory:
    """读取已证明覆盖的日期；缺渠道证据时保留未知，不能据此声明空档。"""
    production_evidence = card_spec is None
    spec = card_spec or require_readback_evidence()
    required = ("datetime_regex", "date_format", "time_format")
    missing = [key for key in required if not spec.attributes.get(key)]
    range_required = ("visible_month_role", "visible_month_format",
                      "visible_year_role", "visible_year_format")
    range_missing = [key for key in range_required
                     if not spec.attributes.get(key)]
    if spec.kind != "semantic" or missing:
        raise ProbeRequired(
            "planner_scheduled_card 缺少远端槽位回读证据：%s"
            % "、".join(missing or ("semantic",)))
    if production_evidence and range_missing:
        raise ProbeRequired(
            "Planner 远端占用读取缺少可见日期范围证据：%s；"
            "不能把当前 DOM 误当成未来所有日期"
            % "、".join(range_missing))
    try:
        pattern = re.compile(spec.attributes["datetime_regex"])
    except re.error as exc:
        raise ProbeRequired("datetime_regex 无效：%s" % exc) from exc
    ui_zone = resolve_ui_timezone(ui_timezone)
    business_zone = resolve_ui_timezone(business_timezone)
    loaded_spec, empty_spec = _planner_runtime_signals(card_spec)
    cards = await _planner_cards(
        page, spec, timeout=timeout, loaded_spec=loaded_spec,
        empty_spec=empty_spec)
    visible = await _visible_calendar_range(page, spec)
    occupied: dict[float, datetime] = {}
    remote_cards: list[RemotePlannerCard] = []
    combined_format = "%s %s" % (
        spec.attributes["date_format"], spec.attributes["time_format"])
    for card in cards:
        raw = await _node_text(card)
        matches = list(pattern.finditer(raw))
        if not matches:
            # 导航 link 不属于日历条目，跳过无法解析时刻的项。
            continue
        if len(matches) != 1:
            raise PublishStepError(
                "已录证的 Planner 条目必须且只能读出一个时刻（实际 %d 个）；"
                "为避免占用同一槽位，整批停止" % len(matches))
        for match in matches:
            groups = match.groupdict()
            if not groups.get("date") or not groups.get("time"):
                raise ProbeRequired(
                    "datetime_regex 必须提供命名组 (?P<date>...) / (?P<time>...)")
            try:
                naive = datetime.strptime(
                    "%s %s" % (groups["date"], groups["time"]),
                    combined_format)
            except ValueError as exc:
                raise PublishStepError(
                    "日历卡片时刻无法按已录证格式解析：%s" % exc) from exc
            first = naive.replace(tzinfo=ui_zone, fold=0)
            second = naive.replace(tzinfo=ui_zone, fold=1)
            if first.astimezone(timezone.utc).astimezone(ui_zone).replace(tzinfo=None) != naive:
                raise PublishStepError("Planner 卡片时刻在 UI 时区的夏令时切换中不存在")
            moments = [first.astimezone(business_zone)]
            if first.utcoffset() != second.utcoffset():
                # UI 卡片没有 offset；回拨小时的两种绝对时刻都当作已占用。
                moments.append(second.astimezone(business_zone))
            occupied.update((at.timestamp(), at) for at in moments)
            if include_cards:
                remote_ids = await _open_channel_dialogs(page, card, spec, timeout=timeout)
                for at in moments:
                    remote_cards.append(RemotePlannerCard(
                        at=at, channels=tuple(sorted(remote_ids)),
                        remote_ids=tuple(sorted(remote_ids.items())), rendered=raw,
                        card_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(), placement='feed'))
    return RemoteSlotInventory(
        occupied=tuple(occupied[key] for key in sorted(occupied)), ui_timezone=ui_timezone,
        visible_start=(visible[0] if visible else None),
        visible_end=(visible[1] if visible else None),
        cards=tuple(remote_cards), cards_loaded=include_cards)


async def read_remote_occupied_slots(
        page, *, ui_timezone: str, business_timezone: str,
        timeout: float = DEFAULT_UI_TIMEOUT,
        card_spec: EvidenceSignal | None = None) -> tuple[datetime, ...]:
    """兼容入口；实际 approve 使用带可见范围的 inventory。"""
    inventory = await read_remote_slot_inventory(
        page, ui_timezone=ui_timezone, business_timezone=business_timezone,
        timeout=timeout, card_spec=card_spec)
    return inventory.occupied


# 失败留痕

async def capture_failure(page, step: str, message: str, *,
                          state_dir: Path, stamp: str) -> StepFailure:
    """记录失败步骤及遮罩截图；遮罩失败不截图，不删除远端内容。"""
    folder = Path(state_dir) / "publish_failures"
    folder.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^0-9A-Za-z_-]+", "_", step).strip("_") or "step"
    target = folder / ("%s_%s.png" % (stamp, safe))
    shot: Path | None = None
    url = ""
    try:
        url = page.url or ""
    except Exception:                             # noqa: BLE001
        url = ""
    try:
        mask = [page.locator(SENSITIVE_INPUT_SELECTOR)]
        await page.screenshot(path=str(target), full_page=False, mask=mask,
                              timeout=_ms(PAGE_HEALTH_TIMEOUT))
        shot = target
    except Exception:                             # noqa: BLE001 - 截不到不算失败
        shot = None
    return StepFailure(step=step, url=url, message=message, screenshot=shot)
