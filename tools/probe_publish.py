r"""G1 Business Suite DOM 探查：只记录用户交互，不驱动页面。

工具附着到 ``[publish]`` 的 Chrome，在现有/新开标签页里监听用户自己的
click / input / change / submit。它不会打开 URL、点击、填写、上传或提交。
每条记录只保留抗构建期混淆的属性，不记录 class/CSS path，也不读取 cookie。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.chrome import attach                             # noqa: E402
from core.config import cfg                                # noqa: E402
from core.console import force_utf8                        # noqa: E402

BINDING_NAME = "__fbscraperPublishProbeRecord"

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
      const item = describe(element, depth);
      if (item) result.push(item);
      if (["body", "html"].includes(String(element.tagName || "").toLowerCase())) break;
      element = element.parentElement;
      depth += 1;
    }
    return result;
  };

  const emit = (eventType, target) => {
    const binding = window[bindingName];
    if (typeof binding !== "function") return;
    const chain = candidates(target);
    const payload = {
      session_id: sessionId,
      event_type: eventType,
      client_timestamp: new Date().toISOString(),
      page_url: window.location.href,
      document_title: document.title,
      target: chain[0] || null,
      candidates: chain,
    };
    Promise.resolve(binding(payload)).catch(() => {});
  };

  const timers = new Map();
  const handlers = {
    click: (event) => emit("click", event.target),
    change: (event) => emit("change", event.target),
    submit: (event) => emit("submit", event.submitter || document.activeElement || event.target),
    input: (event) => {
      const target = event.target;
      const old = timers.get(target);
      if (old) clearTimeout(old);
      timers.set(target, setTimeout(() => {
        timers.delete(target);
        emit("input", target);
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
        self._counter = 0
        self.data = {
            "schema_version": 1,
            "session_id": self.session_id,
            "started_at": _utc_iso(),
            "finished_at": None,
            "cdp_port": int(port),
            "profile_dir": str(profile),
            "mode": "record-only",
            "privacy": ("不记录 cookie、密码输入值或 CSS/class；visible_text 最长 500 字符"),
            "interactions": [],
            "observations": {},
        }
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self._write()

    def _write(self) -> None:
        temporary = self.output_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        temporary.replace(self.output_path)

    async def record(self, page, payload: object) -> None:
        if not isinstance(payload, dict):
            payload = {"event_type": "invalid-payload", "raw_type": type(payload).__name__}
        async with self._lock:
            self._counter += 1
            sequence = self._counter
            event_type = str(payload.get("event_type") or "interaction")
            safe_event = "".join(ch for ch in event_type if ch.isalnum() or ch in "-_")[:24]
            screenshot = self.screenshot_dir / (
                "%03d_%s.png" % (sequence, safe_event or "interaction"))
            screenshot_error = None
            try:
                await page.screenshot(path=str(screenshot), full_page=False)
            except Exception as exc:  # 页面恰在导航/关闭时，记录失败但不丢交互本身
                screenshot_error = "%s: %s" % (type(exc).__name__, exc)

            record = dict(payload)
            record["sequence"] = sequence
            record["recorded_at"] = _utc_iso()
            record["screenshot"] = str(screenshot)
            record["screenshot_error"] = screenshot_error
            record["playwright_page_url"] = getattr(page, "url", "")
            self.data["interactions"].append(record)
            self._write()

    async def set_observations(self, values: dict[str, str]) -> None:
        async with self._lock:
            self.data["observations"].update(
                {key: value for key, value in values.items() if value})
            self._write()

    async def finish(self) -> None:
        async with self._lock:
            self.data["finished_at"] = _utc_iso()
            self._write()


async def _read_line(prompt: str) -> str:
    try:
        return (await asyncio.to_thread(input, prompt)).strip()
    except EOFError:
        return ""


async def _install_existing(context, script: str) -> None:
    for page in list(context.pages):
        for frame in list(page.frames):
            try:
                await frame.evaluate(script)
            except Exception:
                # 某个 frame 正在导航/销毁不应终止整场；init script 会覆盖下一页。
                continue


async def run_probe(*, collect_notes: bool = True) -> Path:
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

        async def receive(source, payload):
            page = source.get("page")
            if page is not None:
                await recorder.record(page, payload)

        await context.expose_binding(BINDING_NAME, receive)
        script = install_script(recorder.session_id)
        await context.add_init_script(script=script)
        await _install_existing(context, script)

        print("G1 探查已开始：%s" % recorder.output_path)
        print("模式：只记录，不驱动。工具不会打开 URL、点击、填写、上传或提交。")
        print("请先确认 DE 发布账号已经人工登录；不要在探查运行期间输入账号密码。")
        print("请在发布专用 Chrome 里手工走完整流程；每次 click/input/change/submit 都会截图。")
        print("不要关闭这个终端。完成后回到这里按 Enter 停止记录。")
        await _read_line("\n按 Enter 停止记录：")
        # 让最后一次 input 的 700ms 去抖有机会落盘；不触碰页面状态。
        await asyncio.sleep(0.9)

        if collect_notes:
            print("\n下面只记你亲眼看到的结果；不知道就直接按 Enter，绝不猜。")
            prompts = {
                "business_suite_entry_url": "创建帖入口最终 URL：",
                "facebook_page_slug": "FB Page 地址栏 slug（只填账号名段）：",
                "ui_timezone": "日期/时间控件原样显示的时区字符串：",
                "schedule_min_ahead": "UI 实测最早可排多久之后：",
                "schedule_max_ahead": "UI 实测最晚可排多远：",
                "schedule_input_behavior": "日期/时间能直接输入还是必须点选：",
                "success_signal": "提交成功的明确信号（toast/跳转/列表项）：",
                "extra_notes": "其它观察：",
            }
            values = {}
            for key, prompt in prompts.items():
                values[key] = await _read_line(prompt)
            await recorder.set_observations(values)
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
        "--no-notes", action="store_true",
        help="结束时不询问时区/窗口等人工观察（交互 dump 仍照常保存）")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    force_utf8()
    args = _parse_args(argv)
    asyncio.run(run_probe(collect_notes=not args.no_notes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
