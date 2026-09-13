"""Prepared editor media must preserve count and order; no real browser or upload."""
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image, ImageDraw
from playwright.async_api import APIRequestContext, async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.config import Config
from publish.media import compare_ordered, verify_upload
from publish.business_suite import PublishStepError


class MediaTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths, self.bodies = [], []
        for index in range(2):
            picture = Image.new('RGB', (200, 200), 'white')
            ImageDraw.Draw(picture).rectangle((index * 100, 0, index * 100 + 90, 199), fill='black')
            path = Path(self.temp.name) / ('%s.png' % index)
            picture.save(path)
            stream = io.BytesIO()
            picture.save(stream, format='JPEG', quality=80)
            self.paths.append(path)
            self.bodies.append(stream.getvalue())

    def test_compressed_ordered_images_match_with_recorded_source_and_rendered_hashes(self):
        result = compare_ordered(self.paths, self.bodies)
        self.assertTrue(result['order_verified'])
        self.assertEqual(result['image_count'], 2)
        self.assertNotEqual(result['images'][0]['source_sha256'], result['images'][0]['rendered_sha256'])

    def test_missing_swapped_or_different_images_stop_before_submission(self):
        for images in (self.bodies[:1], self.bodies[::-1], [self.bodies[1], self.bodies[1]]):
            with self.assertRaises(PublishStepError):
                compare_ordered(self.paths, images)

    def test_same_structure_with_changed_colours_is_not_the_same_image(self):
        source = Path(self.temp.name) / 'solid.png'
        Image.new('RGB', (200, 200), 'red').save(source)
        changed = io.BytesIO()
        Image.new('RGB', (200, 200), 'blue').save(changed, format='PNG')
        with self.assertRaises(PublishStepError):
            compare_ordered([source], [changed.getvalue()])

    async def test_nested_listitems_count_each_remove_control_once(self):
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=Config().chrome_exe, headless=True)
            try:
                page = await browser.new_page()
                async def serve(route):
                    index = int(route.request.url.rsplit('/', 1)[-1].split('.')[0])
                    await route.fulfill(body=self.bodies[index], content_type='image/jpeg')
                await page.route('**/*', serve)
                await page.set_content(''.join(
                    '<div role="listitem"><div role="listitem"><div role="listitem">'
                    f'<img src="https://offline.fbcdn.net/{i}.jpg"><button>Remove photo</button>'
                    '</div></div></div>' for i in range(2)))
                responses = [SimpleNamespace(status=200, body=AsyncMock(return_value=body),
                                             dispose=AsyncMock()) for body in self.bodies]
                with patch.object(APIRequestContext, 'get', AsyncMock(side_effect=responses)):
                    result = await verify_upload(page, self.paths, timeout=1)
                self.assertTrue(result['order_verified'])
                self.assertEqual(result['image_count'], 2)
            finally:
                await browser.close()


if __name__ == '__main__':
    unittest.main()
