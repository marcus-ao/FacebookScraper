"""依据指定 capture 离线修复一篇 IG 单图的完整性；默认预览，不下载或调用模型。"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from core import index_db, maintenance, paid_model
from core.config import cfg
from core.console import force_utf8
from core.parse import from_iphone_struct, is_iphone_struct, on_timeline_of, walk
from core.store import (Archive, _atomic_write_text, archive_write_lock,
                        assert_physical_direct_path, media_storage_info, read_post_truth)


def repair(account_dir: Path, post_id: str, capture: Path, *, apply: bool = False) -> dict:
    account_dir, capture = Path(account_dir), Path(capture)
    if not account_dir.is_dir():
        raise ValueError('账号归档目录不存在')
    assert_physical_direct_path(account_dir.parent, account_dir, kind='directory', label='账号归档')
    assert_physical_direct_path(capture.parent, capture, kind='file', label='capture')
    captured = capture.read_bytes()
    payload = json.loads(captured.decode('utf-8-sig'))
    # 逐份匹配证据都须一致；extract 的择优合并可能掩盖同帖矛盾片段。
    nodes = list(walk(payload, lambda node: any(
        str(node.get(key) or '').split('_')[0] == post_id for key in ('pk', 'id'))))
    if not nodes:
        raise ValueError('capture 中没有指定帖子的来源证据')

    with archive_write_lock(account_dir):
        arc = Archive(account_dir.parent, account_dir.name)
        indexed = next((row for row in arc.rows() if row['post_id'] == post_id), None)
        if indexed is None:
            raise ValueError('归档索引中没有指定帖子；不创建新帖')
        source, directory = read_post_truth(account_dir, indexed)
        if (source.get('platform') != 'instagram' or account_dir.name != 'in_' + source.get('account', '')
                or source.get('source_media_count') not in (None, 1)
                or len(source.get('media') or []) != 1 or source['media'][0].get('kind') != 'image'):
            raise ValueError('仅支持已有的一篇 Instagram 单图归档')
        media = source['media'][0]
        for node in nodes:
            if (not is_iphone_struct(node) or node.get('media_type') != 1
                    or node.get('carousel_media') not in (None, [])):
                raise ValueError('来源不是明确单图，或包含矛盾的轮播证据')
            parsed = from_iphone_struct(node, source['account'], 'backfill')
            if (parsed.post_id != post_id or not parsed.source_media_complete
                    or parsed.source_media_count != 1 or len(parsed.media) != 1
                    or parsed.media[0].kind != 'image'
                    or not on_timeline_of(parsed, source['account'])
                    or parsed.owner != source.get('owner')
                    or parsed.permalink != source.get('permalink')
                    or parsed.media[0].url != media.get('url')
                    or (media.get('source_media_id') and
                        parsed.media[0].source_media_id != media['source_media_id'])):
                raise ValueError('来源与归档的帖子、作者、图片或完整性证据不一致')
        observed = media_storage_info(account_dir, source)
        if len(observed) != 1 or observed[0]['storage_status'] != 'saved':
            raise ValueError('原图缺失、损坏或与归档校验值不一致；未修改完整性')

        updated = dict(source, source_media_complete=True, source_media_count=1, media_complete=True)
        changed = updated != source
        report = {'post_id': post_id, 'account': source['account'], 'apply': apply,
                  'changed': changed, 'source_media_complete': True, 'source_media_count': 1,
                  'media_complete': True, 'verified_images': 1, 'backup': None,
                  'capture_sha256': hashlib.sha256(captured).hexdigest(),
                  'image_sha256': observed[0]['sha256']}
        if not apply:
            return report
        if changed:
            truth = directory / 'post.json'
            before = truth.read_bytes()
            backup = directory / ('post.json.before-completeness-' + uuid4().hex + '.bak')
            # 先独占建立原字节备份，再原子更新；原图及全部人工/付费/发布账本不写入。
            with backup.open('xb') as stream:
                stream.write(before)
            report['backup'] = str(backup)
            _atomic_write_text(backup.with_suffix('.evidence.json'), json.dumps(report, ensure_ascii=False, indent=2),
                               label='完整性修复依据')
            _atomic_write_text(truth, json.dumps(updated, ensure_ascii=False, indent=2), label='post.json')
        # post.json 已更新而 manifest 写入中断时，重跑只修派生行，不再改真相或重复备份。
        if indexed != updated:
            paid_model.append_jsonl(arc.manifest, updated, guard=lambda path: assert_physical_direct_path(
                account_dir, path, kind='file', label='manifest.jsonl'))
        return report


@maintenance.guarded('repair_ig_completeness_cli')
def main(argv=None) -> int:
    force_utf8()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--post-id', required=True, help='只修复这一篇，不扫描整批帖子')
    parser.add_argument('--capture', type=Path, required=True, help='已有响应 JSON；只给文件名时从配置的 IG 账号归档目录读取')
    parser.add_argument('--apply', action='store_true', help='备份后写入；省略则只预览')
    args = parser.parse_args(argv)
    c = cfg()
    account_dir = c.archive_dir / ('in_' + c['targets']['instagram'])
    capture = account_dir / args.capture if len(args.capture.parts) == 1 else args.capture
    try:
        result = repair(account_dir, args.post_id, capture, apply=args.apply)
    except (OSError, ValueError, paid_model.FileLockBusy) as exc:
        print('修复未完成：%s；保留现场，核对 capture 和原图后可重跑同一命令。' % exc)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.apply:
        try:
            for history, name in ((False, 'index.sqlite'), (True, 'index-history.sqlite')):
                index_db.rebuild_index(c.archive_dir, c.state_dir / name,
                                      state_dir=c.state_dir, include_frozen=history)
        except (OSError, ValueError, sqlite3.DatabaseError, paid_model.FileLockBusy) as exc:
            print('单帖归档已核对，但派生索引重建未完成：%s。保留备份，重跑同一命令可继续。' % exc)
            return 1
        print('单帖归档与两个展示索引已核对；请重启服务并重新打开详情。')
    else:
        print('仅预览，未修改业务记录；确认帖子后用同一命令加 --apply 执行。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
