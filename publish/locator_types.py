"""定位与证据的数据类型；独立于注册表，供生成文件复用。"""
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
    """携带步骤、来源 dump、交互序号及失效条件的定位。"""

    key: str
    step: str
    surface: str
    role: str
    name: str
    # 可访问名来源；visible-text 会随界面语言变化。
    name_source: str
    source_dump: str
    sequences: tuple[int, ...]
    breaks_when: str
    # 相对录证的推广及依据；原样抄录时留空。
    inferred: str = ""
    #: 该元素在 dump 里还带着的其它稳定属性，回查与排障用。
    attributes: dict[str, str] = field(default_factory=dict)
    #: ``interaction`` = 来自用户可信事件；``snapshot`` = 来自 v2 被动语义快照。
    evidence_kind: str = "interaction"

    def match(self) -> tuple[str, bool]:
        """返回 (name, exact)；零宽空格名称退为子串匹配。"""
        cleaned = self.name.replace(ZERO_WIDTH_SPACE, "").strip()
        return (cleaned, False) if cleaned != self.name else (self.name, True)


@dataclass(frozen=True)
class EvidenceSignal:
    """可回查的信号：semantic 使用 role/name，url 使用去查询参数的 URL 前缀。"""

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
    """尚缺录证的控件及其阻塞范围。"""

    key: str
    step: str
    #: 为什么这一份 dump 里没有它（不是"忘了"，多半是结构性原因）。
    why_missing: str
    #: 它挡住了哪一步。
    blocks: str
    #: 怎么补上。要么补录，要么运行时发现并回读校验。
    how_to_close: str

