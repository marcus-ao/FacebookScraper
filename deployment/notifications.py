"""Deployment events use a separate Feishu outbox and the existing uncertain-send rules."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from core.feishu import FeishuSettings, Outbox, WebhookBot


def deliver(control: Path, event: dict, *, settings=None, bot=None):
    settings = settings or FeishuSettings.load()
    if not settings.enabled:
        return 0
    if event['kind'] not in {'success', 'failure', 'rollback', 'blocked'}:
        raise ValueError('invalid_deployment_event')
    outbox = Outbox(control / 'deployment_outbox.json', settings)
    now = datetime.now(timezone.utc)
    summary = {'success': '服务更新完成', 'failure': '服务更新未完成，请检查运行状态',
               'rollback': '服务已回退到已验证版本', 'blocked': '服务更新已等待超过 30 分钟'}[event['kind']]
    occurred = event.get('occurred_at') or event.get('created_at')
    if occurred is None and type(event.get('at')) in {int, float}:
        try:
            occurred = datetime.fromtimestamp(event['at'], timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            occurred = None
    error = event.get('error_code')
    reason = {'worker_exit_unconfirmed': '上一运行实例的退出状态尚未确认',
              'rollback_start_failed': '回退版本未能启动', 'rollback_health_failed': '回退版本健康检查未通过',
              'running_worker_exited': '运行进程意外退出', 'running_worker_unknown': '无法确认运行进程状态',
              'stop_waiting_for_session': '正在等待当前会话结束',
              'switch_gate_not_closed': '服务切换前尚未进入维护状态'}.get(error, str(error or summary))
    outbox.enqueue('deployment:' + str(event['event_id']) + ':' + event['kind'], 'system',
        {'text': summary, 'deployment_status': event['kind'], 'module_name': '服务更新',
         'occurred_at': occurred, 'version': str(event.get('sha') or '')[:12], 'error_reason': reason,
         'next_step': '请在运行页核对更新状态与当前版本。'}, now)
    client = bot or WebhookBot.from_environment()
    return outbox.dispatch(now, client.send, allowed_kinds={'system'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    from deployment.host import validate_root
    validate_root(args.root)
    deliver(args.root / 'control', json.load(sys.stdin))


if __name__ == '__main__':
    main()
