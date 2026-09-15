"""Read effective review material for Feishu, sharing the editor's selection rules."""
from __future__ import annotations

from datetime import datetime

from pipeline.risk_scan import result_for
from core import localization, translated
from core.config import MonitorSchedule
from core.integrity import parse_ts
from core.monitoring import SKIP_LABELS
from core.store import assert_physical_direct_path
from localize import images as image_de

# 发现卡上的英文摘要长度；够判断"现在开还是等会儿开"，不喧宾夺主。
DISCOVERED_EXCERPT = 300

IMAGE_NOTES = {'de': '德语首图', 'original': '尚无有效德语首图，预览使用原图',
               'unreadable': '首图读不出，请进入审校台核对'}

PLATFORM_LABELS = {'facebook': 'Facebook', 'instagram': 'Instagram'}
RUN_KIND_LABELS = {'delta': '普通探测', 'reconcile': '兜底对账'}


def shanghai_clock(value) -> str:
    """卡片里所有时刻都按上海显示。源 created_at 是 UTC，和扫描时刻混着看会误判发帖时间。

    ⚠️ 既要收 datetime（扫描时刻）也要收字符串（归档里的 created_at）。只走 parse_ts 会让
    datetime 落到 str() 兜底，结果是 UTC 时刻挂着"上海"的标签——看不出错，但读出来是错的。
    """
    stamp = value if isinstance(value, datetime) else parse_ts(value)
    return MonitorSchedule.local(stamp).strftime('%Y-%m-%d %H:%M:%S') if stamp else '时间待核对'


def skip_summary(skipped) -> str:
    parts = [f'{SKIP_LABELS.get(key, key)} {count}'
             for key, count in (skipped or {}).items() if count]
    if not parts:
        return ''
    return '本轮跳过 %d 篇：%s' % (sum((skipped or {}).values()), ' / '.join(parts))


def _origin(row) -> str:
    """合作帖由一方发布、双方主页同时显示；不写清楚会被当成本账号原创。"""
    owner, account = (row.get('owner') or '').strip(), (row.get('account') or '').strip()
    parts = ['合作帖，原作者 %s' % owner] if owner and account and owner != account else []
    names = [name for name in row.get('coauthors') or [] if isinstance(name, str) and name.strip()]
    if names:
        parts.append('合作方 %s' % '、'.join(names))
    return '（%s）' % '；'.join(parts) if parts else ''


CLASS_LABELS = {'new': '新发布', 'historical': '历史补获', 'source_updated': '源帖更新',
                'recovered': '补齐完成', 'time_unknown': '时间待核对'}


def capture_card(event):
    row = event['source']
    saved, expected = event.get('saved_images', 0), row.get('source_media_count')
    complete = event['status'] == 'complete'
    status = '完整' if complete else '部分完成 / 待人工' if event.get('archived') else '失败 / 待人工'
    excerpt = row.get('text') or ''
    if len(excerpt) > DISCOVERED_EXCERPT:
        excerpt = excerpt[:DISCOVERED_EXCERPT] + '（已截断，完整正文见详情）'
    image_status = (f'已保存并校验 {saved}/{expected} 张' if expected is not None else
                    f'已保存并校验 {saved} 张，原帖总数待确认')
    fields = [
        ('内容分类', CLASS_LABELS.get(event['classification'], '时间待核对')),
        ('来源', PLATFORM_LABELS[row['platform']] + ' · ' + row['account']),
        ('作者', (row.get('owner') or '未知') + _origin(row)),
        ('原帖发布时间（北京时间）', shanghai_clock(row.get('created_at'))),
        ('首次发现（北京时间）', shanghai_clock(event['first_seen_at'])),
        ('抓取结束（北京时间）', shanghai_clock(event.get('finished_at'))),
        ('产品分类', ' / '.join(row.get('tags') or []) or '未分类'),
        ('英文摘要', excerpt or '无正文'),
        ('正文状态', '已保存' if event.get('archived') else '未确认保存'),
        ('图片状态', image_status),
        ('异常与下一步', event.get('reason') or ('可查看已存原帖' if complete else '需要人工处理')),
        ('帖子 ID', row['post_id']),
    ]
    return {'capture_status': status, 'fields': [{'label': label, 'value': value} for label, value in fields],
            'account_dir': row['platform'][:2] + '_' + row['account'], 'post_id': row['post_id'],
            'capture_key': event['key'], 'archived': bool(event.get('archived')),
            'captured_at': event.get('finished_at'),
            'permalink': row.get('permalink')}


def scan_cards(run_kind, platform, account, rows, skipped, now):
    """同一扫描的持久结果：检测汇总与逐帖结果；原始事件 ID 由调用方保留。"""
    if not rows:
        return None
    counts = {}
    for row in rows:
        label = CLASS_LABELS.get(row['classification'], '时间待核对')
        counts[label] = counts.get(label, 0) + 1
    summary = ' / '.join(f'{label} {count} 篇' for label, count in counts.items())
    detail = '\n'.join(f"{item['source']['post_id']} · {(item['source'].get('text') or '')[:80]} {_origin(item['source'])}" for item in rows[:5])
    first = {'platform': PLATFORM_LABELS[platform], 'account': account,
             'created_at': shanghai_clock(now) + ' 北京时间',
             'text': summary + '\n' + detail[:1000] + ('\n更多条目请查看运行详情' if len(rows) > 5 else '') + ('\n' + skip_summary(skipped) if skipped else ''),
             'run_id': rows[0]['scan_id'], 'next_step': '逐篇结果见新帖爬取机器人，可在运行页核对本轮记录。'}
    return first, [(row['event_id'], capture_card(row)) for row in rows if row['eligible']]


def material(account, source):
    identifier = source['post_id']
    machine = translated.load_translated(account / 'translated.jsonl').get(identifier)
    human = translated.load_human_translated(account / 'translated_human.jsonl').get(identifier)
    effective = translated.effective_translation(source, machine, human)
    draft = localization.effective_draft(account, source, effective)
    checks = localization.validate(draft)
    notes = [item['message'] for item in checks['issues'] + checks['warnings']]
    scan = result_for(account, source)
    if scan['status'] != 'completed':
        notes.append('英文风险预扫描：' + scan['message'])
    else:
        notes.extend(str(item.get('message') or item.get('reason') or item.get('explanation') or item.get('text') or '')
                     for item in scan['risks'])
    if effective and effective.get('stale'):
        notes.append('当前德语稿的源文已有变化，请重新核对。')
    image_text = translated.image_translation(source, machine, human)
    pairs = image_de.review_image_pairs(account, source, image_text) if image_text else []
    # 首图取第一项 image；media_index 可能包含前置视频。
    lead = next(((index, item) for index, item in enumerate(source.get('media') or [])
                 if isinstance(item, dict) and item.get('kind') == 'image'), None)
    first = next((p for p in pairs if lead is not None and p.media_index == lead[0]), None)
    path, variant = None, 'original'
    if first and first.localized_rel:
        path, variant = account / first.localized_rel, 'de'
    try:
        if path is None and lead is not None:
            path, _ = image_de._source_from_manifest(account, source, lead[1])
        if path:
            assert_physical_direct_path(path.parent, path, kind='file', label='通知首图')
    except (OSError, ValueError):
        # 首图读不出只降级图片。德语正文可能完全没问题，整张卡丢掉等于白等一轮审校。
        path, variant = None, 'unreadable'
    caption = localization.render(draft) if effective else '德语稿尚未就绪，请进入页面查看待处理问题。'
    return {'text': caption[:1000], 'risk': '\n'.join(dict.fromkeys(notes)),
            'image_variant': variant, 'image_note': IMAGE_NOTES[variant],
            'image_count': sum(m.get('kind') == 'image' for m in source.get('media', [])),
            'source_text_sha256': translated.source_text_sha256(source['text']),
            'checks': checks}, path
