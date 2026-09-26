"""读取完整月历及手工项，排除推荐时段；未知条目使覆盖判定不完整。"""
from __future__ import annotations

from publish.channel_evidence import require as channel_proof
from core.chrome import _cdp_profile_matches, close_owned_page
import asyncio
import calendar
import hashlib
import json
import os
import re
import time
from collections import Counter
from datetime import date, datetime, timezone
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit
from uuid import uuid4

from playwright.async_api import expect, TimeoutError as BrowserTimeout, Error as BrowserError

from core.config import ROOT, cfg
from core.paid_model import atomic_write_json
from core.store import assert_physical_direct_path
from publish import business_suite as bs, selectors
from publish import planner_content as content, published_details, scheduled_details
from publish.insights_evidence import InsightsEvidence
from publish.channel_evidence import accounts, context_ids

DAY_SELECTOR = '[role="link"][draggable="false"]'
WEEK_DAY_SELECTOR = DAY_SELECTOR + ':not(' + DAY_SELECTOR + ' ' + DAY_SELECTOR + ')'
WEEK_HEADER = re.compile(r'^\s*Sun\s*(\d{1,2})\s*Mon\s*(\d{1,2})\s*Tue\s*(\d{1,2})\s*Wed\s*(\d{1,2})\s*Thu\s*(\d{1,2})\s*Fri\s*(\d{1,2})\s*Sat\s*(\d{1,2})\s*$')
WEEK_RECOMMENDATION = 'This week, your Instagram followers are most active at this time.'
RECOMMENDATION = 'Recommendations are based on when your followers were most active on Instagram and Facebook respectively in the last 7 days.'
TIME_PATTERN = re.compile(r'\b(\d{1,2}:\d{2}\s*[AP]M)\b')
MORE_PATTERN = re.compile(r'^\+\s*([1-9]\d*)\s+more$')
FILENAME = 'planner_controls.json'
# Release a heavy insights page before opening the next; UI readiness still
# governs clicks and no background/parallel history readers are started.
DETAIL_RELEASE_SECONDS = 1.0
DIAGNOSTIC_TIMEOUT = 3.0


class BrowserReadInterrupted(bs.PublishStepError):
    """Stop a month read when its browser/page disappears; never resume submitting."""


class CalendarTitleUnavailable(bs.PublishStepError):
    """Missing titles can recover on a fresh read; conflicting titles cannot."""
    def __init__(self, months, years, headings):
        self.retryable = len(months) <= 1 and len(years) <= 1 and (not months or not years)
        self.diagnostic = {'phase': 'grid', 'month_candidates': len(months),
                           'year_candidates': len(years), 'headings': headings}
        super().__init__(f'月历的月份与年份尚未唯一识别（月份候选 {len(months)}，年份候选 {len(years)}）')


# Click 225 in the bound 2026-09-20 recording targets a time-only link; the
# caption is on its third ancestor. Never climb into a day or a sibling card.
ITEM_DATA_JS = r'''n => {
  const labels=[];
  const cell=n.parentElement.closest('[role="link"][draggable="false"]');
  for(let p=n; p && p!==cell; p=p.parentElement) {
    if(p!==n && [...p.querySelectorAll('[role="link"],a')].some(x=>x!==n && !n.contains(x))) break;
    const label=p.getAttribute('aria-label');
    if(label && !labels.includes(label)) labels.push(label);
  }
  return {text:n.innerText,aria:n.getAttribute('aria-label')||'',labels,
    icons:[...n.querySelectorAll('img[alt]')].map(x=>x.alt).filter(s=>s==='Facebook'||s==='Instagram'),
    href:n.getAttribute('href')||n.querySelector('a[href]')?.getAttribute('href')||''};
}'''


class PlannerItemError(bs.PublishStepError):
    """Only structural context crosses the cache/API boundary, never exception text or URLs."""
    def __init__(self, row, item, stage, cause=None):
        self.variants = cause.variants if isinstance(cause, content.DetailReadError) else ()
        self.diagnostic = {'date': row['date'].isoformat(), 'time': item['time'],
                           'item_index': item['index'], 'stage': stage,
                           'has_href': bool(item.get('href')),
                           'text_length': len(item.get('text', '')),
                           'label_count': len(item.get('labels', [])),
                           'code': cause.code if isinstance(cause, content.DetailReadError) else
                                   'load_timeout' if isinstance(cause, (BrowserTimeout, TimeoutError)) else 'read_failed',
                           'placement': cause.placement if isinstance(cause, content.DetailReadError) else 'unknown',
                           'missing_fields': list(cause.missing_fields) if isinstance(cause, content.DetailReadError) else []}
        super().__init__(f"月历条目读取失败：{self.diagnostic['date']} {item['time']}（{stage}）")


def dates_for_cells(month: date, numbers: list[int]) -> tuple[date, ...]:
    expected = tuple(calendar.Calendar(firstweekday=6).itermonthdates(month.year, month.month))
    if numbers != [value.day for value in expected]:
        raise bs.PublishStepError('月历日期格尚未完整覆盖月份，不能把未加载日期判作空档')
    return expected


def week_dates(month_rows, numbers):
    """Bind all seven weekday numbers to one complete, independently read month."""
    candidates = [tuple(row['date'] for row in month_rows[index:index+7])
                  for index in range(0, len(month_rows), 7)]
    matches = [days for days in candidates if len(days) == 7 and [day.day for day in days] == numbers]
    if len(matches) != 1:
        raise bs.PublishStepError('周视图日期未与完整月历唯一对应，不能核对折叠条目')
    return matches[0]


def require_expanded_day(month_row, week_row):
    def identity(item):
        href = item.get('href', '')
        parsed = urlsplit(href)
        remote = parse_qs(parsed.query).get('content_id', [])
        if (parsed.hostname in (None, 'business.facebook.com')
                and parsed.path.rstrip('/') == '/latest/insights/object_insights'
                and len(remote) == 1 and re.fullmatch(r'\d{6,}', remote[0])):
            href = parsed.path.rstrip('/') + '?content_id=' + remote[0]
        return item['time'], href, tuple(sorted(set(item.get('icons', []))))
    visible = Counter(identity(item) for item in month_row['items'])
    expanded = Counter(identity(item) for item in week_row['items'])
    if (week_row['date'] != month_row['date'] or visible - expanded
            or sum(expanded.values()) != sum(visible.values()) + month_row.get('hidden_count', 0)):
        raise bs.PublishStepError('周视图条目与月历可见项及折叠数量不一致，不能确认完整列表')


async def visible_month(page):
    months, years = set(), set()
    # Snapshot the visible headings together; English UI labels do not depend
    # on the Windows Python locale. Both split and combined titles are valid.
    headings = await page.get_by_role('heading').all_inner_texts()
    for raw in headings:
        text = ' '.join(raw.split()).lower()
        parts = text.split()
        if text in bs._ENGLISH_MONTHS:
            months.add(bs._ENGLISH_MONTHS[text])
        elif re.fullmatch(r'[1-9]\d{3}', text):
            years.add(int(text))
        elif len(parts) == 2 and parts[0] in bs._ENGLISH_MONTHS and re.fullmatch(r'[1-9]\d{3}', parts[1]):
            months.add(bs._ENGLISH_MONTHS[parts[0]])
            years.add(int(parts[1]))
    if len(months) != 1 or len(years) != 1:
        raise CalendarTitleUnavailable(months, years, headings)
    return date(next(iter(years)), next(iter(months)), 1)


async def ready_grid(page, *, timeout, phase='grid'):
    """Wait for a coherent title and complete cells, even when Meta shows no loader."""
    deadline = time.monotonic() + max(0, timeout)
    while True:
        await settled(page, deadline - time.monotonic())
        try:
            month = await visible_month(page)
            numbers = await page.locator(DAY_SELECTOR).evaluate_all(r'''els => els.map(el => {
              const copy=el.cloneNode(true);
              copy.querySelectorAll('[role="link"],a,button,[role="button"]').forEach(n=>n.remove());
              const label=copy.textContent.trim(); return /^\d{1,2}$/.test(label) ? Number(label) : null;
            })''')
            return month, dates_for_cells(month, numbers)
        except bs.PublishStepError as exc:
            if time.monotonic() >= deadline:
                if isinstance(exc, CalendarTitleUnavailable):
                    exc.diagnostic['phase'] = phase
                raise
        await asyncio.sleep(min(.1, max(0, deadline - time.monotonic())))


async def settled(page, timeout):
    try:
        loaders = page.get_by_role('progressbar').or_(page.locator('[aria-busy="true"]'))
        await expect(loaders).to_have_count(0, timeout=max(1, timeout * 1000))
    except AssertionError as exc:
        raise bs.PublishStepError('月历仍在加载，未确认空档') from exc


async def read_grid(page, *, timeout=30, phase='grid'):
    """Sweep every day twice, waiting for loaders and newly mounted cards; read labels from DOM copies."""
    deadline = time.monotonic() + timeout
    previous = None
    while True:
        month, dates = await ready_grid(page, timeout=deadline - time.monotonic(), phase=phase)
        result = await read_days(page, dates, timeout=deadline - time.monotonic())
        if await ready_grid(page, timeout=deadline - time.monotonic(), phase=phase) != (month, dates):
            raise bs.PublishStepError('读取过程中月份已变化，请重试')
        if result == previous:
            return result
        if time.monotonic() >= deadline:
            raise bs.PublishStepError('月历条目仍在变化，未确认完整范围')
        previous = result
        await asyncio.sleep(min(.4, max(0, deadline - time.monotonic())))


async def read_days(page, dates, *, timeout, week=False):
    deadline = time.monotonic() + max(0, timeout)
    selector = WEEK_DAY_SELECTOR if week else DAY_SELECTOR
    cells = page.locator(selector)
    result = []
    for index, day in enumerate(dates):
        cell = cells.nth(index)
        # DOM scrolling avoids background RAF throttling; loaders and two sweeps determine readiness.
        # evaluate_all snapshots avoid acquiring/disposing an ElementHandle for every cell.
        # Still visit every day: scrolling can mount cards and loaders in an otherwise empty cell.
        present = await cell.evaluate_all('''els => {
          if(els.length!==1) return false;
          els[0].scrollIntoView({block:'center', behavior:'instant'}); return true;
        }''')
        if not present:
            raise bs.PublishStepError('月历日期格在读取过程中消失，请重新读取')
        await settled(page, deadline - time.monotonic())
        # Top-level descendant links represent one card; nested wrappers repeat its time.
        snapshot = await cell.or_(cell.get_by_role('button')).evaluate_all('''(nodes, selector) => {
          const cells=nodes.filter(n=>n.matches(selector));
          if(cells.length!==1) return null;
          const el=cells[0];
          const items=[...el.querySelectorAll('[role="link"],a')]
            .filter(n=>n.parentElement.closest('[role="link"],a')===el)
            .map((n,index)=>({index,...(''' + ITEM_DATA_JS + ''')(n)}));
          return {items,extra:nodes.filter(n=>n!==el).map(n=>n.innerText)};
        }''', selector)
        if snapshot is None:
            raise bs.PublishStepError('月历日期格在读取过程中消失，请重新读取')
        items, extra = snapshot['items'], snapshot['extra']
        if any(' '.join(label.replace('\u200b', '').split()) not in {'Schedule', 'Create'} for label in extra):
            raise bs.PublishStepError('日期格存在未处理的展开控件，月历读取不完整')
        cards, hidden_count = [], 0
        for item in items:
            # Service month DOM: <a role="link" href="#">+ 1 more</a>
            # is an overflow control, not a scheduled object without a time.
            more = MORE_PATTERN.fullmatch(' '.join(item['text'].split()))
            if more and item['href'] == '#':
                if hidden_count:
                    raise bs.PublishStepError('日期格出现多个折叠入口，未确认完整列表')
                hidden_count = int(more[1])
                continue
            values = TIME_PATTERN.findall(' '.join(item['text'].split()))
            # A scheduled aria label may hold date+caption instead of the compact time.
            if not values:
                values = TIME_PATTERN.findall(' '.join(item['aria'].split()))
            if len(values) != 1:
                raise bs.PublishStepError('月历条目时刻无法唯一读取，保留现场待探查')
            item['time'] = ' '.join(values[0].split())
            cards.append(item)
        result.append({'date': day, 'cell_index': index, 'items': cards,
                       **({'view': 'week'} if week else {}),
                       **({'hidden_count': hidden_count} if hidden_count else {})})
    return result


async def ready_week(page, month_rows, *, timeout, changed_from=None):
    deadline = time.monotonic() + max(0, timeout)
    while True:
        await settled(page, deadline - time.monotonic())
        labels = await page.get_by_text(WEEK_HEADER).all_inner_texts()
        candidates = [WEEK_HEADER.fullmatch(' '.join(label.split())) for label in labels]
        try:
            if len(candidates) != 1 or candidates[0] is None:
                raise bs.PublishStepError('周视图的七个日期标题尚未完整读取')
            days = week_dates(month_rows, [int(number) for number in candidates[0].groups()])
            headings = [' '.join(label.lower().split()) for label in await page.get_by_role('heading').all_inner_texts()]
            month_labels = [re.split(r'\s*[-–]\s*', label) for label in headings]
            month_labels = [parts for parts in month_labels if all(part in bs._ENGLISH_MONTHS for part in parts)]
            years = {int(value) for label in headings if re.fullmatch(r'\d{4}(?:\s*[-–]\s*\d{4})?', label)
                     for value in re.findall(r'\d{4}', label)}
            if (len(month_labels) != 1 or len(month_labels[0]) > 2 or not {day.month for day in days}.issubset(
                    {bs._ENGLISH_MONTHS[label] for label in month_labels[0]}) or days[0].year not in years
                    or not years.issubset({day.year for day in days})):
                raise bs.PublishStepError('周视图月份或年份与已读取的月历不一致')
            if await page.locator(WEEK_DAY_SELECTOR).count() != 7 or days == changed_from:
                raise bs.PublishStepError('周视图尚未完成切换')
            return days
        except bs.PublishStepError:
            if time.monotonic() >= deadline:
                raise
        await asyncio.sleep(.1)


async def read_week(page, month_rows, *, timeout):
    deadline = time.monotonic() + timeout
    previous = None
    while True:
        days = await ready_week(page, month_rows, timeout=deadline - time.monotonic())
        rows = await read_days(page, days, timeout=deadline - time.monotonic(), week=True)
        if any(row.get('hidden_count') for row in rows):
            raise bs.PublishStepError('周视图仍存在折叠条目，未读到完整列表')
        if days != await ready_week(page, month_rows, timeout=deadline - time.monotonic()):
            raise bs.PublishStepError('读取条目期间周视图日期已变化')
        if rows == previous:
            return rows
        if time.monotonic() >= deadline:
            raise bs.PublishStepError('周视图条目仍在变化，未确认完整范围')
        previous = rows
        await asyncio.sleep(.4)


async def detail_grids(page, month_rows, selected_rows, *, timeout):
    """Read folded days and Instagram captions in the recorded complete week view."""
    expanded = []
    for row in selected_rows:
        if row.get('hidden_count'):
            expanded.append(row)
            continue
        for item in row['items']:
            if (not item.get('href') and item.get('icons') == ['Instagram']
                    and await recommendation_state(page, item_locator(page, row, item)) != 'shown'):
                expanded.append(row)
                break
    ordinary = [row for row in selected_rows if row not in expanded]
    if ordinary:
        yield ordinary
    if not expanded:
        return
    await page.get_by_role('button', name='Week', exact=True).click(timeout=timeout * 1000)
    for name in ('Content type: all', 'Shared to: all'):
        await expect(page.get_by_role('button', name=name, exact=True)).to_have_count(1, timeout=timeout * 1000)
    pending = {row['date']: row for row in expanded}
    while pending:
        target = min(pending)
        days = await ready_week(page, month_rows, timeout=timeout)
        for _ in range(len(month_rows) // 7):
            if target in days:
                break
            direction = 'Left' if target < days[0] else 'Right'
            old = days
            await page.get_by_role('button', name=direction, exact=True).click(timeout=timeout * 1000)
            days = await ready_week(page, month_rows, timeout=timeout, changed_from=old)
            if (days[0] - old[0]).days != (-7 if direction == 'Left' else 7):
                raise bs.PublishStepError('周视图没有按相邻七天切换，不能核对日期')
        else:
            raise bs.PublishStepError('未能定位目标日期的完整周视图')
        rows = await read_week(page, month_rows, timeout=timeout)
        selected = [row for row in rows if row['date'] in pending]
        for row in selected:
            require_expanded_day(pending.pop(row['date']), row)
        yield selected
        if rows != await read_week(page, month_rows, timeout=timeout):
            raise bs.PublishStepError('核对详情期间周视图条目已变化，请重新读取')
    await page.get_by_role('button', name='Month', exact=True).click(timeout=timeout * 1000)


def week_caption(row, item):
    # The service week card has one caption-labelled wrapper around its time
    # link. ITEM_DATA_JS stops before the day and before sibling cards.
    if row.get('view') != 'week':
        return ''
    labels = {bs._card_text(text) for text in item.get('labels', []) if text.strip()
              and not TIME_PATTERN.fullmatch(' '.join(text.split()))}
    if len(labels) > 1:
        raise content.DetailReadError('structure_unknown', missing_fields=('unique_week_caption',))
    return next(iter(labels), '')


def unreadable_item(tooltip):
    """An item with neither a detail link nor complete text stays a real task.

    It cannot be skipped, so the refusal carries why the recommendation tooltip
    did not settle it; without that the run only reports that time ran out.
    """
    return content.DetailReadError('structure_unknown', missing_fields=(
        'item_caption', *(('recommendation_' + tooltip,) if tooltip else ())))


def item_locator(page, row, item):
    cell = page.locator(WEEK_DAY_SELECTOR if row.get('view') == 'week' else DAY_SELECTOR).nth(row['cell_index'])
    # Same structural relationship as read_grid; no clickable navigation is included.
    return cell.locator('xpath=.//*[@role="link" or self::a][not(ancestor::*[@role="link" or self::a][ancestor::*[@draggable="false"]])]') .nth(item['index'])


async def recommendation_state(page, item, *, timeout=1.5):
    """Hover the card and report which step decided it, not just whether it passed.

    Only `shown` skips a slot. A placeholder that is not recognised is read as a
    real task and fails far away in item_ready, so the step that refused has to
    reach the diagnostic: `stale` a tooltip that never cleared, `unreachable` a
    card that could not be hovered, `absent` a hover that opened no tooltip.
    """
    await page.mouse.move(0, 0)
    # Keep the recorded recommendation sentence as positive evidence; extra
    # tooltip headings must not turn it into an unread scheduled post.
    tip = page.get_by_role('tooltip').filter(has_text=RECOMMENDATION)
    try:
        await tip.wait_for(state='hidden', timeout=timeout * 1000)
    except Exception:
        return 'stale'
    try:
        await item.hover(timeout=timeout * 1000)
    except Exception:
        return 'unreachable'
    try:
        await tip.wait_for(state='visible', timeout=timeout * 1000)
    except Exception:
        return 'absent'
    return 'shown'


async def read_item(page, row, item, *, timeout=30, ui_timezone=None, card_spec=None):
    stage = 'item_ready'
    try:
        ready = await ready_item(page, row, item, timeout=timeout, card_spec=card_spec)
        if ready is None:
            return None
        node, raw = ready
        stage = 'published_detail' if item['href'] else 'scheduled_detail'
        return await read_item_detail(page, row, item, node, raw, timeout=timeout, ui_timezone=ui_timezone,
                                      card_spec=card_spec)
    except (BrowserReadInterrupted, bs.PlannerDialogCloseError, bs.PlannerNavigationError):
        raise
    except Exception as exc:
        raise_if_browser_lost(page, exc)
        raise PlannerItemError(row, item, stage, exc) from exc
    finally:
        # Hover cards must not contaminate the final month sweep or the next slot.
        try:
            await page.mouse.move(0, 0)
        except BrowserError as exc:
            raise_if_browser_lost(page, exc)


def raise_if_browser_lost(page, cause):
    """Connection loss is fatal for this read, never an unknown historical card."""
    browser = page.context.browser
    if page.is_closed() or (browser is not None and not browser.is_connected()):
        raise BrowserReadInterrupted('读取月历时发布浏览器或标签页已关闭、连接已断开；本次读取已停止。'
                                  '请检查发布 Chrome，再根据发布回执决定是否重试；不会自动重启或重新提交。') from cause
    nested, seen = cause, set()
    while nested is not None and id(nested) not in seen:
        seen.add(id(nested))
        if isinstance(nested, BrowserError) and 'crash' in str(nested).lower():
            raise BrowserReadInterrupted('读取月历时发布浏览器页面发生崩溃；本次读取已停止。'
                '请保留系统故障记录并检查发布回执；不会自动重启或重新提交。') from cause
        nested = nested.__cause__ or nested.__context__


async def ready_item(page, row, item, *, timeout, card_spec=None):
    """Re-read the card and bind a newly opened hover link to its caption and grid time."""
    node = item_locator(page, row, item)
    deadline = time.monotonic() + timeout
    fresh = await node.evaluate(ITEM_DATA_JS, timeout=max(1, timeout * 1000))
    require_same_item(item, fresh)
    spec = None
    before_hover = set()
    tooltip = ''
    if not fresh['href']:
        spec = card_spec or bs.require_readback_evidence()
        if (row.get('view') == 'week' and not fresh['labels']
                and await node.get_by_text(WEEK_RECOMMENDATION, exact=True).count() == 1
                and await node.get_by_role('button', name='Schedule', exact=True).count() == 1):
            return None
        before_hover = set(await page.get_by_role('link').all_inner_texts())
        tooltip = ('absent' if week_caption(row, fresh) else await recommendation_state(
            page, node, timeout=min(1.5, max(.001, deadline - time.monotonic()))))
        if tooltip == 'shown':
            return None
    previous, stable_since = None, time.monotonic()
    expected = datetime.combine(row['date'], datetime.strptime(item['time'], '%I:%M %p').time())
    while True:
        if time.monotonic() >= deadline:
            raise unreadable_item(tooltip)
        try:
            fresh = await node.evaluate(ITEM_DATA_JS, timeout=max(1, (deadline - time.monotonic()) * 1000))
        except BrowserTimeout:
            # A deadline-shortened evaluate reports a browser fault for what is
            # really this item staying unreadable; it must not hide the reason.
            if time.monotonic() < deadline:
                raise
            raise unreadable_item(tooltip) from None
        require_same_item(item, fresh)
        raw = ''
        if not fresh['href'] and not week_caption(row, fresh):
            spec = spec or card_spec or bs.require_readback_evidence()
            candidates = [fresh['aria'], fresh['text'], *fresh['labels']]
            # The recorded hover preview is itself a link, outside the time node.
            # A link already visible before hovering cannot establish this association.
            captions = [' '.join(label.split()) for label in fresh['labels']
                        if not TIME_PATTERN.fullmatch(' '.join(label.split()))]
            for text in await page.get_by_role('link').all_inner_texts():
                normalized = ' '.join(text.split())
                match = re.search(spec.attributes['datetime_regex'], normalized)
                if text not in before_hover and match and normalized[:match.start()].strip() in captions:
                    candidates.append(normalized)
            complete = set()
            for text in candidates:
                text = ' '.join(text.split())
                parsed = bs._entry_naive(text, spec)
                if parsed is not None:
                    if parsed != expected:
                        raise bs.PublishStepError('月历日期格与条目完整时刻不一致')
                    match = re.search(spec.attributes['datetime_regex'], text)
                    if match and text[:match.start()].strip():
                        complete.add(text)
            if len(complete) > 1:
                raise bs.PublishStepError('月历条目出现多个不同的完整正文')
            raw = next(iter(complete), '')
        value = (fresh, raw)
        if value != previous:
            previous, stable_since = value, time.monotonic()
        if time.monotonic() - stable_since >= .4:
            # Hydration is accepted only after checking the original slot identity;
            # read() compares this refreshed DOM snapshot with the final sweep.
            item.update(fresh)
            return node, raw
        await asyncio.sleep(min(.2, max(0, deadline - time.monotonic())))


def require_same_item(item, fresh):
    clocks = TIME_PATTERN.findall(' '.join(fresh['text'].split()))
    if not clocks:
        clocks = TIME_PATTERN.findall(' '.join(fresh['aria'].split()))
    if (clocks != [item['time']] or (item['href'] and fresh['href'] != item['href'])
            or item.get('icons') and fresh.get('icons') != item['icons']):
        raise bs.PublishStepError('月历条目的时刻或链接已变化，请重新读取')
    known = {' '.join(text.split()) for text in
             [item.get('text', ''), item.get('aria', ''), *item.get('labels', [])]
             if text.strip() and not TIME_PATTERN.fullmatch(' '.join(text.split()))}
    current = {' '.join(text.split()) for text in [fresh['text'], fresh['aria'], *fresh['labels']]}
    if not known.issubset(current):
        raise bs.PublishStepError('月历条目已读取的正文发生变化，请重新读取')


async def read_item_detail(page, row, item, node, raw, *, timeout, observe_detail=None, ui_timezone=None,
                           card_spec=None, observe_scheduled=None):
    if item['href']:
        url = urljoin(page.url, item['href'])
        parsed = urlsplit(url)
        query = parse_qs(parsed.query)
        context_query = parse_qs(urlsplit(page.url).query)
        for key in ('asset_id', 'business_id'):
            if key in context_query:
                query[key] = context_query[key]
        url = urlunsplit(parsed._replace(query=urlencode(query, doseq=True)))
        remote = parse_qs(parsed.query).get('content_id', [''])[0]
        if (parsed.hostname != 'business.facebook.com' or parsed.path.rstrip('/') != '/latest/insights/object_insights'
                or not re.fullmatch(r'\d{6,}', remote)):
            raise content.DetailReadError('unsupported_type', missing_fields=('detail_adapter',))
        detail = await page.context.new_page()
        evidence = InsightsEvidence(detail, remote)
        evidence.start()
        observer = None
        try:
            if observe_detail:
                observer = observe_detail(detail)
                evidence.on_channel = getattr(observer, 'begin_channel', None)
            try:
                await detail.goto(url, wait_until='domcontentloaded', timeout=timeout * 1000)
            except BrowserError as exc:
                raise_if_browser_lost(detail, exc)
                raise content.DetailReadError('navigation_failed') from exc
            destination = urlsplit(detail.url)
            if (destination.hostname != parsed.hostname or destination.path.rstrip('/') != parsed.path.rstrip('/')
                    or parse_qs(destination.query).get('content_id') != [remote]):
                raise content.DetailReadError('navigation_failed')
            await detail.bring_to_front()
            try:
                variants = await published_details.read(detail, row, item, accounts(), timeout=timeout, evidence=evidence,
                                                       ui_timezone=ui_timezone or cfg().get('publish', 'ui_timezone', ''))
            except content.DetailReadError as exc:
                for variant in exc.variants:
                    variant['source_content_id'] = remote
                raise
            for variant in variants:
                variant['source_content_id'] = remote
            return {'variants': variants}
        except Exception as exc:
            raise_if_browser_lost(detail, exc)
            raise
        finally:
            try:
                await evidence.finish()
                if observer:
                    await observer.finish()
            finally:
                await close_owned_page(detail)
                await asyncio.sleep(DETAIL_RELEASE_SECONDS)
    spec = card_spec or bs.require_readback_evidence()
    caption = week_caption(row, item)
    if caption:
        async def observe_week_identity(dialog, ids):
            await scheduled_details.verify_preview_owner(dialog, ids, accounts(), timeout=timeout)
            if observe_scheduled is not None:
                await observe_scheduled(dialog, ids)

        ids = await bs._open_channel_dialogs(page, node, spec, timeout=timeout, observe_detail=observe_week_identity)
        if len(ids) != 1 or {name.lower() for name in item.get('icons', [])} != set(ids):
            raise content.DetailReadError('identity_unverified', placement='feed', missing_fields=('channel_identity',))
        return {'channels': tuple(ids), 'remote_ids': ids, 'accounts': {key: accounts()[key] for key in ids},
                'text': caption, 'delivery': 'scheduled', 'placement': 'feed', 'caption_status': 'present',
                'ui_at': datetime.combine(row['date'], datetime.strptime(item['time'], '%I:%M %p').time())}
    if not raw:
        material = None

        async def prepare_dialog(dialog):
            await scheduled_details.expand(dialog, timeout=timeout, expected_accounts=accounts())

        async def observe_dialog(dialog, ids):
            nonlocal material
            material = await scheduled_details.read(dialog, row['date'], item['time'], ids, accounts())
            if observe_scheduled is not None:
                await observe_scheduled(dialog, ids)

        await bs._open_channel_dialogs(page, node, spec, timeout=timeout,
            prepare_detail=prepare_dialog, observe_detail=observe_dialog)
        if material is None:
            raise content.DetailReadError('identity_unverified', placement='feed', missing_fields=('channel_identity',))
        return material
    parsed = bs._entry_naive(raw, spec)
    expected = datetime.combine(row['date'], datetime.strptime(item['time'], '%I:%M %p').time())
    if parsed is None or parsed != expected:
        raise bs.PublishStepError('未知月历条目没有完整日期与正文证据；不能当作推荐时段跳过')
    dialog_options = {'observe_detail': observe_scheduled} if observe_scheduled is not None else {}
    remote_ids = await bs._open_channel_dialogs(page, node, spec, timeout=timeout, **dialog_options)
    if len(remote_ids) != 1:
        raise content.DetailReadError('identity_unverified', placement='feed',
                                      missing_fields=('channel_identity',))
    match = re.search(spec.attributes['datetime_regex'], raw)
    caption = raw[:match.start()].strip() if match else raw
    return {'channels': tuple(sorted(remote_ids)), 'remote_ids': remote_ids,
            'text': caption, 'delivery': 'scheduled', 'placement': 'feed',
            'caption_status': 'present', 'accounts': {key:accounts()[key] for key in remote_ids}}


async def read_scheduled_target(page, card, *, ui_timezone, observe_detail, timeout=30, card_spec=None):
    """Reacquire one scheduled object; ordinary month reads never fetch its media."""
    if (len(card.channels) != 1 or card.delivery != 'scheduled' or card.placement != 'feed'
            or card.read_status != 'complete' or card.caption_status != 'present'):
        raise bs.PublishStepError('图片复验只接受已核实的单渠道 Feed 排期')
    spec = card_spec or bs.require_readback_evidence()
    channel = card.channels[0]
    if (dict(card.accounts) != {channel: accounts()[channel]}
            or spec.attributes.get(channel + '_account_token') != accounts()[channel]):
        raise bs.PublishStepError('取图目标账号与当前发布账号不一致')
    local = card.at.astimezone(bs.resolve_ui_timezone(ui_timezone)).replace(tzinfo=None)
    rows = await read_grid(page, timeout=timeout)
    selected = [row for row in rows if row['date'] == local.date()]
    captured = None
    async for batch in detail_grids(page, rows, selected, timeout=timeout):
        captured = await read_scheduled_rows(page, card, batch, local=local, spec=spec,
            ui_timezone=ui_timezone, observe_detail=observe_detail, timeout=timeout)
    if captured is None or rows != await read_grid(page, timeout=timeout):
        raise bs.PublishStepError('取图期间原排期对象或月历发生变化')
    return captured


async def read_scheduled_rows(page, card, rows, *, local, spec, ui_timezone, observe_detail, timeout):
    channel = card.channels[0]
    matches = []
    for row in rows:
        if row['date'] != local.date():
            continue
        for item in row['items']:
            if item['href'] or datetime.strptime(item['time'], '%I:%M %p').time() != local.time():
                continue
            ready = await ready_item(page, row, item, timeout=timeout, card_spec=spec)
            if ready is None:
                continue
            node, raw = ready
            match = re.search(spec.attributes['datetime_regex'], raw)
            if not raw:
                detail = await read_item_detail(page, row, item, node, raw, timeout=timeout,
                                               card_spec=spec, ui_timezone=ui_timezone)
                if (detail['remote_ids'] == dict(card.remote_ids)
                        and bs._card_text(detail['text']) == bs._card_text(card.rendered)):
                    matches.append((row, item))
            elif (match and bs._entry_naive(raw, spec) == local
                  and bs._card_text(raw[:match.start()]) == bs._card_text(card.rendered)):
                matches.append((row, item))
    if len(matches) != 1:
        raise bs.PublishStepError('取图时未能重新定位唯一的原排期卡片')
    row, item = matches[0]
    ready = await ready_item(page, row, item, timeout=timeout, card_spec=spec)
    if ready is None:
        raise bs.PublishStepError('原排期卡片已变化')
    node, raw = ready
    captured = []

    async def observe(dialog, ids):
        if ids != dict(card.remote_ids) or set(ids) != {channel}:
            raise bs.PublishStepError('重新打开的排期详情不是原 remote ID')
        caption = week_caption(row, item) or ((await scheduled_details.read(dialog, row['date'], item['time'], ids, accounts()))['text']
                   if not raw else await bs._node_text(dialog))
        if bs._card_text(card.rendered) not in bs._card_text(caption):
            raise bs.PublishStepError('重新打开的排期详情没有完整冻结正文')
        captured.append(await observe_detail(dialog))

    try:
        detail = await read_item_detail(page, row, item, node, raw, timeout=timeout,
            card_spec=spec, ui_timezone=ui_timezone, observe_scheduled=observe)
    except content.DetailReadError as exc:
        raise bs.PublishStepError('重新打开的排期详情身份未核实') from exc
    finally:
        await page.mouse.move(0, 0)
    if (len(captured) != 1 or detail['remote_ids'] != dict(card.remote_ids)
            or detail['accounts'] != dict(card.accounts)
            or bs._card_text(detail['text']) != bs._card_text(card.rendered)):
        raise bs.PublishStepError('取图期间原排期对象或月历发生变化')
    return captured[0]


async def open_calendar(page, asset_context, *, timeout=30):
    """打开指定业务资产的月历。调用方负责提供资产，这里不读录证文件。"""
    url = selectors.CONTENT_CALENDAR_URL + '?' + urlencode(asset_context)
    # This is the caller-owned Planner tab, never the composer or a human tab.
    # Readiness must not depend on an inactive tab being rendered in the background.
    await page.bring_to_front()
    await page.goto(url, wait_until='domcontentloaded', timeout=timeout * 1000)
    await bs.assert_page_usable(page)
    if context_ids(page.url) != asset_context:
        raise bs.PublishStepError('月历当前资产与本次发布目标不一致，请核对发布账号')
    await page.get_by_role('button', name='Month', exact=True).click(timeout=timeout * 1000)
    for prefix in ('Content type:', 'Shared to:'):
        control = page.get_by_role('button', name=re.compile('^' + re.escape(prefix)))
        if ' '.join((await control.inner_text()).split()) != prefix + ' all':
            await control.click()
            await page.get_by_role('menuitem', name='All', exact=True).click()
        await expect(control).to_have_text(prefix + ' all', timeout=timeout * 1000)


async def prepare(page, *, timeout=30):
    proof = require()
    await open_calendar(page, proof['context_ids'], timeout=timeout)


def moments(day, clock, ui_zone, business_zone):
    naive = datetime.combine(day, datetime.strptime(clock, '%I:%M %p').time())
    first, second = naive.replace(tzinfo=ui_zone, fold=0), naive.replace(tzinfo=ui_zone, fold=1)
    if first.astimezone(timezone.utc).astimezone(ui_zone).replace(tzinfo=None) != naive:
        raise bs.PublishStepError('月历时刻处于夏令时不存在的区间')
    return tuple(at.astimezone(business_zone) for at in ((first, second) if first.utcoffset() != second.utcoffset() else (first,)))


async def calendar_failure_snapshot(page, error):
    """Capture the failing tab before cleanup, without input values or URL parameters."""
    snapshot = dict(error.diagnostic, observed_at=datetime.now(timezone.utc).isoformat())

    async def collect():
        snapshot.update(await page.evaluate('''selector => ({
          document: {ready: document.readyState, visibility: document.visibilityState,
                     lang: document.documentElement.lang},
          day_cells: document.querySelectorAll(selector).length,
          raw_heading_nodes: [...document.querySelectorAll('h1,h2,h3,h4,h5,h6,[role="heading"]')]
            .slice(0,32).map(n=>({tag:n.tagName, role:n.getAttribute('role'),
              aria_hidden:!!n.closest('[aria-hidden="true"]'), inert:!!n.closest('[inert]'),
              css_visible:!!(n.getClientRects().length && getComputedStyle(n).visibility==='visible'),
              text_length:n.textContent.length}))
        })''', DAY_SELECTOR))
        snapshot['including_hidden'] = await page.get_by_role('heading', include_hidden=True).all_text_contents()
        snapshot['dialogs'] = await page.get_by_role('dialog').count()
        parsed = urlsplit(page.url)
        snapshot['surface'] = ('calendar' if parsed.hostname == 'business.facebook.com'
            and parsed.path.rstrip('/') == urlsplit(selectors.CONTENT_CALENDAR_URL).path.rstrip('/') else 'other')

    try:
        await asyncio.wait_for(collect(), DIAGNOSTIC_TIMEOUT)
    except Exception as exc:
        snapshot['capture_error'] = type(exc).__name__
    for key in ('headings', 'including_hidden'):
        labels = []
        for text in snapshot.get(key, [])[:16]:
            text = ' '.join(text.split())
            parts = text.lower().split()
            known = (text.lower() in {'planner', 'goals', *bs._ENGLISH_MONTHS}
                or re.fullmatch(r'[1-9]\d{3}', text)
                or len(parts) == 2 and parts[0] in bs._ENGLISH_MONTHS and re.fullmatch(r'[1-9]\d{3}', parts[1]))
            labels.append(text if known else {'length': len(text), 'sha256': hashlib.sha256(text.encode()).hexdigest()})
        snapshot[key] = labels
    return snapshot


def save_calendar_diagnostic(name, data):
    """A diagnostic write failure must not replace the original browser failure."""
    try:
        state = cfg().state_dir
        folder = assert_physical_direct_path(state, state / 'planner_diagnostics',
                                             kind='directory', label='月历诊断目录')
        path = assert_physical_direct_path(folder, folder / name, kind='file', label='月历诊断文件')
        atomic_write_json(path, data, indent=2)
        return str(path)
    except Exception as exc:
        print('月历诊断未能保存：' + type(exc).__name__)
        return ''


async def read(page, *, ui_timezone, business_timezone, timeout=30, run=None, detail_range=None):
    """Read a fresh inventory; recover a missing title once without replaying a submission."""
    data = {'schema_version': 1, 'process_id': os.getpid(), 'recovered': False, 'failures': []}
    name = uuid4().hex + '.json'
    try:
        for read_index in range(2):
            try:
                result = await _read_once(page, ui_timezone=ui_timezone, business_timezone=business_timezone,
                                         timeout=timeout, run=run, detail_range=detail_range)
                data['recovered'] = bool(data['failures'])
                return result
            except CalendarTitleUnavailable as exc:
                data['failures'].append(await calendar_failure_snapshot(page, exc))
                saved = save_calendar_diagnostic(name, data)
                if read_index or not exc.retryable:
                    if saved:
                        exc.args = (str(exc) + '；诊断已保存：' + saved,)
                    raise
                print('月历标题未就绪；仅重新读取一次同一资产的月历，不重复提交。')
    finally:
        if data['failures']:
            save_calendar_diagnostic(name, data)


async def _read_once(page, *, ui_timezone, business_timezone, timeout=30, run=None, detail_range=None):
    """Check the whole grid; optionally expand only complete UI days needed by one decision.

    Never select individual cards by the outer clock: aggregate channel times
    can differ. Relevant days still read every item and verify each channel.
    A scoped result cannot certify another day or an entire-month deletion.
    """
    ui_zone, business_zone = bs.resolve_ui_timezone(ui_timezone), bs.resolve_ui_timezone(business_timezone)
    selected_dates = None
    if detail_range is not None:
        start, end = detail_range
        if (start.tzinfo is None or end.tzinfo is None or start.utcoffset() is None
                or end.utcoffset() is None or start.timestamp() > end.timestamp()):
            raise ValueError('月历详情范围须为带时区的有序时刻')
        selected_dates = start.astimezone(ui_zone).date(), end.astimezone(ui_zone).date()
    if run is None:
        await prepare(page, timeout=timeout)
        card_spec = None
    else:
        await open_calendar(page, run.asset_context, timeout=timeout)
        card_spec = run.planner_card
    rows = await read_grid(page, timeout=timeout, phase='before_details')
    detail_rows = rows if selected_dates is None else [row for row in rows
        if selected_dates[0] <= row['date'] <= selected_dates[1]]
    cards, occupied, diagnostics = [], {}, []
    async for batch in detail_grids(page, rows, detail_rows, timeout=timeout):
        for row in batch:
            for item in row['items']:
                try:
                    material = await read_item(page, row, item, timeout=timeout, ui_timezone=ui_timezone,
                                               card_spec=card_spec)
                except PlannerItemError as exc:
                    diagnostics.append(exc.diagnostic)
                    # A failed detail read proves neither a post nor a schedule.
                    # Retain its uncertainty for vacancy checks, not a false status.
                    material = {'channels': (), 'remote_ids': {}, 'text': '',
                                'delivery': 'unknown',
                                'placement': exc.diagnostic['placement'],
                                'time_verified': False, 'diagnostic_index': len(diagnostics)-1,
                                'read_status': 'unsupported' if exc.diagnostic['code']=='unsupported_type' else
                                               'unavailable' if exc.diagnostic['code']=='permission_denied' else 'incomplete'}
                    material = {'variants': [*exc.variants, material]}
                if material is None:
                    continue
                for variant in material.get('variants', [material]):
                    diagnostic_index = variant.get('diagnostic_index')
                    if variant.get('read_status', 'complete') != 'complete' and diagnostic_index is None:
                        diagnostic_index = len(diagnostics)
                        diagnostics.append({'date':row['date'].isoformat(), 'time':item['time'],
                            'item_index':item['index'], 'stage':'detail',
                            'code':'missing_fields', 'placement':variant.get('placement','unknown'),
                            'missing_fields':list(variant.get('missing_fields', ()))})
                    at = variant.get('ui_at')
                    for moment in moments(at.date() if at else row['date'],
                                          at.strftime('%I:%M %p') if at else item['time'], ui_zone, business_zone):
                        occupied[moment.timestamp()] = moment
                        cards.append(bs.RemotePlannerCard(moment, variant['channels'],
                            tuple(sorted(variant['remote_ids'].items())), variant['text'],
                            hashlib.sha256(variant['text'].encode('utf-8')).hexdigest(), variant['delivery'],
                            placement=variant.get('placement','unknown'), media_kind=variant.get('media_kind','unknown'),
                            caption_status=variant.get('caption_status','unknown'),
                            accounts=tuple(sorted(variant.get('accounts',{}).items())),
                            relationships=tuple(variant.get('relationships',())),
                            read_status=variant.get('read_status','complete'),
                            time_verified=variant.get('time_verified', True), diagnostic_index=diagnostic_index,
                            source_content_id=variant.get('source_content_id',''),
                            permalinks=tuple(sorted((variant.get('permalinks') or {}).items()))))
    # Verify a final sweep so edits/late rendering during detail reads invalidate the inventory.
    if rows != await read_grid(page, timeout=timeout, phase='after_details'):
        raise bs.PublishStepError('核对详情期间远端月历已更新，请重新读取')
    return bs.RemoteSlotInventory(tuple(occupied[key] for key in sorted(occupied)), ui_timezone,
        detail_rows[0]['date'] if detail_rows else None, detail_rows[-1]['date'] if detail_rows else None,
        tuple(cards), True, tuple(diagnostics), selected_dates is not None)


def require():
    directory = ROOT / cfg().get('paths', 'state', 'state')
    try:
        path = assert_physical_direct_path(directory, directory / FILENAME, kind='file', label='月历证据')
        data = json.loads(path.read_text('utf-8'))
        if (data.get('schema_version') != 1 or data.get('capture_origin') != 'playwright_live'
                or data.get('accounts') != accounts() or data.get('port') != cfg().publish_debug_port
                or data.get('profile') != str(cfg().publish_profile_dir.resolve()) or not data.get('grid_complete')
                or data.get('filters') != ['Content type: all', 'Shared to: all']):
            raise ValueError('capture attribution')
        if any(channel_proof(channel)['context_ids'] != data.get('context_ids') for channel in ('facebook', 'instagram')):
            raise ValueError('capture asset attribution')
        if not re.fullmatch(r'planner_month_\d{8}T\d{12}\.png', data['screenshot']):
            raise ValueError('screenshot name')
        shot = assert_physical_direct_path(directory, directory / data['screenshot'], kind='file', label='月历截图')
        content = shot.read_bytes()
        if not content.startswith(b'\x89PNG\r\n\x1a\n') or hashlib.sha256(content).hexdigest() != data['screenshot_sha256']:
            raise ValueError('screenshot integrity')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise bs.ProbeRequired('缺少本机完整月份与加载控件录证，请运行 tools/probe_calendar.py') from exc
    return data


async def capture(page):
    if _cdp_profile_matches(cfg().publish_debug_port, cfg().publish_profile_dir) is not True:
        raise bs.ProbeRequired('无法确认月历录证来自发布 Chrome 的 profile')
    if not page.url.startswith('https://business.facebook.com/latest/content_calendar'):
        raise bs.ProbeRequired('请先打开发布后台的月视图')
    await page.bring_to_front()
    for name in ('Content type: all', 'Shared to: all'):
        await expect(page.get_by_role('button', name=name, exact=True)).to_have_count(1)
    rows = await read_grid(page)
    ids = context_ids(page.url)
    if any(channel_proof(channel)['context_ids'] != ids for channel in ('facebook', 'instagram')):
        raise bs.ProbeRequired('月历与单渠道编辑器的业务资产不一致')
    directory = cfg().state_dir
    stamp = datetime.now(timezone.utc)
    shot = f'planner_month_{stamp:%Y%m%dT%H%M%S%f}.png'
    content = await page.screenshot(path=str(directory / shot), full_page=True,
                                   mask=[page.locator(bs.SENSITIVE_INPUT_SELECTOR)])
    data = {'schema_version': 1, 'capture_origin': 'playwright_live', 'accounts': accounts(),
            'port': cfg().publish_debug_port, 'profile': str(cfg().publish_profile_dir.resolve()),
            'context_ids': ids,
            'observed_at': stamp.isoformat(), 'grid_complete': True, 'days_observed': len(rows),
            'start': str(rows[0]['date']), 'end': str(rows[-1]['date']),
            'filters': ['Content type: all', 'Shared to: all'], 'screenshot': shot,
            'screenshot_sha256': hashlib.sha256(content).hexdigest()}
    atomic_write_json(directory / FILENAME, data)
    return data
