"""The diagnostic uses only isolated browser pages and temporary lock storage."""
import contextlib
import io
import sys
import tempfile
import unittest
from datetime import date, time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.config import Config
from publish.journal import PublishOperationLock
from tools import probe_calendar_detail as probe


PAGE = '''<h2>This content has no text</h2><span>Story · Published on: Fri Sep 4, 6:39pm</span>
<p>PRIVATE CAPTION SECRET_TOKEN</p><h2>Feed preview</h2>
<a href="https://www.facebook.com/permalink.php?story_fbid=654321&id=123456&token=SECRET_TOKEN">Private link text</a>
<button role="tab" aria-selected="true" onclick="select(this)">Total performance</button>
<button role="tab" aria-selected="false" onclick="select(this)">Facebook</button>
<button role="tab" aria-selected="false" onclick="select(this)">Instagram</button>
<button onclick="window.submitted=true">Publish now</button>
<script>window.submitted=false;function select(tab){
 document.querySelectorAll('[role=tab]').forEach(n=>n.setAttribute('aria-selected','false'));
 tab.setAttribute('aria-selected','true');}</script>'''


class DetailProbeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(executable_path=Config().chrome_exe, headless=True)
        self.context = await self.browser.new_context()
        await self.context.route('**/*', lambda route: route.fulfill(
            content_type='text/html; charset=utf-8', body=PAGE))
        self.page = await self.context.new_page()
        await self.page.goto('https://business.facebook.com/latest/insights/object_insights/?content_id=999999&token=SECRET_TOKEN')

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def test_discovers_visible_detail_without_handcopied_id_and_restores_selected_tab(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = await probe.inspect(self.browser, day=date(2026,9,4), clock=time(18,39), kind='Story')
        self.assertTrue(result)
        text = output.getvalue()
        self.assertIn('FOUND: 2026-09-04 18:39 Story', text)
        self.assertIn('content_id=999999', text)
        self.assertIn('story_fbid=654321', text)
        self.assertIn('DONE: read-only inspection complete', text)
        for private in ('PRIVATE CAPTION', 'SECRET_TOKEN', 'Private link text'):
            self.assertNotIn(private, text)
        self.assertEqual(await self.page.get_by_role('tab', selected=True).inner_text(), 'Total performance')
        self.assertFalse(await self.page.evaluate('window.submitted'))

    async def test_no_match_reports_sanitized_open_pages_without_clicking_tabs(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = await probe.inspect(self.browser, day=date(2026,9,5), clock=time(18,39), kind='Story')
        self.assertFalse(result)
        self.assertIn('OPEN_PAGE', output.getvalue())
        self.assertIn('matching detail pages: 0', output.getvalue())
        self.assertNotIn('SECRET_TOKEN', output.getvalue())
        self.assertEqual(await self.page.get_by_role('tab', selected=True).inner_text(), 'Total performance')

    async def test_ambiguous_pages_do_not_select_an_arbitrary_content_item(self):
        another = await self.context.new_page()
        await another.goto('https://business.facebook.com/latest/insights/object_insights/?content_id=888888')
        with contextlib.redirect_stdout(io.StringIO()):
            result = await probe.inspect(self.browser, day=date(2026,9,4), clock=time(18,39), kind='Story')
        self.assertFalse(result)
        for page in (self.page, another):
            self.assertEqual(await page.get_by_role('tab', selected=True).inner_text(), 'Total performance')

    async def test_busy_publication_lock_prevents_browser_attachment(self):
        with tempfile.TemporaryDirectory() as raw:
            config = SimpleNamespace(state_dir=Path(raw), assert_publish_chrome_isolated=lambda: None)
            with PublishOperationLock(config.state_dir/'publish.lock'), \
                    patch.object(probe,'cfg',return_value=config), \
                    patch.object(probe,'attach',AsyncMock()) as attach, \
                    contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(RuntimeError):
                    await probe.run(SimpleNamespace(date=date(2026,9,4),time=time(18,39),kind='Story'))
                attach.assert_not_called()

    async def test_late_tabs_are_not_changed_without_a_captured_original_selection(self):
        await self.page.set_content('''<h2>This content has no text</h2>
          <span>Story · Published on: Fri Sep 4, 6:39pm</span><script>
          setTimeout(()=>document.body.insertAdjacentHTML('beforeend',
            '<button role="tab" aria-selected="true">Total performance</button>'+
            '<button role="tab" onclick="window.changed=true">Facebook</button>'+
            '<button role="tab" onclick="window.changed=true">Instagram</button>'),350);
          window.changed=false;</script>''')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = await probe.inspect(self.browser,day=date(2026,9,4),clock=time(18,39),kind='Story')
        self.assertTrue(result)
        self.assertIn('tabs left unchanged',output.getvalue())
        self.assertFalse(await self.page.evaluate('window.changed'))
        self.assertEqual(await self.page.get_by_role('tab',selected=True).inner_text(),'Total performance')


if __name__ == '__main__':
    unittest.main()
