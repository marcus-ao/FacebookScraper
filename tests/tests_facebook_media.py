"""Facebook media regressions: minimized capture structures and isolated archive writes."""
import asyncio
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.capture import download_media
from core.parse import extract, from_fb_story
from core.store import Archive
from image_fixtures import image_bytes


def story(**changes):
    node = {
        'post_id': '122123783349379375', 'message': {'text': 'Berlin bound! Riko at IFA'},
        'creation_time': 1788098411,
        'url': 'https://www.facebook.com/neakasaofficial/posts/pfbidFixture',
        'actors': [{'id': '61591381265280', 'url': 'https://www.facebook.com/neakasaofficial'}],
    }
    node.update(changes)
    return node


def photo():
    return {'media': {'__typename': 'Photo', 'id': 'image-one',
                     'image': {'uri': 'https://cdn.invalid/photo.png', 'width': 80, 'height': 60}}}


def parse(payloads):
    return extract(copy.deepcopy(payloads), 'facebook', 'neakasaofficial', 'backfill')


class FacebookMediaTests(unittest.TestCase):
    def captured(self):
        # Real 2026-09-18 capture: retain attachment nesting/IDs, replace CDN URLs,
        # omit tracking, comments and unrelated profile data. No network access.
        path = Path(__file__).parent / 'fixtures' / 'facebook_media_styles.json'
        return json.loads(path.read_text('utf-8'))

    def test_captured_photo_renderer_resolves_placeholder_and_empty_subattachments(self):
        root = next(p['data']['node'] for p in self.captured()
                    if p['data']['node']['post_id'] == '122123783349379375')
        content = root['comet_sections']['content']['story']
        for node in (root, content):
            with self.subTest(fragment='root' if node is root else 'content'):
                post = from_fb_story(node, 'neakasaofficial', 'backfill')
                self.assertEqual([m.source_media_id for m in post.media], ['122123783337379375'])
                self.assertEqual(post.media[0].url, 'https://cdn.invalid/122123783337379375/photo_image.jpg')
                self.assertEqual((post.media[0].width, post.media[0].height), (512, 640))
                self.assertTrue(post.source_media_complete)
                self.assertEqual(post.source_media_count, 1)

    def test_captured_albums_keep_child_order_largest_sizes_and_video_identity(self):
        expected = {
            '122123185335379375': ['122123185281379375', '122123185275379375',
                '122123185269379375', '122123185293379375', '122123185287379375'],
            '122120053587379375': ['122120053533379375', '122120053539379375',
                '122120053545379375', '122120053527379375'],
        }
        for payloads in (self.captured(), list(reversed(self.captured()))):
            posts = {p.post_id: p for p in parse(payloads)}
            self.assertEqual(len(posts), 4)
            for pid, media_ids in expected.items():
                post = posts[pid]
                self.assertEqual([m.source_media_id for m in post.media], media_ids)
                self.assertTrue(all(m.kind == 'image' and m.url.endswith('/viewer_image.jpg') for m in post.media))
                self.assertTrue(post.source_media_complete)
                self.assertEqual(post.source_media_count, len(media_ids))
            video = posts['122125125675379375']
            self.assertEqual([(m.kind, m.source_media_id) for m in video.media], [('video', '3751016248369637')])

    def test_incomplete_rendered_album_keeps_available_images_without_extra_cover(self):
        root = next(p['data']['node'] for p in self.captured()
                    if p['data']['node']['post_id'] == '122123185335379375')
        connection = root['attachments'][0]['styles']['attachment']['all_subattachments']
        connection['nodes'] = connection['nodes'][:2]
        post = from_fb_story(root, 'neakasaofficial', 'backfill')
        self.assertEqual([m.source_media_id for m in post.media], ['122123185281379375', '122123185275379375'])
        self.assertFalse(post.source_media_complete)

    def test_missing_or_null_attachments_are_unknown_not_confirmed_empty(self):
        for node in (story(), story(attachments=None)):
            post = from_fb_story(node, 'neakasaofficial', 'backfill')
            self.assertFalse(post.source_media_complete)
            self.assertFalse(post.media_complete)
            self.assertIsNone(post.source_media_count)

    def test_explicit_empty_attachments_remain_a_text_only_post(self):
        post = parse([story(attachments=[])])[0]
        self.assertEqual(post.media, [])
        self.assertTrue(post.source_media_complete)
        self.assertEqual(post.source_media_count, 0)

    def test_empty_fragment_never_overrides_observed_partial_album(self):
        partial = story(attachments=[{'all_subattachments': {'count': 2, 'nodes': [photo()]}}])
        for empty in (story(), story(attachments=[])):
            for nodes in ([empty, partial], [partial, empty]):
                post = parse(nodes)[0]
                self.assertEqual([m.source_media_id for m in post.media], ['image-one'])
                self.assertFalse(post.source_media_complete)
                self.assertNotEqual(post.source_media_count, 0)

    def test_later_video_fragment_is_not_lost_to_an_empty_story(self):
        video = story(attachments=[{'media': {'__typename': 'Video', 'id': 'video-one'}}])
        post = parse([story(), video])[0]
        self.assertEqual([m.kind for m in post.media], ['video'])
        self.assertTrue(post.source_media_complete)

    def test_rebackfill_repairs_old_false_complete_empty_archive(self):
        for partial in (False, True):
            with self.subTest(partial=partial), tempfile.TemporaryDirectory() as temporary:
                arc = Archive(Path(temporary), 'fa_neakasaofficial')
                # Reproduce the supplied post.json: no media, but both completeness flags true.
                old = parse([story(attachments=[])])[0]
                old.tags, old.tags_origin = ['Riko'], 'manual'
                self.assertTrue(arc.append(old))
                directory = arc.post_dir(old)
                manual = arc.base / 'translated_human.jsonl'
                manual.write_bytes(b'{"fixture":"preserve human translation"}\n')
                attachments = ([{'all_subattachments': {'count': 2, 'nodes': [photo()]}}]
                               if partial else [photo()])
                recovered = parse([story(attachments=attachments)])[0]
                self.assertTrue(arc.should_append(recovered))
                response = SimpleNamespace(status=200, ok=True, headers={'content-type': 'image/png'},
                    body=AsyncMock(return_value=image_bytes(format='PNG')))
                context = SimpleNamespace(request=SimpleNamespace(get=AsyncMock(return_value=response)))
                asyncio.run(download_media(context, arc, recovered, recovered.permalink))
                self.assertTrue(arc.append(recovered))
                row = arc.rows()[0]
                self.assertEqual(len(row['media']), 1)
                self.assertTrue((arc.base / row['media'][0]['local_path']).is_file())
                self.assertEqual(row['media_complete'], not partial)
                self.assertEqual(row['tags'], ['Riko'])
                self.assertEqual(row['tags_origin'], 'manual')
                self.assertEqual(arc.post_dir(recovered), directory)
                self.assertEqual(manual.read_bytes(), b'{"fixture":"preserve human translation"}\n')
                history = json.loads((directory / 'source_history.jsonl').read_text('utf-8').splitlines()[0])
                self.assertEqual(history['source']['media'], [])


if __name__ == '__main__':
    unittest.main()
