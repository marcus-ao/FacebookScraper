r"""G1 Business Suite DOM 探查 v2：记录可信交互与被动成功证据，不驱动页面。

工具附着到 ``[publish]`` 的 Chrome，在现有/新开标签页里监听用户自己的
click / input / change / submit，并被动采样 URL、alert/status/toast 与页面语义。
它不会打开 URL、点击、填写、上传或提交。每条记录只保留抗构建期混淆的
属性，不记录 class/CSS path、cookie、输入值或凭据。
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import http.client
import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.chrome import attach                             # noqa: E402
from core.config import cfg                                # noqa: E402
from core.console import force_utf8                        # noqa: E402

BINDING_NAME = "__fbscraperPublishProbeRecord"
_EVENT_TYPES = {"click", "input", "change", "submit"}
_OBSERVATION_KEYS = {
    "business_suite_entry_url",
    "facebook_page_slug",
    "ui_timezone",
    "schedule_min_ahead",
    "schedule_max_ahead",
    "schedule_min_ahead_seconds",
    "schedule_max_ahead_seconds",
    "schedule_input_behavior",
    "success_signal",
    "instagram_min_aspect_ratio",
    "instagram_max_aspect_ratio",
    "instagram_max_images",
    "instagram_max_caption_length",
    "instagram_caption_length_mode",
    "instagram_max_hashtags",
    "instagram_aspect_ratio_rejection",
    "instagram_image_count_rejection",
    "instagram_caption_length_rejection",
    "instagram_hashtag_rejection",
    "extra_notes",
}
# 只用于 probe screenshot 的通用“有值控件”遮罩，不是流程定位器。dump 明令
# 不保存输入值，因此正文、日期/时间与下拉框也必须遮住，不能只遮登录凭据。
_SENSITIVE_INPUT_SELECTOR = (
    'input, textarea, select, [contenteditable=""], [contenteditable="true"], '
    '[contenteditable="plaintext-only"], [role="textbox"], [role="combobox"], '
    '[role="searchbox"], iframe'
)
_ELEMENT_STRING_FIELDS = {
    "tag": 64,
    "role": 128,
    "explicit_role": 128,
    "aria_label": 500,
    "aria_labelledby": 500,
    "data_testid": 500,
    "name": 500,
    "placeholder": 500,
    "visible_text": 500,
    "accessible_name": 500,
    "accessible_name_source": 64,
    "contenteditable": 64,
    "input_type": 64,
    "autocomplete": 128,
    "accept": 500,
}
_ELEMENT_BOOL_FIELDS = {
    "visible_text_truncated",
    "is_contenteditable",
    # 事件目标本身可能是 Submit button，但它的 dialog/group 祖先还包含作为
    # sibling 的正文编辑器。页面侧会先从 clone 删除这些后代；这个位让
    # Python 边界也能在监听脚本漂移/测试注入时再次失败闭合。
    "contains_editable_descendant",
    "multiple",
    "checked",
    "disabled",
}

_SEMANTIC_STRING_FIELDS = {
    "tag": 64,
    "role": 128,
    "accessible_name": 500,
    "visible_text": 500,
    "aria_live": 64,
    # 最近的卡片型语义容器；不是 CSS path/class，只用于证明子语义确实属于
    # 同一张 Planner 卡片，避免正文 token 冒充排期元数据。
    "container_role": 128,
    "container_accessible_name": 500,
}
_SEMANTIC_ROLES = {
    "alert", "status", "dialog", "button", "link", "heading", "article",
    "listitem", "list", "row", "grid", "gridcell", "group", "main", "region",
    "progressbar", "tab", "menuitem", "img",
}
_VISUAL_EVIDENCE_ROLES = {
    "alert", "status", "dialog", "heading", "article", "listitem",
    "row", "gridcell", "progressbar",
}


def console_text(value: str) -> str:
    r"""修掉控制台输入里的孤立代理字符，必要时按本机编码还原中文。

    ⚠️ **不修就会丢数据**：Windows 上 ``input()`` 遇到本机编码解不出的字节会走
    ``surrogateescape``，留下 ``\udcaf`` 这类孤立代理字符。它们
    ``json.dumps`` 之后 ``write_text(encoding="utf-8")`` **直接抛
    UnicodeEncodeError** —— 也就是说，你在观察项里**输入中文就可能整条写不进去**
    （2026-09-01 实测到）。这些框本来就是让人写中文说明的。

    先原样试；不行就把代理字符还原成原始字节，按 UTF-8 / GBK 依次重解；
    都不行才退到替换字符——**宁可看到几个 U+FFFD，也不能让整份 dump 写不进去**。
    """
    if not isinstance(value, str):
        return ""
    try:
        value.encode("utf-8")
        return value
    except UnicodeEncodeError:
        pass
    raw = value.encode("utf-8", "surrogateescape")
    for encoding in ("utf-8", "gbk", "cp936"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return value.encode("utf-8", "replace").decode("utf-8")


def _safe_text(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(console_text(value).split())[:limit]


def _safe_url(value: object) -> str:
    raw = _safe_text(value, 4096)
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return ""
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    if parts.username is not None or parts.password is not None:
        return ""
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))[:2048]


def _safe_element(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    input_type = _safe_text(value.get("input_type"), 64).lower()
    autocomplete = _safe_text(value.get("autocomplete"), 128).lower()
    credential_autocomplete = {
        "username", "email", "current-password", "new-password",
        "one-time-code", "cc-number", "cc-csc",
    }
    if (input_type in {"password", "hidden", "email"}
            or set(autocomplete.split()) & credential_autocomplete):
        return None
    # 页面侧 sensitiveTarget() 是第一道闸；这里防测试注入、监听脚本漂移，
    # 以及 type=text 但 name/aria/placeholder 明说自己是账号或验证码的控件。
    tag = _safe_text(value.get("tag"), 64).lower()
    credential_hint = " ".join(_safe_text(value.get(key), 500).casefold()
                               for key in ("name", "aria_label", "placeholder"))
    if tag == "input" and any(token in credential_hint for token in (
            "username", "user name", "e-mail", "email", "password",
            "passcode", "one-time", "otp", "verification code")):
        return None
    out: dict[str, object] = {}
    depth = value.get("ancestor_depth")
    if isinstance(depth, int) and not isinstance(depth, bool) and 0 <= depth < 8:
        out["ancestor_depth"] = depth
    for key, limit in _ELEMENT_STRING_FIELDS.items():
        out[key] = _safe_text(value.get(key), limit)
    for key in _ELEMENT_BOOL_FIELDS:
        raw = value.get(key)
        out[key] = raw if isinstance(raw, bool) else None
    out["href"] = _safe_url(value.get("href"))
    if not out["tag"] or out["tag"] in {"body", "html"}:
        return None
    return out


def _safe_payload(payload: object, session_id: str) -> dict | None:
    """页面只能提交固定 schema；未知字段和非可信事件一律丢弃。

    页面侧统一发 JSON 字符串（CDP 的 ``Runtime.addBinding`` 只收 string），
    所以这里先解析再校验。仍然接受 dict，便于测试直接喂结构体。
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            return None
    if not isinstance(payload, dict):
        return None
    if payload.get("session_id") != session_id or payload.get("is_trusted") is not True:
        return None
    event_type = payload.get("event_type")
    if event_type not in _EVENT_TYPES:
        return None
    target = _safe_element(payload.get("target"))
    if target is None:
        return None
    candidates = []
    raw_candidates = payload.get("candidates")
    if isinstance(raw_candidates, list):
        for candidate in raw_candidates[:8]:
            safe = _safe_element(candidate)
            if safe is not None:
                candidates.append(safe)
    if candidates:
        target = candidates[0]
    else:
        candidates = [target]
    editable = any(
        row.get("is_contenteditable") is True
        or str(row.get("role") or "") in {"textbox", "combobox", "searchbox"}
        or str(row.get("tag") or "") in {"input", "textarea", "select"}
        for row in candidates)
    if editable:
        # 页面侧已经整条祖先链脱敏；Python 再做一道，防监听脚本漂移或测试直接
        # 注入 contenteditable payload 时把正文塞进 visible/access name。
        redacted: list[dict] = []
        for row in candidates:
            clean = dict(row)
            clean["visible_text"] = ""
            clean["visible_text_truncated"] = False
            clean["accessible_name"] = str(
                clean.get("aria_label") or clean.get("placeholder") or "")
            redacted.append(clean)
        candidates = redacted
        target = candidates[0]
    else:
        # 点击 Schedule/Submit 时，按钮祖先可能是包含正文编辑器 sibling 的
        # composer dialog。即使页面监听器误把祖先整段 innerText 传来，也只
        # 丢弃该祖先的文本字段，不牺牲真正按钮自己的静态名称。
        redacted = []
        for row in candidates:
            clean = dict(row)
            if clean.get("contains_editable_descendant") is True:
                clean["visible_text"] = ""
                clean["visible_text_truncated"] = False
                clean["accessible_name"] = ""
                clean["aria_label"] = ""
                clean["placeholder"] = ""
            redacted.append(clean)
        candidates = redacted
        target = candidates[0]
    return {
        "session_id": session_id,
        "event_type": event_type,
        "is_trusted": True,
        "client_timestamp": _safe_text(payload.get("client_timestamp"), 64),
        "page_url": _safe_url(payload.get("page_url")),
        "document_title": _safe_text(payload.get("document_title"), 300),
        "target": target,
        "candidates": candidates,
    }


def _safe_semantic_item(value: object) -> dict | None:
    """过滤一条被动页面语义。

    v2 明确不收 textbox/combobox，也不收任何 ``value``：正文、账号、密码和
    OTP 都不应因为“成功证据”采样而进入 dump。需要定位正文输入框的证据仍由
    用户自己的可信 input 事件提供，且事件契约同样不记录输入值。
    """
    if not isinstance(value, dict):
        return None
    role = _safe_text(value.get("role"), 128).lower()
    if role not in _SEMANTIC_ROLES:
        return None
    out = {key: _safe_text(value.get(key), limit)
           for key, limit in _SEMANTIC_STRING_FIELDS.items()}
    out["href"] = _safe_url(value.get("href"))
    if not out["tag"] or not (out["accessible_name"] or out["visible_text"]):
        return None
    return out


def _safe_snapshot(value: object) -> dict | None:
    """把 CDP 被动采样压成 v2 白名单结构；未知字段一律丢弃。"""
    if not isinstance(value, dict):
        return None
    url = _safe_url(value.get("page_url"))
    if not url:
        return None
    items: list[dict] = []
    raw_items = value.get("semantic_items")
    if isinstance(raw_items, list):
        for raw in raw_items[:200]:
            item = _safe_semantic_item(raw)
            if item is not None and item not in items:
                items.append(item)
    return {
        "page_url": url,
        "document_title": _safe_text(value.get("document_title"), 300),
        "semantic_items": items,
    }


def _visual_evidence_fingerprint(payload: dict) -> str:
    """只用 URL 与证据相关角色决定自动截图，避免动态 button/link 造成截图风暴。"""
    focused = {
        "page_url": payload.get("page_url"),
        "document_title": payload.get("document_title"),
        "semantic_items": [
            item for item in payload.get("semantic_items", [])
            if item.get("role") in _VISUAL_EVIDENCE_ROLES
        ],
    }
    return json.dumps(focused, ensure_ascii=False, sort_keys=True)


# 只读语义采样。没有 click/focus/goto/query by class，也不读取 input.value。
# 允许角色的文本只遍历当前真正可见、且不在可编辑控件里的 text node；因此正文、
# display:none/aria-hidden/opacity:0 后代都不会从 main/region/group 祖先泄露。
SEMANTIC_SNAPSHOT_EXPRESSION = r"""(() => {
  const valueSelector = 'input, textarea, select, [contenteditable=""], '
    + '[contenteditable="true"], [contenteditable="plaintext-only"], '
    + '[role="textbox"], [role="combobox"], [role="searchbox"]';
  const sensitive = (element) => {
    if (!(element instanceof Element)) return false;
    const input = element.closest('input');
    if (!input) return false;
    const type = String(input.getAttribute('type') || '').toLowerCase();
    const ac = String(input.getAttribute('autocomplete') || '').toLowerCase();
    return ['password', 'email', 'hidden'].includes(type)
      || /(?:username|password|one-time-code)/.test(ac);
  };
  const implicitRole = (element) => {
    const tag = String(element.tagName || '').toLowerCase();
    if (tag === 'button') return 'button';
    if (tag === 'a' && element.hasAttribute('href')) return 'link';
    if (/^h[1-6]$/.test(tag)) return 'heading';
    if (tag === 'article') return 'article';
    if (tag === 'li') return 'listitem';
    if (tag === 'img') return 'img';
    return '';
  };
  const allowed = new Set([
    'alert', 'status', 'dialog', 'button', 'link', 'heading', 'article',
    'listitem', 'list', 'row', 'grid', 'gridcell', 'group', 'main', 'region',
    'progressbar', 'tab', 'menuitem', 'img'
  ]);
  const compact = (value, limit = 500) =>
    String(value || '').replace(/\s+/g, ' ').trim().slice(0, limit);
  const containsValue = (element) => Boolean(
    element instanceof Element
    && (element.matches(valueSelector) || element.querySelector(valueSelector)));
  const isVisible = (element) => {
    if (!(element instanceof Element)) return false;
    for (let current = element; current; current = current.parentElement) {
      if (current.hasAttribute('hidden')
          || current.getAttribute('aria-hidden') === 'true'
          || current.hasAttribute('inert')) return false;
      const style = window.getComputedStyle(current);
      const opacity = Number.parseFloat(style.opacity || '1');
      if (style.display === 'none'
          || ['hidden', 'collapse'].includes(style.visibility)
          || (Number.isFinite(opacity) && opacity <= 0)) return false;
    }
    return element.getClientRects().length > 0;
  };
  const safeText = (element, limit = 500) => {
    if (!(element instanceof Element)
        || element.closest(valueSelector)
        || !isVisible(element)) return '';
    const chunks = [];
    const walker = document.createTreeWalker(
      element, NodeFilter.SHOW_TEXT,
      {acceptNode: (node) => {
        const parent = node.parentElement;
        if (!parent || parent.closest(valueSelector) || !isVisible(parent)) {
          return NodeFilter.FILTER_REJECT;
        }
        return String(node.nodeValue || '').trim()
          ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      }});
    while (walker.nextNode()) {
      chunks.push(walker.currentNode.nodeValue || '');
      if (chunks.join(' ').length >= limit * 2) break;
    }
    return compact(chunks.join(' '), limit);
  };
  const semantic_items = [];
  for (const element of document.querySelectorAll(
      '[role], [aria-live], button, a[href], h1, h2, h3, h4, h5, h6, article, li, img')) {
    if (semantic_items.length >= 200 || sensitive(element) || !isVisible(element)) continue;
    const role = compact(element.getAttribute('role') || implicitRole(element), 128)
      .toLowerCase();
    const ariaLive = compact(element.getAttribute('aria-live'), 64);
    const effectiveRole = role || (ariaLive ? 'status' : '');
    if (!allowed.has(effectiveRole)) continue;
    const aria = containsValue(element) ? '' : compact(
      element.getAttribute('aria-label')
      || (element instanceof HTMLImageElement ? element.getAttribute('alt') : ''));
    const text = safeText(element);
    const name = aria || text;
    if (!name) continue;
    // 卡片型容器优先，避免 article 内更近的普通 group 把 Planner 归属截断；
    // 若没有卡片祖先，再允许 composer 的账号 group/region/dialog 作为容器。
    const cardContainer = element.parentElement?.closest(
      '[role="article"], article, [role="listitem"], li, '
      + '[role="row"], [role="gridcell"]');
    const accountContainer = element.parentElement?.closest(
      '[role="group"], [role="region"], [role="dialog"]');
    const container = cardContainer || accountContainer;
    let containerRole = '';
    let containerName = '';
    if (container) {
      containerRole = compact(
        container.getAttribute('role') || implicitRole(container), 128).toLowerCase();
      containerName = (containsValue(container) ? '' : compact(
        container.getAttribute('aria-label'), 500)) || safeText(container, 500);
    }
    semantic_items.push({
      tag: String(element.tagName || '').toLowerCase().slice(0, 64),
      role: effectiveRole,
      accessible_name: name,
      visible_text: text,
      aria_live: ariaLive,
      container_role: containerRole,
      container_accessible_name: containerName,
      href: element instanceof HTMLAnchorElement ? element.href : ''
    });
  }
  return {
    page_url: location.href,
    document_title: document.title,
    semantic_items
  };
})()"""


async def _cdp_semantic_snapshot(session) -> dict | None:
    try:
        response = await asyncio.wait_for(
            session.send("Runtime.evaluate", {
                "expression": SEMANTIC_SNAPSHOT_EXPRESSION,
                "returnByValue": True,
            }), timeout=3)
    except Exception:
        return None
    return _safe_snapshot((response.get("result") or {}).get("value"))

# 这个函数只安装事件监听器。刻意没有 querySelector 驱动、click/fill/goto，
# 更没有 className/CSS path。点击到 span 时把语义祖先链一起记下，避免按钮的
# role/aria-label 在父层而 event.target 恰好是内层图标。
INSTALL_FUNCTION = r"""({sessionId, bindingName}) => {
  // 截图会整体遮住 iframe；监听也只装在顶层文档。否则 stop 的主 execution
  // context 无法可靠遍历/清除跨源子 frame 里的 DOM listener 与 debounce timer。
  if (window !== window.top) return;
  const registryName = "__fbscraperPublishProbeHandlers";
  const previous = window[registryName];
  if (previous && previous.handlers) {
    for (const [name, handler] of Object.entries(previous.handlers)) {
      window.removeEventListener(name, handler, true);
    }
    for (const timer of previous.timers.values()) clearTimeout(timer);
  }

  const compact = (value, limit = 500) => {
    const text = String(value || "").replace(/\s+/g, " ").trim();
    return {
      text: text.slice(0, limit),
      truncated: text.length > limit,
    };
  };

  const valueSelector = 'input, textarea, select, [contenteditable=""], '
    + '[contenteditable="true"], [contenteditable="plaintext-only"], '
    + '[role="textbox"], [role="combobox"], [role="searchbox"]';
  const containsEditableDescendant = (element) => Boolean(
    element instanceof Element && element.querySelector(valueSelector));
  const isVisible = (element) => {
    if (!(element instanceof Element)) return false;
    for (let current = element; current; current = current.parentElement) {
      if (current.hasAttribute('hidden')
          || current.getAttribute('aria-hidden') === 'true'
          || current.hasAttribute('inert')) return false;
      const style = window.getComputedStyle(current);
      const opacity = Number.parseFloat(style.opacity || '1');
      if (style.display === 'none'
          || ['hidden', 'collapse'].includes(style.visibility)
          || (Number.isFinite(opacity) && opacity <= 0)) return false;
    }
    return element.getClientRects().length > 0;
  };
  const textWithoutEditableDescendants = (element) => {
    if (!(element instanceof Element)
        || element.matches(valueSelector)
        || !isVisible(element)) return "";
    const chunks = [];
    const walker = document.createTreeWalker(
      element, NodeFilter.SHOW_TEXT,
      {acceptNode: (node) => {
        const parent = node.parentElement;
        if (!parent || parent.closest(valueSelector) || !isVisible(parent)) {
          return NodeFilter.FILTER_REJECT;
        }
        return String(node.nodeValue || '').trim()
          ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      }});
    while (walker.nextNode()) chunks.push(walker.currentNode.nodeValue || '');
    return chunks.join(' ');
  };

  const implicitRole = (element) => {
    const tag = String(element.tagName || "").toLowerCase();
    const type = String(element.getAttribute?.("type") || "").toLowerCase();
    if (tag === "button") return "button";
    if (tag === "a" && element.hasAttribute("href")) return "link";
    if (tag === "textarea") return "textbox";
    if (tag === "select") return element.multiple ? "listbox" : "combobox";
    if (tag === "img") return "img";
    if (/^h[1-6]$/.test(tag)) return "heading";
    if (tag !== "input") return "";
    if (["button", "image", "reset", "submit"].includes(type)) return "button";
    if (type === "checkbox") return "checkbox";
    if (type === "radio") return "radio";
    if (type === "range") return "slider";
    if (type === "number") return "spinbutton";
    if (type === "search") return "searchbox";
    if (type === "hidden") return "";
    return "textbox";
  };

  const labelledByText = (element) => {
    const raw = element.getAttribute?.("aria-labelledby") || "";
    const chunks = [];
    for (const id of raw.split(/\s+/).filter(Boolean)) {
      const node = document.getElementById(id);
      if (node) chunks.push(textWithoutEditableDescendants(node));
    }
    return compact(chunks.join(" ")).text;
  };

  const associatedLabelText = (element) => {
    const chunks = [];
    if (element.labels) {
      for (const label of element.labels) {
        chunks.push(textWithoutEditableDescendants(label));
      }
    }
    const parentLabel = element.closest?.("label");
    if (parentLabel) chunks.push(textWithoutEditableDescendants(parentLabel));
    return compact(chunks.join(" ")).text;
  };

  const sensitiveTarget = (element) => {
    if (!(element instanceof Element)) return false;
    const input = element.closest?.(
      'input[type="password"], input[type="hidden"], input[type="email"], ' +
      'input[autocomplete~="username"], input[autocomplete~="current-password"], ' +
      'input[autocomplete~="new-password"], input[autocomplete~="one-time-code"]'
    );
    return Boolean(input);
  };

  const describe = (element, depth = 0, redactEditableText = false) => {
    if (!(element instanceof Element)) return null;
    const tag = String(element.tagName || "").toLowerCase();
    const explicitRole = element.getAttribute("role") || "";
    const hasEditableDescendant = containsEditableDescendant(element);
    const ariaLabel = (redactEditableText || hasEditableDescendant)
      ? "" : (element.getAttribute("aria-label") || "");
    const labelled = redactEditableText ? "" : labelledByText(element);
    const label = redactEditableText ? "" : associatedLabelText(element);
    let visible = redactEditableText ? "" : textWithoutEditableDescendants(element);
    const type = element.getAttribute("type") || "";
    if (tag === "input" && ["button", "reset", "submit"].includes(type.toLowerCase())) {
      visible = element.getAttribute("value") || visible;
    }
    const visibleText = compact(visible);
    const placeholder = element.getAttribute("placeholder") || "";
    const accessibleName = compact(
      ariaLabel || labelled || label || element.getAttribute("alt") ||
      placeholder || visibleText.text
    );
    const semantic = depth === 0 || explicitRole || implicitRole(element) ||
      ariaLabel || labelled || label || element.getAttribute("data-testid") ||
      element.getAttribute("name") || placeholder || element.isContentEditable ||
      ["button", "a", "input", "textarea", "select"].includes(tag);
    if (!semantic) return null;
    return {
      ancestor_depth: depth,
      tag,
      role: explicitRole || implicitRole(element),
      explicit_role: explicitRole,
      aria_label: ariaLabel,
      aria_labelledby: element.getAttribute("aria-labelledby") || "",
      data_testid: element.getAttribute("data-testid") || "",
      name: element.getAttribute("name") || "",
      placeholder,
      visible_text: visibleText.text,
      visible_text_truncated: visibleText.truncated,
      contains_editable_descendant: hasEditableDescendant,
      accessible_name: accessibleName.text,
      accessible_name_source: ariaLabel ? "aria-label" :
        (labelled ? "aria-labelledby" :
          (label ? "label" :
            (placeholder ? "placeholder" : "visible-text"))),
      is_contenteditable: Boolean(element.isContentEditable),
      contenteditable: element.getAttribute("contenteditable") || "",
      input_type: type,
      autocomplete: element.getAttribute("autocomplete") || "",
      accept: element.getAttribute("accept") || "",
      multiple: ("multiple" in element) ? Boolean(element.multiple) : null,
      checked: ("checked" in element) ? Boolean(element.checked) : null,
      disabled: ("disabled" in element) ? Boolean(element.disabled) : null,
      href: tag === "a" ? (element.href || "") : "",
    };
  };

  const candidates = (target) => {
    const result = [];
    let element = target instanceof Element ? target : target?.parentElement;
    const editable = element?.closest?.(
      'input, textarea, select, [contenteditable=""], [contenteditable="true"], ' +
      '[role="textbox"], [role="combobox"], [role="searchbox"]'
    );
    // 输入目标的祖先 innerText 同样会包含正文；整条祖先链都只保留结构属性。
    const redactEditableText = Boolean(editable);
    let depth = 0;
    while (element && depth < 8) {
      const tag = String(element.tagName || "").toLowerCase();
      if (["body", "html"].includes(tag)) break;
      const item = describe(element, depth, redactEditableText);
      if (item) result.push(item);
      element = element.parentElement;
      depth += 1;
    }
    return result;
  };

  const emit = (eventType, target, event) => {
    if (!event?.isTrusted || sensitiveTarget(target)) return;
    const binding = window[bindingName];
    if (typeof binding !== "function") return;
    const chain = candidates(target);
    const payload = {
      session_id: sessionId,
      event_type: eventType,
      is_trusted: true,
      client_timestamp: new Date().toISOString(),
      page_url: window.location.href,
      document_title: document.title,
      target: chain[0] || null,
      candidates: chain,
    };
    // ⚠️ 必须传**字符串**。CDP 的 Runtime.addBinding 暴露出来的函数只接受一个
    // string 参数；早期版本直接传对象，只有 Playwright 的 expose_binding 认。
    // 现在两条通路都走 JSON 字符串，Python 侧统一解析（CR-64）。
    try {
      Promise.resolve(binding(JSON.stringify(payload))).catch(() => {});
    } catch (err) { /* 页面被卸载途中，忽略 */ }
  };

  const timers = new Map();
  const handlers = {
    click: (event) => emit("click", event.target, event),
    change: (event) => emit("change", event.target, event),
    submit: (event) => emit(
      "submit", event.submitter || document.activeElement || event.target, event),
    input: (event) => {
      if (!event.isTrusted || sensitiveTarget(event.target)) return;
      const target = event.target;
      const old = timers.get(target);
      if (old) clearTimeout(old);
      timers.set(target, setTimeout(() => {
        timers.delete(target);
        emit("input", target, event);
      }, 700));
    },
  };
  for (const [name, handler] of Object.entries(handlers)) {
    window.addEventListener(name, handler, true);
  }
  window[registryName] = {sessionId, handlers, timers};
}"""


def install_script(session_id: str, binding_name: str = BINDING_NAME) -> str:
    """生成既可作为 init script、也可注入现有 frame 的监听器脚本。"""
    args = json.dumps(
        {"sessionId": session_id, "bindingName": binding_name},
        ensure_ascii=True)
    return "(%s)(%s);" % (INSTALL_FUNCTION, args)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_MASK_STYLE_ID = "__fbscraperPublishProbeMask"


def _drop_mask_expression() -> str:
    # getElementById 只会返回第一项；上一次在 add_mask await 中途被取消时，
    # 理论上可能与下一次插入重叠，因此循环删除同 ID 的全部 style。
    return (
        "(() => { let s; let n = 0; while ((s = document.getElementById(%s))) {"
        " s.remove(); n += 1; } return n; })()" % json.dumps(_MASK_STYLE_ID))


async def _drop_probe_masks(session, *, timeout: float = 2.0) -> bool:
    """限时移除 probe 截图遮罩；停止链路也会防御性再调用一次。"""
    try:
        completed, response = await _hard_wait(
            session.send("Runtime.evaluate", {
                "expression": _drop_mask_expression(),
                "returnByValue": True,
            }), timeout=timeout)
        return bool(completed and isinstance(response, dict)
                    and not response.get("exceptionDetails"))
    except Exception:
        return False


async def _cleanup_probe_masks(session) -> bool:
    """让一次普通 task.cancel() 不能截断遮罩清理，同时保持 2 秒硬上限。"""
    cleanup = asyncio.create_task(_drop_probe_masks(session, timeout=2.0))
    try:
        return bool(await asyncio.shield(cleanup))
    except asyncio.CancelledError:
        # 若取消恰好发生在 cleanup 这个 await 上，shield 会让真正的清理继续；
        # 给它最后一个有界收尾窗口，然后保留原取消语义。
        try:
            await asyncio.wait_for(asyncio.shield(cleanup), timeout=2.1)
        except (Exception, asyncio.CancelledError):
            pass
        raise


async def _cdp_screenshot(session, path: Path) -> tuple[bool, str]:
    """Playwright 截不了时用 CDP 兜底，**并且保住敏感输入的遮罩**。

    Playwright 的 ``mask=`` 在这条路上用不了，所以先临时插一条 CSS 把
    所有有值/可编辑控件模糊掉，截完再撤掉。**不能因为换了通路就把隐私边界丢了**——
    `PUBLISH_PLAN` 的探查隐私要求对两条路径同样成立。
    """
    add_mask = (
        "(() => { let old; while ((old = document.getElementById(%s))) old.remove();"
        " const s = document.createElement('style');"
        " s.id = %s;"
        " s.textContent = %s + '{filter:blur(12px)!important}';"
        " document.documentElement.appendChild(s); return true; })()"
        % (json.dumps(_MASK_STYLE_ID), json.dumps(_MASK_STYLE_ID),
           json.dumps(_SENSITIVE_INPUT_SELECTOR)))
    result: tuple[bool, str] = (False, "截图未完成")
    cleanup_ok = False
    try:
        try:
            mask_response = await asyncio.wait_for(
                session.send("Runtime.evaluate",
                             {"expression": add_mask, "returnByValue": True}),
                timeout=2)
            if (mask_response.get("exceptionDetails")
                    or (mask_response.get("result") or {}).get("value") is not True):
                raise RuntimeError("浏览器没有确认遮罩已插入")
        except asyncio.CancelledError:
            raise
        except Exception:
            result = (False, "插入遮罩样式失败，为免泄露敏感输入放弃截图")
        else:
            try:
                shot = await asyncio.wait_for(
                    session.send("Page.captureScreenshot", {"format": "png"}),
                    timeout=5)
                data = shot.get("data")
                if not isinstance(data, str) or not data:
                    result = (False, "CDP 没有返回图片数据")
                else:
                    path.write_bytes(base64.b64decode(data))
                    result = (True, "ok")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                result = (False, "%s: %s" % (
                    type(exc).__name__, str(exc).splitlines()[0][:80]))
    finally:
        try:
            cleanup_ok = await _cleanup_probe_masks(session)
        except asyncio.CancelledError:
            raise
    if not cleanup_ok:
        return False, "%s；撤除遮罩失败，停止时会再次清理" % result[1]
    return result


async def _playwright_screenshot(page, path: Path) -> tuple[bool, str]:
    """带同一隐私遮罩的 Playwright 后备截图。

    发布 probe 已经有原始 CDP 会话；真实页面上 Playwright 的旧 page/frame 对象
    可能每张都耗满超时，所以它不能再是主路径。这里只在 CDP 截图失败时短暂兜底。
    """
    try:
        masks = [page.locator(_SENSITIVE_INPUT_SELECTOR)]
        await page.screenshot(
            path=str(path), full_page=False, mask=masks, timeout=3000)
        return True, "ok"
    except Exception as exc:
        return False, "%s: %s" % (
            type(exc).__name__, str(exc).splitlines()[0][:120])


class ProbeRecorder:
    """逐步截图并原子刷新 JSON；中途退出也保留已经记录的部分。"""

    def __init__(self, state_dir: Path, *, port: int, profile: Path,
                 timestamp: str | None = None):
        self.state_dir = Path(state_dir)
        self.timestamp = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self.session_id = "publish-probe-%s" % self.timestamp
        self.output_path = self.state_dir / ("publish_probe_%s.json" % self.timestamp)
        self.screenshot_dir = self.state_dir / (
            "publish_probe_%s_screenshots" % self.timestamp)
        self._lock = asyncio.Lock()
        self._finalizing = False
        self._closing = False
        self._last_snapshot_fingerprints: dict[str, str] = {}
        self._last_visual_fingerprints: dict[str, str] = {}
        self._last_snapshot_urls: dict[str, str] = {}
        # 没有 _counter：序号一律从 len(interactions) 推（CR-67，见 record()）。
        self.data = {
            "schema_version": 2,
            "session_id": self.session_id,
            "started_at": _utc_iso(),
            "finished_at": None,
            "cdp_port": int(port),
            "profile_dir": str(profile),
            "mode": "record-and-passive-evidence",
            "privacy": (
                "只保留白名单稳定属性；不记录 cookie/输入值/CSS/class，"
                "敏感输入不产生事件且截图遮罩，URL 去掉 query/fragment；"
                "被动语义不采样 textbox/combobox 或任何 value"),
            "interactions": [],
            "snapshots": [],
            "observations": {},
        }
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self._write()

    def _write(self) -> None:
        temporary = self.output_path.with_suffix(".json.tmp")
        payload = json.dumps(self.data, ensure_ascii=False, indent=2) + "\n"
        try:
            temporary.write_text(payload, encoding="utf-8")
        except UnicodeEncodeError:
            # 兜底：真有编不出来的字符时，宁可把那几个字符换掉，
            # 也不能让整份 dump 写不进去（上游 console_text 已经先修过一道）。
            temporary.write_text(payload, encoding="utf-8", errors="replace")
        temporary.replace(self.output_path)

    async def record(self, page, payload: object, session=None, *,
                     page_id: str = "page-001") -> bool:
        payload = _safe_payload(payload, self.session_id)
        if payload is None:
            return False
        async with self._lock:
            if self._closing or self._finalizing:
                return False
            # ⚠️ **序号必须由已落盘的条数推出来，不能用一个只增不减的计数器**（CR-67）。
            # 旧写法先 `self._counter += 1` 再去截图/写盘；中途只要失败一次
            # （截图抛异常、任务被取消、进程被 Ctrl+C），这个号就**永久空掉**。
            # 而 `compose._validated_probe_dump` 要求 sequence 从 1 起严格连续，
            # 于是一份内容完好的 dump 会被判为"交互序号不连续"而永远用不了。
            # 2026-09-01 真踩了：那次 Business Suite 录到 24 条，
            # 却因为死锁被中断，缺了 #19/#21/#24/#27 —— 数据在，但严格模式不认。
            # 现在按 len(interactions)+1 取号：失败的那一次不占号，下一次接着用。
            sequence = len(self.data["interactions"]) + 1
            event_type = str(payload.get("event_type") or "interaction")
            safe_event = "".join(ch for ch in event_type if ch.isalnum() or ch in "-_")[:24]
            screenshot = self.screenshot_dir / (
                "%03d_%s.png" % (sequence, safe_event or "interaction"))
            screenshot_error = None
            # 真实录制证明旧顺序会让每张图先耗满 Playwright 的 8 秒超时，之后
            # CDP 虽然能成功，画面却已经落后交互几十秒。原始 CDP 是本工具已验证
            # 的稳定通路，因此优先用它；Playwright 只做短时后备。
            if session is not None:
                ok, detail = await _cdp_screenshot(session, screenshot)
                if not ok:
                    fallback_ok, fallback_detail = await _playwright_screenshot(
                        page, screenshot)
                    if not fallback_ok:
                        screenshot_error = (
                            "CDP 截图失败：%s；Playwright 后备也失败：%s"
                            % (detail, fallback_detail))
            else:
                ok, detail = await _playwright_screenshot(page, screenshot)
                if not ok:
                    screenshot_error = detail

            if self._closing or self._finalizing:
                # final 已经划定边界；被 cancel 后迟到恢复的截图不得再追加 evidence。
                return False

            record = dict(payload)
            record["page_id"] = _safe_text(page_id, 64) or "page-unknown"
            # 全局顺序也必须在所有可取消的 await 完成后，从已落盘总数推出来。
            # 否则 Ctrl+C 落在截图期间会让 evidence_order 永久空一格。
            record["evidence_order"] = (
                len(self.data["interactions"]) +
                len(self.data["snapshots"]) + 1)
            record["sequence"] = sequence
            record["recorded_at"] = _utc_iso()
            record["screenshot"] = str(screenshot)
            record["screenshot_error"] = screenshot_error
            record["playwright_page_url"] = _safe_url(getattr(page, "url", ""))
            self.data["interactions"].append(record)
            self._write()
            return True

    async def snapshot(self, session, *, reason: str,
                       screenshot: bool = False,
                       page_id: str = "page-001", page=None) -> bool:
        """被动记录 URL/状态/页面语义；证据相关变化同步留遮罩截图。

        交互截图只能证明用户点了哪里，无法证明点击 URL 后落到哪个页面，也可能
        错过提交后短暂出现的 Boost 建议。URL、alert/status/dialog、标题与 Planner
        卡片角色变化会截图；普通动态 button/link 只落语义而不制造截图风暴；完全
        相同的快照仍去重。
        """
        safe_page_id = _safe_text(page_id, 64) or "page-unknown"
        payload = None
        if not screenshot:
            payload = await _cdp_semantic_snapshot(session)
            if payload is None:
                return False
            initial_fingerprint = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            initial_visual = _visual_evidence_fingerprint(payload)
            if (initial_fingerprint
                    == self._last_snapshot_fingerprints.get(safe_page_id)
                    and initial_visual
                    == self._last_visual_fingerprints.get(safe_page_id)):
                # 绝大多数 0.5 秒 tick 都走这里：一次轻量读取后直接返回，
                # 不拿全局 recorder 锁，也不做第二次语义读取/截图。
                return False
        async with self._lock:
            if self._closing or (self._finalizing and reason != "final"):
                return False
            # 语义初采样发生在锁外，用来避免无变化页面争锁；真正要落证据时再在
            # 锁内紧邻截图重采一次。否则前面排着一张慢截图时，可能把“旧 Boost
            # 语义”配到数十秒后的 Planner 图片上——本次真机错位正是这种形态。
            latest = await _cdp_semantic_snapshot(session)
            if latest is not None:
                payload = latest
            if payload is None:
                return False
            fingerprint = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            visual_fingerprint = _visual_evidence_fingerprint(payload)
            semantic_changed = (
                fingerprint != self._last_snapshot_fingerprints.get(safe_page_id))
            visual_changed = (
                visual_fingerprint != self._last_visual_fingerprints.get(safe_page_id))
            if not semantic_changed and not screenshot and not visual_changed:
                return False
            rows = self.data.setdefault("snapshots", [])
            sequence = len(rows) + 1
            previous_url = self._last_snapshot_urls.get(safe_page_id, "")
            record = dict(payload)
            record.update({
                "sequence": sequence,
                "page_id": safe_page_id,
                "evidence_order": (
                    len(self.data["interactions"]) + len(rows) + 1),
                "recorded_at": _utc_iso(),
                "reason": _safe_text(reason, 64),
                "previous_url": previous_url,
                "url_changed": bool(previous_url and previous_url != payload["page_url"]),
                "screenshot": "",
                "screenshot_error": None,
            })
            visual_captured = not visual_changed
            if screenshot or visual_changed:
                target = self.screenshot_dir / (
                    "semantic_%03d_%s.png" % (
                        sequence,
                        "".join(ch for ch in reason if ch.isalnum() or ch in "-_")[:24]
                        or "snapshot"))
                ok, detail = await _cdp_screenshot(session, target)
                if not ok and page is not None:
                    fallback_ok, fallback_detail = await _playwright_screenshot(
                        page, target)
                    if fallback_ok:
                        ok, detail = True, "ok"
                    else:
                        detail = ("CDP 截图失败：%s；Playwright 后备也失败：%s"
                                  % (detail, fallback_detail))
                if ok:
                    record["screenshot"] = str(target)
                    visual_captured = True
                else:
                    record["screenshot_error"] = detail
            if self._closing or (self._finalizing and reason != "final"):
                return False
            rows.append(record)
            self._last_snapshot_fingerprints[safe_page_id] = fingerprint
            # 截图失败时不能把视觉状态标成“已捕获”；即使语义完全相同，下一次
            # periodic 也必须重试，短暂 Boost/toast 才不会一次失败后永久丢证。
            if visual_captured:
                self._last_visual_fingerprints[safe_page_id] = visual_fingerprint
            self._last_snapshot_urls[safe_page_id] = str(payload.get("page_url") or "")
            self._write()
            return True

    async def set_observations(self, values: dict[str, str]) -> None:
        async with self._lock:
            clean = {}
            for key, value in values.items():
                if key not in _OBSERVATION_KEYS or not value:
                    continue
                if key == "business_suite_entry_url":
                    safe = _safe_url(value)
                else:
                    safe = _safe_text(value, 2000 if key == "extra_notes" else 500)
                if safe:
                    clean[key] = safe
            self.data["observations"].update(clean)
            self._write()

    def close_admission(self) -> None:
        """停止所有后续 evidence commit；在 final capture 完成后同步调用。"""
        self._closing = True

    def seal_for_final(self) -> None:
        """隔离所有迟到 interaction/periodic，只允许显式 ``reason=final``。"""
        self._finalizing = True

    async def finish(self, *, timeout: float = 5.0) -> bool:
        """写 ``finished_at``，即使旧截图 task 异常占锁也不无限等待。

        正常路径仍遵守 recorder 锁；超时后直接写共享 data 是安全的：停止闸已经
        关闭，且同步的 ``_write`` 中间没有 await。若旧 task 随后恢复，它写出的仍
        是同一个、已经带 finished_at 的 data。
        """
        acquired = False
        try:
            await asyncio.wait_for(self._lock.acquire(), timeout=timeout)
            acquired = True
            self.data["finished_at"] = _utc_iso()
            self._write()
            return True
        except asyncio.TimeoutError:
            self.data["finished_at"] = _utc_iso()
            self._write()
            return False
        finally:
            if acquired:
                self._lock.release()


async def _read_line(prompt: str) -> str:
    try:
        return console_text(await asyncio.to_thread(input, prompt)).strip()
    except EOFError:
        return ""


def _stdin_stop_waiter(*, readline=None) -> tuple[threading.Event, threading.Thread]:
    """用守护线程等待 Enter，避免 executor 的 stdin 线程拖住进程退出。

    这沿用 backfill 已在 Windows 真机验证过的模式。stdin 关闭也等价于停止；
    守护线程即使终端异常，也不会让 probe 在收尾完成后仍挂着。
    """
    done = threading.Event()
    read = readline or sys.stdin.readline

    def wait() -> None:
        try:
            read()
        except Exception:
            pass
        finally:
            done.set()

    thread = threading.Thread(
        target=wait, name="publish-probe-stdin", daemon=True)
    thread.start()
    return done, thread


async def _wait_for_stop_enter(prompt: str) -> None:
    print(prompt, end="", flush=True)
    done, _thread = _stdin_stop_waiter()
    while not done.is_set():
        await asyncio.sleep(0.05)


async def _drain_tasks(tasks: set[asyncio.Task], *,
                       timeout: float = 5.0) -> tuple[int, int]:
    """以单一绝对 deadline 收完旧事件、发 cancel 并收割，不做无界 gather。"""
    pending = [task for task in list(tasks) if not task.done()]
    if not pending:
        return 0, 0
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, timeout)
    # 预留最多 2 秒给截图 finally 撤遮罩；不能把 5 秒全花在首次 wait 上，
    # 再用一个无界 gather 把“最多 5 秒”承诺悄悄变回无限。
    cleanup_budget = min(2.0, max(0.0, timeout) / 2)
    first_wait = max(0.0, deadline - loop.time() - cleanup_budget)
    done, still_pending = await asyncio.wait(pending, timeout=first_wait)
    completed_before_cancel = len(done)
    for task in still_pending:
        task.cancel()
    if still_pending:
        remaining = max(0.0, deadline - loop.time())
        cancelled_done, stubborn = await asyncio.wait(
            still_pending, timeout=remaining)
        done.update(cancelled_done)
        # 消费已完成任务的异常；仍抗拒取消的任务不再阻塞 final/finished_at。
        if cancelled_done:
            await asyncio.gather(*cancelled_done, return_exceptions=True)
        for task in stubborn:
            task.add_done_callback(_consume_task_result)
    else:
        stubborn = set()
    if done:
        await asyncio.gather(*done, return_exceptions=True)
    return completed_before_cancel, len(still_pending)


def _consume_task_result(task: asyncio.Task) -> None:
    """后台 task 的异常不应变成 'Task exception was never retrieved' 噪音。"""
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        pass


async def _hard_wait(awaitable, *, timeout: float) -> tuple[bool, object | None]:
    """硬 deadline：超时后发 cancel 但绝不等待抗拒取消的第三方 coroutine。"""
    task = asyncio.ensure_future(awaitable)
    done, _pending = await asyncio.wait({task}, timeout=max(0.0, timeout))
    if task not in done:
        task.cancel()
        task.add_done_callback(_consume_task_result)
        return False, None
    try:
        return True, task.result()
    except asyncio.CancelledError:
        return False, None


async def _cancel_tasks(tasks: set[asyncio.Task], *, timeout: float = 3.0) -> int:
    """立即取消维护类 task，并只在同一个有界窗口内等待。"""
    pending = {task for task in list(tasks) if not task.done()}
    for task in pending:
        task.cancel()
    if not pending:
        return 0
    done, stubborn = await asyncio.wait(pending, timeout=max(0.0, timeout))
    if done:
        await asyncio.gather(*done, return_exceptions=True)
    for task in stubborn:
        task.add_done_callback(_consume_task_result)
    return len(stubborn)


REGISTRY_NAME = "__fbscraperPublishProbeHandlers"
_INSTALL_TIMEOUT = 15.0


async def _stop_page_listeners(session) -> None:
    """移除 listener/debounce/残留遮罩；只停止 probe，不改变业务页面。"""
    expression = r"""(() => {
      const registry = window[%s];
      let changed = false;
      let mask;
      while ((mask = document.getElementById(%s))) {
        mask.remove(); changed = true;
      }
      if (!registry) return changed;
      for (const [name, handler] of Object.entries(registry.handlers || {})) {
        window.removeEventListener(name, handler, true);
      }
      for (const timer of (registry.timers || new Map()).values()) clearTimeout(timer);
      window[%s] = {
        sessionId: registry.sessionId, handlers: {}, timers: new Map(), stopped: true
      };
      return true;
    })()""" % (json.dumps(REGISTRY_NAME), json.dumps(_MASK_STYLE_ID),
                  json.dumps(REGISTRY_NAME))
    try:
        await _hard_wait(
            session.send("Runtime.evaluate", {
                "expression": expression,
                "returnByValue": True,
            }), timeout=3)
    except Exception:
        # 后面的 session.detach 仍会切断通路；停止不能因一个已导航页面卡住。
        pass


def _remove_emitter_listener(emitter, event: str, handler) -> None:
    """兼容 Playwright EventEmitter 的两个版本接口；移除失败不阻断停止。"""
    for method_name in ("remove_listener", "off"):
        method = getattr(emitter, method_name, None)
        if callable(method):
            try:
                method(event, handler)
                return
            except Exception:
                continue


def _remove_tracked_callbacks(
        tracker: dict[object, list[tuple[str, object]]], emitter) -> None:
    for event, handler in tracker.pop(emitter, []):
        _remove_emitter_listener(emitter, event, handler)


async def _disable_probe_instrumentation(session, registration: dict | None = None) -> None:
    """停止当前 DOM，并撤销会在未来导航重新注入的 CDP 注册。"""
    registration = registration or {}

    async def send(method: str, params: dict) -> None:
        try:
            await _hard_wait(session.send(method, params), timeout=2)
        except Exception:
            pass

    removals = [
        send("Page.removeScriptToEvaluateOnNewDocument", {"identifier": identifier})
        for identifier in registration.get("new_document_ids", [])
        if isinstance(identifier, str) and identifier
    ]
    binding_name = registration.get("binding_name")
    if isinstance(binding_name, str) and binding_name:
        removals.append(send("Runtime.removeBinding", {"name": binding_name}))
    if removals:
        await asyncio.gather(*removals, return_exceptions=True)
    # 必须在撤销 init-script/binding **之后**清当前 DOM：若主帧恰在两步之间
    # 导航，新文档可能被尚有效的 init script 注入；最后再清一次才能闭合竞态。
    await _stop_page_listeners(session)


async def _install_existing(context, script: str) -> None:
    """遗留入口：只在已有页面上注入，不做校验。**新代码请用 install_on_page。**

    保留是因为老测试与手工排查还在用它。
    """
    for page in list(context.pages):
        for frame in list(page.frames):
            try:
                await frame.evaluate(script)
            except Exception:
                continue


async def install_on_page(context, page, script: str,
                          on_payload, *,
                          task_tracker: set[asyncio.Task] | None = None,
                          navigation_task_tracker: set[asyncio.Task] | None = None,
                          session_tracker: set[object] | None = None,
                          callback_tracker: dict[object, list[tuple[str, object]]] | None = None,
                          instrumentation_tracker: dict[object, dict] | None = None,
                          accepting=None,
                          on_admitted=None,
                          stopping=None,
                          ) -> tuple[object | None, bool, str]:
    """把监听器装进一个页面，**并确认它真的装上了**。返回 (session, ok, 说明)。

    ⚠️ **为什么整条改走 CDP，而不是 Playwright 的 page/frame API**（CR-64）：

    用 `connect_over_cdp` 附着到**连接之前就已经打开**的页面时，Playwright 的
    page 对象可能永远拿不到帧树——2026-09-01 实测用户那个 Business Suite 标签页：

        page.url            -> ''          （真实是 business.facebook.com/...）
        page.frames         -> 1 个空帧
        page.evaluate('1+1')-> **TimeoutError**
        expose_binding 之后 typeof window[binding] -> undefined
        add_init_script     -> 不生效（SPA 客户端路由，不产生新文档）

    而**同一个 target 上的原始 CDP 完全正常**：

        Runtime.evaluate('location.href') -> 真实 URL
        Page.getFrameTree                 -> 真实主帧
        Runtime.evaluate(注入脚本)         -> handlers = object

    旧实现在 `_install_existing` 里 `except Exception: continue` 把这个
    TimeoutError **整个吞掉**，于是：监听器一个都没装上、一条事件都没记录、
    **而且从头到尾没有任何提示**。用户走完整个发帖流程才发现 dump 是空的。

    所以现在：① 用 CDP 装；② **装完立刻回读校验**；③ 装不上就如实报出来。
    """
    try:
        session = await context.new_cdp_session(page)
    except Exception as exc:
        return None, False, "开不了 CDP 会话：%s" % type(exc).__name__
    if session_tracker is not None:
        # 必须在第一条可能挂住/抛错的 CDP 命令之前登记；半初始化 session 也要在
        # Enter 时移除 callback、清 listener 并 detach，不能只管 installed 成功表。
        session_tracker.add(session)
    registration = {"binding_name": BINDING_NAME, "new_document_ids": []}
    if instrumentation_tracker is not None:
        instrumentation_tracker[session] = registration

    def _remember_callback(event: str, handler) -> None:
        session.on(event, handler)
        if callback_tracker is not None:
            callback_tracker.setdefault(session, []).append((event, handler))

    def _on_binding(params):
        if params.get("name") != BINDING_NAME:
            return
        # admission 在 CDP callback 同步完成：Enter 之前已经抵达 Python 的事件
        # 会进入限时 drain；Enter 之后才到的事件当场拒绝，不会被异步 receive 的
        # 时序反过来误丢/误收。
        if accepting is not None and not accepting():
            return
        if on_admitted is not None:
            on_admitted(page)
        task = asyncio.create_task(on_payload(page, params.get("payload")))
        if task_tracker is not None:
            task_tracker.add(task)
            task.add_done_callback(task_tracker.discard)
        task.add_done_callback(_consume_task_result)

    try:
        _remember_callback("Runtime.bindingCalled", _on_binding)
        # ⚠️ **这两个 enable 不能省。** 实测：不开 Runtime/Page 域时，
        # 首个文档上一切正常，但**一导航就再也收不到 bindingCalled**——
        # addBinding 只会被装进"已被跟踪的"执行上下文，而新文档那个
        # 没有 Runtime.enable 就不会被跟踪。表现和这次的 bug 一模一样：
        # 前面记得好好的，换一页就全丢（CR-64）。
        await asyncio.wait_for(session.send("Runtime.enable"),
                               timeout=_INSTALL_TIMEOUT)
        await asyncio.wait_for(session.send("Page.enable"),
                               timeout=_INSTALL_TIMEOUT)
        await asyncio.wait_for(
            session.send("Runtime.addBinding", {"name": BINDING_NAME}),
            timeout=_INSTALL_TIMEOUT)
        # 覆盖后续顶层文档；脚本在子 frame 中会立即 return，避免停止后遗留
        # 无法从主 execution context 清掉的跨源 DOM listener/timer。
        new_document = await asyncio.wait_for(
            session.send("Page.addScriptToEvaluateOnNewDocument",
                         {"source": script}),
            timeout=_INSTALL_TIMEOUT)
        identifier = new_document.get("identifier") if isinstance(new_document, dict) else None
        if isinstance(identifier, str) and identifier:
            registration["new_document_ids"].append(identifier)
        # 覆盖**当前**这个文档——SPA 客户端路由不产生新文档，只靠上一条会漏。
        await asyncio.wait_for(
            session.send("Runtime.evaluate",
                         {"expression": script, "returnByValue": True}),
            timeout=_INSTALL_TIMEOUT)
        verify = await asyncio.wait_for(
            session.send("Runtime.evaluate",
                         {"expression": "typeof window.%s" % REGISTRY_NAME,
                          "returnByValue": True}),
            timeout=_INSTALL_TIMEOUT)
    except Exception as exc:
        return session, False, "%s: %s" % (type(exc).__name__, str(exc).splitlines()[0][:120])

    if (verify.get("result") or {}).get("value") != "object":
        return session, False, "注入后回读不到监听器标记（页面可能立刻导航了）"

    # 主帧一导航就立刻补注入一次。addScriptToEvaluateOnNewDocument 正常情况下
    # 已经覆盖了新文档，但**"正常情况下"正是这次栽跟头的地方**：装到一半页面
    # 就跳走、跨进程换渲染器等等都可能让它落空。这一层给的是"立刻"，
    # 巡检那一层给的是"最多两秒"，两层都便宜，都留着。
    def _on_navigated(params):
        frame = (params or {}).get("frame") or {}
        if frame.get("parentId") or (stopping is not None and stopping()):
            return                      # 只管主帧，子帧由 init script 覆盖
        task = asyncio.create_task(_reinject(session, script, stopping=stopping))
        if navigation_task_tracker is not None:
            navigation_task_tracker.add(task)
            task.add_done_callback(navigation_task_tracker.discard)
        task.add_done_callback(_consume_task_result)

    _remember_callback("Page.frameNavigated", _on_navigated)
    return session, True, "ok"


async def _reinject(session, script: str, *, stopping=None) -> None:
    """导航后补注入。失败不抛——巡检那一层还会再兜一次。"""
    if stopping is not None and stopping():
        return
    try:
        await asyncio.wait_for(
            session.send("Runtime.evaluate",
                         {"expression": script, "returnByValue": True}),
            timeout=_INSTALL_TIMEOUT)
    except Exception:
        pass


async def cdp_page_targets(port: int) -> list[dict]:
    """直接问 CDP 有哪些 page target —— 用来**核对 Playwright 有没有漏页**。

    Playwright 的 `context.pages` 与浏览器真实的 page target 可能对不上
    （见 :func:`install_on_page` 的实测）。漏了就等于那一页全程不记录，
    所以宁可多问一次 HTTP，也不能默认它们一致。
    """
    def _fetch() -> list[dict]:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            conn.request("GET", "/json/list")
            data = json.loads(conn.getresponse().read())
        finally:
            conn.close()
        if not isinstance(data, list):
            return []
        return [t for t in data
                if isinstance(t, dict) and t.get("type") == "page"]
    try:
        return await asyncio.to_thread(_fetch)
    except Exception:
        return []


# 观察项的问法集中在这里，录制和事后补填共用同一份，免得两处漂移。
OBSERVATION_PROMPTS = {
    "business_suite_entry_url": "创建帖入口最终 URL：",
    "facebook_page_slug": "FB Page 地址栏 slug（只填账号名段）：",
    "ui_timezone": "日期/时间控件原样显示的时区字符串：",
    "schedule_min_ahead": "UI 实测最早可排多久之后：",
    "schedule_max_ahead": "UI 实测最晚可排多远：",
    "schedule_min_ahead_seconds": "同一下限换算成整数秒：",
    "schedule_max_ahead_seconds": "同一上限换算成整数秒：",
    "schedule_input_behavior": "日期/时间能直接输入还是必须点选：",
    "success_signal": "提交成功的明确信号（toast/跳转/列表项）：",
    "instagram_min_aspect_ratio": "IG 实测最小宽高比（小数）：",
    "instagram_max_aspect_ratio": "IG 实测最大宽高比（小数）：",
    "instagram_max_images": "IG 实测单帖图片数上限（整数）：",
    "instagram_max_caption_length": "IG 实测正文长度上限（整数）：",
    "instagram_caption_length_mode": (
        "UI 计数方式（codepoints/utf16_units/utf8_bytes）："),
    "instagram_max_hashtags": "IG 实测标签数上限（整数）：",
    "instagram_aspect_ratio_rejection": "超出画幅边界时 UI 的实际拒绝行为：",
    "instagram_image_count_rejection": "超出图片数时 UI 的实际拒绝行为：",
    "instagram_caption_length_rejection": "超出正文长度时 UI 的实际拒绝行为：",
    "instagram_hashtag_rejection": "超出标签数时 UI 的实际拒绝行为：",
    "extra_notes": "其它观察：",
}


async def _ask_observations(apply, current: dict | None = None) -> None:
    """逐项问观察值。已有值会显示出来，直接回车＝保留原值。"""
    current = current or {}
    print("\n下面只记你亲眼看到的结果；不知道就直接按 Enter 跳过，绝不猜。")
    print("（随时 Ctrl+C 退出，已经填的会保留。）")
    values = {}
    for key, prompt in OBSERVATION_PROMPTS.items():
        old = current.get(key) or ""
        shown = "%s[当前 %s] " % (prompt, old) if old else prompt
        answer = await _read_line(shown)
        values[key] = answer or old
    await apply(values)


def _print_notes_hint(dump_path: Path) -> None:
    """没填观察项时说清楚：不影响这份 dump，但严格发布需要，而且随时能补。"""
    print("\n观察项（时区 / 排期窗口 / IG 四类上限）这一轮**没有填**。")
    print("  · 不影响这份交互 dump —— 回填 selectors.py 靠的是上面那些交互记录；")
    print("  · 但 `--strict` 真实发布**需要**它们：那几个数字只能你亲眼看 UI 得到，")
    print("    程序不许照抄网上流传的值（PUBLISH_PLAN 第 5 节第 4 条）。")
    print("  · 什么时候想填都行，不用重录：")
    print("      .venv\\Scripts\\python.exe tools\\probe_publish.py --fill-notes %s"
          % dump_path)


def _parse_preset(items) -> tuple[dict, list[str]]:
    """把 ``--set-note key=value`` 解析成字典；未知/畸形的原样报错，不静默丢。"""
    values: dict[str, str] = {}
    problems: list[str] = []
    for raw in items or ():
        key, sep, value = str(raw).partition("=")
        key = key.strip()
        if not sep or not key:
            problems.append("不是 KEY=VALUE 形式：%r" % raw)
        elif key not in OBSERVATION_PROMPTS:
            problems.append("不是已知观察项：%r" % key)
        elif not value.strip():
            problems.append("值是空的：%r" % key)
        else:
            values[key] = value.strip()
    return values, problems


async def fill_notes(dump_path: Path, preset=None) -> int:
    """只补填某份已有 dump 的观察项，不连浏览器、不录制。

    把"录交互"和"记数值"拆开：前者要对着浏览器一步步走，后者要去 UI 上试边界，
    本来就是两件事、两个时间点。**捆在一起问只会逼人一路回车跳过**，
    那样填出来的是假数据，比空着更糟。
    """
    if not dump_path.is_file():
        print("[!] 找不到这份 dump：%s" % dump_path)
        return 1
    try:
        data = json.loads(dump_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print("[!] 这份 dump 读不出来：%s" % exc)
        return 1
    if (not isinstance(data, dict)
            or data.get("schema_version") not in {1, 2}):
        print("[!] 不是受支持的 probe v1/v2 契约，拒绝改动。")
        return 1

    recorder = ProbeRecorder.__new__(ProbeRecorder)   # 只借它的校验与原子写
    recorder.output_path = dump_path
    recorder.data = data
    recorder._lock = asyncio.Lock()
    recorder._last_snapshot_fingerprints = {}
    recorder._last_snapshot_urls = {}
    print("dump：%s（%d 条交互）"
          % (dump_path, len(data.get("interactions") or [])))
    values, problems = _parse_preset(preset)
    if problems:
        for line in problems:
            print("[!] --set-note %s" % line)
        return 1
    if values:
        await recorder.set_observations(values)
        for key in sorted(values):
            print("  写入 %-34s %s" % (key, values[key]))
    else:
        await _ask_observations(recorder.set_observations,
                                current=data.get("observations") or {})
    observations = recorder.data.get("observations") or {}
    print("\n已写回：%s" % dump_path)
    # 必填清单直接问 compose 要，不在这里抄第二份 —— 抄了就会漂
    # （CR-48 就是两组对同一件事各写一套判据的代价）。
    from publish.compose import _PROBE_REQUIRED_OBSERVATIONS   # noqa: E402
    missing = [key for key in _PROBE_REQUIRED_OBSERVATIONS
               if not str(observations.get(key) or "").strip()]
    if missing:
        print("⚠️ `--strict` 真实发布还缺这 %d 项（缺任一都会失败闭合）：" % len(missing))
        for key in missing:
            print("     - %s　%s" % (key, OBSERVATION_PROMPTS.get(key, "")))
    else:
        print("✅ `--strict` 需要的必填项已经齐了。")
    return 0


async def run_probe(*, collect_notes: bool = False) -> Path:
    c = cfg()
    c.assert_publish_chrome_isolated()
    port = c.publish_debug_port
    profile = c.publish_profile_dir
    recorder = ProbeRecorder(c.state_dir, port=port, profile=profile)

    pw = browser = context = None
    try:
        pw, browser, context = await attach(
            port=port,
            profile=profile,
            start_script=r"scripts\start_chrome_publish.bat",
            login_hint="DE 发布账号")

        # ⚠️ 「按 Enter 停止记录」以前**并没有真的停下来**：它只停了巡检，
        # 页面上的监听器和 CDP 会话都还在，于是问答期间的每一次点击仍然排队进
        # record()，而 record() 是**持锁**的、单条最坏要十几秒。
        # 结果 set_observations() 在同一把锁上排到队尾，看起来就是卡死（CR-66）。
        recording = {"on": True}
        stopping = {"on": False}
        page_ids: dict[object, str] = {}
        receive_tasks: set[asyncio.Task] = set()
        background_tasks: set[asyncio.Task] = set()
        navigation_tasks: set[asyncio.Task] = set()
        last_admitted_page = {"page": None}
        all_sessions: set[object] = set()
        session_callbacks: dict[object, list[tuple[str, object]]] = {}
        session_instrumentation: dict[object, dict] = {}
        page_io_locks: dict[object, asyncio.Lock] = {}
        observation_scheduler = {"fn": None}

        def page_id_for(page) -> str:
            value = page_ids.get(page)
            if value is None:
                value = "page-%03d" % (len(page_ids) + 1)
                page_ids[page] = value
            return value

        def page_io_lock_for(page) -> asyncio.Lock:
            lock = page_io_locks.get(page)
            if lock is None:
                lock = asyncio.Lock()
                page_io_locks[page] = lock
            return lock

        async def receive(page, payload):
            try:
                recorded = False
                # 同一页的 interaction / semantic / repair 共用一条 I/O 单飞通道，
                # 避免截图与重注入在同一个 CDP session 上交错。
                async with page_io_lock_for(page):
                    recorded = await recorder.record(
                        page, payload, session=installed.get(page),
                        page_id=page_id_for(page))
                    if recorded:
                        # ⚠️ 逐条回显。上一次用户走完整个流程才发现 dump 是空的——
                        # 只要屏幕上不再跳数字，当场就知道没记上（CR-64）。
                        print("  #%d %s" % (len(recorder.data["interactions"]),
                                            _safe_text(
                                                (_safe_payload(payload, recorder.session_id)
                                                 or {}).get("event_type"), 12) or "?"))
                if recorded and installed.get(page) is not None:
                    # 不要拿着 page I/O 锁睡 0.35 秒再截图：连续输入事件会全部排在
                    # 后面并再次产生“早标签→晚画面”。释放锁后延迟发起每页单飞观察。
                    await asyncio.sleep(0.35)
                    scheduler = observation_scheduler["fn"]
                    if callable(scheduler):
                        scheduler(page, reason="after_interaction")
            except Exception:
                # 记录一条失败不能让后面全都收不到（binding 回调里抛异常会静默断链）
                pass

        def mark_admitted(page) -> None:
            last_admitted_page["page"] = page

        script = install_script(recorder.session_id)
        # ⚠️ 用 page 对象本身做键，**不要用 id(page)**：id 会在对象被回收后重用，
        # 那样一个新页面可能被当成"已经装过了"而被跳过。
        installed: dict[object, object] = {}        # page -> cdp session
        installation_tasks: dict[object, asyncio.Task] = {}
        observation_tasks: dict[object, asyncio.Task] = {}
        repair_tasks: dict[object, asyncio.Task] = {}
        retired_sessions: set[object] = set()
        failures: list[str] = []
        repairs = {"count": 0}

        async def detach_session(session) -> None:
            try:
                await _hard_wait(session.detach(), timeout=5)
            except Exception:
                pass

        async def retire_session(session) -> None:
            """统一退休成功/失败/repair-pop 的所有 session，不留孤儿 callback。"""
            if session is None or session in retired_sessions:
                return
            retired_sessions.add(session)
            _remove_tracked_callbacks(session_callbacks, session)
            await _disable_probe_instrumentation(
                session, session_instrumentation.pop(session, None))
            await detach_session(session)

        async def _install_one(page, *, quiet: bool) -> bool:
            session, ok, detail = await install_on_page(
                context, page, script, receive,
                task_tracker=receive_tasks,
                navigation_task_tracker=navigation_tasks,
                session_tracker=all_sessions,
                callback_tracker=session_callbacks,
                instrumentation_tracker=session_instrumentation,
                accepting=lambda: recording["on"],
                on_admitted=mark_admitted,
                stopping=lambda: stopping["on"])
            if stopping["on"]:
                await retire_session(session)
                return False
            if ok:
                installed[page] = session
                async with page_io_lock_for(page):
                    if not stopping["on"] and installed.get(page) is session:
                        await recorder.snapshot(
                            session, reason="attached", page_id=page_id_for(page),
                            page=page)
                if not quiet:
                    print("  [ok] 已挂上监听：%s"
                          % (_safe_url(page.url) or "(新标签页)"))
                return True
            failures.append(detail)
            await retire_session(session)
            if not quiet:
                print("  [!] 挂不上监听（%s）：%s"
                      % (_safe_url(page.url) or "未知页面", detail))
            return False

        def start_install(page, *, quiet: bool = False) -> asyncio.Task | None:
            if stopping["on"] or page in installed:
                return None
            repair = repair_tasks.get(page)
            if repair is not None and not repair.done():
                return None
            current = installation_tasks.get(page)
            if current is not None and not current.done():
                return current
            task = asyncio.create_task(_install_one(page, quiet=quiet))
            installation_tasks[page] = task
            background_tasks.add(task)

            def done(completed, target=page):
                background_tasks.discard(completed)
                if installation_tasks.get(target) is completed:
                    installation_tasks.pop(target, None)
                _consume_task_result(completed)

            task.add_done_callback(done)
            return task

        async def ensure_installed(page, *, quiet: bool = False) -> bool:
            if page in installed:
                return True
            task = start_install(page, quiet=quiet)
            if task is None:
                return page in installed
            try:
                return bool(await asyncio.shield(task))
            except asyncio.CancelledError:
                raise
            except Exception:
                return False

        async def repair_if_lost(page) -> None:
            """回读标记；掉了就地补装。**这是整套东西真正的安全网。**

            监听器会因为很多种原因消失：跨进程导航、装到一半页面就跳走了、
            SPA 把 window 上的东西清掉、Playwright 的 page 对象本身是坏的……
            与其逐一去猜是哪一种（2026-09-01 试过，scratch 环境复现不出来），
            不如**每隔几秒回读一次，掉了就补**。
            这样无论因为什么丢的，最多几秒钟就自己回来了。
            """
            if stopping["on"]:
                return
            session = installed.get(page)
            if session is None:
                return
            retire = False
            async with page_io_lock_for(page):
                if stopping["on"] or installed.get(page) is not session:
                    return
                try:
                    probe = await asyncio.wait_for(
                        session.send("Runtime.evaluate", {
                            "expression": "typeof window.%s" % REGISTRY_NAME,
                            "returnByValue": True,
                        }), timeout=8)
                except Exception:
                    if installed.get(page) is session:
                        installed.pop(page, None)
                    retire = True
                else:
                    if (probe.get("result") or {}).get("value") == "object":
                        return
                    try:
                        await asyncio.wait_for(
                            session.send("Runtime.evaluate", {
                                "expression": script,
                                "returnByValue": True,
                            }), timeout=8)
                        repairs["count"] += 1
                        print("  [~] 监听器掉了，已就地补装：%s"
                              % (_safe_url(page.url) or "当前页面"))
                    except Exception:
                        if installed.get(page) is session:
                            installed.pop(page, None)
                        retire = True
            if retire:
                await retire_session(session)

        def start_repair(page) -> asyncio.Task | None:
            if stopping["on"] or page not in installed:
                return None
            current = repair_tasks.get(page)
            if current is not None and not current.done():
                return current
            task = asyncio.create_task(repair_if_lost(page))
            repair_tasks[page] = task
            background_tasks.add(task)

            def done(completed, target=page):
                background_tasks.discard(completed)
                if repair_tasks.get(target) is completed:
                    repair_tasks.pop(target, None)
                _consume_task_result(completed)

            task.add_done_callback(done)
            return task

        async def observe_page(page, *, reason: str = "periodic") -> None:
            session = installed.get(page)
            if session is None or stopping["on"]:
                return
            async with page_io_lock_for(page):
                if stopping["on"] or installed.get(page) is not session:
                    return
                await recorder.snapshot(
                    session, reason=reason, page_id=page_id_for(page), page=page)

        def start_observation(page, *, reason: str = "periodic") -> asyncio.Task | None:
            """每页最多一条语义采样；慢页期间的新 tick 自动合并，不堆队列。"""
            if stopping["on"] or page not in installed:
                return None
            current = observation_tasks.get(page)
            if current is not None and not current.done():
                return current
            task = asyncio.create_task(observe_page(page, reason=reason))
            observation_tasks[page] = task
            background_tasks.add(task)

            def done(completed, target=page):
                background_tasks.discard(completed)
                if observation_tasks.get(target) is completed:
                    observation_tasks.pop(target, None)
                _consume_task_result(completed)

            task.add_done_callback(done)
            return task

        observation_scheduler["fn"] = start_observation

        initial_pages = list(context.pages)
        if initial_pages:
            await asyncio.gather(
                *(ensure_installed(page) for page in initial_pages),
                return_exceptions=True)

        def _on_new_page(page):
            if not stopping["on"]:
                start_install(page, quiet=False)

        context.on("page", _on_new_page)

        # 每 0.5 秒按单调时钟**发起**一次轻量语义观察，尽量留下提交后的短暂
        # toast/Boost 建议；每页最多一条在途，慢页会合并 tick 而不会排成长队。
        # 每 4 轮发起一次监听修复与 CDP target 核对；它们同样不阻塞下一轮采样。
        stop_sweep = asyncio.Event()
        missing_warned = {"at": -1}
        target_check_task = {"task": None}

        async def check_targets() -> None:
            targets = await cdp_page_targets(port)
            # 只在数字变化时说一次，别每轮刷一屏
            if len(targets) > len(installed) != missing_warned["at"]:
                missing_warned["at"] = len(installed)
                print("  [!] 浏览器有 %d 个页面，但只挂上了 %d 个监听。"
                      % (len(targets), len(installed)))
                print("      没挂上的那个页面**不会被记录**。"
                      "在它上面按 F5 刷新一次通常就能补上。")

        def start_target_check() -> None:
            current = target_check_task["task"]
            if stopping["on"] or (current is not None and not current.done()):
                return
            task = asyncio.create_task(check_targets())
            target_check_task["task"] = task
            background_tasks.add(task)

            def done(completed):
                background_tasks.discard(completed)
                if target_check_task["task"] is completed:
                    target_check_task["task"] = None
                _consume_task_result(completed)

            task.add_done_callback(done)

        async def sweep() -> None:
            cycle = 0
            loop = asyncio.get_running_loop()
            next_tick = loop.time()
            while not stop_sweep.is_set():
                next_tick += 0.5
                try:
                    await asyncio.wait_for(
                        stop_sweep.wait(),
                        timeout=max(0.0, next_tick - loop.time()))
                    return
                except asyncio.TimeoutError:
                    pass
                cycle += 1
                maintenance = cycle % 4 == 0
                try:
                    for page in list(context.pages):
                        if page not in installed:
                            start_install(page, quiet=True)
                            continue
                        if maintenance:
                            start_repair(page)
                        start_observation(page, reason="periodic")
                    if maintenance:
                        start_target_check()
                except Exception:
                    pass

        sweeper = asyncio.create_task(sweep())

        if not installed:
            print("\n[!] **一个页面都没能挂上监听——现在开始走流程会全部记录不到。**")
            print("    先在发布 Chrome 里按 F5 刷新一次那个标签页，再重跑本工具。")
            for detail in failures[:3]:
                print("    原因：%s" % detail)

        print("\nG1 探查已开始：%s" % recorder.output_path)
        print("已挂上监听的页面：%d 个" % len(installed))
        print("模式：只记录，不驱动。工具不会打开 URL、点击、填写、上传或提交。")
        print("请先确认 DE 发布账号已经人工登录；不要在探查运行期间输入账号密码。")
        print("请在发布专用 Chrome 里手工走完整流程；每次 click/input/change/submit 都会截图。")
        print("⚠️ 边走边看这里：每记录一条会累加计数。**长时间不动就是没记上**，")
        print("   那时先按 F5 刷新页面，而不是把整个流程走完才发现是空的。")
        print("不要关闭这个终端。完成后回到这里按 Enter 停止记录。")
        await _wait_for_stop_enter("\n按 Enter 停止记录：")
        print("\n已收到 Enter，正在停止监听并保存最终证据……")
        # **先关闸**：不再接受任何新事件。旧实现先睡 0.9 秒，又让更多截图任务
        # 排进同一把锁；真机上每张 Playwright 图超时 8 秒，Enter 看起来永远无效。
        recording["on"] = False
        stopping["on"] = True
        # 先切断所有会产生新工作的入口：context 新页、binding、导航补注入。
        _remove_emitter_listener(context, "page", _on_new_page)
        for session in list(all_sessions):
            _remove_tracked_callbacks(session_callbacks, session)
        stop_sweep.set()
        sweeper.cancel()
        stubborn_background = await _cancel_tasks(
            {sweeper, *background_tasks, *navigation_tasks}, timeout=3.0)
        if stubborn_background:
            print("  [!] %d 个维护任务未在 3 秒内响应取消；已与后续证据隔离。"
                  % stubborn_background)
        # Enter 前已经到达 Python 的少量事件可以完成；若某张图仍挂住，5 秒后取消，
        # 绝不让最终快照和 finished_at 无限排队。此时先保留页面 mask，避免一条
        # 已接纳截图正在 capture 时，被停止清理提前撤掉遮罩而拍到输入内容。
        drained, cancelled = await _drain_tasks(receive_tasks, timeout=5.0)
        if cancelled:
            print("  [!] %d 个超时的交互截图任务已取消；其余证据已保留。" % cancelled)
        elif drained:
            print("  [ok] 已收尾 %d 个 Enter 前到达的交互任务。" % drained)
        # 即使有抗拒取消的旧 task，它恢复后也不能再追加/占用最终证据边界；
        # 从这里起只允许下面显式 reason=final 的快照落盘。
        recorder.seal_for_final()
        # receive 已完成/取消后，再移除所有曾创建 session 的 listener/timer/mask、
        # init script 与 binding；半初始化失败/repair 弹出的 session 也不能成为孤儿。
        await asyncio.gather(
            *(_disable_probe_instrumentation(
                session, session_instrumentation.pop(session, None))
              for session in list(all_sessions)),
            return_exceptions=True)
        # 用户按计划应当把“提交成功后的最终页面/内容日历卡片”留在屏幕上再停止。
        # 这条 final snapshot 带遮罩截图；即使语义与上一轮相同也强制保留。
        evidence_rows = [
            *(recorder.data.get("interactions") or []),
            *(recorder.data.get("snapshots") or []),
        ]
        latest_page_id = ""
        if last_admitted_page["page"] is not None:
            latest_page_id = page_id_for(last_admitted_page["page"])
        elif evidence_rows:
            latest_page_id = str(max(
                evidence_rows,
                key=lambda row: int(row.get("evidence_order") or 0),
            ).get("page_id") or "")
        final_pages = sorted(
            [(page, session) for page, session in installed.items()
             if session not in retired_sessions],
            key=lambda item: page_id_for(item[0]) != latest_page_id)

        # 只给最后一条获准真人交互所在页做强制 final；其它标签页已有周期证据，
        # 不应分享/耗尽主页面的预算。
        primary_final_pages = final_pages[:1]

        async def capture_finals() -> None:
            for page, session in primary_final_pages:
                try:
                    await recorder.snapshot(
                        session, reason="final", screenshot=True,
                        page_id=page_id_for(page), page=page)
                except Exception:
                    pass

        try:
            # 最近真人交互页优先；即使底层吞掉 CancelledError，硬 deadline 也返回。
            final_completed, _ = await _hard_wait(capture_finals(), timeout=10)
        except Exception:
            final_completed = True  # 已结束但失败；各页 snapshot 自身会保留错误信息
        if not final_completed:
            print("  [!] 主页面 final 快照超过 10 秒，已停止等待；已完成的证据仍保留。")

        # final 之后不再允许任何迟到 task 追加 evidence；即使它稍后从被取消的
        # screenshot await 恢复，finished_at 也仍然是文件里的最后状态边界。
        recorder.close_admission()
        await asyncio.gather(
            *(_drop_probe_masks(session) for session in list(all_sessions)),
            return_exceptions=True)
        for session in list(all_sessions):
            _remove_tracked_callbacks(session_callbacks, session)
        await asyncio.gather(
            *(detach_session(session) for session in list(all_sessions)),
            return_exceptions=True)
        print("已停止记录，共 %d 条交互。" % len(recorder.data["interactions"]))

        # ⚠️ 空 dump 必须当场说清楚。用户上一次就是走完整个流程才发现是空的。
        if not recorder.data["interactions"]:
            print("\n[!] **这一轮一条交互都没记录到。**")
            print("    这份 dump 不能用来回填 selectors.py，请不要拿它当验收材料。")
            print("    最常见的原因：附着时那个标签页早就打开着，Playwright 拿不到它的帧树。")
            print("    做法：在发布 Chrome 里按 F5 刷新一次要操作的页面，再重跑本工具。")
            if failures:
                print("    本轮挂载失败原因：%s" % failures[0])

        if collect_notes:
            await _ask_observations(recorder.set_observations)
        else:
            _print_notes_hint(recorder.output_path)
        return recorder.output_path
    finally:
        recorder.close_admission()
        await recorder.finish()
        # attach() 连的是用户自己的发布 Chrome。只断开 Playwright，绝不关浏览器。
        if pw is not None:
            try:
                await _hard_wait(pw.stop(), timeout=5)
            except Exception:
                pass
        print("探查记录已保存：%s" % recorder.output_path)
        print("逐步截图目录：%s" % recorder.screenshot_dir)
        print("下一步先人工复核 dump，再回填 selectors.py；不要从截图猜 CSS。")


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="记录发布专用 Chrome 里的人工交互（不驱动页面）")
    parser.add_argument(
        "--notes", action="store_true",
        help="录完就地问那 20 个观察项。**默认不问** —— "
             "录交互和量 UI 边界是两件事，捆在一起只会逼人一路回车跳过")
    parser.add_argument(
        "--fill-notes", metavar="DUMP",
        help="只给某份已有 dump 补填观察项，不连浏览器、不录制")
    parser.add_argument(
        "--set-note", action="append", metavar="KEY=VALUE", default=None,
        help="非交互地写一条观察项，可重复。20 项里大多数只能人亲眼看 UI 得到，"
             "但有几项（入口 URL / 成功信号 / 日期时间输入方式）dump 里本来就"
             "证明得了，逐条问一遍纯属浪费。**--set-note 只是输入方式，"
             "不放松任何判据**：写进去的字符串照样要被人复核")
    parser.add_argument(
        "--no-notes", action="store_true",
        help=argparse.SUPPRESS)      # 兼容旧写法；现在本来就是默认行为
    return parser.parse_args(argv)


def _run_with_bounded_shutdown(awaitable):
    """probe 专用 runner：关环时也不对抗拒 cancel 的第三方 task 做无界 gather。"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(awaitable)
    finally:
        pending = {task for task in asyncio.all_tasks(loop) if not task.done()}
        for task in pending:
            task.cancel()
        if pending:
            async def settle() -> None:
                await asyncio.wait(pending, timeout=0.5)
            try:
                loop.run_until_complete(settle())
            except Exception:
                pass
        # 极端情况下仍抗拒取消的 task 不应阻止进程退出；正常路径这里为空。
        remaining = {task for task in asyncio.all_tasks(loop) if not task.done()}
        for task in remaining:
            if not task.done():
                setattr(task, "_log_destroy_pending", False)
        asyncio.set_event_loop(None)
        loop.close()


def main(argv=None) -> int:
    force_utf8()
    args = _parse_args(argv)
    if args.fill_notes:
        return asyncio.run(fill_notes(
            Path(args.fill_notes), preset=args.set_note))
    _run_with_bounded_shutdown(
        run_probe(collect_notes=args.notes and not args.no_notes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
