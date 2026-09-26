"""Known G1 identity semantics plus synthetic mutations; media layout is unknown."""
import calendar
import html
import sys
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.config import Config
from publish import business_suite as bs, month_inventory as month, month_readback, planning
from publish.compose import ScheduleWindow
from tests_month_inventory import SPEC, CAPTION, ENTRY


class ScheduledDetailTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(executable_path=Config().chrome_exe, headless=True)
        self.page = await self.browser.new_page()
        self.when = datetime(2026, 9, 30, 17, 30, tzinfo=ZoneInfo('Asia/Shanghai'))
        self.card = bs.RemotePlannerCard(self.when, ('facebook',), (('facebook', '123456789'),),
            CAPTION, 'hash', 'scheduled', placement='feed', caption_status='present',
            accounts=(('facebook', 'Neakasa Deutschland'),), time_verified=True)
        await self.mount()

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def mount(self, *, copies=1, remote='123456789', caption=CAPTION, account='Neakasa Deutschland',
                    marker="Facebook's Feed"):
        item = '<a role="link" onclick="document.querySelector(\'[role=dialog]\').hidden=false">' + html.escape(ENTRY) + '</a>'
        cells = ''.join(f'<div role="link" draggable="false"><span>{day.day}</span>'
            + (item * copies if day == self.when.date() else '') + '</div>'
            for day in calendar.Calendar(firstweekday=6).itermonthdates(2026, 9))
        await self.page.set_content('<h1>September</h1><h1>2026</h1>' + cells
            + f'<div role="dialog" aria-label="Post details" hidden>ID: {remote} '
              f"{marker} <span id=account>{account}</span> <span id=caption>{html.escape(caption)}</span></div>"
            + '<script>document.onkeydown=e=>{if(e.key==="Escape")document.querySelector("[role=dialog]").hidden=true}</script>')

    async def read(self, observer, card=None):
        with patch.object(month, 'recommendation_state', AsyncMock(return_value='absent')):
            return await month.read_scheduled_target(self.page, card or self.card,
                ui_timezone='Asia/Shanghai', card_spec=SPEC, timeout=1.5, observe_detail=observer)

    async def mount_time_only(self, *, changed_clock=False, truncate=False):
        # Structure from the 2026-09-25 service Post details accessibility snapshot.
        # The article's unrelated boost/progress/comment controls are deliberately present.
        await self.page.locator('[draggable=false] a').evaluate("el=>el.textContent='5:30\\u202fPM'")
        await self.page.get_by_role('dialog', include_hidden=True).evaluate('''(el, data) => {
          el.innerHTML=`<h3>Post details</h3>ID: 123456789
            <h4>Post overview</h4><div>This view may not represent Facebook's Feed.</div>
            <button>Actions</button><article><h2><a href="/profile.php?id=123456">Neakasa Deutschland</a></h2>
            <a href="/987654321">September 30 at ${data.changed ? '6:30' : '5:30'} PM</a>
            <img alt="Shared with Public"><span id="actions-label" hidden>Actions for this post by Neakasa Deutschland</span><button aria-labelledby="actions-label"></button>
            <div id="body">This is a manual… <button id="expand">See more</button></div>
            <a href="/photo.php"><img alt="May be an image of text"></a>
            Boost this post to get more reach.<div role="progressbar"></div>
            <button>Comment</button><div role="textbox">Private comment draft</div></article>
            <button>Boost</button><button>Publish now</button>`;
          document.getElementById('expand').onclick=()=>{
            if(!data.truncate) document.getElementById('body').innerHTML=
              'This is a manual test.<img alt="😊"> <a>#SmartPetFeeder</a> #CatLovers #NeakasaRiko';
          };
          window.writes=[];
          [...el.querySelectorAll('button')].filter(b=>b.id!=='expand').forEach(b=>b.onclick=()=>writes.push(b.innerText));
        }''', {'changed': changed_clock, 'truncate': truncate})

    async def time_only_inventory(self):
        with patch.object(month, 'prepare', AsyncMock()), \
                patch.object(month, 'recommendation_state', AsyncMock(return_value='absent')), \
                patch.object(month.bs, 'require_readback_evidence', return_value=SPEC):
            return await month.read(self.page, ui_timezone='Asia/Shanghai',
                business_timezone='Asia/Shanghai', timeout=1.5, detail_range=(self.when, self.when))

    async def test_time_only_card_expands_its_own_preview_and_reads_full_caption(self):
        await self.mount_time_only()
        result = await self.time_only_inventory()
        self.assertFalse(result.diagnostics)
        self.assertEqual(len(result.cards_in_range('facebook', self.when, self.when)), 1)
        self.assertEqual(result.cards[0].rendered, CAPTION)
        self.assertEqual(result.cards[0].remote_ids, self.card.remote_ids)
        self.assertEqual(result.cards[0].at, self.when)
        self.assertEqual(await self.page.evaluate('writes'), [])

    async def mount_instagram_preview(self, *, owner='neakasa.de', stuck=False):
        # Service 2026-09-26: Instagram has no article or date link; the
        # author span and its following caption live after the Like/Save row.
        await self.page.locator('[draggable=false] a').evaluate("el=>el.textContent='8:00\\u202fPM'")
        await self.page.get_by_role('dialog', include_hidden=True).evaluate('''(el, data) => {
          el.innerHTML=`<h3>Post details</h3>ID: 1099867215965804
            <h4>Post overview</h4><span>This view of your post may not represent exactly how it appears on your Instagram feed.</span>
            <button>Actions</button><div>${data.owner}</div><div><img alt=""><img alt=""><img alt=""></div>
            <button>Like</button><button>Comment</button><button>Share</button><button>Save</button>
            <div id="caption"><span>${data.owner}</span><span></span> <span id="body">
              <span><div>Tag 1 auf der @ifa.berlin – was für ein Start!<br></div><span> ✨... <button>more</button></span></span>
            </span></div><button>Boost</button><button>Publish now</button>`;
          window.writes=[];
          el.querySelector('#body button').onclick=()=>{
            if(!data.stuck) document.getElementById('body').innerHTML=
              '<span>Tag 1 auf der @ifa.berlin – was für ein Start! ✨<br>Voller Text mit #Neakasa und @home.</span>';
          };
          [...el.querySelectorAll('button')].filter(b=>b.textContent!=='more').forEach(b=>b.onclick=()=>writes.push(b.innerText));
        }''', {'owner': owner, 'stuck': stuck})

    async def test_instagram_time_only_preview_reads_full_caption_without_facebook_article(self):
        await self.mount_instagram_preview()
        result = await self.time_only_inventory()
        self.assertFalse(result.diagnostics, result.diagnostics)
        self.assertEqual(len(result.cards), 1)
        card = result.cards[0]
        self.assertEqual(card.channels, ('instagram',))
        self.assertEqual(card.remote_ids, (('instagram', '1099867215965804'),))
        self.assertEqual(card.at, self.when.replace(hour=20, minute=0))
        self.assertEqual(bs._card_text(card.rendered),
            'Tag 1 auf der @ifa.berlin – was für ein Start! ✨ Voller Text mit #Neakasa und @home.')
        self.assertEqual(card.accounts, (('instagram', 'neakasa.de'),))
        self.assertTrue(card.time_verified)
        self.assertEqual(await self.page.evaluate('writes'), [])

    async def test_instagram_truncated_caption_or_wrong_owner_cannot_supply_a_receipt(self):
        for changes in ({'stuck': True}, {'owner': 'wrong.account'}):
            with self.subTest(changes=changes):
                await self.mount()
                await self.mount_instagram_preview(**changes)
                result = await self.time_only_inventory()
                self.assertTrue(result.diagnostics)
                self.assertFalse(result.channels_complete)
                self.assertEqual(await self.page.evaluate('writes'), [])

    async def test_unexpected_composer_navigation_is_not_a_missing_month_or_unread_card(self):
        async def respond(route):
            body = ('<a href="/latest/composer/">8:00 PM</a>'
                    if 'content_calendar' in route.request.url else '<h1>Create post</h1>')
            await route.fulfill(content_type='text/html', body=body)
        await self.page.route('https://business.facebook.com/**', respond)
        await self.page.goto('https://business.facebook.com/latest/content_calendar')
        with self.assertRaisesRegex(bs.PlannerNavigationError, '离开了月历'):
            await bs._open_channel_dialogs(self.page, self.page.get_by_role('link'), SPEC, timeout=2)

    async def test_two_time_only_posts_on_one_day_keep_separate_details_and_identity(self):
        await self.mount_time_only()
        await self.page.evaluate('''() => {
          const dialog=document.querySelector('[role=dialog]');
          const template=dialog.innerHTML;
          const first=document.querySelector('[draggable=false] a');
          const second=first.cloneNode(true); second.textContent='11:00\\u202fPM';
          first.parentElement.append(second);
          window.opened=[];
          [first,second].forEach((entry,index)=>entry.onclick=()=>{
            opened.push(index);
            dialog.innerHTML=template.replace('ID: 123456789', 'ID: '+(index ? '987654321' : '123456789'));
            dialog.querySelector('article a[href="/987654321"]').textContent=
              'September 30 at '+(index ? '11:00' : '5:30')+' PM';
            dialog.hidden=false;
            dialog.querySelector('#expand').onclick=()=>{
              dialog.querySelector('#body').innerHTML=index ? 'Other scheduled post' :
                'This is a manual test.<img alt="😊"> #SmartPetFeeder #CatLovers #NeakasaRiko';
            };
          });
        }''')
        result = await self.time_only_inventory()
        self.assertFalse(result.diagnostics)
        self.assertEqual(await self.page.evaluate('opened'), [0, 1])
        self.assertEqual([dict(c.remote_ids)['facebook'] for c in result.cards], ['123456789', '987654321'])
        self.assertEqual([c.at.hour for c in result.cards], [17, 23])
        self.assertEqual(result.cards_in_range('facebook', self.when, self.when), (result.cards[0],))

    async def test_time_only_wrong_preview_time_or_unexpanded_text_stays_incomplete(self):
        for changes in ({'changed_clock': True}, {'truncate': True}):
            await self.mount()
            await self.mount_time_only(**changes)
            result = await self.time_only_inventory()
            self.assertFalse(result.decision_complete)
            self.assertEqual(await self.page.evaluate('writes'), [])

    async def test_two_details_close_via_recorded_button_when_escape_is_ignored(self):
        await self.mount_time_only()
        await self.page.evaluate('''() => {
          const dialog=document.querySelector('[role=dialog]');
          dialog.style.cssText='position:fixed;inset:0;z-index:10;background:white';
          dialog.insertAdjacentHTML('afterbegin','<button id="close">Close</button>');
          const template=dialog.innerHTML;
          const first=document.querySelector('[draggable=false] a');
          const second=first.cloneNode(true); second.textContent='11:00\\u202fPM';
          first.parentElement.append(second);
          window.opened=[]; window.detailClosures=[]; document.onkeydown=()=>{};
          [first,second].forEach((entry,index)=>entry.onclick=()=>{
            opened.push(index);
            dialog.innerHTML=template.replace('ID: 123456789', 'ID: '+(index ? '987654321' : '123456789'));
            dialog.querySelector('article a[href="/987654321"]').textContent=
              'September 30 at '+(index ? '11:00' : '5:30')+' PM';
            dialog.hidden=false;
            dialog.querySelector('#expand').onclick=()=>{
              dialog.querySelector('#body').innerHTML=index ? 'Other scheduled post' :
                'This is a manual test.<img alt="😊"> #SmartPetFeeder #CatLovers #NeakasaRiko';
            };
            dialog.querySelector('#close').onclick=()=>setTimeout(()=>{
              dialog.hidden=true; detailClosures.push(index);
            },150);
          });
        }''')
        result = await self.time_only_inventory()
        self.assertFalse(result.diagnostics, result.diagnostics)
        self.assertEqual(await self.page.evaluate('opened'), [0, 1])
        self.assertEqual(await self.page.evaluate('detailClosures'), [0, 1])
        target = self.when.replace(hour=20, minute=0)
        window = ScheduleWindow('', timedelta(0), None, 'Asia/Shanghai')
        decision = planning.evaluate_slot(target, 'instagram', result,
            now=target-timedelta(days=4), window=window)
        self.assertTrue(decision.allowed, decision)
        self.assertEqual(await self.page.evaluate('writes'), [])

    async def test_escape_fallback_waits_until_the_dialog_has_actually_closed(self):
        await self.page.evaluate('''() => {
          document.onkeydown=e=>{if(e.key==='Escape')
            setTimeout(()=>document.querySelector('[role=dialog]').hidden=true,250)};
        }''')
        await bs._open_channel_dialogs(self.page, self.page.locator('[draggable=false] a'), SPEC, timeout=1.5)
        self.assertFalse(await self.page.get_by_role('dialog').is_visible())

    async def test_unclosable_detail_stops_before_clicking_another_card(self):
        await self.mount_time_only()
        await self.page.evaluate('''() => {
          const dialog=document.querySelector('[role=dialog]');
          dialog.insertAdjacentHTML('afterbegin','<button>Close</button>');
          const first=document.querySelector('[draggable=false] a');
          const second=first.cloneNode(true); second.textContent='11:00\\u202fPM';
          first.parentElement.append(second);
          window.opened=[]; document.onkeydown=()=>{};
          [first,second].forEach((entry,index)=>entry.onclick=()=>{
            opened.push(index); dialog.hidden=false;
          });
        }''')
        with self.assertRaisesRegex(bs.PlannerDialogCloseError, '详情未能关闭'):
            await self.time_only_inventory()
        self.assertEqual(await self.page.evaluate('opened'), [0])
        self.assertEqual(await self.page.evaluate('writes'), [])

    async def test_verified_1730_receipt_survives_2300_detail_read_failure(self):
        await self.mount_time_only()
        await self.page.locator('[draggable=false] a').evaluate('''first => {
          const second=first.cloneNode(true); second.textContent='11:00\\u202fPM';
          first.parentElement.append(second);
        }''')
        read_item = month.read_item
        async def service_failure(page, row, item, **kwargs):
            if item['index'] == 1:
                raise month.PlannerItemError(row, item, 'scheduled_detail')
            return await read_item(page, row, item, **kwargs)
        with patch.object(month, 'prepare', AsyncMock()), \
                patch.object(month, 'recommendation_state', AsyncMock(return_value='absent')), \
                patch.object(month.bs, 'require_readback_evidence', return_value=SPEC), \
                patch.object(month, 'read_item', service_failure), \
                patch.object(bs, '_readback_screenshot', AsyncMock(return_value='')):
            result = await month_readback.verify(self.page, self.when, CAPTION,
                ui_timezone='Asia/Shanghai', target_channels=('facebook',), timeout=1.5)
        self.assertTrue(result.found, result.error)
        self.assertEqual(result.remote_id, 'facebook=123456789')
        diagnostic, = result.diagnostics['inventory_diagnostics']
        self.assertEqual((diagnostic['time'], diagnostic['code']), ('11:00 PM', 'read_failed'))
        self.assertFalse(result.diagnostics['complete_month'])
        self.assertEqual(await self.page.evaluate('writes'), [])

    async def test_time_only_caption_arriving_after_header_is_expanded_before_read(self):
        await self.mount_time_only()
        await self.page.locator('[draggable=false] a').evaluate('''entry => {
          entry.onclick=()=>{
            const dialog=document.querySelector('[role=dialog]'); dialog.hidden=false;
            const body=document.getElementById('body'); body.textContent='';
            setTimeout(()=>{
              body.innerHTML='This is a manual… <button id="expand">See more</button>';
              document.getElementById('expand').onclick=()=>body.innerHTML=
                'This is a manual test.<img alt="😊"> #SmartPetFeeder #CatLovers #NeakasaRiko';
            },800);
          };
        }''')
        result = await self.time_only_inventory()
        self.assertFalse(result.diagnostics, result.diagnostics)
        self.assertEqual(result.cards[0].rendered, CAPTION)

    async def test_time_only_existing_object_can_be_reacquired_for_read_only_media(self):
        await self.mount_time_only()
        captured = []
        async def observe(dialog):
            captured.append(await dialog.get_by_role('article').count())
            return 'capture'
        self.assertEqual(await self.read(observe), 'capture')
        self.assertEqual(captured, [1])
        self.assertEqual(await self.page.evaluate('writes'), [])

    async def test_only_exact_target_is_observed_and_dialog_is_closed(self):
        async def observe(dialog):
            self.assertIn('123456789', await dialog.inner_text())
            return 'captured'
        self.assertEqual(await self.read(observe), 'captured')
        self.assertFalse(await self.page.get_by_role('dialog').is_visible())

    async def test_instagram_identity_uses_its_own_channel_and_account(self):
        await self.mount(account='neakasa.de', marker='Instagram feed')
        card = replace(self.card, channels=('instagram',), remote_ids=(('instagram', '123456789'),),
                       accounts=(('instagram', 'neakasa.de'),))
        async def observe(dialog):
            return 'instagram capture'
        self.assertEqual(await self.read(observe, card), 'instagram capture')
        with self.assertRaises(bs.PublishStepError):
            await self.read(observe, self.card)

    async def test_wrong_id_account_duplicate_target_or_truncated_caption_never_reaches_media(self):
        for changed in ({'remote': '987654321'}, {'account': 'Wrong account'},
                        {'copies': 2}, {'caption': CAPTION[:10]}):
            with self.subTest(changed=changed):
                await self.mount(**changed)
                observed = []
                async def observe(dialog):
                    observed.append(True)
                with self.assertRaises(bs.PublishStepError):
                    await self.read(observe)
                self.assertEqual(observed, [])

    async def test_identity_or_grid_changes_during_media_capture_invalidate_result(self):
        for selector, changed in (('#account', 'Changed account'), ('#caption', 'Changed caption'),
                                  ('[draggable=false] a', 'Changed September 30, 2026, 5:30 PM')):
            with self.subTest(selector=selector):
                await self.mount()
                async def observe(dialog):
                    await self.page.locator(selector).evaluate('(el, value)=>el.textContent=value', changed)
                    return 'must not survive'
                with self.assertRaises(bs.PublishStepError):
                    await self.read(observe)

    async def test_observer_exception_is_not_swallowed_as_an_empty_identity(self):
        async def observe(dialog):
            raise bs.PublishStepError('fixture capture error')
        with self.assertRaisesRegex(bs.PublishStepError, 'fixture capture error'):
            await self.read(observe)
        self.assertFalse(await self.page.get_by_role('dialog').is_visible())


if __name__ == '__main__':
    unittest.main()
