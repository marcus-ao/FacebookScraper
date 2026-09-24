"""Owned application processes: independent heartbeat and cooperative shutdown."""
from __future__ import annotations

import argparse
import os
from threading import Event, Thread

from core.config import ROOT, cfg
from core.maintenance import managed_gate, MaintenanceBlocked
from core.paid_model import FileLock
from core.process_identity import current_worker, process_identity
from core.runtime_identity import read_release, validate_binding
from core.web_access import load_web_access


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--role', choices=('web', 'scheduler'), required=True)
    parser.add_argument('--process', action='store_true')
    parser.add_argument('--host', choices=('127.0.0.1', '0.0.0.0'), default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args(argv)
    if args.process and args.role != 'scheduler':
        parser.error('--process only applies to scheduler')
    gate = managed_gate()
    manifest = read_release(ROOT)
    if gate is None or manifest is None or not os.environ.get('FBSCRAPER_LAUNCH_ID'):
        raise ValueError('受管进程必须由部署控制器启动')
    access = load_web_access(gate.control)
    if args.role == 'web' and (args.host != access.web_host or args.port != access.web_port):
        raise ValueError('web_binding_mismatch')
    instance = validate_binding(cfg(), ROOT)
    owner = current_worker()
    parent = os.getppid()
    controller_pid = int(os.environ.get('FBSCRAPER_CONTROLLER_PID', str(parent)))
    launcher = process_identity(parent) if parent != controller_pid else owner
    metadata = {key: manifest[key] for key in ('sha', 'runtime_id')}
    metadata.update(worker=owner, instance_id=instance['instance_id'], role=args.role,
                    launch_id=os.environ['FBSCRAPER_LAUNCH_ID'], launcher=launcher,
                    web_host=access.web_host, web_port=access.web_port,
                    public_base_url=access.public_base_url)
    stop, done = Event(), Event()
    with FileLock(gate.control / (args.role + '.lock'), busy_message='同一实例已有运行进程'):
        if args.role == 'web':
            import uvicorn
            server = uvicorn.Server(uvicorn.Config('web.api.app:app', host=args.host, port=args.port,
                                                   reload=False, workers=1, timeout_keep_alive=5,
                                                   proxy_headers=False))
            run = server.run
        else:
            from pipeline.scheduler import main as scheduler_main, Scheduler, default_callback
            from pipeline.service import Runtime
            # Exercise actual startup settings before advertising readiness, without
            # recovering processing records, drawing a persisted schedule or dispatching work.
            probe = Runtime(detector=default_callback, process=args.process, inspect_running=False)
            try:
                probe.processing.processing_status()
                Scheduler(cfg().state_dir / 'scheduler.json').preview()
            finally:
                probe.close()
            server = None
            def run():
                while gate.status()['phase'] == 'quiesced':
                    if stop.wait(.2):
                        return 0
                return scheduler_main(['--run', *(['--process'] if args.process else [])], stop_event=stop)

        def watch():
            while not done.is_set():
                try:
                    gate.heartbeat(args.role, metadata)
                    if gate.should_stop(owner):
                        stop.set()
                        if server is not None:
                            server.should_exit = True
                except (OSError, MaintenanceBlocked):
                    # Admission fails closed. Exit only after existing operations return.
                    stop.set()
                    if server is not None:
                        server.should_exit = True
                done.wait(1)

        watcher = Thread(target=watch, name='deployment-heartbeat', daemon=True)
        watcher.start()
        try:
            return run() or 0
        finally:
            done.set()
            watcher.join(timeout=5)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        from core.console import note_stop
        note_stop()
        raise SystemExit(0)
