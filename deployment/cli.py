"""Local managed-service commands; only install-task registers a Windows task."""
from __future__ import annotations

import argparse
import json
import os
import runpy
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from core.maintenance import Gate, operation
from core.console import note_stop
from core.paid_model import FileLock
from deployment.controller import Controller
from deployment.github import GitHub, GitHubError
from deployment.errors import DeploymentError
from deployment.host import (LocalBackend, check_legacy_tasks, install, install_task,
                             read_json, update_settings, validate_root, worker_environment)


def _managed_command(root, arguments):
    validate_root(root)
    state = read_json(root / 'control/deployment.json')
    backend = LocalBackend(root)
    backend.manifest(state['current_sha'])
    release = root / 'releases' / state['current_sha']
    return subprocess.run([str(release / '.venv/Scripts/python.exe'), '-m', 'deployment',
        '_exec-worker', '--root', str(root), '--', *arguments], cwd=release,
        env=worker_environment(root / 'control', release / 'config.local.toml', uuid4().hex)).returncode


def _exec_worker(arguments):
    if arguments and arguments[0] == '--':
        arguments = arguments[1:]
    if not arguments:
        raise DeploymentError('exec_python_arguments_required')
    # 延迟导入：必须先进入带受管环境变量的发行版本子进程，再加载业务配置。
    from core.config import cfg, ROOT
    from core.runtime_identity import read_release, validate_binding
    validate_binding(cfg(), ROOT)
    with operation('manual_cli'):
        current = read_json(Path(os.environ['FBSCRAPER_CONTROL_DIR']) / 'deployment.json')['current_sha']
        if read_release(ROOT)['sha'] != current:
            raise DeploymentError('stale_cli_release')
        if arguments[0] == '-m' and len(arguments) >= 2:
            sys.argv = arguments[1:]
            runpy.run_module(arguments[1], run_name='__main__', alter_sys=True)
        elif arguments[0] == '-c' and len(arguments) >= 2:
            sys.argv = ['-c', *arguments[2:]]
            exec(compile(arguments[1], '<managed-command>', 'exec'), {'__name__': '__main__'})
        elif not arguments[0].startswith('-'):
            sys.argv = arguments
            runpy.run_path(arguments[0], run_name='__main__')
        else:
            raise DeploymentError('exec_supports_module_script_or_command')
    return 0


def _process_prerequisite(root):
    validate_root(root)
    # Read only the activation boundary; this command does not activate or call a model.
    script = ('from pipeline.engine import activation_time; from core.config import cfg; '
              'raise SystemExit(0 if activation_time(cfg().state_dir) is not None else 3)')
    if _managed_command(root, ['-c', script]):
        raise DeploymentError('process_requires_activation')


def _notification(root, kind, payload):
    state = read_json(root / 'control/deployment.json')
    release = root / 'releases' / state['current_sha']
    result = subprocess.run([str(release / '.venv/Scripts/python.exe'), '-m', 'deployment.notifications',
                            '--root', str(root)], input=json.dumps({'kind': kind, **payload}),
        text=True, capture_output=True, timeout=45, cwd=release,
        env=worker_environment(root / 'control', release / 'config.local.toml', uuid4().hex),
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise DeploymentError('notification_failed')


def supervise(root):
    validate_root(root)
    check_legacy_tasks()
    with FileLock(root / 'control/controller.lock', busy_message='controller_already_running'):
        remote = GitHub(root / 'control/github.token')
        backend = LocalBackend(root, remote)
        controller = Controller(root, backend, remote,
                                notify=lambda kind, payload: _notification(root, kind, payload))
        while True:
            controller.tick()
            time.sleep(1)


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest='command', required=True)
    for name in ('install', 'install-task', 'supervise', 'status', 'pause', 'resume', 'retry',
                 'rollback', 'clear-session', 'mode', 'exec', '_exec-worker'):
        command = commands.add_parser(name)
        command.add_argument('--root', type=Path, required=True)
        if name == 'install':
            command.add_argument('--release', type=Path, required=True)
            command.add_argument('--network-config', type=Path,
                                 help='Read installation network settings from a separate JSON file')
            command.add_argument('--web-host', choices=('127.0.0.1', '0.0.0.0'))
            command.add_argument('--web-port', type=int)
            command.add_argument('--public-base-url')
            command.add_argument('--allow-client-subnet', action='append', default=[])
        elif name == 'install-task':
            command.add_argument('--dry-run', action='store_true')
        elif name == 'rollback':
            command.add_argument('--sha', required=True)
        elif name == 'clear-session':
            command.add_argument('--session', required=True)
        elif name == 'mode':
            command.add_argument('--scheduler', choices=('on', 'off'))
            command.add_argument('--process', choices=('on', 'off'))
        elif name in {'exec', '_exec-worker'}:
            command.add_argument('python_args', nargs=argparse.REMAINDER)
    return result


def _install_network(args):
    if args.network_config is not None:
        if (args.web_host is not None or args.web_port is not None
                or args.public_base_url is not None or args.allow_client_subnet):
            raise DeploymentError('network_config_conflicts_with_flags')
        value = read_json(args.network_config)
        if set(value) != {'web_host', 'web_port', 'public_base_url', 'allowed_client_cidrs'}:
            raise DeploymentError('network_config_requires_exact_network_fields')
        return value
    return {'web_host': args.web_host if args.web_host is not None else '127.0.0.1',
            'web_port': args.web_port if args.web_port is not None else 8765,
            'public_base_url': args.public_base_url, 'allowed_client_cidrs': args.allow_client_subnet}


def main(argv=None):
    args = parser().parse_args(argv)
    root = args.root.resolve()
    if args.command == 'install':
        value = install(root, args.release, **_install_network(args))
    elif args.command == 'install-task':
        value = install_task(root, dry_run=args.dry_run)
    elif args.command == 'supervise':
        return supervise(root)
    elif args.command == '_exec-worker':
        return _exec_worker(args.python_args)
    else:
        validate_root(root)
        if args.command == 'status':
            value = {'deployment': read_json(root / 'control/deployment.json'),
                     'settings': read_json(root / 'control/host.json'),
                     'maintenance': Gate(root / 'control').status()}
        elif args.command in {'pause', 'resume'}:
            value = update_settings(root, {'paused': args.command == 'pause'})
        elif args.command in {'retry', 'rollback'}:
            request = {'kind': args.command}
            if args.command == 'rollback':
                state = read_json(root / 'control/deployment.json')
                if args.sha not in state['history']:
                    raise DeploymentError('rollback_not_retained_success')
                LocalBackend(root).manifest(args.sha)
                request['sha'] = args.sha
            value = update_settings(root, {'request': request, 'request_id': uuid4().hex})
        elif args.command == 'clear-session':
            Gate(root / 'control').clear_session(args.session)
            value = {'cleared_session': args.session}
        elif args.command == 'mode':
            if args.scheduler is None and args.process is None:
                raise DeploymentError('explicit_mode_required')
            settings = read_json(root / 'control/host.json')
            updates = {key + '_enabled': getattr(args, key) == 'on'
                       for key in ('scheduler', 'process') if getattr(args, key) is not None}
            proposed = dict(settings, **updates)
            if proposed['process_enabled'] and not proposed['scheduler_enabled']:
                raise DeploymentError('process_requires_scheduler')
            if args.process == 'on':
                _process_prerequisite(root)
            value = update_settings(root, updates)
        elif args.command == 'exec':
            arguments = args.python_args[1:] if args.python_args[:1] == ['--'] else args.python_args
            return _managed_command(root, arguments)
        else:
            raise DeploymentError('unknown_command')
    print(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2))
    return 0


def entrypoint():
    try:
        return main()
    except KeyboardInterrupt:
        note_stop()
        return 130
    except Exception as exc:
        # Underlying HTTP/process exceptions can include credentials; never print arbitrary text.
        code = str(exc) if isinstance(exc, (DeploymentError, GitHubError)) else type(exc).__name__
        print('Deployment command failed: ' + code, file=sys.stderr)
        return 1
