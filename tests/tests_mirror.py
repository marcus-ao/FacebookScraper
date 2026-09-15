"""单向云盘镜像：全部用临时源文件与假 Drive，绝不上传真实数据。"""
from __future__ import annotations

import json
import hashlib
import io
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from contextlib import redirect_stdout

import httpx
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import mirror, store  # noqa: E402
from tests.image_fixtures import image_bytes  # noqa: E402


class FakeDrive:
    def __init__(self):
        self.folders, self.files, self.moves = [], [], []
        self.fail_upload = False

    def create_folder(self, parent, name):
        token = 'folder-' + str(len(self.folders))
        self.folders.append((parent, name, token))
        return token

    def upload_file(self, parent, name, content):
        if self.fail_upload:
            raise mirror.DriveError('pending', http_status=429)
        self.files.append((parent, name, content))
        return 'file-' + str(len(self.files))

    def move_folder(self, token, parent):
        self.moves.append((token, parent))
        return None

    def task_status(self, task_id):
        return 'success'


class MirrorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.arc = store.Archive(self.root / 'archive', 'in_neakasa.global')
        self.source = store.Post('123', 'instagram', 'neakasa.global', 'Source caption',
                                 '2026-09-10T10:00:00Z', tags=['M1 Pro', 'Campaign'])
        self.arc.append(self.source)
        self.now = datetime(2026, 9, 12, 1, tzinfo=timezone.utc)
        self.settings = mirror.MirrorSettings(True, 'remote-root', True)
        self.service = mirror.MirrorService(self.root / 'state', self.settings)
        self.drive = FakeDrive()

    def test_source_snapshot_is_immutable_versioned_and_deduplicated(self):
        row = self.arc.rows()[0]
        self.assertTrue(self.service.queue_source(self.arc.base, row, now=self.now))
        changed = store.Post('123', 'instagram', 'neakasa.global', 'Changed source',
                             self.source.created_at)
        self.arc.append(changed)
        self.assertTrue(self.service.queue_source(self.arc.base, self.arc.rows()[0], now=self.now))
        self.assertFalse(self.service.queue_source(self.arc.base, self.arc.rows()[0], now=self.now))
        before = (self.arc.post_dir(changed) / 'post.json').read_bytes()
        self.assertEqual(self.service.dispatch(self.drive, now=self.now)['completed'], 2)
        names = [item[1] for item in self.drive.folders]
        self.assertIn('2026-09', names)
        self.assertIn('M1-Pro', names)
        self.assertIn('01_原帖', names)
        self.assertIn('01_原帖_v2', names)
        texts = [item[2] for item in self.drive.files if item[1] == 'text_en.txt']
        self.assertEqual(texts, [b'Source caption', b'Changed source'])
        self.assertEqual((self.arc.post_dir(changed) / 'post.json').read_bytes(), before)
        count = len(self.drive.files)
        self.service.dispatch(self.drive, now=self.now)
        self.assertEqual(len(self.drive.files), count)

    def test_lost_upload_response_is_uncertain_and_never_replayed_after_restart(self):
        self.service.queue_source(self.arc.base, self.arc.rows()[0], now=self.now)
        original = self.drive.upload_file
        def lose_response(*args):
            original(*args)
            raise httpx.ReadTimeout('secret remote response')
        with patch.object(self.drive, 'upload_file', side_effect=lose_response):
            self.service.dispatch(self.drive, now=self.now)
        count = len(self.drive.files)
        restarted = mirror.MirrorService(self.root / 'state', self.settings)
        restarted.dispatch(self.drive, now=self.now + timedelta(days=1))
        self.assertEqual(len(self.drive.files), count)
        status = restarted.status()
        self.assertEqual(status['counts']['uncertain'], 1)
        self.assertNotIn('secret remote response', json.dumps(status))

    def test_metadata_pagination_and_safe_rejection_classification(self):
        pages = []
        def handle(request):
            if request.url.path.endswith('/internal'):
                return httpx.Response(200, json={'code': 0, 'tenant_access_token': 'token', 'expire': 7200})
            if request.method == 'GET':
                pages.append(dict(request.url.params))
                second = request.url.params.get('page_token') == 'next'
                return httpx.Response(200, json={'code': 0, 'data': {'files':
                    [{'name': 'same', 'type': 'folder', 'token': 'two' if second else 'one'}],
                    'has_more': not second, 'next_page_token': '' if second else 'next'}})
            return httpx.Response(403, headers={'X-Tt-Logid': 'safe-request-id'},
                                  json={'code': 1061004, 'msg': 'SECRET'})
        with httpx.Client(transport=httpx.MockTransport(handle)) as http:
            client = mirror.DriveClient('id', 'secret', http=http)
            self.assertEqual(len(client.list_files('root')), 2)
            self.assertEqual(pages[0]['page_size'], '200')
            with self.assertRaises(mirror.DriveError) as error:
                client.create_folder('root', 'new')
            self.assertEqual(error.exception.status, 'blocked')
            self.assertEqual(error.exception.details['request_id'], 'safe-request-id')
            self.assertNotIn('SECRET', str(error.exception))

    def test_drive_errors_distinguish_confirmed_causes_without_response_text(self):
        cases = [(429, 99991400, 'pending', 'rate_limit'), (401, 99991663, 'blocked', 'auth'),
                 (403, 1061004, 'blocked', 'permission'), (400, 1061045, 'blocked', 'quota_or_transient'),
                 (503, None, 'uncertain', 'transient'), (200, 1061001, 'uncertain', 'transient'),
                 (200, 1064230, 'pending', 'transient'), (403, 1061073, 'blocked', 'permission'),
                 (400, 1061101, 'blocked', 'capacity'), (400, 1061061, 'blocked', 'capacity'),
                 (400, 1062507, 'blocked', 'capacity')]
        for http_status, code, state, reason in cases:
            with self.subTest(reason=reason):
                response = httpx.Response(http_status, headers={'X-Tt-Logid': 'safe-id'},
                                          json={'code': code, 'msg': 'SECRET response body'})
                with self.assertRaises(mirror.DriveError) as error:
                    mirror.DriveClient._drive_response(response)
                self.assertEqual(error.exception.status, state)
                self.assertEqual(error.exception.details.get('reason'), reason)
                self.assertNotIn('SECRET', json.dumps(error.exception.details))

    def test_status_reports_a_safe_retry_reason_without_read_side_effects(self):
        self.service.queue_source(self.arc.base, self.arc.rows()[0], now=self.now)
        self.drive.fail_upload = True
        self.service.dispatch(self.drive, now=self.now)
        before = self.service.path.read_bytes()
        with patch.object(mirror.DriveClient, 'from_environment') as client:
            status = self.service.status()
            client.assert_not_called()
        self.assertEqual(self.service.path.read_bytes(), before)
        self.assertIn('last_error', status)
        self.assertIn('限流', status['last_error'])
        self.assertTrue(any(op['error'] and op['error'].get('reason') == 'rate_limit'
                            for op in status['operations']))
        self.drive.fail_upload = False
        self.service.dispatch(self.drive, now=self.now + timedelta(minutes=16))
        self.assertIsNone(self.service.status()['last_error'])

    def test_multipart_retries_only_rejected_part_and_never_replays_lost_finish(self):
        calls = []
        rejected = [False]
        def handle(request):
            route = request.url.path.rsplit('/', 1)[-1]
            calls.append(route)
            if route == 'internal':
                return httpx.Response(200, json={'code': 0, 'tenant_access_token': 'token', 'expire': 7200})
            if route == 'create_folder':
                data = {'token': 'folder-' + str(len(calls))}
            elif route == 'upload_prepare':
                data = {'upload_id': 'transaction', 'block_size': 1024, 'block_num': 2}
            elif route == 'upload_part':
                if b'EFG' in request.content and not rejected[0]:
                    rejected[0] = True
                    return httpx.Response(429, json={'code': 99991400})
                data = {}
            elif route == 'upload_finish':
                raise httpx.ReadTimeout('response lost')
            else:
                data = {'file_token': 'small'}
            return httpx.Response(200, json={'code': 0, 'data': data})
        row = self.arc.rows()[0]
        self.service.queue_stage(self.arc.base, row, 'approved', {'large.bin': b'A' * 1024 + b'EFG'},
            evidence={'status': 'approved', 'post_id': row['post_id'], 'platform': row['platform']}, now=self.now)
        with httpx.Client(transport=httpx.MockTransport(handle)) as http:
            client = mirror.DriveClient('id', 'secret', http=http)
            with patch.object(client, 'SMALL_FILE_LIMIT', 1024), patch.object(client, '_pace_drive'):
                self.service.dispatch(client, now=self.now)
                restarted = mirror.MirrorService(self.root / 'state', self.settings)
                restarted.dispatch(client, now=self.now + timedelta(minutes=16))
                restarted.dispatch(client, now=self.now + timedelta(days=2))
        self.assertEqual(calls.count('upload_prepare'), 1)
        self.assertEqual(calls.count('upload_part'), 3)
        self.assertEqual(calls.count('upload_finish'), 1)
        self.assertEqual(restarted.status()['counts']['uncertain'], 1)

    def test_manual_resolve_requires_frozen_hash_and_does_not_send(self):
        self.service.queue_source(self.arc.base, self.arc.rows()[0], now=self.now)
        original = self.drive.upload_file
        def lost(*args):
            original(*args)
            raise httpx.ReadTimeout('lost')
        with patch.object(self.drive, 'upload_file', side_effect=lost):
            self.service.dispatch(self.drive, now=self.now)
        operation = next(op for op in self.service.status()['operations'] if op['status'] == 'uncertain')
        with self.assertRaises(mirror.MirrorError):
            self.service.resolve(operation['id'], sha256='0' * 64, remote_token='known-token', note='checked', now=self.now)
        self.service.resolve(operation['id'], sha256=operation['sha256'], remote_token='known-token', note='checked', now=self.now)
        self.assertEqual(len(self.drive.files), 1)
        self.service.dispatch(self.drive, now=self.now)
        self.assertEqual(sum(name == 'text_en.txt' for _, name, _ in self.drive.files), 1)
        self.assertEqual(self.service.status()['counts']['completed'], 1)

    def test_preflight_directory_duplicates_are_ambiguous_and_read_only(self):
        self.service.queue_source(self.arc.base, self.arc.rows()[0], now=self.now)
        with patch.object(self.drive, 'create_folder', side_effect=httpx.ReadTimeout('lost')):
            self.service.dispatch(self.drive, now=self.now)
        before = self.service.path.read_bytes()
        self.drive.list_files = lambda parent: [{'name': '2026-09', 'type': 'folder', 'token': token}
                                                for token in ('one', 'two')]
        report = self.service.preflight(self.drive)
        self.assertEqual(report['reconciliation'][0]['status'], 'uncertain')
        self.assertEqual(self.service.path.read_bytes(), before)

    def test_local_ack_failure_and_interrupted_intent_preserve_uncertainty(self):
        self.service.queue_source(self.arc.base, self.arc.rows()[0], now=self.now)
        write = mirror.atomic_write_json
        fail = [True]
        def lose_ack(path, value, **kwargs):
            if (path == self.service.path and fail[0] and any(op.get('kind') == 'upload'
                    and op.get('status') == 'completed' for op in value.get('operations', {}).values())):
                fail[0] = False
                raise OSError('disk failure')
            return write(path, value, **kwargs)
        with patch.object(mirror, 'atomic_write_json', side_effect=lose_ack):
            self.service.dispatch(self.drive, now=self.now)
        count = len(self.drive.files)
        restarted = mirror.MirrorService(self.root / 'state', self.settings)
        restarted.dispatch(self.drive, now=self.now + timedelta(days=1))
        self.assertEqual(len(self.drive.files), count)
        self.assertEqual(restarted.status()['counts']['uncertain'], 1)
        # Model a process death after an intent was flushed but before its ACK.
        raw = restarted.snapshot()
        operation = next(op for op in raw['operations'].values() if op['kind'] == 'upload')
        operation['status'] = 'in_flight'
        self.service.path.write_text(json.dumps(raw), encoding='utf-8', newline='')
        before = self.service.path.read_bytes()
        self.assertEqual(restarted.status()['counts']['uncertain'], 1)
        self.assertEqual(self.service.path.read_bytes(), before)

    def test_missing_media_mirrors_available_bytes_and_reports_incomplete(self):
        post = store.Post('missing', 'instagram', 'neakasa.global', 'caption', self.source.created_at,
                          media=[store.Media('https://cdn.invalid/missing', 'image')])
        self.arc.append(post)
        self.service.queue_source(self.arc.base, post.to_row(), now=self.now)
        self.service.dispatch(self.drive, now=self.now)
        self.assertTrue(self.service.status()['posts']['in_neakasa.global/missing']['incomplete_source'])
        self.assertIn('原帖不完整.json', [name for _, name, _ in self.drive.files])

    def test_deleted_local_media_does_not_prevent_remaining_source_backup(self):
        post = store.Post('deleted', 'instagram', 'neakasa.global', 'saved caption', self.source.created_at,
                          media=[store.Media('https://cdn.invalid/missing', 'image')])
        path = self.arc.media_path(post, 0, 'image/jpeg')
        path.write_bytes(b'ORIGINAL')
        post.media[0].local_path = path.relative_to(self.arc.base).as_posix()
        self.arc.append(post)
        path.unlink()
        self.service.queue_source(self.arc.base, post.to_row(), now=self.now)
        self.service.dispatch(self.drive, now=self.now)
        self.assertIn(b'saved caption', [content for _, _, content in self.drive.files])
        self.assertTrue(self.service.status()['posts']['in_neakasa.global/deleted']['incomplete_source'])

    def test_restored_media_with_recorded_hash_creates_a_complete_version(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                service = mirror.MirrorService(self.root / ('legacy' if legacy else 'current'), self.settings)
                drive = FakeDrive()
                content = image_bytes()
                post = store.Post('restore-' + str(legacy), 'instagram', 'neakasa.global', 'caption',
                                  self.source.created_at, media=[store.Media('https://cdn.invalid/restore.jpg', 'image')])
                path = self.arc.media_path(post, 0, 'image/jpeg')
                path.write_bytes(content)
                post.media[0].local_path = path.relative_to(self.arc.base).as_posix()
                post.media[0].sha256 = hashlib.sha256(content).hexdigest()
                self.arc.append(post)
                path.unlink()
                service.queue_source(self.arc.base, post.to_row(), now=self.now)
                service.dispatch(drive, now=self.now)
                if legacy:
                    raw = service.snapshot()
                    raw['schema_version'] = 1
                    raw.pop('operations')
                    for item in raw['snapshots'].values():
                        item.pop('content_sha256', None)
                        item.pop('source_identity_version', None)
                    service.path.write_text(json.dumps(raw), encoding='utf-8', newline='')
                path.write_bytes(content)
                self.assertTrue(service.queue_source(self.arc.base, post.to_row(), now=self.now))
                service.dispatch(drive, now=self.now)
                self.assertEqual(len(service.snapshot()['snapshots']), 2)
                self.assertIn(content, [raw for _, _, raw in drive.files])
                self.assertFalse(service.status()['posts']['in_neakasa.global/' + post.post_id]['incomplete_source'])

    def test_restored_historical_media_versions_without_path_or_tag_identity(self):
        originals = []
        for label, color in (('old', 'red'), ('new', 'blue')):
            content = image_bytes(color=color)
            post = store.Post('history-restore', 'instagram', 'neakasa.global', label,
                              self.source.created_at, media=[store.Media('https://cdn.invalid/' + label + '.jpg', 'image')])
            path = self.arc.media_path(post, 0, 'image/jpeg')
            path.write_bytes(content)
            post.media[0].local_path = path.relative_to(self.arc.base).as_posix()
            post.media[0].sha256 = hashlib.sha256(content).hexdigest()
            self.arc.append(post)
            originals.append((path, content))
        originals[0][0].unlink()
        self.service.queue_source(self.arc.base, post.to_row(), now=self.now)
        self.service.dispatch(self.drive, now=self.now)
        originals[0][0].write_bytes(originals[0][1])
        store.update_post_tags(self.arc.base, post.to_row(), ['Moved history'])
        self.assertTrue(self.service.queue_source(self.arc.base, post.to_row(), now=self.now))
        self.service.dispatch(self.drive, now=self.now)
        self.assertIn(originals[0][1], [raw for _, _, raw in self.drive.files])
        self.assertFalse(self.service.status()['posts']['in_neakasa.global/history-restore']['incomplete_source'])
        raw = self.service.snapshot()
        raw['schema_version'] = 1
        raw.pop('operations')
        for item in raw['snapshots'].values():
            item.pop('content_sha256', None)
            item.pop('source_identity_version', None)
        raw['snapshots'] = {'legacy-' + str(index): item for index, item in enumerate(raw['snapshots'].values())}
        self.service.path.write_text(json.dumps(raw), encoding='utf-8', newline='')
        before = len(self.drive.files)
        store.update_post_tags(self.arc.base, post.to_row(), ['Moved again'])
        self.assertFalse(self.service.queue_source(self.arc.base, post.to_row(), now=self.now))
        self.service.dispatch(self.drive, now=self.now)
        self.assertEqual(len(self.drive.files), before)
        self.assertEqual(len(self.service.snapshot()['snapshots']), 2)

    def test_corrupt_status_is_read_only_and_never_constructs_client(self):
        self.service.state_dir.mkdir()
        for raw in (b'{"SECRET":', b'{"schema_version":2,"root_token":"remote-root","posts":{},"folders":{},"snapshots":{},"operations":{"bad":{}}}'):
            self.service.path.write_bytes(raw)
            with patch.object(mirror.DriveClient, 'from_environment') as client:
                with self.assertRaises(mirror.MirrorError) as error:
                    self.service.status()
                self.assertNotIn('SECRET', str(error.exception))
                client.assert_not_called()
            self.assertEqual(self.service.path.read_bytes(), raw)

    def test_legacy_attempt_preserves_tokens_and_can_be_manually_resolved(self):
        self.service.queue_source(self.arc.base, self.arc.rows()[0], now=self.now)
        self.service.dispatch(self.drive, now=self.now)
        raw = self.service.snapshot()
        raw['schema_version'] = 1
        raw.pop('operations')
        item = next(iter(raw['snapshots'].values()))
        old_token = item['files'][0]['token']
        item['files'][0]['token'] = None
        item['status'] = 'pending'
        self.service.path.write_text(json.dumps(raw), encoding='utf-8', newline='')
        status = self.service.status()
        self.assertEqual(status['counts']['uncertain'], 1)
        op = next(op for op in status['operations'] if op['status'] == 'uncertain')
        self.service.resolve(op['id'], sha256=op['sha256'], remote_token=old_token, note='legacy checked', now=self.now)
        before = len(self.drive.files)
        self.service.dispatch(self.drive, now=self.now)
        self.assertEqual(len(self.drive.files), before)
        self.assertEqual(self.service.status()['counts']['completed'], 1)

    def test_legacy_source_identity_migration_does_not_duplicate_frozen_version(self):
        row = self.arc.rows()[0]
        self.service.queue_source(self.arc.base, row, now=self.now)
        raw = self.service.snapshot()
        item = next(iter(raw['snapshots'].values()))
        item.pop('content_sha256', None)
        raw['snapshots'] = {'legacy-fingerprint': item}
        self.service.path.write_text(json.dumps(raw), encoding='utf-8', newline='')
        store.update_post_tags(self.arc.base, row, ['S1 Pro'])
        self.assertFalse(self.service.queue_source(self.arc.base, row, now=self.now))
        self.assertEqual(len(self.service.snapshot()['snapshots']), 1)

    def test_expired_multipart_transaction_blocks_without_new_prepare(self):
        row = self.arc.rows()[0]
        self.service.queue_stage(self.arc.base, row, 'approved', {'large.bin': b'A' * 1025},
            evidence={'status': 'approved', 'post_id': row['post_id'], 'platform': row['platform']}, now=self.now)
        self.drive.SMALL_FILE_LIMIT = 1024
        self.drive.prepare_upload = lambda *args: {'upload_id': 'expired', 'block_size': 1024, 'block_num': 2}
        self.drive.upload_part = lambda *args: (_ for _ in ()).throw(mirror.DriveError('pending', http_status=429))
        with patch.object(self.drive, 'prepare_upload', wraps=self.drive.prepare_upload) as prepare:
            self.service.dispatch(self.drive, now=self.now)
            self.service.dispatch(self.drive, now=self.now + timedelta(days=2))
            prepare.assert_called_once()
        self.assertEqual(self.service.status()['counts']['blocked'], 1)
        self.assertTrue(any(op['status'] == 'blocked' and op['kind'] == 'upload_prepare'
                            for op in self.service.status()['operations']))

    def test_tags_move_existing_folder_without_reuploading_versions(self):
        row = self.arc.rows()[0]
        self.service.queue_source(self.arc.base, row, now=self.now)
        self.service.dispatch(self.drive, now=self.now)
        count = len(self.drive.files)
        store.update_post_tags(self.arc.base, row, ['S1 Pro'])
        self.assertFalse(self.service.queue_source(self.arc.base, row, now=self.now))
        self.assertEqual(self.service.status()['posts']['in_neakasa.global/123']['status'], 'pending')
        self.assertEqual(self.service.status()['status'], 'pending')
        self.service.dispatch(self.drive, now=self.now)
        self.assertEqual(len(self.drive.moves), 1)
        self.assertEqual(len(self.drive.files), count)
        self.assertIn('S1-Pro', [item[1] for item in self.drive.folders])

    def test_tag_change_preserves_uncertain_placement_until_resolution(self):
        row = self.arc.rows()[0]
        self.service.queue_source(self.arc.base, row, now=self.now)
        with patch.object(self.drive, 'create_folder', side_effect=httpx.ReadTimeout('lost')):
            self.service.dispatch(self.drive, now=self.now)
        store.update_post_tags(self.arc.base, row, ['New destination'])
        self.service.queue_source(self.arc.base, row, now=self.now)
        self.service.dispatch(self.drive, now=self.now + timedelta(days=1))
        self.assertEqual(self.service.status()['status'], 'uncertain')
        self.assertEqual(self.drive.folders, [])

    def test_old_source_without_folder_name_keeps_its_actual_directory_month(self):
        row = {'post_id': 'legacy-month', 'platform': 'instagram', 'account': 'neakasa.global',
               'text': 'legacy original', 'created_at': '2026-08-31T18:00:00Z', 'tags': ['Old'], 'media': []}
        directory = self.arc.base / 'posts' / '2026-08-31_1800_legacy-month'
        directory.mkdir()
        (directory / 'post.json').write_text(json.dumps(row), encoding='utf-8', newline='')
        self.service.queue_source(self.arc.base, row, now=self.now)
        self.service.dispatch(self.drive, now=self.now)
        self.assertEqual(self.service.snapshot()['posts']['in_neakasa.global/legacy-month']['month'], '2026-08')
        self.assertIn('2026-08', [name for _, name, _ in self.drive.folders])

    def test_tag_move_with_images_does_not_version_source_content(self):
        post = store.Post('image-tag', 'instagram', 'neakasa.global', 'caption',
                          '2026-08-31T18:00:00Z', tags=['old'],
                          media=[store.Media('https://cdn.invalid/pic.jpg?sig=1', 'image')])
        path = self.arc.media_path(post, 0, 'image/jpeg')
        path.write_bytes(b'ORIGINAL')
        post.media[0].local_path = path.relative_to(self.arc.base).as_posix()
        self.arc.append(post)
        row = next(r for r in self.arc.rows() if r['post_id'] == post.post_id)
        self.service.queue_source(self.arc.base, row, now=self.now)
        self.service.dispatch(self.drive, now=self.now)
        before = len(self.drive.files)
        store.update_post_tags(self.arc.base, row, ['new'])
        self.assertFalse(self.service.queue_source(self.arc.base, row, now=self.now))
        self.service.dispatch(self.drive, now=self.now)
        self.assertEqual(len(self.drive.files), before)

    def test_repeated_round_trip_tag_moves_each_execute_once(self):
        row = self.arc.rows()[0]
        self.service.queue_source(self.arc.base, row, now=self.now)
        self.service.dispatch(self.drive, now=self.now)
        for tag in ('S1 Pro', 'M1 Pro', 'S1 Pro'):
            store.update_post_tags(self.arc.base, row, [tag])
            self.service.queue_source(self.arc.base, row, now=self.now)
            self.service.dispatch(self.drive, now=self.now)
        self.assertEqual(len(self.drive.moves), 3)

    def test_stage_gates_and_upload_retry_keep_frozen_approved_bytes(self):
        row = self.arc.rows()[0]
        files = {'text_de.txt': b'Approved German caption'}
        for status in ('prepared', 'submit_ambiguous', 'submitted_unverified', 'failed_pre_submit'):
            with self.assertRaises(mirror.MirrorError):
                self.service.queue_stage(self.arc.base, row, 'scheduled', files,
                                         evidence={'status': status}, now=self.now)
        approved = {'status': 'approved', 'post_id': row['post_id'], 'platform': row['platform']}
        self.service.queue_stage(self.arc.base, row, 'approved', files, evidence=approved, now=self.now)
        self.drive.fail_upload = True
        self.assertGreater(self.service.dispatch(self.drive, now=self.now)['pending'], 0)
        restarted = mirror.MirrorService(self.root / 'state', self.settings)
        self.drive.fail_upload = False
        self.assertEqual(restarted.dispatch(self.drive, now=self.now + timedelta(minutes=1))['completed'], 0)
        self.assertEqual(restarted.dispatch(self.drive, now=self.now + timedelta(minutes=16))['completed'], 1)
        self.assertIn(b'Approved German caption', [item[2] for item in self.drive.files])
        confirmed = dict(approved, status='scheduled', attempt_id='confirmed-1')
        restarted.queue_stage(self.arc.base, row, 'scheduled', files, evidence=confirmed, now=self.now)
        restarted.dispatch(self.drive, now=self.now + timedelta(minutes=16))
        self.assertIn('03_已发布', [item[1] for item in self.drive.folders])
        self.assertIn('排期回执.json', [item[1] for item in self.drive.files])

    def test_state_backup_is_default_keeps_raw_bytes_and_does_not_mirror_itself(self):
        state = self.root / 'state'
        state.mkdir()
        (state / 'published.jsonl').write_bytes(b'{"status":"scheduled"}\n')
        (state / 'empty.jsonl').write_bytes(b'')
        (state / 'publish.lock').write_bytes(b'lock')
        self.assertTrue(self.service.queue_state(state, now=self.now))
        self.service.dispatch(self.drive, now=self.now)
        archive = next(content for _, name, content in self.drive.files if name == 'state.zip')
        with zipfile.ZipFile(io.BytesIO(archive)) as snapshot:
            self.assertEqual(set(snapshot.namelist()), {'published.jsonl', 'empty.jsonl'})
            self.assertEqual(snapshot.read('published.jsonl'), b'{"status":"scheduled"}\n')
            self.assertEqual(snapshot.read('empty.jsonl'), b'')
        self.assertFalse(self.service.queue_state(state, now=self.now + timedelta(minutes=1)))
        self.assertFalse(self.service.queue_state(state, now=self.now + timedelta(hours=2)))
        (state / 'published.jsonl').write_bytes(b'{"status":"scheduled","version":2}\n')
        self.assertTrue(self.service.queue_state(state, now=self.now + timedelta(hours=4)))

    def test_drive_adapter_uses_official_upload_and_move_contracts(self):
        requests = []
        def handle(request):
            requests.append(request)
            route = request.url.path
            if route.endswith('/internal'):
                return httpx.Response(200, json={'code': 0, 'tenant_access_token': 'fixture-token', 'expire': 7200})
            self.assertEqual(request.headers['authorization'], 'Bearer fixture-token')
            if route.endswith('/create_folder'):
                self.assertEqual(json.loads(request.content), {'name': 'M1-Pro', 'folder_token': 'root'})
                data = {'token': 'folder'}
            elif route.endswith('/upload_all'):
                self.assertIn(b'name="parent_type"\r\n\r\nexplorer', request.content)
                self.assertIn(b'name="parent_node"\r\n\r\nfolder', request.content)
                self.assertIn(b'ARCHIVE BYTES', request.content)
                self.assertNotIn(b'name="file_token"', request.content)
                data = {'file_token': 'uploaded'}
            elif route.endswith('/move'):
                self.assertEqual(json.loads(request.content), {'type': 'folder', 'folder_token': 'new-parent'})
                data = {'task_id': 'move-task'}
            else:
                self.assertEqual(request.method, 'GET')
                self.assertTrue(route.endswith('/task_check'))
                self.assertEqual(request.url.params['task_id'], 'move-task')
                data = {'status': 'success'}
            return httpx.Response(200, json={'code': 0, 'data': data})
        with httpx.Client(transport=httpx.MockTransport(handle)) as http:
            client = mirror.DriveClient('fixture-id', 'fixture-secret', http=http)
            self.assertEqual(client.create_folder('root', 'M1-Pro'), 'folder')
            self.assertEqual(client.upload_file('folder', '01.jpg', b'ARCHIVE BYTES'), 'uploaded')
            self.assertEqual(client.move_folder('folder', 'new-parent'), 'move-task')
            self.assertEqual(client.task_status('move-task'), 'success')
        self.assertEqual(len(requests), 5)

    def test_move_rate_uses_dispatch_time_after_authentication(self):
        clock, requests = [100.0], []
        def handle(request):
            if request.url.path.endswith('/internal'):
                clock[0] += 2.0
                return httpx.Response(200, json={'code': 0, 'tenant_access_token': 'fixture', 'expire': 7200})
            requests.append(clock[0])
            return httpx.Response(200, json={'code': 0, 'data': {}})
        def sleep(seconds):
            clock[0] += seconds
        with patch('time.monotonic', side_effect=lambda: clock[0]), patch('time.sleep', side_effect=sleep):
            with httpx.Client(transport=httpx.MockTransport(handle)) as http:
                client = mirror.DriveClient('fixture', 'fixture-secret', http=http)
                for _ in range(21):
                    client.move_folder('folder', 'destination')
        self.assertEqual(len(requests), 21)
        self.assertGreaterEqual(requests[-1] - requests[0], 60.0)

    def test_config_and_large_evidence_are_versioned_without_hourly_repacking(self):
        state = self.root / 'state'
        state.mkdir()
        config = self.root / 'config.toml'
        config.write_bytes(b'# operator policy\n[review]\nsnooze_default_days = 3\n')
        shot = state / 'proof.png'
        shot.write_bytes(b'first screenshot')
        (state / 'published.jsonl').write_bytes(b'{"status":"scheduled"}\n')
        self.service.queue_state(state, config_path=config, now=self.now)
        self.service.dispatch(self.drive, now=self.now)
        first = list(self.drive.files)
        raw = next(content for _, name, content in first if name == 'state.zip')
        with zipfile.ZipFile(io.BytesIO(raw)) as backup:
            self.assertEqual(backup.read('config.toml'), config.read_bytes())
            self.assertNotIn('proof.png', backup.namelist())
        self.assertEqual(sum(name == 'proof.png' for _, name, _ in first), 1)
        self.service.queue_state(state, config_path=config, now=self.now + timedelta(hours=2))
        self.service.dispatch(self.drive, now=self.now + timedelta(hours=2))
        self.assertEqual(self.drive.files, first)
        shot.write_bytes(b'changed screenshot')
        self.service.queue_state(state, config_path=config, now=self.now + timedelta(hours=4))
        self.service.dispatch(self.drive, now=self.now + timedelta(hours=4))
        self.assertEqual(sum(name == 'proof.png' for _, name, _ in self.drive.files), 2)

    def test_large_files_use_prepare_parts_and_finish_without_overwrite_token(self):
        blocks = []
        def handle(request):
            if request.url.path.endswith('/internal'):
                return httpx.Response(200, json={'code': 0, 'tenant_access_token': 'token', 'expire': 7200})
            if request.url.path.endswith('/upload_prepare'):
                self.assertEqual(json.loads(request.content)['size'], 7)
                data = {'upload_id': 'transaction', 'block_size': 4, 'block_num': 2}
            elif request.url.path.endswith('/upload_part'):
                blocks.append(request.content)
                data = {}
            else:
                self.assertTrue(request.url.path.endswith('/upload_finish'))
                self.assertEqual(json.loads(request.content), {'upload_id': 'transaction', 'block_num': 2})
                data = {'file_token': 'large-file'}
            return httpx.Response(200, json={'code': 0, 'data': data})
        with httpx.Client(transport=httpx.MockTransport(handle)) as http:
            client = mirror.DriveClient('id', 'secret', http=http)
            with patch.object(client, 'SMALL_FILE_LIMIT', 4):
                self.assertEqual(client.upload_file('folder', 'state.zip', b'ABCDEFG'), 'large-file')
        self.assertEqual(len(blocks), 2)
        self.assertIn(b'ABCD', blocks[0])
        self.assertIn(b'EFG', blocks[1])
        self.assertNotIn(b'name="file_token"', b''.join(blocks))

    def test_async_tag_move_and_disabled_preview_do_not_claim_completion(self):
        row = self.arc.rows()[0]
        self.service.queue_source(self.arc.base, row, now=self.now)
        self.service.dispatch(self.drive, now=self.now)
        store.update_post_tags(self.arc.base, row, ['S1 Pro'])
        self.service.queue_source(self.arc.base, row, now=self.now)
        with patch.object(self.drive, 'move_folder', return_value='task') as move:
            first = self.service.dispatch(self.drive, now=self.now)
            self.assertGreater(first['pending'], 0)
            with patch.object(self.drive, 'task_status', return_value='pending'):
                self.assertGreater(self.service.dispatch(self.drive, now=self.now + timedelta(minutes=16))['pending'], 0)
            self.assertEqual(self.service.dispatch(self.drive, now=self.now + timedelta(minutes=32))['pending'], 0)
            move.assert_called_once()
        disabled = mirror.MirrorService(self.root / 'untouched', mirror.MirrorSettings(False, ''))
        self.assertEqual(disabled.snapshot()['snapshots'], {})
        self.assertEqual(disabled.dispatch(self.drive, now=self.now)['completed'], 0)
        self.assertFalse((self.root / 'untouched').exists())

    def test_failed_move_task_has_resolvable_blocked_operation(self):
        row = self.arc.rows()[0]
        self.service.queue_source(self.arc.base, row, now=self.now)
        self.service.dispatch(self.drive, now=self.now)
        store.update_post_tags(self.arc.base, row, ['S1 Pro'])
        self.service.queue_source(self.arc.base, row, now=self.now)
        with patch.object(self.drive, 'move_folder', return_value='task') as move:
            self.service.dispatch(self.drive, now=self.now)
            with patch.object(self.drive, 'task_status', return_value='failed'):
                self.service.dispatch(self.drive, now=self.now + timedelta(minutes=16))
            self.service.dispatch(self.drive, now=self.now + timedelta(days=1))
            move.assert_called_once()
        self.assertEqual(self.service.status()['status'], 'blocked')
        self.assertTrue(any(op['kind'] == 'move' and op['status'] == 'blocked'
                            for op in self.service.status()['operations']))

    def test_legacy_move_task_failure_remains_resolvable_without_reissuing_move(self):
        row = self.arc.rows()[0]
        self.service.queue_source(self.arc.base, row, now=self.now)
        self.service.dispatch(self.drive, now=self.now)
        store.update_post_tags(self.arc.base, row, ['S1 Pro'])
        self.service.queue_source(self.arc.base, row, now=self.now)
        with patch.object(self.drive, 'move_folder', return_value='legacy-task'):
            self.service.dispatch(self.drive, now=self.now)
        raw = self.service.snapshot()
        raw['schema_version'] = 1
        raw.pop('operations')
        for post in raw['posts'].values():
            post.pop('status', None)
        self.service.path.write_text(json.dumps(raw), encoding='utf-8', newline='')
        with patch.object(self.drive, 'task_status', return_value='pending'):
            self.service.dispatch(self.drive, now=self.now + timedelta(minutes=16))
        self.assertEqual(self.drive.moves, [])
        with patch.object(self.drive, 'task_status', return_value='failed'):
            self.service.dispatch(self.drive, now=self.now + timedelta(minutes=32))
        status = self.service.status()
        self.assertEqual(status['status'], 'blocked')
        operations = [op for op in status['operations'] if op['kind'] == 'move' and op['status'] == 'blocked']
        self.assertEqual(len(operations), 1)
        self.service.resolve(operations[0]['id'], not_created=True, note='old task explicitly failed', now=self.now)
        self.assertEqual(self.drive.moves, [])
        self.service.dispatch(self.drive, now=self.now)
        self.assertEqual(len(self.drive.moves), 1)
        self.assertEqual(self.service.status()['status'], 'completed')

    def test_first_mirror_preserves_original_history_and_all_old_media(self):
        old = store.Post('history', 'instagram', 'neakasa.global', 'Old original',
                          '2026-09-10T10:00:00Z', media=[store.Media('https://cdn.invalid/old.jpg', 'image')])
        old_path = self.arc.media_path(old, 0, 'image/jpeg')
        old_path.write_bytes(b'OLD ORIGINAL')
        old.media[0].local_path = old_path.relative_to(self.arc.base).as_posix()
        self.arc.append(old)
        new = store.Post('history', 'instagram', 'neakasa.global', 'New original',
                          old.created_at, media=[store.Media('https://cdn.invalid/new.jpg', 'image')])
        new_path = self.arc.media_path(new, 0, 'image/jpeg')
        new_path.write_bytes(b'NEW ORIGINAL')
        new.media[0].local_path = new_path.relative_to(self.arc.base).as_posix()
        self.arc.append(new)
        store.update_post_tags(self.arc.base, new.to_row(), ['Moved tag'])
        self.service.queue_source(self.arc.base, new.to_row(), now=self.now)
        self.service.dispatch(self.drive, now=self.now)
        self.assertIn(b'OLD ORIGINAL', [item[2] for item in self.drive.files])
        self.assertIn(b'NEW ORIGINAL', [item[2] for item in self.drive.files])
        history = next(content for _, name, content in self.drive.files if name == 'source_history.jsonl')
        self.assertEqual(json.loads(history.splitlines()[0])['source']['text'], 'Old original')

    def test_cli_preview_does_not_create_state_or_construct_client(self):
        from core import config
        from tools import mirror as command
        configured = config.Config()
        configured._d['paths'] = {'archive': str(self.root / 'archive'), 'state': str(self.root / 'preview-state')}
        configured._d['mirror'] = {'enabled': False, 'root_folder_token': '', 'mirror_state': True}
        before = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        with patch.object(config, '_cfg', configured), patch.object(command.DriveClient, 'from_environment') as client:
            self.assertEqual(command.main([]), 0)
            client.assert_not_called()
        self.assertFalse((self.root / 'preview-state').exists())
        self.assertEqual(before, {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob('*') if path.is_file()})

    def test_cli_preflight_explains_missing_credentials_without_values(self):
        from core import config
        from tools import mirror as command
        configured = config.Config()
        configured._d['paths'] = {'archive': str(self.root / 'archive'), 'state': str(self.root / 'preflight-state')}
        configured._d['mirror'] = {'enabled': False, 'root_folder_token': 'fixture-root', 'mirror_state': True}
        output = io.StringIO()
        with patch.object(config, '_cfg', configured), redirect_stdout(output), \
                patch('core.feishu.ModelCredentials.optional_value', return_value=''):
            self.assertEqual(command.main(['preflight']), 1)
        result = json.loads(output.getvalue())
        self.assertIn('last_error', result)
        self.assertIn('凭据', result['last_error'])
        self.assertFalse((self.root / 'preflight-state').exists())

    def test_state_backup_rejects_partial_or_concurrently_appended_ledgers(self):
        state = self.root / 'state'
        state.mkdir()
        ledger = state / 'published.jsonl'
        ledger.write_bytes(b'{"status":"scheduled"}\n')
        self.service.queue_state(state, now=self.now)
        baseline = self.service.snapshot()
        ledger.write_bytes(b'{"status":"scheduled"}\n{"status":')
        with self.assertRaises(mirror.MirrorError):
            self.service.queue_state(state, now=self.now + timedelta(hours=2))
        self.assertEqual(self.service.snapshot(), baseline)
        ledger.write_bytes(b'{"status":"scheduled"}\n')
        original_read = Path.read_bytes

        def append_during_read(path):
            content = original_read(path)
            if path == ledger:
                path.write_bytes(content + b'{"status":"prepared"}\n')
            return content

        with patch.object(Path, 'read_bytes', append_during_read):
            with self.assertRaises(mirror.MirrorError):
                self.service.queue_state(state, now=self.now + timedelta(hours=2))
        self.assertEqual(self.service.snapshot(), baseline)


if __name__ == '__main__':
    unittest.main()
