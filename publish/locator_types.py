r"""Business Suite 定位的**词汇表**：三个数据类 + 界面常量 + 名值归一化。

这里只有"一条定位长什么样"，没有"有哪些定位"。两者分开是为了打断一条环：

    selectors.py  ──尾部 try-import──▶  signals_backfilled.py
         ▲                                       │
         └───────── from publish.selectors ──────┘

`signals_backfilled.py` 是 `tools/_scaffolding/probe_signals.py --emit` 机械
生成的**数据**文件，它只需要 :class:`Locator` / :class:`EvidenceSignal` 两个
形状，却因此要 import 那个正在加载它的注册表。能跑，是因为 selectors.py 把
三个类定义在文件上半部分 —— **文件内的定义顺序成了承重结构**，谁把类挪到
文件末尾谁就炸。

所以词汇表下沉到这里：生成的数据文件依赖形状，注册表也依赖形状，
但**数据文件不再依赖注册表**。
"""
import re
from dataclasses import dataclass, field

# Business Suite 到处塞零宽空格（可访问名里真实存在，不是抄错了）。
ZERO_WIDTH_SPACE = "​"


def normalize_account_value(value: str) -> str:
    """账号/主页值的精确比较口径；IG 可访问名常带一个前导 @。"""
    return " ".join(str(value or "").split()).lstrip("@").casefold()


def account_value_from_text(rendered: str, pattern: str) -> str | None:
    """从完整独立语义值提取 account 命名组；禁止前缀子串命中。"""
    try:
        match = re.fullmatch(pattern, " ".join(str(rendered or "").split()))
    except re.error:
        return None
    if match is None or not match.groupdict().get("account"):
        return None
    return str(match.group("account"))

# ---- 界面（surface）。两份 dump 是两个界面，定位不通用 ----------------------
SURFACE_COMPOSER = "business.facebook.com/latest/composer"
SURFACE_DASHBOARD = "www.facebook.com/professional_dashboard"

COMPOSER_DUMP = "publish_probe_20260831_222850_347719.json"
DASHBOARD_DUMP = "publish_probe_20260831_195237_008047.json"

# dump 里原样出现过的两个 URL（recorder 会去掉 query/fragment）。
CONTENT_CALENDAR_URL = "https://business.facebook.com/latest/content_calendar"
COMPOSER_URL = "https://business.facebook.com/latest/composer/"


@dataclass(frozen=True)
class Locator:
    """一条**有据可查**的定位。

    字段的意义就是 PUBLISH_PLAN 第 3.4 节那条【验收】的三问：
    对应哪一步（``step``）、从哪份 dump 得来（``source_dump`` / ``sequences``）、
    什么信号说明它失效了（``breaks_when``）。
    """

    key: str
    step: str
    surface: str
    role: str
    name: str
    #: dump 里 ``accessible_name_source`` 的原值：aria-label / visible-text /
    #: aria-labelledby。**visible-text 来源的名字最脆**——它跟着界面语言走，
    #: 发布账号一旦切到德语界面就会全部失配（见 breaks_when）。
    name_source: str
    source_dump: str
    sequences: tuple[int, ...]
    breaks_when: str
    #: 相对 dump 做过的推广，没有就留空。**有值就等于"这一条不是纯抄录"**，
    #: 必须在这里说清推广了什么、凭什么。
    inferred: str = ""
    #: 该元素在 dump 里还带着的其它稳定属性，回查与排障用。
    attributes: dict[str, str] = field(default_factory=dict)
    #: ``interaction`` = 来自用户可信事件；``snapshot`` = 来自 v2 被动语义快照。
    evidence_kind: str = "interaction"

    def match(self) -> tuple[str, bool]:
        """返回 ``get_by_role(name=…, exact=…)`` 要用的 (字符串, 是否精确)。

        可访问名里带零宽空格时**自动退成子串匹配**：Playwright 的名字比较
        只归一化常规空白，``​`` 会被原样留下，精确匹配必然失配。
        """
        cleaned = self.name.replace(ZERO_WIDTH_SPACE, "").strip()
        return (cleaned, False) if cleaned != self.name else (self.name, True)


@dataclass(frozen=True)
class EvidenceSignal:
    """提交后成功/日历回读信号；同样必须能逐条回查到 v2 dump。

    ``kind=semantic`` 使用 role + name；``kind=url`` 使用已经去掉查询参数的
    URL 前缀。当前表故意为空：用户还没有提供新版完整提交 dump。
    """

    key: str
    step: str
    kind: str
    surface: str
    source_dump: str
    sequences: tuple[int, ...]
    breaks_when: str
    role: str = ""
    name: str = ""
    url_prefix: str = ""
    attributes: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in {"semantic", "url"}:
            raise ValueError("未知证据信号类型：%r" % self.kind)
        if self.kind == "semantic" and not (self.role and self.name):
            raise ValueError("semantic 信号必须同时给 role/name")
        if self.kind == "url" and not self.url_prefix:
            raise ValueError("url 信号必须给 url_prefix")


@dataclass(frozen=True)
class Gap:
    """G1 **没有**录到的东西。

    这个类存在的唯一理由：让"还没测出来"变成代码里**看得见、拦得住**的东西。
    这个项目已经为同型失效吃过亏——"没跑"和"跑了但没事做"输出上一模一样。
    """

    key: str
    step: str
    #: 为什么这一份 dump 里没有它（不是"忘了"，多半是结构性原因）。
    why_missing: str
    #: 它挡住了哪一步。
    blocks: str
    #: 怎么补上。要么补录，要么运行时发现并回读校验。
    how_to_close: str

