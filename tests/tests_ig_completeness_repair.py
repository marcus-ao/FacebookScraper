"""单图完整性修复只使用临时归档、合成图片及本地 capture。"""
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import config, index_db, parse, store
from core import review, translated
from pipeline import refinement
from image_fixtures import image_bytes
from tools import repair_ig_completeness as repair


class RepairTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.c = config.Config()
        self.c._d['paths'] = {'archive': str(self.root / 'archive'), 'state': str(self.root / 'state')}
        self.account = 'neakasa.global'
        self.arc = store.Archive(self.c.archive_dir, 'in_' + self.account)
        self.node = {'pk': '3984612646028833441', 'code': 'SingleFixture', 'taken_at': 1789251660,
                     'media_type': 1, '__typename': 'XDTMediaDict', 'product_type': 'feed',
                     'carousel_media': None, 'carousel_media_count': None,
                     'user': {'username': self.account}, 'caption': {'text': 'Cats need water.'},
                     'image_versions2': {'candidates': [{'url': 'https://cdn.invalid/one.jpg'}]}}
        post = parse.from_iphone_struct(self.node, self.account, 'backfill')
        post.media_complete = post.source_media_complete = False
        post.source_media_count = None
        self.arc.append(post)
        self.directory = self.arc.post_dir(post)
        self.picture = self.directory / '01.jpg'
        self.picture.write_bytes(image_bytes('JPEG'))
        self.source = json.loads((self.directory / 'post.json').read_text('utf-8'))
        self.source['media'][0].update(local_path=self.picture.relative_to(self.arc.base).as_posix(),
                                      sha256=hashlib.sha256(self.picture.read_bytes()).hexdigest(),
                                      byte_size=self.picture.stat().st_size)
        self.source['preserved_unknown_field'] = {'value': 'keep'}
        self.write_source()
        self.arc.reindex()
        self.capture = self.arc.base / '_capture_fixture.json'
        self.write_capture()
        self.kept = {}
        for name in ('translated_human.jsonl', 'review_items.jsonl', 'paid_requests.jsonl', 'published.jsonl'):
            parent = self.c.state_dir if name in ('paid_requests.jsonl', 'published.jsonl') else self.arc.base
            parent.mkdir(parents=True, exist_ok=True)
            path = parent / name
            path.write_bytes(b'unchanged fixture bytes\n')
            self.kept[path] = path.read_bytes()
        self.kept[self.picture] = self.picture.read_bytes()
        other = parse.from_iphone_struct(dict(self.node, pk='3984612646028833442', code='OtherFixture'),
                                        self.account, 'backfill')
        self.arc.append(other)
        other_truth = self.arc.post_dir(other) / 'post.json'
        self.kept[other_truth] = other_truth.read_bytes()
        self.addCleanup(patch.stopall)
        patch('httpx.Client.send', side_effect=AssertionError('No network')).start()

    def write_source(self):
        (self.directory / 'post.json').write_text(json.dumps(self.source), encoding='utf-8', newline='')

    def write_capture(self):
        self.capture.write_text(json.dumps([{'items': [self.node]}]), encoding='utf-8', newline='')

    def run_repair(self, apply=False):
        return repair.repair(self.arc.base, self.node['pk'], self.capture, apply=apply)

    def test_preview_apply_preserves_content_backup_and_repeated_run(self):
        before = (self.directory / 'post.json').read_bytes()
        manifest = self.arc.manifest.read_bytes()
        preview = self.run_repair()
        self.assertTrue(preview['changed'])
        self.assertEqual((self.directory / 'post.json').read_bytes(), before)
        self.assertEqual(self.arc.manifest.read_bytes(), manifest)
        result = self.run_repair(apply=True)
        self.assertEqual(Path(result['backup']).read_bytes(), before)
        after = json.loads((self.directory / 'post.json').read_text('utf-8'))
        self.assertEqual(after, dict(self.source, source_media_complete=True, media_complete=True, source_media_count=1))
        self.assertEqual(store.Archive(self.c.archive_dir, self.arc.base.name).rows()[0], after)
        for path, raw in self.kept.items():
            self.assertEqual(path.read_bytes(), raw)
        manifest = self.arc.manifest.read_bytes()
        self.assertFalse(self.run_repair(apply=True)['changed'])
        self.assertEqual(self.arc.manifest.read_bytes(), manifest)

    def test_contradictory_capture_and_identity_mismatch_do_not_write(self):
        original = copy.deepcopy(self.node)
        for changes in ({'media_type': 8}, {'media_type': None},
                        {'carousel_media': [{'image_versions2': original['image_versions2']}]},
                        {'carousel_media_count': 2}, {'user': {'username': 'different'}},
                        {'code': 'OtherPost'},
                        {'image_versions2': {'candidates': [{'url': 'https://cdn.invalid/other.jpg'}]}}):
            with self.subTest(changes=changes):
                self.node = dict(original, **changes)
                self.write_capture()
                before = (self.directory / 'post.json').read_bytes()
                with self.assertRaises(ValueError):
                    self.run_repair(apply=True)
                self.assertEqual((self.directory / 'post.json').read_bytes(), before)
        self.assertEqual(list(self.directory.glob('post.json.before-completeness-*')), [])

    def test_missing_or_corrupt_original_is_not_marked_complete(self):
        before = (self.directory / 'post.json').read_bytes()
        for raw in (b'', b'not an image'):
            self.picture.write_bytes(raw)
            with self.assertRaisesRegex(ValueError, '原图'):
                self.run_repair(apply=True)
        self.picture.unlink()
        with self.assertRaisesRegex(ValueError, '原图'):
            self.run_repair(apply=True)
        self.assertEqual((self.directory / 'post.json').read_bytes(), before)

    def test_interrupted_manifest_write_can_be_repaired_without_new_truth_change(self):
        original_append = repair.paid_model.append_jsonl
        def interrupted(path, *args, **kwargs):
            if Path(path) == self.arc.manifest:
                raise OSError('simulated manifest failure')
            return original_append(path, *args, **kwargs)
        with patch.object(repair.paid_model, 'append_jsonl', side_effect=interrupted):
            with self.assertRaises(OSError):
                self.run_repair(apply=True)
        self.run_repair(apply=True)
        self.assertTrue(store.Archive(self.c.archive_dir, self.arc.base.name).rows()[0]['media_complete'])
        self.assertEqual(len(list(self.directory.glob('post.json.before-completeness-*.bak'))), 1)

    def test_cli_rebuilds_indexes_and_removes_only_material_rejection(self):
        for path in self.kept:
            if path.suffix == '.jsonl':
                path.write_bytes(b'')
        with patch.object(config, '_cfg', self.c):
            kwargs = {'source_hash': translated.source_text_sha256(self.source['text'])}
            with self.assertRaisesRegex(review.ReviewConflict, '完整性硬闸'):
                refinement._eligible(self.arc.base, self.source, **kwargs)
            args = ['--post-id', self.node['pk'], '--capture', self.capture.name]
            self.assertEqual(repair.main(args), 0)
            self.assertFalse((self.c.state_dir / 'index.sqlite').exists())
            self.assertEqual(repair.main([*args, '--apply']), 0)
            source, _ = refinement._eligible(self.arc.base, self.source, **kwargs)
            self.assertTrue(source['media_complete'])
            for history, name in ((False, 'index.sqlite'), (True, 'index-history.sqlite')):
                database = self.c.state_dir / name
                self.assertTrue(index_db.check_consistency(self.c.archive_dir, database,
                                state_dir=self.c.state_dir, include_frozen=history)['consistent'])
            self.assertEqual((self.c.state_dir / 'paid_requests.jsonl').read_bytes(), b'')


if __name__ == '__main__':
    unittest.main()
