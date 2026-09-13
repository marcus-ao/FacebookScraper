"""审校状态真相源：本地临时归档、可注入时钟，无外部调用。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import review, translated  # noqa: E402
from core.store import post_dirname  # noqa: E402


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.account = Path(self.temporary.name) / "fa_example"
        self.source = {"post_id": "123", "platform": "facebook", "account": "example",
                       "created_at": "2026-09-11T02:00:00Z", "text": "Source"}
        self.post = self.account / "posts" / post_dirname("123", self.source["created_at"])
        self.post.mkdir(parents=True)
        self.write_source()
        (self.account / "manifest.jsonl").write_text(json.dumps(self.source) + "\n", encoding="utf-8")
        self.now = datetime(2026, 9, 11, 2, 0, tzinfo=timezone.utc)  # Friday 10:00 Shanghai.

    def write_source(self):
        (self.post / "post.json").write_text(json.dumps(self.source), encoding="utf-8")

    def change(self, action, **kwargs):
        current = review.state_for(self.account, self.source)
        return review.transition(
            self.account, self.source, action,
            expected_revision=kwargs.pop("expected_revision", current["revision"]),
            expected_source_sha256=kwargs.pop("expected_source_sha256",
                translated.source_text_sha256(self.source["text"])), now=self.now, **kwargs)

    def test_snooze_skips_weekend_and_wakes_once(self):
        event = self.change("snoozed")
        self.assertEqual(event["wake_at"], "2026-09-16T02:00:00+00:00")
        self.assertEqual(review.wake_due([self.account], now=self.now), [])
        due = datetime(2026, 9, 16, 2, 0, tzinfo=timezone.utc)
        events = review.wake_due([self.account], now=due)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["action"], "woke")
        self.assertEqual(events[0]["status"], "pending_review")
        self.assertEqual(events[0]["account"], "fa_example")
        self.assertIsNone(events[0]["actor"])
        self.assertEqual(review.wake_due([self.account], now=due), [])

    def test_skip_requires_reason_and_remains_distinct_from_handoff(self):
        with self.assertRaises(review.ReviewValidationError):
            self.change("skipped", reason="  ")
        event = self.change("skipped", reason="德国站没有此活动")
        self.assertEqual(event["status"], "skipped")
        self.assertEqual(event["reason"], "德国站没有此活动")
        with self.assertRaises(review.ReviewConflict):
            self.change("handed_off")

    def test_handoff_keeps_optional_url_and_cannot_resume_as_pending(self):
        event = self.change("handed_off", handoff_url="https://www.facebook.com/posts/123")
        self.assertEqual(event["status"], "handed_off")
        self.assertEqual(event["handoff_url"], "https://www.facebook.com/posts/123")
        with self.assertRaises(review.ReviewConflict):
            self.change("woke")

    def test_source_change_wakes_snoozed_but_preserves_terminal_decision(self):
        self.change("snoozed")
        self.source["text"] = "Changed source"
        self.write_source()
        self.assertEqual(review.state_for(self.account, self.source)["status"], "pending_review")
        events = review.wake_due([self.account], now=self.now)
        self.assertEqual(len(events), 1)
        self.change("skipped", reason="不适合本站")
        self.source["text"] = "Changed again"
        self.write_source()
        self.assertEqual(review.state_for(self.account, self.source)["status"], "skipped")

    def test_old_state_or_source_version_is_rejected(self):
        self.change("snoozed")
        with self.assertRaises(review.ReviewConflict):
            self.change("skipped", reason="old tab", expected_revision=None)
        with self.assertRaises(review.ReviewConflict):
            self.change("skipped", reason="old source", expected_source_sha256="0" * 64)

    def test_scheduled_truth_prevents_skip_or_handoff(self):
        for action in ("snoozed", "skipped", "handed_off", "edited"):
            with self.subTest(action=action), self.assertRaises(review.ReviewConflict):
                self.change(action, scheduled=True, reason="test")
        self.assertEqual(review.state_for(self.account, self.source, scheduled=True)["status"], "scheduled")

    def test_confirmed_schedule_can_record_transition_but_guess_cannot(self):
        self.change("approved")
        with self.assertRaises(review.ReviewConflict):
            self.change("scheduled")
        event = self.change("scheduled", scheduled=True)
        self.assertEqual(event["status"], "scheduled")
        self.assertEqual(review.state_for(self.account, self.source)["status"], "scheduled")

    def test_editing_a_snoozed_post_does_not_lose_its_wakeup(self):
        first = self.change("snoozed")
        event = self.change("edited")
        self.assertEqual(event["status"], "snoozed")
        self.assertEqual(event["wake_at"], first["wake_at"])

    def test_malformed_tail_blocks_decisions_without_changing_ledger(self):
        self.change("skipped", reason="已确认不发")
        path = self.account / "review_items.jsonl"
        with path.open("ab") as handle:
            handle.write(b'\n{"broken":')
        before = path.read_bytes()
        with self.assertRaisesRegex(review.ReviewConflict, "审校记录.*损坏"):
            review.state_for(self.account, self.source)
        self.assertEqual(path.read_bytes(), before)

    def test_corrupt_only_record_never_becomes_an_unreviewed_post(self):
        (self.account / "review_items.jsonl").write_bytes(b'{"status":"skipped"\n')
        with self.assertRaises(review.ReviewConflict):
            review.state_for(self.account, self.source)

    def test_record_from_other_account_is_not_accepted(self):
        record = self.change("skipped", reason="不发")
        record["account"] = "fa_other"
        (self.account / "review_items.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
        with self.assertRaises(review.ReviewConflict):
            review.state_for(self.account, self.source)

    def test_wake_due_ignores_posts_already_scheduled(self):
        self.change("snoozed")
        due = datetime(2026, 9, 17, tzinfo=timezone.utc)
        self.assertEqual(review.wake_due([self.account], now=due,
                                        scheduled_refs={"facebook:123"}), [])

    def test_invalid_dates_links_or_unknown_actions_do_not_write(self):
        for action, options in [
            ("snoozed", {"wake_at": "not-a-date"}),
            ("snoozed", {"wake_at": "2026-09-10T02:00:00Z"}),
            ("handed_off", {"handoff_url": "javascript:alert(1)"}),
            ("unknown", {}),
        ]:
            with self.subTest(action=action), self.assertRaises(review.ReviewValidationError):
                self.change(action, **options)
        self.assertFalse((self.account / "review_items.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
