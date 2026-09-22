"""Replay one retained detail page against the month grid in an isolated browser.

Every Story/Post reading test boots the same way: an isolated Chromium, a September
grid with one entry, and a route that answers the detail page and its GraphQL calls
from fixtures. Each reading scenario keeps its own `tests_*.py` script, because
`tools.test_offline` gives every script the same wall-clock budget and these tests
spend theirs waiting out identity verification.
"""
import calendar
import json
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.config import Config
from publish import month_inventory as month

ACCOUNTS = {'facebook': 'Neakasa Deutschland', 'instagram': 'neakasa.de'}


class MonthDetailCase(unittest.IsolatedAsyncioTestCase):
    """A browser per test, the grid entry on `day` at `clock`, and the fixture responses.

    Subclasses set `content_id` / `day` / `clock`, then fill `self.documents` (one JSON
    document per response line) and `self.detail` in `load_responses`. Both are read at
    request time, so a test may replace them before calling `inventory`.
    """

    content_id = ''
    day = date(2026, 9, 1)
    clock = ''

    def load_responses(self):
        raise NotImplementedError

    async def asyncSetUp(self):
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(executable_path=Config().chrome_exe, headless=True)
        self.context = await self.browser.new_context()
        self.requests = []
        self.load_responses()
        async def route(request):
            url = request.request.url
            self.requests.append(url)
            if '/api/graphql/' in url:
                body = '\n'.join(json.dumps(document) for document in self.documents)
                await request.fulfill(content_type='application/json', body=body)
            elif '/object_insights/' in url:
                await request.fulfill(content_type='text/html; charset=utf-8', body=self.detail)
            else:
                await request.fulfill(content_type='text/html', body='<html></html>')
        await self.context.route('**/*', route)
        self.page = await self.context.new_page()
        await self.page.goto('https://business.facebook.com/latest/content_calendar')
        days = calendar.Calendar(firstweekday=6).itermonthdates(self.day.year, self.day.month)
        cells = ''.join(f'<div role="link" draggable="false"><span>{d.day}</span>' + (
            f'<a href="/latest/insights/object_insights/?content_id={self.content_id}">{self.clock}</a>'
            if d == self.day else '') + '</div>' for d in days)
        await self.page.set_content(
            f'<h1>{self.day.strftime("%B")}</h1><h1>{self.day.year}</h1>' + cells)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def inventory(self):
        with patch.object(month, 'prepare', AsyncMock()), patch.object(month, 'accounts', return_value=ACCOUNTS):
            return await month.read(self.page, ui_timezone='Asia/Shanghai', business_timezone='Asia/Shanghai', timeout=5)
