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
from publish.business_suite import PublishStepError


class MonthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(executable_path=Config().chrome_exe, headless=True)
        self.page = await self.browser.new_page()
        days = calendar.Calendar(firstweekday=6).itermonthdates(2026, 9)
        cells = ''.join(f'<div role="link" draggable="false"><span>{d.day}</span></div>' for d in days)
        await self.page.set_content('<h1>Planner</h1><h1>September</h1><h1>2026</h1>'
            '<button>Month</button><button>Content type: all</button><button>Shared to: all</button>' + cells)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

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
        await self.page.set_content('<div role="link" onmouseenter="tip.hidden=false">10:00 AM<img alt="Instagram"></div>'
            '<div role="tooltip" id="tip" hidden>' + month.RECOMMENDATION + '</div>')
        item = self.page.get_by_role('link')
        self.assertTrue(await month.is_recommendation(self.page, item, timeout=.3))
        await self.page.get_by_role('tooltip').evaluate("el=>{el.textContent='A future post';el.hidden=true}")
        await self.page.mouse.move(0, 0)
        self.assertFalse(await month.is_recommendation(self.page, item, timeout=.3))

    async def test_conflict_inventory_contains_unknown_channel_cards(self):
        rows = [{'date': date(2026, 9, 15), 'items': [{'index': 0, 'time': '10:00 AM'}]}]
        with patch.object(month, 'prepare', AsyncMock()), patch.object(month, 'read_grid', AsyncMock(return_value=rows)), \
                patch.object(month, 'read_item', AsyncMock(return_value={'channels': (), 'remote_ids': {},
                    'text': 'Unknown target', 'delivery': 'scheduled'})):
            result = await month.read(self.page, ui_timezone='America/Los_Angeles', business_timezone='Europe/Berlin')
        self.assertEqual(len(result.occupied), 1)
        self.assertFalse(result.channels_complete)

    async def test_published_metadata_binds_channels_collaborator_and_time(self):
        header = {'text': 'Post · Published on: Sat Sep 5, 11:09am · neakasa.global in collaboration with neakasa.de',
                  'platforms': ['Instagram'], 'authors': ['neakasa.global', 'neakasa.de']}
        with patch.object(month, 'accounts', return_value={'instagram': 'neakasa.de', 'facebook': 'Neakasa Deutschland'}):
            self.assertEqual(month.published_channels(header, date(2026, 9, 5), '11:09 AM'), ('instagram',))
            self.assertEqual(month.published_channels(dict(header, authors=['neakasa.deals']), date(2026, 9, 5), '11:09 AM'), ())
            with self.assertRaises(PublishStepError):
                month.published_channels(header, date(2026, 9, 4), '11:09 AM')

    async def test_hovered_schedule_control_is_not_an_expand_button(self):
        await self.page.locator(month.DAY_SELECTOR).nth(20).evaluate("el=>el.insertAdjacentHTML('beforeend','<button>Schedule\\u200b</button>')")
        self.assertEqual(len(await month.read_grid(self.page, timeout=10)), 35)

    async def test_schedule_aria_and_grid_must_agree_on_time_before_reading_remote_id(self):
        row = {'date': date(2026, 9, 15), 'cell_index': 0}
        item = {'index': 0, 'href': '', 'text': '1:00 AM', 'time': '1:00 AM',
                'aria': 'Full caption September 15, 2026, 2:00 AM'}
        spec = SimpleNamespace(attributes={
            'datetime_regex': r'(?P<date>September \d+, \d{4}), (?P<time>\d+:\d+ [AP]M)',
            'date_format': '%B %d, %Y', 'time_format': '%I:%M %p'})
        with patch.object(month, 'is_recommendation', AsyncMock(return_value=False)), \
                patch.object(month.bs, 'require_readback_evidence', return_value=spec), \
                patch.object(month.bs, '_open_channel_dialogs', AsyncMock()) as open_detail:
            with self.assertRaises(PublishStepError):
                await month.read_item(self.page, row, item)
            open_detail.assert_not_called()


if __name__ == '__main__':
    unittest.main()
