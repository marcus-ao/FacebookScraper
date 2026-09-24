"""Browser lifetime and bounded scheduling reads, without a logged-in profile."""
import asyncio
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import chrome
from core.config import Config
from publish import business_suite as bs, month_inventory as month, month_readback, planning, planner_cache
from publish.insights_evidence import InsightsEvidence


WHEN = datetime(2026, 9, 30, 15, tzinfo=timezone.utc)


class ScopedReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_schedule_does_not_open_months_of_historical_insights(self):
        rows = [{'date': date(2026, 9, day), 'items': [
            {'index': 0, 'time': '11:00 PM', 'href': '/insights'}]} for day in range(1, 31)]
        material = {'channels': ('facebook',), 'remote_ids': {'facebook': '123456'},
                    'text': 'Caption', 'delivery': 'scheduled', 'placement': 'feed'}
        read = AsyncMock(return_value=material)
        with patch.object(month, 'prepare', AsyncMock()), \
                patch.object(month, 'read_grid', AsyncMock(return_value=rows)), \
                patch.object(month, 'read_item', read):
            result = await month.read(None, ui_timezone='Asia/Shanghai', business_timezone='Europe/Berlin',
                                      detail_range=(WHEN, WHEN))
        self.assertEqual(read.await_count, 1)
        self.assertEqual(read.await_args.args[1]['date'], date(2026, 9, 30))
        self.assertTrue(result.covers((WHEN,)))
        self.assertFalse(result.covers((WHEN-timedelta(days=1),)))
        self.assertFalse(result.decision_complete, 'A day read cannot certify an entire month or remote deletion')
        self.assertEqual(len(result.cards_in_range('facebook', WHEN, WHEN)), 1)
        cached = planner_cache.inventory_from_cache({'inventory': planner_cache._serialize(result)})
        self.assertFalse(cached.decision_complete)

    async def test_scope_converts_to_ui_dates_and_keeps_unknown_items_on_relevant_days(self):
        at = datetime(2026, 9, 29, 16, 30, tzinfo=timezone.utc)  # Sep 30 00:30 in the UI
        rows = [{'date': date(2026, 9, day), 'items': [
            {'index': 0, 'time': '11:50 PM', 'href': ''}]} for day in (28, 29, 30)]
        async def unreadable(_page, row, item, **kw):
            raise month.PlannerItemError(row, item, 'item_ready')
        with patch.object(month, 'prepare', AsyncMock()), \
                patch.object(month, 'read_grid', AsyncMock(return_value=rows)), \
                patch.object(month, 'read_item', AsyncMock(side_effect=unreadable)) as read:
            result = await month.read(None, ui_timezone='Asia/Shanghai', business_timezone='UTC',
                detail_range=planning.slot_range(at))
        self.assertEqual([call.args[1]['date'].day for call in read.await_args_list], [29, 30])
        self.assertFalse(result.cards[0].time_verified)
        with self.assertRaises(bs.ProbeRequired):
            result.cards_in_range('facebook', *planning.slot_range(at))

    async def test_readback_and_baseline_request_only_target_day(self):
        inventory = bs.RemoteSlotInventory((), 'UTC', date(2026, 9, 1), date(2026, 9, 30), cards_loaded=True)
        with patch.object(month, 'read', AsyncMock(return_value=inventory)) as read, \
                patch.object(bs, '_readback_screenshot', AsyncMock(return_value='')):
            await month_readback.baseline(None, WHEN, 'Caption', ui_timezone='UTC', target_channels=('facebook',))
            await month_readback.verify(None, WHEN, 'Caption', ui_timezone='UTC', target_channels=('facebook',))
        for call in read.await_args_list:
            self.assertEqual(call.kwargs.get('detail_range'), (WHEN, WHEN))

    async def test_full_month_read_still_visits_all_dates(self):
        rows = [{'date': date(2026, 9, day), 'items': [
            {'index': 0, 'time': '11:00 PM', 'href': ''}]} for day in (1, 15, 30)]
        material = {'channels': ('facebook',), 'remote_ids': {'facebook': '123456'},
                    'text': 'Caption', 'delivery': 'scheduled', 'placement': 'feed'}
        with patch.object(month, 'prepare', AsyncMock()), \
                patch.object(month, 'read_grid', AsyncMock(return_value=rows)), \
                patch.object(month, 'read_item', AsyncMock(return_value=material)) as read:
            result = await month.read(None, ui_timezone='UTC', business_timezone='UTC')
        self.assertEqual(read.await_count, 3)
        self.assertTrue(result.decision_complete)

    async def test_browser_loss_stops_the_read_instead_of_becoming_an_unknown_card(self):
        page = SimpleNamespace(is_closed=lambda: False, mouse=SimpleNamespace(move=AsyncMock()),
            context=SimpleNamespace(browser=SimpleNamespace(is_connected=lambda: True)))
        row = {'date': date(2026, 9, 30)}
        item = {'index': 0, 'time': '11:00 PM', 'href': '/insights'}
        with patch.object(month, 'ready_item', AsyncMock(return_value=(None, ''))), \
                patch.object(month, 'read_item_detail', AsyncMock(side_effect=month.BrowserReadInterrupted('lost'))):
            with self.assertRaises(month.BrowserReadInterrupted):
                await month.read_item(page, row, item)
        page.context.browser.is_connected = lambda: False
        with patch.object(month, 'ready_item', AsyncMock(side_effect=RuntimeError('disconnected'))):
            with self.assertRaisesRegex(month.BrowserReadInterrupted, '连接已断开'):
                await month.read_item(page, row, item)

    async def test_uncovered_month_boundary_cannot_approve_a_cross_midnight_gap(self):
        rows = [{'date': date(2026, 9, 30), 'items': []}]
        at = WHEN + timedelta(minutes=50)
        with patch.object(month, 'prepare', AsyncMock()), \
                patch.object(month, 'read_grid', AsyncMock(return_value=rows)):
            result = await month.read(None, ui_timezone='Asia/Shanghai', business_timezone='UTC',
                                      detail_range=planning.slot_range(at))
        self.assertFalse(result.covers(planning.slot_range(at)))
        with self.assertRaises(bs.ProbeRequired):
            result.cards_in_range('facebook', *planning.slot_range(at))


class AttachedBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def test_composer_navigation_failure_releases_its_new_page(self):
        page = SimpleNamespace(goto=AsyncMock(side_effect=RuntimeError('navigation failed')),
                               close=AsyncMock(), context=SimpleNamespace(pages=[]))
        context = SimpleNamespace(new_page=AsyncMock(return_value=page))
        with self.assertRaisesRegex(RuntimeError, 'navigation failed'):
            await bs.open_composer(context, asset_context={'asset_id': '1001', 'business_id': '2002'})
        page.close.assert_awaited_once()

    async def test_repeated_read_cleanup_keeps_the_external_browser_and_human_tab_alive(self):
        with tempfile.TemporaryDirectory(prefix='g8-cdp-lifetime-') as raw:
            profile = Path(raw)
            async with async_playwright() as owner:
                context = await owner.chromium.launch_persistent_context(str(profile),
                    executable_path=Config().chrome_exe, headless=True, args=['--remote-debugging-port=0'])
                try:
                    port = int((profile / 'DevToolsActivePort').read_text().splitlines()[0])
                    human_tab = context.pages[0]
                    await human_tab.set_content('<h1>Human tab</h1>')
                    for _ in range(3):
                        pw, browser, attached = await chrome.attach(port=port, profile=profile)
                        try:
                            detail = await attached.new_page()
                            await detail.set_content('<h1>Temporary detail</h1>')
                            await chrome.close_owned_page(detail)
                        finally:
                            await pw.stop()
                        self.assertTrue(chrome.cdp_ready(port))
                        self.assertEqual(len(context.pages), 1)
                        self.assertEqual(await human_tab.locator('h1').inner_text(), 'Human tab')
                    # The operator can close their own tab while a reader is running.
                    # Releasing the reader's final tab must leave a lightweight window.
                    pw, browser, attached = await chrome.attach(port=port, profile=profile)
                    try:
                        last_owned = await attached.new_page()
                        await last_owned.set_content('<h1>Heavy calendar</h1>')
                        await human_tab.close()
                        await chrome.close_owned_page(last_owned)
                        self.assertFalse(last_owned.is_closed())
                        self.assertEqual(last_owned.url, 'about:blank')
                    finally:
                        await pw.stop()
                    self.assertTrue(chrome.cdp_ready(port))
                finally:
                    await context.close()

    async def test_failed_owned_page_cleanup_cannot_replace_original_result(self):
        page = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError('closed')),
                               context=SimpleNamespace(pages=[]))
        self.assertEqual(await chrome.close_owned_page(page), 'RuntimeError')


class ResponseBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_response_bodies_are_bounded_and_cancelled_on_finish(self):
        active = peak = 0
        gate = asyncio.Event()
        async def body():
            nonlocal active, peak
            active += 1
            peak = max(active, peak)
            try:
                await gate.wait()
                return b'{}'
            finally:
                active -= 1
        page = SimpleNamespace(remove_listener=lambda *_: None)
        evidence = InsightsEvidence(page, '123456')
        response = SimpleNamespace(url='https://business.facebook.com/api/graphql', status=200, body=body)
        for _ in range(40):
            evidence.observe(response)
        await asyncio.sleep(.05)
        self.assertLessEqual(peak, 4)
        self.assertGreater(peak, 0)
        await evidence.finish()
        self.assertEqual(active, 0)
        self.assertEqual(len(evidence.tasks), 0)


if __name__ == '__main__':
    unittest.main()
