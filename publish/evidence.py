r"""把 :mod:`publish.selectors` 的每一条定位**回查**到它自称的 probe dump。

存在的理由只有一条：**让"不许猜选择器"从一句纪律变成一个可执行的检查。**

红线 5（不得凭猜测编写 Business Suite 的选择器）此前只能靠人守。
一个"看起来合理"的定位提交进来，比不提交更糟——下一个人会以为它验证过。
有了这个模块，`tests/tests_publish.py` 可以在 dump 还在本机时逐条回查：
**编出来的定位当场被打回。**

⚠️ dump 在 `state/` 下，**不进版本库**。所以回查是"有则必查、无则跳过"：

- dump 在 → 逐条比对，对不上就是错；
- dump 不在（别人的机器 / CI）→ 只能做结构检查（来源字段填了没）。

**跳过不等于通过**，调用方要把两种结果分开打印。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from publish.selectors import (REGISTRY, SIGNALS, EvidenceSignal, Locator,
                               account_value_from_text,
                               normalize_account_value)

#: dump 里能拿来核对 :attr:`Locator.attributes` 的字段。
#: 其余键（``page_url`` / ``rendered_text`` / ``nested_editable_depth`` …）
#: 是给人读的旁注，不参与逐字段比对。
_CHECKABLE = ("tag", "input_type", "placeholder", "accept", "multiple",
              "explicit_role", "is_contenteditable")


def _candidates(item: dict) -> list[dict]:
    rows = [item.get("target") or {}]
    rows.extend(row for row in (item.get("candidates") or [])
                if isinstance(row, dict))
    return [row for row in rows if isinstance(row, dict)]


def _snapshot_candidates(item: dict) -> list[dict]:
    return [row for row in (item.get("semantic_items") or [])
            if isinstance(row, dict)]


def _parse_aware(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


# 校验结果缓存。**键含 mtime 与大小**，dump 在进程存活期间被换掉会重新校验。
#
# 一次 `--submit` 全程会沿 compose / workflow / business_suite 三条路径反复
# 要同一份 dump：实测约 38 次解析、约 72 MB 读盘，每次得出完全相同的结论。
# 这里只缓存"校验通过的结果"，失败路径照旧每次重算（失败是要给人看原因的）。
_DUMP_CACHE: dict[tuple, tuple[dict, str]] = {}


def clear_dump_cache() -> None:
    """测试用：换了 dump 文件内容后强制重新校验。"""
    _DUMP_CACHE.clear()


def validate_v2_dump(source_dump: str, dumps_dir: Path
                     ) -> tuple[dict | None, str]:
    """验证新版证据契约完整性；部分录制绝不能解锁生产提交。"""
    if not source_dump:
        return None, "没有来源 dump"
    path = Path(dumps_dir) / source_dump
    if not path.is_file():
        return None, "本机没有 %s（dump 不进版本库）" % source_dump
    try:
        stat = path.stat()
        cache_key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    except OSError:
        cache_key = None
    if cache_key is not None and cache_key in _DUMP_CACHE:
        return _DUMP_CACHE[cache_key]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, "dump 读不了：%s" % exc
    if not isinstance(data, dict):
        return None, "dump 顶层不是对象"
    if (data.get("schema_version"), data.get("mode")) != (
            2, "record-and-passive-evidence"):
        return None, "生产成功/回读证据只接受完整 v2 契约"
    if _parse_aware(data.get("started_at")) is None:
        return None, "v2 dump 缺少有效 started_at"
    if _parse_aware(data.get("finished_at")) is None:
        return None, "v2 dump 尚未正常停止（finished_at 无效）"
    if not isinstance(data.get("session_id"), str) or not data["session_id"]:
        return None, "v2 dump 缺少 session_id"

    orders: list[int] = []
    for key in ("interactions", "snapshots"):
        rows = data.get(key)
        if not isinstance(rows, list):
            return None, "v2 dump 缺少 %s 数组" % key
        for sequence, row in enumerate(rows, 1):
            if not isinstance(row, dict) or row.get("sequence") != sequence:
                return None, "%s 序号不连续" % key
            if not isinstance(row.get("page_id"), str) or not row["page_id"]:
                return None, "%s 第 %d 条缺少稳定 page_id" % (key, sequence)
            order = row.get("evidence_order")
            if not isinstance(order, int) or isinstance(order, bool) or order <= 0:
                return None, "%s 第 %d 条缺少 evidence_order" % (key, sequence)
            if _parse_aware(row.get("recorded_at")) is None:
                return None, "%s 第 %d 条 recorded_at 无效" % (key, sequence)
            orders.append(order)
    if sorted(orders) != list(range(1, len(orders) + 1)):
        return None, "交互与快照的 evidence_order 不连续/重复"

    finals = [row for row in data["snapshots"]
              if isinstance(row, dict) and row.get("reason") == "final"]
    if not finals:
        return None, "v2 dump 缺少停止录制时的 final 快照"
    screenshot_dir = (Path(dumps_dir) / (Path(source_dump).stem + "_screenshots"))
    valid_final = False
    for row in finals:
        raw = row.get("screenshot")
        if row.get("screenshot_error") not in {None, ""} or not isinstance(raw, str):
            continue
        shot = Path(raw).resolve(strict=False)
        try:
            shot.relative_to(screenshot_dir.resolve(strict=False))
        except ValueError:
            continue
        if shot.is_file() and shot.stat().st_size > 0:
            valid_final = True
            break
    if not valid_final:
        return None, "v2 dump 没有位于本轮截图目录内的有效 final 遮罩截图"
    if cache_key is not None:
        _DUMP_CACHE[cache_key] = (data, "")
    return data, ""


def _name_matches(row: dict, spec: Locator) -> bool:
    if not spec.name:
        return True
    haystack = "\n".join(str(row.get(key) or "") for key in
                         ("accessible_name", "aria_label", "visible_text"))
    return spec.name in haystack


def _role_matches(row: dict, spec: Locator) -> bool:
    if not spec.role:
        return True
    return spec.role in {str(row.get("role") or ""),
                         str(row.get("explicit_role") or "")}


def _attributes_match(row: dict, spec: Locator) -> bool:
    for key in _CHECKABLE:
        expected = spec.attributes.get(key)
        if expected is None:
            continue
        actual = row.get(key)
        if isinstance(actual, bool):
            if str(actual).lower() != str(expected).lower():
                return False
        elif str(actual or "") != str(expected):
            return False
    return True


def verify(spec: Locator, dumps_dir: Path) -> tuple[bool | None, str]:
    """回查一条定位。

    返回 ``(True, "")`` / ``(False, 原因)`` / ``(None, 跳过原因)``。
    """
    if not spec.source_dump or not spec.sequences:
        return False, "没写来源 dump 或来源交互序号"
    path = Path(dumps_dir) / spec.source_dump
    if not path.is_file():
        return None, "本机没有 %s（dump 不进版本库）" % spec.source_dump
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return False, "dump 读不了：%s" % exc
    collection = (data.get("snapshots") if spec.evidence_kind == "snapshot"
                  else data.get("interactions")) or []
    if spec.evidence_kind not in {"interaction", "snapshot"}:
        return False, "未知 evidence_kind=%r" % spec.evidence_kind
    by_sequence = {row.get("sequence"): row
                   for row in collection
                   if isinstance(row, dict)}
    for sequence in spec.sequences:
        item = by_sequence.get(sequence)
        if item is None:
            return False, "%s 里没有第 %d 条交互" % (spec.source_dump, sequence)
        candidates = (_snapshot_candidates(item)
                      if spec.evidence_kind == "snapshot" else _candidates(item))
        hit = any(_role_matches(row, spec) and _name_matches(row, spec)
                  and _attributes_match(row, spec)
                  for row in candidates)
        if not hit:
            return False, ("第 %d 条交互里找不到 role=%s / name=%r / %r —— "
                           "这条定位不是从 dump 抄来的"
                           % (sequence, spec.role, spec.name, spec.attributes))
    return True, ""


def verify_all(dumps_dir: Path) -> dict[str, tuple[bool | None, str]]:
    return {key: verify(spec, dumps_dir) for key, spec in REGISTRY.items()}


def _row_text(row: dict) -> str:
    return "\n".join(str(row.get(key) or "") for key in (
        "accessible_name", "visible_text"))


def _row_text_values(row: dict) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        str(row.get(key) or "") for key in ("accessible_name", "visible_text")
        if str(row.get(key) or "")))


def _surface_matches(item: dict, surface: str) -> bool:
    return bool(surface and surface in str(item.get("page_url") or ""))


def _signal_hits(item: dict, spec: EvidenceSignal) -> bool:
    if spec.kind == "url":
        return str(item.get("page_url") or "").startswith(spec.url_prefix)
    rows = [row for row in _snapshot_candidates(item)
            if str(row.get("role") or "") == spec.role
            and spec.name in _row_text(row)]
    if spec.key == "composer_account_context":
        # ⚠️ **2026-09-01 按真实 dump 收窄为只验 Facebook。**
        # 实测（`publish_probe_20260901_054226_378622.json`，46 张 composer
        # 快照）：composer 上**从头到尾没有出现过 IG 帐号名**，渠道只有
        # `img 'Instagram'` 一个图标。所以"提交前同时证明两个渠道"这条
        # 在这个 UI 上不可满足，不是没录到。IG 改由提交后的 Planner 详情
        # 弹窗回读证明（见 `_verify_planner_structure`），
        # **"少任一渠道就转人工"这条保证没有放松，只是挪到了提交之后。**
        attrs = spec.attributes
        expected = normalize_account_value(
            str(attrs.get("facebook_account_token") or ""))
        extracted = [account_value_from_text(
            value, str(attrs.get("facebook_account_regex") or ""))
            for row in rows for value in _row_text_values(row)]
        extracted = [value for value in extracted if value is not None]
        return bool(rows and expected and extracted and all(
            normalize_account_value(value) == expected
            for value in extracted))
    if spec.key == "planner_scheduled_card":
        return bool(_planner_entries([item], spec))
    return bool(rows)


#: `planner_scheduled_card` v2 契约要求的属性。
#: **2026-09-01 按真实 Planner 重写**，见 `docs/PROBE_FINDINGS_20260901.md`。
PLANNER_REQUIRED = (
    "date_format", "time_format", "datetime_regex",
    "entry_role", "entry_probe_text",
    "dialog_role", "dialog_name", "remote_id_regex",
    "facebook_marker", "facebook_account_token",
    "instagram_marker", "instagram_account_token",
    "visible_month_role", "visible_month_format",
    "visible_year_role", "visible_year_format",
)


def parse_entry_moment(rendered: str, attrs: dict) -> datetime | None:
    """从日历条目文本里解析出**唯一**一个时刻。

    ⚠️ 判据是"只指向一个时刻"，**不是"只匹配一次"**。
    可访问名与可见文本常常一模一样，拼起来天然就是两遍；
    按次数判会把唯一正确的条目当成"两个时刻"丢掉。
    真出现两个**不同**的时刻才返回 None —— 那时确实分不清哪个是目标。
    """
    try:
        pattern = re.compile(str(attrs.get("datetime_regex") or ""))
    except re.error:
        return None
    moments: set[datetime] = set()
    for match in pattern.finditer(" ".join(str(rendered or "").split())):
        groups = match.groupdict()
        if not groups.get("date") or not groups.get("time"):
            return None
        try:
            moments.add(datetime.strptime(
                "%s %s" % (groups["date"], groups["time"]),
                "%s %s" % (attrs.get("date_format"), attrs.get("time_format"))))
        except (ValueError, TypeError):
            return None
    return moments.pop() if len(moments) == 1 else None


def _planner_entries(items: list[dict], spec: EvidenceSignal) -> list[dict]:
    """日历条目：同一条可访问名里同时带正文样本与可解析的完整时刻。

    真实 Planner 没有"一张卡片带全部元数据"这种东西。周视图/待发列表里的
    那条 ``link`` 是**唯一**同时承载正文与目标时刻的元素，所以它就是锚点。
    """
    attrs = spec.attributes
    probe = str(attrs.get("entry_probe_text") or "")
    out = []
    for item in items:
        for row in _snapshot_candidates(item):
            if str(row.get("role") or "") != attrs.get("entry_role"):
                continue
            text = _row_text(row)
            if not probe or probe not in text:
                continue
            if parse_entry_moment(text, attrs) is None:
                continue
            out.append(row)
    return out


def token_present(haystack: str, token: str) -> bool:
    """账号 token 是否作为**独立词**出现在一整串弹窗文本里。

    ⚠️ 不能用 ``in``：``neakasa.de`` 是 ``neakasa.deals`` 的子串，
    子串判会把一个近碰撞账号读成目标账号。前后都不许接字母/数字/点/@。

    ⚠️ 这一条挡不住"目标名 + 后缀"（`Neakasa Deutschland Test`），
    空格是合法词边界。挡它的是**提交前**那道 composer 账号闸：
    那里比的是独立元素的**完整值**，多一个词就不等。
    """
    needle = " ".join(str(token or "").split())
    if not needle:
        return False
    return re.search(r"(?<![\w.@])%s(?![\w.@])" % re.escape(needle),
                     haystack) is not None


def _channel_dialogs(items: list[dict], spec: EvidenceSignal,
                     channel: str) -> list[tuple[dict, str]]:
    """某个渠道的详情弹窗 + 它的 remote id。

    FB 与 IG 是**两个独立弹窗、两个独立 remote id**（实测
    FB `1887083152480681` / IG `4378984725697354`）。账号名和正文只存在于
    弹窗自己那一整串可访问名里 —— IG 侧连独立子元素都没有，
    所以这里按"整串里同时含渠道标记与目标账号 token"判。
    """
    attrs = spec.attributes
    marker = str(attrs.get("%s_marker" % channel) or "")
    token = str(attrs.get("%s_account_token" % channel) or "")
    try:
        pattern = re.compile(str(attrs.get("remote_id_regex") or ""))
    except re.error:
        return []
    out: list[tuple[dict, str]] = []
    for item in items:
        for row in _snapshot_candidates(item):
            if str(row.get("role") or "") != attrs.get("dialog_role"):
                continue
            text = _row_text(row)
            if str(attrs.get("dialog_name") or "") not in text:
                continue
            if not marker or marker not in text:
                continue
            if not token or not token_present(text, token):
                continue
            match = pattern.search(text)
            if match is None or not match.groupdict().get("remote_id"):
                continue
            out.append((row, match.group("remote_id")))
    return out


def _visible_month(items: list[dict], spec: EvidenceSignal) -> bool:
    """Planner 的可见范围：`heading 'September'` + `heading '2026'` 两条。

    ⚠️ 真实 UI **没有** "start - end" 那种范围串（旧契约那条被实测推翻）。
    月份与年份是两条独立 heading，合起来就是当前视图覆盖的那个月。
    """
    attrs = spec.attributes
    found = {"month": False, "year": False}
    for item in items:
        for row in _snapshot_candidates(item):
            role = str(row.get("role") or "")
            # ⚠️ 逐个**单值**试。accessible_name 与 visible_text 常常一样，
            # 拼起来就是 "September September"，怎么都解析不出来。
            for raw in _row_text_values(row):
                text = " ".join(raw.split())
                for key in ("month", "year"):
                    if role != attrs.get("visible_%s_role" % key):
                        continue
                    try:
                        datetime.strptime(
                            text, str(attrs["visible_%s_format" % key]))
                    except (ValueError, TypeError):
                        continue
                    found[key] = True
    return found["month"] and found["year"]


def _verify_planner_structure(items: list[dict], spec: EvidenceSignal) -> str:
    """按**真实** Planner 证明：条目（时刻+正文）+ 两个渠道弹窗 + 可见月份。

    与旧契约的区别（旧的是照着想象中的富卡片写的，2026-09-01 被实测推翻）：

    - 时刻与正文**在同一条 link 上**，不是卡片内两个独立子元素；
    - FB / IG 是**两个独立对象**，各有各的详情弹窗与 remote id，
      不存在"一张卡片同时带两个渠道"；
    - 可见范围是月份 + 年份两条 heading，不是 start-end 串；
    - **图片数量在 Planner 侧完全不存在** —— 那条硬闸移到 composer 上传后。
    """
    attrs = spec.attributes
    missing = [key for key in PLANNER_REQUIRED if not attrs.get(key)]
    if missing:
        return "Planner 结构证据缺少属性：%s" % "、".join(missing)
    if not _planner_entries(items, spec):
        return "找不到同时带正文样本与可解析完整时刻的日历条目"
    if not _visible_month(items, spec):
        return "找不到能按声明格式解析的可见月份 + 年份 heading"
    ids: dict[str, set[str]] = {}
    for channel in ("facebook", "instagram"):
        found = _channel_dialogs(items, spec, channel)
        if not found:
            return ("找不到 %s 的详情弹窗（要求同一串里含渠道标记 %r、目标账号"
                    " token 与 remote id）" % (
                        channel, attrs.get("%s_marker" % channel)))
        ids[channel] = {remote for _row, remote in found}
    shared = ids["facebook"] & ids["instagram"]
    if shared:
        return ("FB 与 IG 读到同一个 remote id %s —— 两个渠道必须是两个独立"
                "远端对象，相同说明渠道标记没有真正区分开"
                % "、".join(sorted(shared)))
    return ""


def verify_signal(spec: EvidenceSignal,
                  dumps_dir: Path) -> tuple[bool | None, str]:
    """把成功/回读信号回查到 v2 ``snapshots``，不接受人工 notes 冒充。"""
    if not spec.source_dump or not spec.sequences:
        return False, "没写来源 v2 dump 或快照序号"
    data, detail = validate_v2_dump(spec.source_dump, dumps_dir)
    if data is None:
        return (None if detail.startswith("本机没有") else False), detail
    by_sequence = {row.get("sequence"): row
                   for row in (data.get("snapshots") or [])
                   if isinstance(row, dict)}
    declared: list[dict] = []
    for sequence in spec.sequences:
        item = by_sequence.get(sequence)
        if item is None:
            return False, "%s 里没有第 %d 条语义快照" % (
                spec.source_dump, sequence)
        if not _surface_matches(item, spec.surface):
            return False, "第 %d 条快照不在声明的 surface=%r" % (
                sequence, spec.surface)
        declared.append(item)
    if spec.key == "planner_scheduled_card":
        # ⚠️ 排期卡片的证据**天然跨多张快照**：条目在日历上，
        # FB 与 IG 的详情弹窗各要点开一次才看得到，三者不可能同框。
        # 所以这一条按声明的几张快照的**并集**验，其余信号仍然逐张验。
        problem = _verify_planner_structure(declared, spec)
        if problem:
            return False, "第 %s 条快照（并集）：%s" % (
                "/".join(str(n) for n in spec.sequences), problem)
        return True, ""
    for sequence, item in zip(spec.sequences, declared):
        if not _signal_hits(item, spec):
            return False, "第 %d 条快照没有声明的 %s 信号" % (
                sequence, spec.key)
    return True, ""


def verify_publish_chain(button: Locator, account: EvidenceSignal,
                         success: EvidenceSignal, loaded: EvidenceSignal,
                         planner: EvidenceSignal,
                         dumps_dir: Path) -> tuple[bool | None, str]:
    """证明同一已完成页面会话的 account→submit→success→loaded→card。"""
    sources = {button.source_dump, account.source_dump,
               success.source_dump, loaded.source_dump, planner.source_dump}
    if len(sources) != 1 or not next(iter(sources), ""):
        return False, "账号/提交/成功/Planner 证据不是同一份 v2 dump"
    source = next(iter(sources))
    data, detail = validate_v2_dump(source, dumps_dir)
    if data is None:
        return (None if detail.startswith("本机没有") else False), detail
    interactions = {row.get("sequence"): row for row in data["interactions"]}
    snapshots = {row.get("sequence"): row for row in data["snapshots"]}
    button_rows = [interactions.get(number) for number in button.sequences]
    button_rows = [row for row in button_rows if isinstance(row, dict)
                   and row.get("is_trusted") is True
                   and row.get("event_type") in {"click", "submit"}
                   and _surface_matches(row, button.surface)]
    account_rows = [snapshots.get(number) for number in account.sequences]
    account_rows = [row for row in account_rows if isinstance(row, dict)
                    and _signal_hits(row, account)
                    and _surface_matches(row, account.surface)]
    success_rows = [snapshots.get(number) for number in success.sequences]
    success_rows = [row for row in success_rows if isinstance(row, dict)
                    and _signal_hits(row, success)
                    and _surface_matches(row, success.surface)]
    loaded_rows = [snapshots.get(number) for number in loaded.sequences]
    loaded_rows = [row for row in loaded_rows if isinstance(row, dict)
                   and _signal_hits(row, loaded)
                   and _surface_matches(row, loaded.surface)]
    planner_rows = [snapshots.get(number) for number in planner.sequences]
    planner_rows = [row for row in planner_rows if isinstance(row, dict)
                    and _signal_hits(row, planner)
                    and _surface_matches(row, planner.surface)]
    if not all((button_rows, account_rows, success_rows,
                loaded_rows, planner_rows)):
        return False, "因果链缺账号上下文、可信提交、成功、Planner 就绪或卡片任一环"
    finals = [row for row in data["snapshots"] if row.get("reason") == "final"]
    screenshot_dir = (Path(dumps_dir) /
                      (Path(source).stem + "_screenshots")).resolve(strict=False)

    def valid_final(row: dict) -> bool:
        raw = row.get("screenshot")
        if row.get("screenshot_error") not in {None, ""} or not isinstance(raw, str):
            return False
        shot = Path(raw).resolve(strict=False)
        try:
            shot.relative_to(screenshot_dir)
        except ValueError:
            return False
        return shot.is_file() and shot.stat().st_size > 0

    for before in account_rows:
        for clicked in button_rows:
            for confirmed in success_rows:
                for ready in loaded_rows:
                    for card in planner_rows:
                        page_ids = {
                            before.get("page_id"), clicked.get("page_id"),
                            confirmed.get("page_id"), ready.get("page_id"),
                            card.get("page_id")}
                        orders = (before.get("evidence_order"),
                                  clicked.get("evidence_order"),
                                  confirmed.get("evidence_order"),
                                  ready.get("evidence_order"),
                                  card.get("evidence_order"))
                        if (len(page_ids) == 1
                                and all(isinstance(value, int) for value in orders)
                                and not _signal_hits(before, success)
                                and orders[0] < orders[1] < orders[2] <= orders[3]
                                <= orders[4]
                                and any(
                                    final.get("page_id") == card.get("page_id")
                                    and int(final.get("evidence_order") or 0)
                                    >= int(card.get("evidence_order") or 0)
                                    and valid_final(final)
                                    for final in finals)):
                            return True, ""
    return False, ("证据不在同一页面，或顺序不是账号 → 提交 → 成功 → "
                   "Planner 就绪 → 卡片 → final")


def verify_all_signals(dumps_dir: Path) -> dict[str, tuple[bool | None, str]]:
    return {key: verify_signal(spec, dumps_dir) for key, spec in SIGNALS.items()}
