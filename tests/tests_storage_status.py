"""Storage facts shown by the read-only task detail endpoint."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests_web_review as fixtures
from core import config, store


class StorageStatusTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.WebReviewTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def _write_source(self, **changes):
        self.f.source.update(changes)
        self.f.write_source()
        (self.f.account / 'manifest.jsonl').write_text(
            json.dumps(self.f.source, ensure_ascii=False) + '\n', encoding='utf-8')

    def test_detail_reports_actual_media_order_and_independent_storage_facts(self):
        folder = self.f.source['media'][0]['local_path'].rsplit('/', 1)[0]
        self._write_source(
            tags=['S10'], tags_origin='manual', archived_at='2026-09-14T08:20:00+00:00',
            media=[
                {'kind': 'image', 'url': 'https://example.invalid/first.jpg',
                 'local_path': folder + '/01.jpg', 'content_type': 'image/jpeg'},
                {'kind': 'image', 'url': 'https://example.invalid/second.jpg',
                 'local_path': folder + '/02.jpg', 'content_type': 'image/jpeg'},
            ])

        response = self.f.client.get(self.f.url)
        self.assertEqual(response.status_code, 200, response.text)
        storage = response.json()['storage']
        self.assertEqual(storage['classified_by'], 'manual')
        self.assertEqual(storage['account_dir'], self.f.account.name)
        self.assertEqual(storage['folder'], folder)
        self.assertEqual(storage['first_archived_at'], '2026-09-14T08:20:00+00:00')
        self.assertEqual(storage['local'], {
            'status': 'partial', 'saved_images': 1, 'expected_images': 2})
        self.assertEqual([(item['ordinal'], item['storage_status']) for item in storage['media']],
                         [(0, 'saved'), (1, 'missing')])
        self.assertEqual(storage['database']['status'], 'unbuilt')
        self.assertEqual(storage['feishu']['status'], 'disabled')

    def test_legacy_source_has_no_invented_first_archive_time(self):
        self._write_source(tags_origin=None, archived_at=None)

        storage = self.f.client.get(self.f.url).json()['storage']
        self.assertEqual(storage['classified_by'], 'legacy')
        self.assertIsNone(storage['first_archived_at'])

    def test_detail_storage_read_does_not_start_cloud_work_or_change_source_files(self):
        before = {path.relative_to(self.f.root): path.read_bytes()
                  for path in self.f.root.rglob('*') if path.is_file()}
        with patch('core.mirror.DriveClient.from_environment', side_effect=AssertionError('network')):
            self.assertEqual(self.f.client.get(self.f.url).status_code, 200)
        after = {path.relative_to(self.f.root): path.read_bytes()
                 for path in self.f.root.rglob('*') if path.is_file()}
        self.assertEqual(after, before)

    def test_unsafe_legacy_media_is_reported_without_hiding_the_detail(self):
        self._write_source(media=[{'kind': 'image', 'url': 'https://example.invalid/outside.jpg',
                                   'local_path': '../outside.jpg'}])

        detail = self.f.client.get(self.f.url).json()
        self.assertEqual(detail['status'], 'pending_review')
        self.assertEqual(detail['storage']['local']['status'], 'corrupt')
        self.assertEqual(detail['storage']['media'][0]['storage_status'], 'corrupt')

    def test_unsafe_media_does_not_reclassify_a_separate_missing_file_as_corrupt(self):
        self._write_source(media=[
            {'kind': 'image', 'url': 'https://example.invalid/outside.jpg', 'local_path': '../outside.jpg'},
            {'kind': 'image', 'url': 'https://example.invalid/not-downloaded.jpg'},
        ])

        media = self.f.client.get(self.f.url).json()['storage']['media']
        self.assertEqual([item['storage_status'] for item in media], ['corrupt', 'missing'])

    def test_review_month_uses_the_new_post_beijing_archive_month(self):
        archive = store.Archive(self.f.root / 'archive', self.f.account.name)
        archive.append(store.Post('beijing-month', 'facebook', 'neakasaofficial', 'Late August UTC',
                                  '2026-08-31T20:00:00Z',
                                  media=[store.Media('https://example.invalid/new.jpg', 'image')]))

        september = self.f.client.get('/api/tasks?scope=review&month=2026-09').json()
        august = self.f.client.get('/api/tasks?scope=review&month=2026-08').json()
        september_row = next(row for row in september['tasks'] if row['id'].endswith('/beijing-month'))
        self.assertEqual(september_row['month'], '2026-09')
        self.assertFalse(any(row['id'].endswith('/beijing-month') for row in august['tasks']))

    def test_review_month_keeps_a_legacy_folder_month_instead_of_reparsing_utc(self):
        post_id, folder = 'legacy-cross-month', '2026-08-31_2000_legacy-cross-month'
        directory = self.f.account / 'posts' / folder
        directory.mkdir(parents=True)
        (directory / '01.jpg').write_bytes((self.f.post_dir / '01.jpg').read_bytes())
        source = dict(self.f.source, post_id=post_id, created_at='2026-08-31T20:00:00Z',
                      media=[{'kind': 'image', 'local_path': f'posts/{folder}/01.jpg'}])
        source.pop('folder_name', None)
        (directory / 'post.json').write_text(json.dumps(source), encoding='utf-8')
        with (self.f.account / 'manifest.jsonl').open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(source) + '\n')

        august = self.f.client.get('/api/tasks?scope=review&month=2026-08').json()
        september = self.f.client.get('/api/tasks?scope=review&month=2026-09').json()
        august_row = next(row for row in august['tasks'] if row['id'].endswith('/' + post_id))
        self.assertEqual(august_row['month'], '2026-08')
        self.assertFalse(any(row['id'].endswith('/' + post_id) for row in september['tasks']))

    def test_detail_cloud_status_is_scoped_to_the_current_post(self):
        state = self.f.root / 'state'
        state.mkdir()
        config.cfg()._d['mirror'] = {'enabled': True, 'root_folder_token': 'fixture-root'}
        (state / 'mirror_queue.json').write_text(json.dumps({
            'schema_version': 2, 'root_token': 'fixture-root', 'folders': {},
            'posts': {'fa_other/other': {'token': 'folder', 'status': 'completed'}},
            'snapshots': {'source': {'post_key': 'fa_other/other', 'status': 'completed'}},
            'operations': {},
        }), encoding='utf-8')

        cloud = self.f.client.get(self.f.url).json()['storage']['feishu']
        self.assertEqual(cloud['status'], 'idle')
        self.assertFalse(cloud['incomplete_source'])
        self.assertEqual(cloud['missing_media'], [])
        self.assertNotIn('fa_other/other', cloud.get('posts', {}))

        (state / 'mirror_queue.json').write_text(json.dumps({
            'schema_version': 2, 'root_token': 'fixture-root', 'folders': {},
            'posts': {
                'fa_other/other': {'token': 'folder', 'status': 'completed'},
                self.f.task_id: {'status': 'pending', 'incomplete_source': True, 'missing_media': [1]},
            },
            'snapshots': {'source': {'post_key': self.f.task_id, 'status': 'pending'}},
            'operations': {},
        }), encoding='utf-8')
        current = self.f.client.get(self.f.url).json()['storage']['feishu']
        self.assertEqual(current['status'], 'pending')
        self.assertTrue(current['incomplete_source'])
        self.assertEqual(current['missing_media'], [1])
        self.assertEqual(current['counts'], {'pending': 1, 'completed': 0, 'uncertain': 0, 'blocked': 0})


if __name__ == '__main__':
    unittest.main()
