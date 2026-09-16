"""云盘镜像入口；默认只读预览，run/--run 且配置启用时才会上传。"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import maintenance
from core.config import cfg  # noqa: E402
from core.console import force_utf8  # noqa: E402
from core.feishu import FeishuError  # noqa: E402
from core.mirror import DRIVE_ERROR_SUMMARIES, DriveClient, DriveError, MirrorError, MirrorService, MirrorSettings  # noqa: E402
from core.store import Archive, account_dirs, iter_post_dirs  # noqa: E402


@maintenance.guarded('mirror_cli')
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='单向镜像归档原帖与 state；默认只读预览')
    parser.add_argument('command', nargs='?', choices=('status', 'preflight', 'run', 'resolve'), default='status')
    parser.add_argument('--run', action='store_true', help='执行已启用的镜像配置，可能上传文件')
    parser.add_argument('--account', help='只处理指定账号目录；默认当前抓取目标')
    parser.add_argument('--operation-id')
    parser.add_argument('--sha256', help='文件操作冻结字节的完整 SHA-256')
    parser.add_argument('--remote-token')
    parser.add_argument('--not-created', action='store_true', help='人工确认该操作未成功；只恢复待执行，不自动重放')
    parser.add_argument('--note', help='人工核对依据，必填')
    args = parser.parse_args(argv)
    force_utf8()
    c = cfg()
    settings = MirrorSettings.load()
    directories = account_dirs(c.archive_dir, args.account)
    if args.account is None:
        directories = [directory for directory in directories if directory.name in c.active_accounts()]
    # state_dir 属性会 mkdir，预览直接读取配置路径。
    state_dir = ROOT / c.get('paths', 'state', 'state')
    service = MirrorService(state_dir, settings)
    now = datetime.now(timezone.utc)
    if args.command == 'resolve':
        if args.run:
            parser.error('resolve 与 --run 不可组合；确认后另行 run')
        if not args.operation_id or not args.note:
            parser.error('resolve 必须提供 --operation-id 和 --note')
        try:
            result = service.resolve(args.operation_id, sha256=args.sha256, remote_token=args.remote_token,
                                     not_created=args.not_created, note=args.note, now=now)
        except MirrorError as exc:
            print(str(exc))
            return 1
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == 'preflight':
        if args.run:
            parser.error('preflight 与 --run 不可组合')
        if not settings.root_folder_token:
            print('未配置镜像根目录。')
            return 1
        try:
            client = DriveClient.from_environment()
            try:
                result = service.preflight(client)
            finally:
                client.close()
        except Exception as exc:
            summary = (exc.summary if isinstance(exc, DriveError) else DRIVE_ERROR_SUMMARIES['auth']
                       if isinstance(exc, FeishuError) else '镜像预检未能完成，请核对本地队列与根目录配置。')
            print(json.dumps({'status': 'blocked', 'error': exc.details if isinstance(exc, DriveError) else type(exc).__name__,
                              'last_error': summary}, ensure_ascii=False))
            return 1
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if not args.run and args.command != 'run':
        try:
            queue, status = service.snapshot(), service.status()
        except (MirrorError, OSError, ValueError, KeyError, TypeError):
            print(json.dumps({'enabled': settings.enabled, 'status': 'blocked',
                              'error': 'mirror_queue_unreadable'}, ensure_ascii=False))
            return 1
        print(json.dumps({**status, 'mode': 'preview',
                          'accounts': [directory.name for directory in directories],
                          'source_directories': sum(1 for directory in directories for _ in iter_post_dirs(directory)),
                          'queued_snapshots': len(queue['snapshots']),
                          'mirror_state': settings.mirror_state}, ensure_ascii=False, indent=2))
        return 0
    if not settings.enabled:
        print('镜像未启用；请先配置 [mirror] 根目录与应用权限。未上传文件。')
        return 1
    failures = 0
    for directory in directories:
        for source in Archive(directory.parent, directory.name).rows():
            try:
                service.queue_source(directory, source, now=now)
            except Exception as exc:
                failures += 1
                print('原帖镜像待重试：%s/%s（%s）' % (directory.name, source['post_id'], type(exc).__name__))
    service.queue_state(state_dir, now=now, config_path=c.path)
    client = DriveClient.from_environment()
    try:
        result = service.dispatch(client, now=now)
    finally:
        client.close()
    print(json.dumps(result, ensure_ascii=False))
    return 1 if failures or result['pending'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
