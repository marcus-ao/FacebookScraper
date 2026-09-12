"""Web 展示索引可丢弃，修改/提交路径仍读取文件；全部使用临时归档。"""
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import config, review, store, translated  # noqa: E402
from publish import journal  # noqa: E402
from web.api import query_index  # noqa: E402


class QueryIndexTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        configured = config.Config()
        configured._d['paths'] = {'archive': str(self.root / 'archive'), 'state': str(self.root / 'state')}
        self.patch = patch.object(config, '_cfg', configured)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.arc = store.Archive(self.root / 'archive', 'in_neakasa.global')
        self.first = store.Post('1', 'instagram', 'neakasa.global', 'First', '2026-09-10T10:00:00Z', tags=['M1 Pro'])
        self.second = store.Post('2', 'instagram', 'neakasa.global', 'Second', '2026-08-10T10:00:00Z', tags=[])
        self.arc.append(self.first)
        self.arc.append(self.second)
        self.now = datetime(2026, 9, 12, 1, tzinfo=timezone.utc)

    def test_missing_database_rebuilds_and_sql_filters_ignore_manifest(self):
        self.arc.manifest.write_text('{bad derived index', encoding='utf-8')
        before = (self.arc.post_dir(self.first) / 'post.json').read_bytes()
        result = query_index.candidates(tag='M1 Pro', month='2026-09', status='not_ready', now=self.now)
        self.assertEqual(result['task_ids'], ['in_neakasa.global/1'])
        self.assertTrue(result['index']['available'])
        self.assertFalse(result['index']['stale'])
        self.assertTrue((self.root / 'state' / 'index.sqlite').exists())
        self.assertEqual((self.arc.post_dir(self.first) / 'post.json').read_bytes(), before)
        self.assertEqual(query_index.candidates(tag='__untagged__', now=self.now)['task_ids'], ['in_neakasa.global/2'])

    def test_changed_tags_and_missing_or_corrupt_database_are_rebuilt(self):
        query_index.candidates(now=self.now)
        store.update_post_tags(self.arc.base, self.first.to_row(), ['S1 Pro'])
        self.assertEqual(query_index.candidates(tag='S1 Pro', now=self.now)['task_ids'], ['in_neakasa.global/1'])
        self.assertEqual(query_index.candidates(tag='M1 Pro', now=self.now)['task_ids'], [])
        database = self.root / 'state' / 'index.sqlite'
        database.write_bytes(b'corrupt disposable database')
        recovered = query_index.candidates(tag='S1 Pro', now=self.now)
        self.assertEqual(recovered['task_ids'], ['in_neakasa.global/1'])
        self.assertFalse(recovered['index']['stale'])
        database.unlink()
        self.assertEqual(query_index.candidates(tag='S1 Pro', now=self.now)['task_ids'], ['in_neakasa.global/1'])

    def test_rebuild_failure_marks_stale_and_keeps_last_database(self):
        query_index.candidates(now=self.now)
        database = self.root / 'state' / 'index.sqlite'
        original = database.read_bytes()
        (self.arc.post_dir(self.first) / 'post.json').write_text('{partial source', encoding='utf-8')
        result = query_index.candidates(tag='M1 Pro', now=self.now)
        self.assertIsNone(result['task_ids'])
        self.assertTrue(result['index']['stale'])
        self.assertTrue(result['index']['available'])
        self.assertEqual(database.read_bytes(), original)

    def test_review_and_publish_facts_refresh_but_unchanged_queries_reuse_snapshot(self):
        query_index.candidates(now=self.now)
        metadata = (self.root / 'state' / 'index.meta.json').read_bytes()
        query_index.candidates(now=self.now + timedelta(minutes=1))
        self.assertEqual((self.root / 'state' / 'index.meta.json').read_bytes(), metadata)
        review.transition(self.arc.base, self.first.to_row(), 'skipped', expected_revision=None,
                          expected_source_sha256=translated.source_text_sha256(self.first.text),
                          reason='Campaign ended', now=self.now)
        self.assertEqual(query_index.candidates(status='skipped', now=self.now)['task_ids'], ['in_neakasa.global/1'])
        journal.append(self.root / 'state', journal.PublishAttempt(
            post_id='1', platform='instagram', status='scheduled',
            recorded_at=self.now.isoformat(), scheduled_at=(self.now + timedelta(days=1)).isoformat(),
            text_de_sha256='fixture'))
        self.assertEqual(query_index.candidates(status='scheduled', now=self.now)['task_ids'], ['in_neakasa.global/1'])


if __name__ == '__main__':
    unittest.main()
