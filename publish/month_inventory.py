"""读取完整月历及手工项，排除推荐时段；未知条目使覆盖判定不完整。"""
from __future__ import annotations

from publish.channel_evidence import require as channel_proof
from core.chrome import _cdp_profile_matches
import asyncio
import calendar
import hashlib
import json
import re
import time
from datetime import date, datetime, timezone
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from playwright.async_api import expect

from core.config import ROOT, cfg
from core.paid_model import atomic_write_json
from core.store import assert_physical_direct_path
from publish import business_suite as bs, selectors
from publish.channel_evidence import accounts, context_ids

DAY_SELECTOR = '[role="link"][draggable="false"]'
RECOMMENDATION = 'Recommendations are based on when your followers were most active on Instagram and Facebook respectively in the last 7 days.'
TIME_PATTERN = re.compile(r'\b(\d{1,2}:\d{2}\s*[AP]M)\b')
FILENAME = 'planner_controls.json'


def dates_for_cells(month: date, numbers: list[int]) -> tuple[date, ...]:
    expected = tuple(calendar.Calendar(firstweekday=6).itermonthdates(month.year, month.month))
    if numbers != [value.day for value in expected]:
        raise bs.PublishStepError('月历日期格尚未完整覆盖月份，不能把未加载日期判作空档')
    return expected


async def visible_month(page):
    months, years = set(), set()
    for node in await page.get_by_role('heading').all():
        text = (await node.inner_text()).strip()
        for name, values in (('%B', months), ('%Y', years)):
            try:
                value = datetime.strptime(text, name)
            except ValueError:
                continue
            values.add(value.month if name == '%B' else value.year)
    if len(months) != 1 or len(years) != 1:
        raise bs.PublishStepError('月历的月份与年份无法唯一识别')
    return date(next(iter(years)), next(iter(months)), 1)


async def settled(page, timeout):
    try:
        await expect(page.get_by_role('progressbar')).to_have_count(0, timeout=max(1, timeout * 1000))
        await expect(page.locator('[aria-busy="true"]')).to_have_count(0, timeout=max(1, timeout * 1000))
    except AssertionError as exc:
        raise bs.PublishStepError('月历仍在加载，未确认空档') from exc


async def read_grid(page, *, timeout=30):
    """Sweep every day twice, waiting for loaders and newly mounted cards; read labels from DOM copies."""
    deadline = time.monotonic() + timeout
    previous = None
    while True:
        await settled(page, deadline - time.monotonic())
        month = await visible_month(page)
        cells = page.locator(DAY_SELECTOR)
        numbers = await cells.evaluate_all(r'''els => els.map(el => {
          const copy=el.cloneNode(true);
          copy.querySelectorAll('[role="link"],a,button,[role="button"]').forEach(n=>n.remove());
          const label=copy.textContent.trim(); return /^\d{1,2}$/.test(label) ? Number(label) : null;
        })''')
        dates = dates_for_cells(month, numbers)
        result = []
        for index, day in enumerate(dates):
            cell = cells.nth(index)
            # DOM scrolling avoids background RAF throttling; loaders and two sweeps determine readiness.
            await cell.evaluate("el => el.scrollIntoView({block:'center', behavior:'instant'})")
            await settled(page, deadline - time.monotonic())
            # Top-level descendant links represent one card; nested wrappers repeat its time.
            items = await cell.evaluate('''el => [...el.querySelectorAll('[role="link"],a')]
              .filter(n=>n.parentElement.closest('[role="link"],a')===el)
              .map((n,index)=>({index,text:n.innerText,aria:n.getAttribute('aria-label')||'',
                href:n.getAttribute('href')||n.querySelector('a[href]')?.getAttribute('href')||''}))''')
            extra = await cell.get_by_role('button').all_inner_texts()
            if any(' '.join(label.replace('\u200b', '').split()) not in {'Schedule', 'Create'} for label in extra):
                raise bs.PublishStepError('日期格存在未处理的展开控件，月历读取不完整')
            for item in items:
                values = TIME_PATTERN.findall(' '.join(item['text'].split()))
                # A scheduled aria label may hold date+caption instead of the compact time.
                if not values:
                    values = TIME_PATTERN.findall(' '.join(item['aria'].split()))
                if len(values) != 1:
                    raise bs.PublishStepError('月历条目时刻无法唯一读取，保留现场待探查')
                item['time'] = ' '.join(values[0].split())
            result.append({'date': day, 'cell_index': index, 'items': items})
        if await visible_month(page) != month:
            raise bs.PublishStepError('读取过程中月份已变化，请重试')
        if result == previous:
            return result
        if time.monotonic() >= deadline:
            raise bs.PublishStepError('月历条目仍在变化，未确认完整范围')
        previous = result
        await asyncio.sleep(min(.4, max(0, deadline - time.monotonic())))


def item_locator(page, row, item):
    cell = page.locator(DAY_SELECTOR).nth(row['cell_index'])
    # Same structural relationship as read_grid; no clickable navigation is included.
    return cell.locator('xpath=.//*[@role="link" or self::a][not(ancestor::*[@role="link" or self::a][ancestor::*[@draggable="false"]])]') .nth(item['index'])


async def is_recommendation(page, item, *, timeout=1.5):
    await page.mouse.move(0, 0)
    tip = page.get_by_role('tooltip', name=RECOMMENDATION, exact=True)
    try:
        await tip.wait_for(state='hidden', timeout=timeout * 1000)
        await item.hover(timeout=timeout * 1000)
        await tip.wait_for(state='visible', timeout=timeout * 1000)
        return True
    except Exception:
        return False


async def read_item(page, row, item, *, timeout=30):
    node = item_locator(page, row, item)
    if not item['href'] and await is_recommendation(page, node):
        return None
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
            raise bs.PublishStepError('月历条目链接尚未录证，不能跳过未知内容')
        detail = await page.context.new_page()
        try:
            await detail.goto(url, wait_until='domcontentloaded', timeout=timeout * 1000)
            await detail.bring_to_front()
            # Direct URLs render a page; in-place navigation wraps the same content in a dialog.
            dialog = detail.locator('body')
            # Metrics can continue loading after the post itself is fully observed.
            label = dialog.get_by_text(re.compile(r'Published on:'))
            await label.wait_for(timeout=timeout * 1000)
            header = await label.evaluate('''el => ({text:el.innerText,
              platforms:[...el.parentElement.querySelectorAll('img[alt]')].map(n=>n.alt),
              authors:[...el.querySelectorAll('strong')].map(n=>n.textContent)})''')
            channels = published_channels(header, row['date'], item['time'])
            heading = dialog.get_by_role('heading', level=3).first
            caption = await heading.inner_text()
            return {'channels': channels, 'remote_ids': {channels[0]: remote} if len(channels) == 1 else {},
                    'text': caption, 'delivery': 'published', 'detail_url': url}
        finally:
            await detail.close()
    spec = bs.require_readback_evidence()
    raw = ' '.join((item.get('aria') or item.get('text') or '').split())
    parsed = bs._entry_naive(raw, spec)
    expected = datetime.combine(row['date'], datetime.strptime(item['time'], '%I:%M %p').time())
    if parsed is None or parsed != expected:
        raise bs.PublishStepError('未知月历条目没有完整日期与正文证据；不能当作推荐时段跳过')
    remote_ids = await bs._open_channel_dialogs(page, node, spec, timeout=timeout)
    match = re.search(spec.attributes['datetime_regex'], raw)
    caption = raw[:match.start()].strip() if match else raw
    return {'channels': tuple(sorted(remote_ids)), 'remote_ids': remote_ids,
            'text': caption, 'delivery': 'scheduled'}


def published_channels(header, day, clock):
    """Read channel evidence from metadata icons, time and collaborators, never from caption text."""
    match = re.search(r'Published on: \w{3} (\w{3} \d{1,2}), (\d{1,2}:\d{2}[ap]m)', header['text'])
    if not match:
        raise bs.PublishStepError('已发布详情缺少可核对的日期与时刻')
    observed = datetime.strptime(f'{day.year} {match[1]} {match[2]}', '%Y %b %d %I:%M%p')
    expected = datetime.combine(day, datetime.strptime(clock, '%I:%M %p').time())
    if observed != expected:
        raise bs.PublishStepError('月历日期格与已发布详情时刻不一致')
    markers = set(header['platforms'])
    if not markers or markers - {'Facebook', 'Instagram'}:
        return ()
    channels = tuple(channel for channel, label in (('facebook', 'Facebook'), ('instagram', 'Instagram'))
                     if label in markers)
    identity = ' '.join(header['authors'])
    if identity and not all(re.search(r'(?<![\w.])' + re.escape(accounts()[channel]) + r'(?![\w.])', identity)
                            for channel in channels):
        return ()
    return channels


async def prepare(page, *, timeout=30):
    proof = require()
    url = selectors.CONTENT_CALENDAR_URL + '?' + urlencode(proof['context_ids'])
    await page.goto(url, wait_until='domcontentloaded', timeout=timeout * 1000)
    await bs.assert_page_usable(page)
    if context_ids(page.url) != proof['context_ids']:
        raise bs.PublishStepError('月历当前资产与单渠道控件录证不一致，请核对发布账号')
    await page.get_by_role('button', name='Month', exact=True).click(timeout=timeout * 1000)
    for prefix in ('Content type:', 'Shared to:'):
        control = page.get_by_role('button', name=re.compile('^' + re.escape(prefix)))
        if ' '.join((await control.inner_text()).split()) != prefix + ' all':
            await control.click()
            await page.get_by_role('menuitem', name='All', exact=True).click()
        await expect(control).to_have_text(prefix + ' all', timeout=timeout * 1000)


def moments(day, clock, ui_zone, business_zone):
    naive = datetime.combine(day, datetime.strptime(clock, '%I:%M %p').time())
    first, second = naive.replace(tzinfo=ui_zone, fold=0), naive.replace(tzinfo=ui_zone, fold=1)
    if first.astimezone(timezone.utc).astimezone(ui_zone).replace(tzinfo=None) != naive:
        raise bs.PublishStepError('月历时刻处于夏令时不存在的区间')
    return tuple(at.astimezone(business_zone) for at in ((first, second) if first.utcoffset() != second.utcoffset() else (first,)))


async def read(page, *, ui_timezone, business_timezone, timeout=30):
    await prepare(page, timeout=timeout)
    rows = await read_grid(page, timeout=timeout)
    ui_zone, business_zone = bs.resolve_ui_timezone(ui_timezone), bs.resolve_ui_timezone(business_timezone)
    cards, occupied = [], {}
    for row in rows:
        for item in row['items']:
            material = await read_item(page, row, item, timeout=timeout)
            if material is None:
                continue
            for moment in moments(row['date'], item['time'], ui_zone, business_zone):
                occupied[moment.timestamp()] = moment
                cards.append(bs.RemotePlannerCard(moment, material['channels'],
                    tuple(sorted(material['remote_ids'].items())), material['text'],
                    hashlib.sha256(material['text'].encode('utf-8')).hexdigest(), material['delivery']))
    # Verify a final sweep so edits/late rendering during detail reads invalidate the inventory.
    if rows != await read_grid(page, timeout=timeout):
        raise bs.PublishStepError('核对详情期间远端月历已更新，请重新读取')
    return bs.RemoteSlotInventory(tuple(occupied[key] for key in sorted(occupied)), ui_timezone,
        rows[0]['date'], rows[-1]['date'], tuple(cards), True)


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
