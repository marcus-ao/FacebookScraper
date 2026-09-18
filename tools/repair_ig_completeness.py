"""依据指定 capture 离线修复一篇 IG 单图或单视频的完整性；默认预览，不下载或调用模型。"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from core import index_db, maintenance, paid_model
from core.config import cfg
from core.console import force_utf8
from core.archive_integrity import repair


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
