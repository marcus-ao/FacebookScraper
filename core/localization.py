"""平台文案的分区、确认与版本契约；无模型调用、无远程链接探测。"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from core import paid_model, translated
from core.config import cfg
from core.store import assert_physical_direct_path, read_post_truth

CTA_PRESETS = ("Link in Bio 🔗", "Mehr dazu im Profil 🔗", "Entdecke mehr über den Link in unserer Bio.")
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"']+", re.IGNORECASE)

# IG 正文里残留的主页引导句。⚠️ 必须出现"链接/去某处"的指向词才算命中：德语的 Bio 还有
# "有机"的意思（Bio-Abfall、biologisch abbaubar），对这个品类是会真出现的词。
_PROFILE_HINT = re.compile(
    r"(?:bio|profil)\w*[-\s]?link"
    # bio/profil 必须是独立的词：跟着连字符或别的字母就是 Bio-Qualität、biologisch
    # 这类复合词，属于正常文案。
    r"|(?:in|im)\s+(?:der\s+|die\s+|unserer\s+|unserem\s+|our\s+|the\s+)?(?:bio|profil)\b(?![-\w])"
    r"|swipe\s+up", re.IGNORECASE)


def mentions_profile_link(text: str) -> bool:
    """正文里是否已经有一句去主页/bio 看链接的引导。"""
    return bool(_PROFILE_HINT.search(text or ""))


class LocalizationConflict(ValueError):
    """人工版本、源文或本地化选择已经改变。"""


class LocalizationValidationError(ValueError):
    """分区输入类型或格式不完整。"""


def _url_spans(text):
    for match in _URL_RE.finditer(text or ""):
        value = match.group().rstrip(".,!?;:，。！？；：…")
        while value and value[-1] in ")]}":
            opening = {")": "(", "]": "[", "}": "{"}[value[-1]]
            if value.count(value[-1]) <= value.count(opening):
                break
            value = value[:-1]
        if value:
            yield match.start(), match.start() + len(value), value


def extract_urls(text: str) -> list[str]:
    return list(dict.fromkeys(value for _, _, value in _url_spans(text)))


def without_urls(text: str) -> str:
    """仅拿掉 URL；其余字符不改，包括 hashtag、金额与换行。"""
    value = text or ""
    for start, end, _ in reversed(list(_url_spans(value))):
        value = value[:start] + value[end:]
    return value


def split_content(text: str) -> dict:
    value = without_urls(text)
    tags = translated.extract_hashtags(value)
    spans = []
    i = 0
    while i < len(value):
        if value[i] == "#" and (not i or not (translated._is_hashtag_char(value[i - 1]) or value[i - 1] == "#")):
            end = i + 1
            while end < len(value) and translated._is_hashtag_char(value[end]):
                end += 1
            if end > i + 1:
                spans.append((i, end))
                i = end
                continue
        i += 1
    for start, end in reversed(spans):
        value = value[:start] + value[end:]
    value = "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines())
    return {"body": re.sub(r"\n{3,}", "\n\n", value).strip(), "tags": tags,
            "links": extract_urls(text)}


def _key(value):
    return "".join(c for c in unicodedata.normalize("NFC", value).casefold() if c.isalnum())


def protected_tags(text: str, keep_verbatim: dict | None = None) -> list[str]:
    keep = keep_verbatim or {}
    names = {_key(value) for kind in ("brands", "models") for value in keep.get(kind, [])}
    return [tag for tag in split_content(text)["tags"] if _key(tag) in names]


def valid_url(value: str) -> bool:
    if not isinstance(value, str) or not value or any(c.isspace() for c in value):
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname) and not parsed.username
    except ValueError:
        return False


def _text_hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def draft_for(source: dict, effective_translation: dict | None, record: dict | None = None, *,
              link_map: dict | None = None, ig_bio_url: str = "", keep_verbatim: dict | None = None) -> dict:
    link_map = link_map or {}
    entry = effective_translation or {}
    text_de = str(entry.get("text_de") or "")
    current, original = split_content(text_de), split_content(source["text"])
    protected = protected_tags(source["text"], keep_verbatim)
    source_hash = translated.source_text_sha256(source["text"])
    is_human = entry.get("is_human") is True
    bound = bool(record and record.get("source_text_sha256") == source_hash
                 and record.get("text_de_sha256") == _text_hash(text_de)
                 and is_human and record.get("human_revision") == entry.get("revision"))
    links = []
    for index, url in enumerate(original["links"]):
        mapped = link_map.get(url, "")
        target = mapped
        if is_human and len(current["links"]) == len(original["links"]):
            actual = current["links"][index]
            if actual != url:
                target = actual
        links.append({"source_url": url, "target_url": target,
                      "mapped_url": mapped, "confirmed": bool(target and target == mapped),
                      "origin": "mapping" if target and target == mapped else "manual" if target else "missing"})
    if not original["links"] and is_human:
        links = [{"source_url": "", "target_url": url, "mapped_url": "",
                  "confirmed": False, "origin": "manual"} for url in current["links"]]
    tags = current["tags"] if text_de.strip() else list(original["tags"])
    draft = {"platform": source["platform"], "body_de": current["body"], "source_body": original["body"],
             "source_tags": original["tags"], "protected_tags": protected, "tags": tags,
             "hashtags_confirmed": not bool([tag for tag in original["tags"] if tag not in protected]),
             "links": links, "ig_cta": CTA_PRESETS[0] if source["platform"] == "instagram" and original["links"] else "",
             "ig_bio_url": ig_bio_url, "cta_presets": list(CTA_PRESETS),
             "revision": record.get("revision") if record else None,
             "source_stale": bool(text_de and not translated.translation_is_current(source, entry)),
             "record_stale": bool(record and not bound), "has_record": bound,
             "source_text_sha256": source_hash}
    if bound:
        for key in ("body_de", "tags", "hashtags_confirmed", "links", "ig_cta"):
            draft[key] = copy.deepcopy(record[key])
        for link in draft["links"]:
            mapped = link_map.get(link["source_url"], "")
            link["mapped_url"] = mapped
            # 映射表后来改动不能静默换掉单篇已经选定的链接。
            link["origin"] = "mapping" if mapped and link["target_url"] == mapped else "manual" if link["target_url"] else "missing"
    if not bound and source["platform"] == "instagram" and draft["ig_cta"]:
        # 兼容旧整段人工稿：已有同一句 bio 引导时只拆出一份。
        for cta in CTA_PRESETS:
            if draft["body_de"].endswith(cta):
                draft["body_de"] = draft["body_de"][:-len(cta)].rstrip()
                draft["ig_cta"] = cta
                break
    return draft


def effective_draft(account_dir: Path, source: dict, effective_translation: dict | None) -> dict:
    config = cfg()
    return draft_for(source, effective_translation, load_localizations(account_dir).get(source["post_id"]),
                     link_map=config.get("publish", "link_map", {}),
                     ig_bio_url=config.get("publish", "ig_bio_url", ""),
                     keep_verbatim=config.get("image", "keep_verbatim", {}))


def normalize_fields(fields: dict) -> dict:
    """只规范分区输入；不裁剪业务内容、不把未确认内容假装成已确认。"""
    body, tags, links = fields.get("body_de"), fields.get("tags"), fields.get("links")
    if not isinstance(body, str) or not isinstance(tags, list) or not isinstance(links, list):
        raise LocalizationValidationError("请提供正文、话题标签列表和链接列表")
    if any(not isinstance(tag, str) or translated.extract_hashtags(tag.strip()) != [tag.strip()] for tag in tags):
        raise LocalizationValidationError("每个话题标签应为 # 开头的完整词，不含空格或标点")
    if type(fields.get("hashtags_confirmed")) is not bool:
        raise LocalizationValidationError("请明确话题标签是否已确认")
    cleaned_links = []
    for item in links:
        if (not isinstance(item, dict) or not isinstance(item.get("source_url"), str)
                or not isinstance(item.get("target_url"), str) or type(item.get("confirmed")) is not bool):
            raise LocalizationValidationError("每条链接需包含原链接、德语链接和确认状态")
        cleaned_links.append({key: item[key].strip() if isinstance(item[key], str) else item[key]
                              for key in ("source_url", "target_url", "confirmed")})
    cta = fields.get("ig_cta", "")
    if not isinstance(cta, str):
        raise LocalizationValidationError("bio 引导话术须为文字")
    return {"body_de": body.strip(), "tags": [tag.strip() for tag in tags],
            "hashtags_confirmed": fields["hashtags_confirmed"], "links": cleaned_links, "ig_cta": cta.strip()}


LINK_PLACEHOLDER = re.compile(r'\{\{link([1-9][0-9]*)\}\}')


def render(draft: dict) -> str:
    body = draft.get("body_de") or ""
    if draft.get("platform") == "instagram":
        # 即使调用方绕过 Web，IG 最终文案也不能漏回 URL。
        body = without_urls(body).strip()
        tail = without_urls(draft.get("ig_cta") or "").strip()
    else:
        links = draft.get('links', [])
        used = set()
        def replace_link(match):
            index = int(match[1]) - 1
            if index >= len(links) or not valid_url(links[index].get('target_url')):
                return match[0]  # Kept visible in drafts; validate blocks publication.
            used.add(index)
            return links[index]['target_url']
        body = LINK_PLACEHOLDER.sub(replace_link, body)
        tail = "\n".join(link["target_url"] for index, link in enumerate(links)
                         if index not in used and valid_url(link.get("target_url")))
    return "\n\n".join(part for part in (body, tail, " ".join(draft.get("tags") or [])) if part)


def validate(draft: dict) -> dict:
    issues, warnings = [], []
    def issue(code, message):
        issues.append({"code": code, "message": message})
    if not str(draft.get("body_de") or "").strip():
        issue("body_missing", "请补充德语正文")
    body = str(draft.get('body_de') or '')
    has_placeholder = '{{' in body or '}}' in body
    cta = str(draft.get('ig_cta') or '')
    ig_placeholder = draft.get('platform') == 'instagram' and (
        has_placeholder or '{{' in cta or '}}' in cta)
    if ig_placeholder:
        issue('placeholder_not_supported', 'Instagram 不支持链接占位符，请使用 bio 引导话术')
    elif has_placeholder:
        remainder = LINK_PLACEHOLDER.sub('', body)
        links = draft.get('links') or []
        if ('{{' in remainder or '}}' in remainder or any(
                int(match[1]) > len(links) or not valid_url(links[int(match[1]) - 1].get('target_url'))
                for match in LINK_PLACEHOLDER.finditer(body))):
            issue('unknown_link_placeholder', '链接占位符无效或落地页未填写；请使用对应编号，如 {{link1}}')
    if draft.get("source_stale"):
        issue("source_stale", "源帖已更新，请重新复核并保存")
    if extract_urls(draft.get("body_de") or ""):
        issue("body_urls", "请把正文中的链接移到链接区")
    if split_content(draft.get("body_de") or "")["tags"]:
        issue("body_hashtags", "请把正文中的话题标签移到标签区")
    tags = draft.get("tags") or []
    protected = draft.get("protected_tags") or []
    if any(tags.count(tag) < protected.count(tag) for tag in protected):
        issue("protected_tags_changed", "品牌和型号标签须保留原始写法")
    semantic = [tag for tag in draft.get("source_tags", []) if tag not in protected]
    if (semantic or [tag for tag in tags if tag not in protected]) and not draft.get("hashtags_confirmed"):
        issue("hashtags_unconfirmed", "请确认本篇使用的话题标签")
    if draft.get("platform") == "facebook":
        if any(not item.get("target_url") or not item.get("confirmed") for item in draft.get("links", [])):
            issue("links_unconfirmed", "部分德语落地页尚未填写或确认")
        if any(item.get("target_url") and not valid_url(item["target_url"]) for item in draft.get("links", [])):
            issue("invalid_link", "德语链接须使用有效的 http 或 https 地址")
    elif draft.get("platform") == "instagram":
        if extract_urls(draft.get("ig_cta") or "") or split_content(draft.get("ig_cta") or "")["tags"]:
            issue("invalid_cta", "bio 引导话术不应包含链接或话题标签")
        if draft.get("links") and not str(draft.get("ig_cta") or "").strip():
            issue("cta_missing", "原帖有链接，请选择或填写 bio 引导话术")
        if (str(draft.get("ig_cta") or "").strip()
                and mentions_profile_link(draft.get("body_de") or "")):
            # 只提示不拦：自然语言判断误杀的代价是拒绝一次已经付过钱的产出。
            warnings.append({"code": "duplicate_profile_hint",
                             "message": "正文里可能还有一句主页引导，和下面的引导话术重复了；"
                                        "确认后删掉其中一处"})
    else:
        issue("unsupported_platform", "不支持这个目标平台")
    caption = render(draft)
    # 一并回传成品文案：审校台的复制按钮要给出的就是这一份，不能在前端另拼一遍。
    counts = {"caption": caption, "char_count": len(caption),
              "body_char_count": len(draft.get("body_de") or ""),
              "hashtag_count": len(tags)}
    if draft.get("platform") == "instagram":
        if counts["char_count"] >= 1980:
            warnings.append({"code": "caption_length", "message": "文案接近或超过 2200 字符，请人工检查；保存不会截断"})
        if counts["hashtag_count"] >= 27:
            warnings.append({"code": "hashtag_count", "message": "话题标签接近或超过 30 个，请人工检查；保存不会删减"})
    return {"issues": issues, "warnings": warnings, "ready": not issues, **counts}


def _ledger(account_dir: Path) -> Path:
    account_dir = Path(account_dir)
    assert_physical_direct_path(account_dir.parent, account_dir, kind="directory", label="本地化账号目录")
    return assert_physical_direct_path(account_dir, account_dir / "localization.jsonl", kind="file", label="本地化记录")


def load_localizations(account_dir: Path) -> dict[str, dict]:
    path = _ledger(account_dir)
    rows = {}
    if not path.exists():
        return rows
    with path.open("rb") as handle:
        for line in handle:
            try:
                row = json.loads(line.decode("utf-8"))
                if not isinstance(row, dict) or not isinstance(row.get("post_id"), str):
                    continue
                UUID(row["revision"])
                UUID(row["human_revision"])
                for key in ("source_text_sha256", "text_de_sha256"):
                    if not re.fullmatch(r"[0-9a-f]{64}", row[key]):
                        raise ValueError
                moment = datetime.fromisoformat(row["recorded_at"])
                if moment.tzinfo is None:
                    continue
                normalize_fields(row)
                rows[row["post_id"]] = row
            except (ValueError, KeyError, TypeError, AttributeError, UnicodeError):
                continue
    return rows


def append_localization(account_dir: Path, source: dict, draft: dict, *, human_revision: str,
                        expected_revision: str | None, expected_source_sha256: str, now=None) -> dict:
    account_dir = Path(account_dir)
    path = _ledger(account_dir)
    lock_path = assert_physical_direct_path(account_dir, account_dir / "localization.lock", kind="file", label="本地化写入锁")
    fields = normalize_fields(draft)
    moment = now or datetime.now(timezone.utc)
    if not isinstance(moment, datetime) or moment.tzinfo is None:
        raise LocalizationValidationError("保存时间须包含时区")
    with paid_model.FileLock(lock_path, busy_message="本地化选择正在保存，请稍后重试"):
        truth, _ = read_post_truth(account_dir, source)
        if translated.source_text_sha256(truth["text"]) != expected_source_sha256:
            raise LocalizationConflict("源帖已更新，请载入最新内容")
        previous = load_localizations(account_dir).get(truth["post_id"])
        if (previous.get("revision") if previous else None) != expected_revision:
            raise LocalizationConflict("本地化选择已有更新，请载入最新内容后再保存")
        human = translated.load_human_translated(account_dir / "translated_human.jsonl").get(truth["post_id"])
        if (not human or human["revision"] != human_revision
                or human["source_text_sha256"] != expected_source_sha256
                or human["text_de"] != render(dict(draft, **fields))):
            raise LocalizationConflict("本地化选择与当前人工文案不一致，请重新加载")
        row = {"post_id": truth["post_id"], "platform": truth["platform"], **fields,
               "revision": str(uuid4()), "previous_revision": expected_revision,
               "source_text_sha256": expected_source_sha256, "human_revision": human_revision,
               "text_de_sha256": _text_hash(human["text_de"]), "recorded_at": moment.astimezone(timezone.utc).isoformat(),
               "actor": None}
        paid_model.append_jsonl(path, row, guard=lambda p: _ledger(p.parent))
    return row
