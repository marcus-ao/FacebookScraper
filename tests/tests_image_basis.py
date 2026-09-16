"""Image selection stays aligned across text candidates, review and composition."""
import io
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests_web_review as fixtures
from core import translated
from core.config import cfg
from localize import images
from pipeline import engine, refinement
from publish import compose


class ImageBasisTests(unittest.TestCase):
    def setUp(self):
        self.fx = fixtures.WebReviewTests()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.text = "Ein sauberes Zuhause. #Neakasa"

    def compose(self):
        return compose.compose_post(
            self.fx.post_id, datetime.now(timezone.utc), archive_root=cfg().archive_dir,
            account=self.fx.account.name, warning_sink=None)

    def generate_text_candidate(self):
        detail = self.fx.client.get(self.fx.url).json()
        with patch.object(engine, "budget_preflight"):
            job = refinement.submit(
                self.fx.account, self.fx.source, kind="text", instruction="语气更自然",
                source_text_sha256=detail["text"]["source_text_sha256"],
                human_revision=detail["text"]["human_revision"],
                review_revision=detail["review"]["revision"], executor=Mock())
            result = refinement.execute(job, self.fx.source, translator=SimpleNamespace(
                translate=lambda text, system: "Ein neuer Modellvorschlag. #Neakasa"))
        self.assertEqual(result["status"], "succeeded")

    def assert_current(self, expected):
        self.fx.assert_localized_image_visible(expected)
        jobs, _, stats = images.build_jobs(images.Settings(), self.fx.account, [self.fx.source])
        self.assertEqual(jobs, [], "A text candidate must not enqueue another paid image")
        self.assertEqual(stats.skipped_current, 1)
        self.assertEqual(self.compose().image_paths[0].read_bytes(), expected)

    def test_unadopted_text_candidate_keeps_machine_image_and_human_caption(self):
        expected = self.fx.write_generated_image(self.text)
        self.assertEqual(self.fx.save("Von Hand bewahrt. #Neakasa").status_code, 200)
        human_path = self.fx.account / "translated_human.jsonl"
        before_human = human_path.read_bytes()
        before_images = (self.fx.account / "images_de.jsonl").read_bytes()
        self.generate_text_candidate()
        self.assertEqual(human_path.read_bytes(), before_human)
        self.assertEqual((self.fx.account / "images_de.jsonl").read_bytes(), before_images)
        self.assert_current(expected)
        forced, _, _ = images.build_jobs(
            images.Settings(), self.fx.account, [self.fx.source], force=True,
            allow_manual_refine=True)
        self.assertEqual(forced[0].text_de, "Von Hand bewahrt. #Neakasa")

    def test_reselected_image_and_history_remain_usable_after_text_candidate(self):
        expected = self.fx.write_generated_image(self.text)
        self.fx.write_refined_image(self.text, "a" * 32)
        original = self.fx.versions()[0]
        self.assertEqual(self.fx.select(original["out_path"]).status_code, 200)
        self.generate_text_candidate()
        self.assert_current(expected)
        versions = self.fx.versions()
        self.assertTrue(all(item["usable"] for item in versions), versions)
        self.assertEqual(self.fx.select(versions[1]["out_path"]).status_code, 200)
        self.assertEqual(self.compose().image_paths[0].name, "01_v" + "a" * 32 + ".jpg")

    def test_image_prompt_upgrade_is_rejected_by_review_and_composition(self):
        self.fx.write_generated_image(self.text)
        path = self.fx.account / "images_de.jsonl"
        row = json.loads(path.read_text("utf-8"))
        row["prompt_version"] = images.IMAGE_PROMPT_VERSION - 1
        path.write_text(json.dumps(row) + "\n", encoding="utf-8", newline="")
        self.assertFalse(self.fx.client.get(self.fx.url).json()["images"][0]["de_present"])
        with self.assertRaises(compose.ComposeError):
            self.compose()

    def test_non_candidate_translation_change_is_rejected_by_composition(self):
        self.fx.write_generated_image(self.text)
        self.fx.write_machine("Ein vollständig neu übersetzter Text. #Neakasa")
        self.assertFalse(self.fx.client.get(self.fx.url).json()["images"][0]["de_present"])
        with self.assertRaises(compose.ComposeError):
            self.compose()

    def test_historical_basis_cannot_survive_a_changed_source_caption(self):
        self.fx.write_generated_image(self.text)
        self.generate_text_candidate()
        self.fx.source["text"] = "A changed source caption. #Neakasa"
        self.fx.write_source()
        self.fx.write_machine("Eine Übersetzung der geänderten Quelle. #Neakasa")
        self.generate_text_candidate()
        self.assertFalse(self.fx.client.get(self.fx.url).json()["images"][0]["de_present"])
        jobs, _, _ = images.build_jobs(images.Settings(), self.fx.account, [self.fx.source])
        self.assertEqual(len(jobs), 1)
        with self.assertRaises(compose.ComposeError):
            self.compose()

    def test_historical_basis_requires_a_current_translation_prompt(self):
        self.fx.write_generated_image(self.text)
        path = self.fx.account / "translated.jsonl"
        row = json.loads(path.read_text("utf-8"))
        row["prompt_version"] = translated.PROMPT_VERSION - 1
        path.write_text(json.dumps(row) + "\n", encoding="utf-8", newline="")
        self.fx.write_machine("Ein aktueller Text. #Neakasa")
        self.generate_text_candidate()
        self.assertFalse(self.fx.client.get(self.fx.url).json()["images"][0]["de_present"])
        jobs, _, _ = images.build_jobs(images.Settings(), self.fx.account, [self.fx.source])
        self.assertEqual(len(jobs), 1)
        with self.assertRaises(compose.ComposeError):
            self.compose()

    def test_failed_image_refinement_keeps_previous_selection_and_attempt(self):
        expected = self.fx.write_generated_image(self.text)
        output = io.BytesIO()
        Image.new("RGB", (1088, 1088), "purple").save(output, "JPEG")
        validated = images.ValidatedImage(output.getvalue(), 1088, 1088, "JPEG", 1, 0.02)
        editor = Mock(paid_request_id="")
        editor.edit.return_value = images.EditResult("", "gpt-image-2", {}, "response")
        with patch.object(engine, "budget_preflight"):
            job = refinement.submit(
                self.fx.account, self.fx.source, kind="image", instruction="调整图片 CTA",
                source_text_sha256=translated.source_text_sha256(self.fx.source["text"]),
                human_revision=None, review_revision=None, media_index=0, executor=Mock())
            with patch.object(images, "validate_output", return_value=validated), patch.object(
                    images.os, "link", side_effect=PermissionError("output file denied")):
                result = refinement.execute(job, self.fx.source, editor=editor)
        self.assertEqual(result["status"], "failed")
        self.assert_current(expected)
        state = images.load_image_state(self.fx.account / "images_de.jsonl")
        self.assertTrue(state.latest[(self.fx.post_id, 0)]["selected_from"])
        self.assertEqual(sum(row.get("refine_id") == job["job_id"] for row in state.records), 1)
        self.assertEqual(refinement.capabilities(self.fx.account, self.fx.post_id)["image_attempts"]["0"], 1)


if __name__ == "__main__":
    unittest.main()
