"""Read effective review material for Feishu, sharing the editor's selection rules."""
from __future__ import annotations

from pipeline.risk_scan import result_for
from core import localization, translated
from core.store import assert_physical_direct_path, infer_tags, post_directory
from localize import images as image_de

# 发现卡片上的英文摘要长度；够判断"现在开还是等会儿开"，不喧宾夺主。
DISCOVERED_EXCERPT = 300


def discovered_material(account, source):
    """抓取刚完成时的卡片素材。此刻没有译文也没有德语图，**不要复用 material()**——
    它会去读 translated.jsonl 与 media_de/，在发现阶段只会退化成"德语稿尚未就绪"。"""
    images = [item for item in source.get('media') or []
              if isinstance(item, dict) and item.get('kind') == 'image']
    tags = source.get('tags')
    if not isinstance(tags, list) or not tags:
        tags = infer_tags(source.get('text') or '')
    directory = post_directory(account, source)
    meta = ['%d 张图 · 产品 %s' % (len(images), '、'.join(tags) if tags else '未分类'),
            '归档 %s' % directory.relative_to(account).as_posix()]
    owner = (source.get('owner') or '').strip()
    coauthors = [name for name in source.get('coauthors') or [] if isinstance(name, str) and name.strip()]
    if owner and owner != (source.get('account') or '').strip():
        # 合作帖由一方发布、双方主页同时显示；不写清楚会被当成本账号原创。
        meta.append('合作帖，原作者 %s' % owner)
    if coauthors:
        meta.append('合作方 %s' % '、'.join(coauthors))
    path = None
    if images:
        path, _ = image_de._source_from_manifest(account, source, images[0])
        assert_physical_direct_path(path.parent, path, kind='file', label='发现卡首图')
    return {'text': (source.get('text') or '').strip()[:DISCOVERED_EXCERPT],
            'meta': '\n'.join(meta), 'image_count': len(images), 'tags': list(tags),
            'image_note': '原帖预览，德语稿尚未开始'}, path


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
