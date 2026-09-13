"""审校台人工保存的真实归档集成测试；仅使用临时目录，无网络、付费或浏览器。"""
from __future__ import annotations

import json
import hashlib
import io
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config, review, translated  # noqa: E402
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
        detail = self.client.get(self.url).json()
        body = {
            "text_de": text,
            "source_text_sha256": translated.source_text_sha256(self.source["text"]),
            "human_revision": detail["text"].get("human_revision"),
            "review_revision": detail.get("review", {}).get("revision"),
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

    def test_approval_without_verified_browser_fails_without_success_record(self):
        options = self.client.get(self.url + "/approval-options")
        self.assertEqual(options.status_code, 200, options.text)
        self.assertFalse(options.json()["available"])
        self.assertTrue(options.json()["reason"])
        body = {**self.action_body(), "human_revision": None,
                "scheduled_at": "2026-09-20T10:00", "content_fingerprint": "not-a-real-fingerprint"}
        response = self.client.post(self.url + "/approve", json=body)
        self.assertIn(response.status_code, {400, 409}, response.text)
        self.assertEqual(review.history(self.account), [])

    def action_body(self, **extra):
        detail = self.client.get(self.url).json()
        return {"source_text_sha256": detail["text"]["source_text_sha256"],
                "review_revision": detail.get("review", {}).get("revision"), **extra}

    def act(self, action, **extra):
        return self.client.post(self.url + "/review", json=self.action_body(action=action, **extra))

    def test_skip_requires_reason_and_survives_reload(self):
        self.assertEqual(self.act("skipped", reason=" ").status_code, 400)
        result = self.act("skipped", reason="德国站没有这个促销")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.client.get(self.url).json()["status"], "skipped")
        self.assertEqual(self.client.get("/api/tasks?status=pending_review").json()["tasks"], [])
        skipped = self.client.get("/api/tasks?status=skipped").json()["tasks"]
        self.assertEqual(skipped[0]["review"]["reason"], "德国站没有这个促销")

    def test_snooze_resume_and_stale_review_version(self):
        old = self.action_body(action="skipped", reason="旧页面决定")
        result = self.act("snoozed")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["status"], "snoozed")
        self.assertTrue(result.json()["review"]["wake_at"])
        self.assertEqual(self.client.post(self.url + "/review", json=old).status_code, 409)
        resumed = self.act("woke")
        self.assertEqual(resumed.status_code, 200, resumed.text)
        self.assertEqual(resumed.json()["status"], "pending_review")

    def test_terminal_decision_blocks_late_text_save(self):
        self.assertEqual(self.act("skipped", reason="不发").status_code, 200)
        response = self.save("不应覆盖已结束决定")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertFalse((self.account / "translated_human.jsonl").exists())

    def test_export_zip_contains_current_text_and_explicit_original_fallback(self):
        self.assertEqual(self.save("Von Hand verbessert. #Neakasa").status_code, 200)
        result = self.client.post(self.url + "/export", json=self.action_body())
        self.assertEqual(result.status_code, 200, result.text[:100] if result.status_code != 200 else "")
        self.assertEqual(result.headers["content-type"], "application/zip")
        with zipfile.ZipFile(io.BytesIO(result.content)) as package:
            self.assertEqual(package.read("text_de.txt").decode("utf-8"), "Von Hand verbessert. #Neakasa")
            metadata = json.loads(package.read("metadata.json"))
            self.assertTrue(metadata["images"][0]["used_original"])
            self.assertIn("原图", package.read("README.txt").decode("utf-8"))
            image = package.read(metadata["images"][0]["file"])
            self.assertEqual(image, (self.post_dir / "01.jpg").read_bytes())
        self.assertEqual(self.client.get(self.url).json()["status"], "handed_off")
        before = len(review.history(self.account))
        self.assertEqual(self.client.post(self.url + "/export", json=self.action_body()).status_code, 200)
        self.assertEqual(len(review.history(self.account)), before)

    def test_export_uses_localized_image_and_failed_export_does_not_handoff(self):
        generated = self.write_generated_image("Ein sauberes Zuhause. #Neakasa")
        result = self.client.post(self.url + "/export", json=self.action_body())
        self.assertEqual(result.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(result.content)) as package:
            meta = json.loads(package.read("metadata.json"))
            self.assertFalse(meta["images"][0]["used_original"])
            self.assertEqual(package.read(meta["images"][0]["file"]), generated)

    def test_export_rejects_missing_or_outside_media_without_changing_state(self):
        for path in ["../outside.jpg", "missing.jpg"]:
            with self.subTest(path=path):
                self.source["media"][0]["local_path"] = path
                self.write_source()
                result = self.client.post(self.url + "/export", json=self.action_body())
                self.assertEqual(result.status_code, 409, result.text)
                self.assertEqual(self.client.get(self.url).json()["status"], "pending_review")

    def test_handoff_link_can_be_added_later(self):
        self.assertEqual(self.act("handed_off").status_code, 200)
        updated = self.act("handoff_link", handoff_url="https://www.facebook.com/posts/456")
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["review"]["handoff_url"], "https://www.facebook.com/posts/456")
        self.assertEqual(self.act("woke").status_code, 409)

    def localization_body(self, **extra):
        detail = self.client.get(self.url).json()
        draft = detail["localization"]
        return {"body_de": draft["body_de"], "tags": draft["tags"], "links": draft["links"],
                "hashtags_confirmed": draft["hashtags_confirmed"], "ig_cta": draft["ig_cta"],
                "source_text_sha256": detail["text"]["source_text_sha256"],
                "human_revision": detail["text"]["human_revision"],
                "review_revision": detail["review"]["revision"], "localization_revision": draft["revision"], **extra}

    def save_localization(self, **extra):
        return self.client.put(self.url + "/localization", json=self.localization_body(**extra))

    def test_localization_saves_three_blocks_as_bound_human_version(self):
        self.source["text"] += " https://us.example/product #CatLover"
        self.write_source()
        self.write_machine("Ein sauberes Zuhause. #Neakasa #CatLover")
        response = self.save_localization(body_de="Von Menschen geprüft.", tags=["#Neakasa", "#Katzenliebe"],
            hashtags_confirmed=True, links=[{"source_url": "https://us.example/product",
                                            "target_url": "https://de.example/product", "confirmed": True}])
        self.assertEqual(response.status_code, 200, response.text)
        detail = response.json()
        self.assertTrue(detail["localization_validation"]["ready"])
        self.assertTrue(detail["localization"]["has_record"])
        self.assertEqual(detail["localization"]["body_de"], "Von Menschen geprüft.")
        self.assertIn("https://de.example/product", self.human_rows()[-1]["text_de"])
        self.assertIn("#Katzenliebe", self.human_rows()[-1]["text_de"])
        self.assertEqual(self.client.get(self.url).json()["localization"]["tags"], ["#Neakasa", "#Katzenliebe"])
        self.write_machine("Maschine darf die Auswahl nicht ersetzen. #Neakasa #CatLover")
        self.assertTrue(self.client.get(self.url).json()["localization_validation"]["ready"])

    def test_localization_conflict_does_not_append_any_new_human_version(self):
        old = self.localization_body()
        response = self.save_localization(body_de="First human choice.")
        self.assertEqual(response.status_code, 200, response.text)
        latest = self.localization_body(body_de="Another choice.")
        latest["localization_revision"] = old["localization_revision"]
        self.assertEqual(self.client.put(self.url + "/localization", json=latest).status_code, 409)
        self.assertEqual(len(self.human_rows()), 1)
        self.assertEqual(self.client.put(self.url + "/localization", json=old).status_code, 409)
        self.assertEqual(len(self.human_rows()), 1)

    def test_localization_incomplete_choice_can_save_but_not_claim_ready(self):
        self.source["text"] += " https://us.example/product #CatLover"
        self.write_source()
        self.write_machine("Noch zu prüfen. #Neakasa #CatLover")
        response = self.save_localization()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["localization_validation"]["ready"])
        self.assertTrue(response.json()["localization"]["has_record"])

    def test_localization_rejects_hidden_urls_brand_changes_or_dropped_source_link(self):
        self.source["text"] += " https://us.example/product"
        self.write_source()
        self.write_machine("Noch zu prüfen. #Neakasa")
        for extra in ({"body_de": "URL https://us.example/new"}, {"tags": []}, {"links": []},
                      {"body_de": "Hashtag #new"}):
            with self.subTest(extra=extra):
                self.assertEqual(self.save_localization(**extra).status_code, 400)
        self.assertFalse((self.account / "translated_human.jsonl").exists())

    def test_instagram_three_blocks_remove_urls_keep_custom_cta_and_warn_only(self):
        relative = self.post_dir.relative_to(self.account)
        self.account = self.account.rename(self.root / 'archive' / 'in_neakasa.global')
        self.post_dir = self.account / relative
        self.task_id = self.account.name + '/' + self.post_id
        self.url = '/api/tasks/' + self.task_id
        self.source['account'] = self.source['owner'] = 'neakasa.global'
        self.source["platform"] = "instagram"
        self.source["text"] += " https://us.example/product"
        self.write_source()
        (self.account / "manifest.jsonl").write_text(json.dumps(self.source) + "\n", encoding="utf-8")
        self.write_machine("Ein Zuhause. #Neakasa")
        long_body = "😀" * 2201
        tags = ["#Neakasa"] + ["#Tag%d" % index for index in range(30)]
        response = self.save_localization(body_de=long_body, tags=tags, hashtags_confirmed=True,
                                          ig_cta="Mehr Infos in unserem Profil 🔗")
        self.assertEqual(response.status_code, 200, response.text)
        detail = response.json()
        self.assertEqual(detail["localization_validation"]["hashtag_count"], 31)
        self.assertEqual(detail["localization"]["body_de"], long_body)
        self.assertTrue(detail["localization_validation"]["ready"])
        self.assertEqual(len(detail["localization_validation"]["warnings"]), 2)
        self.assertNotIn("https://", detail["text"]["de_human"])
        self.assertIn("Mehr Infos in unserem Profil 🔗", detail["text"]["de_human"])

    def test_refinement_accepts_real_job_and_polling_without_calling_model(self):
        from pipeline import refinement
        from unittest.mock import Mock
        executor = Mock()
        detail = self.client.get(self.url).json()
        body = {"kind": "text", "instruction": "更自然，但保留型号", **self.action_body(),
                "human_revision": detail["text"]["human_revision"]}
        endpoint = "/api/refinements/task/" + self.task_id
        with patch.object(refinement, "_executor", executor), patch.object(refinement.engine, "budget_preflight"):
            response = self.client.post(endpoint, json=body)
        self.assertEqual(response.status_code, 202, response.text)
        job = response.json()
        self.assertEqual(job["status"], "pending")
        self.assertEqual(executor.submit.call_count, 1)
        polled = self.client.get("/api/refinements/jobs/" + job["job_id"])
        self.assertEqual(polled.status_code, 200)
        self.assertEqual(polled.json()["instruction"], body["instruction"])
        caps = self.client.get(endpoint).json()
        self.assertEqual(caps["jobs"][0]["job_id"], job["job_id"])
        self.assertIn("estimate_basis", caps)
        with patch.object(refinement, "_executor", executor), patch.object(refinement.engine, "budget_preflight"):
            repeated = self.client.post(endpoint, json=body)
        self.assertEqual(repeated.status_code, 409)
        self.assertEqual(executor.submit.call_count, 1)
        self.assertFalse((self.account / "translated_human.jsonl").exists())

    def test_refinement_validation_budget_and_templates_are_read_only(self):
        from pipeline import engine, refinement
        endpoint = "/api/refinements/task/" + self.task_id
        body = {"kind": "text", "instruction": "更自然", **self.action_body(), "human_revision": None}
        with patch.object(refinement, "submit", side_effect=engine.BudgetStopped("测试预算已停止")):
            self.assertEqual(self.client.post(endpoint, json=body).status_code, 409)
        self.assertEqual(self.client.post(endpoint, json={**body, "kind": []}).status_code, 400)
        self.assertEqual(self.client.get("/api/refinements/jobs/" + "0" * 32).status_code, 404)
        template = self.client.get("/api/templates/text")
        self.assertEqual(template.status_code, 200)
        self.assertEqual(template.json()["content"], (ROOT / "prompts" / "translate_de.md").read_text(encoding="utf-8"))
        self.assertEqual(self.client.put("/api/templates/text", json={"content": "bad"}).status_code, 405)

    def test_hashtag_suggestions_are_candidates_only_and_do_not_save(self):
        from pipeline import hashtag_suggestions
        result = {"groups": [{"source_tag": "#CatLover", "protected": False,
                    "candidates": [{"tag": "#Katzenliebe", "signals": [], "current_signals": []}]}],
                  "selected": ["#Katzenliebe"], "notice": "未采样，语义建议", "generated_at": "2026-09-12T02:00:00Z"}
        body = {**self.action_body(), "human_revision": None}
        with patch.object(hashtag_suggestions, "suggest", return_value=result) as suggest:
            response = self.client.post("/api/hashtags/task/" + self.task_id, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["notice"], "未采样，语义建议")
        self.assertEqual(suggest.call_count, 1)
        self.assertFalse((self.account / "translated_human.jsonl").exists())
        self.assertFalse((self.account / "localization.jsonl").exists())

    def test_list_uses_sql_candidates_and_falls_back_only_when_unavailable(self):
        from web.api import query_index
        fresh = {"available": True, "stale": False, "rebuilt_at": "2026-09-12T02:00:00Z", "error": None}
        with patch.object(query_index, "candidates", return_value={"task_ids": [], "index": fresh}) as candidates:
            response = self.client.get("/api/tasks?tag=Missing")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["tasks"], [])
        self.assertEqual(response.json()["index"], fresh)
        self.assertEqual(candidates.call_args.kwargs["tag"], "Missing")
        stale = {**fresh, "stale": True, "error": "busy"}
        with patch.object(query_index, "candidates", return_value={"task_ids": None, "index": stale}):
            response = self.client.get("/api/tasks")
        self.assertEqual(len(response.json()["tasks"]), 1)
        self.assertTrue(response.json()["index"]["stale"])

    def test_malformed_review_action_is_client_error(self):
        for action in (None, [], {}, 123):
            with self.subTest(action=action):
                self.assertEqual(self.act(action).status_code, 400)

    def test_tags_edit_persists_filters_and_rejects_old_tags_version(self):
        detail = self.client.get(self.url).json()
        body = {"tags": [" M1 Pro ", "Campaign", "M1 Pro"],
                "tags_revision": detail.get("tags_revision"),
                "source_text_sha256": detail["text"]["source_text_sha256"]}
        response = self.client.put(self.url + "/tags", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["tags"], ["M1 Pro", "Campaign"])
        persisted = json.loads((self.post_dir / "post.json").read_text(encoding="utf-8"))
        self.assertEqual(persisted["tags"], ["M1 Pro", "Campaign"])
        self.assertEqual(len(self.client.get("/api/tasks?tag=M1%20Pro").json()["tasks"]), 1)
        self.assertEqual(self.client.get("/api/tasks?tag=Other").json()["tasks"], [])
        self.assertEqual(self.client.put(self.url + "/tags", json={**body, "tags": ["Other"]}).status_code, 409)


if __name__ == "__main__":
    unittest.main()
