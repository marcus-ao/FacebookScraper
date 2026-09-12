"""展示索引只从文件重建，数据库丢失、损坏或被改不能成为业务真相。"""
import json
import sys
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
