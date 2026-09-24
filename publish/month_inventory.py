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
from publish.insights_evidence import InsightsEvidence
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
        loaders = page.get_by_role('progressbar').or_(page.locator('[aria-busy="true"]'))
        await expect(loaders).to_have_count(0, timeout=max(1, timeout * 1000))
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
            }''', DAY_SELECTOR)
            if snapshot is None:
                raise bs.PublishStepError('月历日期格在读取过程中消失，请重新读取')
            items, extra = snapshot['items'], snapshot['extra']
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


def unreadable_item(tooltip):
    """An item with neither a detail link nor complete text stays a real task.

    It cannot be skipped, so the refusal carries why the recommendation tooltip
    did not settle it; without that the run only reports that time ran out.
    """
    return content.DetailReadError('structure_unknown', missing_fields=(
        'item_caption', *(('recommendation_' + tooltip,) if tooltip else ())))


def item_locator(page, row, item):
    cell = page.locator(DAY_SELECTOR).nth(row['cell_index'])
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
    tip = page.get_by_role('tooltip', name=RECOMMENDATION, exact=True)
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
    except Exception as exc:
        raise PlannerItemError(row, item, stage, exc) from exc
    finally:
        # Hover cards must not contaminate the final month sweep or the next slot.
        await page.mouse.move(0, 0)


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
        before_hover = set(await page.get_by_role('link').all_inner_texts())
        tooltip = await recommendation_state(page, node, timeout=min(1.5, max(.001, deadline - time.monotonic())))
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
        if not fresh['href']:
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
        if (fresh['href'] or raw) and time.monotonic() - stable_since >= .4:
            # Hydration is accepted only after checking the original slot identity;
            # read() compares this refreshed DOM snapshot with the final sweep.
            item.update(fresh)
            return node, raw
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
        finally:
            try:
                await evidence.finish()
                if observer:
                    await observer.finish()
            finally:
                await detail.close()
    spec = card_spec or bs.require_readback_evidence()
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
            if (match and bs._entry_naive(raw, spec) == local
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
        if bs._card_text(card.rendered) not in await bs._node_text(dialog):
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
            or bs._card_text(detail['text']) != bs._card_text(card.rendered)
            or rows != await read_grid(page, timeout=timeout)):
        raise bs.PublishStepError('取图期间原排期对象或月历发生变化')
    return captured[0]


async def open_calendar(page, asset_context, *, timeout=30):
    """打开指定业务资产的月历。调用方负责提供资产，这里不读录证文件。"""
    url = selectors.CONTENT_CALENDAR_URL + '?' + urlencode(asset_context)
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


async def read(page, *, ui_timezone, business_timezone, timeout=30, run=None):
    if run is None:
        await prepare(page, timeout=timeout)
        card_spec = None
    else:
        await open_calendar(page, run.asset_context, timeout=timeout)
        card_spec = run.planner_card
    rows = await read_grid(page, timeout=timeout)
    ui_zone, business_zone = bs.resolve_ui_timezone(ui_timezone), bs.resolve_ui_timezone(business_timezone)
    cards, occupied, diagnostics = [], {}, []
    for row in rows:
        for item in row['items']:
            try:
                material = await read_item(page, row, item, timeout=timeout, ui_timezone=ui_timezone,
                                           card_spec=card_spec)
            except PlannerItemError as exc:
                diagnostics.append(exc.diagnostic)
                # has_href 只区分展示：有 insights 链接看成已发布，否则看成定时。
                # 这不是详情核实过的公开事实，时刻也还没按变体独立核对。
                material = {'channels': (), 'remote_ids': {}, 'text': '',
                            'delivery': 'published' if exc.diagnostic['has_href'] else 'scheduled',
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
