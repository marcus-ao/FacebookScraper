"""Pure presentation for group cards; business timestamps and states come from callers."""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

from core.integrity import parse_ts


TITLES = {'ready': '待审核素材就绪', 'backlog': '待审情况', 'scheduled': '排期已确认',
          'schedule_failed': '排期未完成，请核对', 'system': '任务异常告警', 'morning': '晨间处理情况',
          'monitor_found': '监测到新发帖', 'monitor_saved': '原帖抓取完成', 'selftest': '通道自检'}
CLASS_LABELS = {'new': '新发布', 'historical': '历史补获', 'source_updated': '源帖更新',
                'recovered': '补齐完成', 'time_unknown': '时间待核对'}
PLATFORM_LABELS = {'facebook': 'Facebook', 'instagram': 'Instagram'}


def _md(value) -> str:
    text = html.escape(str(value or ''), quote=False)
    return re.sub(r'([\\`*_\[\]{}()#!|~])', r'\\\1', text)


def _excerpt(value) -> str:
    text = str(value or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    text = re.sub(r'\n(?:[^\S\n]*\n)+', '\n', text)
    return (text[:150] + '...' if len(text) > 150 else text) or '无正文'


def _clock(value, settings) -> str:
    try:
        stamp = value if isinstance(value, datetime) else parse_ts(value)
        if stamp is None:
            return '-'
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(ZoneInfo(settings.timezone)).strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError, OverflowError, OSError):
        return '-'


def _payload(kind, raw):
    """Only map known legacy fields; a detection's old created_at was delivery time."""
    p = dict(raw)
    fields = {f.get('label'): f.get('value') for f in p.get('fields') or [] if isinstance(f, dict)}
    for key, label in [('text', '英文摘要'), ('post_id', '帖子 ID'), ('media_summary', '图片状态'),
                       ('origin_note', '作者'), ('action_recommendation', '异常与下一步')]:
        if label in fields:
            p.setdefault(key, fields[label])
    source = str(fields.get('来源') or '').partition(' · ')
    if source[1]:
        p.setdefault('platform', source[0])
        p.setdefault('account', source[2])
    for key, label in [('published_at', '原帖发布时间（北京时间）'),
                       ('discovered_at', '首次发现（北京时间）'), ('scraped_at', '抓取结束（北京时间）')]:
        stamp = parse_ts(fields.get(label))
        if stamp:
            # These legacy strings were already formatted in +08:00; never convert them as UTC.
            p.setdefault(key, stamp.replace(tzinfo=ZoneInfo('Asia/Shanghai')).isoformat())
    if kind in {'ready', 'scheduled'}:
        p.setdefault('published_at', p.get('created_at'))
    if kind == 'monitor_saved':
        p.setdefault('scraped_at', p.get('captured_at'))
    return p


def _field(label, value, *, color=None):
    text = _md(value if value is not None and value != '' else '-')
    if color:
        text = f"<font color='{color}'>{text}</font>"
    return {'is_short': True, 'text': {'tag': 'lark_md', 'content': f'**{label}：**\n{text}'}}


def _quote(value, *, heading=None):
    content = '\n'.join('> ' + _md(line) for line in _excerpt(value).split('\n'))
    if heading:
        content = '> **' + _md(heading) + '**\n' + content
    return {'tag': 'div', 'text': {'tag': 'lark_md', 'content': content}}


def _risk(p):
    checks = p.get('checks') or {}
    status = p.get('risk_status')
    count = p.get('risk_count')
    issues = len(checks.get('issues') or []) + len(checks.get('warnings') or [])
    if p.get('preview_error'):
        return '当前稿读取失败，请核对', None
    if status != 'completed':
        return {'not_scanned': '尚未预扫，请人工核对', 'failed': '预扫失败，请人工核对',
                'stale': '预扫已失效，请重新核对'}.get(status, '预扫状态待核对'), None
    if count:
        return f'预扫发现 {count} 项风险，请人工核对', 'red'
    if issues or p.get('processing_notes') or p.get('risk') or p.get('image_variant') in {'original', 'unreadable'}:
        return '素材有待核对事项，请人工检查', None
    if count == 0 and checks.get('ready') is True:
        return '预扫完成，未发现风险', 'green'
    return '素材检查尚未完成', None


def _title(kind, payloads):
    title, color = TITLES[kind], 'blue'
    p = payloads[0] if payloads else {}
    if kind == 'monitor_found':
        classes = {key for row in payloads for key, count in (row.get('counts') or {}).items() if count}
        title = ({'historical': '监测到历史补获', 'source_updated': '监测到源帖更新',
                  'recovered': '监测到补齐完成', 'time_unknown': '监测到时间待核对帖子'}.get(next(iter(classes)), title)
                 if len(classes) == 1 else '监测到帖子变化')
        color = 'turquoise'
    elif kind == 'monitor_saved':
        status = p.get('capture_status')
        title = ('原帖抓取完成' if status == '完整' else '原帖抓取部分完成' if p.get('archived')
                 else '原帖抓取失败' if status else '原帖抓取结果待核对')
        color = 'turquoise'
    elif kind == 'ready':
        if any(not row.get('ready_at') or row.get('preview_error') or row.get('image_variant') in {'original', 'unreadable'} or
               (row.get('checks') or {}).get('ready') is not True for row in payloads):
            title = '待审核素材需处理'
        color = 'orange'
    elif kind == 'backlog':
        color = 'orange'
    elif kind == 'morning' and p.get('next_step'):
        color = 'orange'
    elif kind in {'system', 'schedule_failed'}:
        color = 'red'
    if kind == 'system' and p.get('deployment_status'):
        title, color = {'success': ('服务更新完成', 'blue'), 'rollback': ('服务已回退', 'orange'),
                        'blocked': ('服务更新等待处理', 'orange'),
                        'failure': ('服务更新失败', 'red')}.get(p['deployment_status'], (title, color))
    return title, color


def _actions(kind, p, settings, number):
    base = settings.base_url.rstrip('/')
    task = '/?task=' + quote(str(p['task_id']), safe='') if p.get('task_id') else None
    runtime = '/runtime'
    if p.get('capture_key'):
        runtime += '?capture=' + quote(str(p['capture_key']), safe='')
    elif p.get('run_id'):
        runtime += '?scan=' + quote(str(p['run_id']), safe='')
    if kind == 'monitor_found':
        label, suffix, style = '查看运行看板', runtime, 'default'
    elif kind == 'monitor_saved':
        archived = p.get('archived') and p.get('account_dir') and p.get('post_id')
        suffix = ('/history/' + quote(str(p['account_dir']), safe='') + '/' + quote(str(p['post_id']), safe='')
                  if archived else runtime)
        label, style = ('查看已存原帖' if archived else '查看该项采集异常'), 'primary'
    elif kind == 'ready':
        label, suffix, style = '前往审核入库', task or '/', 'primary'
    elif kind == 'backlog':
        platform = str(p.get('platform') or '').lower()
        label, suffix, style = '查看待审列表', '/review/' + platform if platform in PLATFORM_LABELS else '/', 'primary'
    elif kind == 'scheduled':
        label, suffix, style = '查看排期回执', task or runtime, 'primary'
    elif kind == 'schedule_failed':
        label, suffix, style = '核对排期结果', task or runtime, 'danger'
    else:
        alert = kind == 'system' and p.get('deployment_status') not in {'success', 'rollback', 'blocked'}
        label, suffix, style = ('排查系统运行状态' if alert else '查看运行看板'), runtime, ('danger' if alert else 'default')
    prefix = f'{number}. ' if number else ''
    result = [{'tag': 'button', 'type': style, 'text': {'tag': 'plain_text', 'content': prefix + label},
               'url': base + suffix}]
    if kind == 'monitor_saved':
        try:
            original = urlsplit(str(p.get('permalink') or ''))
            valid = original.scheme == 'https' and original.hostname and not original.username and not original.password
        except ValueError:
            valid = False
        if valid:
            result.append({'tag': 'button', 'type': 'default',
                           'text': {'tag': 'plain_text', 'content': '访问源帖链接'}, 'url': p['permalink']})
    return result


def build_card(kind, payloads, settings, *, role=None):
    payloads = [_payload(kind, p) for p in payloads] or [{}]
    title, color = _title(kind, payloads)
    elements, actions = [], []
    for index, p in enumerate(payloads, 1):
        number = index if len(payloads) > 1 else None
        platform = PLATFORM_LABELS.get(str(p.get('platform') or '').lower(), p.get('platform') or '-')
        account = str(p.get('account') or '').lstrip('@')
        source = platform + (' · @' + account if account else '')
        clock = lambda value: _clock(value, settings)
        if kind in {'monitor_found', 'monitor_saved', 'ready'}:
            if kind == 'monitor_found':
                counts = p.get('counts') or {}
                summary = ' / '.join(f'{CLASS_LABELS.get(key, key)} {count} 篇' for key, count in counts.items() if count)
                state = _field('检测状态', summary or '检测结果待核对', color='green' if set(counts) == {'new'} else None)
                time_label = '最新发帖时间' if sum(counts.values()) > 1 else '发帖时间'
                event_label, event_at = '监测时间', p.get('discovered_at')
            elif kind == 'monitor_saved':
                state = _field('附件状态', p.get('media_summary') or '附件情况待核对')
                time_label, event_label, event_at = '发帖时间', '抓取时间', p.get('scraped_at')
            else:
                risk, risk_color = _risk(p)
                state = _field('机审风控', risk, color=risk_color)
                time_label = '发帖时间'
                event_label, event_at = ('就绪时间', p['ready_at']) if p.get('ready_at') else ('待处理时间', p.get('occurred_at'))
            fields = [_field('平台账号' if kind == 'monitor_found' else '来源账号', source), state,
                      _field(time_label, clock(p.get('published_at'))), _field(event_label, clock(event_at))]
        elif kind in {'system', 'schedule_failed'}:
            module = ' '.join(v for v in (platform if platform != '-' else '', p.get('module_name') or '系统运行') if v)
            informational = p.get('deployment_status') in {'success', 'rollback', 'blocked'}
            fields = [_field('处理模块' if informational else '告警模块', module),
                      _field('发生时间', clock(p.get('occurred_at')))]
            if kind == 'schedule_failed' or p.get('scheduled_at'):
                fields.extend([_field('来源账号', source), _field('目标排期时间', clock(p.get('scheduled_at')))])
            if p.get('version'):
                fields.append(_field('运行版本', p['version']))
        elif kind == 'morning' and p.get('morning_stats'):
            stats = p['morning_stats']
            fields = [_field('发现帖子', str(stats['discovered']) + ' 篇'),
                      _field('晨间补抓', str(stats['reconcile_discovered']) + ' 篇'),
                      _field('当前待审', str(stats['pending']) + ' 篇'),
                      _field('最近处理耗时', str(stats['duration']) + ' 分钟' if stats['duration'] is not None else '-'),
                      _field('处理状态', '需要核对' if p.get('next_step') else '无待核对异常'),
                      _field('统计时间', clock(p.get('occurred_at')))]
        elif kind == 'scheduled':
            fields = [_field('来源账号', source), _field('排期状态', '远端已确认排期'),
                      _field('排期时间', clock(p.get('scheduled_at'))), _field('确认时间', clock(p.get('occurred_at')))]
        else:
            fields = [_field('平台账号', source), _field('统计时间', clock(p.get('occurred_at')))]
        if number:
            elements.append({'tag': 'div', 'text': {'tag': 'lark_md', 'content': f'**{number}. 待审核素材**'}})
        elements.append({'tag': 'div', 'fields': fields})
        if kind in {'system', 'schedule_failed'} and p.get('deployment_status') not in {'success', 'rollback', 'blocked'}:
            reason = _md(_excerpt(p.get('error_reason') or p.get('text') or '异常原因待核对'))
            advice = _md(_excerpt(p.get('action_recommendation') or p.get('next_step') or '请打开运行状态核对记录。'))
            content = "> <font color='red'>**异常原因：** " + reason.replace('\n', '\n> ') + '</font>\n> **处置建议：** ' + advice.replace('\n', '\n> ')
            elements.append({'tag': 'div', 'text': {'tag': 'lark_md', 'content': content}})
        else:
            if kind == 'morning' and p.get('morning_stats'):
                for line in p.get('summary_lines') or []:
                    elements.append(_quote(line))
            else:
                elements.append(_quote(p.get('text'), heading='ID: ' + str(p['post_id']) if kind == 'monitor_saved' and p.get('post_id') else None))
            notes = [p.get('origin_note')]
            if kind == 'ready':
                notes += [p.get('risk'), p.get('image_note') if p.get('image_variant') != 'de' else None]
            if kind != 'monitor_found':
                notes += [p.get('action_recommendation') or p.get('next_step')]
            for note in dict.fromkeys(str(note) for note in notes if note):
                elements.append(_quote(note))
        actions.extend(_actions(kind, p, settings, number))
    for offset in range(0, len(actions), 5):
        elements.append({'tag': 'action', 'actions': actions[offset:offset + 5]})
    if kind == 'selftest' and role:
        title += ' · ' + {'detect': '检测', 'capture': '抓取', 'publish': '待审', 'alert': '告警'}.get(role, role)
    return {'config': {'wide_screen_mode': True},
            'header': {'template': color, 'title': {'tag': 'plain_text', 'content': f'[{settings.site_name}] {title}'}},
            'elements': elements}


def selftest_cards(settings):
    """The same four examples are used for offline preview and explicit channel self-test."""
    common = {'platform': 'facebook', 'account': 'neakasaofficial', 'post_id': 'example-post',
              'published_at': '2026-09-22T01:00:00Z', 'text': '通道自检示例，没有真实业务含义。',
              'discovered_at': '2026-09-22T01:05:00Z', 'run_id': 'selftest'}
    examples = [('detect', 'monitor_found', {'counts': {'new': 1}}),
                ('capture', 'monitor_saved', {'capture_status': '完整', 'archived': True,
                    'account_dir': 'fa_neakasaofficial', 'media_summary': '已保存并校验 2/2 张',
                    'scraped_at': '2026-09-22T01:06:00Z', 'permalink': 'https://www.facebook.com/neakasaofficial'}),
                ('publish', 'ready', {'task_id': 'fa_neakasaofficial/example-post',
                    'text': 'Ein sauberes Zuhause. 示例译文，无真实业务含义。', 'ready_at': '2026-09-22T01:10:00Z',
                    'risk_status': 'completed', 'risk_count': 0,
                    'checks': {'ready': True, 'issues': [], 'warnings': []}}),
                ('alert', 'system', {'module_name': '采集', 'occurred_at': '2026-09-22T01:07:00Z',
                    'error_reason': '模拟会话失效，并非真实故障', 'action_recommendation': '请核对卡片样式与发送者。'})]
    result = []
    for role, kind, values in examples:
        card = build_card(kind, [{**common, **values}], settings)
        label = {'detect': '检测', 'capture': '抓取', 'publish': '待审', 'alert': '告警'}[role]
        card['header']['title']['content'] += ' · 通道自检（' + label + '）'
        result.append({'role': role, 'card': card})
    return result
