"""单向云盘镜像：全部用临时源文件与假 Drive，绝不上传真实数据。"""
from __future__ import annotations

import json
import io
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

import httpx
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import mirror, store  # noqa: E402


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
            raise RuntimeError('offline failure')
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

    def test_tags_move_existing_folder_without_reuploading_versions(self):
        row = self.arc.rows()[0]
        self.service.queue_source(self.arc.base, row, now=self.now)
        self.service.dispatch(self.drive, now=self.now)
        count = len(self.drive.files)
        store.update_post_tags(self.arc.base, row, ['S1 Pro'])
        self.assertFalse(self.service.queue_source(self.arc.base, row, now=self.now))
        self.service.dispatch(self.drive, now=self.now)
        self.assertEqual(len(self.drive.moves), 1)
        self.assertEqual(len(self.drive.files), count)
        self.assertIn('S1-Pro', [item[1] for item in self.drive.folders])

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
