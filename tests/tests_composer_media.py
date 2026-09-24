"""Frozen attachments use editor status; extra image comparisons use isolated fixtures."""
import io
import sys
import tempfile
import unittest
from contextlib import asynccontextmanager
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

    @asynccontextmanager
    async def editor(self):
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=Config().chrome_exe, headless=True)
            try:
                page = await browser.new_page()
                await page.route('**/*', lambda route: route.abort())
                yield page
            finally:
                await browser.close()

    def test_compressed_ordered_images_match_with_recorded_source_and_rendered_hashes(self):
        result = compare_ordered(self.paths, self.bodies)
        self.assertTrue(result['order_verified'])
        self.assertEqual(result['image_count'], 2)
        self.assertNotEqual(result['images'][0]['source_sha256'], result['images'][0]['rendered_sha256'])

    def test_missing_swapped_or_different_images_fail_extra_image_comparison(self):
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
                self.assertFalse(result['order_verified'])
                self.assertEqual(result['image_count'], 2)
                self.assertEqual(result['method'], 'file_chooser_attachment_count')
            finally:
                await browser.close()

    async def test_thumbnail_scheme_pixels_and_loading_artwork_do_not_gate_chosen_files(self):
        async with self.editor() as page:
            with patch.object(APIRequestContext, 'get', AsyncMock(side_effect=AssertionError('no redownload'))) as get:
                for src in ('blob:https://business.facebook.com/local-preview',
                            'data:image/gif;base64,R0lGODlhAQABAAAAACw=',
                            'https://another-preview.test/cropped-small.jpg', ''):
                    with self.subTest(src=src.split(':')[0]):
                        await page.set_content(''.join(
                            f'<div role="listitem"><img src="{src}" alt="Wird geladen.....">'
                            '<button>Remove photo</button></div>' for _ in self.paths))
                        result = await verify_upload(page, self.paths, timeout=.3)
                        self.assertEqual(result['image_count'], len(self.paths))
                        self.assertFalse(result['order_verified'])
                        self.assertEqual(result['method'], 'file_chooser_attachment_count')
                get.assert_not_called()

    async def test_missing_or_extra_attachments_and_real_upload_progress_still_stop(self):
        async with self.editor() as page:
            for count in (1, 3):
                await page.set_content('<button>Remove photo</button>' * count)
                with self.assertRaisesRegex(PublishStepError, '图片数量'):
                    await verify_upload(page, self.paths, timeout=.1)
            await page.set_content('<button>Remove photo</button>' * 2 + '<p>Uploading media</p>')
            with self.assertRaisesRegex(PublishStepError, '上传仍未完成'):
                await verify_upload(page, self.paths, timeout=.1)

    async def test_transient_upload_progress_settles_without_waiting_for_thumbnail_pixels(self):
        async with self.editor() as page:
            await page.set_content('<p id="busy">Uploading media</p><script>'
                'setTimeout(()=>{document.querySelector("#busy").remove();'
                'document.body.innerHTML="<button>Remove photo</button><button>Remove photo</button>"},150)'
                '</script>')
            result = await verify_upload(page, self.paths, timeout=1)
            self.assertEqual(result['image_count'], 2)

    async def test_known_reordering_stops_but_preview_url_replacement_does_not(self):
        async with self.editor() as page:
            await page.set_content('<ul>' + ''.join(
                f'<li role="listitem"><img src="blob:preview-{i}"><button>Remove photo</button></li>'
                for i in range(2)) + '</ul>')
            first = await verify_upload(page, self.paths, timeout=.3)
            await page.evaluate('document.querySelector("ul").append(document.querySelector("li"))')
            with self.assertRaisesRegex(PublishStepError, '顺序'):
                await verify_upload(page, self.paths, timeout=.3, previous=first)
            await page.evaluate('document.querySelector("ul").append(document.querySelector("li"));'
                'document.querySelectorAll("img").forEach((img,i)=>img.src="https://preview.test/"+i)')
            result = await verify_upload(page, self.paths, timeout=.3, previous=first)
            self.assertEqual(result['image_count'], 2)
            self.assertIsNone(result['attachment_order_unchanged'])
            self.assertFalse(result['order_verified'])


if __name__ == '__main__':
    unittest.main()
