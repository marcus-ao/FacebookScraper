"""Read-only composer verification after the live Planner wait, in isolated Chromium."""
import io
import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from publish import business_suite as bs, workflow, journal

WHEN = datetime(2026, 9, 25, 12, 30, tzinfo=timezone.utc)
TEXT = 'Freude mit #Neakasa @home'
HTML = '''<div role="combobox" contenteditable="true" aria-label="Write into the dialogue box to include text with your post.">Freude mit #Neakasa @home</div>
<input type="checkbox" role="switch" aria-label="Set date and time" checked>
<input aria-label="Date picker" value="09/25/2026">
<div role="application" aria-label="Time input"><input role="spinbutton" value="12"><input role="spinbutton" aria-label="minutes" value="30"><input role="spinbutton" aria-label="meridiem" value="PM"><span>12 : 30 PM</span></div>
<ul><li role="listitem"><img src="https://fixture.fbcdn.net/1.png"><button>Remove photo</button></li>
<li role="listitem"><img src="https://fixture.fbcdn.net/2.png"><button>Remove photo</button></li></ul>'''


class FinalFormTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(headless=True)
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        self.bodies, self.paths = {}, []
        for i, colour in enumerate(('red', 'green'), 1):
            output = io.BytesIO()
            picture = Image.new('RGB', (128,128), colour)
            picture.putdata([(x*2,0,0) if i == 1 else (0,255-x*2,0)
                             for _y in range(128) for x in range(128)])
            picture.save(output, format='PNG')
            self.bodies[f'https://fixture.fbcdn.net/{i}.png'] = output.getvalue()
            path = self.directory / f'{i}.png'
            path.write_bytes(output.getvalue())
            self.paths.append(path)
        async def route(request):
            if request.request.url in self.bodies:
                await request.fulfill(body=self.bodies[request.request.url], content_type='image/png')
            else:
                await request.abort()
        await self.context.route('**/*', route)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def run_workflow(self, change):
        await self.page.set_content(HTML)
        await self.page.locator('img').first.wait_for()
        await self.page.wait_for_function('Array.from(document.images).every(i=>i.complete&&i.naturalWidth)')
        config = SimpleNamespace(state_dir=self.directory/'state', publish_debug_port=0,
            publish_profile_dir=self.directory/'unused-profile', assert_publish_chrome_isolated=lambda:None)
        post = SimpleNamespace(post_id='fixture-'+change, platform='facebook', text_de=TEXT,
            original_text_de=TEXT, source_text='English', image_paths=tuple(self.paths),
            image_sources=('media_de','media_de'), warnings=())
        async def live(*args, **kw):
            mutations = {
                'caption': "document.querySelector('[role=combobox]').textContent += ' changed'",
                'images': "document.querySelector('ul').append(document.querySelector('li'))",
                'count': "document.querySelector('li').remove()",
                'time': "document.querySelector('[aria-label=minutes]').value='31'",
                'date': "document.querySelector('[aria-label=\"Date picker\"]').value='09/26/2026'",
                'switch': "document.querySelector('[role=switch]').checked=false",
                'none': '0',
            }
            await self.page.evaluate(mutations[change])
        async def get(url, **kw):
            return SimpleNamespace(status=200, body=AsyncMock(return_value=self.bodies[url]), dispose=AsyncMock())
        async def submit(*args, **kw):
            self.assertEqual(journal.load(config.state_dir)[-1]['status'], journal.STATUS_SUBMIT_AMBIGUOUS)
            return bs.SubmitResult(clicked=True, confirmed=False, error='fixture unresolved')
        submit_mock = AsyncMock(side_effect=submit)
        with ExitStack() as stack:
            for obj, name, value in (
                (workflow,'cfg',lambda:config),
                (workflow,'attach',AsyncMock(return_value=(SimpleNamespace(stop=AsyncMock()), None, self.context))),
                (workflow.month_readback,'baseline',AsyncMock(return_value=bs.ScheduledBaseline('fixture',0))),
                (bs,'open_composer',AsyncMock(return_value=self.page)),
                (bs,'upload_images',AsyncMock(return_value=())),
                (bs,'fill_caption',AsyncMock()), (bs,'set_schedule',AsyncMock(return_value='fixture')),
                (bs,'assert_page_usable',AsyncMock()),
                (workflow.channels,'select',AsyncMock(return_value={'channel':'facebook','account':'fixture'})),
                (workflow.channels,'verify_before_submit',AsyncMock()),
                (workflow,'check_live_slot',AsyncMock(side_effect=live)),
                (workflow.channel_evidence,'require',lambda _: {'context_ids':{}}),
                (self.page.request,'get',AsyncMock(side_effect=get)),
                (bs,'submit',submit_mock),
                (bs,'capture_failure',AsyncMock(return_value=SimpleNamespace(screenshot='', lines=lambda:[]))),
            ):
                stack.enter_context(patch.object(obj,name,value))
            result = await workflow._execute_unlocked(post, WHEN, ui_timezone='UTC', timeout=.3,
                stamp='fixture', submit_enabled=True, report=lambda *_:None)
        return result, submit_mock

    async def test_changes_during_planner_wait_stop_before_submit_intent_without_refill(self):
        for change in ('caption','images','count','time','date','switch'):
            with self.subTest(change=change):
                result, submit = await self.run_workflow(change)
                self.assertEqual(result.attempt.status, journal.STATUS_FAILED_PRE_SUBMIT, result.message)
                self.assertEqual(result.attempt.step, '提交前最终表单复核', result.message)
                submit.assert_not_called()
                self.assertFalse(any(row['status']==journal.STATUS_SUBMIT_AMBIGUOUS
                                     for row in journal.load(self.directory/'state')))

    async def test_unchanged_form_arms_durably_and_submits_once(self):
        result, submit = await self.run_workflow('none')
        self.assertEqual(result.attempt.status, journal.STATUS_SUBMIT_AMBIGUOUS, result.message)
        self.assertEqual(submit.await_count, 1)


if __name__ == '__main__':
    unittest.main()
