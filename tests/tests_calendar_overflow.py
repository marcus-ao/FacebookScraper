"""Recorded month overflow links are controls, never posts or invented times."""
import calendar
import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.config import Config
from publish import month_inventory as month
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

    async def mount_week_view(self, *, missing=False, mutate=False, count=3):
        entries = [
            {'time': '5:30 PM', 'platform': 'Facebook', 'id': '1884787296017457', 'caption': 'Riko full caption. #Riko'},
            {'time': '8:00 PM', 'platform': 'Instagram', 'id': '1099867215965804', 'caption': 'Tag 1 auf der @ifa.berlin ✨\nBis morgen! 👋 #Berlin'},
            {'time': '11:00 PM', 'platform': 'Facebook', 'id': '1084557747316275', 'caption': 'Nur noch 7 Tage! 🐱✨ Full caption.'},
            {'time': '11:01 PM', 'platform': 'Instagram', 'id': '1000000000000004', 'caption': 'Fourth full caption at 5:30 PM.'},
        ][:count]
        days = [day.day for day in calendar.Calendar(firstweekday=6).itermonthdates(2026, 9)]
        await self.page.set_content('''<h1>Planner</h1><h1 id="month">September</h1><h1>2026</h1>
          <button id="week">Week</button><button id="monthly">Month</button>
          <button id="left">Left</button><button id="right">Right</button>
          <button>Content type: all</button><button>Shared to: all</button>
          <div id="headers"></div><div id="grid"></div><div id="details"></div>''')
        await self.page.evaluate('''data=>{
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
                  '<p>This view of your post may not represent exactly how it appears on your Instagram feed.</p><div>neakasa.de</div><div><span>neakasa.de</span><span></span><span>Truncated ... <button>more</button></span></div>' :
                  "<p>This view may not represent Facebook's Feed.</p><article><h2>Neakasa Deutschland</h2><div>Truncated ...</div></article>")+
                '<button id="close">Close</button><button>Boost</button><button>Publish now</button></div>';
              document.querySelectorAll('#details button:not(#close)').forEach(b=>b.onclick=()=>writes.push(b.innerText));
              document.getElementById('close').onclick=()=>{
                document.getElementById('details').innerHTML='';
                if(data.mutate){entries[2].caption='Changed during detail';render();}
              };
            });
            document.querySelectorAll('#grid button,#grid a').forEach(n=>n.onclick=e=>{e.preventDefault();writes.push(n.innerText)});
          };
          document.getElementById('week').onclick=()=>{mode='week';offset=0;render()};
          document.getElementById('monthly').onclick=()=>{mode='month';render()};
          document.getElementById('right').onclick=()=>{offset++;render()};
          document.getElementById('left').onclick=()=>{offset--;render()};
          render();
        }''', {'days': days, 'entries': entries, 'missing': missing, 'mutate': mutate})
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

    async def test_week_recommendation_is_not_an_eighth_day_or_a_post(self):
        await self.mount_week_view()
        inv = await self.inventory(whole_month=True)
        self.assertEqual(len(inv.cards), 3)
        self.assertEqual(inv.diagnostics, ())
        self.assertTrue(inv.decision_complete)
        self.assertEqual(await self.page.evaluate('writes'), [])

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
              d.innerHTML=d.innerHTML.replaceAll('neakasa.de', 'wrong.account').replace('Truncated ...', 'thanks to neakasa.de for the sample');
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

    async def test_unrecorded_link_is_not_silently_skipped(self):
        await self.page.get_by_role('link', name='+ 1 more', exact=True).evaluate("el=>el.textContent='Other action'")
        with self.assertRaisesRegex(month.bs.PublishStepError, '时刻无法唯一读取'):
            await month.read_grid(self.page, timeout=5)


if __name__ == '__main__':
    unittest.main()
