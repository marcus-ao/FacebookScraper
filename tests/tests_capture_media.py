"""Source media identity, order and completeness regressions; no external requests."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.parse import from_graphql_node, from_iphone_struct, from_fb_story


class MediaParsingTests(unittest.TestCase):
    def test_sidecar_uses_children_once_in_source_order(self):
        post = from_graphql_node({'id': 'p', 'shortcode': 'P', '__typename': 'GraphSidecar',
            'display_url': 'https://cdn.invalid/A', 'edge_sidecar_to_children': {'edges': [
                {'node': {'id': 'a', 'display_url': 'https://cdn.invalid/A'}},
                {'node': {'id': 'b', 'display_url': 'https://cdn.invalid/B'}}]}}, 'target', 'delta')
        self.assertEqual([m.url for m in post.media], ['https://cdn.invalid/A', 'https://cdn.invalid/B'])
        self.assertTrue(post.source_media_complete)

    def test_declared_carousel_without_children_stays_incomplete(self):
        post = from_iphone_struct({'pk': 'p', 'media_type': 8, 'carousel_media_count': 3,
            'image_versions2': {'candidates': [{'url': 'https://cdn.invalid/cover'}]}}, 'target', 'delta')
        self.assertFalse(post.media_complete)
        self.assertIs(post.source_media_complete, False)
        self.assertEqual(post.source_media_count, 3)

    def test_video_without_download_url_never_becomes_static_image(self):
        post = from_iphone_struct({'pk': 'p', 'code': 'P', 'media_type': 2, 'product_type': 'clips',
            'image_versions2': {'candidates': [{'url': 'https://cdn.invalid/cover'}]}}, 'target', 'delta')
        self.assertTrue(any(m.kind == 'video' for m in post.media))
        self.assertFalse(any(m.kind == 'image' for m in post.media))

    def test_missing_sidecar_child_is_not_complete(self):
        post = from_graphql_node({'id': 'p', '__typename': 'GraphSidecar',
            'edge_sidecar_to_children': {'edges': [
                {'node': {'id': 'a', 'display_url': 'https://cdn.invalid/A'}}, {'node': {'id': 'b'}}]}},
            'target', 'delta')
        self.assertFalse(post.source_media_complete)

    def test_unknown_and_missing_instagram_discriminators_preserve_partial_images(self):
        for discriminator in (None, 'UnexpectedMediaShape'):
            node = {'id': 'p', 'display_url': 'https://cdn.invalid/cover'}
            if discriminator is not None:
                node['__typename'] = discriminator
            post = from_graphql_node(node, 'target', 'delta')
            self.assertFalse(post.source_media_complete)
            self.assertIsNone(post.source_media_count)
            self.assertEqual(len(post.media), 1)
            node['edge_sidecar_to_children'] = {'count': 3}
            self.assertEqual(from_graphql_node(node, 'target', 'delta').source_media_count, 3)
        for discriminator in (None, 99):
            node = {'pk': 'p', 'image_versions2': {'candidates': [{'url': 'https://cdn.invalid/cover'}]}}
            if discriminator is not None:
                node['media_type'] = discriminator
            post = from_iphone_struct(node, 'target', 'delta')
            self.assertFalse(post.source_media_complete)
            self.assertIsNone(post.source_media_count)
            self.assertEqual(len(post.media), 1)
            node['carousel_media_count'] = 3
            self.assertEqual(from_iphone_struct(node, 'target', 'delta').source_media_count, 3)

    def test_facebook_keeps_photo_order_and_one_size_per_photo(self):
        post = from_fb_story({'post_id': 'p', 'attachments': [{'all_subattachments': {'nodes': [
            {'media': {'__typename': 'Photo', 'id': 'a',
                'image': {'uri': 'https://cdn.invalid/A', 'width': 600, 'height': 600},
                'thumbnail': {'uri': 'https://cdn.invalid/a-small', 'width': 60, 'height': 60}}},
            {'media': {'__typename': 'Photo', 'id': 'b',
                'image': {'uri': 'https://cdn.invalid/B', 'width': 1200, 'height': 1200}}}]}}]},
            'target', 'delta')
        self.assertEqual([m.url for m in post.media], ['https://cdn.invalid/A', 'https://cdn.invalid/B'])
        self.assertEqual([m.source_media_id for m in post.media], ['a', 'b'])


if __name__ == '__main__':
    unittest.main()
