"""Offline browser regression for the observed single-channel control contract."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.config import Config
from publish import channel_evidence as ce, channels
from publish.business_suite import ProbeRequired, PublishStepError

HTML = '''<!doctype html><body>
<div id="combo" role="combobox" aria-label="Post to Neakasa Deutschland and neakasa.de" onclick="menu.hidden=!menu.hidden">Post to</div>
<div id="menu" role="listbox" aria-label="Post to" hidden>
 <div role="option" aria-selected="true" onclick="toggle(this)">Neakasa Deutschland</div>
 <div role="option" aria-selected="true" onclick="toggle(this)">neakasa.de</div>
</div>
<input type="checkbox" role="switch" aria-label="Share to Facebook Story" aria-checked="true" checked onclick="event.preventDefault();dialog.hidden=false">
<input role="switch" aria-label="Share to Threads" aria-checked="false">
<input role="switch" aria-label="Boost" aria-checked="false">
<div id="dialog" role="dialog" aria-label="Stop sharing to Facebook Story" hidden>
 <div role="radio" aria-label="Don't share this post" aria-checked="true"></div>
 <button onclick="stopStory()">Confirm</button>
</div>
<script>
const menu=document.getElementById('menu'), combo=document.getElementById('combo'), dialog=document.getElementById('dialog');
function stopStory(){document.querySelector('[aria-label="Share to Facebook Story"]').setAttribute('aria-checked','false');dialog.hidden=true;}
function toggle(el){el.setAttribute('aria-selected',el.getAttribute('aria-selected')==='true'?'false':'true');
 combo.setAttribute('aria-label','Post to '+[...menu.children].filter(x=>x.getAttribute('aria-selected')==='true').map(x=>x.textContent).join(' and '));}
document.addEventListener('keydown',e=>{if(e.key==='Escape')menu.hidden=true});
</script>'''


class ChannelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(executable_path=Config().chrome_exe, headless=True)
        self.page = await self.browser.new_page()
        await self.page.route('**/*', lambda route: route.fulfill(body=HTML, content_type='text/html'))
        await self.page.goto(ce.SURFACE + '?asset_id=123&business_id=456')
        self.guard = patch.object(ce, 'require', return_value={'context_ids': {'asset_id': '123', 'business_id': '456'}})
        self.guard.start()

    async def asyncTearDown(self):
        self.guard.stop()
        await self.browser.close()
        await self.pw.stop()

    async def test_default_both_becomes_each_single_channel_and_story_is_disabled(self):
        for target in ('facebook', 'instagram'):
            await channels.select(self.page, (target,))
            self.assertEqual(await ce.selected(self.page), {c: c == target for c in ce.PREVIEWS})
            await self.page.keyboard.press('Escape')
            self.assertEqual(await self.page.get_by_role('switch', name=ce.STORY).get_attribute('aria-checked'), 'false')

    async def test_unrecognized_selected_account_stops_before_changes(self):
        await self.page.evaluate("menu.insertAdjacentHTML('beforeend','<div role=option aria-selected=true>Another Page</div>')")
        with self.assertRaises(PublishStepError):
            await channels.select(self.page, ('facebook',))
        self.assertEqual(await self.page.get_by_role('option', name='neakasa.de', exact=True).get_attribute('aria-selected'), 'true')

    async def test_changed_asset_stops_selection_and_last_submission_check(self):
        await self.page.goto(ce.SURFACE + '?asset_id=999&business_id=456')
        for action in (channels.select, channels.verify_before_submit):
            with self.assertRaises(ProbeRequired):
                await action(self.page, ('facebook',))
        self.assertTrue(await self.page.get_by_role('listbox', name='Post to').is_hidden())

    async def test_story_confirmation_cannot_change_global_preference(self):
        await self.page.get_by_role('radio', name="Don't share this post", include_hidden=True).evaluate("el=>el.setAttribute('aria-checked','false')")
        with self.assertRaises(ProbeRequired):
            await channels.select(self.page, ('facebook',))
        self.assertTrue(await self.page.get_by_role('dialog').is_visible())

    async def test_capture_rejects_dual_controls_or_additional_placement(self):
        row = {'channel': 'facebook', 'accounts': ce.accounts(), 'selected': {'facebook': True, 'instagram': False},
               'date_count': 1, 'time_count': 1, 'preview': True, 'url': ce.SURFACE,
               'switches': {ce.SCHEDULE: 'true', ce.STORY: 'false', 'Boost': 'false'}}
        ce.validate_record(row, 'facebook')
        for changed in ({'date_count': 2}, {'selected': {'facebook': True, 'instagram': True}},
                        {'switches': {**row['switches'], ce.THREADS: 'true'}}):
            with self.assertRaises(ProbeRequired):
                ce.validate_record({**row, **changed}, 'facebook')


if __name__ == '__main__':
    unittest.main()
