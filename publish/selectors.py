"""Business Suite 定位注册表；按真实录证保存 role/name、来源与失效条件，不跨界面复用。

⛔ **红线 5：不得凭猜测编写 Business Suite 的选择器。** 每一条都必须能在真实
probe dump 里逐字找到，并带上出自哪份 dump、第几条交互的来源注释。
**不要因为"看截图就知道那个按钮叫什么"而把缺的项填上** —— 截图不是 dump，
看着填进来的定位会给下一个人一个危险的假绿灯。

只用 ARIA role + 可访问名，不记 class、不记 CSS path：Business Suite 是 React
SPA，class 是构建期混淆的，下次发版就全废。缺的项在 GAPS 里逐条点名。
"""
from __future__ import annotations

from publish.locator_types import (COMPOSER_DUMP, COMPOSER_URL,
                                   CONTENT_CALENDAR_URL, DASHBOARD_DUMP,
                                   SURFACE_COMPOSER, SURFACE_DASHBOARD,
                                   ZERO_WIDTH_SPACE, EvidenceSignal, Gap,
                                   Locator, account_value_from_text,
                                   normalize_account_value)


# Composer

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
            # 富文本 combobox 非 textarea；写入后须核验换行与完整正文。
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
            # UI 日期为 mm/dd/yyyy。
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
            # 时间显示使用 12 小时制及 AM/PM。
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

# Professional dashboard 旁证，不用于 composer 定位。

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

# 只装载可回查的信号；空表保持提交阻塞。
SIGNALS: dict[str, EvidenceSignal] = {}


# 缺失控件

_GAPS = (
    Gap(
        key="composer_file_input",
        step="G3 上传",
        why_missing="录证未包含编辑器文件输入控件。",
        blocks="缺少文件输入控件的静态定位。",
        how_to_close="打开上传入口后查找接受图片的 file input，缺失即失败。",
    ),
    Gap(
        key="composer_hours_spinbutton",
        step="G5 排期",
        why_missing="小时字段未录到独立名称。",
        blocks="不能按名称定位小时字段。",
        how_to_close="仅在恰有三个 spinbutton 时排除已知分钟与 AM/PM，设值后完整回读。",
    ),
    Gap(
        key="composer_upload_thumbnails",
        step="G3 上传",
        why_missing="录证未包含上传后的缩略图。",
        blocks="无法证明编辑器已接收的图片数量和顺序。",
        how_to_close="按操作指南录取多图容器及顺序；远端排期图片另行核验。",
    ),
    Gap(
        key="composer_placement_toggles",
        step="G1 第 4 点",
        why_missing="该份录证不含渠道勾选控件。",
        blocks="不能据默认勾选推定单渠道正确。",
        how_to_close="使用 tools/probe_channels.py 录取控件，并在提交前核验唯一渠道。",
    ),
    Gap(
        key="composer_submit_button",
        step="G6 提交",
        why_missing="录证未覆盖提交控件。",
        blocks="提交信号缺失时保持阻塞。",
        how_to_close="按操作指南准备并确认具体内容，再录取受控提交。",
    ),
    Gap(
        key="composer_success_signal",
        step="G6 提交",
        why_missing="录证未包含提交后的成功信号。",
        blocks="仅点击按钮不能证明创建成功。",
        how_to_close="补录成功后的语义，并在 --fill-notes 中填写 success_signal。",
    ),
    Gap(
        key="planner_scheduled_card",
        step="G6c 排期回读",
        why_missing="录证未覆盖已排期任务详情。",
        blocks="缺少完整回读时只记 submitted_unverified。",
        how_to_close="优先只读录取已有任务的时刻、全文、渠道、图片及来源；需新增样本时先确认具体内容。",
    ),
    Gap(
        key="composer_account_context",
        step="G2 账号核对",
        why_missing="编辑器中未取得目标账号证据。",
        blocks="不能证明提交上下文属于配置账号。",
        how_to_close="录取账号控件的 role 与名称，并按任务渠道核验目标。",
    ),
    Gap(
        key="ui_timezone",
        step="G5 排期",
        why_missing="录证未填写设备时区及日期控件约束。",
        blocks="配置值不能替代人工核验的 UI 证据。",
        how_to_close="运行 scripts/run_python.bat -m tools._scaffolding.probe_publish --fill-notes <dump>，填写 IANA 时区及实际控件限制。",
    ),
)

GAPS: dict[str, Gap] = {item.key: item for item in _GAPS}


def describe_gap(key: str) -> str:
    """把一条缺口渲染成可以直接丢进异常消息的文本。"""
    gap = GAPS[key]
    return ("缺少控件证据：%s（%s）。\n原因：%s\n影响：%s\n处理：%s"
            % (gap.key, gap.step, gap.why_missing, gap.blocks, gap.how_to_close))


# 装载生成信号；提交前仍须回查并与配置中的人工复核 dump 一致。
try:
    from publish import signals_backfilled as _backfilled
except ImportError:                               # 正常状态：还没有验收证据
    pass
else:
    REGISTRY.update(_backfilled.LOCATORS)
    COMPOSER.update(_backfilled.LOCATORS)
    SIGNALS.update(_backfilled.SIGNALS)
