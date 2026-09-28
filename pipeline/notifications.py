"""Read effective review material for Feishu, sharing the editor's selection rules."""
from __future__ import annotations

import re

from pipeline.risk_scan import result_for
from publish import compose
from core import localization, translated
from core.integrity import parse_ts
from core.store import assert_physical_direct_path
from localize import images as image_de

IMAGE_NOTES = {'de': '德语首图', 'original_confirmed': '首图已确认使用原图',
               'original': '尚无有效德语首图，预览使用原图',
               'unreadable': '首图读不出，请进入审校台核对'}

# 待人工记录的原文带帖子编号、配置键、命令和“硬闸”术语，是留给维护人员查账的；
# 待审卡只说业务能做的下一步。原文仍在待人工记录与运行页里。
_KIND_NOTES = {
    'human_translation_stale': '原帖已更新，人工德语稿需要重新复核并保存。',
    'unknown_owner': '原帖作者信息不完整，暂不能发布，请联系维护人员核对。',
    'unknown_collaborator': '这篇是合作帖，合作方尚未确认，请在审校台确认来源与处理许可。',
    'unmapped_price': '原帖里的金额还没有德国站价格对照，请联系维护人员补充。',
}
# 按顺序取第一条命中：价格对照先于金额，德语图先于泛指的图片问题。
_GATE_NOTES = (
    (r'未映射金额|price_map|价格映射', _KIND_NOTES['unmapped_price']),
    (r'金额', '德语正文里的金额与原帖不一致，请回到德语正文核对金额写法。'),
    (r'IG 正文长度', '德语文案超过 Instagram 长度上限，请精简正文。'),
    (r'IG 标签数', '话题标签超过 Instagram 上限，请删减标签。'),
    (r'IG 图片数', '图片数量超过 Instagram 单帖上限，请联系维护人员处理。'),
    (r'画幅比', '有图片的画幅超出 Instagram 允许范围，请换图或联系维护人员。'),
    (r'缺少德语图|多个人工图片候选', '仍有图片没有可发布的版本，请回到逐张图片处理。'),
    (r'没有译文|德语译文缺失', '尚无德语正文，请先在德语正文步骤补充。'),
    (r'源帖已变更|指纹已过期|提示词版本已过期', '原帖或德语初稿已更新，请回到德语正文重新复核并保存。'),
    (r'owner|coauthors|归属', _KIND_NOTES['unknown_owner']),
    (r'G1|probe|排期', '发布环境尚未完成校准，请联系维护人员处理。'),
    (r'图片|媒体|media|原文缺失', '原帖素材不完整或读不出，请联系维护人员核对归档。'),
)
GATE_FALLBACK = '发布前自动检查未通过，请联系维护人员核对。'


def review_note(kind: str, summary: str) -> str | None:
    """待审卡上的处理说明；返回 None 表示由当前稿校验逐项给出，不再重复。"""
    if kind in _KIND_NOTES:
        return _KIND_NOTES[kind]
    if compose.platform_text_unready(summary):
        return None
    return next((note for pattern, note in _GATE_NOTES if re.search(pattern, summary)), GATE_FALLBACK)


def _origin(row) -> str:
    """合作帖由一方发布、双方主页同时显示；不写清楚会被当成本账号原创。"""
    owner, account = (row.get('owner') or '').strip(), (row.get('account') or '').strip()
    parts = ['合作帖，原作者 %s' % owner] if owner and account and owner != account else []
    names = [name for name in row.get('coauthors') or [] if isinstance(name, str) and name.strip()]
    if names:
        parts.append('合作方 %s' % '、'.join(names))
    return '（%s）' % '；'.join(parts) if parts else ''


def capture_card(event):
    row = event['source']
    saved, expected = event.get('saved_images', 0), row.get('source_media_count')
    complete = event['status'] == 'complete'
    status = '完整' if complete else '部分完成 / 待人工' if event.get('archived') else '失败 / 待人工'
    image_status = (f'已保存并校验 {saved}/{expected} 张' if expected is not None else
                    f'已保存并校验 {saved} 张，原帖总数待确认')
    return {'capture_status': status, 'media_summary': image_status,
            'platform': row['platform'], 'account': row['account'],
            'published_at': row.get('created_at'), 'discovered_at': event.get('first_seen_at'),
            'scraped_at': event.get('finished_at'), 'text': row.get('text') or '',
            'origin_note': _origin(row), 'classification': event['classification'],
            'action_recommendation': event.get('reason') if not complete else None,
            'account_dir': row['platform'][:2] + '_' + row['account'], 'post_id': row['post_id'],
            'capture_key': event['key'], 'archived': bool(event.get('archived')),
            'permalink': row.get('permalink')}


def scan_cards(run_kind, platform, account, rows, skipped, now):
    """同一扫描的持久结果：检测汇总与逐帖结果；原始事件 ID 由调用方保留。"""
    if not rows:
        return None
    counts = {}
    for row in rows:
        label = row['classification']
        counts[label] = counts.get(label, 0) + 1
    dated = [(parse_ts(row['source'].get('created_at')), row) for row in rows]
    known = [(stamp, row) for stamp, row in dated if stamp is not None]
    latest = max(known, key=lambda pair: pair[0])[1] if known else rows[0]
    source = latest['source']
    first = {'platform': platform, 'account': account, 'counts': counts,
             'published_at': source.get('created_at'), 'discovered_at': latest.get('first_seen_at'),
             'text': source.get('text') or '', 'origin_note': _origin(source),
             'run_id': rows[0]['scan_id']}
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
        notes.extend(str(item.get('label') or item.get('message') or item.get('reason') or item.get('explanation') or item.get('text') or '')
                     for item in scan['risks'])
    if effective and effective.get('stale'):
        notes.append('当前德语稿的源文已有变化，请重新核对。')
    image_text = translated.image_translation(source, machine, human)
    pairs = image_de.review_image_pairs(account, source, image_text)
    # 首图取第一项 image；media_index 可能包含前置视频。
    lead = next(((index, item) for index, item in enumerate(source.get('media') or [])
                 if isinstance(item, dict) and item.get('kind') == 'image'), None)
    first = next((p for p in pairs if lead is not None and p.media_index == lead[0]), None)
    path, variant = None, 'original'
    if first and first.selected_rel:
        path, variant = account / first.selected_rel, ('original_confirmed' if first.selection == 'original_confirmed' else 'de')
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
            'risk_status': scan['status'], 'risk_count': len(scan['risks']) if scan['status'] == 'completed' else None,
            'image_variant': variant, 'image_note': IMAGE_NOTES[variant],
            'image_count': sum(m.get('kind') == 'image' for m in source.get('media', [])),
            'source_text_sha256': translated.source_text_sha256(source['text']),
            'checks': checks}, path
