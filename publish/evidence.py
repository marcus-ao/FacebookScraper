"""将定位与信号回查到本机 probe；缺证据返回 None，真实提交必须为 True。"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from publish.selectors import (REGISTRY, SIGNALS, EvidenceSignal, Locator,
                               account_value_from_text,
                               normalize_account_value)

# 可参与 Locator.attributes 回查的字段。
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


# 仅缓存校验成功结果；mtime 或大小变化后重查。
_DUMP_CACHE: dict[tuple, tuple[dict, str]] = {}


def clear_dump_cache() -> None:
    """测试用：换了 dump 文件内容后强制重新校验。"""
    _DUMP_CACHE.clear()


def validate_v2_dump(source_dump: str, dumps_dir: Path
                     ) -> tuple[dict | None, str]:
    """验证新版证据契约完整性；部分录制绝不能解锁发布提交。"""
    if not source_dump:
        return None, "没有来源 dump"
    path = Path(dumps_dir) / source_dump
    if not path.is_file():
        return None, (
            "本机没有 %s —— 证据 dump 不进版本库，**本项目的自动发布是单机工具**。\n"
            "    换机器或 dump 丢失后必须重录一次探查才能重新解锁 --submit：\n"
            "        scripts\\run_probe_signals.bat  （详见 docs/MANUAL_STEPS.md 第 3 节）"
        ) % source_dump
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
        return None, "实际成功/回读证据只接受完整 v2 契约"
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
    """返回 (True, 空串)、(False, 原因) 或 (None, 缺证据原因)。"""
    if not spec.source_dump or not spec.sequences:
        return False, "没写来源 dump 或来源交互序号"
    path = Path(dumps_dir) / spec.source_dump
    if not path.is_file():
        return None, (
            "本机没有 %s —— 证据 dump 不进版本库，**本项目的自动发布是单机工具**。\n"
            "    换机器或 dump 丢失后必须重录一次探查才能重新解锁 --submit：\n"
            "        scripts\\run_probe_signals.bat  （详见 docs/MANUAL_STEPS.md 第 3 节）"
        ) % spec.source_dump
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
        # 此账号信号仅覆盖 FB；IG 须由独立详情证据确认。
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


# Planner 条目 v2 契约属性。
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
    """允许同一时刻重复出现；存在多个不同的时刻时返回 None。"""
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
    """以同时包含正文与完整时刻的 link 作为条目锚点。"""
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
    """按独立 token 匹配账号，防止子串碰撞；完整显示名另由提交前账号闸核验。"""
    needle = " ".join(str(token or "").split())
    if not needle:
        return False
    return re.search(r"(?<![\w.@])%s(?![\w.@])" % re.escape(needle),
                     haystack) is not None


def _channel_dialogs(items: list[dict], spec: EvidenceSignal,
                     channel: str) -> list[tuple[dict, str]]:
    """从独立渠道详情读取目标账号与 remote ID。"""
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
    """从独立的月份与年份 heading 解析可见月份。"""
    attrs = spec.attributes
    found = {"month": False, "year": False}
    for item in items:
        for row in _snapshot_candidates(item):
            role = str(row.get("role") or "")
            # 名称与可见文本逐一解析，不拼接重复的月份名。
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
    """核验正文时刻条目、独立渠道详情及可见月份；不证明远端图片。"""
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
        # 日历条目与各渠道详情跨快照，需按同一录证集合核验。
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

    # 同页按证据顺序贪心选择最早可行节点，后续仍要求有效 final 截图。
    def orders_on(rows: list[dict], page: str) -> list[int]:
        return sorted(
            row["evidence_order"] for row in rows
            if row.get("page_id") == page
            and isinstance(row.get("evidence_order"), int)
            and not isinstance(row.get("evidence_order"), bool))

    def first_at_least(values: list[int], bound: int, *, strict: bool) -> int | None:
        for value in values:
            if value > bound or (not strict and value == bound):
                return value
        return None

    pages = {row.get("page_id") for row in account_rows if row.get("page_id")}
    for page in pages:
        # 账号上下文那一条本身不能已经命中成功信号——否则"提交前不可见"就没证到。
        account_orders = orders_on(
            [row for row in account_rows if not _signal_hits(row, success)], page)
        if not account_orders:
            continue
        chain_orders = [account_orders[0]]
        for rows, strict in ((button_rows, True), (success_rows, True),
                             (loaded_rows, False), (planner_rows, False)):
            nxt = first_at_least(orders_on(rows, page), chain_orders[-1],
                                 strict=strict)
            if nxt is None:
                break
            chain_orders.append(nxt)
        if len(chain_orders) != 5:
            continue
        card_order = chain_orders[-1]
        if any(final.get("page_id") == page
               and int(final.get("evidence_order") or 0) >= card_order
               and valid_final(final)
               for final in finals):
            return True, ""
    return False, ("证据不在同一页面，或顺序不是账号 → 提交 → 成功 → "
                   "Planner 就绪 → 卡片 → final")

