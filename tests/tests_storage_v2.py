"""月份布局、人工标签与源帖更新的文件契约；全部使用临时归档。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import config, store  # noqa: E402
from core.console import force_utf8  # noqa: E402

force_utf8()


class StorageLayoutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.arc = store.Archive(self.root / "archive", "in_neakasa.global")
        configured = config.Config()
        configured._d.setdefault("image", {}).setdefault("keep_verbatim", {})["models"] = [
            "M1", "M1 Pro", "P1", "P1 Pro"]
        self.addCleanup(patch.stopall)
        patch.object(config, "_cfg", configured).start()

    def post(self, text="M1 Pro anniversary sale! 🐈", **kwargs):
        return store.Post("3973012230169803390", "instagram", "neakasa.global", text,
                          "2026-09-10T14:23:00Z", owner="neakasa.global", **kwargs)

    def test_new_post_uses_month_and_readable_stable_ascii_folder(self):
        post = self.post()
        self.assertTrue(self.arc.append(post))
        row = self.arc.rows()[0]
        directory = self.arc.post_dir(post)
        self.assertEqual(directory.parent, self.arc.posts_dir / "2026-09")
        self.assertEqual(row["folder_name"],
                         "2026-09-10_1423_m1-pro-anniversary-sale_3973012230169803390")
        self.assertEqual(directory.name, row["folder_name"])
        self.assertEqual(row["tags"], ["M1 Pro"])
        self.assertLess(len(str(directory / "media_de" / "01.jpeg")), 240)
        self.assertEqual(store.read_post_truth(self.arc.base, row), (row, directory))

    def test_source_edit_keeps_folder_and_manual_tags_and_archives_previous_source(self):
        original = self.post(media=[store.Media("https://cdn.invalid/a.jpg", "image")])
        media = self.arc.media_path(original, 0, "image/jpeg")
        media.write_bytes(b"original image")
        original.media[0].local_path = media.relative_to(self.arc.base).as_posix()
        self.arc.append(original)
        first = self.arc.rows()[0]
        directory = self.arc.post_dir(original)
        store.update_post_tags(self.arc.base, first, ["Custom campaign"], expected_tags=["M1 Pro"])
        changed = self.post("P1 launch after source edit", media=[store.Media("https://cdn.invalid/a.jpg", "image")])
        self.assertTrue(self.arc.should_append(changed))
        self.assertTrue(self.arc.append(changed))
        latest, latest_dir = store.read_post_truth(self.arc.base, self.arc.rows()[0])
        self.assertEqual(latest_dir, directory)
        self.assertEqual(latest["folder_name"], first["folder_name"])
        self.assertEqual(latest["tags"], ["Custom campaign"])
        self.assertEqual(latest["media"][0]["local_path"], first["media"][0]["local_path"])
        history = [json.loads(line) for line in (directory / "source_history.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(history[-1]["source"]["text"], original.text)
        self.assertFalse(self.arc.append(changed))

    def test_tag_save_compares_tags_and_source_under_archive_lock(self):
        self.arc.append(self.post())
        row = self.arc.rows()[0]
        saved = store.update_post_tags(self.arc.base, row, [], expected_tags=["M1 Pro"])
        self.assertEqual(saved["tags"], [])
        with self.assertRaises(store.ArchiveRevisionConflict):
            store.update_post_tags(self.arc.base, row, ["Stale tab"], expected_tags=["M1 Pro"])
        with self.assertRaises(store.ArchiveRevisionConflict):
            store.update_post_tags(self.arc.base, row, ["Old source"], expected_source_sha256="0" * 64)
        self.assertEqual(store.read_post_truth(self.arc.base, row)[0]["tags"], [])

    def test_model_tags_have_boundaries_and_longest_match_wins(self):
        self.assertEqual(store.infer_tags("XM1 M10 P1 Pro & m1 pro! P1.", ["M1", "M1 Pro", "P1", "P1 Pro"]),
                         ["P1 Pro", "M1 Pro", "P1"])
        self.assertEqual(store.infer_tags("general news", ["M1"]), [])

    def test_folder_matching_and_recovery_support_shortened_long_ids(self):
        post = self.post()
        post.post_id = "long" + "9" * 80
        self.arc.append(post)
        directory = self.arc.post_dir(post)
        self.assertTrue(store.post_folder_matches_id(directory.name, post.post_id))
        self.assertTrue(store.post_folder_matches_id(store.post_dirname(post.post_id, post.created_at), post.post_id))
        self.assertFalse(store.post_folder_matches_id(directory.name, "unrelated"))
        stale = post.to_row()
        stale.pop("folder_name")
        self.assertEqual(store.read_post_truth(self.arc.base, stale)[1], directory)

    def test_source_media_change_keeps_original_bytes_and_old_paths(self):
        old = self.post(media=[store.Media("https://cdn.invalid/old.jpg", "image")])
        old_path = self.arc.media_path(old, 0, "image/jpeg")
        old_path.write_bytes(b"OLD")
        old.media[0].local_path = old_path.relative_to(self.arc.base).as_posix()
        self.arc.append(old)
        new = self.post(media=[store.Media("https://cdn.invalid/new.jpg", "image")])
        new_path = self.arc.media_path(new, 0, "image/jpeg")
        self.assertNotEqual(new_path, old_path)
        new_path.write_bytes(b"NEW")
        new.media[0].local_path = new_path.relative_to(self.arc.base).as_posix()
        self.arc.append(new)
        self.assertEqual(old_path.read_bytes(), b"OLD")
        self.assertEqual(new_path.read_bytes(), b"NEW")
        history = json.loads((new_path.parent / "source_history.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(history["source"]["media"][0]["local_path"], old.media[0].local_path)

        # A previously seen URL can return different CDN bytes. Its old file is evidence.
        for url in (old.media[0].url, new.media[0].url):
            changed = self.post(media=[store.Media(url, "image")])
            path = self.arc.media_path(changed, 0, "image/jpeg")
            self.assertFalse(path.exists())
            path.write_bytes(b"NEW REVISION")
            changed.media[0].local_path = path.relative_to(self.arc.base).as_posix()
            self.arc.append(changed)
        self.assertEqual(old_path.read_bytes(), b"OLD")
        self.assertEqual(new_path.read_bytes(), b"NEW")

    def test_replay_keeps_versioned_media_in_its_existing_post_directory(self):
        from tools import replay
        original = self.post(media=[store.Media("https://cdn.invalid/old.jpg", "image")])
        first = self.arc.media_path(original, 0, "image/jpeg")
        first.write_bytes(b"OLD")
        original.media[0].local_path = first.relative_to(self.arc.base).as_posix()
        self.arc.append(original)
        changed = self.post(media=[store.Media("https://cdn.invalid/new.jpg", "image")])
        path = self.arc.media_path(changed, 0, "image/jpeg")
        path.write_bytes(b"NEW")
        changed.media[0].local_path = path.relative_to(self.arc.base).as_posix()
        self.arc.append(changed)
        known = {(changed.post_id, changed.media[0].url): changed.media[0].local_path}
        replay.preflight_replay_paths(self.arc.base, [changed], known)
        self.assertEqual(replay.relink([changed], known, self.arc.base, self.arc, move=True), (1, 0, 0))
        self.assertEqual(first.read_bytes(), b"OLD")
        self.assertEqual(path.read_bytes(), b"NEW")

    def test_reindex_reads_monthly_and_flat_truth_without_changing_sources(self):
        self.arc.append(self.post())
        legacy = self.post("Legacy caption").to_row()
        legacy.update(post_id="legacy", folder_name=None, tags=["Legacy tag"])
        directory = self.arc.posts_dir / store.post_dirname("legacy", legacy["created_at"])
        directory.mkdir()
        path = directory / "post.json"
        path.write_text(json.dumps(legacy), encoding="utf-8")
        before = path.read_bytes()
        self.assertEqual(self.arc.reindex(), 2)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(store.read_post_truth(self.arc.base, legacy), (legacy, directory))

    def test_explicit_migration_preserves_media_and_translations_and_is_repeatable(self):
        from tools import layout
        row = self.post("Legacy M1 Pro sale").to_row()
        row.pop("folder_name")
        row.pop("tags")
        old = self.arc.posts_dir / store.post_dirname(row["post_id"], row["created_at"])
        old.mkdir()
        (old / "01.jpg").write_bytes(b"ORIGINAL")
        (old / "media_de").mkdir()
        (old / "media_de" / "01.jpg").write_bytes(b"GERMAN IMAGE")
        (old / "text_de.txt").write_text("Human draft", encoding="utf-8")
        row["media"] = [{"url": "https://cdn.invalid/old.jpg", "kind": "image", "local_path": (old / "01.jpg").relative_to(self.arc.base).as_posix()}]
        row["text"] = old.relative_to(self.arc.base).as_posix() + "/is literal caption text"
        (old / "post.json").write_text(json.dumps(row), encoding="utf-8")
        self.arc.manifest.write_text(json.dumps(row) + "\n", encoding="utf-8")
        images = self.arc.base / "images_de.jsonl"
        images.write_text(json.dumps({"post_id": row["post_id"], "out_path": (old / "media_de" / "01.jpg").relative_to(self.arc.base).as_posix()}) + "\n", encoding="utf-8")
        before = {path.relative_to(self.arc.base): path.read_bytes() for path in self.arc.base.rglob("*") if path.is_file()}
        self.assertEqual(layout.migrate(self.arc.base, dry_run=True), 1)
        self.assertEqual(before, {path.relative_to(self.arc.base): path.read_bytes() for path in self.arc.base.rglob("*") if path.is_file()})
        self.assertEqual(layout.migrate(self.arc.base, dry_run=False), 1)
        moved = store.Archive(self.arc.base.parent, self.arc.base.name).rows()[0]
        truth, directory = store.read_post_truth(self.arc.base, moved)
        self.assertEqual(directory.parent.name, "2026-09")
        self.assertEqual(truth["text"], row["text"])
        self.assertEqual((self.arc.base / truth["media"][0]["local_path"]).read_bytes(), b"ORIGINAL")
        self.assertEqual((directory / "text_de.txt").read_text(encoding="utf-8"), "Human draft")
        self.assertTrue(list((self.arc.base / "_layout_backups").rglob("post.json")))
        latest_image = json.loads(images.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual((self.arc.base / latest_image["out_path"]).read_bytes(), b"GERMAN IMAGE")
        self.assertEqual(layout.migrate(self.arc.base, dry_run=False), 0)

    def test_migration_resumes_after_directory_move_before_source_write(self):
        from tools import layout
        row = self.post("Recover a source").to_row()
        row.pop("folder_name")
        old = self.arc.posts_dir / store.post_dirname(row["post_id"], row["created_at"])
        old.mkdir()
        (old / "post.json").write_text(json.dumps(row), encoding="utf-8")
        self.arc.manifest.write_text(json.dumps(row) + "\n", encoding="utf-8")
        original_write = layout._atomic_write_text

        def fail_source_write(path, *args, **kwargs):
            if path.name == "post.json":
                raise OSError("interrupted after move")
            return original_write(path, *args, **kwargs)

        with patch.object(layout, "_atomic_write_text", side_effect=fail_source_write):
            with self.assertRaisesRegex(OSError, "interrupted after move"):
                layout.migrate(self.arc.base, dry_run=False)
        self.assertEqual(layout.migrate(self.arc.base, dry_run=False), 1)
        latest = store.Archive(self.arc.base.parent, self.arc.base.name).rows()[0]
        self.assertEqual(store.read_post_truth(self.arc.base, latest)[0]["text"], "Recover a source")
        self.assertEqual(layout.migrate(self.arc.base, dry_run=False), 0)


if __name__ == "__main__":
    unittest.main()
