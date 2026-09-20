"""The diagnostic uses only isolated browser pages and temporary lock storage."""
import contextlib
import base64
import io
import json
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
from tools.calendar_detail_response import entity_identity_fields


PAGE = '''<header><h2>This content has no text</h2><span>Story · Published on: Fri Sep 4, 6:39pm</span></header>
<p>PRIVATE CAPTION SECRET_TOKEN</p><h2>Feed preview</h2>
<div id="instagram_story_preview_frame"><div><div><span>neakasa.de</span></div></div></div>
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
        records=[json.loads(line) for line in text.splitlines() if line.startswith('{')]
        settled=next(row for row in records if row.get('phase')=='settled')
        self.assertEqual(settled['story_previews'][0]['node']['id'],'instagram_story_preview_frame')
        self.assertEqual(settled['story_previews'][0]['node']['text'],'neakasa.de')
        self.assertTrue(settled['readiness']['caption_empty'])
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
            '<button role="tab" onclick="window.changed=true">Instagram</button>'),3000);
          window.changed=false;</script>''')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = await probe.inspect(self.browser,day=date(2026,9,4),clock=time(18,39),kind='Story')
        self.assertFalse(result)
        self.assertIn('tabs left unchanged',output.getvalue())
        self.assertNotIn('DONE:', output.getvalue())
        self.assertFalse(await self.page.evaluate('window.changed'))
        self.assertEqual(await self.page.get_by_role('tab',selected=True).inner_text(),'Total performance')

    async def test_channel_hydration_does_not_skip_instagram_or_capture_loading_as_ready(self):
        # The supplied live log has no aria-controls and loses all visible tabs
        # during the Facebook samples. The delay here is an isolated reproduction.
        await self.page.evaluate('''() => {
          window.visits=[];
          window.select=tab=>{
            const name=tab.textContent;
            visits.push(name);
            document.querySelectorAll('[role=tab]').forEach(n=>n.setAttribute('aria-selected','false'));
            tab.setAttribute('aria-selected','true');
            if(name==='Total performance')return;
            document.querySelector('h2').textContent='Loading...';
            document.querySelectorAll('[role=tab]').forEach(n=>n.hidden=true);
            setTimeout(()=>{
              document.querySelector('h2').textContent='This content has no text';
              document.querySelectorAll('[role=tab]').forEach(n=>n.hidden=false);
            },1600);
          };
        }''')
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            result=await probe.inspect(self.browser,day=date(2026,9,4),clock=time(18,39),kind='Story')
        self.assertTrue(result)
        records=[json.loads(s) for s in output.getvalue().splitlines() if s.startswith('{')]
        settled=[r for r in records if r.get('phase')=='settled' and r.get('frame')==0]
        self.assertEqual({r['view'] for r in settled},{'initial','Facebook','Instagram'})
        self.assertTrue(all(r['readiness']['caption_ready'] for r in settled))
        self.assertEqual(await self.page.evaluate('window.visits'),['Facebook','Instagram','Total performance'])

    async def test_missing_selected_channel_fails_instead_of_claiming_complete(self):
        await self.page.get_by_role('tab',name='Instagram',exact=True).evaluate(
            "el=>el.onclick=()=>{document.querySelectorAll('[role=tab]').forEach(n=>n.setAttribute('aria-selected','false'))}")
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            result=await probe.inspect(self.browser,day=date(2026,9,4),clock=time(18,39),kind='Story',timeout=1)
        self.assertFalse(result)
        self.assertNotIn('DONE:',output.getvalue())
        self.assertIn('Instagram',output.getvalue())
        self.assertEqual(await self.page.get_by_role('tab',selected=True).inner_text(),'Total performance')

    async def test_loading_preview_is_partial_but_other_channel_is_still_collected(self):
        # The live Facebook header was stable while Loading preview persisted;
        # Instagram then had a visible Story preview despite a metrics error.
        await self.page.evaluate('''() => {const original=window.select;
          window.select=tab=>{original(tab);
            document.querySelector('#preview-loading')?.remove();
            if(tab.textContent==='Facebook')document.body.insertAdjacentHTML('beforeend',
              '<section id="preview-loading"><h2>Loading preview</h2><div role="progressbar"></div></section>');
          };document.body.insertAdjacentHTML('beforeend',
            '<section><h3>Metrics unavailable</h3><div role="progressbar"></div></section>');}''')
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            result=await probe.inspect(self.browser,day=date(2026,9,4),clock=time(18,39),kind='Story',timeout=1)
        self.assertFalse(result)
        records=[json.loads(s) for s in output.getvalue().splitlines() if s.startswith('{')]
        self.assertFalse(any(r.get('view')=='Facebook' and r.get('phase')=='settled' for r in records))
        stalled=next(r for r in records if r.get('view')=='Facebook' and r.get('phase')=='timeout')
        self.assertTrue(stalled['readiness']['preview_loading'])
        self.assertTrue(any(r.get('view')=='Instagram' and r.get('phase')=='settled' for r in records))
        self.assertNotIn('DONE:',output.getvalue())
        self.assertEqual(await self.page.get_by_role('tab',selected=True).inner_text(),'Total performance')

    async def test_single_detail_reload_observes_initial_responses_and_embedded_identity(self):
        payload={'data':{'story':{'__typename':'Story','id':'765432109',
            'owner':{'id':'123456789','username':'neakasa.de','access_token':'SECRET_TOKEN'},
            'caption':'PRIVATE INITIAL CAPTION','creation_time':1788518340}}}
        body=PAGE + '<script type="application/json">'+json.dumps(payload)+'</script>' + '''
          <script>fetch('/api/graphql/?secret=SECRET_TOKEN',{method:'POST'})</script>'''
        document_requests=[]
        async def document(route):
            document_requests.append(route.request.url)
            await route.fulfill(content_type='text/html; charset=utf-8',body=body)
        await self.context.route('**/latest/insights/object_insights/**',document)
        await self.context.route('**/api/graphql/**',lambda route: route.fulfill(
            content_type='application/json',body=json.dumps(payload)))
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            result=await probe.inspect(self.browser,day=date(2026,9,4),clock=time(18,39),kind='Story',reload=True)
        self.assertTrue(result)
        self.assertEqual(document_requests,[self.page.url])
        text=output.getvalue()
        records=[json.loads(s) for s in text.splitlines() if s.startswith('{')]
        self.assertTrue(any(isinstance(r.get('RESPONSE_EVIDENCE'),dict) and r['during_view'] in {'reload','initial'} for r in records))
        embedded=next(r['EMBEDDED_EVIDENCE'] for r in records if isinstance(r.get('EMBEDDED_EVIDENCE'),dict))
        self.assertEqual(embedded['data']['story']['owner']['id'],'123456789')
        self.assertEqual(embedded['data']['story']['id'],'765432109')
        for private in ('SECRET_TOKEN','PRIVATE INITIAL CAPTION','access_token'):
            self.assertNotIn(private,text)
        self.assertEqual(await self.page.get_by_role('tab',selected=True).inner_text(),'Total performance')
        self.assertFalse(await self.page.evaluate('window.submitted'))

    async def test_passive_content_evidence_redacts_credentials_and_keeps_identity_relationships(self):
        payload={'data':{'node':{'__typename':'Story','id':'987654321',
            'owner':{'id':'123456789','username':'neakasa.de','access_token':'SECRET_TOKEN'},
            'caption':'PRIVATE CAPTION','creation_time':1788518340,'media_type':'IMAGE',
            'url':'https://instagram.com/stories/neakasa.de/987654321/?token=SECRET_TOKEN',
            'permalink_url':'https://www.facebook.com/permalink.php?story_fbid=654321&id=123456&token=SECRET_TOKEN'}},
            'session_token':'SECRET_TOKEN'}
        await self.context.route('**/api/graphql/**',lambda route: route.fulfill(
            content_type='application/json',body=json.dumps(payload)))
        await self.page.evaluate('''() => {const original=window.select;
          window.select=tab=>{original(tab);fetch('/api/graphql/?secret=SECRET_TOKEN',{method:'POST'})};}''')
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            result=await probe.inspect(self.browser,day=date(2026,9,4),clock=time(18,39),kind='Story',responses=True)
        self.assertTrue(result)
        text=output.getvalue()
        self.assertIn('987654321',text)
        self.assertIn('123456789',text)
        self.assertIn('neakasa.de',text)
        self.assertIn('creation_time',text)
        records=[json.loads(line) for line in text.splitlines() if line.startswith('{')]
        responses=[row['RESPONSE_EVIDENCE'] for row in records if isinstance(row.get('RESPONSE_EVIDENCE'),dict)]
        self.assertTrue(responses)
        self.assertEqual(responses[0]['data']['node']['permalink_url'],
                         'https://www.facebook.com/permalink.php?story_fbid=654321&id=123456')
        self.assertNotIn('PRIVATE CAPTION',text)
        self.assertNotIn('SECRET_TOKEN',text)
        self.assertNotIn('access_token',text)
        self.assertEqual(await self.page.get_by_role('tab',selected=True).inner_text(),'Total performance')

    async def test_probe_json_survives_non_utf8_powershell_pipelines(self):
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            probe.emit({'metadata':'Story · Published on: Fri Sep 4, 6:39pm'})
        text=output.getvalue()
        self.assertTrue(text.isascii())
        self.assertEqual(json.loads(text)['metadata'],'Story · Published on: Fri Sep 4, 6:39pm')

    async def test_identity_schema_exposes_id_relations_without_encoded_private_values(self):
        opaque = base64.b64encode(json.dumps({'post_id':'765432109','page_id':'123456789',
            'caption':'PRIVATE CAPTION','access_token':'SECRET_TOKEN'}).encode()).decode()
        fields = entity_identity_fields({'data':{'tofu_entity':{'id':opaque,'entity_info':{
            '__typename':'TofuFBStoryEntityInfo','title':'Your Story','unrecognized_id':'998877665',
            'unknown_field':'PRIVATE VALUE','cookie':'SECRET_TOKEN'}}}})
        self.assertEqual(fields['id']['decoded']['post_id'],'765432109')
        self.assertEqual(fields['entity_info']['unrecognized_id'],'998877665')
        self.assertEqual(fields['entity_info']['title'],{'title_length':10})
        for private in (opaque,'PRIVATE CAPTION','PRIVATE VALUE','SECRET_TOKEN','access_token','cookie'):
            self.assertNotIn(private,json.dumps(fields))


if __name__ == '__main__':
    unittest.main()
