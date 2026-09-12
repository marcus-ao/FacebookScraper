"""审校台人工保存的真实归档集成测试；仅使用临时目录，无网络、付费或浏览器。"""
from __future__ import annotations

import json
import hashlib
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config, translated  # noqa: E402
from core.store import post_dirname  # noqa: E402
from web.api import app as api_app, fake_writer  # noqa: E402
import localize_images  # noqa: E402


class WebReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.account = self.root / "archive" / "fa_neakasaofficial"
        self.account.mkdir(parents=True)
        self.post_id = "1234567890"
        self.task_id = "fa_neakasaofficial/" + self.post_id
        self.url = "/api/tasks/" + self.task_id
        created = (datetime.now(timezone.utc) - timedelta(days=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        folder = "posts/" + post_dirname(self.post_id, created)
        self.post_dir = self.account / folder
        self.post_dir.mkdir(parents=True)
        Image.new("RGB", (1080, 1080), "blue").save(self.post_dir / "01.jpg")
        self.source = {
            "post_id": self.post_id, "platform": "facebook",
            "account": "neakasaofficial", "owner": "neakasaofficial",
            "coauthors": [], "created_at": created,
            "permalink": "https://www.facebook.com/neakasaofficial/posts/1234567890",
            "text": "A clean home. #Neakasa", "media_complete": True,
            "media": [{"kind": "image", "local_path": folder + "/01.jpg"}],
        }
        self.write_source()
        (self.account / "manifest.jsonl").write_text(
            json.dumps(self.source) + "\n", encoding="utf-8")
        self.write_machine("Ein sauberes Zuhause. #Neakasa")
        test_config = config.Config()
        test_config._d["paths"] = {
            "archive": str(self.root / "archive"),
            "state": str(self.root / "state"),
        }
        self.addCleanup(patch.stopall)
        patch.object(config, "_cfg", test_config).start()
        self.fake_path = self.root / "_fake_state.json"
        patch.object(fake_writer, "STATE_PATH", self.fake_path).start()
        self.client = TestClient(api_app.app)
        self.addCleanup(self.client.close)

    def write_source(self):
        (self.post_dir / "post.json").write_text(
            json.dumps(self.source, ensure_ascii=False), encoding="utf-8")

    def write_machine(self, text):
        row = {
            "post_id": self.post_id, "text_de": text,
            "translated_at": datetime.now(timezone.utc).isoformat(),
            "model": "offline-fixture", "prompt_version": translated.PROMPT_VERSION,
            "source_text_sha256": translated.source_text_sha256(self.source["text"]),
        }
        with (self.account / "translated.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def save(self, text="Von Hand verbessert. #Neakasa", **extra):
        body = {
            "text_de": text,
            "source_text_sha256": translated.source_text_sha256(self.source["text"]),
            "human_revision": self.client.get(self.url).json()["text"].get("human_revision"),
            **extra,
        }
        return self.client.put(self.url + "/text_de", json=body)

    def human_rows(self):
        path = self.account / "translated_human.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def write_generated_image(self, text_de):
        destination = self.post_dir / "media_de" / "01.jpg"
        destination.parent.mkdir(exist_ok=True)
        Image.new("RGB", (1080, 1080), "green").save(destination)
        generated = destination.read_bytes()
        row = {
            "post_id": self.post_id, "media_index": 0,
            "source_sha256": hashlib.sha256((self.post_dir / "01.jpg").read_bytes()).hexdigest(),
            "text_de_sha256": hashlib.sha256(text_de.strip().encode("utf-8")).hexdigest(),
            "prompt_version": localize_images.IMAGE_PROMPT_VERSION,
            "out_path": destination.relative_to(self.account).as_posix(),
            "output_sha256": hashlib.sha256(generated).hexdigest(),
            "model": "offline-fixture", "size_requested": "1080x1080",
            "size_returned": "1080x1080", "quality": "high",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        (self.account / "images_de.jsonl").write_text(
            json.dumps(row) + "\n", encoding="utf-8")
        return generated

    def assert_localized_image_visible(self, generated):
        detail = self.client.get(self.url).json()
        self.assertTrue(detail["images"][0]["de_present"])
        response = self.client.get(detail["images"][0]["de_url"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, generated)

    def test_updated_source_changes_image_url_and_revalidates_cached_images(self):
        before = self.client.get(self.url).json()["images"][0]["de_url"]
        self.source["text"] = "Changed source caption. #Neakasa"
        self.write_source()
        after = self.client.get(self.url).json()["images"][0]["de_url"]
        self.assertNotEqual(before, after)
        response = self.client.get(after)
        self.assertIn("no-cache", response.headers.get("Cache-Control", ""))

    def test_ordinary_human_edit_preserves_machine_based_image(self):
        generated = self.write_generated_image("Ein sauberes Zuhause. #Neakasa")
        self.assertEqual(self.save("Geänderter Begleittext. #Neakasa").status_code, 200)
        self.assert_localized_image_visible(generated)

    def test_reviewed_human_image_is_visible_when_machine_translation_is_stale(self):
        self.source["text"] = "Updated caption. #Neakasa"
        self.write_source()
        self.assertEqual(self.save("Erneut geprüfter Text. #Neakasa").status_code, 200)
        generated = self.write_generated_image("Erneut geprüfter Text. #Neakasa")
        self.assert_localized_image_visible(generated)

    def test_reviewed_human_image_is_visible_without_machine_translation(self):
        (self.account / "translated.jsonl").unlink()
        self.assertEqual(self.save("Manuell erstellter Text. #Neakasa").status_code, 200)
        generated = self.write_generated_image("Manuell erstellter Text. #Neakasa")
        self.assert_localized_image_visible(generated)

    def test_stale_human_review_does_not_claim_new_machine_image_is_reviewed(self):
        self.assertEqual(self.save().status_code, 200)
        self.source["text"] = "Updated caption. #Neakasa"
        self.write_source()
        self.write_machine("Neue Fassung. #Neakasa")
        self.write_generated_image("Neue Fassung. #Neakasa")
        detail = self.client.get(self.url).json()
        self.assertFalse(detail["images"][0]["de_present"])
        response = self.client.get(self.url + "/image/0?variant=de")
        self.assertEqual(response.content, (self.post_dir / "01.jpg").read_bytes())

    def test_save_persists_real_human_version_and_refreshes_list(self):
        response = self.save(actor="must not be stored")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue((self.account / "translated_human.jsonl").is_file())
        row = self.human_rows()[0]
        self.assertEqual(row["text_de"], "Von Hand verbessert. #Neakasa")
        self.assertIsNone(row["actor"])
        self.assertTrue(row["revision"])
        self.assertFalse(self.fake_path.exists())
        detail = self.client.get(self.url).json()
        self.assertEqual(detail["text"]["de_human"], "Von Hand verbessert. #Neakasa")
        self.assertEqual(detail["text"]["de_machine"], "Ein sauberes Zuhause. #Neakasa")
        self.assertEqual(detail["status"], "edited")
        self.assertEqual(detail["trail"][-1]["action"], "text_edited")
        self.assertIsNone(detail["trail"][-1]["actor"])
        listed = self.client.get("/api/tasks").json()["tasks"][0]
        self.assertEqual(listed["status"], "edited")
        self.assertEqual(listed["text_de_excerpt"], "Von Hand verbessert. #Neakasa")

    def test_machine_retranslation_cannot_replace_human_text(self):
        self.assertEqual(self.save().status_code, 200)
        self.write_machine("Neue maschinelle Fassung. #Neakasa")
        detail = self.client.get(self.url).json()
        self.assertEqual(detail["text"]["de_human"], "Von Hand verbessert. #Neakasa")
        self.assertEqual(detail["text"]["de_machine"], "Neue maschinelle Fassung. #Neakasa")

    def test_successive_saves_append_revisions(self):
        self.assertEqual(self.save("Erste Fassung").status_code, 200)
        self.assertEqual(self.save("Zweite Fassung").status_code, 200)
        self.assertTrue((self.account / "translated_human.jsonl").is_file())
        rows = self.human_rows()
        self.assertEqual([row["text_de"] for row in rows], ["Erste Fassung", "Zweite Fassung"])
        self.assertNotEqual(rows[0]["revision"], rows[1]["revision"])

    def test_older_browser_tab_cannot_overwrite_a_newer_human_revision(self):
        self.assertEqual(self.save("Aus dem ersten Tab").status_code, 200)
        response = self.save("Aus dem veralteten Tab", human_revision=None)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.client.get(self.url).json()["text"]["de_human"],
                         "Aus dem ersten Tab")

    def test_another_archive_writer_cannot_race_the_web_save(self):
        append = translated.append_human_translation

        def concurrent_append(path, source, text_de, **kwargs):
            append(path, source, "Anderweitig gespeicherter Entwurf")
            return append(path, source, text_de, **kwargs)

        with patch.object(translated, "append_human_translation", concurrent_append):
            response = self.save("Veralteter Entwurf")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.human_rows()[-1]["text_de"], "Anderweitig gespeicherter Entwurf")

    def test_old_demo_state_cannot_override_real_text_or_status(self):
        self.fake_path.write_text(json.dumps({"schema_version": 1, "tasks": {
            self.task_id: {"text_de_human": "Old demo", "status": "approved",
                           "trail": [{"actor": "demo", "action": "approved"}]},
        }}), encoding="utf-8")
        self.assertEqual(self.save().status_code, 200)
        detail = self.client.get(self.url).json()
        self.assertEqual(detail["text"]["de_human"], "Von Hand verbessert. #Neakasa")
        self.assertEqual(detail["status"], "edited")
        self.assertEqual(detail["trail"][-1]["action"], "text_edited")

    def test_source_changes_require_review_but_preserve_human_work(self):
        old_hash = translated.source_text_sha256(self.source["text"])
        self.assertEqual(self.save().status_code, 200)
        self.source["text"] = "An updated source post. #Neakasa"
        self.write_source()  # manifest deliberately remains stale: post.json is authoritative.
        rejected = self.save("Based on old source", source_text_sha256=old_hash)
        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.assertEqual(len(self.human_rows()), 1)
        detail = self.client.get(self.url).json()
        self.assertEqual(detail["text"]["en"], "An updated source post. #Neakasa")
        self.assertTrue(detail["text"]["stale"])
        self.assertEqual(detail["text"]["de_human"], "Von Hand verbessert. #Neakasa")
        self.assertEqual(detail["status"], "pending_review")
        self.assertEqual(self.save("Nach erneuter Prüfung. #Neakasa").status_code, 200)
        self.assertFalse(self.client.get(self.url).json()["text"]["stale"])

    def test_advisory_content_checks_do_not_reject_human_save(self):
        response = self.save("Eigene Freigabe mit 42 EUR und #AnderesThema")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["highlights"])

    def test_empty_or_invalid_input_never_writes_human_archive(self):
        for text in [None, "", " \n ", 123, ["text"]]:
            with self.subTest(text=text):
                self.assertEqual(self.save(text).status_code, 400)
        for digest in [None, "", 123]:
            with self.subTest(digest=digest):
                self.assertEqual(self.save(source_text_sha256=digest).status_code, 400)
        self.assertFalse((self.account / "translated_human.jsonl").exists())

    def test_unknown_and_traversal_tasks_never_write(self):
        for task in ["fa_neakasaofficial/missing", "..%2Foutside%2F1234567890",
                     "fa_neakasaofficial%2F..%2F..%2Foutside"]:
            with self.subTest(task=task):
                response = self.client.put("/api/tasks/" + task + "/text_de", json={
                    "text_de": "Rejected", "source_text_sha256": "a" * 64,
                })
                self.assertEqual(response.status_code, 404, response.text)
        self.assertFalse((self.account / "translated_human.jsonl").exists())

    def test_unimplemented_decisions_never_claim_success(self):
        for action in ["approve", "skip"]:
            response = self.client.post(self.url + "/" + action, json={"reason": "test"})
            self.assertEqual(response.status_code, 501, response.text)
        self.assertFalse(self.fake_path.exists())
        self.assertEqual(self.client.get(self.url).json()["status"], "pending_review")


if __name__ == "__main__":
    unittest.main()
