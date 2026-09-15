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

PLATFORM_LABELS = {'facebook': 'Facebook', 'instagram': 'Instagram'}
RUN_KIND_LABELS = {'delta': '普通探测', 'reconcile': '兜底对账'}


def shanghai_clock(value) -> str:
    """卡片里所有时刻都按上海显示。源 created_at 是 UTC，和扫描时刻混着看会误判发帖时间。

    ⚠️ 既要收 datetime（扫描时刻）也要收字符串（归档里的 created_at）。只走 parse_ts 会让
    datetime 落到 str() 兜底，结果是 UTC 时刻挂着"上海"的标签——看不出错，但读出来是错的。
    """
    stamp = value if isinstance(value, datetime) else parse_ts(value)
    return MonitorSchedule.local(stamp).strftime('%m-%d %H:%M') if stamp else str(value or '时间未知')


def skip_summary(skipped) -> str:
    parts = [f'{SKIP_LABELS.get(key, key)} {count}'
             for key, count in (skipped or {}).items() if count]
    if not parts:
        return ''
    return '本轮跳过 %d 篇：%s' % (sum((skipped or {}).values()), ' / '.join(parts))


def scan_cards(run_kind, platform, account, rows, skipped, now):
    """一轮扫描组两张卡：发现一张、落档一张；没有发现就不推，避免 64 次/天的空播报。

    ⚠️ 两张卡靠 kind 名字的字典序排序（见 core.feishu.MONITOR_KINDS），不是靠这里的返回顺序。
    """
    found = [row for row in rows if row['event'] == 'post_discovered']
    if not found:
        return None
    captured = {row['post_id']: row for row in rows if row['event'] == 'post_captured'}
    incomplete = [row['post_id'] for row in rows if row['event'] == 'post_capture_incomplete']
    fresh = [row for row in found if not row.get('known')]
    skip_line = skip_summary(skipped)
    base = {'platform': PLATFORM_LABELS.get(platform, platform), 'account': account,
            'created_at': '%s · 上海 %s（以下时刻均为上海）'
                          % (RUN_KIND_LABELS.get(run_kind, run_kind), shanghai_clock(now)),
            'permalink': next((row.get('permalink') for row in found if row.get('permalink')), None)}

    headline = []
    if fresh:
        headline.append('发现 %d 篇新帖' % len(fresh))
    if len(found) > len(fresh):
        # known=True 同时涵盖媒体补齐和源内容变化两种重抓，抓取侧不区分，这里也不假装区分。
        headline.append('另有 %d 篇已有帖被重新抓取（媒体补齐或源内容变化）' % (len(found) - len(fresh)))
    detail = ['· %s  原帖 %s · %d 图%s\n  %s' % (
        row['post_id'], shanghai_clock(row.get('created_at')),
        row.get('images', 0), ' %d 视频' % row['videos'] if row.get('videos') else '',
        row.get('head') or '（无正文）') for row in found]
    first = dict(base, text='\n'.join(['、'.join(headline), *detail, skip_line]).strip(),
                 next_step='这一轮的落档结果见紧随其后的「原帖抓取完成」卡片。')

    counted = ['发现 %d 篇' % len(found), '落档 %d 篇' % len(captured)]
    if incomplete:
        counted.append('%d 篇媒体未补全' % len(incomplete))
    report = ['· %s · %d 图 %d 视频 · %s' % (post_id, row.get('images', 0), row.get('videos', 0),
                                            row.get('folder') or '已入档')
              for post_id, row in captured.items()]
    report += ['· %s · 媒体未补全，保留原归档，下一轮重试' % post_id for post_id in incomplete]
    missing = [row['post_id'] for row in found
               if row['post_id'] not in captured and row['post_id'] not in incomplete]
    report += ['· %s · 本轮没有落档记录' % post_id for post_id in missing]
    risk = ''
    if incomplete or missing:
        # 原图 CDN URL 带签名且有时效，"下一轮再抓"不是稳妥的假设（HANDOFF §5）。
        risk = ('有 %d 篇没有完整落档。原图链接有时效，不能假设下一轮还能补回来，'
                '请核对抓取日志。' % (len(incomplete) + len(missing)))
    second = dict(base, text='\n'.join(['，'.join(counted), *report, skip_line]).strip(), risk=risk,
                  next_step='已落档的内容进入本地化排队；跳过分类不加工也不发布。'
                  if captured else '本轮没有任何内容落档，请先核对抓取日志与登录状态。')
    return first, second


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
    if path is None and lead is not None:
        path, _ = image_de._source_from_manifest(account, source, lead[1])
    if path:
        assert_physical_direct_path(path.parent, path, kind='file', label='通知首图')
    caption = localization.render(draft) if effective else '德语稿尚未就绪，请进入页面查看待处理问题。'
    return {'text': caption[:1000], 'risk': '\n'.join(dict.fromkeys(notes)),
            'image_variant': variant, 'image_note': '德语首图' if variant == 'de' else '尚无有效德语首图，预览使用原图',
            'image_count': sum(m.get('kind') == 'image' for m in source.get('media', [])),
            'source_text_sha256': translated.source_text_sha256(source['text']),
            'checks': checks}, path
