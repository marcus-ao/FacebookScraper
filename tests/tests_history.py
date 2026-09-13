"""Historical queries page through source-backed data without triggering models."""
import json
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests_web_review as fixtures
from core import index_db, store
from web.api import reader


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.WebReviewTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.archive = store.Archive(self.f.root / 'archive', 'in_neakasa.tech')
        for n in range(3):
            self.archive.append(store.Post(str(10+n), 'instagram', 'neakasa.tech', 'Archived '+str(n),
                f'2021-0{n+1}-01T12:00:00Z', tags=['M1']))

    def test_history_is_paginated_filtered_and_frozen_account_is_read_only(self):
        first = self.f.client.get('/api/tasks?scope=history&platform=instagram&tag=M1&limit=2&page=1').json()
        self.assertEqual(first['pagination']['total'], 3)
        self.assertEqual(len(first['tasks']), 2)
        self.assertTrue(all(row['read_only'] for row in first['tasks']))
        second = self.f.client.get('/api/tasks?scope=history&platform=instagram&tag=M1&limit=2&page=2').json()
        self.assertEqual([row['id'] for row in second['tasks']], ['in_neakasa.tech/10'])
        self.assertTrue(all(not row['id'].startswith('in_neakasa.tech/') for row in reader.list_tasks()['tasks']))
        detail = self.f.client.get('/api/tasks/in_neakasa.tech/10')
        self.assertEqual(detail.status_code, 200)
        self.assertTrue(detail.json()['read_only'])
        response = self.f.client.put('/api/tasks/in_neakasa.tech/10/text_de', json={
            'text_de': 'Neu', 'source_text_sha256': detail.json()['text']['source_text_sha256'], 'human_revision': None})
        self.assertEqual(response.status_code, 409)
        self.assertFalse((self.archive.base / 'translated_human.jsonl').exists())

    def test_old_active_post_detail_ignores_daily_horizon(self):
        self.f.source['created_at'] = '2021-01-01T12:00:00Z'
        self.f.write_source()
        (self.f.account / 'manifest.jsonl').write_text(json.dumps(self.f.source) + '\n', encoding='utf-8')
        self.assertIsNotNone(reader.task_detail(self.f.account.name + '/' + self.f.source['post_id']))

    def test_history_falls_back_to_files_on_index_failure(self):
        with patch('web.api.query_index.refresh_display_index', return_value={'available': False, 'stale': True, 'error': 'offline'}):
            payload = self.f.client.get('/api/tasks?scope=history&month=2021-02&limit=1').json()
        self.assertEqual(payload['pagination']['total'], 1)
        self.assertEqual(payload['tasks'][0]['id'], 'in_neakasa.tech/11')
        self.assertTrue(payload['index']['stale'])

    def test_consistency_compares_database_values_with_local_files(self):
        database = self.f.root / 'state' / 'history.sqlite'
        index_db.rebuild_index(self.f.root / 'archive', database, include_frozen=True)
        with sqlite3.connect(database) as connection:
            row = json.loads(connection.execute("SELECT row_json FROM posts WHERE task_id='in_neakasa.tech/10'").fetchone()[0])
            row['text'] = 'wrong'
            connection.execute("UPDATE posts SET row_json=? WHERE task_id='in_neakasa.tech/10'", (json.dumps(row),))
        result = index_db.check_consistency(self.f.root / 'archive', database, include_frozen=True)
        self.assertFalse(result['consistent'])
        self.assertIn('in_neakasa.tech/10', result['changed'])


if __name__ == '__main__':
    unittest.main()
