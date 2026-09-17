"""一次提交的可观察记录：进度落盘，页面轮询，进程死掉也能判定。

⛔ 这不是发布队列。同一时刻只允许一条运行中的记录，第二个请求直接被拒；排队会破坏
`pipeline` 的对账原则（见 FUNCTIONALITY F5-9）。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from core.config import cfg
from core.paid_model import atomic_write_json
from core.process_identity import current_worker, worker_alive
from core.store import assert_physical_direct_path

# 浏览器提交的七步，与 publish/workflow.py 的阶段一一对应。
STEPS = ('提交前核对后台已有排期', '打开编辑器', '核对登录态与目标账号', '上传图片',
         '填写正文', '设置发布时间', '提交并回读月历')
RUNNING, SUCCEEDED, FAILED, UNCERTAIN = 'running', 'succeeded', 'failed', 'uncertain'


def _root() -> Path:
    root = cfg().state_dir / 'publish_operations'
    assert_physical_direct_path(root.parent, root, kind='directory', label='发布操作目录')
    return root


def _path(operation_id: str) -> Path:
    if not isinstance(operation_id, str) or not re.fullmatch(r'[0-9a-f]{32}', operation_id):
        raise ValueError('发布操作编号无效')
    root = _root()
    return assert_physical_direct_path(root, root / (operation_id + '.json'),
                                       kind='file', label='发布操作记录')


def _write(record: dict) -> dict:
    record['updated_at'] = datetime.now(timezone.utc).isoformat()
    atomic_write_json(_path(record['operation_id']), record)
    return record


def start(task_id: str, *, platform: str, snapshot_id: str, scheduled_at: datetime) -> dict:
    _root().mkdir(parents=True, exist_ok=True)
    moment = datetime.now(timezone.utc).isoformat()
    return _write({'version': 1, 'operation_id': uuid4().hex, 'task_id': task_id,
                   'platform': platform, 'snapshot_id': snapshot_id,
                   'scheduled_at': scheduled_at.isoformat(), 'status': RUNNING,
                   'step_index': 0, 'step_total': len(STEPS), 'step': '准备中',
                   'message': '', 'result': None, 'worker': current_worker(),
                   'started_at': moment, 'updated_at': moment})


def progress(operation_id: str, step_index: int, step: str) -> None:
    """浏览器每走一步调一次。记录丢失或损坏不能中断提交——提交本身才是要紧的。"""
    try:
        record = json.loads(_path(operation_id).read_text(encoding='utf-8'))
        if record.get('status') != RUNNING:
            return
        _write({**record, 'step_index': step_index, 'step': step})
    except (OSError, ValueError):
        return


def finish(operation_id: str, *, status: str, message: str, result=None) -> dict:
    record = json.loads(_path(operation_id).read_text(encoding='utf-8'))
    return _write({**record, 'status': status, 'message': message, 'result': result,
                   'step_index': record['step_total'] if status == SUCCEEDED else record['step_index']})


def read(operation_id: str) -> dict | None:
    try:
        record = json.loads(_path(operation_id).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if record.get('status') == RUNNING and worker_alive(record.get('worker')) is False:
        # 进程没了不等于远端没提交：点击意图已耐久记在发布账本里，必须人工核对。
        return _write({**record, 'status': UNCERTAIN,
                       'message': '提交进程已退出，结果不明确；请核对发布回执，不要直接重试。'})
    return record


def active() -> dict | None:
    """当前是否已有运行中的提交。发布锁是权威，这里只为给出能读懂的提示。"""
    root = cfg().state_dir / 'publish_operations'
    if not root.is_dir():
        return None
    for path in sorted(root.glob('*.json')):
        record = read(path.stem)
        if record and record['status'] == RUNNING:
            return record
    return None


def for_task(task_id: str, snapshot_id: str | None) -> dict | None:
    """Recover the latest submission of this frozen revision after a page reload."""
    if not snapshot_id:
        return None
    matches = []
    for path in _root().glob('*.json'):
        record = read(path.stem)
        if record and record.get('task_id') == task_id and record.get('snapshot_id') == snapshot_id:
            matches.append(record)
    return max(matches, key=lambda row: row['started_at'], default=None)
