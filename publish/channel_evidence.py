"""只读捕获已知渠道控件名称及布尔/数量状态，不保存正文或日期输入值。"""
from __future__ import annotations

from core.chrome import _cdp_profile_matches
import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from core.config import cfg
from core.paid_model import atomic_write_json
from core.store import assert_physical_direct_path
from publish import business_suite as bs
from publish.asset_context import context_ids

FILENAME = 'channel_controls.json'
SURFACE = 'https://business.facebook.com/latest/composer/'
STORY = 'Share to Facebook Story'
THREADS = 'Share to Threads'
SCHEDULE = 'Set date and time'
DATE = 'Date picker'
TIME = 'Time input'
PREVIEWS = {'facebook': 'Facebook Feed preview', 'instagram': 'Instagram Feed preview'}


def accounts():
    c = cfg()
    result = {'facebook': c.get('publish', 'facebook_page_name', ''),
              'instagram': c.get('publish', 'instagram_account', '')}
    if not all(isinstance(value, str) and value.strip() for value in result.values()):
        raise bs.ProbeRequired('发布账号核验名尚未配置')
    return result


def assert_context(page, channel, *, asset_context=None):
    """Bind the live composer to the asset used for this conflict check."""
    expected = asset_context if asset_context is not None else require(channel)['context_ids']
    url = urlsplit(page.url)
    if (url.scheme != 'https' or url.hostname != 'business.facebook.com'
            or url.path.rstrip('/') != '/latest/composer'
            or context_ids(page.url) != expected):
        raise bs.ProbeRequired('当前编辑器资产与本次发布目标不一致，请核对发布账号'
                               if asset_context is not None else
                               '当前编辑器资产与渠道录证/月历不一致，请核对发布账号')


def combo(page):
    names = accounts()
    choices = [names['facebook'], names['instagram'], names['facebook'] + ' and ' + names['instagram']]
    return page.get_by_role('combobox', name=re.compile('^Post to (?:' + '|'.join(re.escape(v) for v in choices) + ')$'))


async def selected(page, *, timeout=30):
    control = combo(page)
    if await control.count() != 1:
        raise bs.PublishStepError('无法唯一核对发布渠道下拉框及目标账号')
    menu = page.get_by_role('listbox', name='Post to', exact=True)
    if not await menu.is_visible():
        await control.click(timeout=timeout * 1000)
    await menu.wait_for(state='visible', timeout=timeout * 1000)
    for option in await menu.get_by_role('option').all():
        if await option.get_attribute('aria-selected') == 'true':
            label = ' '.join((await option.inner_text()).split())
            if label not in accounts().values():
                raise bs.PublishStepError('存在未配置的勾选发布目标，请人工核对')
    found = {}
    for channel, name in accounts().items():
        option = menu.get_by_role('option', name=name, exact=True)
        if await option.count() != 1:
            raise bs.PublishStepError('渠道列表中缺少唯一的目标账号：' + name)
        value = await option.get_attribute('aria-selected')
        if value not in {'true', 'false', None}:
            raise bs.PublishStepError('渠道勾选状态无法读取')
        found[channel] = value == 'true'
    return found


async def observe(page, channel):
    """Read the current single-channel form. Opening a dropdown is the only action."""
    if channel not in PREVIEWS or not page.url.startswith(SURFACE):
        raise bs.ProbeRequired('请在发布 Chrome 的 composer 页面记录渠道')
    chosen = await selected(page)
    await page.keyboard.press('Escape')
    switches = {}
    for name in (STORY, THREADS, 'Boost', 'Make this an ad post', SCHEDULE):
        node = page.get_by_role('switch', name=name, exact=True)
        switches[name] = await node.get_attribute('aria-checked') if await node.count() == 1 else None
    record = {'channel': channel, 'accounts': accounts(), 'selected': chosen,
              'switches': switches, 'date_count': await page.get_by_role('textbox', name=DATE, exact=True).count(),
              'time_count': await page.get_by_role('application', name=TIME, exact=True).count(),
              'preview': await page.get_by_role('heading', name=PREVIEWS[channel], exact=True).is_visible(),
              'url': SURFACE, 'context_ids': context_ids(page.url),
              'observed_at': datetime.now(timezone.utc).isoformat()}
    validate_record(record, channel)
    return record


def validate_record(row, channel):
    if (row.get('channel') != channel or row.get('accounts') != accounts()
            or row.get('selected') != {key: key == channel for key in PREVIEWS}
            or row.get('date_count') != 1 or row.get('time_count') != 1 or row.get('preview') is not True
            or row.get('url') != SURFACE or row.get('switches', {}).get(SCHEDULE) != 'true'):
        raise bs.ProbeRequired('单渠道账号、预览或唯一排期控件尚未完整录证：' + channel)
    for name in (STORY, THREADS, 'Boost', 'Make this an ad post'):
        if row['switches'].get(name) not in {None, 'false'}:
            raise bs.ProbeRequired('尚有附加发布/广告开关开启：' + name)
    expected = STORY if channel == 'facebook' else THREADS
    if row['switches'].get(expected) != 'false' or row['switches'].get('Boost') != 'false':
        raise bs.ProbeRequired('单渠道附加发布开关缺少关闭回读证据')


def read_record(channel):
    """读取既有资产绑定；控件是否可用由严格验收或当次页面另行核对。"""
    directory = cfg().state_dir
    path = assert_physical_direct_path(directory, directory / FILENAME, kind='file', label='渠道证据')
    try:
        data = json.loads(path.read_text('utf-8'))
        if data.get('schema_version') != 1 or data.get('capture_origin') != 'playwright_live':
            raise ValueError('capture format')
        row = data['channels'][channel]
        if (row.get('channel') != channel or row.get('port') != cfg().publish_debug_port
                or row.get('profile') != str(cfg().publish_profile_dir.resolve())):
            raise ValueError('capture browser attribution')
        if not all(re.fullmatch(r'\d+', str(row.get('context_ids', {}).get(key, '')))
                   for key in ('asset_id', 'business_id')):
            raise ValueError('capture asset attribution')
        return row
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise bs.ProbeRequired('发布资产记录缺失或与当前发布浏览器不符，请核对已有渠道记录：' + channel) from exc


def require(channel):
    directory = cfg().state_dir
    try:
        row = read_record(channel)
        validate_record(row, channel)
        stamp = datetime.fromisoformat(row['observed_at'])
        if stamp.tzinfo is None:
            raise ValueError('capture time')
        if not re.fullmatch(r'channel_(facebook|instagram)_\d{8}T\d{12}\.png', row['screenshot']):
            raise ValueError('capture screenshot path')
        shot = assert_physical_direct_path(directory, directory / row['screenshot'], kind='file', label='渠道探查截图')
        content = shot.read_bytes()
        if not content.startswith(b'\x89PNG\r\n\x1a\n') or hashlib.sha256(content).hexdigest() != row['screenshot_sha256']:
            raise ValueError('capture screenshot')
        return row
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise bs.ProbeRequired('单渠道控件证据未通过本机核对，请运行 tools/probe_channels.py：' + channel) from exc


async def capture(page, channel):
    if _cdp_profile_matches(cfg().publish_debug_port, cfg().publish_profile_dir) is not True:
        raise bs.ProbeRequired('无法确认单渠道录证来自发布 Chrome 的 profile')
    await page.bring_to_front()
    row = await observe(page, channel)
    directory = cfg().state_dir
    filename = f'channel_{channel}_{datetime.now(timezone.utc):%Y%m%dT%H%M%S%f}.png'
    # The probe deliberately masks all value-bearing fields, including the caption.
    from tools._scaffolding.probe_publish import _SENSITIVE_INPUT_SELECTOR
    content = await page.screenshot(path=str(directory / filename), full_page=True,
                                    mask=[page.locator(_SENSITIVE_INPUT_SELECTOR)])
    row.update(screenshot=filename, screenshot_sha256=hashlib.sha256(content).hexdigest(),
               port=cfg().publish_debug_port, profile=str(cfg().publish_profile_dir.resolve()),
               context_ids=context_ids(page.url))
    path = directory / FILENAME
    data = json.loads(path.read_text('utf-8')) if path.exists() else {
        'schema_version': 1, 'capture_origin': 'playwright_live', 'channels': {}}
    data['channels'][channel] = row
    atomic_write_json(path, data)
    return row
