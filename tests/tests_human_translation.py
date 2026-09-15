"""人工译文的真实文件契约与消费者回归；无网络、无模型调用。"""
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image

from localize import images as images
from core import translated as T
from core.console import force_utf8
from core.store import Archive, Media, Post
from tools import review_report as review

force_utf8()


class HumanTranslationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.arc = Archive(Path(self.temp.name) / "archive", "in_acme")
        self.post = Post("p1", "instagram", "acme", "Original caption",
                         "2026-09-10T12:00:00Z", owner="acme")
        self.arc.append(self.post)
        self.source = self.post.to_row()
        self.machine_path = self.arc.base / "translated.jsonl"
        self.human_path = self.arc.base / "translated_human.jsonl"
        self.copy = self.arc.post_dir(self.post) / "text_de.txt"

    def machine(self, text="Maschinelle Fassung", source=None):
        row = {"post_id": "p1", "text_de": text,
               "source_text_sha256": hashlib.sha256(
                   (source or self.source)["text"].encode("utf-8")).hexdigest(),
               "translated_at": "2026-09-10T12:01:00Z", "model": "fake",
               "prompt_version": T.PROMPT_VERSION}
        T.append_translated(self.machine_path, row)
        return row

    def save(self, text="Von Hand verbessert"):
        self.assertTrue(callable(getattr(T, "append_human_translation", None)),
                        "人工修改必须有独立的耐久写入入口")
        return T.append_human_translation(self.human_path, self.source, text)

    def test_edits_append_without_mutating_paid_translation(self):
        self.machine()
        before = self.machine_path.read_bytes()
        first = self.save()
        second = self.save("Zweite menschliche Fassung")
        self.assertEqual(self.machine_path.read_bytes(), before)
        self.assertEqual(len(self.human_path.read_text(encoding="utf-8").splitlines()), 2)
        self.assertEqual(T.load_human_translated(self.human_path)["p1"], second)
        self.assertNotEqual(first["revision"], second["revision"])
        UUID(second["revision"])
        self.assertIsNone(second["actor"])
        self.assertEqual(second["source_text_sha256"],
                         hashlib.sha256(b"Original caption").hexdigest())

    def test_human_survives_machine_prompt_upgrade_and_source_change(self):
        human = self.save()
        machine = self.machine()
        machine["prompt_version"] = -1
        selected = T.effective_translation(self.source, machine, human)
        self.assertTrue(T.translation_is_current(self.source, selected))
        self.assertTrue(selected["is_human"])
        self.assertNotIn("is_human", human)
        changed = dict(self.source, text="Changed original")
        selected = T.effective_translation(changed, self.machine(source=changed), human)
        self.assertEqual(selected["text_de"], "Von Hand verbessert")
        self.assertTrue(selected["stale"])
        self.assertFalse(T.translation_is_current(changed, selected))

    def test_invalid_edits_never_create_ledger(self):
        self.assertTrue(callable(getattr(T, "append_human_translation", None)))
        for text in ("", " \n ", None):
            with self.subTest(text=text), self.assertRaises(ValueError):
                T.append_human_translation(self.human_path, self.source, text)
        with self.assertRaises(ValueError):
            T.append_human_translation(self.human_path, dict(self.source, post_id=""), "Deutsch")
        with self.assertRaises(ValueError):
            T.append_human_translation(self.human_path, self.source, "Deutsch",
                                       now=datetime(2026, 9, 10))
        self.assertFalse(self.human_path.exists())

    def test_damaged_tail_does_not_hide_later_saved_edit(self):
        self.save()
        with self.human_path.open("ab") as handle:
            handle.write(b'{"post_id":"p1","text_de":"\xe4')
        saved = self.save("Nach dem Abbruch")
        with self.human_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"post_id": "p1", "text_de": "Malformed"}) + "\n")
        self.assertEqual(T.load_human_translated(self.human_path)["p1"], saved)

    def test_expected_revision_prevents_stale_browser_overwrite(self):
        first = self.save()
        try:
            second = T.append_human_translation(
                self.human_path, self.source, "Neuer Entwurf",
                expected_revision=first["revision"])
        except TypeError as exc:
            self.fail(f"保存接口必须在账本锁内支持版本比较：{exc}")
        before = self.human_path.read_bytes()
        for stale_revision in (None, first["revision"]):
            with self.subTest(revision=stale_revision), self.assertRaises(T.HumanRevisionConflict):
                T.append_human_translation(
                    self.human_path, self.source, "Alter Browserentwurf",
                    expected_revision=stale_revision)
        self.assertEqual(self.human_path.read_bytes(), before)
        self.assertEqual(T.load_human_translated(self.human_path)["p1"], second)

    def test_linked_human_ledger_is_neither_read_nor_modified(self):
        self.save()
        target = Path(self.temp.name) / "outside.jsonl"
        target.write_bytes(self.human_path.read_bytes())
        self.human_path.unlink()
        os.link(target, self.human_path)
        before = target.read_bytes()
        with self.assertRaises(ValueError):
            T.load_human_translated(self.human_path)
        with self.assertRaises(ValueError):
            T.append_human_translation(self.human_path, self.source, "Do not write")
        self.assertEqual(target.read_bytes(), before)

    def test_explicit_confirmation_resolves_imported_legacy_staleness(self):
        self.machine()
        self.copy.write_text("Mein alter Entwurf", encoding="utf-8")
        review.run_review(self.arc.base)
        saved = self.save("Mein alter Entwurf")
        selected = T.effective_translation(self.source, self.machine(), saved)
        self.assertTrue(T.translation_is_current(self.source, selected))
        self.assertFalse(selected["stale"])
        review.run_review(self.arc.base)
        self.assertEqual(self.copy.read_text(encoding="utf-8"), "Mein alter Entwurf")

    def test_review_exports_human_when_no_machine_translation_exists(self):
        self.save()
        self.assertEqual(review.run_review(self.arc.base), 1)
        self.assertEqual(self.copy.read_text(encoding="utf-8"), "Von Hand verbessert")
        self.assertIn("Von Hand verbessert", (self.arc.base / "review.md").read_text(encoding="utf-8"))

    def test_report_preserves_unknown_legacy_text_as_unbound_human(self):
        self.machine()
        self.copy.write_text("Meine bisherige Handarbeit", encoding="utf-8")
        review.run_review(self.arc.base)
        self.assertEqual(self.copy.read_text(encoding="utf-8"), "Meine bisherige Handarbeit")
        human = T.load_human_translated(self.human_path)["p1"]
        self.assertIsNone(human["source_text_sha256"])
        selected = T.effective_translation(self.source, self.machine(), human)
        self.assertTrue(selected["stale"])
        before = self.human_path.read_bytes()
        review.run_review(self.arc.base)
        self.assertEqual(self.human_path.read_bytes(), before)
        self.assertIn("复核", (self.arc.base / "review.md").read_text(encoding="utf-8"))

    def test_legacy_file_cannot_replace_already_saved_human_version(self):
        self.machine()
        saved = self.save("Im Browser gespeichert")
        before = self.human_path.read_bytes()
        self.copy.write_text("Unbekannter alter Entwurf", encoding="utf-8")
        review.run_review(self.arc.base)
        self.assertEqual(self.human_path.read_bytes(), before)
        self.assertEqual(T.load_human_translated(self.human_path)["p1"], saved)
        self.assertEqual(self.copy.read_text(encoding="utf-8"), "Unbekannter alter Entwurf")
        report = (self.arc.base / "review.md").read_text(encoding="utf-8")
        self.assertIn("Im Browser gespeichert", report)
        self.assertIn("text_de.txt", report)
        self.assertIn("保留", report)

    def test_browser_save_during_legacy_import_keeps_both_versions(self):
        self.machine()
        self.copy.write_text("Unbekannter alter Entwurf", encoding="utf-8")
        real_import = review.preserve_legacy_translation

        def save_browser_then_import(*args, **kwargs):
            self.save("Gleichzeitig im Browser gespeichert")
            return real_import(*args, **kwargs)

        # 在真实迁移写入之前交错一次真实网页写入，稳定重现两个进程的顺序。
        with patch.object(review, "preserve_legacy_translation", save_browser_then_import):
            review.run_review(self.arc.base)
        self.assertEqual(T.load_human_translated(self.human_path)["p1"]["text_de"],
                         "Gleichzeitig im Browser gespeichert")
        self.assertEqual(self.copy.read_text(encoding="utf-8"), "Unbekannter alter Entwurf")
        report = (self.arc.base / "review.md").read_text(encoding="utf-8")
        self.assertIn("重新", report)
        before = self.human_path.read_bytes()
        review.run_review(self.arc.base)
        self.assertEqual(self.human_path.read_bytes(), before)
        self.assertEqual(self.copy.read_text(encoding="utf-8"), "Unbekannter alter Entwurf")

    def test_busy_ledger_leaves_legacy_file_for_retry(self):
        self.machine()
        self.copy.write_text("Noch nicht importiert", encoding="utf-8")
        with T.paid_model.FileLock(self.human_path.with_suffix(".lock"), busy_message="test"):
            try:
                review.run_review(self.arc.base)
            except T.paid_model.FileLockBusy as exc:
                self.fail(f"账本正在保存时应保留旧文件并提示重跑：{exc}")
        self.assertEqual(self.copy.read_text(encoding="utf-8"), "Noch nicht importiert")
        self.assertFalse(self.human_path.exists())
        report = (self.arc.base / "review.md").read_text(encoding="utf-8")
        self.assertIn("text_de.txt", report)
        self.assertIn("重新", report)

    def test_report_uses_post_truth_after_human_confirms_changed_source(self):
        self.machine()
        self.source["text"] = "Updated source truth"
        truth = self.arc.post_dir(self.post) / "post.json"
        truth.write_text(json.dumps(self.source), encoding="utf-8")
        self.save("Nach Prüfung der neuen Quelle")
        review.run_review(self.arc.base)
        report = (self.arc.base / "review.md").read_text(encoding="utf-8")
        self.assertIn("Updated source truth", report)
        self.assertNotIn("Original caption", report)
        self.assertIn("当前有效译文 1 篇", report)
        self.assertEqual(self.copy.read_text(encoding="utf-8"), "Nach Prüfung der neuen Quelle")

    def test_unreadable_post_truth_never_imports_or_overwrites_text_copy(self):
        self.machine()
        self.copy.write_text("Unbekannter alter Entwurf", encoding="utf-8")
        truth = self.arc.post_dir(self.post) / "post.json"
        for contents in ("{broken", None):
            with self.subTest(contents=contents):
                if contents is None:
                    truth.unlink()
                else:
                    truth.write_text(contents, encoding="utf-8")
                output = io.StringIO()
                with redirect_stdout(output):
                    review.run_review(self.arc.base)
                self.assertEqual(self.copy.read_text(encoding="utf-8"), "Unbekannter alter Entwurf")
                self.assertFalse(self.human_path.exists())
                self.assertIn("post.json", output.getvalue())

    def test_confirmed_human_text_unblocks_images_when_machine_is_outdated(self):
        self.machine()
        self.source["text"] = "Updated source truth"
        self.save("Nach Prüfung der neuen Quelle")
        original = self.arc.post_dir(self.post) / "01.png"
        Image.new("RGB", (1024, 1024), "white").save(original)
        self.source["media"] = [Media("https://example.invalid/image.png", "image",
                                      original.relative_to(self.arc.base).as_posix()).__dict__]
        (original.parent / "post.json").write_text(json.dumps(self.source), encoding="utf-8")
        jobs, _state, stats = images.build_jobs(images.Settings(), self.arc.base, [self.source])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].text_de, "Nach Prüfung der neuen Quelle")
        self.assertEqual(stats.skipped_no_translation, 0)

    def test_image_jobs_refresh_old_index_after_human_confirms_post_truth(self):
        self.machine()
        original = self.arc.post_dir(self.post) / "01.png"
        Image.new("RGB", (1024, 1024), "white").save(original)
        self.source["text"] = "Updated source truth"
        self.source["media"] = [Media("https://example.invalid/image.png", "image",
                                      original.relative_to(self.arc.base).as_posix()).__dict__]
        (original.parent / "post.json").write_text(json.dumps(self.source), encoding="utf-8")
        self.save("Nach Prüfung der neuen Quelle")
        jobs, _state, _stats = images.build_jobs(images.Settings(), self.arc.base, self.arc.rows())
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].text_de, "Nach Prüfung der neuen Quelle")
        self.assertEqual(jobs[0].source_path, original)
        preview = io.StringIO()
        with redirect_stdout(preview):
            shown = images.run_show_prompt(images.Settings(), {self.arc.base: self.arc.rows()})
        self.assertEqual(shown, 0)
        self.assertIn("Nach Prüfung der neuen Quelle", preview.getvalue())

    def test_old_machine_copy_is_refreshed_without_becoming_human(self):
        self.machine("Frühere maschinelle Fassung")
        self.copy.write_text("Frühere maschinelle Fassung", encoding="utf-8")
        self.machine("Neue maschinelle Fassung")
        review.run_review(self.arc.base)
        self.assertEqual(self.copy.read_text(encoding="utf-8"), "Neue maschinelle Fassung")
        self.assertFalse(self.human_path.exists())

    def test_source_change_keeps_human_report_and_blocks_image_jobs(self):
        self.machine()
        self.save()
        image_path = self.arc.post_dir(self.post) / "01.png"
        Image.new("RGB", (1024, 1024), "white").save(image_path)
        self.source["media"] = [Media("https://example.invalid/image.png", "image",
                                      image_path.relative_to(self.arc.base).as_posix()).__dict__]
        (image_path.parent / "post.json").write_text(json.dumps(self.source), encoding="utf-8")
        jobs, _state, stats = images.build_jobs(images.Settings(), self.arc.base, [self.source])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].text_de, "Maschinelle Fassung")
        changed = dict(self.source, text="Changed original")
        self.machine(source=changed)
        (image_path.parent / "post.json").write_text(json.dumps(changed), encoding="utf-8")
        jobs, _state, stats = images.build_jobs(images.Settings(), self.arc.base, [changed])
        self.assertEqual(jobs, [])
        self.assertEqual(stats.skipped_no_translation, 1)
        self.arc.manifest.write_text(json.dumps(changed) + "\n", encoding="utf-8")
        (self.arc.post_dir(self.post) / "post.json").write_text(json.dumps(changed), encoding="utf-8")
        review.run_review(self.arc.base)
        self.assertEqual(self.copy.read_text(encoding="utf-8"), "Von Hand verbessert")
        report = (self.arc.base / "review.md").read_text(encoding="utf-8")
        self.assertIn("Von Hand verbessert", report)
        self.assertIn("复核", report)

    def test_caption_edit_keeps_existing_machine_image_without_new_paid_job(self):
        machine = self.machine()
        original = self.arc.post_dir(self.post) / "01.png"
        Image.new("RGB", (1024, 1024), "white").save(original)
        self.source["media"] = [Media("https://example.invalid/image.png", "image",
                                      original.relative_to(self.arc.base).as_posix()).__dict__]
        (original.parent / "post.json").write_text(json.dumps(self.source), encoding="utf-8")
        output = original.parent / "media_de" / "01.jpg"
        output.parent.mkdir()
        Image.new("RGB", (1024, 1024), "white").save(output)
        record = {"post_id": "p1", "media_index": 0,
                  "source_sha256": hashlib.sha256(original.read_bytes()).hexdigest(),
                  "text_de_sha256": hashlib.sha256(b"Maschinelle Fassung").hexdigest(),
                  "prompt_version": images.IMAGE_PROMPT_VERSION,
                  "out_path": output.relative_to(self.arc.base).as_posix(),
                  "folder_name": output.parent.parent.name,
                  "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                  "model": "fake", "size_requested": "1024x1024",
                  "size_returned": "1024x1024", "quality": "high",
                  "created_at": "2026-09-10T12:10:00Z"}
        images.append_image_jsonl(self.arc.base / "images_de.jsonl", record)
        self.save()
        jobs, _state, stats = images.build_jobs(images.Settings(), self.arc.base, [self.source])
        self.assertEqual(jobs, [])
        self.assertEqual(stats.skipped_current, 1)
        pair = images.review_image_pairs(self.arc.base, self.source, machine)[0]
        self.assertEqual(pair.localized_rel, record["out_path"])

    def test_each_generated_image_keeps_its_confirmed_human_basis_until_forced(self):
        machine = self.machine()
        self.source["text"] = "Updated source truth"
        originals = []
        for index in range(3):
            original = self.arc.post_dir(self.post) / f"{index + 1:02d}.png"
            Image.new("RGB", (1024, 1024), "white").save(original)
            originals.append(original)
        self.source["media"] = [
            Media("https://example.invalid/image.png", "image",
                  path.relative_to(self.arc.base).as_posix()).__dict__ for path in originals]
        truth = self.arc.post_dir(self.post) / "post.json"
        truth.write_text(json.dumps(self.source), encoding="utf-8")
        for index, caption in enumerate(("Menschliche Fassung eins", "Menschliche Fassung zwei")):
            self.save(caption)
            output = originals[index].parent / "media_de" / f"{index + 1:02d}.jpg"
            output.parent.mkdir(exist_ok=True)
            Image.new("RGB", (1024, 1024), "white").save(output)
            record = {"post_id": "p1", "media_index": index,
                      "source_sha256": hashlib.sha256(originals[index].read_bytes()).hexdigest(),
                      "text_de_sha256": hashlib.sha256(caption.encode("utf-8")).hexdigest(),
                      "prompt_version": images.IMAGE_PROMPT_VERSION,
                      "out_path": output.relative_to(self.arc.base).as_posix(),
                      "folder_name": output.parent.parent.name,
                      "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                      "model": "fake", "size_requested": "1024x1024",
                      "size_returned": "1024x1024", "quality": "high",
                      "created_at": "2026-09-10T12:10:00Z"}
            images.append_image_jsonl(self.arc.base / "images_de.jsonl", record)
        latest = self.save("Menschliche Fassung drei")
        jobs, _state, _stats = images.build_jobs(images.Settings(), self.arc.base, self.arc.rows())
        self.assertEqual([job.media_index for job in jobs], [2])
        self.assertEqual(jobs[0].text_de, "Menschliche Fassung drei")
        selected = T.image_translation(self.source, machine, latest)
        pairs = images.review_image_pairs(self.arc.base, self.source, selected)
        self.assertEqual([bool(pair.localized_rel) for pair in pairs], [True, True, False])
        forced, _state, _stats = images.build_jobs(
            images.Settings(), self.arc.base, self.arc.rows(), force=True)
        self.assertEqual(len(forced), 3)
        self.assertEqual({job.text_de for job in forced}, {"Menschliche Fassung drei"})
        self.source["text"] = "The source changed again"
        truth.write_text(json.dumps(self.source), encoding="utf-8")
        self.save("Nach einer weiteren Quellenänderung")
        changed, _state, _stats = images.build_jobs(images.Settings(), self.arc.base, self.arc.rows())
        self.assertEqual(len(changed), 3)
        self.assertEqual({job.text_de for job in changed}, {"Nach einer weiteren Quellenänderung"})


if __name__ == "__main__":
    unittest.main()
