"""自动处理范围预览的隔离文件回归；不使用真实浏览器、模型或账本。"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image
from core import config, paid_requests, review, translated
from core.console import force_utf8
from core.store import Archive, Media, Post
from localize import images
from pipeline import cli, engine, processing_preview

force_utf8()
NOW = datetime(2026, 9, 16, 12, tzinfo=timezone.utc)
ACTIVATED = "2026-09-15T12:00:00Z"


class ProcessingPreviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / "state"
        self.state.mkdir()
        self.config = config.Config()
        self.config._d["paths"] = {"archive": str(self.root / "archive"), "state": str(self.state)}
        self.config._d["targets"] = {"facebook": "acme", "instagram": "acme"}
        self.config._d["publish"]["trusted_owners"] = {"facebook": ["acme"], "instagram": ["acme"]}
        self.settings = {"autonomy": "assisted", "dead_man_days": 3,
                         "daily_budget_usd": 5.0, "monthly_budget_usd": 60.0}
        self.config._d["pipeline"] = self.settings
        self.addCleanup(patch.stopall)
        patch.object(config, "_cfg", self.config).start()
        patch("socket.create_connection", side_effect=AssertionError("no network in preview")).start()
        patch.object(paid_requests.RequestController, "run",
                     side_effect=AssertionError("no paid requests in preview")).start()
        (self.state / engine.STATE_NAME).write_text(json.dumps({"activated_at": ACTIVATED}), encoding="utf-8")

    def post(self, pid, *, platform="instagram", when="2026-09-16T08:00:00Z", owner="acme", account="acme"):
        arc = Archive(self.root / "archive", platform[:2] + "_" + account)
        post = Post(pid, platform, account, "Meet our M1! " + pid, when,
                    owner=owner, coauthors=[account] if owner != account else [])
        folder = arc.post_dir(post)
        folder.mkdir(parents=True, exist_ok=True)
        for index in range(2):
            path = folder / ("%02d.png" % (index + 1))
            Image.new("RGB", (1024, 1024), (100 + index, 140, 180)).save(path)
            post.media.append(Media("https://example.invalid/%d.png" % index, "image",
                                    path.relative_to(arc.base).as_posix()))
        arc.append(post)
        return arc, post

    def machine(self, arc, post, *, prompt_version=None):
        row = {"post_id": post.post_id, "text_de": "Entdecke unseren M1!",
               "source_text_sha256": translated.source_text_sha256(post.text),
               "prompt_version": translated.PROMPT_VERSION if prompt_version is None else prompt_version,
               "model": "offline-fixture", "translated_at": "2026-08-01T12:00:00Z"}
        translated.append_translated(arc.base / "translated.jsonl", row)
        return row

    def output(self, arc, post, index, text, *, manual=False):
        original = arc.base / post.media[index].local_path
        target = original.parent / "media_de" / ("%02d.jpg" % (index + 1))
        target.parent.mkdir(exist_ok=True)
        Image.new("RGB", (1024, 1024), (90, 120, 160)).save(target)
        if not manual:
            images.append_image_jsonl(arc.base / "images_de.jsonl", {
                "post_id": post.post_id, "media_index": index,
                "source_sha256": images.sha256_file(original),
                "text_de_sha256": images.text_de_sha256(text),
                "prompt_version": images.IMAGE_PROMPT_VERSION,
                "out_path": target.relative_to(arc.base).as_posix(),
                "folder_name": original.parent.name,
                "output_sha256": images.sha256_file(target),
                "created_at": "2026-08-01T12:10:00Z", "model": "offline-fixture",
                "size_requested": "1024x1024", "size_returned": "1024x1024", "quality": "high",
            })

    def files(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def preview(self, *, selected=None):
        dirs = cli.translation.account_dirs(self.root / "archive")
        before = self.files()
        result = processing_preview.snapshot(
            account_dirs=dirs, processing_account_dirs=selected,
            state_dir=self.state, settings=self.settings, now=NOW)
        self.assertEqual(self.files(), before, "preview must not create or change business/derived files")
        return result

    def test_activation_and_permission_match_automatic_scope_and_predict_untranslated_images(self):
        arc, _old = self.post("old", when="2026-09-15T11:59:00Z")
        self.post("boundary", when=ACTIVATED)
        _arc, new = self.post("new")
        self.post("third-party", owner="creator")
        self.post("frozen", account="old-brand")
        value = self.preview()
        rows = {row["post_id"]: row for row in value["posts"]}
        self.assertEqual(value["totals"]["translations"], 1)
        self.assertEqual(value["totals"]["risk_scans"], 1)
        self.assertEqual(value["totals"]["new_images_min"], 2)
        self.assertEqual(rows["new"]["required_indices"], [0, 1])
        self.assertEqual(rows["old"]["status"], "excluded")
        self.assertEqual(rows["boundary"]["status"], "excluded")
        self.assertIn("合作方", rows["third-party"]["reasons"][0])
        self.assertIn("冻结", rows["frozen"]["reasons"][0])
        self.assertGreater(value["cost"]["text_base_reference_usd"], 0)
        self.assertAlmostEqual(value["cost"]["image_reference_min_usd"], 0.422)
        self.machine(arc, new)
        current_arc = Archive(arc.base.parent, arc.base.name)
        jobs, _, _ = images.build_jobs(images.Settings(), arc.base,
                                      [row for row in current_arc.rows() if row["post_id"] == "new"])
        self.assertEqual([job.media_index for job in jobs], rows["new"]["required_indices"])

    def test_complete_outputs_and_human_caption_are_preserved(self):
        arc, post = self.post("ready")
        machine = self.machine(arc, post)
        for index in range(2):
            self.output(arc, post, index, machine["text_de"])
        translated.append_human_translation(arc.base / "translated_human.jsonl", post.to_row(), "Von Hand verbessert")
        value = self.preview()
        row = value["posts"][0]
        self.assertEqual(row["text_action"], "保留人工文案")
        self.assertEqual(row["status"], "current")
        self.assertEqual(row["kept_current"], 2)
        self.assertEqual(value["totals"]["planned_posts"], 0)
        self.assertEqual(value["cost"]["partial_reference_max_usd"], 0)

    def test_missing_translation_keeps_manual_image_and_counts_remaining_image(self):
        arc, post = self.post("manual-image")
        self.output(arc, post, 0, "", manual=True)
        row = self.preview()["posts"][0]
        self.assertEqual(row["text_action"], "英译德")
        self.assertEqual(row["kept_manual"], 1)
        self.assertEqual(row["required_indices"], [1])

    def test_old_machine_translation_leaves_existing_image_regeneration_conditional(self):
        arc, post = self.post("stale-machine")
        machine = self.machine(arc, post, prompt_version=translated.PROMPT_VERSION - 1)
        self.output(arc, post, 0, machine["text_de"])
        row = self.preview()["posts"][0]
        self.assertEqual(row["required_indices"], [1])
        self.assertEqual(row["conditional_indices"], [0])
        self.assertEqual((row["new_images_min"], row["new_images_max"]), (1, 2))

    def test_human_only_caption_uses_existing_human_image_basis(self):
        arc, post = self.post("human")
        first = translated.append_human_translation(arc.base / "translated_human.jsonl", post.to_row(), "Erste Fassung")
        self.output(arc, post, 0, first["text_de"])
        translated.append_human_translation(arc.base / "translated_human.jsonl", post.to_row(), "Neue Fassung")
        row = self.preview()["posts"][0]
        self.assertEqual(row["text_action"], "保留人工文案")
        self.assertEqual(row["required_indices"], [1])
        self.assertEqual(row["kept_current"], 1)

    def test_stale_human_caption_blocks_paid_processing(self):
        arc, post = self.post("human-stale")
        prior = dict(post.to_row(), text="Earlier source")
        translated.append_human_translation(arc.base / "translated_human.jsonl", prior, "Alte Fassung")
        row = self.preview()["posts"][0]
        self.assertEqual(row["status"], "blocked")
        self.assertIn("人工译文", row["reasons"][0])
        self.assertEqual(row["new_images_max"], 0)

    def test_platform_selection_still_checks_other_accounts_paid_ledger(self):
        arc, _ = self.post("ig")
        self.post("fb", platform="facebook")
        value = self.preview(selected=[arc.base])
        self.assertEqual(value["totals"]["planned_posts"], 1)
        (self.state / "paid_requests.jsonl").write_text(json.dumps({
            "event": "started", "request_id": "unresolved-request", "job_key": "old-job",
            "stage": "translation", "source_ref": "facebook:fb", "recorded_at": NOW.isoformat(),
        }) + "\n", encoding="utf-8")
        value = self.preview(selected=[arc.base])
        self.assertEqual(value["totals"]["planned_posts"], 0)
        self.assertTrue(value["blockers"])

    def test_skipped_review_does_not_reenter_paid_processing(self):
        arc, post = self.post("skip")
        with review.transaction(arc.base) as transaction:
            transaction.change(post.to_row(), "skipped", expected_revision=None,
                               expected_source_sha256=translated.source_text_sha256(post.text),
                               reason="Business chose not to publish", now=NOW)
        value = self.preview()
        self.assertEqual(value["totals"]["planned_posts"], 0)
        self.assertIn("skipped", value["posts"][0]["reasons"][0])

    def test_unactivated_archive_is_blocked_without_fabricating_a_boundary(self):
        self.post("new")
        (self.state / engine.STATE_NAME).unlink()
        value = self.preview()
        self.assertEqual(value["totals"]["planned_posts"], 0)
        self.assertIsNone(value["activated_at"])
        self.assertIn("尚未激活", value["posts"][0]["reasons"][0])

    def test_zero_budget_blocks_paid_scope_and_names_the_limit(self):
        self.post("new")
        self.settings["daily_budget_usd"] = 0.0
        value = self.preview()
        self.assertEqual(value["totals"]["planned_posts"], 0)
        self.assertEqual(value["cost"]["partial_reference_max_usd"], 0)
        self.assertIn("预算已到上限", value["blockers"][0])

    def test_missing_text_rates_fail_before_any_quote_or_write(self):
        self.post("unknown-cost")
        self.config._d["translate"]["cost_rates_usd_per_million"] = {}
        before = self.files()
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(cli.main(["processing-preview"]), 2)
        self.assertIn("cost_rates_usd_per_million", stdout.getvalue())
        self.assertNotIn("美元参考小计", stdout.getvalue())
        self.assertEqual(self.files(), before)

    def test_cli_empty_unactivated_archive_does_not_create_state_or_archive(self):
        self.config._d["paths"] = {"archive": str(self.root / "missing-archive"),
                                     "state": str(self.root / "missing-state")}
        before = self.files()
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(cli.main(["processing-preview", "--json"]), 0)
        value = json.loads(stdout.getvalue())
        self.assertIsNone(value["activated_at"])
        self.assertEqual(value["totals"]["translations"], 0)
        self.assertFalse((self.root / "missing-archive").exists())
        self.assertFalse((self.root / "missing-state").exists())
        self.assertEqual(self.files(), before)

    def test_missing_posts_directory_is_not_created_by_readonly_preview(self):
        arc = self.root / "archive" / "in_acme"
        arc.mkdir(parents=True)
        (arc / "manifest.jsonl").write_text("", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "只读不创建"):
            self.preview()
        self.assertFalse((arc / "posts").exists())


if __name__ == "__main__":
    unittest.main()
