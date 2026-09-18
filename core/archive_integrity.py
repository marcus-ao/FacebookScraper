"""本地归档完整性诊断与有来源证据的 IG 历史标记修复；不访问平台。"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from uuid import uuid4

from core import paid_model
from core.parse import from_iphone_struct, is_iphone_struct, on_timeline_of, walk
from core.store import (Archive, _atomic_write_text, archive_write_lock,
                        assert_physical_direct_path, media_storage_info, read_post_truth)


def archive_report(arc: Archive, capture_items=None) -> dict:
    """从真相及实际图片读当前缺口；采集状态是另一维，不据此推断归档完整。"""
    items = capture_items or {}
    incomplete = []
    counts = Counter()
    for indexed in arc.rows():
        key = ':'.join(str(indexed[name]) for name in ('platform', 'account', 'post_id'))
        item = items.get(key, {})
        problems = []
        result = {'post_id': indexed['post_id'], 'created_at': indexed.get('created_at'),
                  'manifest_media_complete': indexed.get('media_complete'),
                  'capture_status': item.get('status', 'not_in_capture_state'),
                  'capture_reason': item.get('reason')}
        try:
            source, _ = read_post_truth(arc.base, indexed)
            observed = media_storage_info(arc.base, source)
            if source.get('source_media_complete') is False:
                problems.append('source_unconfirmed')
            if any(m['kind'] == 'image' and m['storage_status'] != 'saved' for m in observed):
                problems.append('images_unavailable')
            if indexed.get('media_complete') != source.get('media_complete'):
                problems.append('index_mismatch')
            if not source.get('media_complete', True) and not problems:
                problems.append('completeness_unconfirmed')
            result.update(post_media_complete=source.get('media_complete'),
                          source_media_complete=source.get('source_media_complete'),
                          source_media_count=source.get('source_media_count'),
                          media_files=[{'kind': m['kind'], 'status': m['storage_status']} for m in observed])
            if problems:
                counts['metadata_only_videos'] += sum(m['kind'] == 'video' for m in observed)
        except (OSError, ValueError) as exc:
            problems.append('archive_unreadable')
            result['read_error'] = str(exc)
        if problems:
            result['problems'] = problems
            incomplete.append(result)
            counts.update(problems)
    return {'account': arc.base.name, 'archive_posts': len(arc.rows()),
            'archive_incomplete': len(incomplete),
            **{key: counts[key] for key in ('source_unconfirmed', 'images_unavailable',
                'metadata_only_videos', 'index_mismatch', 'archive_unreadable')},
            'incomplete_items': incomplete}


def _capture_evidence(paths, post_ids):
    evidence = {post_id: [] for post_id in post_ids}
    errors = []
    for path in paths:
        try:
            assert_physical_direct_path(path.parent, path, kind='file', label='capture')
            raw = path.read_bytes()
            payload = json.loads(raw.decode('utf-8-sig'))
            digest = hashlib.sha256(raw).hexdigest()
            for node in walk(payload, lambda n: any(
                    str(n.get(key) or '').split('_')[0] in evidence for key in ('pk', 'id'))):
                for post_id in {str(node.get(key) or '').split('_')[0] for key in ('pk', 'id')} & evidence.keys():
                    evidence[post_id].append((node, path.name, digest))
        except (OSError, ValueError) as exc:
            errors.append({'capture': path.name, 'error': type(exc).__name__})
    return evidence, errors


def _repair(account_dir, post_id, evidence, *, apply):
    with archive_write_lock(account_dir):
        arc = Archive(account_dir.parent, account_dir.name)
        indexed = next((row for row in arc.rows() if row['post_id'] == post_id), None)
        if indexed is None:
            raise ValueError('归档索引中没有指定帖子；不创建新帖')
        source, directory = read_post_truth(account_dir, indexed)
        observed = media_storage_info(account_dir, source)
        if any(m['kind'] == 'image' and m['storage_status'] != 'saved' for m in observed):
            raise ValueError('原图缺失、损坏或与归档校验值不一致；未修改完整性')
        already_complete = (source.get('source_media_complete') is True
                            and source.get('media_complete') is True
                            and source.get('source_media_count') == len(observed))
        proofs = []
        # 已写真相、未写 manifest 的中断可直接补索引，无需已过保留期的 capture。
        if not already_complete or evidence:
            if (source.get('platform') != 'instagram' or account_dir.name != 'in_' + source.get('account', '')
                    or source.get('source_media_count') not in (None, 1) or len(observed) != 1
                    or observed[0]['kind'] not in ('image', 'video')):
                raise ValueError('仅支持已有的一篇 Instagram 单图或单视频归档')
            if not evidence:
                raise ValueError('没有匹配的本地 capture 来源证据；保留原记录')
            media = source['media'][0]
            expected_type = 1 if media['kind'] == 'image' else 2
            # 每份证据都须一致；择优合并会掩盖同帖矛盾的轮播或身份片段。
            for node, filename, digest in evidence:
                if (not is_iphone_struct(node) or node.get('media_type') != expected_type
                        or node.get('carousel_media') not in (None, [])):
                    raise ValueError('来源不是明确单图或单视频，或包含矛盾的轮播证据')
                try:
                    parsed = from_iphone_struct(node, source['account'], 'backfill')
                except (ValueError, TypeError, AttributeError, OverflowError) as exc:
                    raise ValueError('来源结构无法核验；保留原记录') from exc
                if (parsed.post_id != post_id or not parsed.source_media_complete
                        or parsed.source_media_count != 1 or len(parsed.media) != 1
                        or parsed.media[0].kind != media['kind']
                        or not on_timeline_of(parsed, source['account'])
                        or parsed.owner != source.get('owner')
                        or parsed.permalink != source.get('permalink')
                        or parsed.media[0].url != media.get('url')
                        or (media.get('source_media_id') and
                            parsed.media[0].source_media_id != media['source_media_id'])):
                    raise ValueError('来源与归档的帖子、作者、媒体或完整性证据不一致')
                proofs.append({'capture': filename, 'sha256': digest, 'media_type': expected_type,
                               'carousel_media': node.get('carousel_media'),
                               'carousel_media_count': node.get('carousel_media_count'),
                               'post_id': parsed.post_id, 'owner': parsed.owner,
                               'coauthors': parsed.coauthors, 'permalink': parsed.permalink,
                               'media': [m.__dict__ for m in parsed.media]})
        updated = dict(source, source_media_complete=True, source_media_count=len(observed), media_complete=True)
        changed = updated != source
        report = {'post_id': post_id, 'account': source['account'], 'apply': apply,
                  'changed': changed, 'index_changed': indexed != updated,
                  'source_media_complete': True, 'source_media_count': len(observed),
                  'media_complete': True, 'verified_images': sum(m['kind'] == 'image' for m in observed),
                  'metadata_only_videos': sum(m['kind'] == 'video' for m in observed), 'backup': None,
                  'capture_sha256': evidence[0][2] if evidence else None,
                  'image_sha256': observed[0]['sha256'] if len(observed) == 1 else None}
        if not apply:
            return report
        if changed:
            truth = directory / 'post.json'
            before = truth.read_bytes()
            backup = directory / ('post.json.before-completeness-' + uuid4().hex + '.bak')
            with backup.open('xb') as stream:
                stream.write(before)
            report['backup'] = str(backup)
            _atomic_write_text(backup.with_suffix('.evidence.json'), json.dumps(
                dict(report, evidence=proofs), ensure_ascii=False, indent=2), label='完整性修复依据')
            _atomic_write_text(truth, json.dumps(updated, ensure_ascii=False, indent=2), label='post.json')
        if indexed != updated:
            paid_model.append_jsonl(arc.manifest, updated, guard=lambda path: assert_physical_direct_path(
                account_dir, path, kind='file', label='manifest.jsonl'))
        return report


def repair(account_dir: Path, post_id: str, capture: Path, *, apply: bool = False) -> dict:
    """保留指定单帖/证据的离线入口，与自动核验共用同一写入契约。"""
    account_dir = Path(account_dir)
    if not account_dir.is_dir():
        raise ValueError('账号归档目录不存在')
    assert_physical_direct_path(account_dir.parent, account_dir, kind='directory', label='账号归档')
    evidence, errors = _capture_evidence([Path(capture)], {post_id})
    if errors:
        raise ValueError('capture 无法安全读取：' + json.dumps(errors, ensure_ascii=False))
    if not evidence[post_id]:
        raise ValueError('capture 中没有指定帖子的来源证据')
    return _repair(account_dir, post_id, evidence[post_id], apply=apply)


def reconcile_instagram(arc: Archive) -> dict:
    """扫描前批量修复历史误标；无证据、真实缺图或矛盾记录保留并逐项说明。"""
    candidates = arc.needs_media()
    result = {'account': arc.base.name, 'checked': len(candidates), 'repaired': [], 'unresolved': []}
    if not candidates:
        return result
    evidence, errors = _capture_evidence(sorted(arc.base.glob('_capture*.json')),
                                         {row['post_id'] for row in candidates})
    for row in candidates:
        post_id = row['post_id']
        try:
            # 不能跳过坏 capture 再从其它文件挑有利证据，坏文件可能含矛盾片段。
            if errors:
                raise ValueError('本地 capture 无法完整核验：' + json.dumps(errors, ensure_ascii=False))
            report = _repair(arc.base, post_id, evidence[post_id], apply=True)
            result['repaired'].append(report)
        except (OSError, ValueError, paid_model.FileLockBusy) as exc:
            result['unresolved'].append({'post_id': post_id, 'reason': str(exc)})
    return result
