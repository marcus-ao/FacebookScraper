r"""Business Suite UI 自动化（G2–G7）。

默认只做到提交前；显式 ``--submit`` 才开放单次提交与 Planner 回读。
目标账号上下文、提交按钮、成功信号、Planner 数据就绪与同一卡片内独立的
时刻/正文/FB/IG/图片语义，都必须能在本机同一份 v2 probe dump 现场回查，
并证明 account → submit → success → Planner → final 的同页因果顺序；缺文件、
缺序号、final/遮罩截图无效或属性不一致都会在触碰浏览器前失败闭合。

所有定位都来自 :mod:`publish.selectors`，那里每一条都能在真实 probe dump 里
逐字查到。本模块**不允许**出现任何行内的 CSS/class 选择器；唯一的例外是
:func:`upload_images` 里对 ``<input type=file>`` 这一 HTML 标准控件的处理，
而它走的是 Playwright 的 file chooser 通道，连选择器都不需要（见该函数）。

三条从真机踩出来的事必须写在最前面：

1. **附着到"连接之前就已经打开"的标签页时，Playwright 的 page 对象可能是坏的**
   （CR-64 实测：``page.url`` 为空、``page.evaluate("1+1")`` 直接超时，
   而同一个 target 的原始 CDP 完全正常）。所以本模块**自己开新标签页**
   （:func:`open_composer`），并在动手之前先证明这一页是活的
   （:func:`assert_page_usable`）——**"装上了"和"装了但什么都没发生"
   在输出上一模一样，这个项目为同型失效吃过两次亏。**
2. **每一步都回读校验。** UI 自动化没有事务性，"点了"不等于"成了"。
3. **失败不自动删除任何东西**（G7）：截图 + 打印 URL + 说清停在哪一步 +
   提示人工检查草稿残留。
"""
from __future__ import annotations

import asyncio
import time
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from publish import selectors
from publish.evidence import token_present as evidence_token_present
from publish.selectors import (COMPOSER, SIGNALS, EvidenceSignal, Locator,
                               describe_gap)

# ⚠️ **这些超时没有在真机上标定过**，和写死的选择器是同一类东西（CR-63 的教训：
# profile 归属核对写了 5 秒，而本机实际要 7–9 秒，于是"每一次都超时"，
# 一个完全正确的环境被报成"你开错了浏览器"）。
# 所以这里一律取**宽松**值，并且由 `[publish].ui_timeout_seconds` 兜底可调：
# 超时值宁可偏大——偏大只是慢，偏小是把对的环境判成错的。
DEFAULT_UI_TIMEOUT = 30.0
# 等日历条目渲染出来的预算（秒）。空日历会真的等满这一段，所以不设成整个
# ui_timeout —— 那会让每次发布都先干等半分钟；20 秒是实测加载时延的富余量。
_PLANNER_ENTRY_BUDGET = 20.0
# 定时开关点下去之后，等它的 aria-checked 真的翻过来的预算（秒）。
# React 受控开关的状态是异步回来的；点击返回 ≠ 已经打开。
_SWITCH_ON_BUDGET = 8.0
# 页面可用性自检要短，因为它就是用来快速判定"这一页是不是 CR-64 那种坏页"的。
PAGE_HEALTH_TIMEOUT = 8.0
# 上传后给 UI 的沉淀窗口。**不是固定 sleep**：到点就走，不阻塞成功路径。
UPLOAD_SETTLE_TIMEOUT = 15.0

# 截图时遮罩凭据类输入。**与 tools/probe_publish.py 里那条保持一致**
# （`tests_publish.py` 有断言钉住两者相同，防止一边改了另一边忘了）。
# 它是通用 HTML 凭据遮罩，不是 Business Suite 的流程定位器。
SENSITIVE_INPUT_SELECTOR = (
    'input[type="password"], input[type="email"], '
    'input[autocomplete~="username"], input[autocomplete~="current-password"], '
    'input[autocomplete~="new-password"], input[autocomplete~="one-time-code"]'
)

_ZWSP = selectors.ZERO_WIDTH_SPACE
# Time input 容器渲染成 `12 : 30 AM`（dump 第 25/28 条的实测值）。
_RENDERED_TIME = re.compile(r"(\d{1,2})\s*:\s*(\d{2})\s*([AP]M)", re.IGNORECASE)
_RENDERED_DATE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")


class ProbeRequired(RuntimeError):
    """这一步需要 G1 还没录到的东西；不得凭猜补上。"""


class PublishStepError(RuntimeError):
    """某一步在真实 UI 上失败了（定位没找到、回读对不上、页面不可用）。"""


@dataclass(frozen=True)
class AccountContext:
    """:func:`ensure_logged_in` 的结论。**注意它不是一个 bool。**

    "登录了没有"和"选中的是不是对的 Page"是两件事，后者更严重：
    登录态在、但上下文是别的主页，会把德语内容发到错误的主页上。
    默认准备模式没有账号证据时 :attr:`selection_verified` 是 ``False``；
    生产 ``--submit`` 只有在审核过的 v2 账号语义同时读到配置中的 FB Page 与
    IG 帐号后才会把它设为 ``True``。这个字段存在，就是为了不让调用方把
    "没查出问题"误当成"查过了没问题"。
    """

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


@dataclass(frozen=True)
class ScheduledBaseline:
    """提交前同条件卡片集合；G6c 必须证明提交后出现的是新结果。"""

    observed_at: str
    match_count: int
    remote_ids: tuple[str, ...] = ()
    card_sha256: tuple[str, ...] = ()


@dataclass(frozen=True)
class RemoteSlotInventory:
    """一次 Planner 读取的槽位与它实际覆盖的 UI 日期范围。"""

    occupied: tuple[datetime, ...]
    ui_timezone: str
    visible_start: date | None = None
    visible_end: date | None = None

    def covers(self, slots: tuple[datetime, ...] | list[datetime]) -> bool:
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


# ---------------------------------------------------------------------------
# 定位
# ---------------------------------------------------------------------------

def locator_for(page, key: str):
    """按 :mod:`publish.selectors` 的登记项取一个 Playwright 定位。

    ⛔ 只接受 composer 界面的条目。旧版创作者后台那几条是**旁证**，
    拿到 composer 上用就是猜——两份 dump 是两个不同的界面。
    """
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


# ---------------------------------------------------------------------------
# 页面可用性（CR-64）
# ---------------------------------------------------------------------------

async def assert_page_usable(page, *, timeout: float = PAGE_HEALTH_TIMEOUT) -> None:
    """证明这一页真的能被 Playwright 驱动，**再**开始操作。

    背景（CR-64，用户真机）：``connect_over_cdp`` 附着到连接之前就已经打开的
    Business Suite 标签页时，Playwright 的 page 对象可能永远拿不到帧树——
    ``page.url`` 是空串、``page.evaluate("1+1")`` 直接 TimeoutError，
    而同一个 target 的原始 CDP 一切正常。G1 recorder 当年把这个失败
    ``except Exception: continue`` 吞掉了，于是用户走完 20 分钟才发现什么都没记上。

    **这里绝不吞。** 检不过就当场说清楚，并给出可照做的下一步。
    """
    try:
        value = await asyncio.wait_for(page.evaluate("1 + 1"), timeout)
    except asyncio.TimeoutError as exc:
        raise PublishStepError(
            "这个标签页 Playwright 驱动不了（page.evaluate 超时 %.0fs）。\n"
            "  这就是 CR-64 那种坏页：附着到**连接之前就已经打开**的标签页时，\n"
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
            "page.url 是空的——典型的 CR-64 坏页，先刷新那一页再重试。")


# ---------------------------------------------------------------------------
# G1-1 入口
# ---------------------------------------------------------------------------

async def open_composer(context, *, timeout: float = DEFAULT_UI_TIMEOUT):
    """开一个**新**标签页，按 G1 录到的路径进 composer，返回该 page。

    为什么自己开新页而不复用已有标签页：见 :func:`assert_page_usable`。
    Playwright 自己创建的 target 不会踩 CR-64。

    走的是 dump 第 2 条录到的那条路（内容日历 → Create post），
    **不直接 goto composer URL**：没有证据表明直接打开那个地址能得到
    一个可用的新草稿。
    """
    page = await context.new_page()
    await page.goto(selectors.CONTENT_CALENDAR_URL,
                    wait_until="domcontentloaded", timeout=_ms(timeout))
    await assert_page_usable(page)

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
        # dump 第 3 条：进 composer 后先有一屏要点 Done。它是不是渠道选择、
        # 什么条件下出现，dump 没说清（见 selectors 里那条 inferred），
        # 所以这里当成**可选**的一步：出现就点，不出现不报错。
        await _click_if_present(page, "composer_done_button", timeout=timeout)
        await caption.first.wait_for(state="visible", timeout=_ms(timeout))
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


# ---------------------------------------------------------------------------
# G2 登录态与目标 Page
# ---------------------------------------------------------------------------

async def ensure_logged_in(page, *, page_name: str, instagram_account: str = "",
                           account_spec: EvidenceSignal | None = None,
                           timeout: float = DEFAULT_UI_TIMEOUT
                           ) -> AccountContext:
    """确认会话可用，并**尽可能**核对当前上下文是不是那个 DE 主页。

    ❌ **不得实现自动登录**（全局红线 1）。会话过期的正解是让人去登。

    诚实地说清这一步能证明什么、不能证明什么：

    - **能**证明 composer 开着且可驱动（正文框在）；
    - **能**在页面上完全找不到目标显示名时**拦住**——那是强否定信号；
    - 准备模式没有 v2 账号证据时，只能做上述弱核对；
    - 自动提交必须传入同一审核 dump 的 ``composer_account_context``，
      读到目标 FB Page 的**完整显示名**。

    ⚠️ **传 ``account_spec`` 的那一次必须发生在图片上传之后。**
    已录证的那条定位是 FB 预览里的 `heading`，而**预览要有内容才渲染它**
    （dump 里它首次出现在 evidence_order=31，紧跟 `Add photo/video` 的 ord=29）。
    空 composer 上调用它只会白等到超时 —— 2026-09-01 第一次 G8 真机跑
    就是这么失败的。调用顺序见 ``publish/workflow.py`` 的 [2/7] 与 [3/7]。

    ⛔ **IG 帐号 composer 上根本不显示**（46 张 composer 快照里 `neakasa.de`
    命中 0 次），由提交后的 Planner 详情弹窗回读证明。截图上 `Post to` 下拉框
    虽然写着 "… and neakasa.de"，但那个值**从来没被 dump 录到**，
    不许拿它当定位（全局红线 5）。
    """
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
        # ⚠️ **只核对 Facebook。** 实测 composer 上没有 IG 帐号名
        # （`docs/PROBE_FINDINGS_20260901.md` 第一节），IG 由 G6c 提交后
        # 从 Planner 详情弹窗回读证明，少了会转人工。
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
    # 渠道（FB Page / IG 帐号）：用户 2026-09-01 实测确认**进来时默认全勾选**，
    # 所以不需要程序去点。但程序仍然**读不出**当前勾了哪几个
    # （G1 缺口 composer_placement_toggles 只降级、没关闭），
    # 所以这里只提醒人看一眼，不假装核对过。
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
    """页面文本里有没有这串字。

    用 ``get_by_text`` 而不是拼选择器：它按可访问文本找，不碰混淆 class。
    """
    value = (needle or "").strip()
    if not value:
        return False
    try:
        return await page.get_by_text(value, exact=False).count() > 0
    except Exception:                             # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# G3 图片上传
# ---------------------------------------------------------------------------

async def upload_images(page, paths: list[Path], *,
                        timeout: float = DEFAULT_UI_TIMEOUT) -> tuple[str, ...]:
    """把图片交给 composer，返回**必须由人复核**的提示。

    ⚠️ 这里刻意**不写 ``input[type=file]`` 的选择器**：走 Playwright 的
    file chooser 通道（点按钮 → 截住浏览器弹出的文件选择器 → 交文件）。
    好处有三：

    1. 不需要任何选择器，连 HTML 标准控件都不用碰；
    2. 顺便**当场回答了 G1 的第 2 问**——弹得出 chooser 就说明背后是真的
       文件输入，弹不出就是拖拽区，两种情况的处理完全不同；
    3. ``chooser.is_multiple()`` 是一条真回读：只收单文件却要传 5 张时
       当场失败，而不是默默只传上去一张。

    ⛔ 仍然没有解决的是"缩略图数量对不对"——composer 的缩略图容器没进 dump
    （G1 缺口 ``composer_upload_thumbnails``），所以这一条以**返回提示**的方式
    交给人复核，绝不假装已经验过。
    """
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

    # 不用固定 sleep：给 UI 一个**有上限**的沉淀窗口，到点就走。
    # networkidle 在 FB 这种长连接 SPA 上可能永远不到，所以超时是预期路径。
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
        "⚠️ **缩略图数量没有被程序核对过**：composer 的缩略图容器没进 G1 dump"
        "（缺口 composer_upload_thumbnails）。提交前请人眼数一遍。",
    )


# ---------------------------------------------------------------------------
# G4 文案
# ---------------------------------------------------------------------------

def normalize_caption(value: str) -> str:
    """把 UI 回读的正文归一化到可以逐字符比对的形状。

    只做两件事，**都是实测依据**：

    - 去掉零宽空格：Business Suite 的编辑器会往里塞 ``U+200B``
      （dump 第 10 条录到的输入值就是 ``"He​"``）；
    - 统一换行：回读可能带 ``\\r\\n``。

    ⛔ 不做别的归一化。把空格、大小写、标点也"顺手"归一，
    等于把这道回读闸关掉——它存在的全部意义就是发现"填进去的和想填的不一样"。
    """
    return (value or "").replace(_ZWSP, "").replace("\r\n", "\n").replace("\r", "\n")


async def _write_caption(page, box, text: str, *, per_line: bool,
                         timeout: float) -> str:
    """把正文写进 contenteditable，返回归一化后的回读结果。

    ⛔ **不要改回 ``keyboard.type()``。** 那个 API 逐字符派发真实按键事件，
    而 Meta 的编辑器（Lexical 形态）在 ``@`` / ``#`` 上挂着提及与话题标签的
    typeahead。菜单一弹出来就异步改 DOM 并移动光标，后面的字符就落到别处去了。
    2026-09-01 真机实测的破坏样子（CR-71）：

        期望  … Bescheid 👇 \\n\\n#Neakasa … #PetsHome
        实际  … Bescheid 👇 \\n@ifa.ber #Neakasa … #PetsHomelin

    `@ifa.berlin` 被撕成 `@ifa.ber` + `lin` 分别插到了两个地方。

    ``keyboard.insert_text()`` 走 CDP 的 ``Input.insertText``，**不派发按键事件**，
    typeahead 因此不会被触发；编辑器仍然收到 ``beforeinput``/``input``，
    所以标签照常被 token 化（截图里那些蓝色高亮）。
    """
    await box.click(timeout=_ms(timeout))
    # 清空已有内容（重跑时 composer 里可能残留上一次的字）
    await page.keyboard.press("Control+A")
    await page.keyboard.press("Delete")
    if per_line:
        # 后备策略：整段插入时若 `\n` 没有变成换行，就逐行插入 + Shift+Enter。
        # Shift+Enter 是按键事件，但它只在**行末**按，那时 typeahead 已经因为
        # 空格/标点关掉了，风险远小于逐字符敲。
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
    """填正文并**逐字符回读比对**，不一致就抛错。

    正文框是 contenteditable（dump 第 5 条：命中的是内层 div，
    第 3 层语义祖先才是那个 ``role=combobox``），所以：

    - 不能一次 ``fill()``：富文本编辑器会把多段挤成一段，
      或者把 ``\\n`` 当成提交（PUBLISH_PLAN 3.2 第 3 点）；
    - **也不能用 ``keyboard.type()``** —— 见 :func:`_write_caption`，
      逐字符按键会把 ``@`` / ``#`` 的自动补全招出来并打乱正文（CR-71）。

    两种写法**依次试**，每种都当场回读校验：

    1. 整段 ``insert_text``（零按键，最不容易被 typeahead 干扰）；
    2. 逐行 ``insert_text`` + 行末 ``Shift+Enter``（万一整段插入时
       ``\\n`` 没被编辑器变成换行）。

    ⚠️ **回读闸一条都没有放松**：两种写法都必须**逐字符**对上才算成功，
    两种都对不上就带着完整差异停下、绝不提交。重试是安全的 ——
    这一步在提交之前，写坏了只是 composer 里的草稿，没有任何东西发出去。
    """
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
    """指出第一处不同，并**把两段完整打出来**——只说事实，不猜原因。

    ⚠️ 上一版只打第一处差异前后各 20 个字符，结果 2026-09-01 那次真机失败
    **把真正的破坏藏起来了**：窗口里只看到多一个 ``\\n``，而实际是
    ``@ifa.berlin`` 被撕成两半插到了别的地方。为看清它多花了一整轮真机。
    正文上限 2200，全打出来的代价可以忽略。
    """
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


# ---------------------------------------------------------------------------
# G5 排期
# ---------------------------------------------------------------------------

def resolve_ui_timezone(name: str) -> ZoneInfo:
    """把 UI 时区名变成 ``ZoneInfo``，缺 tzdata 时给可照做的提示（CR-60）。"""
    value = (name or "").strip()
    if not value:
        raise ProbeRequired(describe_gap("ui_timezone"))
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise PublishStepError(
            "找不到时区 %r。Windows 不自带 IANA 时区数据库，"
            "需要纯数据包 tzdata：\n"
            "    uv pip install --python .venv\\Scripts\\python.exe tzdata\n"
            "❌ 不要改成写死 UTC 偏移绕过去：夏令时切换日会把帖子发到错误的时刻，"
            "而且没人会立刻发现（PUBLISH_PLAN 3.3）。" % value) from exc
    except ValueError as exc:
        raise PublishStepError("不是有效的 IANA 时区名：%r" % value) from exc


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
    """核对「配置里的 UI 时区」与「这台机器的时区」在目标时刻是同一个偏移。

    ⚠️ **用户 2026-09-01 实测：Business Suite 上那个时刻跟发帖者设备的本机时间走**
    （不是 Page 的时区设置，也不是受众所在时区）。
    既然真相源是**这台机器**，那配置就必须和这台机器一致——否则就是
    「配置写的是一回事、UI 认的是另一回事」，而这种错**只会表现为帖子晚/早一小时
    发出去，没人会立刻发现**。

    比的是**目标时刻的偏移**，不是当下的偏移：两地夏令时切换日不同，
    偏移差在一年里会有两段一周左右的窗口从 9 小时变成 8 小时
    （2026：10-25~11-01；2027：03-14~03-28）。拿"今天的偏移"去判会在那两段里误判。
    """
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
    """把定时开关打开，并**等到回读真的变成开**。

    ⛔ **不要退回"点一下就立刻 is_checked()"。** 2026-09-01 真机实测（CR-76）：
    那样写在同一台机器上有时过、有时不过 —— 它是个竞态，之前只是撞对了。
    React 受控开关的 `aria-checked` 是状态回来之后才翻的，点击返回时往往还没翻。

    ⚠️ **先等再补点，顺序不能反。** 直接连点两下会把已经打开的开关又关回去，
    而"关回去"的表现和"根本没打开"一模一样 —— 那种错最难查。
    """
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
    """等到两组定位的条数**稳定且相等**。

    ⚠️ 只等到"第一个可见"是不够的：Facebook 那一行先渲染、Instagram 那一行
    晚一拍时，枚举会拿到 1 个，然后**静默地只设一个渠道** ——
    那正是 CR-73 的后果（IG 停在默认值等于立刻发）。
    ⛔ 这里**不抛异常**：条数最终对不上由调用方那条"配不成对"报，
    错误信息在那里更完整。
    """
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
                       verify_device: bool = True) -> str:
    """打开定时开关、设好日期与时刻，并**回读 UI 显示的值比对**。

    时区是整组最容易错的一步（PUBLISH_PLAN 3.3）。**用户 2026-09-01 实测的结论是：
    UI 上那个时刻跟发帖者设备的本机时间走。** 所以这里做两件事：

    1. 把目标时刻**显式**换算到 ``ui_timezone``（不依赖本地时区隐式生效，
       因为"隐式生效"和"显式换算到恰好相同的时区"在出错时长得完全不一样）；
    2. 核对 ``ui_timezone`` 与这台机器在**目标时刻**的偏移一致
       （:func:`assert_ui_timezone_is_device`）。
       ``verify_device=False`` 只给纯换算测试用。

    返回一行同时带 **UI 上显示的时刻**与**目标时刻**的文本——
    两个必须一起打，否则人看到 UI 上写着 "1:00 AM" 会以为排错了。
    """
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

    # ---- 每个渠道一套排期控件。**不是一套。** ----
    # ⚠️⚠️ 2026-09-01 真机实测（CR-73）：Schedule 那一块下面有
    # `heading 'Facebook'` 和 `heading 'Instagram'` 两行，**各带一个日期框
    # 和一个时间控件**。dump 里也是这个形状 —— 录制时人把整套流程做了两遍：
    #
    #     #37 点日期框 → #39 选日期 → #40-43 两个 spinbutton      （第一组）
    #     #44 点日期框 → #45 选日期 → #46-49 两个 spinbutton      （第二组）
    #
    # 那不是"改了一次时间"，是两个渠道各设一次。
    # ⛔ **原来这里取 `.first`，于是只设了 Facebook 那一组**，
    #    Instagram 那组停在默认值（当天 + 当前时刻）——**等于立刻发出去**。
    #    截图上是 `Sep 1, 2026 06:23 PM`，而 FB 是 `Sep 10, 2026`。
    #    这是这条链上后果最严重的一种错：内容对、主页对、时刻悄悄错。
    # ⛔ **先等，再枚举。`.all()` 不等待。**
    # 排期那一块是**打开定时开关之后才异步渲染**的。原来的代码是
    # `locator.first` + `wait_for(visible)`，它会等；换成 `.all()` 之后
    # 少了这一步，于是在还没渲染出来的那一刻枚举，拿到 0 个
    # ——2026-09-01 第四次真机就是这么失败的（CR-74）。
    date_locator = locator_for(page, "schedule_date_input")
    group_locator = locator_for(page, "schedule_time_group")
    await date_locator.first.wait_for(state="visible", timeout=_ms(timeout))
    await group_locator.first.wait_for(state="visible", timeout=_ms(timeout))
    await _settle_pairs(date_locator, group_locator, timeout=timeout)
    date_inputs = await date_locator.all()
    groups = await group_locator.all()
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

    # ⚠️ **两个时刻必须一起打。** UI 上显示的是本机时间，而人心里想的是受众那边
    # 的时间——只打前者，他会以为排错了；只打后者，他核对不了屏幕上那一行。
    return "UI 显示 %s（%s）＝ 目标 %s" % (
        " ／ ".join(readbacks), zone.key, when.isoformat())


async def _set_one_time(page, group, hour12: int, minute: int, meridiem: str,
                        *, timeout: float, where: str) -> str:
    """把一组「时 : 分 AM/PM」写好并回读。返回 UI 渲染出来的那一行。

    ⛔ **小时用两种写法依次试**（``1`` 和 ``01``）：分段时间输入通常按
    **固定两位**消化数字并自动跳到下一格，只给一位时它可能还在等第二位，
    也可能把新数字追加到旧值后面（CR-72 就是这么把 1 变成 11 的）。
    哪种对只有真机知道，所以两种都试，**每种都当场回读**。
    """
    # ---- 三个 spinbutton：minutes / meridiem 有名字，小时靠排除法 ----
    spins = await group.get_by_role("spinbutton").all()
    labels = [(await item.get_attribute("aria-label") or "").strip()
              for item in spins]
    known = {COMPOSER["schedule_minutes"].name,
             COMPOSER["schedule_meridiem"].name}
    unnamed = [item for item, label in zip(spins, labels) if label not in known]
    # 三个条件缺一不可：总数是 3、只有一个没名字、两个已知名字都真的在。
    # 少了最后一条，「两个 minutes + 一个没名字」会通过前两条，
    # 然后在 labels.index("meridiem") 上抛 ValueError —— 那是个看不懂的错。
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

    # 只报合成串看不出是哪一格坏的（CR-72 就为此多花了一轮真机），
    # 所以把三个 input 各自的当前值一并打出来。
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
    """把字段真的清空。

    ⛔ **不要退回"只按 Ctrl+A 然后直接打字"。** 那等于假设 Ctrl+A 一定选中
    当前字段的内容 —— Business Suite 的分段时间输入上**这个假设不成立**
    （CR-72：旧值 `1` 上再敲 `1`，得到的是 `11`，排到了 11:00 AM）。
    所以这里清完要**回读确认真的空了**，不空就按 End + 退格兜底。
    """
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
    """把值写进 React 受控输入。返回最终读到的值（不是 input 元素则 ``None``）。

    两条路依次试：**清空 → 打字 → 回读**；对不上再 ``fill()`` → 回读。
    为什么不直接 ``fill()``：这些是 React 受控输入，实测形态未知。
    为什么要有退路：**单一手段失败就报错，会把"手段不合适"报成"环境坏了"。**

    ⛔ **这里不做对错判定**，只负责"尽力写进去并把结果如实交出来"。
    判定留给调用方的**语义**检查（日期用 :func:`_same_date`、
    时刻用 :func:`_same_time`）—— UI 合法地把 ``9`` 显示成 ``09``
    并不是错误，用裸字符串相等去判会把它误报成故障。
    """
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


# ---------------------------------------------------------------------------
# G6/G6c 提交与内容日历回读
# ---------------------------------------------------------------------------

def _verified_locator(spec: Locator) -> tuple[bool | None, str]:
    from core.config import cfg                    # 延迟导入，避免模块初始化环
    from publish import evidence
    return evidence.verify(spec, cfg().state_dir)


def _verified_signal(spec: EvidenceSignal) -> tuple[bool | None, str]:
    from core.config import cfg                    # 延迟导入，避免模块初始化环
    from publish import evidence
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
    from core.config import cfg                    # 延迟导入，避免模块初始化环
    configured = str(cfg().get("publish", "ui_probe_dump", "") or "").strip()
    if not configured:
        raise ProbeRequired("[publish].ui_probe_dump 为空；生产证据没有审核边界")
    expected = Path(configured).name
    wrong = [spec.key for spec in specs
             if getattr(spec, "source_dump", "") != expected]
    if wrong:
        raise ProbeRequired(
            "生产证据没有全部来自 config 审核的同一份 v2 dump（%s）：%s"
            % (expected, "、".join(wrong)))


def require_account_context_evidence() -> EvidenceSignal:
    """提交前的目标主页闸。**只验 Facebook，这是实测结论不是妥协。**

    2026-09-01 的完整 v2 dump（46 张 composer 快照）证明：composer 上
    **从头到尾没有出现过 IG 帐号名**，渠道只有 `img 'Instagram'` 一个图标。
    "提交前同时证明两个渠道"在这个 UI 上不可满足 —— 重录也不会有。

    ⚠️ **"少任一渠道就不算 scheduled"这条保证没有放松**，
    只是 IG 的确认从提交前挪到了提交后：`require_readback_evidence()`
    要求 Planner 上 FB 与 IG **各有一个独立详情弹窗、各带自己的 remote id**，
    少任何一个都只能记 `submitted_unverified` 并转人工。
    """
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
    from core.config import cfg
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
                "\n⛔ 生产提交保持关闭；不得把缺失证据当作通过。"
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
    from publish import evidence as _ev
    missing = [key for key in _ev.PLANNER_REQUIRED
               if not spec.attributes.get(key)]
    if missing:
        raise ProbeRequired(
            "planner_scheduled_card 缺少从 v2 dump 回填的属性：%s。"
            "\n⛔ 无法完整回读排期或远端占用槽，G6c 保持关闭。"
            % "、".join(missing))
    from core.config import cfg
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
    # ⚠️ **图片数量的硬闸从这里搬走了。** 实测 Planner 侧零数量语义
    # （`docs/PROBE_FINDINGS_20260901.md` 第二节），继续要求它等于永远关闸。
    # 张数改由 `upload_images()` 在 composer 上传后数缩略图来保证。
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
    from publish import evidence
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
    """只点击一次并等待 dump 证明的成功信号；超时绝不重试点击。

    ``button_spec/success_spec`` 只用于离线测试。生产调用不传时必须从
    :mod:`publish.selectors` 的证据注册表取得；注册表尚未回填就会在接触页面前
    抛 :class:`ProbeRequired`。
    """
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
    """等到日历里**真的出现能解析出时刻的条目**再返回。

    ⛔ **不要退回"就绪信号一出现就立刻读一次"。**
    2026-09-01 真机实测（CR-75）：``planner_loaded_signal`` 是**月份 heading**，
    它在日历数据**还在转圈**的时候就已经渲染好了 —— 失败截图上
    `Planner` 标题、`September 2026`、`Week`/`Month` 全在，中间一个大转圈。
    那一刻页面上的 ``link`` 全是导航和侧栏（下游那句注释早就写着这件事），
    于是刚刚提交成功的帖子被判成"回读不到"，落成 ``submitted_unverified``。

    ⚠️ **同一个函数也给提交前的基线用，那一侧更危险**：把"还在加载"误读成
    "远端没有同槽卡片"，等于把防重复发布的那道闸悄悄打开。

    判据用的是**已录证的 ``datetime_regex``**，不是新的选择器：
    能从可访问名里解析出时刻的 link 才算日历条目。超时后原样返回当下读到的
    （通常是纯导航 link），由调用方按内容筛出零条 —— 空日历仍然读作空。
    """
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
    """枚举日历条目。**条目 = 同时带正文与完整时刻的那条 link。**

    ⚠️ 2026-09-01 按真实 Planner 重写。旧实现按
    ``get_by_role(spec.role, name=spec.name)`` 找"排期卡片"——
    实测那种元素不存在：条目的可访问名**就是**正文 + 时刻，没有固定标签，
    所以只能按 role 全取回来再在 Python 侧按内容筛。
    """
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
        # 生产路径只会传入同一审核 dump 回填的“数据已就绪”信号；它必须是
        # React 数据完成后的语义，不是页面标题/骨架。出现后零卡片才可读作零占用。
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
    """从日历条目文本里解析出**唯一**一个时刻。

    条目的可访问名同时带正文与时刻（实测形如
    ``'…#test September 15, 2026, 10:00 AM'``），所以这里解析的是整条名字，
    而不是"卡片内那个独立的时刻子元素"——后者在真实 UI 上不存在。
    """
    from publish import evidence as _ev
    return _ev.parse_entry_moment(rendered, spec.attributes)


async def _open_channel_dialogs(
        page, entry, spec: EvidenceSignal, *, timeout: float
        ) -> dict[str, str]:
    """点开这条日历条目的详情弹窗，读出它属于哪个渠道、远端 ID 是多少。

    ⛔ **弹窗里除了关闭什么都不点。** 那上面有 `Publish now` 和 `Boost`，
    误点 `Publish now` 就是立刻发布——所以这里只读文本，
    关闭一律用 Escape（不用坐标、不找按钮）。

    返回 ``{"facebook": "1887…", "instagram": "4378…"}``；读不到就是空 dict。
    """
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
        # 点开没弹窗（点到导航 link、弹窗还没渲染完…）不是错误：
        # 这条条目就是没法给出渠道证据，调用方会当作该渠道未命中。
        await dialog.wait_for(state="visible", timeout=_ms(timeout))
        rendered = await _node_text(dialog)
        match = pattern.search(rendered)
        remote = _regex_remote_id(match) if match else ""
        for channel in ("facebook", "instagram"):
            marker = str(attrs.get("%s_marker" % channel) or "")
            token = str(attrs.get("%s_account_token" % channel) or "")
            if marker and marker in rendered and remote and evidence_token_present(
                    rendered, token):
                found[channel] = remote
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
        open_dialogs: bool = False
        ) -> tuple[list[_PlannerMatch], tuple[str, ...], tuple[str, ...],
                   int | None, bool]:
    """匹配日历条目；``open_dialogs`` 时再逐个点开确认渠道与 remote id。

    ⚠️ **图片数量不再参与匹配**：实测 Planner 侧零数量语义
    （`docs/PROBE_FINDINGS_20260901.md`）。张数的保证挪到了
    `upload_images()` 在 composer 上传后数缩略图那一步。
    """
    for channel in target_channels:
        if channel not in {"facebook", "instagram"}:
            raise ProbeRequired("未知目标渠道：%s" % channel)
    matches: list[_PlannerMatch] = []
    missing_match: tuple[str, ...] = ()
    last_seen: tuple[str, ...] = ()
    hits: list[tuple[object, str]] = []
    for card in cards:
        rendered = await _node_text(card)
        # 必须是完整最终正文，不接受“旧文案 + 新 CTA”把期望文案当子串包含。
        if expected_caption not in rendered:
            continue
        if _entry_naive(rendered, spec) != expected_naive:
            continue
        hits.append((card, rendered))
    if not hits:
        return matches, missing_match, last_seen, None, False
    if not open_dialogs:
        # 提交前基线只要知道"这个槽位上已经有没有同文案条目"，
        # 不点开弹窗——提交前在远端页面上点东西是没必要的风险。
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
    """在提交前读取同槽/同文案/同素材/同渠道卡片，失败一律抛出。

    生产状态机只在 ``match_count == 0`` 时继续点击提交。这样提交后的旧卡片
    不能冒充本次结果；若成功信号与卡片都带 remote_id，后续还必须相等。
    """
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
    (matches, missing_match, last_seen, last_image_count,
     image_mismatch) = await _collect_planner_matches(
        cards, spec, expected_naive=expected_naive,
        expected_caption=expected_caption, target_channels=target_channels,
        expected_image_count=expected_image_count,
        page=page, timeout=timeout, open_dialogs=True)

    causal_error = ""
    selected: _PlannerMatch | None = None
    if pre_submit_baseline is not None and pre_submit_baseline.match_count != 0:
        causal_error = (
            "提交前已经存在 %d 张同槽/同文案/同素材/同渠道卡片；"
            "旧卡不能冒充本次提交" % pre_submit_baseline.match_count)
    else:
        for match in matches:
            # 两端都能读 ID 时必须是同一个远端对象；绝不能用 readback ID
            # 覆盖/掩盖 success ID 不一致。
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
            card_sha256=selected.card_sha256, screenshot=shot)
    detail = (
        "排期卡片图片数不符：实得 %s，期望 %s"
        % (last_image_count, expected_image_count)
        if image_mismatch else
        (causal_error if causal_error else
         "排期卡片明确缺少渠道：%s" % "、".join(missing_match)
         if missing_match else
         "内容日历里找不到同时匹配目标时刻与完整最终正文的卡片"))
    shot = await _readback_screenshot(page, screenshot_path, timeout)
    return ScheduledReadback(
        found=False, observed_at=datetime.now().astimezone().isoformat(),
        target_at=when.isoformat(), ui_at=local.isoformat(),
        final_text_sha256=expected_hash, channels=last_seen,
        missing_channels=missing_match,
        image_count=last_image_count,
        expected_image_count=expected_image_count,
        success_signal="", screenshot=shot, error=detail)


async def _visible_calendar_range(page, spec: EvidenceSignal
                                  ) -> tuple[date, date] | None:
    """当前视图覆盖的日期区间。

    ⚠️ 2026-09-01 按真实 Planner 重写。旧实现找的是一条 "start - end" 范围串
    ——**那种元素不存在**。真实 UI 是 `heading 'September'` +
    `heading '2026'` 两条独立 heading，合起来就是当前月。

    两条 heading 都没有固定标签（月份名每月都变），所以按 role 全取回来、
    逐条试着用声明的格式解析——解析得动的就是它。
    """
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
        card_spec: EvidenceSignal | None = None) -> RemoteSlotInventory:
    """读取占用槽及当前 DOM 被证明覆盖的 UI 日期区间。"""
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
    occupied: set[datetime] = set()
    combined_format = "%s %s" % (
        spec.attributes["date_format"], spec.attributes["time_format"])
    for card in cards:
        raw = await _node_text(card)
        matches = list(pattern.finditer(raw))
        if not matches:
            # 日历上的 link 不止排期条目一种（导航、侧栏都是 link）。
            # 读不出时刻的直接跳过，不是错误。
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
            occupied.add(first.astimezone(business_zone))
            if first.utcoffset() != second.utcoffset():
                # UI 卡片没有 offset；回拨小时的两种绝对时刻都当作已占用。
                occupied.add(second.astimezone(business_zone))
    return RemoteSlotInventory(
        occupied=tuple(sorted(occupied)), ui_timezone=ui_timezone,
        visible_start=(visible[0] if visible else None),
        visible_end=(visible[1] if visible else None))


async def read_remote_occupied_slots(
        page, *, ui_timezone: str, business_timezone: str,
        timeout: float = DEFAULT_UI_TIMEOUT,
        card_spec: EvidenceSignal | None = None) -> tuple[datetime, ...]:
    """兼容入口；生产 approve 使用带可见范围的 inventory。"""
    inventory = await read_remote_slot_inventory(
        page, ui_timezone=ui_timezone, business_timezone=business_timezone,
        timeout=timeout, card_spec=card_spec)
    return inventory.occupied


# ---------------------------------------------------------------------------
# G7 失败留痕
# ---------------------------------------------------------------------------

async def capture_failure(page, step: str, message: str, *,
                          state_dir: Path, stamp: str) -> StepFailure:
    """截图 + 记录停在哪一步。**不删除任何东西。**

    截图会遮罩凭据类输入；遮罩插不进去就**不截**——
    宁可没有截图，也不能把凭据拍进去（与 G1 recorder 同一条边界）。
    """
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
