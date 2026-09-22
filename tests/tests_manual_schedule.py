"""审校台单篇排期必须穿过真实审批和工作流，且不能再调用历史录证检查。"""
from __future__ import annotations

import asyncio
import json
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config, review  # noqa: E402
from pipeline import approval, engine  # noqa: E402
from publish import business_suite as bs, capabilities, journal, manual_run, month_inventory  # noqa: E402
from publish import workflow  # noqa: E402
from tests_approval import NOW, TARGET  # noqa: E402


def _forbid(name):
    def wrapped(*_args, **_kwargs):
        raise AssertionError("单篇路径调用了历史录证检查：" + name)
    return wrapped


class ManualScheduleChainTests(unittest.TestCase):
    def setUp(self):
        from tests_approval import ApprovalTests
        self.host = ApprovalTests()
        self.host.setUp()
        self.addCleanup(self.host.doCleanups)
        state = config.cfg().state_dir / "pipeline_state.json"
        payload = json.loads(state.read_text(encoding="utf-8"))
        payload["activated_at"] = None
        state.write_text(json.dumps(payload), encoding="utf-8")

    def test_both_channels_open_the_calendar_without_historical_files(self):
        async def scenario(channel):
            run = manual_run.load(channel)
            seen = {}

            async def goto(url, **_kwargs):
                seen["url"] = url
                raise bs.PublishStepError("导航后停止")

            page = AsyncMock()
            page.goto = goto
            page.url = "https://business.facebook.com/latest/content_calendar?asset_id=1001&business_id=2002"
            with patch.object(bs, "require_readback_evidence", _forbid("readback")), \
                    patch.object(month_inventory, "require", _forbid("calendar")), \
                    patch.object(capabilities, "require", _forbid("capabilities")):
                with self.assertRaises(bs.PublishStepError) as error:
                    await month_inventory.read(page, ui_timezone=run.ui_timezone,
                                               business_timezone=bs.business_timezone(), timeout=1, run=run)
            self.assertIn("导航后停止", str(error.exception))
            self.assertIn("asset_id=1001", seen["url"])
            self.assertIn("business_id=2002", seen["url"])

        asyncio.run(scenario("facebook"))
        asyncio.run(scenario("instagram"))

    def test_unactivated_review_desk_schedules_without_remote_image_verification(self):
        self.assertIsNone(engine.activation_time(config.cfg().state_dir))
        host = self.host
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo
        zone = ZoneInfo("America/Los_Angeles")
        local_now = datetime.now(zone)
        scheduled_local = (local_now + timedelta(days=2)).replace(minute=0, second=0, microsecond=0)
        if scheduled_local.month != local_now.month:
            scheduled_local = local_now.replace(hour=23, minute=0, second=0, microsecond=0)
        scheduled = scheduled_local.astimezone(timezone.utc)
        moment = datetime.now(timezone.utc)
        start = scheduled_local.date().replace(day=1)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end = start.replace(month=start.month + 1, day=1) - timedelta(days=1)
        inventory = bs.RemoteSlotInventory((), "America/Los_Angeles", start, end, cards=(), cards_loaded=True)
        page = AsyncMock()
        context = AsyncMock()
        context.new_page.return_value = page

        async def submit(page, **_kwargs):
            return bs.SubmitResult(clicked=True, confirmed=True, success_signal="scheduled",
                                   remote_id="facebook=123456789")

        async def verify(*_args, **_kwargs):
            return bs.ScheduledReadback(
                found=True, observed_at=NOW.isoformat(), target_at=TARGET.isoformat(),
                ui_at=TARGET.isoformat(), final_text_sha256="unused",
                channels=("facebook",), remote_id="facebook=123456789",
                success_signal="planner_complete_month_and_scheduled_detail",
                diagnostics={"remote_images_verified": False, "full_caption_equal": True})

        guards = [
            patch.object(bs, "require_readback_evidence", _forbid("readback")),
            patch.object(bs, "require_submission_evidence", _forbid("submission")),
            patch.object(bs, "require_account_context_evidence", _forbid("account")),
            patch("publish.compose.require_probe_evidence", _forbid("probe")),
            patch.object(capabilities, "require", _forbid("capabilities")),
            patch.object(month_inventory, "require", _forbid("calendar")),
            patch("publish.channel_evidence.require", _forbid("channel")),
            patch.object(workflow, "attach", AsyncMock(return_value=(context, None, context))),
            patch.object(workflow.bs, "open_composer", AsyncMock(return_value=page)),
            patch.object(workflow.channels, "select", AsyncMock(return_value={"channel": "facebook", "account": "Neakasa Deutschland"})),
            patch.object(workflow.channels, "verify_before_submit", AsyncMock(return_value={})),
            patch.object(workflow.bs, "upload_images", AsyncMock(return_value=["uploaded"])),
            patch.object(workflow.media, "verify_upload", AsyncMock(return_value={"image_count": 1})),
            patch.object(workflow.bs, "fill_caption", AsyncMock()),
            patch.object(workflow.bs, "set_schedule", AsyncMock(return_value="read-back")),
            patch.object(workflow, "_screenshot", AsyncMock(return_value="")),
            patch.object(workflow.month_inventory, "read", AsyncMock(return_value=inventory)),
            patch.object(workflow.bs, "submit", submit),
            patch.object(workflow.month_readback, "verify", verify),
        ]
        for item in guards:
            item.start()
            self.addCleanup(item.stop)
        result = host.approve(inventory_reader=AsyncMock(return_value=inventory),
                              params={"scheduled_at": scheduled, "now": moment})
        self.assertTrue(result["ok"])
        self.assertEqual(result["operation_status"], "succeeded")
        publication = result["publication"]
        self.assertEqual(publication["status"], journal.STATUS_SCHEDULED)
        self.assertEqual(publication["origin"], "review_desk")
        self.assertIs(publication["readback_diagnostics"]["remote_images_verified"], False)
        accepted = capabilities.acceptance(config.cfg().state_dir)
        self.assertFalse(accepted["facebook"]["verified"])
        self.assertFalse(accepted["instagram"]["verified"])
        blockers = capabilities.activation_blockers(config.cfg().state_dir)
        self.assertTrue(any("尚无绑定冻结快照" in item for item in blockers))

    def test_pre_click_failure_can_be_confirmed_again_but_click_intent_cannot(self):
        host = self.host
        locked = host.lock_content()
        state = config.cfg().state_dir
        post = host.post
        failed = workflow.new_attempt(post, TARGET, ui_timezone="America/Los_Angeles",
                                      target_channels=("facebook",), origin="review_desk")
        failed = journal.transition(failed, journal.STATUS_FAILED_PRE_SUBMIT,
                                    recorded_at=NOW.isoformat(), note="发布浏览器未启动")
        journal.append(state, failed)
        ref = (journal.source_ref(post.platform, post.post_id),)
        self.assertIsNone(journal.pending_record_for_refs(state, ref))
        armed = workflow.new_attempt(post, TARGET + timedelta(hours=1), ui_timezone="America/Los_Angeles",
                                     target_channels=("facebook",), origin="review_desk")
        armed = journal.transition(armed, journal.STATUS_SUBMIT_AMBIGUOUS, recorded_at=NOW.isoformat(),
                                   note="已点击")
        journal.append(state, armed)
        pending = journal.pending_record_for_refs(state, ref)
        self.assertEqual(pending["status"], journal.STATUS_SUBMIT_AMBIGUOUS)
        legacy = workflow.new_attempt(post, TARGET, ui_timezone="America/Los_Angeles",
                                      target_channels=("facebook",))
        legacy = journal.transition(legacy, journal.STATUS_FAILED_PRE_SUBMIT, recorded_at=NOW.isoformat())
        legacy_row = {"attempt_id": legacy.attempt_id, "origin": legacy.origin, "status": legacy.status}
        self.assertFalse(journal.pre_click_failure_closed([legacy_row], legacy.attempt_id))
        self.assertEqual(review.latest(host.account)[host.source["post_id"]]["status"], "content_locked")
        self.assertEqual(locked["status"], "content_locked")


if __name__ == "__main__":
    unittest.main()
