"""本地归档完整性诊断与有来源证据的 IG 历史标记修复；不访问平台。"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from core import paid_model
from core.parse import from_iphone_struct, is_iphone_struct, on_timeline_of, walk
from core.store import (MEDIA_STORAGE_DETAILS, Archive, _atomic_write_text, archive_write_lock,
                        assert_physical_direct_path, media_storage_info, read_post_truth,
                        signed_url_expiry)

# 三类处置完全不同：重新取源要平台访问额度，裁决要人看两份证据，瞬时的什么都不用做。
# 合成一句话的后果真实发生过——每一轮都报同一句，运营分不出哪篇该动、哪篇本来就没事。
IMAGE_PROBLEM_ACTIONS = {
    'never_downloaded': ('refetch', '须重新取源'),
    'file_absent': ('refetch', '须重新取源'),
    'undecodable': ('refetch', '须重新取源'),
    'digest_mismatch': ('adjudicate', '归档记录与文件冲突，须人工裁决'),
    'size_mismatch': ('adjudicate', '归档记录与文件冲突，须人工裁决'),
    'changed_while_reading': ('transient', '不用处理，下一轮自然重新观察'),
    'locked': ('transient', '先关掉占着它的程序，审校台的图片预览就会占用'),
    'read_error': ('local', '本地磁盘或权限问题，先修好再核验'),
    'path_rejected': ('local', '归档路径越界，须人工核对目录'),
}


class ImagesUnusable(ValueError):
    """原图不能当完整性依据；`problems` 是逐张结论，调用方据此决定下一步而不是重读文件。"""

    def __init__(self, message: str, problems: list[dict]):
        super().__init__(message)
        self.problems = problems


def unusable_images(observed: list[dict]) -> list[dict]:
    """逐张列出不能当原图用的图片及其处置方向；视频的 metadata_only 不在其中。"""
    problems = []
    for item in observed:
        if item['kind'] != 'image' or item['storage_status'] == 'saved':
            continue
        detail = item['storage_detail']
        action, _advice = IMAGE_PROBLEM_ACTIONS[detail]
        problem = {'ordinal': item['ordinal'], 'status': item['storage_status'], 'detail': detail,
                   'action': action, 'explain': MEDIA_STORAGE_DETAILS[detail]}
        if action == 'refetch':
            expiry = signed_url_expiry(item.get('source_url'))
            # 归档里那个签名地址过期之后再请求只会拿到 403，直接重下永远失败。
            problem['source_url_expired'] = None if expiry is None else expiry <= datetime.now(timezone.utc)
            problem['source_url_expires_at'] = None if expiry is None else expiry.strftime('%Y-%m-%dT%H:%M:%SZ')
        problems.append(problem)
    return problems


def image_problem_reason(problems: list[dict]) -> str:
    """把逐张结论写成运营看得懂、且能据此决定下一步的一句话。"""
    parts = []
    for problem in problems:
        _action, advice = IMAGE_PROBLEM_ACTIONS[problem['detail']]
        if problem.get('source_url_expired'):
            advice += '，且归档里的图片地址已于 %s 过期，须先刷新详情再取' % problem['source_url_expires_at']
        elif problem.get('source_url_expired') is False:
            advice += '，归档里的图片地址仍在有效期内'
        parts.append('第 %d 张%s —— %s' % (problem['ordinal'] + 1, problem['explain'], advice))
    return '原图不可用：' + '；'.join(parts) + '；未修改完整性'


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
            unusable = unusable_images(observed)
            if source.get('source_media_complete') is False:
                problems.append('source_unconfirmed')
            if unusable:
                problems.append('images_unavailable')
            if indexed.get('media_complete') != source.get('media_complete'):
                problems.append('index_mismatch')
            if not source.get('media_complete', True) and not problems:
                problems.append('completeness_unconfirmed')
            result.update(post_media_complete=source.get('media_complete'),
                          source_media_complete=source.get('source_media_complete'),
                          source_media_count=source.get('source_media_count'),
                          media_files=[{'kind': m['kind'], 'status': m['storage_status'],
                                        'detail': m['storage_detail']} for m in observed])
            if unusable:
                result['image_problems'] = unusable
                counts.update('image_' + problem['detail'] for problem in unusable)
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
            # 汇总按细分原因分开数：要重新取源的、要人裁决的和瞬时的不能算成同一堆。
            'image_problem_counts': {key: value for key, value in sorted(counts.items())
                                     if key.startswith('image_')},
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
        problems = unusable_images(observed)
        if problems:
            raise ImagesUnusable(image_problem_reason(problems), problems)
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
            entry = {'post_id': post_id, 'reason': str(exc)}
            if isinstance(exc, ImagesUnusable):
                # 逐张结论随报告走，运营与后续工具都不必再去反推那句话是什么意思。
                entry['image_problems'] = exc.problems
                entry['actions'] = sorted({problem['action'] for problem in exc.problems})
            result['unresolved'].append(entry)
    return result
