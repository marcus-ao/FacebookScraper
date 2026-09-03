r"""Business Suite 定位常量（G1 回填）。

⛔ **全局红线 5：不得凭猜测编写 Business Suite 的选择器。**
所以本文件里的每一条都必须能在真实 probe dump 里被逐字找到，
并且带着「出自哪一份 dump 的第几条交互」的来源注释。
:data:`REGISTRY` 里的 ``name`` 字段是**原样抄录**的可访问名，
`tests/tests_publish.py` 会在 dump 还在本机时逐条回查
——**编不出来的定位会当场被测试打回。**

三件必须先知道的事：

1. **不记 class、不记 CSS path。** Business Suite 是 React SPA，
   class 是构建期混淆的，`div > div:nth-child(3) > span` 这类定位
   下次发版就全废。这里一律用 ARIA ``role`` + 可访问名。
2. **两份 dump 是两个不同的界面，不许混用**（见 :data:`SURFACE_*`）。
   `business.facebook.com/latest/composer/` 才是本组要自动化的那条路；
   `www.facebook.com/professional_dashboard/` 那份只作旁证保留，
   它是 FB 单平台的旧版创作者后台，**没有 IG 那一路**。
3. **录到的东西比要用的东西少。** 缺的那几项在 :data:`GAPS` 里逐条点名，
   连"缺它会挡住哪一步、怎么补录"一起写清楚。
   ⛔ **不要因为"看截图就知道那个按钮叫什么"而把它们填上**——
   截图不是 dump，看着填进来的定位会给下一个人一个危险的假绿灯。

来源 dump（都在 `state/`，**不进版本库**，所以这里只记文件名）::

    publish_probe_20260831_222850_347719.json   24 条 · composer（本组用这份）
    publish_probe_20260831_195237_008047.json   63 条 · professional_dashboard（旁证）

⚠️ 那份 24 条的 dump `finished_at=null` 且序号不连续（录制中途被 CR-66 的
死锁打断）。**它过不了 `compose._validated_probe_dump` 的严格校验，这是对的**：
那两条校验证明的是"记录没丢过"。**但用来人工读、回填定位完全够用**，
本文件就是这么来的。严格发布仍然要等用户重录一份干净的。
"""
from __future__ import annotations

from publish.locator_types import (COMPOSER_DUMP, COMPOSER_URL,
                                   CONTENT_CALENDAR_URL, DASHBOARD_DUMP,
                                   SURFACE_COMPOSER, SURFACE_DASHBOARD,
                                   ZERO_WIDTH_SPACE, EvidenceSignal, Gap,
                                   Locator, account_value_from_text,
                                   normalize_account_value)


# =============================================================================
# 一、composer 界面：本组真正要自动化的那条路
# =============================================================================

_COMPOSER_LOCATORS = (
    Locator(
        key="create_post_button",
        step="G1-1 入口：内容日历上进 composer 的按钮",
        surface=SURFACE_COMPOSER,
        role="button",
        name="Create post",
        name_source="visible-text",
        source_dump=COMPOSER_DUMP,
        sequences=(2,),
        breaks_when="点击后 URL 不进 /latest/composer/；或界面语言不是英文时"
                    "整条可访问名换掉（本条来源是可见文本，跟界面语言走）",
        attributes={"page_url": CONTENT_CALENDAR_URL, "tag": "div"},
    ),
    Locator(
        key="composer_done_button",
        step="G1-1 入口：进 composer 后第一屏的 Done",
        surface=SURFACE_COMPOSER,
        role="button",
        name="Done",
        name_source="visible-text",
        source_dump=COMPOSER_DUMP,
        sequences=(3,),
        breaks_when="进 composer 后没有这一屏（Meta 改流程），或它其实是"
                    "渠道选择的确认键而当前账号只有一个渠道时不出现",
        inferred="⚠️ **只知道它叫 Done、被点过一次，不知道它确认的是什么。**"
                 "第 3 条交互的截图上下文没有被记进 dump，"
                 "所以代码里把它当作**可选**的一步：出现就点，不出现不报错。",
        attributes={"page_url": COMPOSER_URL, "tag": "div"},
    ),
    Locator(
        key="add_media_button",
        step="G3 上传：打开选图入口",
        surface=SURFACE_COMPOSER,
        role="button",
        name="Add photo/video",
        name_source="visible-text",
        source_dump=COMPOSER_DUMP,
        sequences=(4,),
        breaks_when="按钮改名（例如拆成 Add photo / Add video）；"
                    "或界面语言变化",
        attributes={"page_url": COMPOSER_URL, "tag": "div"},
    ),
    Locator(
        key="caption_box",
        step="G4 文案：正文输入框",
        surface=SURFACE_COMPOSER,
        role="combobox",
        name="Write into the dialogue box to include text with your post.",
        name_source="aria-label",
        source_dump=COMPOSER_DUMP,
        sequences=(5, 6, 18),
        breaks_when="aria-label 改文案；或 role 从 combobox 变回 textbox"
                    "（combobox 是因为它带 @提及/#标签 自动补全）",
        attributes={
            "tag": "div",
            "is_contenteditable": "true",
            # ⚠️ **这不是 <textarea>**。第 5/18 条显示命中的是内层 div，
            # 语义祖先第 3 层才是这个 combobox —— 富文本编辑器的典型形状，
            # 多段文本要逐行输入，不能一次 fill（PUBLISH_PLAN 3.2 第 3 点）。
            "nested_editable_depth": "3",
        },
    ),
    Locator(
        key="hashtag_menu_button",
        step="G4 文案：打开话题标签面板（本组不用，留作旁证）",
        surface=SURFACE_COMPOSER,
        role="button",
        name="Click to open hashtag menu. " + ZERO_WIDTH_SPACE,
        name_source="visible-text",
        source_dump=COMPOSER_DUMP,
        sequences=(7,),
        breaks_when="面板入口改成图标按钮（可见文本没了）",
        inferred="可访问名末尾是真实存在的零宽空格，"
                 "所以 match() 会退成子串匹配。",
        attributes={"page_url": COMPOSER_URL},
    ),
    Locator(
        key="hashtag_dialog",
        step="G4 文案：话题标签面板本体（本组不用，留作旁证）",
        surface=SURFACE_COMPOSER,
        role="dialog",
        name="Add hashtags",
        name_source="visible-text",
        source_dump=COMPOSER_DUMP,
        sequences=(16,),
        breaks_when="面板标题改名",
        inferred="dump 里 dialog 的可访问名是整个面板的可见文本拼接"
                 "（`Add hashtags Close … Cancel Add (1)`），"
                 "这里只取开头那一段做子串匹配。",
    ),
    Locator(
        key="hashtag_search_box",
        step="G4 文案：标签搜索框（本组不用，留作旁证）",
        surface=SURFACE_COMPOSER,
        role="searchbox",
        name="Search for a hashtag",
        name_source="aria-labelledby",
        source_dump=COMPOSER_DUMP,
        sequences=(9, 10),
        breaks_when="搜索框换实现或改名",
        attributes={"tag": "input", "input_type": "search"},
    ),
    Locator(
        key="schedule_switch",
        step="G5 排期：定时开关",
        surface=SURFACE_COMPOSER,
        role="switch",
        name="Set date and time",
        name_source="aria-label",
        source_dump=COMPOSER_DUMP,
        sequences=(22, 23),
        breaks_when="开关改名，或从 <input type=checkbox role=switch> 换成"
                    "别的控件（那样 is_checked() 会直接报错，不会静默）",
        attributes={"tag": "input", "input_type": "checkbox",
                    "explicit_role": "switch"},
    ),
    Locator(
        key="schedule_date_input",
        step="G5 排期：日期输入框",
        surface=SURFACE_COMPOSER,
        role="textbox",
        name="Date picker",
        name_source="aria-labelledby",
        source_dump=COMPOSER_DUMP,
        sequences=(26,),
        breaks_when="placeholder 不再是 mm/dd/yyyy（日期格式变了，"
                    "代码里那句 strftime 会静默地填出一个**别的日子**）——"
                    "所以填完必须回读比对，见 business_suite.set_schedule",
        attributes={
            "tag": "input",
            # ⚠️ **这两条是实测事实，不是推测**：placeholder 是 mm/dd/yyyy，
            # 说明这台 UI 用的是**美式日期**，不是德式 dd.mm.yyyy。
            "placeholder": "mm/dd/yyyy",
        },
    ),
    Locator(
        key="schedule_time_group",
        step="G5 排期：时间控件（时/分/上下午三个 spinbutton 的容器）",
        surface=SURFACE_COMPOSER,
        role="application",
        name="Time input",
        name_source="aria-labelledby",
        source_dump=COMPOSER_DUMP,
        sequences=(25, 28),
        breaks_when="容器不再是 role=application，或它的可见文本不再是"
                    "`12 : 30 AM` 这种形状（回读比对会当场失败）",
        attributes={
            # 录到的渲染值。**这是 12 小时制 + AM/PM 的直接证据**，
            # 也是回读比对的格式依据（h : mm AM/PM）。
            "rendered_text": "12 : 30 AM",
        },
    ),
    Locator(
        key="schedule_minutes",
        step="G5 排期：分钟",
        surface=SURFACE_COMPOSER,
        role="spinbutton",
        name="minutes",
        name_source="aria-label",
        source_dump=COMPOSER_DUMP,
        sequences=(28,),
        breaks_when="aria-label 改名或大小写变化",
        attributes={"tag": "input"},
    ),
    Locator(
        key="schedule_meridiem",
        step="G5 排期：上午/下午",
        surface=SURFACE_COMPOSER,
        role="spinbutton",
        name="meridiem",
        name_source="aria-label",
        source_dump=COMPOSER_DUMP,
        sequences=(25,),
        breaks_when="aria-label 改名；或 Page 时区设置改成 24 小时制"
                    "（那样这个 spinbutton 会整个消失，"
                    "business_suite 的排除法定位会因为只剩两个而失败闭合）",
        attributes={"tag": "input"},
    ),
)

# =============================================================================
# 二、professional_dashboard 界面：**旁证，不参与自动化**
# =============================================================================
#
# 为什么留着这一段：63 条那份 dump 是**唯一**录到过
# 「Meta 的 Photo/video 按钮背后确实是一个真的 <input type=file multiple>」
# 的证据（第 33/34 条）。G3 因此可以放心走 set_input_files() 而不是模拟拖拽。
# ⛔ **但它是另一个界面的证据，不许把这里的定位直接用到 composer 上。**

_DASHBOARD_LOCATORS = (
    Locator(
        key="dashboard_page_switcher",
        step="G2 账号核对：主页切换器（旧版后台）",
        surface=SURFACE_DASHBOARD,
        role="button",
        name="Switch to Neakasa Deutschland",
        name_source="aria-label",
        source_dump=DASHBOARD_DUMP,
        sequences=(2,),
        breaks_when="主页显示名变了（这条 aria-label 里内嵌了显示名，"
                    "所以它其实是 `Switch to <[publish].facebook_page_name>`）",
        inferred="⛔ **composer 界面上没有录到任何主页切换器。**"
                 "这一条只证明旧版后台里有；G2 不拿它去点 composer。",
    ),
    Locator(
        key="dashboard_file_input",
        step="G3 上传：证明背后是真的 <input type=file>",
        surface=SURFACE_DASHBOARD,
        role="textbox",
        name="",
        name_source="",
        source_dump=DASHBOARD_DUMP,
        sequences=(33, 34),
        breaks_when="Meta 改成纯拖拽区（那样 set_input_files 会找不到目标，"
                    "business_suite 会失败闭合并要求补录）",
        attributes={
            "tag": "input",
            "input_type": "file",
            "multiple": "true",
            # 原样抄录，一个字都没删——它同时证明了这个控件收图也收视频。
            "accept": "image/*,image/heif,image/heic,video/*,video/mp4,"
                      "video/x-m4v,video/x-matroska,.mkv,.avi,.wmv,.mov,.flv,"
                      ".webm,.3gp,.3g2,.mts,.m2ts,.vob,.divx,.f4v,.ogv",
        },
    ),
)

REGISTRY: dict[str, Locator] = {
    item.key: item for item in _COMPOSER_LOCATORS + _DASHBOARD_LOCATORS}

COMPOSER: dict[str, Locator] = {item.key: item for item in _COMPOSER_LOCATORS}

# 新版完整提交 dump 交付后，只能把其中已经通过 evidence.verify_signal 回查的
# 条目填到这里。空表意味着 G6/G6c 必须继续失败闭合。
SIGNALS: dict[str, EvidenceSignal] = {}


# =============================================================================
# 三、G1 **没**录到的（这一段和上面两段同等重要）
# =============================================================================

_GAPS = (
    Gap(
        key="composer_file_input",
        step="G3 上传",
        why_missing="点了 Add photo/video 之后弹出的是**操作系统的文件对话框**，"
                    "那一步不产生任何页面事件，recorder 天然录不到；"
                    "而那一轮用户没有真的选文件，所以连 change 事件都没有。",
        blocks="G3：不知道 composer 里那个 <input type=file> 长什么样",
        how_to_close="不必补录。business_suite.upload_images 改成**运行时发现**："
                     "点开入口后在页面里找 accept 含 image/ 的 file input，"
                     "找不到就失败闭合。旧版后台那条（dashboard_file_input）"
                     "已证明这种控件确实存在，所以这不是在赌。",
    ),
    Gap(
        key="composer_hours_spinbutton",
        step="G5 排期",
        why_missing="用户只点了 minutes 与 meridiem 两个 spinbutton，"
                    "小时那个没被点到，因此没有它的 aria-label。",
        blocks="G5：直接按名字定位小时字段",
        how_to_close="不必补录。Time input 容器里的 spinbutton 一共三个，"
                     "两个已知，**剩下那个按排除法就是小时**；"
                     "个数不是 3 就失败闭合，设完还要回读 `h : mm AM/PM` 比对。",
    ),
    Gap(
        key="composer_upload_thumbnails",
        step="G3 上传",
        why_missing="缩略图是上传**之后**渲染出来的，那一轮根本没传成文件，"
                    "自然也没有缩略图可点、可录。",
        blocks="G3 的【验收】「1 张与 5 张缩略图数量正确」——"
               "程序现在能确认文件已交给上传控件，"
               "**不能**确认 UI 真的收下了几张。",
        how_to_close="补录时真的传 5 张图，点一下其中一张缩略图，"
                     "让缩略图容器的 role 与可访问名进 dump。",
    ),
    Gap(
        key="composer_placement_toggles",
        step="G1 第 4 点",
        why_missing="那一轮完全没有走到渠道选择（FB Page 与 IG 帐号各一路），"
                    "dump 里没有任何 checkbox/switch 属于渠道。",
        # ✅ 2026-09-01 用户实测后**降级**：原先写的是
        # 「『FB + IG 同时发』这条用户点名的范围做不了」——那是高估了。
        blocks="**已降级，不再挡住『FB + IG 同时发』。**"
               "用户 2026-09-01 确认：进 composer 时**两个渠道默认就是全勾选的**，"
               "所以本来就不需要点。"
               "提交前仍不读取或操作勾选控件；提交后由 G6c 在日历卡片上"
               "回读 FB + IG。明确少任一渠道就转人工，不能记 scheduled。",
        how_to_close="只有两种情况才需要补录："
                     "① 要**改**选择（比如只发 FB 不发 IG）；"
                     "② 想让程序自己回读校验渠道。"
                     "两者都要重录一次探查，走到渠道那一屏把控件点一遍。",
    ),
    Gap(
        key="composer_submit_button",
        step="G6 提交",
        why_missing="那一轮在设定时刻时被 CR-66 的死锁打断，没走到提交。",
        blocks="G6 的生产证据门：状态机与单击提交已经实现，但 SIGNALS 为空时"
               "会在接触浏览器前失败闭合；绝不凭猜测解锁品牌主页提交。",
        how_to_close="重录一次探查并走到提交（可以排一个几天后的时刻，"
                     "验证完再去取消）。",
    ),
    Gap(
        key="composer_success_signal",
        step="G6 提交",
        why_missing="同上，没走到提交，也就没有成功后的 toast/跳转/列表项。",
        blocks="G6：没有明确成功信号就只能"
               "「点完就当成功」，那等于没有验收（PUBLISH_PLAN 3.2 第 7 点）。",
        how_to_close="补录，并在 --fill-notes 的 success_signal 里写清楚"
                     "看到的是什么。",
    ),
    Gap(
        key="planner_scheduled_card",
        step="G6c 排期回读",
        why_missing="旧 probe 在提交前中断，也没有停留在提交后的 Planner/内容日历，"
                    "所以没有排期卡片的被动语义证据。",
        blocks="G6c：无法用目标时刻、最终正文与 FB/IG 渠道回读远端排期；"
               "出现成功 toast 也只能记 submitted_unverified，不能记 scheduled。",
        how_to_close="用 v2 probe 手工完成一次未来排期提交；成功后进入内容日历，"
                     "先录到 Planner 数据就绪语义，再让同一张卡片以相互独立的"
                     "子语义显示目标时刻、最终正文、FB、IG 与 5 张图/图片数；"
                     "保持最终页面不动，再回终端停止录制。",
    ),
    Gap(
        key="composer_account_context",
        step="G2 账号核对",
        why_missing="composer 界面上没录到主页切换器，也没录到任何"
                    "「当前发的是哪个 Page / 哪个 IG 帐号」的控件。",
        blocks="G2 的生产账号安全闸：显式 `--submit` 必须在点击前证明当前上下文"
               "同时对应配置中的 FB Page 与 IG 帐号；缺证据时在浏览器前失败闭合。",
        how_to_close="补录时在 composer 里点一下主页/帐号那一块，"
                     "让它的 role 与可访问名进 dump。",
    ),
    Gap(
        key="ui_timezone",
        step="G5 排期",
        why_missing="dump 的观察项整段是空的（那一轮被打断，收尾问答没跑完）。"
                    "⚠️ **但答案本身已经有了**：用户 2026-09-01 实测确认，"
                    "Business Suite 上那个时刻**跟发帖者设备的本机时间走**，"
                    "已据此填好 [publish].ui_timezone。",
        blocks="严格 UI 约束（显式 `--submit` 会强制开启）：必须核对 dump 里"
               "的时区与实测排期/IG 上限，配置占位值不能放行真实提交。",
        how_to_close=r"准备自动提交前跑 `.venv\Scripts\python.exe "
                     r"tools\probe_publish.py --fill-notes "
                     r"state\publish_probe_<时间戳>.json`，"
                     "把同一个 IANA 名和其余实测 UI 限制填进观察项（不用重录）。",
    ),
)

GAPS: dict[str, Gap] = {item.key: item for item in _GAPS}


def describe_gap(key: str) -> str:
    """把一条缺口渲染成可以直接丢进异常消息的文本。"""
    gap = GAPS[key]
    return ("G1 尚未录到「%s」（%s）。\n"
            "  为什么没有：%s\n"
            "  它挡住了：%s\n"
            "  怎么补上：%s\n"
            "  ⛔ 不得凭截图或经验猜一个定位顶上（全局红线 5）。"
            % (gap.key, gap.step, gap.why_missing, gap.blocks, gap.how_to_close))


# =============================================================================
# 四、生产证据的装载点
# =============================================================================
#
# `tools/probe_signals.py --emit` 从一份 v2 dump **机械推导**出 G6/G6c 需要的
# 五条证据、逐条丢回 `evidence.py` 回查、并验证同页因果顺序之后，
# 才会写出 `publish/signals_backfilled.py`。这里把它并进三张表。
#
# ⚠️ **装载不等于放行。** `business_suite.require_*_evidence()` 在真正提交前
# 还会**再回查一遍**（用的就是 evidence.py 那套代码），并且要求
# `[publish].ui_probe_dump` 指向同一份 dump —— 那个配置项是人工审核的签字栏，
# 程序不替人填。所以即使有人手工造一个 signals_backfilled.py 塞进来，
# 只要它回查不过或来源对不上，`--submit` 依然会在碰浏览器之前失败闭合。
#
# 文件不存在 = 还没录到干净的 dump = 三张表保持现状 = 生产闸保持关闭。
try:
    from publish import signals_backfilled as _backfilled
except ImportError:                               # 正常状态：还没有生产证据
    pass
else:
    REGISTRY.update(_backfilled.LOCATORS)
    COMPOSER.update(_backfilled.LOCATORS)
    SIGNALS.update(_backfilled.SIGNALS)
