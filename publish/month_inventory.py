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

from playwright.async_api import expect, TimeoutError as BrowserTimeout, Error as BrowserError

from core.config import ROOT, cfg
from core.paid_model import atomic_write_json
from core.store import assert_physical_direct_path
from publish import business_suite as bs, selectors
from publish import planner_content as content, published_details
from publish.channel_evidence import accounts, context_ids

DAY_SELECTOR = '[role="link"][draggable="false"]'
RECOMMENDATION = 'Recommendations are based on when your followers were most active on Instagram and Facebook respectively in the last 7 days.'
TIME_PATTERN = re.compile(r'\b(\d{1,2}:\d{2}\s*[AP]M)\b')
FILENAME = 'planner_controls.json'
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
    href:n.getAttribute('href')||n.querySelector('a[href]')?.getAttribute('href')||''};
}'''


class PlannerItemError(bs.PublishStepError):
    """Only structural context crosses the cache/API boundary, never exception text or URLs."""
    def __init__(self, row, item, stage, cause=None):
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
              .map((n,index)=>({index,...(''' + ITEM_DATA_JS + ''')(n)}))''')
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
    stage = 'item_ready'
    try:
        ready = await ready_item(page, row, item, timeout=timeout)
        if ready is None:
            return None
        node, raw = ready
        stage = 'published_detail' if item['href'] else 'scheduled_detail'
        return await read_item_detail(page, row, item, node, raw, timeout=timeout)
    except Exception as exc:
        raise PlannerItemError(row, item, stage, exc) from exc
    finally:
        # Hover cards must not contaminate the final month sweep or the next slot.
        await page.mouse.move(0, 0)


async def ready_item(page, row, item, *, timeout):
    """Re-read the card and bind a newly opened hover link to its caption and grid time."""
    node = item_locator(page, row, item)
    deadline = time.monotonic() + timeout
    fresh = await node.evaluate(ITEM_DATA_JS, timeout=max(1, timeout * 1000))
    require_same_item(item, fresh)
    spec = None
    before_hover = set()
    if not fresh['href']:
        spec = bs.require_readback_evidence()
        before_hover = set(await page.get_by_role('link').all_inner_texts())
        if await is_recommendation(page, node, timeout=min(1.5, max(.001, deadline - time.monotonic()))):
            return None
    previous, stable_since = None, time.monotonic()
    expected = datetime.combine(row['date'], datetime.strptime(item['time'], '%I:%M %p').time())
    while True:
        fresh = await node.evaluate(ITEM_DATA_JS, timeout=max(1, (deadline - time.monotonic()) * 1000))
        require_same_item(item, fresh)
        raw = ''
        if not fresh['href']:
            spec = spec or bs.require_readback_evidence()
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
        if (fresh['href'] or raw) and time.monotonic() - stable_since >= .4:
            # Hydration is accepted only after checking the original slot identity;
            # read() compares this refreshed DOM snapshot with the final sweep.
            item.update(fresh)
            return node, raw
        if time.monotonic() >= deadline:
            raise bs.PublishStepError('未知月历条目没有完整日期与正文证据；不能当作推荐时段跳过')
        await asyncio.sleep(min(.2, max(0, deadline - time.monotonic())))


def require_same_item(item, fresh):
    clocks = TIME_PATTERN.findall(' '.join(fresh['text'].split()))
    if not clocks:
        clocks = TIME_PATTERN.findall(' '.join(fresh['aria'].split()))
    if clocks != [item['time']] or (item['href'] and fresh['href'] != item['href']):
        raise bs.PublishStepError('月历条目的时刻或链接已变化，请重新读取')
    known = {' '.join(text.split()) for text in
             [item.get('text', ''), item.get('aria', ''), *item.get('labels', [])]
             if text.strip() and not TIME_PATTERN.fullmatch(' '.join(text.split()))}
    current = {' '.join(text.split()) for text in [fresh['text'], fresh['aria'], *fresh['labels']]}
    if not known.issubset(current):
        raise bs.PublishStepError('月历条目已读取的正文发生变化，请重新读取')


async def read_item_detail(page, row, item, node, raw, *, timeout):
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
        try:
            try:
                await detail.goto(url, wait_until='domcontentloaded', timeout=timeout * 1000)
            except BrowserError as exc:
                raise content.DetailReadError('navigation_failed') from exc
            if urlsplit(detail.url).path.rstrip('/') != parsed.path.rstrip('/'):
                raise content.DetailReadError('navigation_failed')
            await detail.bring_to_front()
            variants = await published_details.read(detail, row, item, accounts(), timeout=timeout)
            for variant in variants:
                variant['source_content_id'] = remote
            return {'variants': variants}
        finally:
            await detail.close()
    spec = bs.require_readback_evidence()
    parsed = bs._entry_naive(raw, spec)
    expected = datetime.combine(row['date'], datetime.strptime(item['time'], '%I:%M %p').time())
    if parsed is None or parsed != expected:
        raise bs.PublishStepError('未知月历条目没有完整日期与正文证据；不能当作推荐时段跳过')
    remote_ids = await bs._open_channel_dialogs(page, node, spec, timeout=timeout)
    if len(remote_ids) != 1:
        raise content.DetailReadError('identity_unverified', placement='feed',
                                      missing_fields=('channel_identity',))
    match = re.search(spec.attributes['datetime_regex'], raw)
    caption = raw[:match.start()].strip() if match else raw
    return {'channels': tuple(sorted(remote_ids)), 'remote_ids': remote_ids,
            'text': caption, 'delivery': 'scheduled', 'placement': 'feed',
            'caption_status': 'present', 'accounts': {key:accounts()[key] for key in remote_ids}}


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
    cards, occupied, diagnostics = [], {}, []
    for row in rows:
        for item in row['items']:
            try:
                material = await read_item(page, row, item, timeout=timeout)
            except PlannerItemError as exc:
                diagnostics.append(exc.diagnostic)
                material = {'channels': (), 'remote_ids': {}, 'text': '', 'delivery': 'unknown',
                            'placement': exc.diagnostic['placement'],
                            'read_status': 'unsupported' if exc.diagnostic['code']=='unsupported_type' else
                                           'unavailable' if exc.diagnostic['code']=='permission_denied' else 'incomplete'}
            if material is None:
                continue
            for variant in material.get('variants', [material]):
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
                        source_content_id=variant.get('source_content_id','')))
    # Verify a final sweep so edits/late rendering during detail reads invalidate the inventory.
    if rows != await read_grid(page, timeout=timeout):
        raise bs.PublishStepError('核对详情期间远端月历已更新，请重新读取')
    return bs.RemoteSlotInventory(tuple(occupied[key] for key in sorted(occupied)), ui_timezone,
        rows[0]['date'], rows[-1]['date'], tuple(cards), True, tuple(diagnostics))


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
