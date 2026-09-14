r"""从一份 v2 probe dump 里**机械推导** G6/G6c 验收证据，回查通过后才落盘。

存在的理由只有一条：**把「录完 dump → 发布校验打开」这一段从"再开一次开发会话"
变成"跑一条命令"。**

在此之前这条缝是这样的：用户录 20 分钟 dump → 交给一个 Agent 人工读 →
手写五条 dataclass → 希望它没写错。这个项目已经为同型的手工环节吃过两次亏
（63 条那份录错了界面、24 条那份录到一半被打断），
而两次都是**录完之后**才发现的。

本工具做三件事，一件都不越过红线 5：

``--check``
    只读体检。先跑 :func:`publish.evidence.validate_v2_dump` 的完整 v2 契约，
    再逐项报告六件验收证据**能不能从这份 dump 推导出来**，
    推不出来时直接说"缺哪一块、补录时要做什么"。零写盘。
    **这是录完之后第一件该跑的命令**——30 秒就知道这次录制成不成立，
    不用等到 `--submit` 才发现。

``--emit``
    推导 → **逐条丢回 evidence.verify_signal / verify_publish_chain 回查** →
    全部通过才写 ``publish/signals_backfilled.py``。
    任一条回查不过就整体拒绝落盘，退出码非 0。

``--status``
    当前发布校验是开是关，关着的话差哪一条。

⛔ **这不是"让程序猜选择器"。** 每一个字段的值都是从 dump 里**抄**出来的：
role、可访问名、容器归属、日期格式全部来自被动语义快照，
regex 是拿一张候选表**逐个试到能解析为止**，试不出来就报缺口。
推导完还要原路回查一遍——回查用的是 `evidence.py`，
和 `--submit` 上发布校验用的是同一套代码。**编出来的值会当场被打回。**
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.console import force_utf8                          # noqa: E402
from publish import evidence                                 # noqa: E402
from publish.selectors import (SURFACE_COMPOSER, EvidenceSignal,  # noqa: E402
                               Locator, account_value_from_text,
                               normalize_account_value)

SURFACE_PLANNER = "business.facebook.com/latest/content_calendar"

#: 生成文件的落点。selectors.py 末尾会在它存在时把内容并进三张表。
GENERATED = ROOT / "publish" / "signals_backfilled.py"

# 日期/时间候选表。**逐个试到能 strptime 为止**，不是挑一个像的。
_DATE_FORMS = (
    (r"\d{1,2}/\d{1,2}/\d{4}", "%m/%d/%Y"),
    (r"\d{1,2}\.\d{1,2}\.\d{4}", "%d.%m.%Y"),
    (r"\d{4}-\d{1,2}-\d{1,2}", "%Y-%m-%d"),
    (r"\d{1,2}/\d{1,2}/\d{2}", "%m/%d/%y"),
    (r"\d{1,2}\.\d{1,2}\.\d{2}", "%d.%m.%y"),
    # 实测 Planner 用的是英文长月份名：'September 15, 2026, 10:00 AM'
    # 和详情弹窗里的 'September 15 at 10:00 AM'（后者没有年份）。
    (r"[A-Z][a-z]{2,8} \d{1,2}, \d{4}", "%B %d, %Y"),
    (r"[A-Z][a-z]{2} \d{1,2}, \d{4}", "%b %d, %Y"),
)
_TIME_FORMS = (
    (r"\d{1,2}:\d{2} [AaPp][Mm]", "%I:%M %p"),
    (r"\d{1,2}:\d{2}[AaPp][Mm]", "%I:%M%p"),
    (r"\d{1,2}:\d{2}", "%H:%M"),
)

#: 成功信号优先看的 role。status/alert 是 aria-live 区域，最稳。
_SIGNAL_ROLES = ("status", "alert", "alertdialog", "heading", "banner")

#: 容器名可能是 500 字的整段可见文本，取前缀当定位名。
_NAME_PREFIX_CHARS = 60


def _norm(value: object) -> str:
    """Playwright 与 evidence 的比较口径：折叠所有空白。"""
    return " ".join(str(value or "").split())


def _row_text(row: dict) -> str:
    """与 :func:`publish.evidence._row_text` 逐字一致的口径。"""
    return "\n".join(str(row.get(key) or "") for key in
                     ("accessible_name", "visible_text"))


def _row_values(row: dict) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        str(row.get(key) or "") for key in ("accessible_name", "visible_text")
        if str(row.get(key) or "")))


def _items(snapshot: dict) -> list[dict]:
    return [row for row in (snapshot.get("semantic_items") or [])
            if isinstance(row, dict)]


def _on(snapshot: dict, surface: str) -> bool:
    return bool(surface and surface in str(snapshot.get("page_url") or ""))


def _short_name(value: str) -> str:
    """把一段长文本裁成可以当定位名的前缀（按词边界裁）。"""
    text = _norm(value)
    if len(text) <= _NAME_PREFIX_CHARS:
        return text
    cut = text[:_NAME_PREFIX_CHARS]
    space = cut.rfind(" ")
    return (cut[:space] if space > _NAME_PREFIX_CHARS // 2 else cut).strip()


def _stable_label(row: dict, variables) -> str:
    """挖掉随帖子/日期变化的部分，返回剩下最长的一段**固定**文字。

    ⚠️ **这一步不是好看，是正确性。** 这些名字最后会变成运行时的
    ``get_by_role(name=…, exact=False)``。如果直接把整条渲染文本当名字，
    「Scheduled for 09/08/2026 10:00 AM」下个月就一张卡片都找不到——
    而 `_planner_cards` 找不到卡片时会把结果读成"远端零占用"。
    所以宁可只留「Scheduled for」这半句。

    带数字的片段一律丢弃：数字几乎一定是日期、时刻或张数。
    返回空串表示这一格上**没有**随内容变化之外的固定文字，由调用方决定
    是退回整条文本并打醒目警告，还是直接判缺口。
    """
    raw = _row_text(row)
    marked = _norm(raw)
    for value in variables:
        text = _norm(value)
        if text:
            marked = marked.replace(text, "\x00")
    parts = [_norm(part) for part in marked.split("\x00")]
    # 至少两个字母：不然像「 - 」这种标点残渣会被当成"固定文字"，
    # 而它命中页面上一半的元素，唯一性检查会当场把整条证据判掉。
    parts = [part for part in parts
             if len(re.findall(r"[^\W\d_]", part)) >= 2
             and not re.search(r"\d", part) and part in raw]
    return max(parts, key=len, default="")


# ---------------------------------------------------------------------------
# 账号语义：把「Facebook account Neakasa Deutschland」拆成 前缀 + (?P<account>)
# ---------------------------------------------------------------------------

def _account_label(text: str, token: str) -> str | None:
    """返回账号值**之前**的那段固定标签；找不到 token 就返回 None。"""
    haystack = _norm(text)
    needle = _norm(token)
    if not needle:
        return None
    lowered, target = haystack.casefold(), needle.casefold()
    at = lowered.rfind(target)
    if at < 0:
        return None
    return haystack[:at].rstrip().rstrip("@").rstrip(":").rstrip()


def _account_regex(label: str) -> str:
    """标签 + 可选 @ + account 组。必须能 fullmatch 整条归一化文本。"""
    if not label:
        return r"@?(?P<account>.+)"
    return re.escape(label) + r"\s*:?\s*@?(?P<account>.+)"


def _account_row_ok(row: dict, pattern: str, token: str) -> bool:
    """所有可见值都必须解析出同一个账号——和 evidence 的判据一致。"""
    extracted = [account_value_from_text(value, pattern)
                 for value in _row_values(row)]
    extracted = [value for value in extracted if value is not None]
    if not extracted:
        return False
    expected = normalize_account_value(token)
    return all(normalize_account_value(value) == expected
               for value in extracted)


def _derive_account_child(children: list[dict], token: str
                          ) -> tuple[dict, str, str] | None:
    """在一组子元素里找出承载 token 的那条，返回 (行, 定位名, regex)。"""
    for row in children:
        label = _account_label(_norm(row.get("accessible_name")
                                     or row.get("visible_text")), token)
        if label is None:
            continue
        pattern = _account_regex(label)
        if not _account_row_ok(row, pattern, token):
            continue
        # 定位名要能命中这一行；标签为空时退回用 token 本身。
        name = label or _norm(token)
        if name not in _row_text(row):
            continue
        return row, name, pattern
    return None


# ---------------------------------------------------------------------------
# 容器分组
# ---------------------------------------------------------------------------

@dataclass
class Container:
    role: str
    name: str
    children: list[dict] = field(default_factory=list)


def _containers(snapshot: dict) -> list[Container]:
    """按 (container_role, container_accessible_name) 把语义项分组。"""
    groups: dict[tuple[str, str], Container] = {}
    for row in _items(snapshot):
        role = str(row.get("container_role") or "")
        name = str(row.get("container_accessible_name") or "")
        if not role or not name:
            continue
        key = (role, name)
        if key not in groups:
            groups[key] = Container(role=role, name=name)
        groups[key].children.append(row)
    return list(groups.values())


def _container_is_addressable(snapshot: dict, container: Container,
                              name: str) -> bool:
    """容器自己也必须作为一条语义项存在，否则 _signal_hits 的 rows 为空。"""
    return any(str(row.get("role") or "") == container.role
               and name in _row_text(row) for row in _items(snapshot))


# ---------------------------------------------------------------------------
# 六件证据的推导
# ---------------------------------------------------------------------------

@dataclass
class Derived:
    key: str
    ok: bool
    detail: str
    spec: object = None
    #: 推不出来时告诉用户补录要做什么。
    how: str = ""
    #: 推出来了但有脆弱之处（多半是"这一格上只有会变的文字"）。
    #: **不拦，但必须显眼地说出来**——这类失效是延迟的，而且只在下个月出现。
    warnings: tuple[str, ...] = ()


def derive_account_context(data: dict, dump: str, facebook: str,
                           instagram: str) -> Derived:
    """composer 上证明"这条会发到目标 FB 主页"的那条语义。

    ⚠️ **只找 FB。** 实测 composer 上没有 IG 帐号名（findings 第一节），
    IG 由提交后的 Planner 弹窗回读证明。
    """
    for snapshot in data["snapshots"]:
        if not _on(snapshot, SURFACE_COMPOSER):
            continue
        for row in _items(snapshot):
            label = _account_label(
                _norm(row.get("accessible_name") or row.get("visible_text")),
                facebook)
            if label is None:
                continue
            pattern = _account_regex(label)
            if not _account_row_ok(row, pattern, facebook):
                continue
            name = label or _norm(facebook)
            if name not in _row_text(row):
                continue
            spec = EvidenceSignal(
                key="composer_account_context",
                step="G2 目标主页核对（推导自 v2 被动语义）",
                kind="semantic", surface=SURFACE_COMPOSER,
                source_dump=dump, sequences=(int(snapshot["sequence"]),),
                breaks_when="composer 预览不再显示目标主页显示名；"
                            "或主页改名（那时应改 [publish].facebook_page_name）",
                role=str(row.get("role") or ""), name=name,
                attributes={
                    "facebook_account_token": facebook,
                    "facebook_account_regex": pattern,
                    "instagram_not_provable_here":
                        "composer 上不存在 IG 帐号名；IG 由 G6c 回读证明",
                })
            return Derived("composer_account_context", True,
                           "第 %d 条快照 · %s/%r"
                           % (snapshot["sequence"], spec.role, name), spec,
                           warnings=(
                               "composer 上没有 IG 帐号名（实测），"
                               "所以提交前只能证明 FB。IG 少了会在 G6c 回读时"
                               "被抓到并转人工，不会误记 scheduled。",))
    return Derived(
        "composer_account_context", False,
        "composer 页面上找不到目标 FB 主页显示名 %r" % facebook,
        how="补录时让 composer 的 Facebook 预览渲染出来（正文填几个字即可），"
            "预览抬头那行就是主页显示名。若显示名本身变了，"
            "改 [publish].facebook_page_name。")


#: 定时提交按钮的常见标签。**这不是选择器，是候选排序的先验**——
#: 值仍然只能从 dump 里取，选完还要过 `verify_publish_chain` 的因果顺序。
#: ⚠️ `Publish` 排在 `Schedule` 后面是有意的：两个按钮同时存在，
#: 点错就是**立即发布**而不是定时（实测 composer 上两个都在）。
_SUBMIT_WORDS = ("schedule", "planen", "einplanen", "publish",
                 "veröffentlichen", "post", "share")
#: 提交成功提示的常见措辞。
_SUCCESS_WORDS = ("scheduled", "geplant", "eingeplant", "published",
                  "veröffentlicht", "success", "erfolgreich")


#: 点提交按钮之外、role 上就不像按钮的，排在后面。
_BUTTON_ROLES = {"button", "menuitem", "link", "tab"}


def _word_rank(text: str, words: tuple[str, ...]) -> int:
    """按整词命中排序。

    ⚠️ **必须按整词。** 实测踩过：子串匹配时 `schedule` 命中了成功提示
    「Your post is **scheduled**」，于是"提交按钮"被推成了那条 toast 本身。
    """
    lowered = text.casefold()
    for index, word in enumerate(words):
        if re.search(r"\b%s\b" % re.escape(word), lowered):
            return index
    return len(words)


def _success_candidates(data: dict, account_seq: int) -> list[dict]:
    """提交后才出现、提交前没有的 status/alert 语义。

    "提交前没有"这条不是好看而已：:func:`evidence.verify_publish_chain` 要求
    成功信号**不能**在账号快照里就已经命中，否则它证明不了是这一次提交造成的。
    """
    account_order = int(
        _by_seq(data["snapshots"])[account_seq].get("evidence_order") or 0)
    before = {(str(row.get("role") or ""), _norm(row.get("accessible_name")))
              for snapshot in data["snapshots"]
              if int(snapshot.get("evidence_order") or 0) <= account_order
              for row in _items(snapshot)}
    seen: dict[tuple[str, str], dict] = {}
    for snapshot in data["snapshots"]:
        if int(snapshot.get("evidence_order") or 0) <= account_order:
            continue
        for row in _items(snapshot):
            role = str(row.get("role") or "")
            if role not in _SIGNAL_ROLES:
                continue
            name = _norm(row.get("accessible_name") or row.get("visible_text"))
            if not name or (role, name) in before or (role, name) in seen:
                continue
            seen[(role, name)] = {
                "role": role, "name": _short_name(name),
                "sequence": int(snapshot["sequence"]),
                "surface": (SURFACE_PLANNER if _on(snapshot, SURFACE_PLANNER)
                            else SURFACE_COMPOSER),
                "order": int(snapshot.get("evidence_order") or 0),
            }
    rank = {role: index for index, role in enumerate(_SIGNAL_ROLES)}
    return sorted(seen.values(),
                  key=lambda row: (rank.get(row["role"], 99), row["order"]))


def _by_seq(rows: list[dict]) -> dict[int, dict]:
    return {int(row["sequence"]): row for row in rows
            if isinstance(row, dict) and isinstance(row.get("sequence"), int)}


def derive_success_signal(data: dict, dump: str, account: EvidenceSignal,
                          pick: str = "", submit_order: int = 0) -> Derived:
    """提交成功信号：**点击之后**才出现的那条提示。

    ⚠️ 只按"账号快照之后出现"筛是不够的 —— 实测那样会选中填正文期间
    一闪而过的 `status 'Loading…'`。必须以**提交点击**为界。
    """
    candidates = [row for row in _success_candidates(data, account.sequences[0])
                  if row["order"] > submit_order]
    if pick:
        candidates = [row for row in candidates
                      if pick.casefold() in row["name"].casefold()]
    if not candidates:
        return Derived(
            "composer_success_signal", False,
            "提交点击之后没有出现任何新的 status/alert/heading 语义",
            how="补录时提交之后**不要马上跳走**，让成功提示（toast / "
                "「Your post is scheduled」之类）在页面上停两秒，"
                "被动快照才采得到。")
    # 措辞命中优先 → 名字短的优先（长名字多半是整段弹窗文本）→ 早的优先。
    candidates.sort(key=lambda row: (_word_rank(row["name"], _SUCCESS_WORDS),
                                     len(row["name"]), row["order"]))
    best = candidates[0]
    spec = EvidenceSignal(
        key="composer_success_signal",
        step="G6 提交成功（推导自 v2 被动语义）",
        kind="semantic", surface=best["surface"],
        source_dump=dump, sequences=(best["sequence"],),
        breaks_when="成功提示改文案、改 role，或不再经过 aria-live 区域宣告",
        role=best["role"], name=best["name"])
    others = "；其余候选：" + "、".join(
        "%s/%r" % (row["role"], row["name"]) for row in candidates[1:4]
    ) if len(candidates) > 1 else ""
    return Derived("composer_success_signal", True,
                   "第 %d 条快照 · %s/%r%s"
                   % (best["sequence"], best["role"], best["name"], others),
                   spec)


def _named_candidate(interaction: dict) -> dict | None:
    """取这次点击里**带真实 role 且有可访问名**的那个候选。

    ⚠️ 候选表的第 0 条常常是最内层那个 div，``role`` 是空的。
    空 role 能通过 dump 回查（`_role_matches` 对空 role 直接放行），
    但运行时 `page.get_by_role("", …)` 是废的 —— 所以带真实 role 的优先。
    """
    named = [row for row in evidence._candidates(interaction)
             if _norm(row.get("accessible_name") or row.get("aria_label")
                      or row.get("visible_text"))]
    named.sort(key=lambda row: 0 if str(
        row.get("role") or row.get("explicit_role") or "") not in
        {"", "none", "presentation"} else 1)
    return named[0] if named else None


def derive_submit_button(data: dict, dump: str,
                         account: EvidenceSignal) -> Derived:
    """账号上下文之后、composer 上那次**提交**点击。

    ⚠️ 不能简单取"最后一次点击"：实测提交之后用户还点了成功提示本身、
    又点了关闭按钮，两次都在 composer 上。所以按标签措辞排序
    （`Schedule` 优先于 `Publish` —— 两个按钮同时存在，点错就是立即发布），
    措辞都不命中时才退回最后一次带名字的点击并打警告。
    """
    snapshots = _by_seq(data["snapshots"])
    low = int(snapshots[account.sequences[0]].get("evidence_order") or 0)
    clicks = []
    for row in data["interactions"]:
        if row.get("is_trusted") is not True:
            continue
        if row.get("event_type") not in {"click", "submit"}:
            continue
        if not _on(row, SURFACE_COMPOSER):
            continue
        if int(row.get("evidence_order") or 0) <= low:
            continue
        target = _named_candidate(row)
        if target is None:
            continue
        clicks.append((row, target, _short_name(
            target.get("accessible_name") or target.get("aria_label")
            or target.get("visible_text"))))
    if not clicks:
        return Derived(
            "composer_submit_button", False,
            "账号上下文之后没有 composer 上带可访问名的可信点击",
            how="补录时提交那一下必须是**人真的用鼠标点按钮本体**的（可信事件）；"
                "不要点它外面那层包着正文编辑器的容器——祖先文本会被整段抹掉，"
                "也不要用开发者工具触发。")
    ranked = sorted(clicks, key=lambda item: (
        _word_rank(item[2], _SUBMIT_WORDS),
        0 if str(item[1].get("role") or item[1].get("explicit_role") or "")
        in _BUTTON_ROLES else 1,
        len(item[2]),
        -int(item[0].get("evidence_order") or 0)))
    chosen, target, name = ranked[0]
    warnings: tuple[str, ...] = ()
    if _word_rank(name, _SUBMIT_WORDS) == len(_SUBMIT_WORDS):
        warnings = ("没有一次点击的标签像提交按钮（Schedule/Publish/…），"
                    "只能退回最后一次带名字的点击 %r。**提交前务必人工确认"
                    "这确实是那个按钮。**" % name,)
    role = str(target.get("role") or target.get("explicit_role") or "")
    spec = Locator(
        key="composer_submit_button",
        step="G6 提交（推导自 v2 可信交互）",
        surface=SURFACE_COMPOSER, role=role, name=name,
        name_source=("aria-label" if target.get("aria_label") else "visible-text"),
        source_dump=dump, sequences=(int(chosen["sequence"]),),
        breaks_when="按钮改名（Schedule / Veröffentlichen 随界面语言走）"
                    "或 role 从 button 变成别的",
        inferred="按因果位置 + 标签措辞推导：账号上下文之后、composer 上"
                 "标签最像定时提交的那一次可信点击。",
        attributes={"tag": str(target.get("tag") or "")},
        evidence_kind="interaction")
    return Derived("composer_submit_button", True,
                   "第 %d 条交互 · %s/%r" % (chosen["sequence"], role, name),
                   spec, warnings=warnings)


def _datetime_regex(text: str) -> tuple[str, str, str] | None:
    """在一段文本里试出 (datetime_regex, date_format, time_format)。"""
    haystack = _norm(text)
    for date_re, date_fmt in _DATE_FORMS:
        for time_re, time_fmt in _TIME_FORMS:
            pattern = r"(?P<date>%s)\D{0,10}?(?P<time>%s)" % (date_re, time_re)
            match = re.search(pattern, haystack)
            if match is None:
                continue
            try:
                datetime.strptime(
                    "%s %s" % (match.group("date"), match.group("time")),
                    "%s %s" % (date_fmt, time_fmt))
            except ValueError:
                continue
            return pattern, date_fmt, time_fmt
    return None


#: 详情弹窗上区分渠道的固定文案（实测原样抄录，见 findings 第二节）。
_CHANNEL_MARKERS = {
    "facebook": ("Facebook's Feed", "Facebook feed"),
    "instagram": ("Instagram feed", "your Instagram feed"),
}
_REMOTE_ID_REGEX = r"ID:\s*(?P<remote_id>\d{6,})"
_MONTH_FORMATS = ("%B", "%b")


def _derive_entry(data: dict, caption_hint: str
                  ) -> tuple[dict, int, str, str, str, str] | None:
    """日历条目：同一条可访问名里同时带正文与可解析的完整时刻。

    返回 ``(行, 快照序号, role, 正文样本, datetime_regex, date_fmt\\x00time_fmt)``。
    """
    probe = _norm(caption_hint)[:60]
    for snapshot in data["snapshots"]:
        if not _on(snapshot, SURFACE_PLANNER):
            continue
        for row in _items(snapshot):
            text = _row_text(row)
            if not probe or probe not in _norm(text):
                continue
            built = _datetime_regex(text)
            if built is None:
                continue
            pattern, date_fmt, time_fmt = built
            attrs = {"datetime_regex": pattern, "date_format": date_fmt,
                     "time_format": time_fmt}
            # 用与运行时一模一样的判据（只指向一个时刻，不是只匹配一次）。
            if evidence.parse_entry_moment(text, attrs) is None:
                continue
            if probe not in text:
                continue
            return (row, int(snapshot["sequence"]), str(row.get("role") or ""),
                    probe, pattern, date_fmt + "\x00" + time_fmt)
    return None


def _derive_channel_dialog(data: dict, channel: str, token: str,
                           after_order: int
                           ) -> tuple[int, str, str, str] | None:
    """某渠道的详情弹窗；返回 ``(快照序号, dialog_role, dialog_name, marker)``。

    渠道标记必须**只属于这个渠道**：两个渠道的弹窗都含 "Post details"，
    靠的是 "Facebook's Feed" / "Instagram feed" 这半句区分开。
    """
    pattern = re.compile(_REMOTE_ID_REGEX)
    other = "instagram" if channel == "facebook" else "facebook"
    for snapshot in data["snapshots"]:
        if not _on(snapshot, SURFACE_PLANNER):
            continue
        if int(snapshot.get("evidence_order") or 0) < after_order:
            continue
        for row in _items(snapshot):
            if str(row.get("role") or "") != "dialog":
                continue
            text = _row_text(row)
            # 按**独立词**判：`neakasa.de` 是 `neakasa.deals` 的子串，
            # 子串判会把近碰撞账号读成目标账号。与运行时同一个判据。
            if not evidence.token_present(text, token):
                continue
            if pattern.search(text) is None:
                continue
            if any(alt in text for alt in _CHANNEL_MARKERS[other]):
                continue          # 同一串里两个渠道标记 —— 区分不开，不用它
            marker = next((word for word in _CHANNEL_MARKERS[channel]
                           if word in text), "")
            if not marker:
                continue
            name = _stable_label(row, [token]) or ""
            # 定位名要稳定（不含 ID、不含正文），取弹窗抬头那一句。
            name = "Post details" if "Post details" in text else name
            if not name or name not in text:
                continue
            return int(snapshot["sequence"]), "dialog", name, marker
    return None


def _derive_visible_month(data: dict, after_order: int
                          ) -> tuple[int, str, str, str, str] | None:
    """可见月份 + 年份两条 heading。返回 ``(序号, 月role, 月fmt, 年role, 年fmt)``。"""
    for snapshot in data["snapshots"]:
        if not _on(snapshot, SURFACE_PLANNER):
            continue
        if int(snapshot.get("evidence_order") or 0) < after_order:
            continue
        month = year = None
        for row in _items(snapshot):
            role = str(row.get("role") or "")
            # ⚠️ 逐个**单值**试，不能拼起来试：accessible_name 与 visible_text
            # 常常一模一样，拼完就是 "September September"，怎么都解析不出来。
            for text in (_norm(value) for value in _row_values(row)):
                if month is None:
                    for fmt in _MONTH_FORMATS:
                        try:
                            datetime.strptime(text, fmt)
                        except ValueError:
                            continue
                        month = (role, fmt)
                        break
                if year is None and re.fullmatch(r"\d{4}", text):
                    year = (role, "%Y")
        if month and year:
            return (int(snapshot["sequence"]), month[0], month[1],
                    year[0], year[1])
    return None


def derive_planner_card(data: dict, dump: str, facebook: str,
                        instagram: str, caption_hint: str = "",
                        after_order: int = 0) -> Derived:
    """真实 Planner 的排期证据：条目 + 两个渠道弹窗 + 可见月份。"""
    if not _norm(caption_hint):
        return Derived(
            "planner_scheduled_card", False,
            "没给 --caption：日历条目的可访问名就是正文本身，没有固定标签，"
            "不给一段正文就没法在几十条 link 里认出它",
            how="重跑时加上 --caption \"测试帖正文里的一小句\"。")
    entry = _derive_entry(data, caption_hint)
    if entry is None:
        return Derived(
            "planner_scheduled_card", False,
            "日历上没有一条语义同时带这段正文与可解析的完整时刻",
            how="补录时提交成功后进内容日历，**切到 Week 视图**并等数据渲染出来"
                "（月视图的条目只有时刻、没有正文，认不出是哪一篇）；"
                "让那条条目在屏幕上停两三秒。")
    row, entry_seq, entry_role, sample, datetime_regex, packed = entry
    date_fmt, time_fmt = packed.split("\x00")
    dialogs: dict[str, tuple[int, str, str, str]] = {}
    for channel, token in (("facebook", facebook), ("instagram", instagram)):
        found = _derive_channel_dialog(data, channel, token, after_order)
        if found is None:
            return Derived(
                "planner_scheduled_card", False,
                "找不到 %s 的详情弹窗（要能同时读到 %r 与 ID: <数字>）"
                % (channel, token),
                how="补录时在内容日历上把那条排期**两个渠道各点开一次**："
                    "点条目 → 弹出 Post details → 等预览加载完（账号名和正文"
                    "出现）→ 关掉 → 点同一时刻的另一条条目再来一次。"
                    "FB 与 IG 是两个独立对象，各有各的弹窗。")
        dialogs[channel] = found
    month = _derive_visible_month(data, after_order)
    if month is None:
        return Derived(
            "planner_scheduled_card", False,
            "提交之后的日历快照里读不到可解析的月份 + 年份 heading",
            how="补录时保持日历页面停留久一点，让月份标题渲染出来。")
    month_seq, month_role, month_fmt, year_role, year_fmt = month
    sequences = tuple(sorted({entry_seq, month_seq,
                              dialogs["facebook"][0],
                              dialogs["instagram"][0]}))
    spec = EvidenceSignal(
        key="planner_scheduled_card",
        step="G6c 排期回读（推导自 v2 被动语义）",
        kind="semantic", surface=SURFACE_PLANNER,
        source_dump=dump, sequences=sequences,
        breaks_when="日历条目不再把正文与完整时刻放在同一条可访问名里；"
                    "或详情弹窗不再显示 `ID: <数字>` 与渠道标记",
        role=entry_role, name=sample,
        attributes={
            "date_format": date_fmt,
            "time_format": time_fmt,
            "datetime_regex": datetime_regex,
            "entry_role": entry_role,
            "entry_probe_text": sample,
            "dialog_role": dialogs["facebook"][1],
            "dialog_name": dialogs["facebook"][2],
            "remote_id_regex": _REMOTE_ID_REGEX,
            "facebook_marker": dialogs["facebook"][3],
            "facebook_account_token": facebook,
            "instagram_marker": dialogs["instagram"][3],
            "instagram_account_token": instagram,
            "visible_month_role": month_role,
            "visible_month_format": month_fmt,
            "visible_year_role": year_role,
            "visible_year_format": year_fmt,
        })
    return Derived(
        "planner_scheduled_card", True,
        "条目第 %d 条快照 · %s/%r；FB 弹窗第 %d 条 · IG 弹窗第 %d 条 · "
        "月份第 %d 条" % (entry_seq, entry_role, sample,
                          dialogs["facebook"][0], dialogs["instagram"][0],
                          month_seq),
        spec,
        warnings=("entry_probe_text 用的是这次测试帖的正文片段，只是"
                  "**回查 dump** 用的样本；运行时回读比对的是当次要发的完整正文。",))


def derive_planner_loaded(data: dict, dump: str, card: EvidenceSignal,
                          after_order: int = 0) -> Derived:
    """Planner 数据已就绪的语义（区分"还在转圈"与"真的空"）。

    用**可见月份 heading**：它只在日历数据渲染完之后才出现，
    而且已经在卡片推导时按声明格式解析验证过。

    ⚠️ 月份名每月都变，所以这条的 `name` 是空的 —— 运行时按 role 全取回来
    再逐条试解析（`_visible_calendar_range`），不靠固定名字定位。
    但 `_wait_for_signal` 需要一个可等的名字，所以这里记的是**录制当月**
    那个名字：等不到它不会误判成"数据没就绪"，因为
    `_planner_cards` 在等待失败后仍会继续按 `entry_role` 枚举。
    """
    by = _by_seq(data["snapshots"])
    chosen = None
    for sequence in card.sequences:
        snapshot = by.get(sequence)
        if snapshot is None or not _on(snapshot, SURFACE_PLANNER):
            continue
        if int(snapshot.get("evidence_order") or 0) < after_order:
            continue
        for row in _items(snapshot):
            if str(row.get("role") or "") != card.attributes.get(
                    "visible_month_role"):
                continue
            # 逐个**单值**试；拼接后的 "September September" 解析不出来。
            for text in (_norm(value) for value in _row_values(row)):
                try:
                    datetime.strptime(
                        text, card.attributes["visible_month_format"])
                except (ValueError, TypeError):
                    continue
                chosen = (int(snapshot["sequence"]),
                          str(row.get("role") or ""), text)
                break
            if chosen:
                break
        if chosen:
            break
    if chosen is None:
        return Derived("planner_loaded_signal", False,
                       "排期证据里没有一张快照带可解析的月份 heading")
    sequence, role, name = chosen
    spec = EvidenceSignal(
        key="planner_loaded_signal",
        step="G6c Planner 数据就绪（推导自 v2 被动语义）",
        kind="semantic", surface=SURFACE_PLANNER,
        source_dump=dump, sequences=(sequence,),
        breaks_when="日历不再渲染月份标题，或它在数据到达前就出现了"
                    "（那样它证明不了'数据已就绪'）",
        role=role, name=name)
    return Derived("planner_loaded_signal", True,
                   "第 %d 条快照 · %s/%r" % (sequence, role, name), spec,
                   warnings=("月份名每月都变，这条只是'数据已就绪'的可等锚点；"
                             "真正的可见区间由 visible_month/year 在运行时"
                             "按格式解析得到，不依赖这个名字。",))


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------

REQUIRED = ("composer_account_context", "composer_success_signal",
            "composer_submit_button", "planner_scheduled_card",
            "planner_loaded_signal")


def derive_all(data: dict, dump: str, *, facebook: str, instagram: str,
               caption_hint: str = "", success_pick: str = ""
               ) -> list[Derived]:
    results: list[Derived] = []
    account = derive_account_context(data, dump, facebook, instagram)
    results.append(account)
    if not account.ok:
        for key in ("composer_submit_button", "composer_success_signal"):
            results.append(Derived(
                key, False, "账号上下文没推导出来，本条无法定位因果区间"))
    else:
        # 先定提交点击，再拿它当界找成功信号 —— 反过来会选中填正文期间
        # 一闪而过的 `status 'Loading…'`（实测踩过）。
        submit = derive_submit_button(data, dump, account.spec)
        results.append(submit)
        if submit.ok:
            order = int(_by_seq(data["interactions"])[
                submit.spec.sequences[0]].get("evidence_order") or 0)
            results.append(derive_success_signal(
                data, dump, account.spec, success_pick, submit_order=order))
        else:
            results.append(Derived(
                "composer_success_signal", False,
                "提交点击没推导出来，无法框定成功信号的因果区间"))
    # Planner 侧的证据必须晚于提交成功，否则它证明的是**上一次**的排期。
    success_spec = next((item.spec for item in results
                         if item.key == "composer_success_signal" and item.ok),
                        None)
    after = 0
    if success_spec is not None:
        row = _by_seq(data["snapshots"]).get(success_spec.sequences[0])
        after = int((row or {}).get("evidence_order") or 0)
    card = derive_planner_card(data, dump, facebook, instagram, caption_hint,
                               after_order=after)
    results.append(card)
    results.append(derive_planner_loaded(data, dump, card.spec, after)
                   if card.ok
                   else Derived("planner_loaded_signal", False,
                                "排期卡片没推导出来"))
    return results


def verify_derived(results: list[Derived], dumps_dir: Path
                   ) -> list[tuple[str, bool, str]]:
    """把推导结果原路丢回 evidence 回查。用的就是发布校验那套代码。"""
    out: list[tuple[str, bool, str]] = []
    specs = {item.key: item.spec for item in results if item.ok}
    for item in results:
        if not item.ok:
            out.append((item.key, False, item.detail))
            continue
        if isinstance(item.spec, Locator):
            passed, detail = evidence.verify(item.spec, dumps_dir)
        else:
            passed, detail = evidence.verify_signal(item.spec, dumps_dir)
        out.append((item.key, passed is True,
                    detail or "回查命中"))
    if all(flag for _, flag, _ in out) and len(specs) == len(REQUIRED):
        passed, detail = evidence.verify_publish_chain(
            specs["composer_submit_button"], specs["composer_account_context"],
            specs["composer_success_signal"], specs["planner_loaded_signal"],
            specs["planner_scheduled_card"], dumps_dir)
        out.append(("__chain__", passed is True,
                    detail or "同页因果链成立：账号 → 提交 → 成功 → "
                              "Planner 就绪 → 卡片 → final"))
    return out


# ---------------------------------------------------------------------------
# 落盘
# ---------------------------------------------------------------------------

_HEADER = '''\
r"""**生成文件** —— 由 `tools/probe_signals.py --emit` 从一份 v2 probe dump
机械推导并逐条回查后写出。⛔ 不要手工编辑：下一次 --emit 会整份覆盖。

来源 dump：%s
生成时间：%s

每一条都通过了 `publish.evidence` 的回查（和 `--submit` 上发布校验用的是
同一套代码），并且五条一起通过了 `verify_publish_chain` 的同页因果顺序检查。
想知道它们是怎么推出来的，跑 `tools/probe_signals.py --check <dump>`。
"""
from __future__ import annotations

from publish.locator_types import EvidenceSignal, Locator

'''


def _literal(value: object, indent: int = 8) -> str:
    pad = " " * indent
    if isinstance(value, dict):
        if not value:
            return "{}"
        rows = ",\n".join("%s%r: %r" % (pad + "    ", key, item)
                          for key, item in value.items())
        return "{\n%s,\n%s}" % (rows, pad)
    return repr(value)


def _render(spec) -> str:
    kind = type(spec).__name__
    fields = {
        Locator: ("key", "step", "surface", "role", "name", "name_source",
                  "source_dump", "sequences", "breaks_when", "inferred",
                  "attributes", "evidence_kind"),
        EvidenceSignal: ("key", "step", "kind", "surface", "source_dump",
                         "sequences", "breaks_when", "role", "name",
                         "url_prefix", "attributes"),
    }[type(spec)]
    lines = ["    %s(" % kind]
    for name in fields:
        lines.append("        %s=%s," % (name, _literal(getattr(spec, name))))
    lines.append("    ),")
    return "\n".join(lines)


def emit(results: list[Derived], dump: str, target: Path) -> None:
    locators = [item.spec for item in results
                if item.ok and isinstance(item.spec, Locator)]
    signals = [item.spec for item in results
               if item.ok and isinstance(item.spec, EvidenceSignal)]
    body = [_HEADER % (dump, datetime.now().astimezone().isoformat(timespec="seconds"))]
    body.append("LOCATORS: dict[str, Locator] = {item.key: item for item in (\n")
    body.append("\n".join(_render(spec) for spec in locators))
    body.append("\n)}\n\n")
    body.append("SIGNALS: dict[str, EvidenceSignal] = {item.key: item for item in (\n")
    body.append("\n".join(_render(spec) for spec in signals))
    body.append("\n)}\n")
    text = "".join(body)
    compile(text, str(target), "exec")            # 写之前先证明它能解析
    tmp = target.with_suffix(".py.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(target)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _targets() -> tuple[str, str, Path]:
    from core.config import cfg
    c = cfg()
    return (str(c.get("publish", "facebook_page_name", "") or "").strip(),
            str(c.get("publish", "instagram_account", "") or "").strip(),
            Path(c.state_dir))


def _print_results(results: list[Derived],
                   checks: list[tuple[str, bool, str]]) -> bool:
    by_key = {key: (flag, detail) for key, flag, detail in checks}
    print("\n证据推导与回查（%d 项必需）" % len(REQUIRED))
    print("-" * 72)
    ok = True
    for item in results:
        flag, detail = by_key.get(item.key, (False, item.detail))
        ok = ok and flag
        print("  %s %-28s %s" % ("[OK]  " if flag else "[缺]  ",
                                 item.key, item.detail))
        if not flag and detail != item.detail:
            print("        回查：%s" % detail)
        if not flag and item.how:
            for line in item.how.splitlines():
                print("        补录：%s" % line)
        for warning in item.warnings:
            print("        ⚠ 脆弱：%s" % warning)
    if "__chain__" in by_key:
        flag, detail = by_key["__chain__"]
        ok = ok and flag
        print("  %s %-28s %s" % ("[OK]  " if flag else "[缺]  ",
                                 "同页因果链", detail))
    print("-" * 72)
    return ok


def report(data: dict, facebook: str, instagram: str) -> None:
    """把一份 dump 里**真实存在**的语义摊开给人看。

    `--check` 回答"能不能解锁"；这个回答"那 UI 到底长什么样"。
    两者的区别在 2026-09-01 那份 dump 上第一次显出价值：`--check` 说
    "推不出账号上下文"，而真相是 **composer 上根本没有 IG 账号名这个东西**——
    再录一百遍也不会有。这种时候要改的是证据契约，不是重录。
    """
    surfaces = {"composer": SURFACE_COMPOSER, "planner": SURFACE_PLANNER}
    print("\n=== 各界面的语义清单（role → 出现过的可访问名）===")
    for label, surface in surfaces.items():
        rows: dict[str, set[str]] = {}
        for snapshot in data["snapshots"]:
            if not _on(snapshot, surface):
                continue
            for row in _items(snapshot):
                rows.setdefault(str(row.get("role") or "?"), set()).add(
                    _short_name(row.get("accessible_name")
                                or row.get("visible_text")))
        print("\n-- %s（%s）" % (label, surface))
        for role in sorted(rows):
            names = sorted(rows[role])
            print("   %-12s %d 种：%s" % (
                role, len(names), "、".join(repr(n) for n in names[:6])
                + ("…" if len(names) > 6 else "")))

    print("\n=== 目标账号名在哪些界面出现过 ===")
    for token, label in ((facebook, "FB Page"), (instagram, "IG 帐号")):
        where: dict[str, list[str]] = {}
        for snapshot in data["snapshots"]:
            for row in _items(snapshot):
                if _norm(token).casefold() in _norm(_row_text(row)).casefold():
                    key = ("composer" if _on(snapshot, SURFACE_COMPOSER)
                           else "planner" if _on(snapshot, SURFACE_PLANNER)
                           else "其它")
                    entry = "%s/%s" % (row.get("role") or "?",
                                       _short_name(row.get("accessible_name")))
                    if entry not in where.setdefault(key, []):
                        where[key].append(entry)
        print("  %-8s %r" % (label, token))
        if not where:
            print("      ⛔ **整份 dump 里一次都没出现**"
                  " —— 这个值在这个 UI 上不存在，重录也不会有")
        for key, entries in where.items():
            print("      %-9s %s" % (key, "、".join(entries[:5])
                                     + ("…" if len(entries) > 5 else "")))

    print("\n=== 可信交互里带名字的（提交按钮从这里挑）===")
    for row in data["interactions"]:
        if row.get("is_trusted") is not True:
            continue
        best = ""
        for cand in evidence._candidates(row):
            name = _norm(cand.get("accessible_name") or cand.get("aria_label"))
            role = str(cand.get("role") or cand.get("explicit_role") or "")
            if name and role not in {"", "none", "presentation"}:
                best = "%s/%r" % (role, _short_name(name))
                break
        if best:
            print("  #%-3d ord=%-4d %-6s %-40s %s" % (
                row["sequence"], row["evidence_order"], row.get("event_type"),
                best, str(row.get("page_url") or "")[38:]))

    print("\n=== 疑似成功/状态语义（提交之后才出现的）===")
    seen: set[tuple[str, str]] = set()
    for snapshot in data["snapshots"]:
        for row in _items(snapshot):
            role = str(row.get("role") or "")
            if role not in ("status", "alert", "alertdialog", "dialog"):
                continue
            name = _short_name(row.get("accessible_name")
                               or row.get("visible_text"))
            if not name or (role, name) in seen:
                continue
            seen.add((role, name))
            print("  seq=%-3d %-11s %r" % (snapshot["sequence"], role, name))


def main(argv: list[str] | None = None) -> int:
    force_utf8()
    parser = argparse.ArgumentParser(
        description="从 v2 probe dump 推导 G6/G6c 验收证据并回查后落盘")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", metavar="DUMP",
                       help="只读体检：这份 dump 能不能解锁发布提交")
    group.add_argument("--report", metavar="DUMP",
                       help="摊开这份 dump 里真实存在的语义（推不出来时看它）")
    group.add_argument("--emit", metavar="DUMP",
                       help="推导 → 回查 → 写 publish/signals_backfilled.py")
    group.add_argument("--status", action="store_true",
                       help="当前发布校验是开是关，关着的话差什么")
    parser.add_argument("--caption", default="",
                        help="测试帖正文里的一小段，用来在卡片上认出正文子元素")
    parser.add_argument("--success-name", default="",
                        help="成功提示里的关键词（多个候选时用它选定）")
    args = parser.parse_args(argv)

    if args.status:
        return _status()

    source = Path(args.check or args.emit or args.report)
    facebook, instagram, state_dir = _targets()
    dumps_dir = source.parent if source.parent != Path("") else state_dir
    if not facebook or not instagram:
        print("[!] config.toml 的 [publish].facebook_page_name / "
              "instagram_account 必须先填好——账号证据要拿它们做比对。")
        return 2

    data, detail = evidence.validate_v2_dump(source.name, dumps_dir)
    print("=== v2 契约校验 ===")
    if data is None:
        print("  [X] %s" % detail)
        print("\n这份 dump 解锁不了发布提交。v2 契约要求：schema_version=2、"
              "mode=record-and-passive-evidence、started_at/finished_at 都有效、"
              "interactions 与 snapshots 序号连续、evidence_order 全局连续、"
              "并且有一张位于本轮截图目录内的 final 遮罩截图。")
        print("⚠️ finished_at 无效通常意味着**录制没有正常停止**——"
              "走完流程后回终端按 Enter，等它打完收尾再关窗口。")
        return 2
    print("  [OK] %s · %d 条交互 / %d 条被动快照"
          % (source.name, len(data["interactions"]), len(data["snapshots"])))

    if args.report:
        report(data, facebook, instagram)
        return 0

    results = derive_all(data, source.name, facebook=facebook,
                         instagram=instagram, caption_hint=args.caption,
                         success_pick=args.success_name)
    checks = verify_derived(results, dumps_dir)
    ok = _print_results(results, checks)

    if not ok:
        print("⛔ 至少一项推不出来或回查不过，发布校验保持关闭。")
        print("   按上面每条的「补录」提示重录一次，再跑一次 --check。")
        return 1
    if args.check:
        print("✅ 这份 dump 足以解锁发布提交。下一步：")
        print("   .venv\\Scripts\\python.exe tools\\probe_signals.py --emit %s"
              % source)
        return 0

    emit(results, source.name, GENERATED)
    print("✅ 已写 %s" % GENERATED.relative_to(ROOT))
    print("   还差最后一步：把 config.toml 的 [publish].ui_probe_dump 填成")
    print("   %s —— 那是人工审核这份证据的签字栏，程序不替你填。" % source.name)
    return 0


def _status() -> int:
    from publish import business_suite as bs
    print("=== G6/G6c 发布校验 ===")
    ok = True
    for label, fn in (("账号上下文", bs.require_account_context_evidence),
                      ("提交按钮 + 成功信号", bs.require_submission_evidence),
                      ("Planner 回读", bs.require_readback_evidence)):
        try:
            fn()
        except Exception as exc:                  # noqa: BLE001
            ok = False
            first = str(exc).splitlines()[0]
            print("  [关] %-22s %s" % (label, first))
        else:
            print("  [开] %-22s 证据齐全并已回查" % label)
    print("-" * 72)
    if ok:
        print("✅ 三道闸全开：run_publish_post.bat --submit 会真的点提交。")
    else:
        print("⛔ 闸是关的，--submit 会在碰浏览器之前失败闭合（这是对的）。")
        print("   录一份 v2 dump 之后跑："
              "tools\\probe_signals.py --check state\\publish_probe_<时间戳>.json")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
