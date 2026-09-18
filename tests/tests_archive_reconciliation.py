"""历史 IG 完整性自动核验：临时归档、本地 capture、禁止外部访问。"""
import contextlib
import copy
import hashlib
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import archive_integrity, config, index_db, integrity, parse, store
from core.capture_state import CaptureState
from core.monitor_access import AccessController
from image_fixtures import image_bytes
from routes import delta, reconcile
from web.api.query_index import refresh_display_index


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.c = config.Config()
        self.c._d['paths'] = {'archive': str(self.root / 'archive'), 'state': str(self.root / 'state')}
        self.account = self.c['targets']['instagram']
        self.arc = store.Archive(self.c.archive_dir, 'in_' + self.account)
        self.nodes = []
        self.truths = {}
        self.kept = {}
        for i, kind in enumerate(('image', 'video', 'image', 'video', 'image', 'video', 'video')):
            node = {'pk': str(3978079688277982861 + i), 'code': 'Historical' + str(i),
                    'taken_at': 1788408075 + i, 'media_type': 1 if kind == 'image' else 2,
                    'carousel_media': None, 'carousel_media_count': None,
                    'user': {'username': self.account}, 'caption': {'text': 'Historical fixture.'}}
            if kind == 'image':
                node['image_versions2'] = {'candidates': [{'url': f'https://cdn.invalid/{i}.jpg'}]}
            else:
                node['video_versions'] = [{'url': f'https://cdn.invalid/{i}.mp4'}]
            self.nodes.append(node)
            post = parse.from_iphone_struct(node, self.account, 'backfill')
            post.source_media_complete = post.media_complete = False
            post.source_media_count = None
            self.arc.append(post)
            directory = self.arc.post_dir(post)
            source = json.loads((directory / 'post.json').read_text('utf-8'))
            if kind == 'image':
                picture = directory / '01.jpg'
                raw = image_bytes('JPEG')
                picture.write_bytes(raw)
                source['media'][0].update(local_path=picture.relative_to(self.arc.base).as_posix(),
                    sha256=hashlib.sha256(raw).hexdigest(), byte_size=len(raw))
                self.kept[picture] = raw
            source['preserved_field'] = {'manual': 'keep'}
            truth = directory / 'post.json'
            truth.write_text(json.dumps(source), encoding='utf-8', newline='')
            self.truths[node['pk']] = truth
        self.arc.reindex()
        self.capture = self.arc.base / '_capture_history.json'
        self.write_capture(self.nodes)
        CaptureState(self.c.state_dir).initialize(self.c.archive_dir, self.c['targets'], 'fixture')
        # Nothing is due: exercising the real command must repair locally without visiting a platform.
        AccessController(self.c.state_dir, clock=lambda: datetime.now(timezone.utc) + timedelta(days=1)).initialize('fixture')
        for name in ('capture_state.json', 'monitor_access.json'):
            path = self.c.state_dir / name
            if path.exists():
                self.kept[path] = path.read_bytes()
        for name in ('published.jsonl', 'paid_requests.jsonl', 'review_items.jsonl', 'translated_human.jsonl'):
            path = self.c.state_dir / name
            path.write_bytes(b'')
            self.kept[path] = b''
        self.addCleanup(patch.stopall)
        patch.object(config, '_cfg', self.c).start()
        patch.object(delta, 'cdp_ready', side_effect=AssertionError('No browser')).start()
        patch('httpx.Client.send', side_effect=AssertionError('No network')).start()

    def write_capture(self, nodes, path=None):
        (path or self.capture).write_text(json.dumps([{'items': nodes}]), encoding='utf-8', newline='')

    def run_command(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = delta.main(list(args))
        return code, output.getvalue()

    def test_normal_monitor_command_repairs_all_seven_before_any_platform_visit(self):
        before = {key: path.read_bytes() for key, path in self.truths.items()}
        for history in (False, True):
            self.assertFalse(refresh_display_index(history=history)['stale'])
        code, output = self.run_command('--platform', 'instagram', '--no-jitter')
        self.assertEqual(code, 0, output)
        rows = store.Archive(self.c.archive_dir, self.arc.base.name).rows()
        self.assertEqual(sum(row['media_complete'] for row in rows), 7)
        self.assertIn('历史归档', output)
        for key, truth in self.truths.items():
            source = json.loads(truth.read_text('utf-8'))
            self.assertEqual(source, dict(json.loads(before[key]), source_media_complete=True,
                                         source_media_count=1, media_complete=True))
            backups = list(truth.parent.glob('post.json.before-completeness-*.bak'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), before[key])
        for path, raw in self.kept.items():
            self.assertEqual(path.read_bytes(), raw)
        for history, name in ((False, 'index.sqlite'), (True, 'index-history.sqlite')):
            # 展示入口按真相签名刷新；监测引擎不依赖 SQLite。
            self.assertFalse(refresh_display_index(history=history)['stale'])
            self.assertTrue(index_db.check_consistency(self.c.archive_dir, self.c.state_dir / name,
                state_dir=self.c.state_dir, include_frozen=history)['consistent'])
        after = {path: path.read_bytes() for path in self.truths.values()}
        manifest = self.arc.manifest.read_bytes()
        self.assertEqual(self.run_command('--platform', 'instagram')[0], 0)
        self.assertEqual(self.arc.manifest.read_bytes(), manifest)
        self.assertEqual(after, {path: path.read_bytes() for path in after})

    def test_status_explains_historical_source_gaps_without_writing(self):
        before = {path: path.read_bytes() for path in self.truths.values()}
        code, output = self.run_command('--status', '--platform', 'instagram')
        self.assertEqual(code, 0, output)
        self.assertIn('"archive_incomplete": 7', output)
        self.assertIn('"source_unconfirmed": 7', output)
        self.assertIn('"images_unavailable": 0', output)
        self.assertIn('"metadata_only_videos": 4', output)
        self.assertIn('not_in_capture_state', output)
        for key in self.truths:
            self.assertIn(key, output)
        self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_missing_conflicting_and_corrupt_evidence_never_mark_complete(self):
        omitted, conflict, corrupt = (self.nodes[i]['pk'] for i in (0, 1, 2))
        self.write_capture(self.nodes[1:])
        bad = copy.deepcopy(self.nodes[1])
        bad['media_type'] = 8
        self.write_capture([bad], self.arc.base / '_capture_conflict.json')
        image_path = next(path for path in self.kept if path.parent == self.truths[corrupt].parent)
        image_path.write_bytes(b'broken image')
        before = {key: self.truths[key].read_bytes() for key in (omitted, conflict, corrupt)}
        code, output = self.run_command('--platform', 'instagram')
        self.assertEqual(code, 0, output)
        rows = store.Archive(self.c.archive_dir, self.arc.base.name).rows()
        self.assertEqual(sum(row['media_complete'] for row in rows), 4)
        for key, raw in before.items():
            self.assertEqual(self.truths[key].read_bytes(), raw)
            self.assertIn(key, output)

    def test_incomplete_warning_distinguishes_first_observation_and_saved_comparison(self):
        rows = self.arc.rows()
        entry = {}
        first = integrity.run_checks(rows, rows, entry, 'instagram', gap_days=5, alert_after=4,
                                     now=datetime.now(timezone.utc))
        message = next(row['message'] for row in first if row['kind'] == 'incomplete')
        self.assertIn('首次检查', message)
        self.assertNotIn('从 0', message)
        self.assertNotIn('图片没下全', message)
        self.assertNotIn('下次抓取会自动重试', message)

    def test_interrupted_manifest_is_recovered_even_after_capture_retention(self):
        original = archive_integrity.paid_model.append_jsonl
        failed = False
        def interrupt(path, row, **kwargs):
            nonlocal failed
            if Path(path) == self.arc.manifest and not failed:
                failed = True
                raise OSError('fixture write interruption')
            return original(path, row, **kwargs)
        with patch.object(archive_integrity.paid_model, 'append_jsonl', side_effect=interrupt):
            self.assertEqual(self.run_command('--platform', 'instagram')[0], 0)
        self.assertEqual(len(store.Archive(self.c.archive_dir, self.arc.base.name).needs_media()), 1)
        first = self.truths[self.nodes[0]['pk']]
        self.assertTrue(json.loads(first.read_text('utf-8'))['media_complete'])
        self.capture.unlink()
        self.assertEqual(self.run_command('--platform', 'instagram')[0], 0)
        self.assertEqual(store.Archive(self.c.archive_dir, self.arc.base.name).needs_media(), [])
        self.assertEqual(len(list(first.parent.glob('post.json.before-completeness-*.bak'))), 1)

    def test_dry_run_keeps_history_and_morning_command_uses_same_local_repair(self):
        before = {path: path.read_bytes() for path in self.truths.values()}
        self.assertEqual(self.run_command('--platform', 'instagram', '--dry-run')[0], 0)
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(reconcile.main(['--platform', 'instagram']), 0)
        self.assertEqual(store.Archive(self.c.archive_dir, self.arc.base.name).needs_media(), [])

    def test_unreadable_capture_does_not_hide_conflicting_evidence(self):
        (self.arc.base / '_capture_broken.json').write_bytes(b'{truncated')
        before = {path: path.read_bytes() for path in self.truths.values()}
        code, output = self.run_command('--platform', 'instagram')
        self.assertEqual(code, 0, output)
        self.assertIn('_capture_broken.json', output)
        self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_warning_compares_to_saved_count_and_reduction_does_not_imply_recovery_event(self):
        entry = {'alerts': {'incomplete': 3}}
        rows = self.arc.rows()
        findings = integrity.run_checks(rows, rows, entry, 'instagram', gap_days=5, alert_after=4,
                                        now=datetime.now(timezone.utc))
        message = next(row['message'] for row in findings if row['kind'] == 'incomplete')
        self.assertIn('上次记录 3', message)
        self.assertIn('非本轮前后差值', message)
        self.assertFalse(integrity.run_checks(rows, [], entry, 'instagram', gap_days=5, alert_after=4,
                                              now=datetime.now(timezone.utc)))
        self.assertEqual(entry['alerts']['incomplete'], 0)


if __name__ == '__main__':
    unittest.main()
