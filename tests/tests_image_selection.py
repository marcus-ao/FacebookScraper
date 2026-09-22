"""Image choices bind to source bytes and drive every consumer without model calls."""
import io
import json
import sys
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests_web_review as fixtures
from core import review, translated
from core.config import cfg
from localize import images
from pipeline import approval, engine, refinement
from publish import compose, snapshots
from web.api import exporter


class ImageSelectionTests(unittest.TestCase):
    def setUp(self):
        self.fx = fixtures.WebReviewTests()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)

    def select(self, choice='original', **extra):
        detail = self.fx.client.get(self.fx.url).json()
        body = {'choice': choice,
                'source_image_sha256': images.sha256_file(self.fx.post_dir / '01.jpg'),
                'source_text_sha256': detail['text']['source_text_sha256'],
                'review_revision': detail['review']['revision'], **extra}
        return self.fx.client.post(self.fx.url + '/image/0/selection', json=body)

    def test_confirmed_original_is_previewed_composed_exported_and_never_generated(self):
        self.fx.write_generated_image('Ein sauberes Zuhause. #Neakasa')
        before = (self.fx.account / 'images_de.jsonl').read_bytes()
        response = self.select()
        self.assertEqual(response.status_code, 200, response.text)
        detail = response.json()
        self.assertEqual(detail['images'][0]['selection'], 'original_confirmed')
        self.assertTrue(detail['images'][0]['ready'])
        original = (self.fx.post_dir / '01.jpg').read_bytes()
        self.assertEqual(self.fx.client.get(detail['images'][0]['de_url']).content, original)
        post = compose.compose_post(self.fx.post_id, datetime.now(timezone.utc),
            archive_root=cfg().archive_dir, account=self.fx.account.name, warning_sink=None)
        self.assertEqual(post.image_sources, ('original_confirmed',))
        self.assertEqual(post.image_paths[0].read_bytes(), original)
        package, _ = exporter.package_post(self.fx.account, self.fx.source, handoff=False)
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            metadata = json.loads(archive.read('metadata.json'))
            self.assertEqual(metadata['images'][0]['selection'], 'original_confirmed')
            self.assertEqual(archive.read(metadata['images'][0]['file']), original)
        editor = Mock()
        settings = images.Settings()
        stats = images.run_localize(settings, editor, self.fx.account, [self.fx.source],
                                    None, True, False)
        self.assertEqual(stats.queued, 0)
        editor.edit.assert_not_called()
        self.assertEqual(engine.pending_image_indices(
            engine.SourcePost('facebook', self.fx.account, self.fx.source, datetime.now(timezone.utc),
                              'facebook:' + self.fx.post_id)), ())
        self.assertEqual((self.fx.account / 'images_de.jsonl').read_bytes(), before)
        event = review.history(self.fx.account)[-1]
        self.assertEqual(event['status'], 'edited')
        self.assertEqual(event['action'], 'image_selected')
        self.assertIsNone(event['actor'])
        locked = approval.lock(self.fx.account, self.fx.source,
            source_text_sha256=detail['text']['source_text_sha256'],
            review_revision=detail['review']['revision'],
            content_fingerprint=engine._publish_fingerprint(post))
        metadata, _, files, directory = snapshots.load(locked['snapshot_id'])
        self.assertEqual(files[metadata['images'][0]], original)
        self.assertEqual((directory / metadata['images'][0]).read_bytes(), original)
        self.assertEqual(self.select('automatic').status_code, 409)

    def test_revoke_and_upload_restore_localized_selection_and_stale_revision_is_rejected(self):
        first = self.select()
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(self.select('automatic', review_revision=None).status_code, 409)
        revoked = self.select('automatic')
        self.assertEqual(revoked.status_code, 200, revoked.text)
        self.assertEqual(revoked.json()['images'][0]['selection'], 'original')
        self.assertFalse(revoked.json()['images'][0]['ready'])
        self.assertEqual(self.select().status_code, 200)
        uploaded = self.fx.upload()
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        self.assertEqual(uploaded.json()['images'][0]['selection'], 'manual')

    def test_changed_source_bytes_or_media_identity_invalidate_confirmation(self):
        self.assertEqual(self.select().status_code, 200)
        self.fx.source['media'][0]['source_media_id'] = 'new-media'
        self.fx.write_source()
        self.assertEqual(self.fx.client.get(self.fx.url).json()['images'][0]['selection'], 'original')
        self.assertEqual(self.select().status_code, 200)
        old_sha = images.sha256_file(self.fx.post_dir / '01.jpg')
        Image.new('RGB', (1080, 1080), 'red').save(self.fx.post_dir / '01.jpg')
        self.assertEqual(self.fx.client.get(self.fx.url).json()['images'][0]['selection'], 'original')
        self.assertEqual(self.select(source_image_sha256=old_sha).status_code, 409)

    def test_frozen_or_generating_images_reject_selection(self):
        with images.ImageRunLock(cfg().state_dir / 'images.lock'):
            self.assertEqual(self.select().status_code, 409)
        review.transition(self.fx.account, self.fx.source, 'content_locked', expected_revision=None,
            expected_source_sha256=translated.source_text_sha256(self.fx.source['text']), snapshot_id='a' * 32)
        self.assertEqual(self.select().status_code, 409)

    def test_mixed_choices_keep_order_and_source_reordering_invalidates_the_choice(self):
        second = self.fx.post_dir / '02.jpg'
        Image.new('RGB', (1080, 1080), 'red').save(second)
        self.fx.source['media'].append({'kind': 'image', 'local_path': second.relative_to(self.fx.account).as_posix()})
        self.fx.write_source()
        self.assertEqual(self.select().status_code, 200)
        localized = self.fx.post_dir / 'media_de'
        localized.mkdir()
        Image.new('RGB', (1080, 1080), 'green').save(localized / '02.png')
        post = compose.compose_post(self.fx.post_id, datetime.now(timezone.utc),
            archive_root=cfg().archive_dir, account=self.fx.account.name, warning_sink=None)
        self.assertEqual(post.image_sources, ('original_confirmed', 'media_de'))
        self.assertEqual([path.name for path in post.image_paths], ['01.jpg', '02.png'])
        self.fx.source['media'].reverse()
        self.fx.write_source()
        self.assertEqual(self.fx.client.get(self.fx.url).json()['images'][0]['selection'], 'original')

    def test_original_choice_is_rechecked_after_job_planning_before_request(self):
        settings = images.Settings()
        planned = images.build_jobs(settings, self.fx.account, [self.fx.source])
        self.assertEqual(len(planned[0]), 1)
        self.assertEqual(self.select().status_code, 200)
        editor = Mock()
        with patch.object(images, 'build_jobs', return_value=planned):
            result = images.run_localize(settings, editor, self.fx.account, [self.fx.source], None, False, False)
        editor.edit.assert_not_called()
        self.assertEqual(result.skipped_manual, 1)

    def test_original_choice_without_translation_and_bad_original_format(self):
        (self.fx.account / 'translated.jsonl').unlink()
        response = self.select()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['images'][0]['selection'], 'original_confirmed')
        self.assertEqual(self.select('automatic').status_code, 200)
        Image.new('RGB', (1080, 1080), 'red').save(self.fx.post_dir / '01.jpg', format='GIF')
        self.assertEqual(self.select().status_code, 400)

    def test_generated_version_can_explicitly_replace_original_choice_without_a_paid_call(self):
        self.fx.write_generated_image('Ein sauberes Zuhause. #Neakasa')
        self.assertEqual(self.select().status_code, 200)
        versions = self.fx.versions()
        self.assertFalse(versions[0]['current'])
        response = self.fx.select(versions[0]['out_path'])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.fx.client.get(self.fx.url).json()['images'][0]['selection'], 'generated')

    def test_broken_image_choice_does_not_become_an_unreviewed_original(self):
        self.assertEqual(self.select().status_code, 200)
        path = self.fx.account / 'review_items.jsonl'
        row = json.loads(path.read_text('utf-8'))
        del row['image_selection']['source_sha256']
        path.write_text(json.dumps(row) + '\n', encoding='utf-8', newline='')
        with self.assertRaises(review.ReviewConflict):
            images.review_image_pairs(self.fx.account, self.fx.source, {'text_de': 'Deutsch'})

    def test_multiple_manual_candidates_are_conflicts_for_preview_planning_compose_and_export(self):
        directory = self.fx.post_dir / 'media_de'
        directory.mkdir()
        for name in ('01.jpg', '01.png'):
            Image.new('RGB', (1080, 1080), 'green').save(directory / name)
        detail = self.fx.client.get(self.fx.url).json()
        self.assertEqual(detail['images'][0]['selection'], 'conflict')
        self.assertFalse(detail['images'][0]['ready'])
        with self.assertRaisesRegex(ValueError, '多个.*人工'):
            images.build_jobs(images.Settings(), self.fx.account, [self.fx.source])
        with self.assertRaisesRegex(compose.ComposeError, '多个.*人工'):
            compose.compose_post(self.fx.post_id, datetime.now(timezone.utc),
                archive_root=cfg().archive_dir, account=self.fx.account.name, warning_sink=None)
        with self.assertRaisesRegex(review.ReviewConflict, '多个.*人工'):
            exporter.package_post(self.fx.account, self.fx.source)


if __name__ == '__main__':
    unittest.main()
