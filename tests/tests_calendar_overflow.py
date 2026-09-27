"""Recorded month overflow links are controls, never posts or invented times."""
import calendar
import asyncio
import json
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.config import Config
from publish import month_inventory as month, month_readback
from tests_month_inventory import SPEC


class WeekDatesTests(unittest.TestCase):
    def test_week_dates_use_the_complete_month_not_the_ambiguous_range_heading(self):
        for year, number in ((2026, 9), (2026, 12), (2027, 1)):
            dates = list(calendar.Calendar(firstweekday=6).itermonthdates(year, number))
            rows = [{'date': day} for day in dates]
            for offset in range(0, len(rows), 7):
                expected = tuple(dates[offset:offset+7])
                self.assertEqual(month.week_dates(rows, [day.day for day in expected]), expected)

    def test_partial_or_mismatched_week_cannot_be_assigned_dates(self):
        rows = [{'date': day} for day in calendar.Calendar(firstweekday=6).itermonthdates(2026, 9)]
        for numbers in ([27, 28, 29, 30, 1, 2], [27, 28, 29, 30, 2, 3, 4], []):
            with self.subTest(numbers=numbers), self.assertRaises(month.bs.PublishStepError):
                month.week_dates(rows, numbers)

    def test_expanded_day_requires_hidden_count_and_all_visible_entries(self):
        def item(clock):
            return {'time': clock, 'href': ''}
        row = {'date': date(2026, 9, 30), 'items': [item('5:30 PM'), item('8:00 PM')], 'hidden_count': 1}
        complete = {'date': row['date'], 'items': [*row['items'], item('11:00 PM')]}
        month.require_expanded_day(row, complete)
        for changed in (
                {'date': date(2026, 10, 1), 'items': complete['items']},
                {'date': row['date'], 'items': row['items']},
                {'date': row['date'], 'items': [*row['items'], item('8:00 PM'), item('11:00 PM')]},
                {'date': row['date'], 'items': [item('5:30 PM'), item('11:00 PM'), item('11:00 PM')]}):
            with self.subTest(changed=changed), self.assertRaises(month.bs.PublishStepError):
                month.require_expanded_day(row, changed)

    def test_view_tracking_parameters_do_not_change_a_published_object_identity(self):
        row = {'date': date(2026, 9, 30), 'items': [{'time': '5:30 PM',
            'href': '/latest/insights/object_insights/?content_id=123456789&nav_ref=monthly'}]}
        weekly = {'date': row['date'], 'items': [{'time': '5:30 PM',
            'href': '/latest/insights/object_insights/?content_id=123456789&nav_ref=weekly'}]}
        month.require_expanded_day(row, weekly)
        weekly['items'][0]['href'] = weekly['items'][0]['href'].replace('123456789', '987654321')
        with self.assertRaises(month.bs.PublishStepError):
            month.require_expanded_day(row, weekly)


class OverflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pw = await async_playwright().start()
        self.addAsyncCleanup(self.pw.stop)
        self.browser = await self.pw.chromium.launch(executable_path=Config().chrome_exe, headless=True)
        self.addAsyncCleanup(self.browser.close)
        self.page = await self.browser.new_page()
        cells = []
        for day in calendar.Calendar(firstweekday=6).itermonthdates(2026, 9):
            cards = ('<div role="link"><div role="link">5:30 PM<img alt="Facebook"></div></div>'
                     '<div role="link"><div role="link">8:00 PM<img alt="Instagram"></div></div>'
                     '<a role="link" href="#">+ 1 more</a>') if day == date(2026, 9, 30) else ''
            cells.append(f'<div role="link" draggable="false"><span>{day.day}</span>{cards}</div>')
        await self.page.set_content('<h1>Planner</h1><h1>September</h1><h1>2026</h1>' + ''.join(cells))

    async def mount_week_view(self, *, missing=False, mutate=False, count=3, navigate_on_close=False):
        entries = [
            {'time': '5:30 PM', 'platform': 'Facebook', 'id': '1884787296017457', 'caption': 'Riko full caption. #Riko'},
            {'time': '8:00 PM', 'platform': 'Instagram', 'id': '1099867215965804', 'caption': 'Tag 1 auf der @ifa.berlin ✨\nBis morgen! 👋 #Berlin'},
            {'time': '11:00 PM', 'platform': 'Facebook', 'id': '1084557747316275', 'caption': 'Nur noch 7 Tage! 🐱✨ Full caption.'},
            {'time': '11:01 PM', 'platform': 'Instagram', 'id': '1000000000000004', 'caption': 'Fourth full caption at 5:30 PM.'},
        ][:count]
        days = [day.day for day in calendar.Calendar(firstweekday=6).itermonthdates(2026, 9)]
        markup = '''<h1>Planner</h1><h1 id="month">September</h1><h1>2026</h1>
          <button id="week">Week</button><button id="monthly">Month</button>
          <button id="left">Left</button><button id="right">Right</button>
          <button>Content type: all</button><button>Shared to: all</button>
          <div id="headers"></div><div id="grid"></div><div id="details"></div>'''
        script = '''data=>{
          window.opened=[];window.writes=[];window.mode='month';window.offset=0;
          window.entries=data.entries;
          const esc=s=>s.replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;');
          window.render=()=>{
            const weekly=mode==='week';
            const numbers=weekly ? (offset ? [27,28,29,30,1,2,3] : [20,21,22,23,24,25,26]) : data.days;
            document.getElementById('month').textContent=weekly ? 'Sep - Oct' : 'September';
            document.getElementById('headers').innerHTML=weekly ?
              numbers.map((n,i)=>'<span>'+['Sun','Mon','Tue','Wed','Thu','Fri','Sat'][i]+' '+n+'</span> ').join('') : '';
            document.getElementById('grid').innerHTML=numbers.map((n,i)=>{
              const target=weekly ? offset && n===30 : i===31;
              let body='';
              if(target) body=entries.slice(0, weekly ? (data.missing?2:entries.length) : 2).map((e,index)=>
                weekly ? '<div aria-label="'+esc(e.caption)+'"><div draggable="true"><div><div role="link" tabindex="0" data-item="'+index+'">'+e.time+'<img alt="'+e.platform+'"></div></div></div></div>' :
                '<div role="link"><div role="link">'+e.time+'<img alt="'+e.platform+'"></div></div>').join('');
              if(target && !weekly && entries.length>2) body+='<a role="link" href="#">+ '+(entries.length-2)+' more</a>';
              if(!weekly && n===28) body='<div role="link">1:00 AM<img alt="Instagram"></div>';
              if(weekly && offset && n===28) body='<div role="link" draggable="false"><div><span>1:00 AM</span></div><div><img alt="Instagram"></div><span>This week, your Instagram followers are most active at this time.</span><div><button>Schedule</button></div></div>';
              return '<div role="link" draggable="false">'+(weekly?'':'<span>'+n+'</span>')+'<div>'+body+'</div></div>';
            }).join('');
            document.querySelectorAll('[data-item]').forEach(n=>n.onclick=e=>{
              e.stopPropagation(); const entry=entries[+n.dataset.item];opened.push(entry.id);
              document.getElementById('details').innerHTML='<div role="dialog" aria-label="Post details">ID: '+entry.id+
                (entry.platform==='Instagram' ?
                  '<p>This view of your post may not represent exactly how it appears on your Instagram feed.</p><div>neakasa.de</div><div><span>neakasa.de</span><span></span><span>'+esc(entry.caption)+'</span></div>' :
                  "<p>This view may not represent Facebook's Feed.</p><article><h2>Neakasa Deutschland</h2><div>"+esc(entry.caption)+"</div></article>")+
                '<button id="close">Close</button><button>Boost</button><button>Publish now</button></div>';
              document.querySelectorAll('#details button:not(#close)').forEach(b=>b.onclick=()=>writes.push(b.innerText));
              document.getElementById('close').onclick=()=>{
                document.getElementById('details').innerHTML='';
                if(data.mutate){entries[2].caption='Changed during detail';render();}
                if(data.navigate_on_close){
                  history.pushState({},'', '/latest/composer/?asset_id=111222333444');
                  if(data.navigate_on_close==='replace_body')
                    document.body.innerHTML='<h1>Create post</h1><div role="dialog" aria-label="Schedule post"><button>Save</button><button>Schedule</button></div>';
                }
              };
            });
            document.querySelectorAll('#grid button,#grid a').forEach(n=>n.onclick=e=>{e.preventDefault();writes.push(n.innerText)});
          };
          document.getElementById('week').onclick=()=>{mode='week';offset=0;render()};
          document.getElementById('monthly').onclick=()=>{mode='month';render()};
          document.getElementById('right').onclick=()=>{offset++;render()};
          document.getElementById('left').onclick=()=>{offset--;render()};
          render();
        }'''
        data = {'days': days, 'entries': entries, 'missing': missing, 'mutate': mutate,
                'navigate_on_close': navigate_on_close}
        self.week_html = markup + '<script>(' + script + ')(' + json.dumps(data) + ')</script>'
        await self.page.set_content(self.week_html)
        return entries

    async def inventory(self, *, whole_month=False):
        when = datetime(2026, 9, 30, 20, tzinfo=ZoneInfo('Asia/Shanghai'))
        with patch.object(month, 'prepare', AsyncMock()), \
                patch.object(month.bs, 'require_readback_evidence', return_value=SPEC), \
                patch.object(month, 'recommendation_state', AsyncMock(return_value='absent')):
            return await month.read(self.page, ui_timezone='Asia/Shanghai', business_timezone='Asia/Shanghai',
                timeout=5, detail_range=None if whole_month else (when, when))

    async def test_week_view_recovers_two_three_and_four_cards_with_separate_captions_and_ids(self):
        for count in (2, 3, 4):
            with self.subTest(count=count):
                entries = await self.mount_week_view(count=count)
                inv = await self.inventory()
                self.assertEqual(inv.diagnostics, ())
                self.assertEqual([card.rendered for card in inv.cards], [month.bs._card_text(e['caption']) for e in entries])
                self.assertEqual([dict(card.remote_ids)[card.channels[0]] for card in inv.cards], [e['id'] for e in entries])
                self.assertTrue(all(card.time_verified and card.read_status=='complete' for card in inv.cards))
                self.assertEqual(await self.page.evaluate('opened'), [e['id'] for e in entries])
                self.assertEqual(await self.page.evaluate('writes'), [])
                self.assertEqual(await self.page.evaluate('mode'), 'month')

    async def test_week_detail_waits_for_caption_after_id_and_owner_before_closing(self):
        entries = await self.mount_week_view()
        await self.page.evaluate('''() => {
          const base=render;
          render=()=>{base();
            const entry=document.querySelector('[data-item="1"]');
            if(!entry)return;
            const open=entry.onclick;
            entry.onclick=e=>{open(e);
              const dialog=document.querySelector('#details [role="dialog"]');
              const body=[...dialog.querySelectorAll('span')].find(n=>n.textContent.includes('Tag 1 auf'));
              body.textContent='';
              window.previewReadyAt=0;window.detailClosedAt=0;
              const close=dialog.querySelector('#close'), oldClose=close.onclick;
              close.onclick=()=>{detailClosedAt=performance.now();oldClose()};
              setTimeout(()=>{body.textContent='Tag 1 auf der @ifa.berlin ✨'},250);
              setTimeout(()=>{body.textContent='Tag 1 auf der @ifa.berlin ✨ Bis morgen! 👋 #Berlin';
                previewReadyAt=performance.now()},2000);
            };
          };
        }''')
        await self.page.get_by_role('button', name='Week').click()
        await self.page.get_by_role('button', name='Right').click()
        row = {'date': date(2026, 9, 30), 'view': 'week', 'cell_index': 3}
        item = {'href': '', 'labels': [entries[1]['caption']], 'icons': ['Instagram'], 'time': '8:00 PM'}
        detail = await month.read_item_detail(self.page, row, item, self.page.locator('[data-item="1"]'), '',
                                              timeout=3, card_spec=SPEC)
        times = await self.page.evaluate('({ready:previewReadyAt, closed:detailClosedAt})')
        self.assertEqual(detail['remote_ids'], {'instagram': entries[1]['id']})
        self.assertGreater(times['ready'], 0)
        self.assertGreaterEqual(times['closed'], times['ready'])

    async def test_facebook_week_detail_waits_for_delayed_article_text(self):
        entries = await self.mount_week_view()
        await self.page.evaluate('''() => {
          const base=render;
          render=()=>{base();
            const entry=document.querySelector('[data-item="0"]');
            if(!entry)return;
            const open=entry.onclick;
            entry.onclick=e=>{open(e);
              const dialog=document.querySelector('#details [role="dialog"]');
              const body=dialog.querySelector('article > div');
              body.textContent='';
              window.previewReadyAt=0;window.detailClosedAt=0;
              const close=dialog.querySelector('#close'), oldClose=close.onclick;
              close.onclick=()=>{detailClosedAt=performance.now();oldClose()};
              setTimeout(()=>{body.textContent='Riko full caption. #Riko';
                previewReadyAt=performance.now()},1200);
            };
          };
        }''')
        await self.page.get_by_role('button', name='Week').click()
        await self.page.get_by_role('button', name='Right').click()
        row = {'date': date(2026, 9, 30), 'view': 'week', 'cell_index': 3}
        item = {'href': '', 'labels': [entries[0]['caption']], 'icons': ['Facebook'], 'time': '5:30 PM'}
        detail = await month.read_item_detail(self.page, row, item, self.page.locator('[data-item="0"]'), '',
                                              timeout=3, card_spec=SPEC)
        times = await self.page.evaluate('({ready:previewReadyAt, closed:detailClosedAt})')
        self.assertEqual(detail['remote_ids'], {'facebook': entries[0]['id']})
        self.assertGreater(times['ready'], 0)
        self.assertGreaterEqual(times['closed'], times['ready'])

    async def test_week_detail_accepts_stable_collapsed_preview_without_opening_more(self):
        entries = await self.mount_week_view()
        await self.page.evaluate('''() => {
          const base=render;
          render=()=>{base();
            const entry=document.querySelector('[data-item="1"]');
            if(!entry)return;
            const open=entry.onclick;
            entry.onclick=e=>{open(e);
              const dialog=document.querySelector('#details [role="dialog"]');
              const body=[...dialog.querySelectorAll('span')].find(n=>n.textContent.includes('Tag 1 auf'));
              body.innerHTML='Tag 1 auf der @ifa.berlin ✨... <button>more</button>';
              window.detailOpenedAt=performance.now();window.detailClosedAt=0;
              const close=dialog.querySelector('#close'), oldClose=close.onclick;
              close.onclick=()=>{detailClosedAt=performance.now();oldClose()};
            };
          };
        }''')
        await self.page.get_by_role('button', name='Week').click()
        await self.page.get_by_role('button', name='Right').click()
        row = {'date': date(2026, 9, 30), 'view': 'week', 'cell_index': 3}
        item = {'href': '', 'labels': [entries[1]['caption']], 'icons': ['Instagram'], 'time': '8:00 PM'}
        detail = await month.read_item_detail(self.page, row, item, self.page.locator('[data-item="1"]'), '',
                                              timeout=4, card_spec=SPEC)
        elapsed = await self.page.evaluate('detailClosedAt-detailOpenedAt')
        self.assertEqual(detail['remote_ids'], {'instagram': entries[1]['id']})
        self.assertGreaterEqual(elapsed, 1000)
        self.assertEqual(await self.page.evaluate('writes'), [])

    async def test_week_detail_without_preview_caption_stays_unverified(self):
        entries = await self.mount_week_view()
        await self.page.evaluate('''() => {
          const base=render;
          render=()=>{base();
            const entry=document.querySelector('[data-item="1"]');
            if(!entry)return;
            const open=entry.onclick;
            entry.onclick=e=>{open(e);
              const dialog=document.querySelector('#details [role="dialog"]');
              const body=[...dialog.querySelectorAll('span')].find(n=>n.textContent.includes('Tag 1 auf'));
              body.textContent='';
            };
          };
        }''')
        await self.page.get_by_role('button', name='Week').click()
        await self.page.get_by_role('button', name='Right').click()
        row = {'date': date(2026, 9, 30), 'view': 'week', 'cell_index': 3}
        item = {'href': '', 'labels': [entries[1]['caption']], 'icons': ['Instagram'], 'time': '8:00 PM'}
        with self.assertRaises(month.content.DetailReadError) as failure:
            await month.read_item_detail(self.page, row, item, self.page.locator('[data-item="1"]'), '',
                                         timeout=1.2, card_spec=SPEC)
        self.assertIn('preview_caption', failure.exception.missing_fields)
        self.assertEqual(await self.page.get_by_role('dialog').count(), 0)

    async def test_week_card_reordering_between_read_and_click_cannot_open_composer(self):
        entries = await self.mount_week_view()
        await self.page.get_by_role('button', name='Week').click()
        await self.page.get_by_role('button', name='Right').click()
        days = tuple(date(2026, 9, day) for day in (27, 28, 29, 30)) + tuple(
            date(2026, 10, day) for day in (1, 2, 3))
        row = (await month.read_days(self.page, days, timeout=5, week=True))[3]
        item = row['items'][1]
        original = month.bs._open_channel_dialogs

        async def insert_before_click(page, node, spec, **kwargs):
            await page.evaluate('''() => {
              const target=document.querySelector('[data-item="1"]');
              const decoy=document.createElement('div');
              decoy.setAttribute('role','link');
              decoy.innerHTML='8:00 PM<img alt="Instagram">';
              decoy.onclick=()=>{
                history.pushState({},'', '/latest/composer/');
                document.body.innerHTML='<h1>Create post</h1>';
              };
              target.parentElement.insertBefore(decoy,target);
            }''')
            return await original(page, node, spec, **kwargs)

        with patch.object(month.bs, '_open_channel_dialogs', insert_before_click):
            result = await month.read_item(self.page, row, item, timeout=5, card_spec=SPEC)
        self.assertEqual(result['remote_ids'], {'instagram': entries[1]['id']})
        self.assertNotIn('/latest/composer/', self.page.url)
        self.assertEqual(await self.page.evaluate('opened'), [entries[1]['id']])

    async def test_opening_detail_composer_recovers_original_week_and_retries_once(self):
        await self.mount_week_view()
        html = self.week_html.replace(
            "document.querySelectorAll('[data-item]').forEach(n=>n.onclick=e=>{",
            """document.querySelectorAll('[data-item]').forEach(n=>n.onclick=e=>{
              if(n.dataset.item==='1' && !sessionStorage.getItem('opened_ig_once')) {
                e.stopPropagation();sessionStorage.setItem('opened_ig_once','1');
                history.pushState({},'', '/latest/composer/?asset_id=111222333444');
                document.body.innerHTML='<h1>Create post</h1><div role="dialog" aria-label="Schedule post"><button>Save</button><button>Schedule</button></div>';
                return;
              }
            """, 1) + '''<script>document.addEventListener('keydown',e=>{
              const n=e.target.closest('[data-item]');
              if(n && e.key==='Enter') {
                e.preventDefault();
                sessionStorage.setItem('keyboard_open', '1');
                n.click();
              }
            });</script>'''
        await self.page.route('https://business.facebook.com/**', lambda route: route.fulfill(
            content_type='text/html', body=html))
        await self.page.goto('https://business.facebook.com/latest/content_calendar?asset_id=111222333444&business_id=555666777888')
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(month, 'cfg', return_value=SimpleNamespace(state_dir=Path(folder))):
            inv = await self.inventory()
            self.assertEqual(inv.diagnostics, ())
            self.assertEqual([dict(c.remote_ids)[c.channels[0]] for c in inv.cards],
                             ['1884787296017457', '1099867215965804', '1084557747316275'])
            self.assertEqual(await self.page.evaluate('opened'),
                             ['1099867215965804', '1084557747316275'])
            self.assertEqual(await self.page.evaluate("sessionStorage.getItem('opened_ig_once')"), '1')
            self.assertEqual(await self.page.evaluate("sessionStorage.getItem('keyboard_open')"), '1')
            self.assertEqual(await self.page.evaluate('writes'), [])
            saved = [json.loads(p.read_text('utf-8')) for p in (Path(folder) / 'planner_diagnostics').glob('*.json')]
            self.assertEqual(len(saved), 1)
            self.assertTrue(saved[0]['recovered'])
            self.assertEqual(saved[0]['failures'][0]['phase'], 'opening_detail')

    async def test_ig_detail_waits_for_transient_composer_route_to_return_to_original_grid(self):
        entries = await self.mount_week_view()
        html = self.week_html.replace(
            "document.querySelectorAll('#details button:not(#close)').forEach",
            """if(entry.id==='1099867215965804') {
                const original=location.pathname+location.search;
                history.pushState({},'', '/latest/composer/?asset_id=111222333444');
                setTimeout(()=>history.replaceState({},'',original),300);
              }
              document.querySelectorAll('#details button:not(#close)').forEach""", 1)
        await self.page.route('https://business.facebook.com/**', lambda route: route.fulfill(
            content_type='text/html', body=html))
        await self.page.goto('https://business.facebook.com/latest/content_calendar?asset_id=111222333444&business_id=555666777888')
        when = datetime(2026, 9, 30, 20, tzinfo=ZoneInfo('Asia/Shanghai'))
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(month, 'cfg', return_value=SimpleNamespace(state_dir=Path(folder))), \
                patch.object(month, 'prepare', AsyncMock()), \
                patch.object(month.bs, 'require_readback_evidence', return_value=SPEC), \
                patch.object(month, 'recommendation_state', AsyncMock(return_value='absent')), \
                patch.object(month.bs, '_readback_screenshot', AsyncMock(return_value='')):
            result = await month_readback.verify(self.page, when, entries[1]['caption'],
                ui_timezone='Asia/Shanghai', target_channels=('instagram',), timeout=5)
            self.assertTrue(result.found, result.error)
            self.assertEqual(result.remote_id, 'instagram=1099867215965804')
            self.assertEqual(result.diagnostics['inventory_evidence']['target_cards'][0]['channels'], ['instagram'])
            self.assertEqual(await self.page.evaluate('opened'), [e['id'] for e in entries])
            self.assertEqual(await self.page.evaluate('writes'), [])
            self.assertEqual(await self.page.evaluate('mode'), 'month')

    async def test_ig_detail_uses_card_click_without_the_outer_day_composer_action(self):
        entries = await self.mount_week_view()
        html = self.week_html.replace(
            'e.stopPropagation(); const entry=entries[+n.dataset.item];',
            "if(n.dataset.item!=='1') e.stopPropagation(); const entry=entries[+n.dataset.item];", 1)
        html += '''<script>
          const priorRender=render;
          render=()=>{priorRender();
            const cards=[...document.querySelectorAll('[data-item]')];if(!cards.length)return;
            const day=cards[1].closest('[role="link"][draggable="false"]');
            const key='__reactProps$fixture';
            cards.forEach(card=>card[key]={onClick:card.onclick});
            day[key]={onClick:e=>{
              history.pushState({},'', '/latest/composer/?asset_id=111222333444');
              document.body.innerHTML='<h1>Create post</h1>';
            }};
            day.onclick=e=>day[key].onClick(e);
          };
          render();
        </script>'''
        await self.page.route('https://business.facebook.com/**', lambda route: route.fulfill(
            content_type='text/html', body=html))
        await self.page.goto('https://business.facebook.com/latest/content_calendar?asset_id=111222333444&business_id=555666777888')
        when = datetime(2026, 9, 30, 20, tzinfo=ZoneInfo('Asia/Shanghai'))
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(month, 'cfg', return_value=SimpleNamespace(state_dir=Path(folder))), \
                patch.object(month, 'prepare', AsyncMock()), \
                patch.object(month.bs, 'require_readback_evidence', return_value=SPEC), \
                patch.object(month, 'recommendation_state', AsyncMock(return_value='absent')), \
                patch.object(month.bs, '_readback_screenshot', AsyncMock(return_value='')):
            result = await month_readback.verify(self.page, when, entries[1]['caption'],
                ui_timezone='Asia/Shanghai', target_channels=('instagram',), timeout=5)
            self.assertTrue(result.found, result.error)
            self.assertEqual(result.remote_id, 'instagram=1099867215965804')
            self.assertEqual(await self.page.evaluate('opened'), [e['id'] for e in entries])
            self.assertEqual(await self.page.evaluate('writes'), [])
            self.assertEqual(await self.page.evaluate('mode'), 'month')

    async def test_existing_ig_receipt_reads_the_other_facebook_card_without_day_navigation(self):
        entries = await self.mount_week_view()
        html = self.week_html.replace(
            'e.stopPropagation(); const entry=entries[+n.dataset.item];',
            "if(n.dataset.item!=='2') e.stopPropagation(); const entry=entries[+n.dataset.item];", 1)
        html += '''<script>
          const priorRender=render;
          render=()=>{priorRender();
            const cards=[...document.querySelectorAll('[data-item]')];
            const day=cards[2].closest('[role="link"][draggable="false"]');
            const key='__reactProps$fixture';
            const open=cards[2].onclick;
            cards[2].onclick=e=>{
              open(e);
              const caption=document.querySelector('#details article > div');
              caption.textContent='';
              setTimeout(()=>caption.textContent=window.entries[2].caption,1100);
            };
            cards.forEach(card=>card[key]={onClick:card.onclick});
            day[key]={onClick:e=>{
              setTimeout(()=>{
                history.pushState({},'', '/latest/composer/?asset_id=111222333444');
                document.body.innerHTML='<h1>Create post</h1>';
              },600);
            }};
            day.onclick=e=>day[key].onClick(e);
          };
          render();
        </script>'''
        await self.page.route('https://business.facebook.com/**', lambda route: route.fulfill(
            content_type='text/html', body=html))
        await self.page.goto('https://business.facebook.com/latest/content_calendar?asset_id=111222333444&business_id=555666777888')
        when = datetime(2026, 9, 30, 20, tzinfo=ZoneInfo('Asia/Shanghai'))
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(month, 'cfg', return_value=SimpleNamespace(state_dir=Path(folder))), \
                patch.object(month, 'prepare', AsyncMock()), \
                patch.object(month.bs, 'require_readback_evidence', return_value=SPEC), \
                patch.object(month, 'recommendation_state', AsyncMock(return_value='absent')), \
                patch.object(month.bs, '_readback_screenshot', AsyncMock(return_value='')):
            result = await month_readback.verify(self.page, when, entries[1]['caption'],
                ui_timezone='Asia/Shanghai', target_channels=('instagram',), timeout=5)
            self.assertTrue(result.found, result.error)
            self.assertEqual(result.remote_id, 'instagram=1099867215965804')
            self.assertEqual(await self.page.evaluate('opened'), [e['id'] for e in entries])
            self.assertEqual(await self.page.evaluate('writes'), [])
            self.assertEqual(await self.page.evaluate('mode'), 'month')

    async def test_opening_detail_stops_after_second_composer_navigation(self):
        await self.mount_week_view()
        html = self.week_html.replace(
            "document.querySelectorAll('[data-item]').forEach(n=>n.onclick=e=>{",
            """document.querySelectorAll('[data-item]').forEach(n=>n.onclick=e=>{
              if(n.dataset.item==='1') {
                e.stopPropagation();
                sessionStorage.setItem('ig_open_count',
                  String(Number(sessionStorage.getItem('ig_open_count')||0)+1));
                history.pushState({},'', '/latest/composer/?asset_id=111222333444');
                document.body.innerHTML='<h1>Create post</h1><div role="dialog" aria-label="Schedule post"><button>Save</button><button>Schedule</button></div>';
                return;
              }
            """, 1) + '''<script>document.addEventListener('keydown',e=>{
              const n=e.target.closest('[data-item]');
              if(n && e.key==='Enter') {e.preventDefault();n.click()}
            });</script>'''
        await self.page.route('https://business.facebook.com/**', lambda route: route.fulfill(
            content_type='text/html', body=html))
        await self.page.goto('https://business.facebook.com/latest/content_calendar?asset_id=111222333444&business_id=555666777888')
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(month, 'cfg', return_value=SimpleNamespace(state_dir=Path(folder))):
            with self.assertRaises(month.bs.PlannerNavigationError):
                await self.inventory()
            self.assertEqual(await self.page.evaluate("sessionStorage.getItem('ig_open_count')"), '2')
            saved = [json.loads(p.read_text('utf-8')) for p in (Path(folder) / 'planner_diagnostics').glob('*.json')]
            self.assertEqual(len(saved), 2)
            final = next(record for record in saved if not record['recovered'])
            self.assertEqual(final['failures'][0]['phase'], 'opening_detail')
            self.assertEqual(final['failures'][0]['activation'], 'keyboard_retry')
            self.assertEqual(final['failures'][0]['time'], '8:00 PM')
            self.assertEqual(final['failures'][0]['view'], 'week')

    async def test_month_view_returns_after_a_non_dialog_overlay_blocks_pointer(self):
        await self.mount_week_view()
        await self.page.evaluate('''() => {
          const oldRender=render;
          render=()=>{oldRender();
            const last=document.querySelector('[data-item="2"]');
            if(!last)return;
            const oldOpen=last.onclick;
            last.onclick=e=>{oldOpen(e);
              const close=document.querySelector('#close'),oldClose=close.onclick;
              close.onclick=()=>{oldClose();
                const button=document.getElementById('monthly');
                const r=button.getBoundingClientRect();
                const blocker=document.createElement('div');
                blocker.id='month-pointer-blocker';
                blocker.style=`position:fixed;left:${r.left}px;top:${r.top}px;width:${r.width}px;height:${r.height}px;z-index:9999`;
                document.body.append(blocker);
              };
            };
          };
        }''')
        inv = await self.inventory()
        self.assertEqual(inv.diagnostics, ())
        self.assertEqual(await self.page.evaluate('mode'), 'month')
        self.assertEqual(await self.page.locator('#month-pointer-blocker').count(), 1)
        self.assertEqual(await self.page.evaluate('writes'), [])

    async def test_calendar_open_uses_the_month_button_with_a_pointer_overlay(self):
        await self.mount_week_view()
        html = self.week_html + '''<script>
          const blocker=document.createElement('div');blocker.id='month-pointer-blocker';
          blocker.style='position:fixed;inset:0;z-index:9999';
          document.body.append(blocker);
        </script>'''
        await self.page.route('https://business.facebook.com/**', lambda route: route.fulfill(
            content_type='text/html', body=html))
        await month.open_calendar(self.page, {'asset_id': '111222333444', 'business_id': '555666777888'}, timeout=3)
        self.assertEqual(await self.page.evaluate('mode'), 'month')
        self.assertEqual(await self.page.locator('#month-pointer-blocker').count(), 1)
        self.assertEqual(await self.page.evaluate('writes'), [])

    async def test_month_view_does_not_activate_behind_an_open_detail_dialog(self):
        await self.mount_week_view()
        await self.page.get_by_role('button', name='Week').click()
        await self.page.evaluate('''() => {
          document.getElementById('details').innerHTML =
            '<div role="dialog" aria-label="Post details"><button>Publish now</button></div>';
        }''')
        with self.assertRaises(month.bs.PlannerDialogCloseError):
            await month.select_month_view(self.page, timeout=2,
                calendar_url=self.page.url, phase='return_to_month')
        self.assertEqual(await self.page.evaluate('mode'), 'week')
        self.assertEqual(await self.page.evaluate('writes'), [])

    async def test_week_recommendation_is_not_an_eighth_day_or_a_post(self):
        await self.mount_week_view()
        inv = await self.inventory(whole_month=True)
        self.assertEqual(len(inv.cards), 3)
        self.assertEqual(inv.diagnostics, ())
        self.assertTrue(inv.decision_complete)
        self.assertEqual(await self.page.evaluate('writes'), [])

    async def test_folded_day_switches_to_complete_week_through_toolbar_overlay(self):
        await self.mount_week_view()
        covered = await self.page.evaluate('''() => {
          const button = document.getElementById('week');
          const box = button.getBoundingClientRect();
          const overlay = document.createElement('div');
          overlay.setAttribute('aria-hidden', 'true');
          Object.assign(overlay.style, {position: 'absolute', zIndex: '1000',
            left: box.left + window.scrollX + 'px', top: box.top + window.scrollY + 'px', width: box.width + 'px',
            height: box.height + 'px', pointerEvents: 'auto'});
          document.body.appendChild(overlay);
          return document.elementFromPoint(box.left + box.width/2, box.top + box.height/2) === overlay;
        }''')
        self.assertTrue(covered)
        inv = await self.inventory(whole_month=True)
        self.assertEqual([card.at.hour for card in inv.cards], [17, 20, 23])
        self.assertEqual(inv.diagnostics, ())
        self.assertTrue(inv.decision_complete)
        self.assertEqual(await self.page.evaluate('mode'), 'month')
        self.assertEqual(await self.page.evaluate('writes'), [])

    async def test_close_navigation_restores_and_rechecks_the_same_week_without_reopening_details(self):
        visits, opened = [], []
        async def serve(route):
            visits.append(route.request.url)
            await route.fulfill(content_type='text/html', body=self.week_html)
        await self.page.route('https://business.facebook.com/**', serve)
        await self.mount_week_view(navigate_on_close='replace_body')
        await self.page.goto('https://business.facebook.com/latest/content_calendar?asset_id=111222333444&business_id=555666777888')
        await self.page.expose_function('recordOpened', lambda value: opened.append(value))
        await self.page.add_init_script("addEventListener('click',e=>{const n=e.target.closest('[data-item]');if(n)recordOpened(entries[+n.dataset.item].id)},true)")
        await self.page.reload()
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(month, 'cfg', return_value=SimpleNamespace(state_dir=Path(folder))):
            inv = await self.inventory()
            self.assertFalse(inv.diagnostics)
            self.assertEqual([dict(c.remote_ids)[c.channels[0]] for c in inv.cards],
                             ['1884787296017457', '1099867215965804', '1084557747316275'])
            self.assertEqual(opened, ['1884787296017457', '1099867215965804', '1084557747316275'])
            self.assertEqual(len(visits), 5)  # initial load/reload, then one restoration per completed detail
            self.assertTrue(all('/content_calendar?' in url for url in visits))
            self.assertEqual(await self.page.evaluate('mode'), 'month')
            self.assertEqual(await self.page.evaluate('writes'), [])
            saved = [json.loads(p.read_text('utf-8')) for p in (Path(folder) / 'planner_diagnostics').glob('*.json')]
            self.assertEqual(len(saved), 3)
            self.assertTrue(all(data['recovered'] for data in saved))
            self.assertTrue(all(data['failures'][0]['phase'] == 'after_detail_close' for data in saved))
            self.assertTrue(all(data['failures'][0]['surface'] == 'composer' for data in saved))
            self.assertNotIn('111222333444', json.dumps(saved))

    async def test_composer_address_with_week_grid_recovers_after_preview_text_changes(self):
        await self.mount_week_view(navigate_on_close='url_only')
        await self.page.route('https://business.facebook.com/**', lambda route: route.fulfill(
            content_type='text/html', body=self.week_html))
        await self.page.goto('https://business.facebook.com/latest/content_calendar?asset_id=111222333444&business_id=555666777888')
        original = month.scheduled_details.verify_preview_owner

        async def preview_changes(dialog, ids, expected_accounts, *, timeout):
            await original(dialog, ids, expected_accounts, timeout=timeout)
            if 'instagram' in ids:
                await dialog.evaluate("el => { const n=document.createElement('span'); n.textContent='preview settled'; el.append(n) }")

        with tempfile.TemporaryDirectory() as folder, \
                patch.object(month, 'cfg', return_value=SimpleNamespace(state_dir=Path(folder))), \
                patch.object(month.scheduled_details, 'verify_preview_owner', preview_changes):
            inv = await self.inventory()
            self.assertEqual(inv.diagnostics, ())
            self.assertEqual([dict(c.remote_ids)[c.channels[0]] for c in inv.cards],
                             ['1884787296017457', '1099867215965804', '1084557747316275'])
            saved = [json.loads(p.read_text('utf-8')) for p in (Path(folder) / 'planner_diagnostics').glob('*.json')]
            self.assertEqual(len(saved), 3)
            self.assertTrue(all(d['recovered'] and d['failures'][0]['surface'] == 'composer' for d in saved))
            self.assertEqual(await self.page.evaluate('mode'), 'month')
            self.assertEqual(await self.page.evaluate('writes'), [])

    async def test_detail_observer_failure_survives_composer_address_and_keeps_other_cards_readable(self):
        await self.mount_week_view(navigate_on_close='url_only')
        await self.page.route('https://business.facebook.com/**', lambda route: route.fulfill(
            content_type='text/html', body=self.week_html))
        await self.page.goto('https://business.facebook.com/latest/content_calendar?asset_id=111222333444&business_id=555666777888')
        original = month.scheduled_details.verify_preview_owner

        async def preview_fails(dialog, ids, expected_accounts, *, timeout):
            await original(dialog, ids, expected_accounts, timeout=timeout)
            if 'instagram' in ids:
                raise month.bs.PublishStepError('synthetic preview uncertainty')

        with tempfile.TemporaryDirectory() as folder, \
                patch.object(month, 'cfg', return_value=SimpleNamespace(state_dir=Path(folder))), \
                patch.object(month.scheduled_details, 'verify_preview_owner', preview_fails):
            inv = await self.inventory()
            self.assertEqual([dict(c.remote_ids).get('facebook') for c in inv.cards if c.read_status == 'complete'],
                             ['1884787296017457', '1084557747316275'])
            self.assertEqual([d['code'] for d in inv.diagnostics], ['read_failed'])
            self.assertFalse(inv.decision_complete)
            self.assertEqual(await self.page.evaluate('mode'), 'month')
            self.assertEqual(await self.page.evaluate('writes'), [])
            saved = [json.loads(p.read_text('utf-8')) for p in (Path(folder) / 'planner_diagnostics').glob('*.json')]
            self.assertEqual(len(saved), 3)
            self.assertTrue(all(d['recovered'] for d in saved))

    async def test_late_route_change_after_close_is_restored_before_accepting_the_card(self):
        await self.mount_week_view()
        await self.page.route('https://business.facebook.com/**', lambda route: route.fulfill(
            content_type='text/html', body=self.week_html))
        await self.page.goto('https://business.facebook.com/latest/content_calendar?asset_id=111222333444&business_id=555666777888')
        original = month.restore_detail_grid
        hopped = False

        async def late_route(page, month_rows, view_rows, calendar_url, timeout, phase):
            nonlocal hopped
            await original(page, month_rows, view_rows, calendar_url, timeout, phase)
            if phase == 'after_detail_close' and not hopped:
                hopped = True
                await page.evaluate("history.pushState({},'', '/latest/composer/?asset_id=111222333444')")

        with tempfile.TemporaryDirectory() as folder, \
                patch.object(month, 'cfg', return_value=SimpleNamespace(state_dir=Path(folder))), \
                patch.object(month, 'restore_detail_grid', late_route):
            inv = await self.inventory()
            self.assertTrue(hopped)
            self.assertEqual(inv.diagnostics, ())
            self.assertEqual([dict(c.remote_ids)[c.channels[0]] for c in inv.cards],
                             ['1884787296017457', '1099867215965804', '1084557747316275'])
            self.assertEqual(await self.page.evaluate('mode'), 'month')
            saved = [json.loads(p.read_text('utf-8')) for p in (Path(folder) / 'planner_diagnostics').glob('*.json')]
            self.assertEqual(len(saved), 1)
            self.assertTrue(saved[0]['recovered'])

    async def test_navigation_recovery_rejects_changed_visible_or_hidden_cards(self):
        for change in ('visible_time', 'hidden_caption'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as folder, \
                    patch.object(month, 'cfg', return_value=SimpleNamespace(state_dir=Path(folder))):
                await self.mount_week_view(navigate_on_close='replace_body')
                original = self.week_html
                changed = (original.replace('5:30 PM', '5:31 PM') if change == 'visible_time' else
                           original.replace('Full caption.', 'Changed remote caption.'))
                visits = []
                async def serve(route):
                    visits.append(route.request.url)
                    await route.fulfill(content_type='text/html', body=original if len(visits)==1 else changed)
                await self.page.route('https://business.facebook.com/**', serve)
                await self.page.goto('https://business.facebook.com/latest/content_calendar?asset_id=111222333444&business_id=555666777888')
                with self.assertRaisesRegex(month.bs.PlannerNavigationError, '未能恢复并核实'):
                    await self.inventory()
                self.assertEqual(len(visits), 2)
                self.assertEqual(await self.page.evaluate('opened'), [])
                saved = [json.loads(p.read_text('utf-8')) for p in (Path(folder) / 'planner_diagnostics').glob('*.json')]
                self.assertTrue(saved)
                self.assertTrue(all(not data['recovered'] for data in saved))
                await self.page.unroute('https://business.facebook.com/**', serve)

    async def test_week_read_on_composer_reports_navigation_instead_of_missing_dates(self):
        await self.page.route('https://business.facebook.com/**', lambda route: route.fulfill(
            content_type='text/html', body='<h1>Create post</h1><div role="dialog" aria-label="Schedule post"></div>'))
        await self.page.goto('https://business.facebook.com/latest/composer/')
        rows = [{'date': day} for day in calendar.Calendar(firstweekday=6).itermonthdates(2026, 9)]
        with self.assertRaises(month.bs.PlannerNavigationError) as failed:
            await month.ready_week(self.page, rows, timeout=30, phase='after_details')
        self.assertEqual(failed.exception.diagnostic, {'phase': 'after_details', 'surface': 'composer'})

    async def test_view_switch_cannot_replace_the_original_asset_binding(self):
        await self.mount_week_view(navigate_on_close='replace_body')
        await self.page.route('https://business.facebook.com/**', lambda route: route.fulfill(
            content_type='text/html', body=self.week_html))
        await self.page.goto('https://business.facebook.com/latest/content_calendar?asset_id=111222333444&business_id=555666777888')
        await self.page.evaluate('''()=>{
          const button=document.getElementById('week'), original=button.onclick;
          button.onclick=()=>{original();history.pushState({},'',
            '/latest/content_calendar?asset_id=999999999999&business_id=555666777888')};
        }''')
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(month, 'cfg', return_value=SimpleNamespace(state_dir=Path(folder))):
            with self.assertRaises(month.bs.PlannerNavigationError) as failed:
                await self.inventory()
            self.assertEqual(failed.exception.diagnostic, {'phase': 'week_select', 'surface': 'other'})
            self.assertEqual(await self.page.evaluate('opened'), [])
            self.assertEqual(await self.page.evaluate('writes'), [])

    async def test_navigation_during_grid_wait_is_not_relabelled_as_missing_dates(self):
        await self.page.route('https://business.facebook.com/**', lambda route: route.fulfill(
            content_type='text/html', body='<h1>Planner</h1>'))
        rows = [{'date': day} for day in calendar.Calendar(firstweekday=6).itermonthdates(2026, 9)]
        async def navigate(*args):
            await self.page.evaluate("history.pushState({},'', '/latest/composer/');document.body.innerHTML='<h1>Create post</h1>'")
        for reader, args in ((month.ready_grid, ()), (month.ready_week, (rows,))):
            with self.subTest(reader=reader.__name__):
                await self.page.goto('https://business.facebook.com/latest/content_calendar')
                with patch.object(month, 'settled', side_effect=navigate), self.assertRaises(month.bs.PlannerNavigationError):
                    await reader(self.page, *args, timeout=0)

    async def test_missing_hidden_post_cannot_be_treated_as_available(self):
        await self.mount_week_view(missing=True)
        with self.assertRaisesRegex(month.bs.PublishStepError, '折叠数量不一致'):
            await self.inventory()
        self.assertEqual(await self.page.evaluate('opened'), [])

    async def test_week_changes_while_opening_details_invalidate_the_read(self):
        await self.mount_week_view(mutate=True)
        with self.assertRaisesRegex(month.bs.PublishStepError, '条目已变化|正文发生变化'):
            await self.inventory()

    async def test_multiple_details_or_card_channel_conflict_never_confirm_instagram(self):
        for mutation in ('duplicate_dialog', 'wrong_icon'):
            with self.subTest(mutation=mutation):
                await self.mount_week_view()
                await self.page.evaluate('''mode=>{
                  const base=render;
                  render=()=>{
                    base();
                    const entry=document.querySelector('[data-item="1"]');
                    if(!entry)return;
                    if(mode==='wrong_icon')entry.querySelector('img').alt='Facebook';
                    else {const open=entry.onclick;entry.onclick=e=>{
                      open(e);document.getElementById('details').insertAdjacentHTML('beforeend',document.getElementById('details').innerHTML);
                    };}
                  };
                }''', mutation)
                if mutation == 'duplicate_dialog':
                    inv = await self.inventory()
                    self.assertTrue(inv.diagnostics)
                    self.assertFalse(inv.decision_complete)
                    self.assertFalse(any(card.channels == ('instagram',) for card in inv.cards))
                else:
                    with self.assertRaisesRegex(month.bs.PublishStepError, '折叠数量不一致'):
                        await self.inventory()
                self.assertEqual(await self.page.evaluate('writes'), [])

    async def test_preview_author_cannot_be_replaced_by_a_caption_mention(self):
        await self.mount_week_view()
        await self.page.evaluate('''()=>{
          const base=render;
          render=()=>{base();const entry=document.querySelector('[data-item="1"]');if(!entry)return;
            const open=entry.onclick;entry.onclick=e=>{open(e);
              const d=document.getElementById('details');
              d.innerHTML=d.innerHTML.replaceAll('neakasa.de', 'wrong.account').replace('Tag 1 auf der @ifa.berlin ✨', 'thanks to neakasa.de for the sample');
              d.querySelector('#close').onclick=()=>d.innerHTML='';
            };
          };
        }''')
        inv = await self.inventory()
        self.assertTrue(inv.diagnostics)
        self.assertFalse(any(card.channels == ('instagram',) for card in inv.cards))
        self.assertEqual(await self.page.evaluate('writes'), [])

    async def test_more_link_is_counted_separately_without_becoming_a_time_card(self):
        rows = await month.read_grid(self.page, timeout=5)
        day = rows[31]
        self.assertEqual(day['date'], date(2026, 9, 30))
        self.assertEqual(day['hidden_count'], 1)
        self.assertEqual([item['time'] for item in day['items']], ['5:30 PM', '8:00 PM'])
        self.assertEqual([item['index'] for item in day['items']], [0, 1])
        for item in day['items']:
            self.assertEqual(await month.item_locator(self.page, day, item).inner_text(), item['text'])

    async def test_week_headers_do_not_need_one_element_containing_only_all_seven_dates(self):
        days = tuple(calendar.Calendar(firstweekday=6).itermonthdates(2026, 9))[-7:]
        headers = ''.join(f'<div>{label} {day.day}</div>'
                          for label, day in zip(('Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'), days))
        cells = ''.join('<div role="link" draggable="false">8:00 PM</div>' for _ in days)
        await self.page.set_content('<h1>Planner</h1><h1>Sep - Oct</h1><h1>2026</h1>'
                                    '<section>' + headers + cells + '</section>')
        rows = [{'date': day} for day in calendar.Calendar(firstweekday=6).itermonthdates(2026, 9)]
        self.assertEqual(await month.ready_week(self.page, rows, timeout=.3), days)

    async def test_recorded_daily_divs_and_rendering_variants_read_the_same_week(self):
        rows = [{'date': day} for day in calendar.Calendar(firstweekday=6).itermonthdates(2026, 9)]
        days = tuple(row['date'] for row in rows[-7:])
        for variant in ('recorded', 'uppercase', 'direction_marks', 'hidden_copy', 'caption_dates'):
            with self.subTest(variant=variant):
                labels = [f'{label} {day.day}' for label, day in zip(month.WEEKDAYS, days)]
                if variant == 'direction_marks':
                    labels = [text.replace(' ', '\u200e\u00a0') for text in labels]
                headers = '<div id="week-dates">' + ''.join(f'<div><div>{text}</div></div>' for text in labels) + '</div>'
                extra = ''
                if variant == 'uppercase':
                    extra = '<style>#week-dates {text-transform:uppercase}</style>'
                elif variant == 'hidden_copy':
                    extra = f'<div hidden>{headers}</div><div aria-hidden="true">{headers}</div>'
                cells = ''.join('<div role="link" draggable="false">' +
                                (labels[i] if variant == 'caption_dates' else '8:00 PM') + '</div>' for i in range(7))
                await self.page.set_content('<h1>Sep - Oct</h1><h1>2026</h1>' + headers + cells + extra)
                self.assertEqual(await month.ready_week(self.page, rows, timeout=.3), days)

    async def test_incomplete_duplicate_or_reordered_visible_week_stays_unresolved(self):
        rows = [{'date': day} for day in calendar.Calendar(firstweekday=6).itermonthdates(2026, 9)]
        correct = ['Sun 27', 'Mon 28', 'Tue 29', 'Wed 30', 'Thu 1', 'Fri 2', 'Sat 3']
        for labels in (correct[:-1], correct + ['Sun 20'], [correct[1], correct[0], *correct[2:]]):
            with self.subTest(labels=labels):
                headers = ''.join(f'<div>{text}</div>' for text in labels)
                # Hidden content must not fill a missing visible weekday.
                await self.page.set_content('<h1>Sep - Oct</h1><h1>2026</h1><div>' + headers +
                    '</div><div hidden>Sat 3</div>' + '<div role="link" draggable="false"></div>' * 7)
                with self.assertRaises(month.WeekHeaderUnavailable):
                    await month.ready_week(self.page, rows, timeout=.2)

    async def test_delayed_week_labels_are_waited_for_without_guessing_the_target_week(self):
        rows = [{'date': day} for day in calendar.Calendar(firstweekday=6).itermonthdates(2026, 9)]
        await self.page.set_content('<h1>Sep - Oct</h1><h1>2026</h1><div id="dates"></div>' +
                                    '<div role="link" draggable="false"></div>' * 7)
        async def render():
            await asyncio.sleep(.2)
            await self.page.locator('#dates').evaluate('''n => n.innerHTML=
              ['Sun 20','Mon 21','Tue 22','Wed 23','Thu 24','Fri 25','Sat 26'].map(s=>'<div>'+s+'</div>').join('')''')
        render_task = asyncio.create_task(render())
        try:
            self.assertEqual(await month.ready_week(self.page, rows, timeout=2), tuple(row['date'] for row in rows[-14:-7]))
        finally:
            await render_task

    async def test_week_header_failure_preserves_date_diagnostics_without_reloading_or_private_text(self):
        rows = [{'date': day} for day in calendar.Calendar(firstweekday=6).itermonthdates(2026, 9)]
        await self.page.set_content('<h1>Sep - Oct</h1><h1>2026</h1><div>Sun 27</div>'
            '<div role="link" draggable="false">private caption with Sun 27</div>')
        async def broken_read(*args, **kwargs):
            return await month.ready_week(self.page, rows, timeout=.2)
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(month, 'cfg', return_value=SimpleNamespace(state_dir=Path(folder))), \
                patch.object(month, '_read_once', AsyncMock(side_effect=broken_read)) as reader:
            with self.assertRaisesRegex(month.WeekHeaderUnavailable, 'planner_diagnostics'):
                await month.read(self.page, ui_timezone='Asia/Shanghai', business_timezone='Asia/Shanghai')
            reader.assert_awaited_once()
            evidence = list((Path(folder) / 'planner_diagnostics').glob('*.json'))
            self.assertEqual(len(evidence), 1)
            saved = json.loads(evidence[0].read_text('utf-8'))
            failure = saved['failures'][0]
            self.assertEqual(failure['phase'], 'week_header')
            self.assertEqual(failure['date_labels'], [{'weekday': 'Sun', 'day': 27}])
            self.assertFalse(saved['recovered'])
            self.assertNotIn('private caption', json.dumps(saved))

    async def test_unrecorded_link_is_not_silently_skipped(self):
        await self.page.get_by_role('link', name='+ 1 more', exact=True).evaluate("el=>el.textContent='Other action'")
        with self.assertRaisesRegex(month.bs.PublishStepError, '时刻无法唯一读取'):
            await month.read_grid(self.page, timeout=5)


if __name__ == '__main__':
    unittest.main()
