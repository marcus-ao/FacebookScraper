"""德语文案的只读优化建议：提示词组装、严格解析与追加存储。

⛔ 建议永远不写 `translated_human.jsonl`，也不改 `translated.jsonl`。它只是摆给人看的
清单，采纳与否由人在编辑区决定（红线 6：人工文案不能被后台任务覆盖）。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from core import paid_model
from core.localization import LINK_PLACEHOLDER, extract_urls, text_de_digest, without_urls
from core.translated import (extract_hashtags, hashtags_preserved, money_preserved,
                             render_glossary)
from localize.text import _ADDRESS_FORM, _ANGLICISM, _GENDER_STYLE

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = ROOT / "prompts" / "suggest_de.md"

#: 改动 prompts/suggest_de.md 的规则时递增；旧建议随之失效。
SUGGEST_PROMPT_VERSION = 2

#: 一次最多给几条。再多人就不看了（FUNCTIONALITY §3.2）。
MAX_ITEMS = 8

KINDS = ("grammar", "wording", "register", "terminology", "fluency")


class SuggestionContractError(ValueError):
    """模型输出不满足建议契约。"""


def build_prompt(settings, *, max_items: int = MAX_ITEMS) -> str:
    """渲染建议模板。语域与术语表和翻译共用同一份配置，避免两套标准。"""
    tpl = TEMPLATE_PATH.read_text(encoding="utf-8")
    while tpl.lstrip().startswith("<!--"):
        stripped = tpl.lstrip()
        end = stripped.find("-->")
        if end < 0:
            break
        tpl = stripped[end + 3:]

    def pick(table, key, label):
        value = table.get((key or "").strip().lower())
        if value is None:
            raise SystemExit(f"config.toml 的 [translate].{label} = {key!r} 不认识。"
                             f"可选值：{', '.join(sorted(table))}")
        return value

    subs = {
        "{{ADDRESS_FORM}}": pick(_ADDRESS_FORM, settings.address_form, "address_form"),
        "{{GENDER_STYLE}}": pick(_GENDER_STYLE, settings.gender_style, "gender_style"),
        "{{ANGLICISM_POLICY}}": pick(_ANGLICISM, settings.anglicism_policy, "anglicism_policy"),
        "{{TONE}}": settings.tone or "（无额外要求）",
        "{{GLOSSARY}}": render_glossary(settings.glossary),
        "{{MAX_ITEMS}}": str(max_items),
    }
    unknown = sorted(set(re.findall(r"\{\{[^{}\r\n]+\}\}", tpl)) - set(subs))
    if unknown:
        raise SystemExit(f"建议提示词模板含未知占位符：{unknown}")
    for key, value in subs.items():
        tpl = tpl.replace(key, value)
    return tpl.strip()


def build_request(source_en: str, text_de: str) -> str:
    """模型输入。两份正文分开加标签，避免模型把英文当成要审校的对象。"""
    return json.dumps({"english_source": without_urls(source_en), "current_german": without_urls(text_de)},
                      ensure_ascii=False, indent=2)


def _applied(text_de: str, item: dict) -> str:
    return text_de.replace(item["quote"], item["replacement"], 1)


def parse_suggestions(value: str, *, text_de: str,
                      max_items: int = MAX_ITEMS) -> dict:
    """严格解析，逐条校验可定位性与不可改内容。

    返回 `{"items": [...], "dropped": [...]}`。**逐条丢弃而不是整批拒绝**：建议之间彼此
    独立，一条越界不该让另外七条也白花钱；但丢了多少要报出来，不能静默。
    """
    try:
        data = json.loads(value)
    except ValueError as exc:
        raise SuggestionContractError("模型没有返回可解析的 JSON 建议数组") from exc
    if not isinstance(data, list):
        raise SuggestionContractError("建议必须是 JSON 数组")
    if len(data) > max_items:
        raise SuggestionContractError(f"建议超过 {max_items} 条上限，请重试")

    items, dropped, seen = [], [], set()
    for raw in data:
        if (not isinstance(raw, dict)
                or any(not isinstance(raw.get(key), str) or not raw.get(key, "").strip()
                       for key in ("quote", "replacement", "kind", "why"))):
            raise SuggestionContractError("每条建议需要 quote、replacement、kind 和 why 四个文本字段")
        item = {key: raw[key] for key in ("quote", "replacement", "kind", "why")}
        if item["kind"] not in KINDS:
            raise SuggestionContractError(f"建议类型须是 {'、'.join(KINDS)} 之一")
        if item["quote"] == item["replacement"]:
            dropped.append("建议没有实际改动")
            continue
        count = text_de.count(item["quote"])
        if count != 1:
            # 定位不了就没法采用；0 次是模型凭印象重打的，多次是上下文取短了。
            dropped.append("片段在译文里出现 %d 次，无法定位" % count)
            continue
        if item["quote"] in seen:
            dropped.append("同一片段有重复建议")
            continue
        if extract_urls(item["replacement"]) or extract_hashtags(item["replacement"]):
            dropped.append("建议里出现了链接或话题标签，它们不归文案区管")
            continue
        # 人工稿可能已经换过定价和标签；建议只能相对本次稿件校验，不能要求退回英文。
        # 这两个函数返回的是违规清单，非空即越界。
        candidate = _applied(text_de, item)
        if (money_preserved(text_de, candidate) or hashtags_preserved(text_de, candidate)):
            dropped.append("建议会改动金额或话题标签")
            continue
        if (extract_urls(text_de) != extract_urls(candidate)
                or LINK_PLACEHOLDER.findall(text_de) != LINK_PLACEHOLDER.findall(candidate)):
            dropped.append("建议会改动链接或链接位置")
            continue
        seen.add(item["quote"])
        items.append(item)
    return {"items": items, "dropped": dropped}


def append_suggestions(path: Path, row: dict) -> dict:
    paid_model.append_jsonl(Path(path), row)
    return row


def load_suggestions(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    return paid_model.read_jsonl(
        path, on_corrupt=lambda *args: ValueError("文案建议记录损坏，请先核对"))


def latest_for(path: Path, *, account: str, post_id: str) -> dict | None:
    rows = [row for row in load_suggestions(path)
            if row.get("account") == account and row.get("post_id") == post_id]
    return rows[-1] if rows else None


def is_current(row: dict | None, *, source_text_sha256_value: str, text_de: str) -> bool:
    """建议绑定源文与当时的译文。译文一改，旧建议的 quote 就可能定位不到了。"""
    return bool(row
                and row.get("source_text_sha256") == source_text_sha256_value
                and row.get("text_de_sha256") == text_de_digest(text_de)
                and row.get("prompt_version") == SUGGEST_PROMPT_VERSION)


def record(*, job_id: str, account: str, post_id: str, source_text_sha256_value: str,
           text_de: str, parsed: dict, paid_request_id: str | None) -> dict:
    return {"job_id": job_id, "account": account, "post_id": post_id,
            "source_text_sha256": source_text_sha256_value,
            "body_de": text_de,
            "text_de_sha256": text_de_digest(text_de),
            "prompt_version": SUGGEST_PROMPT_VERSION,
            "items": parsed["items"], "dropped": parsed["dropped"],
            "paid_request_id": paid_request_id, "actor": None,
            "recorded_at": datetime.now(timezone.utc).isoformat()}
