r"""德语译文这件产物的**契约**：写盘格式、什么算「当前可用」、不可改内容规则。

这里没有一行会调用 API。它是 `translate.py`（写方）与三个读方
—— `publish/compose.py`、`localize_images.py`、`pipeline_assisted.py` ——
之间的那份共识。

单独拆出来的直接原因：`publish/compose.py` 曾经一次从 `translate` 拉 10 个
名字，其中 `apply_money_mapping` / `extract_money_tokens` /
`normalize_money_token` / `extract_hashtags` 在 `translate.py` 内部**一次都没被
调用过** —— 它们本来就是为 compose 写的，只是放错了地方。代价是发布组要
import 翻译执行器（连着 openai SDK 和整个批处理循环）才能判一个金额。
`localize_images.py` 同理，它只要这里的四个名字，却 import 了整个 translate。

⛔ **PROMPT_VERSION 住在这里，不在 translate.py。** 它是写进
``translated.jsonl`` 每一行的版本标记，判据 :func:`translation_is_current`
就在本文件里读它。版本号属于**文件格式**，不属于跑批的那个脚本 ——
放在写方会让读方为了判「这行还能用吗」而 import 写方。
改提示词就在这里 +1。
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from core import paid_model
from core.store import assert_physical_direct_path

# 提示词版本。改了提示词就把它 +1：译文行里记着这个值，
# 于是"这批译文是旧提示词产出的"变成可查的事实，而不是靠记忆。
PROMPT_VERSION = 5
_UNSET_REVISION = object()


class SourceTextError(ValueError):
    """归档正文不满足译文契约的最小输入要求。

    ``translate.SourceDataError`` 继承它，好让翻译批次那边把它当作
    「整批共享的致命错误」一起停下来。
    """


class HumanRevisionConflict(ValueError):
    """打开页面之后出现了更新的人工版本，旧页面不能把它覆盖。"""


# --------------------------------------------------------------------------
# 一、写盘格式与「当前可用」判据
# --------------------------------------------------------------------------

def source_text_sha256(text: str) -> str:
    """绑定模型实际收到的正文（strip 后 UTF-8），防止旧译文错配新正文。"""
    if not isinstance(text, str):
        raise SourceTextError("manifest 的 text 必须是字符串；已停止，未调用 API")
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def translation_is_current(source: dict, translated: dict | None) -> bool:
    """机器版需匹配提示词；人工版只绑定原文，不能被模板升级作废。"""
    if not isinstance(translated, dict):
        return False
    text = source.get("text")
    return (isinstance(text, str)
            and translated.get("source_text_sha256") == source_text_sha256(text)
            and (translated.get("is_human") is True
                 or translated.get("prompt_version") == PROMPT_VERSION))


def effective_translation(source: dict, machine: dict | None,
                          human: dict | None) -> dict | None:
    """返回人工优先的副本；过期人工版仍供复核展示，调用方另判 current。"""
    for entry, is_human in ((human, True), (machine, False)):
        if (not isinstance(entry, dict)
                or entry.get("post_id") != source.get("post_id")
                or not isinstance(entry.get("text_de"), str)
                or not entry["text_de"].strip()):
            continue
        selected = dict(entry, is_human=is_human)
        selected["stale"] = not translation_is_current(source, selected)
        return selected
    return None


def image_translation(source: dict, machine: dict | None,
                       human: dict | None) -> dict | None:
    """图片沿用当前机器文案；机器版缺失或过期时使用已复核的人工正文。

    这样日常改文案不会无声触发整组图片重做，而新原文已经人工复核后也不会
    因为机器译文过期而永远卡住。未复核的人工稿始终拦住新的图片费用。
    """
    selected = effective_translation(source, machine, human)
    if selected is not None and selected["is_human"] and selected["stale"]:
        return None
    generated = effective_translation(source, machine, None)
    if generated is not None and not generated["stale"]:
        return generated
    return selected if selected is not None and not selected["stale"] else None


def load_translated(path: Path) -> dict[str, dict]:
    """读 translated.jsonl，同 post_id 后写胜出（与 manifest 一致的语义）。"""
    return {row["post_id"]: row for row in _translation_rows(path, human=False)}


def _translation_rows(path: Path, *, human: bool):
    """统一读取两份译文账本，保留历史行供旧副本识别。"""
    if not path.exists():
        return
    assert_physical_direct_path(
        path.parent, path, kind="file", label=path.name)
    # 二进制逐行解码：若一次硬终止恰好截断 UTF-8 多字节字符，只跳过那一行；
    # 后面已经 fsync 的付费结果仍然必须可见，不能被整文件 UnicodeDecodeError 吞掉。
    with path.open("rb") as f:
        for raw_line in f:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                line = raw_line.decode("utf-8")
                r = json.loads(line)
                if (not isinstance(r, dict)
                        or not isinstance(r.get("post_id"), str)
                        or not r["post_id"].strip()
                        or not isinstance(r.get("text_de"), str)
                        or not r["text_de"].strip()):
                    continue
                if human:
                    source_hash = r.get("source_text_sha256")
                    if ("source_text_sha256" not in r
                            or (source_hash is not None and not (
                                isinstance(source_hash, str)
                                and re.fullmatch(r"[0-9a-f]{64}", source_hash)))):
                        continue
                    UUID(r["revision"])
                    recorded = datetime.fromisoformat(r["recorded_at"].replace("Z", "+00:00"))
                    if recorded.tzinfo is None or recorded.utcoffset() is None:
                        continue
                elif (not isinstance(r.get("translated_at"), str)
                      or not isinstance(r.get("model"), str)
                      or not isinstance(r.get("prompt_version"), int)):
                    continue
                yield r
            except (UnicodeDecodeError, ValueError, KeyError, TypeError, AttributeError):
                # TypeError：整行是合法 JSON 但不是对象（如数组），下标取不到
                continue


def load_human_translated(path: Path) -> dict[str, dict]:
    """人工版独立追加留档；后写胜出，坏行不遮住此前已保存的版本。"""
    return {row["post_id"]: row for row in _translation_rows(path, human=True)}


def load_human_translation_history(path: Path) -> dict[str, list[dict]]:
    """保留人工版本历史，供图片按其生成时的正文指纹找到同源依据。"""
    history: dict[str, list[dict]] = {}
    for row in _translation_rows(path, human=True):
        history.setdefault(row["post_id"], []).append(row)
    return history


def translation_text_history(arc_base: Path) -> dict[str, set[str]]:
    """全部已知译文的文本，用于区分旧派生副本和未留档的手工修改。"""
    texts: dict[str, set[str]] = {}
    for name, human in (("translated.jsonl", False), ("translated_human.jsonl", True)):
        for row in _translation_rows(arc_base / name, human=human):
            texts.setdefault(row["post_id"], set()).add(row["text_de"])
    return texts


def _append_human_row(path: Path, post_id: str, text_de: str,
                      source_hash: str | None, now: datetime | None, *,
                      expected_revision=_UNSET_REVISION) -> dict:
    if not isinstance(post_id, str) or not post_id.strip():
        raise ValueError("人工译文缺少 post_id")
    if not isinstance(text_de, str) or not text_de.strip():
        raise ValueError("人工译文不能为空")
    recorded = now if now is not None else datetime.now(timezone.utc)
    if (not isinstance(recorded, datetime) or recorded.tzinfo is None
            or recorded.utcoffset() is None):
        raise ValueError("人工译文保存时间必须带时区")
    row = {"post_id": post_id, "text_de": text_de,
           "source_text_sha256": source_hash,
           "recorded_at": recorded.astimezone(timezone.utc).isoformat(),
           "revision": str(uuid4()), "actor": None}
    assert_physical_direct_path(path.parent.parent, path.parent,
                                kind="directory", label="人工译文账号目录")
    assert_physical_direct_path(path.parent, path, kind="file", label=path.name)
    lock_path = path.with_suffix(".lock")
    assert_physical_direct_path(path.parent, lock_path, kind="file", label=lock_path.name)
    with paid_model.FileLock(lock_path, busy_message="正在保存人工译文，请稍后重试"):
        if expected_revision is not _UNSET_REVISION:
            current = load_human_translated(path).get(post_id)
            revision = current.get("revision") if current else None
            if expected_revision != revision:
                raise HumanRevisionConflict("人工译文已有新版本，请重新加载后再保存")
        paid_model.append_jsonl(path, row, guard=lambda p: assert_physical_direct_path(
            p.parent, p, kind="file", label=p.name))
    return row


def append_human_translation(path: Path, source: dict, text_de: str, *,
                             now: datetime | None = None,
                             expected_revision=_UNSET_REVISION) -> dict:
    """保存人工版本；显式传入 expected_revision 时，在追加锁内核对版本。

    None 表示打开页面时尚无人工版；省略参数用于没有页面快照的调用方。
    """
    return _append_human_row(path, source.get("post_id"), text_de,
                             source_text_sha256(source.get("text")), now,
                             expected_revision=expected_revision)


def preserve_legacy_translation(path: Path, post_id: str, text_de: str) -> dict:
    """仅在尚无人工版时导入旧文件，锁内再次确认，避免盖住并发保存的新稿。"""
    return _append_human_row(path, post_id, text_de, None, None,
                             expected_revision=None)


def append_translated(path: Path, row: dict) -> None:
    """把付费结果安全追加成独立一行；坏尾/缺换行不能吞掉新结果。"""
    paid_model.append_jsonl(path, row, guard=lambda p: assert_physical_direct_path(
        p.parent, p, kind="file", label="translated.jsonl"))


# --------------------------------------------------------------------------
# 二、术语表渲染（写方与调图的提示词共用同一份口径）
# --------------------------------------------------------------------------

def render_glossary(glossary: dict) -> str:
    if not glossary:
        return ("（本账号还没有配置术语表。请在同一批译文里对反复出现的产品名与卖点词"
                "保持一致的译法。）")
    lines = ["以下英文词/短语**必须**译成右列指定的德文，不得使用同义替换：", "",
             "| 英文 | 德文 |", "| --- | --- |"]
    lines += [f"| {k} | {v} |" for k, v in sorted(glossary.items())]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 需人工确认的数字（价格 / 尺码 / 英制单位）
# --------------------------------------------------------------------------

# 提示词要求模型**不要**换算金额与尺码，所以这些会原样留在德语译文里。
# 这里把它们标出来，让审校人一眼看到"这篇要改价"，而不是在几十篇里自己找。
_MONEY_RE = re.compile(
    r"[$€£¥]\s?\d"                                       # $50、€ 50（符号在前）
    r"|\d[\d.,]*\s?[$€£¥]"                               # 19,99 €（德式后置，我们自己要求的格式）
    r"|(?<![A-Za-z])\d[\d.,]*\s?(?:USD|EUR|Dollar|Euro)\b",
    re.I)
_SIZE_RE = re.compile(
    r"\b(?:US|UK)\s?\d{1,2}(?:[.,]5)?\b|\b(?:size|Größe|Gr\.)\s?\d{1,3}\b", re.I)
_IMPERIAL_RE = re.compile(
    r"\b\d[\d.,]*\s?(?:inch(?:es)?|lbs?|oz|ft|°F)\b|\d\s?\"", re.I)


def numeric_flags(text: str) -> list[str]:
    """译文里需要人工确认的数字。空列表表示这篇可以直接用。"""
    out = []
    if _MONEY_RE.search(text):
        out.append("含货币金额 —— 需替换成德国站定价（提示词刻意不换算）")
    if _SIZE_RE.search(text):
        out.append("含数字尺码 —— 需确认是否要转 EU 码")
    if _IMPERIAL_RE.search(text):
        out.append("含英制单位 —— 确认换算是否正确")
    return out


def review_numeric_flags(src_en: str, text_de: str) -> list[str]:
    """审校警示同时看原文和译文，避免模型删掉/改写数字后反而不报警。"""
    out: list[str] = []
    if _MONEY_RE.search(src_en or "") or _MONEY_RE.search(text_de or ""):
        out.append("含货币金额 —— 需替换成德国站定价（提示词刻意不换算）")
    if _SIZE_RE.search(src_en or "") or _SIZE_RE.search(text_de or ""):
        out.append("原文或译文含数字尺码 —— 确认未擅自换成 EU 码")
    if _IMPERIAL_RE.search(src_en or "") or _IMPERIAL_RE.search(text_de or ""):
        out.append("原文含/译文保留英制单位 —— 核对物理量换算与有效位数")
    return out


# 一个完整的金额 token：符号在前（$49.99）或在后（49,99 € / 50 USD）。
# 用 \d+(?:[.,]\d+)* 而不是 [\d.,]* ——后者会把句尾的句号也吃进来，
# 导致"原样出现"的比对因为一个标点而误报。
_MONEY_TOKEN_RE = re.compile(
    r"[$€£¥]\s?\d+(?:[.,]\d+)*"
    r"|\d+(?:[.,]\d+)*\s?(?:(?:USD|EUR|Dollar|Euro)\b|[$€£¥](?!\w))",
    re.I)


def normalize_money_token(tok: str) -> str:
    """比对用的归一化：只去掉空白。数值、分隔符、符号、符号位置都要求原样。"""
    return re.sub(r"\s+", "", tok)


# 兼容模块内旧名字；流水线预检使用公开入口，避免与实际替换规则漂移。
_norm_money = normalize_money_token


# 兼容模块内旧名字；流水线预检使用公开入口，避免与实际替换规则漂移。
_norm_money = normalize_money_token


def extract_money_tokens(text: str) -> tuple[str, ...]:
    """公开给流水线分流使用的金额真相；与写盘硬闸共用同一正则。"""
    return tuple(token.strip() for token in _MONEY_TOKEN_RE.findall(text or ""))


def apply_money_mapping(text: str, mapping: Mapping[str, str]) -> str:
    """按完整金额 token 应用业务价格表；空白差异沿用金额硬闸的归一化。

    这一步只改最终发布副本，不回写 ``translated.jsonl``。配置里归一化后
    重复且值不同的键会失败，避免字典顺序偷偷决定价格。
    """
    normalized: dict[str, str] = {}
    for raw_key, raw_value in mapping.items():
        key = _norm_money(str(raw_key))
        value = str(raw_value).strip()
        if not key or not value:
            raise ValueError("价格映射的键和值都不能为空")
        if key in normalized and normalized[key] != value:
            raise ValueError("归一化后重复的金额映射值不一致：%r" % raw_key)
        normalized[key] = value
    return _MONEY_TOKEN_RE.sub(
        lambda match: normalized.get(_norm_money(match.group(0)), match.group(0)),
        text or "")


def money_preserved(src_en: str, text_de: str) -> list[str]:
    """检查原文每处金额是否原样出现，且译文没有新增金额。

    这是对提示词第 3 节的**代码侧强制**：提示词要求模型逐字符复制金额，
    但提示词只是要求，模型可能不听。金额被悄悄换算是本项目里
    最贵的一类错误——格式看着完全正确，人工审校时极易滑过去——
    所以必须有一道机器检查兜底。

    只去空白后比对，不做任何数值或格式归一：
    `$49.99` → `49,99 $` 币种没变，但写法和符号位置都变了，同样算违规。
    """
    source_tokens = _MONEY_TOKEN_RE.findall(src_en or "")
    translated_tokens = _MONEY_TOKEN_RE.findall(text_de or "")

    # 必须按 token 精确、多重集比对。子串判断会把 $5 错认成存在于 $50 中；
    # 单纯逐个 ``in`` 还会让原文出现两次、译文只留一次的金额漏检。
    translated_left = Counter(_norm_money(t) for t in translated_tokens)
    missing: list[str] = []
    for token in source_tokens:
        normalized = _norm_money(token)
        if translated_left[normalized] > 0:
            translated_left[normalized] -= 1
        else:
            missing.append(token.strip())

    source_left = Counter(_norm_money(t) for t in source_tokens)
    added: list[str] = []
    for token in translated_tokens:
        normalized = _norm_money(token)
        if source_left[normalized] > 0:
            source_left[normalized] -= 1
        else:
            added.append(token.strip())

    if not missing and not added:
        return []
    parts = []
    if missing:
        parts.append("原文金额 %s 未在译文里原样出现" % "、".join(missing))
    if added:
        parts.append("译文新增了原文没有的金额 %s" % "、".join(added))
    hint = ("译文里凭空出现了 €/EUR，八成是被换算了"
            if re.search(r"€|EUR\b|Euro", text_de or "", re.I)
            else "可能被改写、被换算、重复或整个漏掉了")
    return ["❗%s —— %s" % ("；".join(parts), hint)]


def _is_hashtag_char(ch: str) -> bool:
    """Meta 标签可用的 Unicode 字符：字母、数字、组合记号与下划线。"""
    return ch == "_" or unicodedata.category(ch)[:1] in {"L", "N", "M"}


def extract_hashtags(text: str) -> list[str]:
    r"""按出现顺序提取 hashtag，并保留原始大小写与 Unicode 码点。

    不用 ``\w+``：它会漏掉部分组合音标/分解式文字；也不用“读到空格为止”，
    否则句尾逗号或句号会被误算进标签。
    """
    value = text or ""
    out: list[str] = []
    i = 0
    while i < len(value):
        if value[i] != "#":
            i += 1
            continue
        if i and (_is_hashtag_char(value[i - 1]) or value[i - 1] == "#"):
            i += 1
            continue
        end = i + 1
        while end < len(value) and _is_hashtag_char(value[end]):
            end += 1
        if end > i + 1:
            out.append(value[i:end])
            i = end
        else:
            i += 1
    return out


def hashtags_preserved(src_en: str, text_de: str) -> list[str]:
    """标签必须逐个原样复制，数量、内容、大小写与顺序全部一致。"""
    source = extract_hashtags(src_en)
    translated = extract_hashtags(text_de)
    if source == translated:
        return []
    return [
        "❗话题标签没有按原帖逐个原样照搬（数量、内容、大小写和顺序都必须一致）"
        f" —— 原文 {json.dumps(source, ensure_ascii=False)}；"
        f"译文 {json.dumps(translated, ensure_ascii=False)}"
    ]
