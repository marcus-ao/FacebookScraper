"""Month completeness and suggestion classification using observed browser semantics."""
import calendar
import sys
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.config import Config
from publish import month_inventory as month
from publish.business_suite import PublishStepError, ProbeRequired
from publish.planner_content import DetailReadError
from publish import published_details

# The real Planner tooltip follows the pointer on and off a slot; recommendation_state's leading
# mouse.move(0, 0) depends on the off half, so a fixture without it only ever reads one slot.
TOOLTIP_SLOT = ('<div role="link" onmouseenter="tip.hidden=false" onmouseleave="tip.hidden=true">'
                '{clock} AM<img alt="Instagram"></div>')
TOOLTIP = '<div role="tooltip" id="tip" hidden>' + month.RECOMMENDATION + '</div>'
CAPTION = 'This is a manual test.😊 #SmartPetFeeder #CatLovers #NeakasaRiko'
ENTRY = CAPTION + ' September 30, 2026, 5:30 PM'
SPEC = SimpleNamespace(attributes={
    'datetime_regex': r'(?P<date>[A-Z][a-z]+ \d+, \d{4}), (?P<time>\d+:\d+ [AP]M)',
    'date_format': '%B %d, %Y', 'time_format': '%I:%M %p',
    'dialog_role': 'dialog', 'dialog_name': 'Post details',
    'remote_id_regex': r'ID:\s*(?P<remote_id>\d{6,})',
    'facebook_marker': "Facebook's Feed", 'facebook_account_token': 'Neakasa Deutschland',
    'instagram_marker': 'Instagram feed', 'instagram_account_token': 'neakasa.de'})


class MonthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(executable_path=Config().chrome_exe, headless=True)
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        days = calendar.Calendar(firstweekday=6).itermonthdates(2026, 9)
        cells = ''.join(f'<div role="link" draggable="false"><span>{d.day}</span></div>' for d in days)
        await self.page.set_content('<h1>Planner</h1><h1>September</h1><h1>2026</h1>'
            '<button>Month</button><button>Content type: all</button><button>Shared to: all</button>' + cells)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def mount_slots(self, count):
        """Mount `count` recommendation slots, with Playwright's one-off first-hover cost already paid."""
        await self.page.set_content(''.join(TOOLTIP_SLOT.format(clock=f'{9 + i}:00') for i in range(count)) + TOOLTIP)
        slots = self.page.get_by_role('link')
        # The first hover on a page carries an actionability setup measured here at a median 641ms,
        # against 47ms for every later one. Spend it now: left inside a timed assertion it overruns
        # short budgets, recommendation_state refuses, and the positive case flakes.
        await slots.first.hover()
        return slots

    async def test_all_months_require_the_complete_contiguous_day_sequence(self):
        for year in (2026, 2028):
            for number in range(1, 13):
                expected = tuple(calendar.Calendar(firstweekday=6).itermonthdates(year, number))
                self.assertEqual(month.dates_for_cells(date(year, number, 1), [d.day for d in expected]), expected)
                for damaged in ([d.day for d in expected[:-1]], [d.day for d in expected[1:]], [1, 2, 3]):
                    with self.assertRaises(PublishStepError):
                        month.dates_for_cells(date(year, number, 1), damaged)

    async def test_month_grid_includes_empty_days_and_delayed_material(self):
        await self.page.evaluate('''() => {let cells=document.querySelectorAll('[draggable=false]');
          setTimeout(()=>cells[10].insertAdjacentHTML('beforeend','<a role="link" href="/latest/insights/object_insights/?content_id=12345678">8:36 AM<img alt="Instagram"></a>'),120)}''')
        result = await month.read_grid(self.page, timeout=10)
        self.assertEqual(len(result), 35)
        self.assertEqual(result[10]['date'], date(2026, 9, 9))
        self.assertEqual(len(result[10]['items']), 1)

    async def test_partial_or_loading_calendar_never_proves_empty(self):
        await self.page.locator('[draggable=false]').last.evaluate('el=>el.remove()')
        with self.assertRaises(PublishStepError):
            await month.read_grid(self.page, timeout=.2)
        await self.page.set_content('<h1>September</h1><h1>2026</h1><div role="progressbar"></div>')
        with self.assertRaises(PublishStepError):
            await month.read_grid(self.page, timeout=.2)

    async def test_only_positive_recommendation_tooltip_can_skip_a_slot(self):
        item = await self.mount_slots(1)
        self.assertEqual(await month.recommendation_state(self.page, item), 'shown')
        await self.page.get_by_role('tooltip').evaluate("el=>el.textContent='A future post'")
        self.assertEqual(await month.recommendation_state(self.page, item), 'absent')
        # Refused for the text, not for a stalled hover: the tooltip did open, and even the full
        # production budget never lets a non-matching one confirm. Shortening the budget here would
        # buy a second back and give away the difference between those two reasons.
        self.assertTrue(await self.page.get_by_role('tooltip').is_visible())

    async def test_every_recommendation_slot_in_a_month_is_confirmed_not_just_the_first(self):
        slots = await self.mount_slots(2)
        for index in range(2):
            self.assertEqual(await month.recommendation_state(self.page, slots.nth(index)), 'shown')

    async def test_a_slot_whose_tooltip_never_resolves_stays_a_post_rather_than_a_recommendation(self):
        slots = await self.mount_slots(1)
        await self.page.get_by_role('tooltip').evaluate("el=>el.removeAttribute('role')")
        # A short budget cannot flip this one: every way the hover can go wrong also refuses.
        self.assertNotEqual(await month.recommendation_state(self.page, slots, timeout=.3), 'shown')
        row = {'date': date(2026, 9, 15), 'cell_index': 0}
        await self.page.set_content('<div role="link" draggable="false">15'
                                    '<div role="link">10:00 AM</div></div>')
        # Refusing to confirm must cost the run an error, never a silently skipped scheduled post.
        with patch.object(month, 'recommendation_state', AsyncMock(return_value='absent')), \
                patch.object(month.bs, 'require_readback_evidence', return_value=SimpleNamespace(
                    attributes={'datetime_regex': r'(?P<date>September \d+, \d{4}), (?P<time>\d+:\d+ [AP]M)',
                                'date_format': '%B %d, %Y', 'time_format': '%I:%M %p'})):
            with self.assertRaises(PublishStepError) as caught:
                await month.read_item(self.page, row, {'index': 0, 'href': '', 'text': '10:00 AM',
                                                       'time': '10:00 AM', 'aria': '10:00 AM'}, timeout=.5)
        # The budget running out is how this ends, but not why: an item left for a
        # real task because its tooltip never opened has to say so, or every such
        # slot reads as a browser timeout and the placeholder stays invisible.
        self.assertEqual(caught.exception.diagnostic['stage'], 'item_ready')
        self.assertEqual(caught.exception.diagnostic['code'], 'structure_unknown')
        self.assertEqual(caught.exception.diagnostic['missing_fields'],
                         ['item_caption', 'recommendation_absent'])

    async def test_conflict_inventory_contains_unknown_channel_cards(self):
        rows = [{'date': date(2026, 9, 15), 'items': [{'index': 0, 'time': '10:00 AM'}]}]
        with patch.object(month, 'prepare', AsyncMock()), patch.object(month, 'read_grid', AsyncMock(return_value=rows)), \
                patch.object(month, 'read_item', AsyncMock(return_value={'channels': (), 'remote_ids': {},
                    'text': 'Unknown target', 'delivery': 'scheduled'})):
            result = await month.read(self.page, ui_timezone='America/Los_Angeles', business_timezone='Europe/Berlin')
        self.assertEqual(len(result.occupied), 1)
        self.assertFalse(result.channels_complete)

    async def test_hovered_schedule_control_is_not_an_expand_button(self):
        await self.page.locator(month.DAY_SELECTOR).nth(20).evaluate("el=>el.insertAdjacentHTML('beforeend','<button>Schedule\\u200b</button>')")
        self.assertEqual(len(await month.read_grid(self.page, timeout=10)), 35)

    async def test_schedule_aria_and_grid_must_agree_on_time_before_reading_remote_id(self):
        row = {'date': date(2026, 9, 15), 'cell_index': 0}
        item = {'index': 0, 'href': '', 'text': '1:00 AM', 'time': '1:00 AM',
                'aria': 'Full caption September 15, 2026, 2:00 AM'}
        await self.page.set_content('<div role="link" draggable="false">15'
            '<div role="link" aria-label="Full caption September 15, 2026, 2:00 AM">1:00 AM</div></div>')
        spec = SimpleNamespace(attributes={
            'datetime_regex': r'(?P<date>September \d+, \d{4}), (?P<time>\d+:\d+ [AP]M)',
            'date_format': '%B %d, %Y', 'time_format': '%I:%M %p'})
        with patch.object(month, 'recommendation_state', AsyncMock(return_value='absent')), \
                patch.object(month.bs, 'require_readback_evidence', return_value=spec), \
                patch.object(month.bs, '_open_channel_dialogs', AsyncMock()) as open_detail:
            with self.assertRaises(PublishStepError):
                await month.read_item(self.page, row, item, timeout=.5)
            open_detail.assert_not_called()

    async def mount_scheduled_card(self, *, delay=120, entry=ENTRY):
        # Recorded click 225: the time link has no href/aria; its third ancestor owns the caption.
        # Snapshot 180 adds a full-date link on hover; 181-184 show the delayed preview.
        await self.page.locator(month.DAY_SELECTOR).nth(31).evaluate('''(cell, data) => {
          cell.insertAdjacentHTML('beforeend', `<div aria-label="${data.caption}"><div><div>
            <div role="link" id="slot">5:30\u202fPM</div></div></div></div>`);
          const slot=document.getElementById('slot');
          slot.onmouseenter=()=>{window.tipTimer=setTimeout(()=>{
            if(!document.getElementById('full')) document.body.insertAdjacentHTML('beforeend',
              `<div id="full" role="link">${data.entry}</div>`);
          },data.delay)};
          slot.onmouseleave=()=>{clearTimeout(window.tipTimer);document.getElementById('full')?.remove()};
          slot.onclick=()=>{
            document.body.insertAdjacentHTML('beforeend', `<div role="dialog" aria-label="Post details">
              Post details ID: 2059528092104126 Facebook's Feed <span id="loading">Loading preview</span></div>`);
            setTimeout(()=>{document.getElementById('loading').outerHTML='<article>Neakasa Deutschland September 30 at 5:30 PM Preview</article>'},350);
          };
          document.onkeydown=e=>{if(e.key==='Escape')document.querySelector('[role=dialog]')?.remove()};
        }''', {'caption': CAPTION, 'entry': entry, 'delay': delay})

    async def test_recorded_time_child_reads_parent_caption_and_delayed_hover_entry(self):
        await self.mount_scheduled_card(delay=1700)
        with patch.object(month, 'prepare', AsyncMock()), \
                patch.object(month.bs, 'require_readback_evidence', return_value=SPEC):
            result = await month.read(self.page, ui_timezone='Asia/Shanghai',
                                      business_timezone='Asia/Shanghai', timeout=5)
        self.assertEqual(len(result.cards), 1)
        self.assertEqual(result.cards[0].rendered, CAPTION)
        self.assertEqual(result.cards[0].remote_ids, (('facebook', '2059528092104126'),))
        self.assertEqual(result.cards[0].at.isoformat(), '2026-09-30T17:30:00+08:00')
        self.assertTrue(result.channels_complete)
        self.assertEqual(await self.page.get_by_role('dialog').count(), 0)

    async def test_delayed_card_label_is_reread_instead_of_reusing_time_only_snapshot(self):
        await self.mount_scheduled_card()
        rows = await month.read_grid(self.page, timeout=5)
        await self.page.locator('#slot').evaluate('(el, text)=>el.setAttribute("aria-label",text)', ENTRY)
        with patch.object(month.bs, 'require_readback_evidence', return_value=SPEC):
            result = await month.read_item(self.page, rows[31], rows[31]['items'][0], timeout=5)
        self.assertEqual(result['text'], CAPTION)
        self.assertEqual(result['channels'], ('facebook',))

    async def test_unrelated_hover_entry_cannot_supply_a_time_only_cards_evidence(self):
        await self.mount_scheduled_card(entry=ENTRY.replace(CAPTION, CAPTION + ' different post'))
        rows = await month.read_grid(self.page, timeout=5)
        with patch.object(month.bs, 'require_readback_evidence', return_value=SPEC):
            with self.assertRaises(PublishStepError):
                await month.read_item(self.page, rows[31], rows[31]['items'][0], timeout=4)

    async def test_sibling_card_label_is_not_borrowed_and_changed_caption_is_not_hydration(self):
        await self.mount_scheduled_card()
        rows = await month.read_grid(self.page, timeout=5)
        await self.page.locator('#slot').evaluate('el=>el.parentElement.parentElement.parentElement.setAttribute("aria-label","Changed caption")')
        await self.page.locator('#slot').evaluate('''el=>el.onmouseenter=()=>{
          document.getElementById('full')?.remove();
          document.body.insertAdjacentHTML('beforeend','<div id="full" role="link">Changed caption September 30, 2026, 5:30 PM</div>');
        }''')
        with patch.object(month.bs, 'require_readback_evidence', return_value=SPEC):
            with self.assertRaises(PublishStepError):
                await month.read_item(self.page, rows[31], rows[31]['items'][0], timeout=4)
        await self.page.locator(month.DAY_SELECTOR).nth(31).evaluate('''cell=>{
          cell.innerHTML='<span>30</span><div><div aria-label="Sibling caption"><div role="link">4:30 PM</div></div><div role="link">5:30 PM</div></div>';
        }''')
        rows = await month.read_grid(self.page, timeout=5)
        self.assertEqual(rows[31]['items'][1]['labels'], [])

    async def test_actual_edit_during_detail_read_still_invalidates_the_month(self):
        await self.mount_scheduled_card()
        await self.page.locator('#slot').evaluate('''el=>el.addEventListener('click',()=>{
          setTimeout(()=>el.textContent='6:30 PM',100);
        })''')
        with patch.object(month, 'prepare', AsyncMock()), \
                patch.object(month.bs, 'require_readback_evidence', return_value=SPEC):
            with self.assertRaisesRegex(PublishStepError, '远端月历已更新'):
                await month.read(self.page, ui_timezone='Asia/Shanghai',
                                 business_timezone='Asia/Shanghai', timeout=5)

    async def test_textless_facebook_story_cannot_borrow_the_feed_identity_adapter(self):
        # Story labels and a Feed permalink are insufficient. Even a permissive
        # Feed identity stub must not bypass the native Story evidence path.
        await self.page.locator(month.DAY_SELECTOR).nth(5).evaluate('''el=>el.insertAdjacentHTML(
          'beforeend','<a href="https://business.facebook.com/latest/insights/object_insights/?content_id=1804155825688886">6:39 PM</a>')''')
        await self.context.route('https://business.facebook.com/**', lambda route: route.fulfill(
            content_type='text/html; charset=utf-8', body='''<header><h2>This content has no text</h2>
              <span>Story · Published on: Fri Sep 4, 6:39pm</span><img alt="Facebook"></header>
              <aside><h2>Feed preview</h2><h2><a href="https://www.facebook.com/profile.php?id=123456">Neakasa Deutschland</a></h2>
              <a href="https://www.facebook.com/permalink.php?story_fbid=654321&id=123456">4d</a></aside>
              <div role="progressbar">Metrics loading</div>'''))
        with patch.object(month, 'prepare', AsyncMock()), patch.object(month, 'accounts',
                return_value={'facebook':'Neakasa Deutschland','instagram':'neakasa.de'}), \
                patch.object(published_details, 'preview_identity', AsyncMock(return_value={
                    'owner':'Neakasa Deutschland','remote_id':'654321'})) as feed_identity:
            result = await month.read(self.page, ui_timezone='Asia/Shanghai', business_timezone='Asia/Shanghai', timeout=5)
        # The point of this fixture, whichever reader ends up refusing it: a Story
        # never reaches the Feed adapter, so the stub above stays unused.
        feed_identity.assert_not_called()
        self.assertFalse(result.decision_complete)
        self.assertEqual(len(result.cards), 1)
        card = result.cards[0]
        self.assertEqual((card.placement, card.rendered, card.caption_status), ('story','','unknown'))
        self.assertEqual(dict(card.remote_ids), {})
        self.assertEqual(card.at.isoformat(), '2026-09-04T18:39:00+08:00')
        # A Facebook-badged Story with no channel tab is read as Facebook-only, so
        # what it lacks is that page's own Story entity, not an Instagram tab it
        # was never going to have.
        self.assertEqual(result.diagnostics[0]['missing_fields'], ['facebook_story_identity'])

    async def test_aggregate_views_require_independent_channel_identity_time_and_caption(self):
        # Contract fixture with channel-owned panels, not captured Meta Story DOM.
        await self.page.set_content('''<header id="summary"><h2>This content has no text</h2>
          <span>Story · Published on: Fri Sep 4, 6:39pm</span><img alt="Facebook"><img alt="Instagram"></header>
          <button role="tab" id="fb" aria-controls="fb-view" aria-selected="false" onclick="select(this)">Facebook</button>
          <button role="tab" id="ig" aria-controls="ig-view" aria-selected="false" onclick="select(this)">Instagram</button>
          <div role="tabpanel" id="fb-view" aria-labelledby="fb" hidden><header><h2>This content has no text</h2>
            <span>Story · Published on: Fri Sep 4, 6:39pm</span></header></div>
          <div role="tabpanel" id="ig-view" aria-labelledby="ig" hidden><header><h2>Instagram version</h2>
            <span>Story · Published on: Fri Sep 4, 6:40pm</span></header></div>
          <script>function select(tab){summary.hidden=true;
            document.querySelectorAll('[role=tab]').forEach(n=>n.setAttribute('aria-selected','false'));
            document.querySelectorAll('[role=tabpanel]').forEach(n=>n.hidden=true);
            tab.setAttribute('aria-selected','true');
            setTimeout(()=>document.getElementById(tab.getAttribute('aria-controls')).hidden=false,350);
          }</script>''')
        async def identity(scope, channel, placement):
            return {'owner': 'Neakasa Deutschland' if channel=='facebook' else 'neakasa.de',
                    'remote_id':'654321' if channel=='facebook' else '987654'}
        with patch.object(published_details,'preview_identity',identity):
            variants = await published_details.read(self.page, {'date':date(2026,9,4)}, {'time':'6:39 PM'},
                {'facebook':'Neakasa Deutschland','instagram':'neakasa.de'}, timeout=3)
        self.assertEqual([v['channels'] for v in variants], [('facebook',),('instagram',)])
        self.assertEqual([v['remote_ids'] for v in variants], [{'facebook':'654321'},{'instagram':'987654'}])
        self.assertEqual([v['ui_at'].minute for v in variants], [39,40])
        self.assertEqual([v['text'] for v in variants], ['', 'Instagram version'])

    async def test_selected_tab_and_independent_id_do_not_bind_a_shared_stale_header(self):
        # Regression for non-atomic tab hydration: a new preview ID is not proof
        # that a shared caption/date outside its channel view have updated.
        await self.page.set_content('''<header><h2>Facebook caption</h2>
          <span>Post · Published on: Fri Sep 4, 6:39pm</span><img alt="Facebook"><img alt="Instagram"></header>
          <button role="tab" aria-selected="true">Instagram</button>
          <aside><h2>Feed preview</h2><a href="https://www.instagram.com/stories/neakasa.de/987654/">View story</a></aside>''')
        with self.assertRaises(DetailReadError) as error:
            await published_details.read_view(self.page, {'date':date(2026,9,4)}, {'time':'6:39 PM'},
                {'instagram':'neakasa.de'}, channel='instagram', aggregate=True, timeout=.7)
        self.assertEqual(error.exception.code,'missing_fields')
        self.assertEqual(error.exception.missing_fields,('channel_detail_scope',))

    async def test_late_channel_tab_cannot_be_silently_omitted(self):
        await self.page.set_content('''<div role="tabpanel" id="view" aria-labelledby="fb"><header><h2>Caption</h2>
          <span>Post · Published on: Fri Sep 4, 6:39pm</span><img alt="Facebook"></header>
          <aside><h2>Feed preview</h2><h2><a href="https://www.facebook.com/profile.php?id=123456">Neakasa Deutschland</a></h2>
          <a href="https://www.facebook.com/permalink.php?story_fbid=654321&id=123456">4d</a></aside></div>
          <button role="tab" id="fb" aria-selected="true" aria-controls="view" onclick="setTimeout(()=>document.body.insertAdjacentHTML('beforeend','<button role=tab>Instagram</button>'),200)">Facebook</button>''')
        with self.assertRaises(DetailReadError) as error:
            await published_details.read(self.page, {'date':date(2026,9,4)}, {'time':'6:39 PM'},
                {'facebook':'Neakasa Deutschland','instagram':'neakasa.de'}, timeout=2)
        self.assertEqual(error.exception.missing_fields,('channel_tabs',))

    async def test_scheduled_aggregate_cannot_copy_one_id_into_two_channels(self):
        await self.page.set_content('''<button onclick="document.querySelector('[role=dialog]').hidden=false">Open</button>
          <div role="dialog" aria-label="Post details" hidden>ID: 123456
          Facebook's Feed Neakasa Deutschland Instagram feed neakasa.de</div>''')
        value = await month.bs._open_channel_dialogs(self.page,self.page.get_by_role('button'),SPEC,timeout=1)
        self.assertEqual(value,{})

    async def test_shared_story_and_feed_source_links_do_not_supply_content_identity(self):
        await self.page.set_content('''<aside><h2>Feed preview</h2>
          <h2><a href="https://www.facebook.com/profile.php?id=123456">Neakasa Deutschland</a></h2>
          <article>Shared post <a href="https://www.facebook.com/permalink.php?story_fbid=444444&id=999999">4d</a></article></aside>''')
        with self.assertRaises(DetailReadError) as error:
            await published_details.preview_identity(self.page,'facebook','story')
        self.assertEqual(error.exception.code,'unsupported_type')
        self.assertEqual(error.exception.placement,'story')
        feed = await published_details.preview_identity(self.page,'facebook','feed')
        self.assertEqual(feed['remote_id'],'')

    async def test_shared_panels_and_outside_ancestors_cannot_supply_channel_metadata(self):
        await self.page.set_content('''<header><h2>Old caption</h2></header>
          <button role="tab" id="fb" aria-selected="true" aria-controls="view">Facebook</button>
          <button role="tab" id="ig" aria-selected="false" aria-controls="view">Instagram</button>
          <div role="tabpanel" id="view" aria-labelledby="fb"><span>Post · Published on: Fri Sep 4, 6:39pm</span></div>''')
        with self.assertRaises(DetailReadError) as error:
            await published_details.read_view(self.page,{'date':date(2026,9,4)},{'time':'6:39 PM'},
                {'facebook':'Neakasa Deutschland'},channel='facebook',aggregate=True,timeout=.5)
        self.assertEqual(error.exception.missing_fields,('channel_detail_scope',))
        scoped = await published_details.header_snapshot(self.page.get_by_role('tabpanel'))
        self.assertIsNone(scoped['caption'])

    async def test_unknown_and_inaccessible_details_remain_visible_in_a_partial_month(self):
        for index in (5,6):
            await self.page.locator(month.DAY_SELECTOR).nth(index).evaluate('''(el,index)=>el.insertAdjacentHTML(
              'beforeend',`<a href="https://business.facebook.com/latest/insights/object_insights/?content_id=123456${index}">6:39 PM</a>`)''',index)
        await self.context.route('https://business.facebook.com/**', lambda route: route.fulfill(
            content_type='text/html; charset=utf-8', body=('''<header><h2>Private caption</h2>
              <span>New content kind · Published on: Fri Sep 4, 6:39pm</span></header>'''
              if '1234565' in route.request.url else '<p>This content isn’t available.</p>')))
        with patch.object(month, 'prepare', AsyncMock()):
            result = await month.read(self.page, ui_timezone='Asia/Shanghai', business_timezone='Asia/Shanghai', timeout=3)
        self.assertTrue(result.cards_loaded)
        self.assertFalse(result.decision_complete)
        self.assertEqual(len(result.cards),2)
        self.assertEqual([d['code'] for d in result.diagnostics], ['unsupported_type','permission_denied'])
        self.assertNotIn('Private caption', str(result.diagnostics))
        with self.assertRaises(ProbeRequired):
            result.occupied_for_channel('facebook')

    async def test_published_detail_waits_for_caption_author_and_platform_but_not_metrics(self):
        await self.page.locator(month.DAY_SELECTOR).nth(31).evaluate('''el=>el.insertAdjacentHTML(
          'beforeend','<a href="https://business.facebook.com/latest/insights/object_insights/?content_id=12345678">5:30 PM</a>')''')
        await self.page.context.route('https://business.facebook.com/**', lambda route: route.fulfill(
            content_type='text/html; charset=utf-8', body='''<header><div><span>Post · Published on: Wed Sep 30, 5:30pm</span></div>
              <h3></h3></header><aside><h2>Feed preview</h2><div id="preview"></div></aside>
              <div role="progressbar">Metrics still loading</div><script>
              setTimeout(()=>{document.querySelector('h3').textContent='Complete caption';
                document.querySelector('span').parentElement.insertAdjacentHTML('beforeend','<img alt="Facebook">');
                preview.innerHTML='<h2><a href="https://www.facebook.com/profile.php?id=123456">Neakasa Deutschland</a></h2><a href="https://www.facebook.com/permalink.php?story_fbid=12345678&id=123456">4d</a>';
              },500);
              </script>'''))
        rows = await month.read_grid(self.page, timeout=5)
        with patch.object(month, 'accounts', return_value={'facebook': 'Neakasa Deutschland'}):
            result = await month.read_item(self.page, rows[31], rows[31]['items'][0], timeout=4)
        self.assertEqual(result['variants'][0]['text'], 'Complete caption')
        self.assertEqual(result['variants'][0]['remote_ids'], {'facebook': '12345678'})


if __name__ == '__main__':
    unittest.main()
