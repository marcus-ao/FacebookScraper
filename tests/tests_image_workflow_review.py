"""Stage-two review regressions; isolated archives, no model or platform calls."""
import io
import json
import sys
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests_web_review as fixtures
from core import review, translated
from core.config import cfg
from localize import images
from pipeline import engine, refinement


class ImageWorkflowReviewTests(unittest.TestCase):
    def setUp(self):
        self.fx = fixtures.WebReviewTests()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.account = self.fx.account
        self.text = "Ein sauberes Zuhause. #Neakasa"

    def test_select_version_does_not_change_budget(self):
        self.fx.write_generated_image(self.text)
        self.fx.write_refined_image(self.text, "a" * 32)
        (self.account / "translated.jsonl").unlink()
        path = self.account / "images_de.jsonl"
        rows = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
        usage = {"input_tokens": 3000, "output_tokens": 1000, "total_tokens": 4000,
                 "input_tokens_details": {"text_tokens": 1000, "image_tokens": 2000}}
        for row in rows:
            row.update(usage=usage, model="gpt-image-2")
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8", newline="")
        before = engine.budget_snapshot([self.account], now=datetime.now(timezone.utc))
        images.select_image_version(self.account, self.fx.source, 0, rows[0]["out_path"],
                                    images.text_de_sha256(self.text))
        after = engine.budget_snapshot([self.account], now=datetime.now(timezone.utc))
        self.assertEqual(after, before)

    def test_frozen_account_cannot_select_an_image_version(self):
        self.fx.write_generated_image(self.text)
        self.fx.write_refined_image(self.text, 'a' * 32)
        wanted = self.fx.versions()[0]['out_path']
        cfg()._d['targets']['facebook'] = 'anotherbrand'
        before = self.fx.client.get(self.fx.url).json()
        self.assertTrue(before['read_only'])
        ledger = self.account / 'images_de.jsonl'
        images_before = ledger.read_bytes()
        result = self.fx.select(wanted)
        self.assertEqual(result.status_code, 400, result.text)
        self.assertIn('冻结', result.text)
        self.assertEqual(ledger.read_bytes(), images_before)
        after = self.fx.client.get(self.fx.url).json()
        self.assertEqual(after['review'], before['review'])
        self.assertEqual(after['images'][0]['de_url'], before['images'][0]['de_url'])

    def test_manual_image_refinement_rejected_before_attempt_is_counted(self):
        self.assertEqual(self.fx.upload().status_code, 200)
        detail = self.fx.client.get(self.fx.url).json()
        executor = Mock()
        with patch.object(engine, "budget_preflight"):
            with self.assertRaisesRegex(review.ReviewConflict, "人工图片"):
                refinement.submit(self.account, self.fx.source, kind="image", instruction="改 CTA",
                    source_text_sha256=detail["text"]["source_text_sha256"],
                    human_revision=None, review_revision=detail["review"]["revision"],
                    media_index=0, executor=executor)
        executor.submit.assert_not_called()
        self.assertFalse(refinement.latest())

    def test_upload_before_worker_starts_prevents_paid_call(self):
        with patch.object(engine, "budget_preflight"):
            job = refinement.submit(self.account, self.fx.source, kind="image", instruction="改 CTA",
                source_text_sha256=translated.source_text_sha256(self.fx.source["text"]),
                human_revision=None, review_revision=None, media_index=0, executor=Mock())
            self.assertEqual(self.fx.upload().status_code, 200)
            editor = Mock()
            result = refinement.execute(job, self.fx.source, editor=editor)
        self.assertEqual(result["status"], "failed")
        editor.edit.assert_not_called()

    def test_failed_upload_keeps_previous_image_visible(self):
        before = self.fx.write_generated_image(self.text)
        body = self.fx.upload_body()
        with patch.object(images.os, "fsync", side_effect=OSError("disk full")):
            result = self.fx.client.post(self.fx.url + "/image/0/upload", json=body)
        self.assertEqual(result.status_code, 400)
        self.assertEqual((self.fx.post_dir / "media_de/01.jpg").read_bytes(), before)

    def test_repeated_manual_upload_changes_image_url(self):
        first = self.fx.upload().json()
        second = self.fx.upload(image=Image.new("RGB", (1080, 700), "red")).json()
        self.assertNotEqual(first["images"][0]["de_url"], second["images"][0]["de_url"])
        self.assertTrue(second["images"][0]["manual"])
        self.assertTrue(second["images"][0]["replaced_at"])
        self.assertTrue(second["images"][0]["warnings"])

    def test_upload_of_a_paid_version_is_still_a_manual_choice(self):
        first = self.fx.write_generated_image(self.text)
        self.fx.write_refined_image(self.text, "f" * 32)
        import base64
        result = self.fx.upload(image_base64=base64.b64encode(first).decode("ascii"))
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json()["images"][0]["manual"])
        self.assertEqual(self.fx.client.get(result.json()["images"][0]["de_url"]).content, first)
        from publish import compose
        post = compose.compose_post(self.fx.post_id, datetime.now(timezone.utc),
            archive_root=cfg().archive_dir, account=self.account.name, warning_sink=None)
        self.assertEqual(post.image_paths[0].read_bytes(), first)

    def test_generation_lock_blocks_upload_and_selection(self):
        self.fx.write_generated_image(self.text)
        self.fx.write_refined_image(self.text, "f" * 32)
        chosen = self.fx.versions()[0]["out_path"]
        with images.ImageRunLock(cfg().state_dir / "images.lock"):
            self.assertEqual(self.fx.upload().status_code, 409)
            self.assertEqual(self.fx.select(chosen).status_code, 409)

    def test_same_byte_upload_adopts_without_replacing_the_file(self):
        first = self.fx.write_generated_image(self.text)
        self.fx.write_refined_image(self.text, "f" * 32)
        import base64
        with patch.object(images.os, "replace", side_effect=OSError("replace denied")) as replace:
            result = self.fx.upload(image_base64=base64.b64encode(first).decode("ascii"))
        self.assertEqual(result.status_code, 200, result.text)
        replace.assert_not_called()
        self.assertTrue(result.json()["images"][0]["manual"])

    def test_replace_failure_keeps_effective_selection(self):
        self.fx.write_generated_image(self.text)
        current = self.fx.write_refined_image(self.text, "f" * 32)
        before = self.fx.client.get(self.fx.url).json()
        with patch.object(images.os, "replace", side_effect=OSError("replace denied")):
            result = self.fx.upload()
        self.assertEqual(result.status_code, 400, result.text)
        after = self.fx.client.get(self.fx.url).json()
        self.assertEqual(after["review"], before["review"])
        self.assertFalse(after["images"][0]["manual"])
        self.assertEqual(self.fx.client.get(after["images"][0]["de_url"]).content, current)

    def test_same_byte_upload_cleanup_failure_keeps_effective_selection(self):
        first = self.fx.write_generated_image(self.text)
        ledger_path = self.account / "images_de.jsonl"
        record = json.loads(ledger_path.read_text("utf-8"))
        other = self.fx.post_dir / "media_de/01.png"
        Image.new("RGB", (1080, 1080), "purple").save(other)
        record.update(out_path=other.relative_to(self.account).as_posix(),
                      output_sha256=images.sha256_file(other))
        images.append_image_jsonl(ledger_path, record)
        import base64
        unlink = Path.unlink
        def fail_sibling(path, *args, **kwargs):
            if path == other:
                raise OSError("old format file busy")
            return unlink(path, *args, **kwargs)
        with patch.object(Path, "unlink", fail_sibling):
            result = self.fx.upload(image_base64=base64.b64encode(first).decode("ascii"))
        self.assertEqual(result.status_code, 400)
        after = self.fx.client.get(self.fx.url).json()
        self.assertFalse(after["images"][0]["manual"])
        self.assertEqual(self.fx.client.get(after["images"][0]["de_url"]).content, other.read_bytes())

    def test_transient_windows_preview_handle_does_not_break_upload(self):
        self.fx.write_generated_image(self.text)
        replace = images.os.replace
        conflict = PermissionError("preview handle")
        conflict.winerror = 32
        attempts = []
        def busy_once(source, destination):
            attempts.append(destination)
            if len(attempts) == 1:
                raise conflict
            return replace(source, destination)
        with patch.object(images.os, "replace", side_effect=busy_once):
            result = self.fx.upload()
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(len(attempts), 2)

    def test_manual_image_blocks_version_adoption_with_a_reason(self):
        self.fx.write_generated_image(self.text)
        self.fx.write_refined_image(self.text, "b" * 32)
        wanted = self.fx.versions()[1]["out_path"]
        self.assertEqual(self.fx.upload().status_code, 200)
        before = (self.account / "images_de.jsonl").read_bytes()
        result = self.fx.select(wanted)
        self.assertIn(result.status_code, {400, 409})
        self.assertIn("人工", result.text)
        self.assertEqual((self.account / "images_de.jsonl").read_bytes(), before)

    def test_version_preview_does_not_change_current_or_review(self):
        original = self.fx.write_generated_image(self.text)
        latest = self.fx.write_refined_image(self.text, "c" * 32)
        before = self.fx.client.get(self.fx.url).json()
        version = self.fx.versions()[0]
        result = self.fx.client.get(version["preview_url"])
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.content, original)
        after = self.fx.client.get(self.fx.url).json()
        self.assertEqual(after["review"], before["review"])
        self.assertEqual(self.fx.client.get(after["images"][0]["de_url"]).content, latest)

    def test_refined_human_text_versions_can_be_reselected(self):
        self.fx.write_generated_image(self.text)
        self.assertEqual(self.fx.save("Ein von Hand bearbeiteter Text. #Neakasa").status_code, 200)
        first = self.fx.write_refined_image("Ein von Hand bearbeiteter Text. #Neakasa", "d" * 32)
        self.fx.write_refined_image("Ein von Hand bearbeiteter Text. #Neakasa", "e" * 32)
        version = self.fx.versions()[1]
        self.assertTrue(version["usable"], version)
        result = self.fx.select(version["out_path"])
        self.assertEqual(result.status_code, 200, result.text)
        self.fx.assert_localized_image_visible(first)

    def test_download_readme_keeps_system_scheduling(self):
        result = self.fx.client.post(self.fx.url + "/export",
                                    json=dict(self.fx.action_body(), mode="download"))
        self.assertEqual(result.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(result.content)) as package:
            notes = package.read("README.txt").decode("utf-8")
        self.assertNotIn("系统不会代为发布", notes)
        self.assertIn("继续", notes)
        self.assertEqual(self.fx.client.get(self.fx.url).json()["status"], "pending_review")

    def test_old_model_usage_keeps_its_own_rates_after_switching(self):
        self.fx.write_generated_image(self.text)
        path = self.account / "images_de.jsonl"
        row = json.loads(path.read_text("utf-8"))
        row.update(model="gpt-image-2", usage={
            "input_tokens": 3000, "output_tokens": 1000, "total_tokens": 4000,
            "input_tokens_details": {"text_tokens": 1000, "image_tokens": 2000}})
        path.write_text(json.dumps(row) + "\n", encoding="utf-8", newline="")
        (self.account / "translated.jsonl").unlink()
        before = engine.budget_snapshot([self.account], now=datetime.now(timezone.utc))
        for model in ("gpt-image-2.5-flare", "gpt-image-2.5-sunburst"):
            with self.subTest(model=model):
                cfg()._d["image"]["model"] = model
                cfg()._d["image"]["cost_rates_usd_per_million"][model] = {
                    "text_input": 50, "image_input": 80, "image_output": 300}
                after = engine.budget_snapshot([self.account], now=datetime.now(timezone.utc))
                self.assertEqual(before, after)
                self.assertIsNone(refinement.capabilities(self.account, self.fx.post_id)["estimated_image_usd"])


if __name__ == "__main__":
    unittest.main()
