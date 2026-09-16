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
    outbox.enqueue('deployment:' + str(event['event_id']) + ':' + event['kind'], 'system',
        {'text': summary, 'meta': '运行版本：' + str(event.get('sha', ''))[:12],
         'next_step': str(event.get('error_code') or '可在运行页查看更新状态')}, now)
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
