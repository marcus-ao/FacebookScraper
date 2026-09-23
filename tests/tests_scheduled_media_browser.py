"""Bounded diagnostic capture in isolated Chromium; no claimed Meta layout fixture."""
import asyncio
import io
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image
from playwright.async_api import APIRequestContext, async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.config import Config
from publish import scheduled_media


class CaptureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(executable_path=Config().chrome_exe, headless=True)
        self.page = await self.browser.new_page()
        encoded = io.BytesIO()
        Image.new('RGB', (100, 100), 'red').save(encoded, format='PNG')
        self.body = encoded.getvalue()
        await self.page.route('**/*', lambda route: route.fulfill(body=self.body, content_type='image/png'))

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def content(self, inner):
        await self.page.set_content('<div role="dialog" aria-label="Post details">' + inner + '</div>')
        return self.page.get_by_role('dialog')

    def response(self, status=200, body=None, headers=None):
        return SimpleNamespace(status=status, headers=headers or {},
            body=AsyncMock(return_value=self.body if body is None else body), dispose=AsyncMock())

    async def test_candidates_preserve_hidden_and_duplicate_nodes_but_never_prove_total(self):
        dialog = await self.content(''.join(
            f'<img alt="secret alt" src="https://offline.fbcdn.net/{i % 2}.png?token=secret"'
            + (' style="display:none"' if i == 2 else '') + '>' for i in range(4)))
        responses = [self.response() for _ in range(4)]
        with patch.object(APIRequestContext, 'get', AsyncMock(side_effect=responses)):
            capture = await scheduled_media.collect(dialog, timeout=2)
        self.assertEqual(len(capture.bodies), 4)
        self.assertEqual(len(capture.structure['images']), 4)
        self.assertFalse(capture.structure['images'][2]['visible'])
        self.assertNotIn('secret', json.dumps(capture.structure))
        self.assertIsNone(capture.image_count)
        self.assertFalse(capture.complete)
        self.assertEqual(capture.error, 'layout_unverified')
        self.assertTrue(capture.screenshot)
        for response in responses:
            response.dispose.assert_awaited_once()

    async def test_download_failures_and_unapproved_sources_remain_diagnostics(self):
        for status, body, headers, expected in (
                (403, None, {}, 'image_download_failed'),
                (200, b'', {}, 'image_empty'),
                (200, b'not an image', {}, 'image_decode_failed'),
                (200, None, {'content-length': str(scheduled_media.media.MAX_BYTES + 1)}, 'image_limit_exceeded')):
            with self.subTest(status=status, expected=expected):
                dialog = await self.content('<img src="https://offline.fbcdn.net/photo.png">')
                response = self.response(status, body, headers)
                with patch.object(APIRequestContext, 'get', AsyncMock(return_value=response)):
                    capture = await scheduled_media.collect(dialog, timeout=1)
                self.assertFalse(capture.complete)
                self.assertEqual(capture.error, expected)
                response.dispose.assert_awaited_once()
        dialog = await self.content('<img src="https://other.example/photo.png">')
        request = AsyncMock(side_effect=AssertionError('must not fetch unknown source'))
        with patch.object(APIRequestContext, 'get', request):
            capture = await scheduled_media.collect(dialog, timeout=1)
        self.assertEqual(capture.error, 'image_source_unverified')
        request.assert_not_called()

    async def test_changed_list_or_unloaded_image_cannot_become_complete(self):
        dialog = await self.content('<img src="https://offline.fbcdn.net/photo.png">')
        async def changed(*args, **kwargs):
            await dialog.evaluate('el => el.appendChild(document.createElement("img"))')
            return self.response()
        with patch.object(APIRequestContext, 'get', changed):
            capture = await scheduled_media.collect(dialog, timeout=1)
        self.assertEqual(capture.error, 'media_list_changed')
        self.assertFalse(capture.complete)
        dialog = await self.content('<img>')
        capture = await scheduled_media.collect(dialog, timeout=.15)
        self.assertEqual(capture.error, 'image_not_loaded')
        self.assertEqual(capture.structure['image_nodes'], 1)

    async def test_candidate_limit_is_explicit_and_downloads_nothing(self):
        dialog = await self.content('<img src="https://offline.fbcdn.net/photo.png">' * 65)
        with patch.object(APIRequestContext, 'get', AsyncMock(side_effect=AssertionError('over limit'))):
            capture = await scheduled_media.collect(dialog, timeout=1)
        self.assertEqual(capture.error, 'image_limit_exceeded')
        self.assertEqual(capture.structure['image_nodes'], 65)
        self.assertFalse(capture.bodies)


if __name__ == '__main__':
    unittest.main()
