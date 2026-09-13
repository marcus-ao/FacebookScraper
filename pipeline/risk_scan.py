"""真实付费模型的英文语义风险预扫，以及可审计的状态视图。"""
from __future__ import annotations

from core.config import cfg
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import translate
from core import paid_model, paid_requests


ROOT = Path(__file__).resolve().parents[1]
PROMPT_PATH = ROOT / "prompts" / "risk_scan_en.md"
PROMPT_VERSION = 1
STATE_NAME = "risk_scans.jsonl"
RISK_KINDS = frozenset({"pun", "ambiguous", "us_only"})


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _prompt(path: Path) -> tuple[str, str]:
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError("风险预扫提示词为空")
    return text, _digest(text)


def _state_path(state_dir: Path) -> Path:
    return Path(state_dir) / STATE_NAME


def _load(state_dir: Path) -> list[dict]:
    path = _state_path(state_dir)
    if not path.is_file():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and isinstance(row.get("task_id"), str):
            rows.append(row)
    return rows


def _base_view(source_text: str, prompt_sha256: str) -> dict:
    return {
        "status": "not_scanned", "risks": [],
        "source_text_sha256": translate.source_text_sha256(source_text),
        "scan_text_sha256": _digest(source_text),
        "prompt_sha256": prompt_sha256, "prompt_version": PROMPT_VERSION,
        "source": None, "scanned_at": None,
        "message": "尚未进行英文语义风险预扫；请人工检查双关、歧义和美国特定表达。",
    }


def current_view(state_dir: Path, task_id: str, source_text: str, *,
                 prompt_path: Path = PROMPT_PATH) -> dict:
    """区分未扫、失败、完成和失效；失效结果绝不继续画在新源文上。"""
    _text, prompt_sha256 = _prompt(prompt_path)
    view = _base_view(source_text, prompt_sha256)
    latest = next((row for row in reversed(_load(state_dir))
                   if row.get("task_id") == task_id), None)
    if latest is None:
        return view
    current_source = view["source_text_sha256"]
    if (latest.get("source_text_sha256") != current_source
            or latest.get('scan_text_sha256', latest.get('source_text_sha256')) != view['scan_text_sha256']
            or latest.get("prompt_sha256") != prompt_sha256
            or latest.get("prompt_version") != PROMPT_VERSION):
        view.update({
            "status": "stale", "source": latest.get("source"),
            "scanned_at": latest.get("scanned_at"),
            "previous_status": latest.get("status"),
            "previous_risk_count": len(latest.get("risks") or []),
            "message": "源文或风险提示词版本已变化，旧扫描不再适用；请重新预扫或人工复核。",
        })
        return view
    status = latest.get("status")
    if status not in {"completed", "failed"}:
        return view
    view.update({
        "status": status, "source": latest.get("source"),
        "scanned_at": latest.get("scanned_at"),
        "message": latest.get("message") or (
            "风险预扫完成；未发现这三类风险。" if status == "completed" and not latest.get("risks")
            else "风险预扫完成，请逐项人工判断。" if status == "completed"
            else "风险预扫失败，不能据此声称安全；请人工审校。"),
        "risks": list(latest.get("risks") or []) if status == "completed" else [],
    })
    return view


def result_for(account_dir: Path, source: dict, *, state_dir: Path | None = None,
               prompt_path: Path = PROMPT_PATH) -> dict:
    """供 Web、通知和阶段状态复用的只读结果；不会触发模型或写文件。"""
    if not isinstance(source, dict) or not isinstance(source.get("post_id"), str):
        raise ValueError("风险结果需要带 post_id 的源帖")
    if state_dir is None:
        state_dir = cfg().state_dir
    task_id = "%s/%s" % (Path(account_dir).name, source["post_id"])
    return current_view(state_dir, task_id, str(source.get("text") or ""),
                        prompt_path=prompt_path)


def parse_response(value: str, source_text: str) -> list[dict]:
    text = str(value).strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.split("\n", 1)[1][:-3].strip() if "\n" in text else ""
    data = json.loads(text)
    if not isinstance(data, dict) or set(data) != {"risks"} or not isinstance(data["risks"], list):
        raise ValueError("风险模型须返回仅含 risks 数组的 JSON 对象")
    result: list[dict] = []
    for item in data["risks"]:
        if not isinstance(item, dict) or item.get("kind") not in RISK_KINDS:
            raise ValueError("风险模型返回了范围外分类")
        start, end = item.get("start"), item.get("end")
        quote, label = item.get("quote"), item.get("label")
        if (not isinstance(start, int) or isinstance(start, bool)
                or not isinstance(end, int) or isinstance(end, bool)
                or not 0 <= start < end <= len(source_text)
                or not isinstance(quote, str) or source_text[start:end] != quote
                or not isinstance(label, str) or not label.strip()):
            raise ValueError("风险位置未与英文源文精确绑定")
        result.append({"kind": item["kind"], "en_span": [start, end],
                       "label": label.strip(), "quote": quote})
    return result


def _append(state_dir: Path, row: dict) -> None:
    path = _state_path(state_dir)
    with paid_model.FileLock(path.with_suffix(".lock"), busy_message="风险扫描状态正在更新"):
        paid_model.append_jsonl(path, row)


def scan_source(state_dir: Path, *, task_id: str, source_ref: str, source_text: str,
                controller: paid_requests.RequestController, caller=None,
                prompt_path: Path = PROMPT_PATH,
                now: datetime | None = None) -> dict:
    """在同一预算控制器中执行一次风险模型请求，并先落盘再闭合收据。"""
    existing = current_view(state_dir, task_id, source_text, prompt_path=prompt_path)
    if existing["status"] in {"completed", "failed"}:
        return existing
    prompt, prompt_sha256 = _prompt(prompt_path)
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    # Risk coordinates and model input bind to the exact raw UTF-8 text.  The
    # translation digest strips outer whitespace and cannot protect offsets.
    scan_text_sha256 = _digest(source_text)
    source = {"kind": "paid_model", "provider": "unknown", "model": "unknown"}
    row: dict[str, Any] = {
        "schema_version": 1, "task_id": task_id, "source_ref": source_ref,
        "source_text_sha256": translate.source_text_sha256(source_text),
        "scan_text_sha256": scan_text_sha256, "prompt_sha256": prompt_sha256,
        "prompt_version": PROMPT_VERSION, "source": source,
        "scanned_at": moment.isoformat(), "actor": None,
    }
    job_key = "risk-scan:" + _digest("\0".join((task_id, scan_text_sha256, prompt_sha256)))
    receipt = None
    try:
        if caller is None:
            caller = translate.Translator(translate.Settings())
        settings = caller.s
        source.update(provider=str(getattr(settings, "provider", "unknown")),
                      model=str(getattr(settings, "model", "unknown")))
        row["source"] = source
        # Translator constructs credentials/client lazily.  Validate locally
        # before RequestController writes `started`, because a missing key did
        # not make a provider request and must not become an uncertain charge.
        if getattr(type(caller), "client", None) is not None:
            _ = caller.client
        response, receipt = controller.run(
            stage="risk_scan", job_key=job_key, source_ref=source_ref,
            media_index=None, model=source["model"],
            request=lambda: caller.translate(source_text, prompt),
            usage_getter=lambda: caller.last_usage,
            usage_errors=translate.translation_usage_errors,
            usage_cost=lambda usage: translate.usage_cost_upper_bound(settings, dict(usage)))
        risks = parse_response(response, source_text)
        row.update(status="completed", risks=risks,
                   message=("风险预扫完成，请逐项人工判断。" if risks
                            else "风险预扫完成；未发现双关、歧义或美国特定表达。"),
                   paid_request_id=receipt.request_id)
        _append(state_dir, row)
        controller.finalize(receipt, accepted=True, reason="risk scan fsynced")
    except (Exception, SystemExit) as exc:  # 扫描失败必须可见，但不伪装为空风险。
        row.update(status="failed", risks=[], error=type(exc).__name__,
                   message="风险预扫失败，不能据此声称安全；请人工审校。")
        try:
            _append(state_dir, row)
        finally:
            if receipt is not None:
                controller.finalize(receipt, accepted=False,
                                    reason="risk scan output contract failed")
    return current_view(state_dir, task_id, source_text, prompt_path=prompt_path)
