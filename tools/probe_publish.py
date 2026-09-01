r"""G1 Business Suite DOM 探查：只记录用户交互，不驱动页面。

工具附着到 ``[publish]`` 的 Chrome，在现有/新开标签页里监听用户自己的
click / input / change / submit。它不会打开 URL、点击、填写、上传或提交。
每条记录只保留抗构建期混淆的属性，不记录 class/CSS path，也不读取 cookie。
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import http.client
import json
import sys
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
# 只用于 screenshot 的通用 HTML 凭据遮罩，不是 Business Suite 流程定位器，
# 不参与点击/填写/导航，也不代表任何真实 DOM 探查结论。
_SENSITIVE_INPUT_SELECTOR = (
    'input[type="password"], input[type="email"], '
    'input[autocomplete~="username"], input[autocomplete~="current-password"], '
    'input[autocomplete~="new-password"], input[autocomplete~="one-time-code"]'
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
    "accept": 500,
}
_ELEMENT_BOOL_FIELDS = {
    "visible_text_truncated",
    "is_contenteditable",
    "multiple",
    "checked",
    "disabled",
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
    if input_type in {"password", "hidden"}:
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

# 这个函数只安装事件监听器。刻意没有 querySelector 驱动、click/fill/goto，
# 更没有 className/CSS path。点击到 span 时把语义祖先链一起记下，避免按钮的
# role/aria-label 在父层而 event.target 恰好是内层图标。
INSTALL_FUNCTION = r"""({sessionId, bindingName}) => {
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
      if (node) chunks.push(node.innerText || node.textContent || "");
    }
    return compact(chunks.join(" ")).text;
  };

  const associatedLabelText = (element) => {
    const chunks = [];
    if (element.labels) {
      for (const label of element.labels) {
        chunks.push(label.innerText || label.textContent || "");
      }
    }
    const parentLabel = element.closest?.("label");
    if (parentLabel) chunks.push(parentLabel.innerText || parentLabel.textContent || "");
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

  const describe = (element, depth = 0) => {
    if (!(element instanceof Element)) return null;
    const tag = String(element.tagName || "").toLowerCase();
    const explicitRole = element.getAttribute("role") || "";
    const ariaLabel = element.getAttribute("aria-label") || "";
    const labelled = labelledByText(element);
    const label = associatedLabelText(element);
    let visible = element.innerText || element.textContent || "";
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
      accessible_name: accessibleName.text,
      accessible_name_source: ariaLabel ? "aria-label" :
        (labelled ? "aria-labelledby" :
          (label ? "label" :
            (placeholder ? "placeholder" : "visible-text"))),
      is_contenteditable: Boolean(element.isContentEditable),
      contenteditable: element.getAttribute("contenteditable") || "",
      input_type: type,
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
    let depth = 0;
    while (element && depth < 8) {
      const tag = String(element.tagName || "").toLowerCase();
      if (["body", "html"].includes(tag)) break;
      const item = describe(element, depth);
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


async def _cdp_screenshot(session, path: Path) -> tuple[bool, str]:
    """Playwright 截不了时用 CDP 兜底，**并且保住敏感输入的遮罩**。

    Playwright 的 ``mask=`` 在这条路上用不了，所以先临时插一条 CSS 把
    密码/邮箱/OTP 类输入模糊掉，截完再撤掉。**不能因为换了通路就把隐私边界丢了**——
    `PUBLISH_PLAN` 的探查隐私要求对两条路径同样成立。
    """
    add_mask = (
        "(() => { const s = document.createElement('style');"
        " s.id = %s;"
        " s.textContent = %s + '{filter:blur(12px)!important}';"
        " document.documentElement.appendChild(s); })()"
        % (json.dumps(_MASK_STYLE_ID), json.dumps(_SENSITIVE_INPUT_SELECTOR)))
    drop_mask = (
        "(() => { const s = document.getElementById(%s);"
        " if (s) s.remove(); })()" % json.dumps(_MASK_STYLE_ID))
    try:
        await asyncio.wait_for(
            session.send("Runtime.evaluate",
                         {"expression": add_mask, "returnByValue": True}),
            timeout=5)
    except Exception:
        return False, "插入遮罩样式失败，为免泄露敏感输入放弃截图"
    try:
        shot = await asyncio.wait_for(
            session.send("Page.captureScreenshot", {"format": "png"}),
            timeout=10)
        data = shot.get("data")
        if not isinstance(data, str) or not data:
            return False, "CDP 没有返回图片数据"
        path.write_bytes(base64.b64decode(data))
        return True, "ok"
    except Exception as exc:
        return False, "%s: %s" % (type(exc).__name__, str(exc).splitlines()[0][:80])
    finally:
        try:
            await asyncio.wait_for(
                session.send("Runtime.evaluate",
                             {"expression": drop_mask, "returnByValue": True}),
                timeout=5)
        except Exception:
            pass


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
        # 没有 _counter：序号一律从 len(interactions) 推（CR-67，见 record()）。
        self.data = {
            "schema_version": 1,
            "session_id": self.session_id,
            "started_at": _utc_iso(),
            "finished_at": None,
            "cdp_port": int(port),
            "profile_dir": str(profile),
            "mode": "record-only",
            "privacy": (
                "只保留白名单稳定属性；不记录 cookie/输入值/CSS/class，"
                "敏感输入不产生事件且截图遮罩，URL 去掉 query/fragment"),
            "interactions": [],
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

    async def record(self, page, payload: object, session=None) -> bool:
        payload = _safe_payload(payload, self.session_id)
        if payload is None:
            return False
        async with self._lock:
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
            try:
                masks = [page.locator(_SENSITIVE_INPUT_SELECTOR)]
                # ⚠️ 显式短超时：帧树坏掉的页面上 Playwright 截图会一直挂着，
                # 而 record 是持锁的 —— 一次挂住就把后面所有事件堵死（CR-64）。
                await page.screenshot(
                    path=str(screenshot), full_page=False, mask=masks,
                    timeout=8000)
            except Exception as exc:  # 页面恰在导航/关闭时，记录失败但不丢交互本身
                screenshot_error = "%s: %s" % (type(exc).__name__, exc)
                if session is not None:
                    ok, detail = await _cdp_screenshot(session, screenshot)
                    screenshot_error = None if ok else "%s；CDP 回退也失败：%s" % (
                        screenshot_error, detail)

            record = dict(payload)
            record["sequence"] = sequence
            record["recorded_at"] = _utc_iso()
            record["screenshot"] = str(screenshot)
            record["screenshot_error"] = screenshot_error
            record["playwright_page_url"] = _safe_url(getattr(page, "url", ""))
            self.data["interactions"].append(record)
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

    async def finish(self) -> None:
        async with self._lock:
            self.data["finished_at"] = _utc_iso()
            self._write()


async def _read_line(prompt: str) -> str:
    try:
        return console_text(await asyncio.to_thread(input, prompt)).strip()
    except EOFError:
        return ""


REGISTRY_NAME = "__fbscraperPublishProbeHandlers"
_INSTALL_TIMEOUT = 15.0


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
                          on_payload) -> tuple[object | None, bool, str]:
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

    def _on_binding(params):
        if params.get("name") != BINDING_NAME:
            return
        asyncio.create_task(on_payload(page, params.get("payload")))

    try:
        session.on("Runtime.bindingCalled", _on_binding)
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
        # 覆盖后续文档与新建的 frame（含跨源 iframe）。
        await asyncio.wait_for(
            session.send("Page.addScriptToEvaluateOnNewDocument",
                         {"source": script}),
            timeout=_INSTALL_TIMEOUT)
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
        if frame.get("parentId"):
            return                      # 只管主帧，子帧由 init script 覆盖
        asyncio.create_task(_reinject(session, script))

    session.on("Page.frameNavigated", _on_navigated)
    return session, True, "ok"


async def _reinject(session, script: str) -> None:
    """导航后补注入。失败不抛——巡检那一层还会再兜一次。"""
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


async def fill_notes(dump_path: Path) -> int:
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
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        print("[!] 不是 record-only v1 契约的 dump，拒绝改动。")
        return 1

    recorder = ProbeRecorder.__new__(ProbeRecorder)   # 只借它的校验与原子写
    recorder.output_path = dump_path
    recorder.data = data
    recorder._lock = asyncio.Lock()
    print("dump：%s（%d 条交互）"
          % (dump_path, len(data.get("interactions") or [])))
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

        async def receive(page, payload):
            if not recording["on"]:
                return
            try:
                if await recorder.record(page, payload,
                                         session=installed.get(page)):
                    # ⚠️ 逐条回显。上一次用户走完整个流程才发现 dump 是空的——
                    # 只要屏幕上不再跳数字，当场就知道没记上（CR-64）。
                    print("  #%d %s" % (len(recorder.data["interactions"]),
                                        _safe_text(
                                            (_safe_payload(payload, recorder.session_id)
                                             or {}).get("event_type"), 12) or "?"))
            except Exception:
                # 记录一条失败不能让后面全都收不到（binding 回调里抛异常会静默断链）
                pass

        script = install_script(recorder.session_id)
        # ⚠️ 用 page 对象本身做键，**不要用 id(page)**：id 会在对象被回收后重用，
        # 那样一个新页面可能被当成"已经装过了"而被跳过。
        installed: dict[object, object] = {}        # page -> cdp session
        failures: list[str] = []
        repairs = {"count": 0}

        async def ensure_installed(page, *, quiet: bool = False) -> bool:
            if page in installed:
                return True
            session, ok, detail = await install_on_page(
                context, page, script, receive)
            if ok:
                installed[page] = session
                if not quiet:
                    print("  [ok] 已挂上监听：%s" % (_safe_url(page.url) or "(新标签页)"))
                return True
            failures.append(detail)
            if not quiet:
                print("  [!] 挂不上监听（%s）：%s"
                      % (_safe_url(page.url) or "未知页面", detail))
            return False

        async def repair_if_lost(page) -> None:
            """回读标记；掉了就地补装。**这是整套东西真正的安全网。**

            监听器会因为很多种原因消失：跨进程导航、装到一半页面就跳走了、
            SPA 把 window 上的东西清掉、Playwright 的 page 对象本身是坏的……
            与其逐一去猜是哪一种（2026-09-01 试过，scratch 环境复现不出来），
            不如**每隔几秒回读一次，掉了就补**。
            这样无论因为什么丢的，最多几秒钟就自己回来了。
            """
            session = installed.get(page)
            if session is None:
                return
            try:
                probe = await asyncio.wait_for(
                    session.send("Runtime.evaluate",
                                 {"expression": "typeof window.%s" % REGISTRY_NAME,
                                  "returnByValue": True}),
                    timeout=8)
            except Exception:
                installed.pop(page, None)      # 会话废了，下一轮当新页面重装
                return
            if (probe.get("result") or {}).get("value") == "object":
                return
            try:
                await asyncio.wait_for(
                    session.send("Runtime.evaluate",
                                 {"expression": script, "returnByValue": True}),
                    timeout=8)
                repairs["count"] += 1
                print("  [~] 监听器掉了，已就地补装：%s"
                      % (_safe_url(page.url) or "当前页面"))
            except Exception:
                installed.pop(page, None)

        for page in list(context.pages):
            await ensure_installed(page)

        def _on_new_page(page):
            asyncio.create_task(ensure_installed(page))

        context.on("page", _on_new_page)

        # 每 2 秒巡检一次：① 补装新页面 ② **回读校验并补装掉了的监听器**
        # ③ 核对 Playwright 有没有漏掉 CDP 能看见的页面。
        # ②是关键：不管监听器因为什么原因消失（跨进程导航、装到一半页面跳走、
        # 页面对象本身是坏的），最多 2 秒就自己回来，用户不需要知道为什么。
        stop_sweep = asyncio.Event()
        missing_warned = {"at": -1}

        async def sweep() -> None:
            while not stop_sweep.is_set():
                try:
                    await asyncio.wait_for(stop_sweep.wait(), timeout=2.0)
                    return
                except asyncio.TimeoutError:
                    pass
                try:
                    for page in list(context.pages):
                        if page in installed:
                            await repair_if_lost(page)
                        else:
                            await ensure_installed(page, quiet=True)
                    targets = await cdp_page_targets(port)
                    # 只在数字变化时说一次，别每 2 秒刷一屏
                    if len(targets) > len(installed) != missing_warned["at"]:
                        missing_warned["at"] = len(installed)
                        print("  [!] 浏览器有 %d 个页面，但只挂上了 %d 个监听。"
                              % (len(targets), len(installed)))
                        print("      没挂上的那个页面**不会被记录**。"
                              "在它上面按 F5 刷新一次通常就能补上。")
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
        await _read_line("\n按 Enter 停止记录：")
        stop_sweep.set()
        sweeper.cancel()
        # 让最后一次 input 的 700ms 去抖有机会落盘；不触碰页面状态。
        await asyncio.sleep(0.9)
        # **到这里才是真的停。** 顺序不能反：先给去抖留时间，再关闸、断会话，
        # 否则最后一次输入会丢。断开会话之后浏览器不再回送事件，
        # 后面无论问不问观察项，都不会再有人跟 set_observations 抢那把锁。
        recording["on"] = False
        for session in list(installed.values()):
            try:
                await asyncio.wait_for(session.detach(), timeout=5)
            except Exception:
                pass
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
        await recorder.finish()
        if browser is not None:
            try:
                await browser.close()
            finally:
                if pw is not None:
                    await pw.stop()
        elif pw is not None:
            await pw.stop()
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
        "--no-notes", action="store_true",
        help=argparse.SUPPRESS)      # 兼容旧写法；现在本来就是默认行为
    return parser.parse_args(argv)


def main(argv=None) -> int:
    force_utf8()
    args = _parse_args(argv)
    if args.fill_notes:
        return asyncio.run(fill_notes(Path(args.fill_notes)))
    asyncio.run(run_probe(collect_notes=args.notes and not args.no_notes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
