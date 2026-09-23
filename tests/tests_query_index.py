"""Web 展示索引可丢弃，修改/提交路径仍读取文件；全部使用临时归档。"""
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import config, index_db, review, store, translated  # noqa: E402
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

    def test_index_status_is_read_only_and_only_verified_for_matching_v2_metadata(self):
        """Database existence alone must never be reported as a current verified snapshot."""
        state = self.root / 'state'
        self.assertEqual(query_index.index_status(), {
            'status': 'unbuilt', 'verified_at': None, 'message': None,
        })
        self.assertFalse(state.exists())

        query_index.candidates(now=self.now)
        database = state / 'index.sqlite'
        metadata_before = (state / 'index.meta.json').read_bytes()
        database_before = database.read_bytes()
        verified = query_index.index_status()
        self.assertEqual(verified['status'], 'verified')
        self.assertEqual(verified['verified_at'], self.now.isoformat())
        self.assertIsNone(verified['message'])
        self.assertEqual((state / 'index.meta.json').read_bytes(), metadata_before)
        self.assertEqual(database.read_bytes(), database_before)

        with closing(sqlite3.connect(database)) as connection:
            connection.execute('PRAGMA user_version=1')
            connection.commit()
        stale = query_index.index_status()
        self.assertEqual(stale['status'], 'stale')
        self.assertIsNone(stale['verified_at'])
        self.assertTrue(stale['message'])

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

    def test_history_month_follows_displayed_date_not_archive_folder(self):
        """历史月份筛选与行内日期一致：归档目录按北京时刻换算，可能与原帖日期跨月。"""
        late = store.Post('late', 'instagram', 'neakasa.global', 'Late', '2026-08-31T22:30:00Z', tags=[])
        nodate = store.Post('nodate', 'instagram', 'neakasa.global', 'NoDate', '', tags=[])
        self.arc.append(late)
        self.arc.append(nodate)
        # 北京时刻已是 9 月 1 日清晨，归档目录落进 2026-09；行内日期仍是 8 月 31 日。
        self.assertTrue(self.arc.post_dir(late).name.startswith('2026-09'))
        august = query_index.history_page(month='2026-08', now=self.now)
        self.assertEqual([row['id'] for row in august['rows']],
                         ['in_neakasa.global/late', 'in_neakasa.global/2'])
        september = query_index.history_page(month='2026-09', now=self.now)
        self.assertEqual([row['id'] for row in september['rows']], ['in_neakasa.global/1'])
        undated = query_index.history_page(month='undated', now=self.now)
        self.assertEqual([row['id'] for row in undated['rows']], ['in_neakasa.global/nodate'])
        self.assertEqual(query_index.history_page(now=self.now)['months'], ['2026-09', '2026-08', 'undated'])

    def test_empty_non_post_directory_does_not_make_the_index_unavailable(self):
        """The source signature must skip the same non-truth directories as the rebuilder."""
        (self.arc.posts_dir / 'empty-recovery-dir').mkdir()
        result = query_index.candidates(now=self.now)
        self.assertEqual(result['task_ids'], ['in_neakasa.global/1', 'in_neakasa.global/2'])
        self.assertFalse(result['index']['stale'])

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

    def test_query_failure_returns_stale_fallback_without_touching_database(self):
        query_index.candidates(now=self.now)
        database = self.root / 'state' / 'index.sqlite'
        original = database.read_bytes()
        with patch.object(query_index.index_db, 'query_posts',
                          side_effect=sqlite3.OperationalError('read failed')):
            result = query_index.candidates(now=self.now + timedelta(seconds=1))
        self.assertIsNone(result['task_ids'])
        self.assertTrue(result['index']['stale'])
        self.assertEqual(result['index']['error'], 'OperationalError')
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

    def test_frozen_account_neither_enters_the_index_nor_makes_it_dirty(self):
        """冻结账号既不进入当前任务索引，也不使该索引失效。"""
        frozen = store.Archive(self.root / 'archive', 'in_neakasa.tech')
        legacy = store.Post('9', 'instagram', 'neakasa.tech', 'Legacy', '2026-09-11T10:00:00Z',
                            tags=['M1 Pro'])
        frozen.append(legacy)
        self.assertEqual([path.name for path in index_db.display_account_dirs(self.root / 'archive')],
                         ['in_neakasa.global'])
        self.assertEqual(query_index.candidates(tag='M1 Pro', now=self.now)['task_ids'],
                         ['in_neakasa.global/1'])

        metadata = (self.root / 'state' / 'index.meta.json').read_bytes()
        store.update_post_tags(frozen.base, legacy.to_row(), ['S1 Pro'])
        result = query_index.candidates(now=self.now)
        self.assertFalse(result['index']['stale'])
        self.assertEqual((self.root / 'state' / 'index.meta.json').read_bytes(), metadata)

        # CLI 的 reindex-db 走的就是这一行；范围与 Web 不一致会让两边来回重建。
        self.assertEqual(index_db.rebuild_index(
            self.root / 'archive', self.root / 'state' / 'index.sqlite',
            state_dir=self.root / 'state'), 2)

    def test_media_content_signature_detects_same_size_bytes_and_missing_files(self):
        """File size and timestamps can stay stable while archived evidence bytes change."""
        media = store.Media('https://cdn.invalid/evidence.jpg', 'image', width=20, height=10)
        post = store.Post('media', 'instagram', 'neakasa.global', 'Media',
                          '2026-09-11T12:00:00Z', media=[media], tags=[])
        path = self.arc.media_path(post, 0, 'image/jpeg')
        variants = []
        for colour in ((255, 0, 0), (0, 0, 255)):
            buffer = io.BytesIO()
            Image.new('RGB', (1, 1), colour).save(buffer, format='PNG')
            variants.append(buffer.getvalue())
        self.assertEqual(len(variants[0]), len(variants[1]))
        path.write_bytes(variants[0])
        media.local_path = path.relative_to(self.arc.base).as_posix()
        self.arc.append(post)

        self.assertIn('in_neakasa.global/media', query_index.candidates(now=self.now)['task_ids'])
        database = self.root / 'state' / 'index.sqlite'
        metadata_path = self.root / 'state' / 'index.meta.json'
        with closing(sqlite3.connect(database)) as connection:
            first_hash = connection.execute(
                "SELECT sha256 FROM post_media WHERE task_id='in_neakasa.global/media'"
            ).fetchone()[0]
        source_path = self.arc.post_dir(post) / 'post.json'
        source_before = (source_path.read_bytes(), source_path.stat().st_mtime_ns)
        media_mtime = path.stat().st_mtime_ns

        path.write_bytes(variants[1])
        os.utime(path, ns=(path.stat().st_atime_ns, media_mtime))
        result = query_index.candidates(now=self.now + timedelta(seconds=1))
        self.assertIn('in_neakasa.global/media', result['task_ids'])
        self.assertFalse(result['index']['stale'])
        with closing(sqlite3.connect(database)) as connection:
            changed_hash = connection.execute(
                "SELECT sha256 FROM post_media WHERE task_id='in_neakasa.global/media'"
            ).fetchone()[0]
            changed_state = connection.execute(
                "SELECT storage_status FROM post_media WHERE task_id='in_neakasa.global/media'"
            ).fetchone()[0]
        self.assertNotEqual(changed_hash, first_hash)
        self.assertEqual(changed_state, 'saved')
        self.assertEqual((source_path.read_bytes(), source_path.stat().st_mtime_ns), source_before)
        self.assertEqual(path.stat().st_mtime_ns, media_mtime)

        previous_signature = json.loads(metadata_path.read_text(encoding='utf-8'))['source_signature']
        path.unlink()
        missing = query_index.candidates(now=self.now + timedelta(seconds=2))
        self.assertFalse(missing['index']['stale'])
        current = json.loads(metadata_path.read_text(encoding='utf-8'))
        self.assertNotEqual(current['source_signature'], previous_signature)
        with closing(sqlite3.connect(database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT storage_status FROM post_media WHERE task_id='in_neakasa.global/media'"
            ).fetchone()[0], 'missing')

    def test_source_change_during_rebuild_returns_stale_source_fallback(self):
        """A rebuild snapshot must not be presented as current if its source changes mid-flight."""
        real_rebuild = index_db.rebuild_index

        def rebuild_then_change(*args, **kwargs):
            count = real_rebuild(*args, **kwargs)
            truth = self.arc.post_dir(self.first) / 'post.json'
            row = json.loads(truth.read_text(encoding='utf-8'))
            row['text'] = 'Changed during rebuild'
            truth.write_text(json.dumps(row), encoding='utf-8')
            return count

        with patch.object(query_index.index_db, 'rebuild_index', side_effect=rebuild_then_change):
            result = query_index.candidates(now=self.now)
        self.assertIsNone(result['task_ids'])
        self.assertFalse(result['index']['available'])
        self.assertTrue(result['index']['stale'])
        self.assertEqual(result['index']['error'], 'source_changed_during_rebuild')
        self.assertFalse((self.root / 'state' / 'index.sqlite').exists())

    def test_source_change_during_final_validation_is_not_marked_verified(self):
        """The verification metadata must describe the source after validation also completes."""
        real_check = index_db.check_consistency

        def check_then_change(*args, **kwargs):
            result = real_check(*args, **kwargs)
            truth = self.arc.post_dir(self.first) / 'post.json'
            row = json.loads(truth.read_text(encoding='utf-8'))
            row['text'] = 'Changed after consistency read'
            truth.write_text(json.dumps(row), encoding='utf-8')
            return result

        with patch.object(query_index.index_db, 'check_consistency', side_effect=check_then_change):
            result = query_index.candidates(now=self.now)
        self.assertIsNone(result['task_ids'])
        self.assertTrue(result['index']['stale'])
        self.assertEqual(result['index']['error'], 'source_changed_during_rebuild')
        self.assertFalse((self.root / 'state' / 'index.meta.json').exists())

    def test_source_change_during_candidate_validation_preserves_previous_verified_database(self):
        """A stale candidate must never replace the last verified database."""
        initial = query_index.candidates(now=self.now)
        self.assertFalse(initial['index']['stale'])
        database = self.root / 'state' / 'index.sqlite'
        metadata = self.root / 'state' / 'index.meta.json'
        database_before = database.read_bytes()
        metadata_before = metadata.read_bytes()

        truth = self.arc.post_dir(self.first) / 'post.json'
        source_b = json.loads(truth.read_text(encoding='utf-8'))
        source_b['text'] = 'Source B'
        truth.write_text(json.dumps(source_b), encoding='utf-8')
        real_validation = index_db._consistency_from_connection
        changed = False

        def validate_then_change(*args, **kwargs):
            nonlocal changed
            result = real_validation(*args, **kwargs)
            if not changed:
                source_c = json.loads(truth.read_text(encoding='utf-8'))
                source_c['text'] = 'Source C'
                truth.write_text(json.dumps(source_c), encoding='utf-8')
                changed = True
            return result

        with patch.object(index_db, '_consistency_from_connection', side_effect=validate_then_change):
            result = query_index.candidates(now=self.now + timedelta(seconds=1))
        self.assertIsNone(result['task_ids'])
        self.assertTrue(result['index']['available'])
        self.assertTrue(result['index']['stale'])
        self.assertEqual(result['index']['error'], 'source_changed_during_rebuild')
        self.assertEqual(database.read_bytes(), database_before)
        self.assertEqual(metadata.read_bytes(), metadata_before)
        self.assertEqual(list(database.parent.glob('*.candidate.sqlite')), [])

    def test_old_schema_is_rebuilt_and_metadata_records_v2(self):
        query_index.candidates(now=self.now)
        database = self.root / 'state' / 'index.sqlite'
        with closing(sqlite3.connect(database)) as connection:
            connection.execute('PRAGMA user_version=1')
            connection.commit()
        result = query_index.candidates(now=self.now + timedelta(seconds=1))
        self.assertFalse(result['index']['stale'])
        with closing(sqlite3.connect(database)) as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 2)
        metadata = json.loads((self.root / 'state' / 'index.meta.json').read_text(encoding='utf-8'))
        self.assertEqual(metadata['schema_version'], 2)


if __name__ == '__main__':
    unittest.main()
