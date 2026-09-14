"""Verify React HTTP writes against explicit API contracts; loopback host and in-memory responses only."""
from __future__ import annotations

import copy
import json
from playwright.sync_api import expect, sync_playwright

from browser_regression import choose
from ui_fixture import EVIDENCE, BrowserFixture, UIFixture


WORKFLOWS = [
    "save", "snooze", "wake", "skip", "handoff", "handoff_link", "tags", "export",
    "approve", "calendar_failure", "settings", "initial", "refine_text", "refine_image",
    "recover_job", "hashtags",
]


def versions(detail):
    return {
        "source_text_sha256": detail["text"]["source_text_sha256"],
        "human_revision": detail["text"]["human_revision"],
        "review_revision": detail["review"]["revision"],
    }


def expected_contract(workflow, detail, path, capability, job, settings):
    base_decision = {
        "source_text_sha256": detail["text"]["source_text_sha256"],
        "review_revision": detail["review"]["revision"],
        "reason": "", "handoff_url": "",
    }
    if workflow == "save":
        return "PUT", path + "/localization", {
            "body_de": "Manuell geprüft 😀 $219.99",
            "tags": detail["localization"]["tags"],
            "hashtags_confirmed": detail["localization"]["hashtags_confirmed"],
            "links": detail["localization"]["links"],
            "ig_cta": detail["localization"]["ig_cta"],
            **versions(detail), "localization_revision": detail["localization"]["revision"],
        }
    if workflow == "snooze":
        return "POST", path + "/review", {
            **base_decision, "action": "snoozed", "reason": "同一份运营理由",
            "wake_at": "2026-09-16T09:00:00+08:00",
        }
    if workflow == "wake":
        return "POST", path + "/review", {**base_decision, "action": "woke"}
    if workflow == "skip":
        return "POST", path + "/review", {
            **base_decision, "action": "skipped", "reason": "同一份运营理由",
        }
    if workflow == "handoff":
        return "POST", path + "/review", {
            **base_decision, "action": "handed_off", "handoff_url": "https://example.invalid/handled",
        }
    if workflow == "handoff_link":
        return "POST", path + "/review", {
            **base_decision, "action": "handoff_link", "handoff_url": "https://example.invalid/handled",
        }
    if workflow == "export":
        return "POST", path + "/export", {
            **base_decision, "action": "export", "handoff_url": "https://example.invalid/handled",
        }
    if workflow == "tags":
        return "PUT", path + "/tags", {
            "tags": ["Riko", "促销"], "tags_revision": detail["tags_revision"],
            "source_text_sha256": detail["text"]["source_text_sha256"],
        }
    if workflow == "approve":
        return "POST", path + "/approve", {
            "scheduled_at": "2026-09-15T10:30",
            "source_text_sha256": detail["text"]["source_text_sha256"],
            "human_revision": detail["text"]["human_revision"],
            "review_revision": detail["review"]["revision"],
            "content_fingerprint": "same-fingerprint",
        }
    if workflow == "calendar_failure":
        return "POST", "/api/calendar/refresh", {}
    if workflow == "settings":
        return "PUT", "/api/settings", {
            "values": {"default_times": ["11:00", "18:30"], "snooze_default_days": 5},
            "version": settings["version"],
        }
    if workflow == "initial":
        return "POST", f"/api/initial-translation/task/{detail['id']}", {
            "consent": True, "source_fingerprint": capability["source_fingerprint"], **versions(detail),
        }
    if workflow == "refine_text":
        return "POST", f"/api/refinements/task/{detail['id']}", {
            "kind": "text", "instruction": "保持事实，缩短开头", "media_index": None, **versions(detail),
        }
    if workflow == "refine_image":
        return "POST", f"/api/refinements/task/{detail['id']}", {
            "kind": "image", "instruction": "保持事实，缩短开头", "media_index": 0, **versions(detail),
        }
    if workflow == "recover_job":
        return "POST", "/api/content-jobs/compare-job/recover", {
            "expected_updated_at": job["recorded_at"],
        }
    if workflow == "hashtags":
        return "POST", f"/api/hashtags/task/{detail['id']}", versions(detail)
    raise AssertionError("No contract for " + workflow)


def run(page, ui, workflow, original):
    detail = copy.deepcopy(original)
    task_id = detail["id"]
    detail["status"] = "snoozed" if workflow == "wake" else "handed_off" if workflow == "handoff_link" else "pending_review"
    detail["review"]["status"] = detail["status"]
    detail["read_only"] = False
    path = f"/api/tasks/{task_id}"
    capability = {"available": True, "reason": "", "source_fingerprint": "a" * 64,
                  "needs_consent": True, "third_party": workflow in ["initial", "recover_job"], "job": None}
    job = {"job_id": "compare-job", "status": "interrupted" if workflow == "recover_job" else "pending",
           "recorded_at": "2026-09-13T00:00:00Z", "kind": "text",
           "source_text_sha256": detail["text"]["source_text_sha256"]}
    if workflow == "recover_job":
        capability["job"] = job
    refinements = {"max_refine_per_media": 3, "image_attempts": {}, "estimated_image_usd": 0.045,
                   "estimate_basis": "fixture", "estimate_samples": 1, "jobs": []}
    options = ui.fx.client.get(path + "/approval-options").json()
    options.update(available=True, reason="", fingerprint="same-fingerprint",
                   earliest="2026-09-14T08:00:00+02:00", latest="2026-10-01T20:00:00+02:00")
    settings = ui.fx.client.get("/api/settings").json()
    calendar = ui.fx.client.get("/api/calendar").json()
    calendar["refresh_available"] = True
    ui.overrides = {
        ("GET", path): (200, detail),
        ("GET", path + "/approval-options"): (200, options),
        ("GET", f"/api/initial-translation/task/{task_id}"): (200, capability),
        ("GET", f"/api/refinements/task/{task_id}"): (200, refinements),
        ("POST", f"/api/initial-translation/task/{task_id}"): (202, job),
        ("POST", f"/api/refinements/task/{task_id}"): (202, job),
        ("GET", "/api/initial-translation/jobs/compare-job"): (200, {**job, "status": "interrupted" if workflow == "recover_job" else "failed"}),
        ("GET", "/api/refinements/jobs/compare-job"): (200, {**job, "status": "failed"}),
        ("POST", "/api/content-jobs/compare-job/recover"): (200, {**job, "status": "failed"}),
        ("POST", path + "/review"): (200, detail),
        ("POST", path + "/export"): (200, {"fixture_zip": True}),
        ("POST", path + "/approve"): (200, {"ok": False, "status": "approved"}),
        ("PUT", path + "/localization"): (200, {**detail, "localization": {**detail["localization"], "body_de": "Manuell geprüft 😀 $219.99"}}),
        ("PUT", path + "/tags"): (200, {**detail, "tags": ["Riko", "促销"]}),
        ("GET", "/api/calendar"): (200, calendar),
        ("POST", "/api/calendar/refresh"): (503, {**calendar, "error": "fixture failure"}),
        ("GET", "/api/settings"): (200, settings),
        ("PUT", "/api/settings"): (200, {**settings, "editable": {"default_times": ["11:00", "18:30"], "snooze_default_days": 5}}),
        ("POST", f"/api/hashtags/task/{task_id}"): (200, {
            "source_text_sha256": detail["text"]["source_text_sha256"],
            "generated_at": "2026-09-13T00:00:00Z", "notice": "未采样", "selected": ["#Katzen"],
            "groups": [], "sampling": {"status": "unavailable", "reason": "fixture offline"},
        }),
    }
    expected = expected_contract(workflow, detail, path, capability, job, settings)
    start = len(ui.requests)
    if workflow in ["calendar_failure", "settings"]:
        page.goto(ui.fx.base_url + ("/calendar" if workflow == "calendar_failure" else "/settings"), wait_until="networkidle")
    else:
        page.goto(ui.fx.base_url + "/review/" + task_id, wait_until="networkidle")
        expect(page.get_by_role("button", name="编辑德语", exact=True)).to_have_count(0 if workflow == "handoff_link" else 1)

    if workflow == "save":
        page.get_by_role("button", name="编辑德语", exact=True).click()
        page.get_by_role("textbox", name="德语正文").fill("Manuell geprüft 😀 $219.99")
        page.wait_for_timeout(350)
        page.get_by_role("button", name="保存", exact=True).click()
    elif workflow in ["snooze", "wake", "skip", "handoff", "handoff_link", "export"]:
        labels = {"snooze": "稍后再审", "wake": "恢复审校", "skip": "这篇不发", "handoff": "我已自行处理",
                  "handoff_link": "补充发布链接", "export": "下载并由我处理"}
        page.get_by_role("button", name="更多处理动作", exact=True).click()
        page.get_by_role("menuitem", name=labels[workflow], exact=True).click()
        modal = page.get_by_role("dialog")
        if workflow == "snooze":
            modal.get_by_role("textbox", name="指定回来时间（上海时间）").fill("2026-09-16T09:00")
        if workflow in ["snooze", "skip"]:
            modal.get_by_role("textbox", name="理由").fill("同一份运营理由")
        if workflow in ["handoff", "handoff_link", "export"]:
            modal.get_by_role("textbox", name="手工发布链接").fill("https://example.invalid/handled")
        if workflow == "export":
            with page.expect_download() as downloaded:
                modal.get_by_role("button", name="下载并交给我", exact=True).click()
            assert downloaded.value.suggested_filename == "post_de.zip"
        else:
            modal.get_by_role("button", name="确认", exact=True).click()
    elif workflow == "tags":
        page.get_by_role("button", name="编辑分类", exact=True).click()
        page.get_by_role("textbox", name="产品分类", exact=True).fill("Riko，促销")
        page.get_by_role("button", name="保存分类", exact=True).click()
    elif workflow == "approve":
        page.get_by_role("textbox", name="发布时间（柏林当地时间）", exact=True).fill("2026-09-15T10:30")
        page.get_by_role("button", name="通过并创建排期", exact=True).click()
        page.get_by_role("button", name="确认通过并创建排期", exact=True).click()
    elif workflow == "calendar_failure":
        page.get_by_role("button", name="刷新月历", exact=True).click()
    elif workflow == "settings":
        page.get_by_role("textbox", name="默认排期时间（柏林）").fill("11:00, 18:30")
        page.get_by_role("spinbutton", name="默认挂起期限").fill("5")
        page.get_by_role("button", name="保存设置", exact=True).click()
    elif workflow == "initial":
        page.get_by_role("checkbox", name="我已确认可以处理这篇内容，开始本篇模型处理。").check()
        page.locator('[data-paid-action="翻译这篇"]').click()
    elif workflow.startswith("refine_"):
        page.get_by_text("单篇优化（可选）", exact=True).click()
        if workflow == "refine_image":
            choose(page, "优化内容", "图片")
        page.get_by_role("textbox", name="这一次希望怎样调整").fill("保持事实，缩短开头")
        page.locator('[data-paid-action="生成图片"], [data-paid-action="生成文案候选"]').click()
    elif workflow == "recover_job":
        page.get_by_role("button", name="核对并恢复本地状态（不重新生成）", exact=True).click()
    elif workflow == "hashtags":
        page.get_by_role("button", name="编辑德语", exact=True).click()
        page.get_by_role("tab", name="话题标签与链接", exact=True).click()
        page.locator('[data-paid-action="生成德语标签建议"]').click()

    page.wait_for_timeout(500)
    trace = ui.requests[start:]
    writes = [request for request in trace if request["method"] in ["POST", "PUT"]
              and not request["path"].endswith("/check")]
    actual = [(request["method"], request["path"], request["body"]) for request in writes]
    assert actual == [expected], (workflow, expected, actual, trace)
    assert all(request["query"] == "" for request in writes), (workflow, writes)
    check_requests = [request for request in trace if request["path"].endswith("/check")]
    assert all(request["query"] == "" for request in check_requests), (workflow, check_requests)
    checks = [request["body"] for request in check_requests]
    if workflow in {"save", "hashtags"}:
        expected_draft = copy.deepcopy(detail["localization"])
        if workflow == "save":
            expected_draft["body_de"] = "Manuell geprüft 😀 $219.99"
        assert checks and checks[-1] == {
            "text_de": expected_draft["body_de"], "body_only": True, "localization": expected_draft,
        }, checks
    else:
        assert not checks, (workflow, checks)
    return {"method": expected[0], "path": expected[1], "body": expected[2], "checks": checks}


def main():
    results = {}
    with BrowserFixture() as fx:
        ui = UIFixture(fx)
        original = fx.detail(next(row["id"] for row in ui.list_data["tasks"]
                                  if row["status"] == "pending_review" and not row["hard_alerts"]))
        with sync_playwright() as driver:
            context = driver.chromium.launch_persistent_context(
                str(fx.root / "contract-profile"), executable_path=fx.chrome_exe, headless=True,
                viewport={"width": 1366, "height": 768}, timezone_id="America/New_York", accept_downloads=True)
            try:
                for workflow in WORKFLOWS:
                    page = context.new_page()
                    page.set_default_timeout(8000)
                    ui.attach(page)
                    try:
                        results[workflow] = run(page, ui, workflow, original)
                    finally:
                        page.close()
                    print("PASS react " + workflow, flush=True)
                assert not ui.errors, ui.errors
                assert not fx.denied_backend_requests, fx.denied_backend_requests
                report = {"ui": "react", "contracts": results, "verified_full_request_contracts": WORKFLOWS,
                          "external_calls": 0, "server_denials": fx.denied_backend_requests}
                (EVIDENCE / "network-contracts.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"{len(WORKFLOWS)} React mutation method/path/full-body contracts passed.", flush=True)
            finally:
                context.close()


if __name__ == "__main__":
    main()
