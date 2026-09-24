"""月历的本地图层：系统自己知道的排期，与远端读回来的卡片并排显示。

⛔ 这一层**只作展示**。占用判定必须吃 `month_inventory` 实时读到的远端 inventory
（见 `planning.evaluate_slot`）——拿本地记录去证明自己没冲突，等于不检查。
"""
from __future__ import annotations

from datetime import datetime

from core import review
from core.config import cfg
from publish import journal, snapshots

# 本地图层的三种条目，按「离真正发出去还有多远」排序。
LOCKED = 'content_locked'          # 内容已冻结，还没选时间
SUBMITTING = 'submitting'          # 已选时间，正在提交或等回读
SCHEDULED = 'scheduled'            # 本地已记为已排期


def _moment(value):
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def entries(*, account_dirs=None) -> list[dict]:
    """读审校账本与发布账本，给出本地视角的待排期与已排期。查询不写盘。"""
    config = cfg()
    if account_dirs is None:
        account_dirs = [config.archive_dir / name for name in config.active_accounts()]
    rows = {row['attempt_id']: row for row in journal.load(config.state_dir)}.values()
    by_ref: dict[str, dict] = {}
    for row in rows:
        if row['status'] in {journal.STATUS_SUBMIT_AMBIGUOUS,
                             journal.STATUS_SUBMITTED_UNVERIFIED, journal.STATUS_SCHEDULED}:
            by_ref[journal.source_ref(row['platform'], row['post_id'])] = row
    result = []
    for account_dir in account_dirs:
        if not account_dir.is_dir():
            continue
        for post_id, state in review.latest(account_dir).items():
            if state['status'] not in {'content_locked', 'approved', 'scheduled'}:
                continue
            row = by_ref.get(journal.source_ref(state['platform'], post_id))
            at = _moment(row['scheduled_at']) if row else None
            if at is None and state.get('snapshot_id'):
                try:
                    at = _moment(snapshots.load(state['snapshot_id'])[0].get('scheduled_at'))
                except (review.ReviewConflict, OSError, ValueError):
                    at = None
            if state['status'] == 'scheduled' and row is not None and row['status'] == journal.STATUS_SCHEDULED:
                kind = SCHEDULED
            elif state['status'] == 'content_locked' and at is None:
                kind = LOCKED
            else:
                kind = SUBMITTING
            result.append({
                'kind': kind, 'task_id': account_dir.name + '/' + post_id,
                'platform': state['platform'], 'review_status': state['status'],
                'channels': list(row['target_channels']) if row else [],
                'at': at.isoformat() if at is not None else None,
                'snapshot_id': state.get('snapshot_id') or '',
                'remote_id': (row or {}).get('remote_id') or ''})
    result.sort(key=lambda item: (item['at'] or '', item['task_id']))
    return result
