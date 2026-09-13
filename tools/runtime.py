"""Bind verified existing runtime data; launch all entry points with one interpreter."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def verified_binding(origin: Path, manifest_path: Path, python: Path) -> str:
    origin, manifest_path, python = origin.resolve(), manifest_path.resolve(), python.resolve()
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('status') != 'verified' or Path(manifest['origin']).resolve() != origin:
        raise ValueError('备份清单不属于此数据目录或未完成校验')
    backup = manifest_path.parent / 'runtime.zip'
    with zipfile.ZipFile(backup) as bundle:
        if bundle.testzip():
            raise ValueError('备份文件校验失败')
        for row in manifest['files']:
            rel = Path(row['path'])
            path = (origin / rel).resolve()
            if rel.is_absolute() or not path.is_relative_to(origin):
                raise ValueError('备份清单含越界路径')
            if rel.suffix != '.jsonl':
                continue
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != row['sha256']:
                raise ValueError('备份后账本已变化，请重新备份：' + str(rel))
            if hashlib.sha256(bundle.read(rel.as_posix())).hexdigest() != row['sha256']:
                raise ValueError('备份账本与清单不一致：' + str(rel))
            for number, line in enumerate(raw.decode('utf-8-sig').splitlines(), 1):
                if line.strip():
                    try:
                        if not isinstance(json.loads(line), dict):
                            raise ValueError('not an object')
                    except ValueError as exc:
                        raise ValueError(f'{rel}:{number} 账本损坏，未绑定') from exc
    if not python.is_file() or not (origin / 'archive').is_dir() or not (origin / 'state').is_dir():
        raise ValueError('归档、状态目录或 Python 不存在')
    paths = {'archive': origin / 'archive', 'state': origin / 'state'}
    runtime = {'python': python, 'env_file': origin / '.env', 'backup_manifest': manifest_path}
    return '# Verified existing data. All entry points share these paths and locks.\n' + '\n'.join(
        '[' + section + ']\n' + '\n'.join(key + ' = ' + json.dumps(str(value), ensure_ascii=False)
                                         for key, value in values.items())
        for section, values in [('paths', paths), ('runtime', runtime)]) + '\n'


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == 'exec' and (len(argv) < 2 or argv[1] != '--'):
        argv.insert(1, '--')
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    bind = sub.add_parser('bind')
    bind.add_argument('--origin', type=Path, required=True)
    bind.add_argument('--backup-manifest', type=Path, required=True)
    bind.add_argument('--python', type=Path, required=True)
    sub.add_parser('status')
    run = sub.add_parser('exec')
    run.add_argument('args', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    local_path = Path(os.environ.get('FBSCRAPER_RUNTIME_CONFIG') or ROOT / 'config.local.toml')
    if args.command == 'bind':
        content = verified_binding(args.origin, args.backup_manifest, args.python)
        # Never silently replace a previous developer binding.
        with local_path.open('x', encoding='utf-8') as handle:
            handle.write(content)
        print('已绑定现有数据：' + str(local_path))
    elif args.command == 'exec':
        local = tomllib.loads(local_path.read_text(encoding='utf-8')) if local_path.exists() else {}
        python = Path(local.get('runtime', {}).get('python') or ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python'))
        if not python.is_file():
            raise ValueError('请先配置 config.local.toml 的 runtime.python 或创建项目 .venv')
        arguments = args.args[1:] if args.args[:1] == ['--'] else args.args
        if not arguments:
            parser.error('exec 需要 Python 参数，例如 -m pipeline preflight')
        return subprocess.call([str(python), *arguments], cwd=ROOT)
    else:
        from core.config import cfg
        c = cfg()
        print(json.dumps({'archive': str(c.archive_dir), 'state': str(c.state_dir),
                          'publish_ledger': str(c.state_dir / 'published.jsonl'),
                          'publish_lock': str(c.state_dir / 'publish.lock'),
                          'runtime_config': str(local_path), 'backup_manifest': c.get('runtime', 'backup_manifest')},
                         ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
