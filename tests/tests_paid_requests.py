from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import paid_model, paid_requests as P
from core.console import force_utf8
from pipeline import engine as A
from localize import images as I
from localize import text as T

force_utf8()
fails = []


def check(condition, message):
    print(("  OK   " if condition else "  FAIL ") + message)
    if not condition:
        fails.append(message)


def usage_errors(usage):
    return T.translation_usage_errors(usage)


def cost(usage):
    return float(usage["output_tokens"]) / 100.0


GOOD = {"input_tokens": 10, "output_tokens": 25}

print("[1] started 在请求前 fsync，usage 与产出结转各自追加")
with tempfile.TemporaryDirectory() as folder:
    state = Path(folder)
    controller = P.RequestController(state, preflight=lambda: None)
    seen_started = []

    def request():
        events = P.load_events(state)
        seen_started.append(events[-1]["event"] == P.EVENT_STARTED)
        return "result"

    result, receipt = controller.run(
        stage="translation", job_key="job-ok", source_ref="facebook:p1",
        media_index=None, model="fixture", request=request,
        usage_getter=lambda: GOOD, usage_errors=usage_errors, usage_cost=cost)
    check(result == "result" and seen_started == [True],
          "真实调用开始前已经能从磁盘读到 started")
    pending = P.ledger_snapshot(state, now=datetime.now(timezone.utc))
    check(pending.unknown and receipt.request_id in pending.request_ids,
          "usage 已记录但产出尚未 fsync 时全局失败闭合")
    controller.finalize(receipt, accepted=True, reason="fixture artifact fsynced")
    events = P.load_events(state)
    check([row["event"] for row in events] == [
              P.EVENT_STARTED, P.EVENT_USAGE, P.EVENT_ACCEPTED],
          "成功请求严格追加 started→usage_recorded→accepted")
    snapshot = P.ledger_snapshot(state, now=datetime.now(timezone.utc))
    check(not snapshot.unknown and abs(snapshot.daily_usd - .25) < 1e-9,
          "独立账本按响应 usage 计费，不依赖业务产物是否可读")

    # 产出拒绝仍计费，同一任务仅允许有界重试。
    def reject_once(key="job-rejected"):
        _, receipt = controller.run(
            stage="translation", job_key=key,
            source_ref="instagram:p2", media_index=None, model="fixture",
            request=lambda: "bad", usage_getter=lambda: GOOD,
            usage_errors=usage_errors, usage_cost=cost)
        controller.finalize(receipt, accepted=False, reason="immutable gate")

    reject_once()
    retry_allowed = True
    try:
        reject_once()          # 第 2 次：预算 2，仍应放行
    except P.PaidRequestBlocked:
        retry_allowed = False
    check(retry_allowed,
          f"被拒 1 次后仍可重试（预算 {P.REJECTED_RETRY_BUDGET} 次）——"
          "偶发抖动不该逼用户改 PROMPT_VERSION")

    try:
        controller.run(
            stage="translation", job_key="job-rejected",
            source_ref="instagram:p2", media_index=None, model="fixture",
            request=lambda: "must-not-run", usage_getter=lambda: GOOD,
            usage_errors=usage_errors, usage_cost=cost)
    except P.PaidRequestBlocked:
        same_job_blocked = True
    else:
        same_job_blocked = False
    check(same_job_blocked,
          f"被拒满 {P.REJECTED_RETRY_BUDGET} 次后禁止继续自动重试")

print("\n[2] crash/未知 usage 最多留下一个在途请求，并阻断全部后续付费")
with tempfile.TemporaryDirectory() as folder:
    state = Path(folder)
    controller = P.RequestController(state, preflight=lambda: None)
    try:
        controller.run(
            stage="image", job_key="crash", source_ref="instagram:p3",
            media_index=0, model="fixture",
            request=lambda: (_ for _ in ()).throw(RuntimeError("fixture")),
            usage_getter=lambda: {}, usage_errors=lambda _usage: ["missing"],
            usage_cost=lambda _usage: None)
    except P.PaidRequestBlocked:
        uncertain = True
    else:
        uncertain = False
    called = []
    try:
        controller.run(
            stage="translation", job_key="later", source_ref="facebook:p4",
            media_index=None, model="fixture",
            request=lambda: called.append(True), usage_getter=lambda: GOOD,
            usage_errors=usage_errors, usage_cost=cost)
    except P.PaidRequestBlocked:
        later_blocked = True
    else:
        later_blocked = False
    check(uncertain and later_blocked and not called,
          "无 usage 异常记 uncertain；另一个任务也在 API 前立即停手")

print("\n[3] 新产物用 paid_request_id 去重，旧产物仍按 legacy usage 计")
with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    state = root / "state"
    account = root / "archive" / "fa_fixture"
    account.mkdir(parents=True)
    controller = P.RequestController(state, preflight=lambda: None)
    _, receipt = controller.run(
        stage="translation", job_key="dedupe", source_ref="facebook:p5",
        media_index=None, model="fixture", request=lambda: "ok",
        usage_getter=lambda: GOOD, usage_errors=usage_errors, usage_cost=cost)
    controller.finalize(receipt, accepted=True)
    now = datetime.now(timezone.utc)
    (account / "translated.jsonl").write_text(
        json.dumps({
            "post_id": "p5", "translated_at": now.isoformat(),
            "paid_request_id": receipt.request_id,
            "usage": {"input_tokens": 999999999, "output_tokens": 999999999},
        }) + "\n", encoding="utf-8")
    snapshot = A.budget_snapshot([account], now=now, state_dir=state)
    check(abs(snapshot.daily_usd - .25) < 1e-9 and not snapshot.unknown,
          "新业务行不重复计费；金额只取独立 ledger 的一次 usage")

print("\n[4] 付费锁跨文本/图片共用")
with tempfile.TemporaryDirectory() as folder:
    path = Path(folder) / P.LOCK_NAME
    try:
        with P.PaidRequestLock(path):
            with P.PaidRequestLock(path):
                pass
    except P.PaidRequestBlocked:
        locked = True
    else:
        locked = False
    check(locked, "同一时刻最多一个文本或图片请求在途")

print("\n[5] SDK 不得在账本外重放可能已经计费的请求")
for kind, failure, supplied_client in (
        ("translation", "timeout", False), ("translation", "503", True),
        ("image", "503", False), ("image", "timeout", True)):
    with tempfile.TemporaryDirectory() as folder:
        state = Path(folder) / "state"
        settings = T.Settings() if kind == "translation" else I.Settings()
        settings.max_retries = 2
        settings.gap = 0
        settings.api_key = lambda: "offline-placeholder"
        attempts = []

        def respond(request):
            if request.method == "GET":
                return httpx.Response(200, request=request, json={
                    "object": "list", "data": [{"id": settings.model, "object": "model"}]})
            attempts.append(request.url.path)
            if len(attempts) == 1:
                if failure == "timeout":
                    raise httpx.ReadTimeout("offline lost response", request=request)
                return httpx.Response(503, request=request, json={"error": {"message": "offline"}})
            # 第二次会成功，防止只检查最终异常而漏掉 SDK 内部重放。
            if kind == "translation":
                payload = {"id": "offline", "object": "chat.completion", "created": 1,
                           "model": settings.model,
                           "choices": [{"index": 0, "finish_reason": "stop",
                                        "message": {"role": "assistant", "content": "[]"}}],
                           "usage": {"prompt_tokens": 100, "completion_tokens": 10,
                                     "total_tokens": 110}}
            else:
                payload = {"created": 1, "model": settings.model,
                           "data": [{"b64_json": "aW1hZ2U="}],
                           "usage": {"input_tokens": 100, "output_tokens": 10,
                                     "total_tokens": 110,
                                     "input_tokens_details": {"text_tokens": 20, "image_tokens": 80}}}
            return httpx.Response(200, request=request, json=payload)

        transport = httpx.MockTransport(respond)
        sdk_clients = []

        def offline_client(**kwargs):
            client = OpenAI(**kwargs, http_client=httpx.Client(transport=transport))
            sdk_clients.append(client)
            return client

        controller = P.RequestController(state, preflight=lambda: None)
        client = (offline_client(api_key="offline-placeholder", base_url="https://offline.invalid",
                                 max_retries=2) if supplied_client else None)
        caller = (T.Translator(settings, client=client, paid_controller=controller)
                  if kind == "translation" else I.ImageEditor(settings, client=client,
                                                              paid_controller=controller))
        source = Path(folder) / "source.png"
        source.write_bytes(b"offline image upload")

        def invoke():
            caller.set_paid_context("offline-" + kind, "facebook:p6")
            if kind == "translation":
                caller.translate("offline body", "offline system")
            else:
                caller.edit(source, "offline prompt", "1024x1024")
            caller.finalize_paid(True, "offline artifact")

        label = f"{kind}/{failure}/{'injected' if supplied_client else 'constructed'}"
        try:
            with patch.object(paid_model, "build_client", side_effect=offline_client), \
                    patch.object(OpenAI, "_sleep_for_retry", return_value=None):
                try:
                    invoke()
                except P.PaidRequestBlocked:
                    blocked = True
                else:
                    blocked = False
                check(blocked and len(attempts) == 1,
                      label + "：一次未知结果后没有第二次 HTTP 请求")
                check([row["event"] for row in P.load_events(state)] == [
                          P.EVENT_STARTED, P.EVENT_UNCERTAIN],
                      label + "：未知结果立即记账，不用后一次成功伪装已闭合")
                try:
                    invoke()
                except P.PaidRequestBlocked:
                    blocked = True
                else:
                    blocked = False
                check(blocked and len(attempts) == 1,
                      label + "：未核账前再次调用也在 HTTP 前停止")
        finally:
            for sdk_client in sdk_clients:
                sdk_client.close()

if fails:
    print("\n%d 项失败" % len(fails))
    raise SystemExit(1)
print("\n全部通过")
