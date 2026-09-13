"""Read effective review material for Feishu, sharing the editor's selection rules."""
from __future__ import annotations

from pipeline.risk_scan import result_for
from core import localization, translated
from core.store import assert_physical_direct_path
import localize_images


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
    pairs = localize_images.review_image_pairs(account, source, image_text) if image_text else []
    first = next((p for p in pairs if p.media_index == 0), None)
    path, variant = None, 'original'
    if first and first.localized_rel:
        path, variant = account / first.localized_rel, 'de'
    if path is None:
        media = next((m for m in source.get('media', []) if m.get('kind') == 'image'), None)
        if media:
            path, _ = localize_images._source_from_manifest(account, source, media)
    if path:
        assert_physical_direct_path(path.parent, path, kind='file', label='通知首图')
    caption = localization.render(draft) if effective else '德语稿尚未就绪，请进入页面查看待处理问题。'
    return {'text': caption[:1000], 'risk': '\n'.join(dict.fromkeys(notes)),
            'image_variant': variant, 'image_note': '德语首图' if variant == 'de' else '尚无有效德语首图，预览使用原图',
            'image_count': sum(m.get('kind') == 'image' for m in source.get('media', [])),
            'source_text_sha256': translated.source_text_sha256(source['text']),
            'checks': checks}, path
