"""Owned Planner navigation recovery; no real browser profile or submissions."""
import calendar
import asyncio
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.config import Config
from publish import month_inventory as month
from publish.business_suite import PublishStepError


class CalendarRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        self.pw = await async_playwright().start()
        self.addAsyncCleanup(self.pw.stop)
        self.browser = await self.pw.chromium.launch(executable_path=Config().chrome_exe, headless=True)
        self.addAsyncCleanup(self.browser.close)
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        self.visits = []
        self.run = SimpleNamespace(channel='instagram', planner_card=None,
            asset_context={'asset_id': '111222333444', 'business_id': '555666777888'})
        cells = ''.join(f'<div role="link" draggable="false">{d.day}</div>'
            for d in calendar.Calendar(firstweekday=6).itermonthdates(2026, 9))
        self.html = ('<h1>Planner</h1><h2>September</h2><h2>2026</h2>'
            '<button>Month</button><button>Content type: all</button>'
            '<button>Shared to: all</button>' + cells)
        self.missing = self.html.replace('<h2>', '<h2 aria-hidden="true">')
        self.config = patch.object(month, 'cfg', return_value=SimpleNamespace(state_dir=self.state))
        self.config.start()
        self.addCleanup(self.config.stop)

    async def serve(self, *pages):
        async def route(request):
            self.visits.append(request.request.url)
            await request.fulfill(content_type='text/html', body=pages[min(len(self.visits)-1, len(pages)-1)])
        await self.context.route('**/*', route)

    async def read(self):
        return await month.read(self.page, ui_timezone='Asia/Shanghai', business_timezone='Asia/Shanghai',
                                run=self.run, timeout=2)

    def diagnostic(self):
        paths = list((self.state / 'planner_diagnostics').glob('*.json'))
        self.assertEqual(len(paths), 1)
        return json.loads(paths[0].read_text('utf-8'))

    async def test_missing_title_reloads_only_calendar_and_rechecks_complete_grid(self):
        await self.serve(self.missing, self.html)
        inventory = await self.read()
        self.assertTrue(inventory.decision_complete)
        self.assertEqual(inventory.visible_start, date(2026, 8, 30))
        self.assertEqual(inventory.visible_end, date(2026, 10, 3))
        self.assertEqual(len(self.visits), 2)
        self.assertTrue(all('/content_calendar?' in url for url in self.visits))
        evidence = self.diagnostic()
        self.assertTrue(evidence['recovered'])
        failed = evidence['failures'][0]
        self.assertEqual(failed['phase'], 'before_details')
        self.assertEqual(failed['month_candidates'], 0)
        self.assertEqual(failed['year_candidates'], 0)
        self.assertEqual(failed['day_cells'], 35)
        self.assertEqual(failed['headings'], ['Planner'])
        self.assertIn('September', failed['including_hidden'])
        self.assertNotIn('111222333444', json.dumps(evidence))

    async def test_persistent_missing_title_stops_after_one_read_only_reload(self):
        await self.serve(self.missing)
        with self.assertRaisesRegex(PublishStepError, '月份候选 0，年份候选 0') as error:
            await self.read()
        self.assertEqual(len(self.visits), 2)
        self.assertIn('planner_diagnostics', str(error.exception))
        evidence = self.diagnostic()
        self.assertFalse(evidence['recovered'])
        self.assertEqual(len(evidence['failures']), 2)

    async def test_conflicting_titles_are_not_reloaded_into_a_success(self):
        await self.serve(self.html + '<h2>October 2026</h2>', self.html)
        with self.assertRaisesRegex(PublishStepError, '月份候选 2'):
            await self.read()
        self.assertEqual(len(self.visits), 1)
        self.assertFalse(self.diagnostic()['recovered'])

    async def test_reloaded_grid_still_cannot_claim_missing_days_are_free(self):
        await self.serve(self.missing, self.html.rsplit('<div role="link"', 1)[0])
        with self.assertRaisesRegex(PublishStepError, '日期格'):
            await self.read()
        self.assertEqual(len(self.visits), 2)
        self.assertFalse(self.diagnostic()['recovered'])

    async def test_healthy_calendar_does_not_reload_or_write_diagnostics(self):
        await self.serve(self.html)
        with patch.object(self.page, 'bring_to_front', AsyncMock()) as foreground:
            self.assertTrue((await self.read()).decision_complete)
        foreground.assert_awaited_once()
        self.assertEqual(len(self.visits), 1)
        self.assertFalse((self.state / 'planner_diagnostics').exists())

    async def test_final_sweep_failure_discards_the_first_read_including_its_cards(self):
        card = '<div role="link">5:30 PM</div>'
        html = ('>30' + card + '</div>').join(self.html.rsplit('>30</div>', 1))
        await self.serve(html)
        calls = []
        async def detail(*args, **kwargs):
            calls.append(True)
            if len(calls) == 1:
                await self.page.locator('h2').evaluate_all('nodes=>nodes.forEach(n=>n.setAttribute("aria-hidden","true"))')
            return {'channels': ('facebook',), 'remote_ids': {'facebook': '111111' if len(calls)==1 else '222222'},
                    'text': 'first' if len(calls)==1 else 'fresh', 'delivery': 'scheduled', 'placement': 'feed'}
        with patch.object(month, 'read_item', AsyncMock(side_effect=detail)):
            inventory = await self.read()
        self.assertEqual(len(self.visits), 2)
        self.assertEqual(len(inventory.cards), 1)
        self.assertEqual(inventory.cards[0].remote_ids, (('facebook', '222222'),))
        self.assertEqual(self.diagnostic()['failures'][0]['phase'], 'after_details')

    async def test_reopen_does_not_bypass_asset_identity(self):
        wrong = self.html + '<script>history.replaceState(null,"","?asset_id=999999&business_id=555666777888")</script>'
        await self.serve(self.missing, wrong)
        with self.assertRaisesRegex(PublishStepError, '当前资产'):
            await self.read()
        self.assertEqual(len(self.visits), 2)
        self.assertFalse(self.diagnostic()['recovered'])

    async def test_diagnostic_is_bounded_and_never_copies_unrelated_headings(self):
        error = month.CalendarTitleUnavailable(set(), set(), ['Planner', 'private account 111222333444'])
        async def stalled(*args):
            await asyncio.Future()
        with patch.object(self.page, 'evaluate', AsyncMock(side_effect=stalled)), \
                patch.object(month, 'DIAGNOSTIC_TIMEOUT', .05):
            result = await month.calendar_failure_snapshot(self.page, error)
        self.assertEqual(result['capture_error'], 'TimeoutError')
        self.assertEqual(result['headings'][0], 'Planner')
        self.assertEqual(result['headings'][1]['length'], 28)
        self.assertNotIn('111222333444', json.dumps(result))

    async def test_diagnostic_disk_failure_preserves_the_original_title_error(self):
        (self.state / 'planner_diagnostics').write_text('file, not directory', encoding='utf-8')
        await self.serve(self.missing)
        with self.assertRaisesRegex(PublishStepError, '月份候选 0，年份候选 0'):
            await self.read()
        self.assertEqual(len(self.visits), 2)


if __name__ == '__main__':
    unittest.main()
