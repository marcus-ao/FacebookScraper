"""Historical queries page through source-backed data without triggering models."""
import json
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests_web_review as fixtures
from core import index_db, store
from web.api import query_index, reader


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

    def test_history_window_absorbs_paging_while_review_queue_stays_per_request(self):
        """历史分页复用有限时间的源校验；待审队列仍逐请求核对。"""
        base = datetime(2026, 9, 13, 8, 0, tzinfo=timezone.utc)
        self.assertEqual(query_index.history_page(tag='M1', limit=50, now=base)['total'], 3)

        self.archive.append(store.Post('13', 'instagram', 'neakasa.tech', 'Archived 3',
                                       '2021-04-01T12:00:00Z', tags=['M1']))
        inside = query_index.history_page(tag='M1', limit=50, now=base + timedelta(seconds=10))
        self.assertEqual(inside['total'], 3)
        self.assertFalse(inside['index']['stale'])

        expired = base + timedelta(seconds=query_index.HISTORY_INDEX_MAX_AGE_SECONDS + 1)
        self.assertEqual(query_index.history_page(tag='M1', limit=50, now=expired)['total'], 4)

        active = store.Archive(self.f.root / 'archive', self.f.account.name)
        before = query_index.candidates(now=base)['task_ids']
        active.append(store.Post('777', 'facebook', 'neakasaofficial', 'Neu',
                                 '2026-09-12T10:00:00Z'))
        after = query_index.candidates(now=base)['task_ids']
        self.assertEqual(len(after), len(before) + 1)

    def test_a_row_without_an_image_advertises_no_thumbnail_to_fetch(self):
        """列表给出的缩略图地址必须真的取得到，否则前端每行都会白跑一次 404。"""
        self.archive.append(store.Post('14', 'instagram', 'neakasa.tech', 'Nur Video',
                                       '2021-05-01T12:00:00Z', tags=['M1'],
                                       media=[store.Media('https://example.invalid/v.mp4', 'video',
                                                          local_path='posts/x/01.mp4')]))
        # 图还没落盘的帖子同样取不到字节，`local_path` 要等下载完成才补上。
        self.archive.append(store.Post('15', 'instagram', 'neakasa.tech', 'Bild fehlt noch',
                                       '2021-06-01T12:00:00Z', tags=['M1'], media_complete=False,
                                       media=[store.Media('https://example.invalid/i.jpg', 'image')]))
        rows = {row['id']: row for row in
                self.f.client.get('/api/tasks?scope=history&limit=50').json()['tasks']}
        # 三条无 media 的归档帖和一条只有视频的，都没有第 0 张图可取。
        for task_id in ('in_neakasa.tech/10', 'in_neakasa.tech/11', 'in_neakasa.tech/12', 'in_neakasa.tech/14'):
            self.assertEqual(rows[task_id]['thumbnail_url'], '', task_id)
            self.assertEqual(rows[task_id]['image_count'], 0, task_id)
        # 待补齐的那条数得出 1 张图，但不给取不到的地址。
        self.assertEqual(rows['in_neakasa.tech/15']['image_count'], 1)
        self.assertEqual(rows['in_neakasa.tech/15']['thumbnail_url'], '')
        # 有图的那条照常给地址，并且该地址确实能取到字节。
        active = rows[self.f.account.name + '/' + self.f.source['post_id']]
        self.assertEqual(active['image_count'], 1)
        self.assertEqual(self.f.client.get(active['thumbnail_url']).status_code, 200)
        # 待审队列走另一条取数路径，同一条契约也要成立。
        queue = {row['id']: row for row in reader.list_tasks()['tasks']}
        self.assertEqual(queue[active['id']]['thumbnail_url'], active['thumbnail_url'])

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
