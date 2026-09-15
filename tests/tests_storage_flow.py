"""同一组 FB/IG 夹具走解析、下载、本地、假云盘与查询；不触发模型或发布。"""
import asyncio
import hashlib
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from image_fixtures import image_bytes

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import capture, config, index_db, mirror, parse, store
from tests_mirror import FakeDrive
from web.api import query_index


class StorageFlowTests(unittest.TestCase):
    def test_two_platform_sources_keep_identity_caption_order_and_bytes_across_stores(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            configured = config.Config()
            configured._d['paths'] = {'archive': str(root / 'archive'), 'state': str(root / 'state')}
            now = datetime(2026, 9, 15, tzinfo=timezone.utc)
            captured_at = int(datetime(2026, 8, 31, 20, tzinfo=timezone.utc).timestamp())
            payloads = {
                'instagram': {'items': [{
                    'pk': 'ig_fixture', 'code': 'ABC', 'taken_at': captured_at,
                    'user': {'username': 'neakasa.global'},
                    'caption': {'text': 'Launch #NeakasaP1Pro #S1Pro'},
                    'carousel_media': [
                        {'image_versions2': {'candidates': [{'url': 'https://cdn.invalid/red.png', 'width': 4, 'height': 3}]}},
                        {'image_versions2': {'candidates': [{'url': 'https://cdn.invalid/blue.png', 'width': 4, 'height': 3}]}},
                    ],
                }]},
                'facebook': {'node': {
                    'post_id': 'fb_fixture', 'creation_time': captured_at,
                    'actors': [{'url': 'https://www.facebook.com/neakasaofficial', 'name': 'Neakasa'}],
                    'message': {'text': 'Launch #NeakasaP1Pro'},
                    'attachments': [{'image': {'uri': 'https://cdn.invalid/red.png', 'width': 4, 'height': 3}}],
                }},
            }
            class BrowserContext:
                request = None
                async def get(self, url, **_kwargs):
                    class Response:
                        ok = True
                        headers = {'content-type': 'image/png'}
                        async def body(self):
                            return image_bytes('PNG', 'blue' if 'blue' in url else 'red')
                    return Response()
            context = BrowserContext()
            context.request = context
            drive = FakeDrive()
            service = mirror.MirrorService(root / 'state', mirror.MirrorSettings(True, 'fixture-root', False))
            with patch.object(config, '_cfg', configured), patch('httpx.Client.send', side_effect=AssertionError('unexpected network')):
                expected_media = {}
                truth = {}
                for platform, account, directory in (
                    ('facebook', 'neakasaofficial', 'fa_neakasaofficial'),
                    ('instagram', 'neakasa.global', 'in_neakasa.global'),
                ):
                    sources = parse.extract([payloads[platform]], platform, account, 'delta')
                    self.assertEqual(len(sources), 1)
                    post = sources[0]
                    self.assertTrue(parse.on_timeline_of(post, account))
                    arc = store.Archive(root / 'archive', directory)
                    asyncio.run(capture.download_media(context, arc, post, 'https://social.invalid/'))
                    self.assertTrue(arc.append(post))
                    self.assertFalse(arc.append(post))
                    row = arc.rows()[0]
                    self.assertTrue(row['folder_name'].startswith('2026-09-01_0400_'))
                    self.assertEqual(row['tags'][0], 'P1 Pro')
                    self.assertEqual(row['text'], post.text)
                    facts = store.media_storage_info(arc.base, row)
                    expected_media[directory + '/' + post.post_id] = [item['sha256'] for item in facts]
                    self.assertTrue(all(item['storage_status'] == 'saved' for item in facts))
                    path = arc.post_dir(post) / 'post.json'
                    truth[path] = path.read_bytes()
                    self.assertTrue(service.queue_source(arc.base, row, now=now))
                self.assertEqual(service.dispatch(drive, now=now)['completed'], 2)
                cloud_images = [hashlib.sha256(content).hexdigest() for _parent, name, content in drive.files if name.endswith('.png')]
                self.assertCountEqual(cloud_images, [digest for values in expected_media.values() for digest in values])
                grouped = {}
                for parent, name, content in drive.files:
                    if name.endswith('.png'):
                        grouped.setdefault(parent, []).append(hashlib.sha256(content).hexdigest())
                self.assertCountEqual(list(grouped.values()), list(expected_media.values()))
                page = query_index.history_page(month='2026-09', now=now)
                self.assertEqual(page['total'], 2)
                database = root / 'state' / 'index-history.sqlite'
                self.assertTrue(index_db.check_consistency(root / 'archive', database, state_dir=root / 'state', include_frozen=True)['consistent'])
                with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
                    for task_id, hashes in expected_media.items():
                        actual = [row[0] for row in connection.execute('SELECT sha256 FROM post_media WHERE task_id=? ORDER BY ordinal', (task_id,))]
                        self.assertEqual(actual, hashes)
                self.assertEqual({path: path.read_bytes() for path in truth}, truth)
                self.assertFalse((root / 'state' / 'paid_requests.jsonl').exists())
                self.assertFalse((root / 'state' / 'published.jsonl').exists())


if __name__ == '__main__':
    unittest.main()
