"""展示索引只从文件重建，数据库丢失、损坏或被改不能成为业务真相。"""
import base64
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from hashlib import sha256
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import store  # noqa: E402


class IndexDatabaseTests(unittest.TestCase):
    def test_rebuild_ignores_manifest_and_restores_filterable_display_rows(self):
        from core import index_db
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arc = store.Archive(root / "archive", "in_neakasa.global")
            first = store.Post("1", "instagram", "neakasa.global", "First caption", "2026-09-10T10:00:00Z", tags=["Campaign"])
            second = store.Post("2", "instagram", "neakasa.global", "Second caption", "2026-08-10T10:00:00Z", tags=[])
            arc.append(first)
            arc.append(second)
            arc.manifest.write_text(json.dumps(dict(first.to_row(), text="WRONG INDEX")) + "\n", encoding="utf-8")
            database = root / "state" / "index.sqlite"
            self.assertEqual(index_db.rebuild_index(root / "archive", database), 2)
            rows = index_db.query_posts(database, month="2026-09", tag="Campaign", status="not_ready")
            self.assertEqual([row["post_id"] for row in rows], ["1"])
            self.assertEqual(rows[0]["text"], "First caption")
            original = index_db.query_posts(database)
            database.unlink()
            self.assertEqual(index_db.rebuild_index(root / "archive", database), 2)
            self.assertEqual(index_db.query_posts(database), original)
            self.assertEqual(len(index_db.query_posts(database, limit=1, offset=1)), 1)
            self.assertEqual(index_db.query_posts(database, tag="' OR 1=1 --"), [])
            self.assertEqual(index_db.query_posts(database), original)
            before = database.read_bytes()
            (arc.post_dir(first) / "post.json").write_text("{broken", encoding="utf-8")
            with self.assertRaises(store.ArchivePathError):
                index_db.rebuild_index(root / "archive", database)
            self.assertEqual(database.read_bytes(), before)
            self.assertEqual(index_db.query_posts(database), original)

    def test_v2_schema_projects_structured_rows_ordered_tags_and_physical_media(self):
        """Dropping a structured field or relation row must make the raw archive index incomplete."""
        from core import index_db
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            instagram = store.Archive(root / "archive", "in_neakasa.global")
            image = store.Media("https://cdn.invalid/one.png", "image", width=640, height=480)
            first = store.Post(
                "same-id", "instagram", "neakasa.global", "Original #Launch #Katzen",
                "2026-09-10T10:00:00Z", permalink="https://instagram.invalid/p/same-id",
                media=[image, store.Media("https://cdn.invalid/movie.mp4", "video")],
                source_route="backfill", owner="maker", coauthors=["partner"],
                media_complete=False, tags=["Campaign", "S1 Pro", "Campaign"],
            )
            image_path = instagram.media_path(first, 0, "image/png")
            image_bytes = base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
            image_path.write_bytes(image_bytes)
            image.local_path = image_path.relative_to(instagram.base).as_posix()
            instagram.append(first)
            first_truth = instagram.post_dir(first) / "post.json"
            source = json.loads(first_truth.read_text(encoding="utf-8"))
            source["archived_at"] = "2026-09-10T10:05:00+00:00"
            source["tags_origin"] = "manual"
            source["media"][0]["content_type"] = "image/png"
            first_truth.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")

            facebook = store.Archive(root / "archive", "fa_neakasaofficial")
            facebook_post = store.Post(
                "same-id", "facebook", "neakasaofficial", "Facebook original",
                "2026-09-09T08:00:00Z", tags=["Campaign"], source_route="delta",
            )
            facebook.append(facebook_post)
            facebook_truth = facebook.post_dir(facebook_post) / "post.json"
            legacy_source = json.loads(facebook_truth.read_text(encoding="utf-8"))
            legacy_source.pop("archived_at", None)
            facebook_truth.write_text(json.dumps(legacy_source), encoding="utf-8")
            database = root / "state" / "index.sqlite"

            self.assertEqual(index_db.rebuild_index(root / "archive", database, include_frozen=True), 2)
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
                self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0].lower(), "delete")
                post_columns = {row[1] for row in connection.execute("PRAGMA table_info(posts)")}
                self.assertTrue({
                    "task_id", "account_dir", "platform", "post_id", "account", "owner",
                    "coauthors_json", "created_at", "archived_at", "permalink", "text",
                    "source_route", "month", "primary_tag", "archive_relpath", "folder_name",
                    "media_complete", "status", "row_json",
                }.issubset(post_columns))
                posts = connection.execute(
                    "SELECT task_id, platform, post_id, owner, coauthors_json, archived_at, text, "
                    "source_route, month, primary_tag, archive_relpath, folder_name, media_complete "
                    "FROM posts ORDER BY task_id"
                ).fetchall()
                tags = connection.execute(
                    "SELECT task_id, ordinal, tag FROM post_tags ORDER BY task_id, ordinal"
                ).fetchall()
                media = connection.execute(
                    "SELECT task_id, ordinal, kind, source_url, local_path, content_type, width, "
                    "height, byte_size, sha256, storage_status FROM post_media ORDER BY task_id, ordinal"
                ).fetchall()
                foreign_keys = connection.execute("PRAGMA foreign_key_list(post_media)").fetchall()
                compatibility_row = json.loads(connection.execute(
                    "SELECT row_json FROM posts WHERE task_id='in_neakasa.global/same-id'"
                ).fetchone()[0])

            by_task = {row[0]: row for row in posts}
            self.assertEqual(set(by_task), {"fa_neakasaofficial/same-id", "in_neakasa.global/same-id"})
            self.assertIsNone(by_task["fa_neakasaofficial/same-id"][5])
            instagram_row = by_task["in_neakasa.global/same-id"]
            self.assertEqual(instagram_row[1:10], (
                "instagram", "same-id", "maker", '["partner"]',
                "2026-09-10T10:05:00+00:00", "Original #Launch #Katzen", "backfill",
                "2026-09", "Campaign",
            ))
            self.assertTrue(instagram_row[10].startswith("in_neakasa.global/posts/"))
            self.assertEqual(instagram_row[11], first.folder_name)
            self.assertEqual(instagram_row[12], 0)
            self.assertEqual(tags, [
                ("fa_neakasaofficial/same-id", 0, "Campaign"),
                ("in_neakasa.global/same-id", 0, "Campaign"),
                ("in_neakasa.global/same-id", 1, "S1 Pro"),
            ])
            self.assertEqual(compatibility_row["tags"], ["Campaign", "S1 Pro", "Campaign"])
            self.assertEqual(media[0], (
                "in_neakasa.global/same-id", 0, "image", "https://cdn.invalid/one.png",
                image.local_path, "image/png", 1, 1, len(image_bytes),
                sha256(image_bytes).hexdigest(), "saved",
            ))
            self.assertEqual(media[1], (
                "in_neakasa.global/same-id", 1, "video", "https://cdn.invalid/movie.mp4",
                None, None, None, None, None, None, "metadata_only",
            ))
            self.assertTrue(foreign_keys)

    def test_archive_month_projection_uses_beijing_for_new_rows_and_preserves_legacy_folder(self):
        """Using the UTC date substring would put evening UTC posts in the wrong Beijing month."""
        from core import index_db
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            current = store.Archive(root / "archive", "in_neakasa.global")
            current.append(store.Post("new", "instagram", "neakasa.global", "New",
                                      "2026-08-31T20:00:00Z", tags=[]))
            current.append(store.Post("unknown", "instagram", "neakasa.global", "Unknown",
                                      "not-a-date", tags=[]))
            legacy = store.Archive(root / "archive", "in_neakasa.tech")
            legacy.append(store.Post(
                "legacy", "instagram", "neakasa.tech", "Legacy", "2026-08-31T20:00:00Z",
                folder_name="2026-08-31_2000_legacy", tags=[],
            ))
            database = root / "state" / "index.sqlite"
            index_db.rebuild_index(root / "archive", database, include_frozen=True)
            with closing(sqlite3.connect(database)) as connection:
                months = dict(connection.execute("SELECT task_id, month FROM posts"))
            self.assertEqual(months, {
                "in_neakasa.global/new": "2026-09",
                "in_neakasa.global/unknown": "undated",
                "in_neakasa.tech/legacy": "2026-08",
            })

    def test_consistency_checks_scalars_json_ids_and_every_ordered_relation_row(self):
        """A correct row_json alone must not hide damage to SQL columns or child tables."""
        from core import index_db
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = store.Archive(root / "archive", "in_neakasa.global")
            post = store.Post(
                "1", "instagram", "neakasa.global", "Source", "2026-09-10T10:00:00Z",
                tags=["First", "Second"], media_complete=False,
                media=[
                    store.Media("https://cdn.invalid/missing.jpg", "image", width=10, height=20),
                    store.Media("https://cdn.invalid/video.mp4", "video"),
                ],
            )
            archive.append(post)
            database = root / "state" / "index.sqlite"

            mutations = {
                "scalar month": "UPDATE posts SET month='1900-01' WHERE task_id='in_neakasa.global/1'",
                "row json": "UPDATE posts SET row_json='{}' WHERE task_id='in_neakasa.global/1'",
                "missing tag": "DELETE FROM post_tags WHERE task_id='in_neakasa.global/1' AND ordinal=0",
                "ordered tag": "UPDATE post_tags SET ordinal=9 WHERE task_id='in_neakasa.global/1' AND ordinal=1",
                "extra tag": "INSERT INTO post_tags(task_id, tag, ordinal) VALUES ('in_neakasa.global/1','Extra',9)",
                "missing media": "DELETE FROM post_media WHERE task_id='in_neakasa.global/1'",
                "ordered media": "UPDATE post_media SET ordinal=9 "
                                 "WHERE task_id='in_neakasa.global/1' AND ordinal=1",
                "extra media": "INSERT INTO post_media(task_id,ordinal,kind,source_url,storage_status) "
                               "VALUES ('in_neakasa.global/1',9,'image','extra','missing')",
            }
            for label, statement in mutations.items():
                with self.subTest(label=label):
                    index_db.rebuild_index(root / "archive", database, include_frozen=True)
                    with closing(sqlite3.connect(database)) as connection:
                        connection.execute(statement)
                        connection.commit()
                    result = index_db.check_consistency(
                        root / "archive", database, include_frozen=True)
                    self.assertFalse(result["consistent"])
                    self.assertIn("in_neakasa.global/1", result["changed"])
                    self.assertTrue(result["details"], result)

            index_db.rebuild_index(root / "archive", database, include_frozen=True)
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    "UPDATE posts SET task_id='renamed/1' WHERE task_id='in_neakasa.global/1'")
                connection.commit()
            renamed = index_db.check_consistency(root / "archive", database, include_frozen=True)
            self.assertEqual(renamed["missing"], ["in_neakasa.global/1"])
            self.assertEqual(renamed["extra"], ["renamed/1"])
            self.assertTrue(any("renamed/1" in detail for detail in renamed["details"]))

            index_db.rebuild_index(root / "archive", database, include_frozen=True)
            with closing(sqlite3.connect(database)) as connection:
                connection.executescript("""
                    ALTER TABLE post_tags RENAME TO saved_post_tags;
                    CREATE TABLE post_tags(task_id TEXT NOT NULL, tag TEXT NOT NULL, ordinal INTEGER NOT NULL);
                    INSERT INTO post_tags SELECT task_id, tag, ordinal FROM saved_post_tags;
                    INSERT INTO post_tags VALUES ('in_neakasa.global/1', 'First', 0);
                    DROP TABLE saved_post_tags;
                """)
                connection.commit()
            duplicate = index_db.check_consistency(root / "archive", database, include_frozen=True)
            self.assertFalse(duplicate["consistent"])
            self.assertIn("in_neakasa.global/1", duplicate["changed"])
            self.assertTrue(any("duplicate tag row" in detail for detail in duplicate["details"]))

            index_db.rebuild_index(root / "archive", database, include_frozen=True)
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("PRAGMA foreign_keys=OFF")
                connection.execute(
                    "INSERT INTO post_media(task_id,ordinal,kind,source_url,storage_status) "
                    "VALUES ('orphan/404',0,'image','orphan','missing')")
                connection.commit()
            result = index_db.check_consistency(root / "archive", database, include_frozen=True)
            self.assertFalse(result["consistent"])
            self.assertTrue(result["foreign_key_errors"])
            self.assertTrue(any("orphan/404" in detail for detail in result["details"]))

    def test_consistency_reports_old_and_corrupt_databases_instead_of_accepting_them(self):
        """A disposable v1 or corrupt database must be classified for a safe rebuild."""
        from core import index_db
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = store.Archive(root / "archive", "in_neakasa.global")
            archive.append(store.Post("1", "instagram", "neakasa.global", "Source",
                                      "2026-09-10T10:00:00Z", tags=[]))
            database = root / "state" / "index.sqlite"
            database.parent.mkdir(parents=True)
            with closing(sqlite3.connect(database)) as connection:
                connection.executescript("PRAGMA user_version=1; CREATE TABLE posts(task_id TEXT, row_json TEXT);")
                connection.commit()
            old = index_db.check_consistency(root / "archive", database, include_frozen=True)
            self.assertFalse(old["consistent"])
            self.assertEqual(old["schema_version"], 1)
            self.assertTrue(any("schema" in detail for detail in old["details"]))

            database.write_bytes(b"not a sqlite database")
            corrupt = index_db.check_consistency(root / "archive", database, include_frozen=True)
            self.assertFalse(corrupt["consistent"])
            self.assertTrue(corrupt["details"])


if __name__ == "__main__":
    unittest.main()
