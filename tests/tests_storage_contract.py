"""原帖归档契约：北京日期、产品别名、完整图片与移动恢复。"""
import asyncio
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import capture, config, store
from core.media import image_facts


def png(color='red'):
    stream = io.BytesIO()
    Image.new('RGB', (4, 3), color).save(stream, format='PNG')
    return stream.getvalue()


class StorageContractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        configured = config.Config()
        configured._d['paths'] = {'archive': str(self.root / 'archive'), 'state': str(self.root / 'state')}
        configured._d['storage'] = {'product_aliases': {'P1 Pro': ['P1Pro', 'NeakasaP1Pro'],
                                                        'S1 Pro': ['S1Pro', 'NeakasaS1Pro']}}
        active = patch.object(config, '_cfg', configured)
        active.start()
        self.addCleanup(active.stop)
        self.arc = store.Archive(self.root / 'archive', 'in_neakasa.global')

    def post(self, text='P1 Pro launch', url=None):
        return store.Post('123', 'instagram', 'neakasa.global', text, '2026-08-31T20:00:00Z',
                          media=[store.Media(url, 'image')] if url else [])

    def save_image(self, post, color='red'):
        path = self.arc.media_path(post, 0, 'image/png')
        path.write_bytes(png(color))
        post.media[0].local_path = path.relative_to(self.arc.base).as_posix()
        self.arc.append(post)
        return path

    def test_new_folder_uses_beijing_date_and_keeps_original_timestamp(self):
        post = self.post()
        self.arc.append(post)
        row = self.arc.rows()[0]
        self.assertTrue(row['folder_name'].startswith('2026-09-01_0400_'))
        self.assertEqual(row['created_at'], '2026-08-31T20:00:00Z')
        self.assertEqual(store.archive_month(row), '2026-09')
        self.assertTrue(row['archived_at'])
        changed = self.post('Updated caption')
        self.arc.append(changed)
        self.assertEqual(self.arc.rows()[0]['archived_at'], row['archived_at'])
        self.assertEqual(self.arc.rows()[0]['folder_name'], row['folder_name'])

    def test_hashtag_aliases_take_priority_without_changing_source_text(self):
        post = self.post('P1 Pro launch #NeakasaS1Pro #P1Pro #P1ProSale')
        self.arc.append(post)
        row = self.arc.rows()[0]
        self.assertEqual(row['tags'], ['S1 Pro', 'P1 Pro'])
        self.assertEqual(row['tags_origin'], 'auto')
        self.assertEqual(row['text'], 'P1 Pro launch #NeakasaS1Pro #P1Pro #P1ProSale')
        manual = store.update_post_tags(self.arc.base, row, [])
        self.assertEqual(manual['tags_origin'], 'manual')
        self.arc.append(self.post('#P1Pro source edit'))
        self.assertEqual(self.arc.rows()[0]['tags'], [])

    def test_history_resolves_after_tag_move_without_rewriting_old_evidence(self):
        old = self.post(url='https://cdn.invalid/old.png')
        self.save_image(old)
        changed = self.post('Source edit', 'https://cdn.invalid/new.png')
        self.save_image(changed, 'blue')
        history_path = self.arc.post_dir(changed) / 'source_history.jsonl'
        history_bytes = history_path.read_bytes()
        old_source = json.loads(history_bytes.splitlines()[0])['source']
        moved = store.update_post_tags(self.arc.base, self.arc.rows()[0], ['S1 Pro'])
        directory = store.post_directory(self.arc.base, moved)
        self.assertEqual((directory / 'source_history.jsonl').read_bytes(), history_bytes)
        resolved = store.resolve_media_path(self.arc.base, old_source, old_source['media'][0])
        self.assertEqual(resolved.parent, directory)
        self.assertEqual(resolved.read_bytes(), png('red'))
        escaped = dict(old_source['media'][0], local_path='../elsewhere/01.png')
        with self.assertRaises(store.ArchivePathError):
            store.resolve_media_path(self.arc.base, old_source, escaped)

    def test_media_observation_distinguishes_missing_corrupt_and_metadata_only(self):
        post = self.post(url='https://cdn.invalid/a.png')
        path = self.save_image(post)
        row = self.arc.rows()[0]
        info = store.media_storage_info(self.arc.base, row)[0]
        self.assertEqual((info['storage_status'], info['width'], info['height']), ('saved', 4, 3))
        self.assertEqual(info['sha256'], hashlib.sha256(png()).hexdigest())
        path.write_bytes(png()[:20])
        self.assertEqual(store.media_storage_info(self.arc.base, row)[0]['storage_status'], 'corrupt')
        path.unlink()
        self.assertEqual(store.media_storage_info(self.arc.base, row)[0]['storage_status'], 'missing')
        video = dict(row, media=[{'kind': 'video', 'url': 'https://cdn.invalid/v.mp4'}])
        self.assertEqual(store.media_storage_info(self.arc.base, video)[0]['storage_status'], 'metadata_only')

    def test_interrupted_tag_move_recovers_originals_and_image_ownership(self):
        post = self.post(url='https://cdn.invalid/a.png')
        original = self.save_image(post)
        directory = original.parent
        localized = directory / 'media_de' / '01.png'
        localized.parent.mkdir()
        localized.write_bytes(png('blue'))
        ledger = self.arc.base / 'images_de.jsonl'
        record = {'post_id': '123', 'source_rel': original.relative_to(self.arc.base).as_posix(),
                  'out_path': localized.relative_to(self.arc.base).as_posix(), 'status': 'ok'}
        ledger.write_text(json.dumps(record) + '\n', encoding='utf-8')
        real_write = store._atomic_write_text
        def fail_source(path, text, **kwargs):
            if path.name == 'post.json':
                raise OSError('simulated interruption after move')
            return real_write(path, text, **kwargs)
        with patch.object(store, '_atomic_write_text', side_effect=fail_source):
            with self.assertRaises(OSError):
                store.update_post_tags(self.arc.base, self.arc.rows()[0], ['S1 Pro'])
        self.assertEqual(store.recover_tag_moves(self.arc.base), 1)
        self.assertEqual(store.recover_tag_moves(self.arc.base), 0)
        row = store.Archive(self.arc.root, self.arc.base.name).rows()[0]
        target = store.post_directory(self.arc.base, row)
        self.assertEqual(row['tags'], ['S1 Pro'])
        self.assertEqual((target / '01.png').read_bytes(), png())
        self.assertEqual((target / 'media_de' / '01.png').read_bytes(), png('blue'))
        entries = [json.loads(line) for line in ledger.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(entries[0], record)
        self.assertEqual(entries[-1]['out_path'], (target / 'media_de' / '01.png').relative_to(self.arc.base).as_posix())

    def test_download_rejects_truncated_image_and_records_valid_image_facts(self):
        class Response:
            ok = True
            headers = {'content-type': 'image/png'}
            async def body(self):
                return png()[:20] if self.truncated else png()
        class Context:
            def __init__(self):
                self.request = self
                self.truncated = True
            async def get(self, *_args, **_kwargs):
                response = Response()
                response.truncated = self.truncated
                return response
        ctx = Context()
        post = self.post(url='https://cdn.invalid/a.png')
        asyncio.run(capture.download_media(ctx, self.arc, post, 'https://instagram.com/'))
        self.assertFalse(post.media_complete)
        self.assertIsNone(post.media[0].local_path)
        ctx.truncated = False
        post = self.post(url='https://cdn.invalid/a.png')
        asyncio.run(capture.download_media(ctx, self.arc, post, 'https://instagram.com/'))
        self.assertTrue(post.media_complete)
        self.assertEqual(post.media[0].sha256, hashlib.sha256(png()).hexdigest())
        self.assertEqual(post.media[0].byte_size, len(png()))
        self.assertEqual(post.media[0].content_type, 'image/png')

    def test_invalid_or_naive_dates_are_undated_and_aliases_are_unambiguous(self):
        for value in ('', None, 'broken', '2026-09-01', '2026-09-01T04:00:00'):
            with self.subTest(value=value):
                post = self.post()
                post.created_at = value
                self.assertTrue(store.planned_post_directory(self.arc.base, post).name.startswith('undated_'))
        self.assertEqual(store.infer_tags('M10 XM1 #NeakasaM1unknown'), [])
        config._cfg._d['storage']['product_aliases']['S1 Pro'].append('P1Pro')
        with self.assertRaises(ValueError):
            store.infer_tags('#P1Pro')

    def test_failed_source_revision_remains_partial_and_retains_previous_bytes(self):
        original = self.post(url='https://cdn.invalid/old.png')
        old = self.save_image(original)
        class Context:
            request = None
            async def get(self, *_args, **_kwargs):
                raise OSError('offline')
        context = Context()
        context.request = context
        changed = self.post(url='https://cdn.invalid/revised.png')
        self.assertTrue(self.arc.should_append(changed))
        asyncio.run(capture.download_media(context, self.arc, changed, 'https://instagram.com/'))
        self.assertTrue(self.arc.append(changed))
        row = self.arc.rows()[0]
        self.assertFalse(row['media_complete'])
        self.assertEqual(row['media'][0]['url'], changed.media[0].url)
        self.assertIsNone(row['media'][0]['local_path'])
        self.assertEqual(old.read_bytes(), png())

    def test_changed_bytes_at_the_same_url_preserve_original_and_create_revision(self):
        original = self.post(url='https://cdn.invalid/same.png')
        old = self.arc.save_media(original, 0, png(), image_facts(png()))
        self.arc.append(original)
        changed = self.post(url=original.media[0].url)
        new = self.arc.save_media(changed, 0, png('blue'), image_facts(png('blue')))
        self.assertNotEqual(old, new)
        self.assertTrue(self.arc.append(changed))
        self.assertEqual(old.read_bytes(), png())
        self.assertEqual(new.read_bytes(), png('blue'))
        self.assertEqual(store.media_storage_info(self.arc.base, self.arc.rows()[0])[0]['sha256'],
                         hashlib.sha256(png('blue')).hexdigest())

    def test_download_rechecks_directory_after_concurrent_classification(self):
        initial = self.post(url='https://cdn.invalid/a.png')
        self.save_image(initial)
        fresh = self.post(url='https://cdn.invalid/a.png')
        fresh.media.append(store.Media('https://cdn.invalid/b.png', 'image'))
        test = self
        class Context:
            request = None
            async def get(self, *_args, **_kwargs):
                store.update_post_tags(test.arc.base, test.arc.rows()[0], ['S1 Pro'])
                class Response:
                    ok = True
                    headers = {'content-type': 'image/png'}
                    async def body(self):
                        return png('blue')
                return Response()
        context = Context()
        context.request = context
        asyncio.run(capture.download_media(context, self.arc, fresh, 'https://instagram.com/'))
        self.arc.append(fresh)
        row = self.arc.rows()[0]
        directory = store.post_directory(self.arc.base, row)
        self.assertEqual(directory.parent.name, 'S1-Pro')
        self.assertEqual([store.resolve_media_path(self.arc.base, row, item).parent for item in row['media']],
                         [directory, directory])
        self.assertEqual([item['storage_status'] for item in store.media_storage_info(self.arc.base, row)],
                         ['saved', 'saved'])

    def test_tag_roundtrip_keeps_latest_localized_ownership_in_current_directory(self):
        post = self.post(url='https://cdn.invalid/a.png')
        original = self.save_image(post)
        ledger = self.arc.base / 'images_de.jsonl'
        entry = {'post_id': '123', 'media_index': 0, 'source_rel': original.relative_to(self.arc.base).as_posix(),
                 'out_path': (original.parent / 'media_de/01.png').relative_to(self.arc.base).as_posix()}
        ledger.write_text(json.dumps(entry) + '\n', encoding='utf-8')
        row = self.arc.rows()[0]
        for tags in (['S1 Pro'], ['P1 Pro'], ['S1 Pro']):
            row = store.update_post_tags(self.arc.base, row, tags)
            last = json.loads(ledger.read_text(encoding='utf-8').splitlines()[-1])
            directory = store.post_directory(self.arc.base, row)
            self.assertEqual(last['out_path'], (directory / 'media_de/01.png').relative_to(self.arc.base).as_posix())

    def test_bad_media_path_does_not_hide_the_other_media_observations(self):
        post = self.post(url='https://cdn.invalid/a.png')
        self.save_image(post)
        row = self.arc.rows()[0]
        row['media'].insert(0, {'kind': 'image', 'url': 'bad', 'local_path': '../outside.png'})
        observed = store.media_storage_info(self.arc.base, row)
        self.assertEqual([item['storage_status'] for item in observed], ['corrupt', 'saved'])
        self.assertIsNone(observed[0]['sha256'])

    def test_atomic_download_interruption_leaves_original_and_other_downloads(self):
        original = self.post(url='https://cdn.invalid/a.png')
        old = self.arc.save_media(original, 0, png(), image_facts(png()))
        self.arc.append(original)
        changed = self.post(url='https://cdn.invalid/b.png')
        with patch.object(store.os, 'fsync', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                self.arc.save_media(changed, 0, png('blue'), image_facts(png('blue')))
        self.assertEqual(old.read_bytes(), png())
        self.assertIsNone(changed.media[0].local_path)
        self.assertEqual(list(old.parent.glob('.download-*')), [])

    def test_tag_recovery_rejects_tampered_plan_before_moving_truth(self):
        post = self.post()
        self.arc.append(post)
        before = self.arc.post_dir(post)
        with patch.object(store, '_complete_tag_move', side_effect=OSError('stopped before rename')):
            with self.assertRaises(OSError):
                store.update_post_tags(self.arc.base, self.arc.rows()[0], ['S1 Pro'])
        ledger = self.arc.base / 'tag_moves.jsonl'
        event = json.loads(ledger.read_text(encoding='utf-8'))
        event['row']['text'] = 'not the original'
        ledger.write_text(json.dumps(event) + '\n', encoding='utf-8')
        with self.assertRaises(store.ArchiveRevisionConflict):
            store.recover_tag_moves(self.arc.base)
        self.assertEqual(json.loads((before / 'post.json').read_text(encoding='utf-8'))['text'], post.text)

    def test_legacy_layout_migration_keeps_history_bytes_resolvable(self):
        from tools.layout import migrate
        post = self.post(url='https://cdn.invalid/a.png')
        directory = self.arc.posts_dir / store.post_dirname(post.post_id, post.created_at)
        directory.mkdir()
        post.media[0].local_path = (directory / '01.png').relative_to(self.arc.base).as_posix()
        (directory / '01.png').write_bytes(png())
        row = post.to_row()
        (directory / 'post.json').write_text(json.dumps(row), encoding='utf-8')
        history = (json.dumps({'recorded_at': '2026-09-01T00:00:00Z', 'source': row}) + '\n').encode()
        (directory / 'source_history.jsonl').write_bytes(history)
        self.assertEqual(migrate(self.arc.base, dry_run=False), 1)
        current = store.Archive(self.arc.root, self.arc.base.name).rows()[0]
        destination = store.post_directory(self.arc.base, current)
        self.assertEqual(destination.name, directory.name)
        self.assertEqual(store.archive_month(current), '2026-08')
        self.assertEqual((destination / 'source_history.jsonl').read_bytes(), history)
        self.assertEqual(store.resolve_media_path(self.arc.base, row, row['media'][0]), destination / '01.png')

    def test_reuse_cannot_bless_changed_original_bytes(self):
        original = self.post(url='https://cdn.invalid/a.png')
        path = self.arc.save_media(original, 0, png(), image_facts(png()))
        original.media_complete = False
        self.arc.append(original)
        path.write_bytes(png('blue'))
        class Context:
            request = None
            calls = 0
            async def get(self, *_args, **_kwargs):
                self.calls += 1
                raise OSError('offline')
        context = Context()
        context.request = context
        fresh = self.post(url=original.media[0].url)
        asyncio.run(capture.download_media(context, self.arc, fresh, 'https://instagram.com/'))
        self.assertEqual(context.calls, 1)
        self.assertFalse(fresh.media_complete)
        self.assertIsNone(fresh.media[0].local_path)
        self.assertFalse(self.arc.append(fresh))
        row = self.arc.rows()[0]
        self.assertEqual(row['media'][0]['sha256'], hashlib.sha256(png()).hexdigest())
        self.assertEqual(store.media_storage_info(self.arc.base, row)[0]['storage_status'], 'corrupt')

    def test_incomplete_caption_edit_keeps_previous_carousel_progress(self):
        original = self.post(url='https://cdn.invalid/a.png')
        self.arc.save_media(original, 0, png(), image_facts(png()))
        original.media.append(store.Media('https://cdn.invalid/b.png', 'image'))
        original.media_complete = False
        self.arc.append(original)
        changed = self.post('Edited caption', url=original.media[0].url)
        changed.media_complete = False
        self.assertTrue(self.arc.append(changed))
        row = self.arc.rows()[0]
        self.assertEqual(row['text'], 'Edited caption')
        self.assertEqual([item['url'] for item in row['media']], [item.url for item in original.media])
        self.assertIsNotNone(row['media'][0]['local_path'])
        self.assertFalse(row['media_complete'])

    def test_automatic_classification_follows_source_edit_with_stable_folder(self):
        original = self.post('#P1Pro launch', url='https://cdn.invalid/a.png')
        path = self.save_image(original)
        changed = self.post('#S1Pro launch', url=original.media[0].url)
        self.assertTrue(self.arc.append(changed))
        row = self.arc.rows()[0]
        directory = store.post_directory(self.arc.base, row)
        self.assertEqual(row['tags'], ['S1 Pro'])
        self.assertEqual(row['tags_origin'], 'auto')
        self.assertEqual(directory.name, path.parent.name)
        self.assertEqual(directory.parent.name, 'S1-Pro')
        self.assertEqual(store.resolve_media_path(self.arc.base, row, row['media'][0]).read_bytes(), png())


if __name__ == '__main__':
    unittest.main()
