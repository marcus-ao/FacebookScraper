"""Build and verify immutable Windows release payloads using an explicit allowlist."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import time
import zipfile
from pathlib import Path

SOURCE_DIRS = ('core', 'routes', 'localize', 'publish', 'pipeline', 'web/api', 'tools', 'deployment')
ROOT_FILES = ('config.toml', 'requirements.txt', 'requirements.lock', '.env.example')
EXCLUDED_PARTS = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', 'state', 'archive',
                  '.pytest_cache', '.env', 'config.local.toml'}
ARTIFACT_NAME = 'fbscraper-windows'
REPOSITORY = 'marcus-ao/FacebookScraper'
COMPATIBILITY = {'version': 1, 'protocol': 1, 'truth_contract': 1,
                 'python': '3.12.9', 'platform': 'win_amd64', 'workflow': 'release.yml'}


def _safe_name(name: str) -> str:
    if not isinstance(name, str) or not name or '\\' in name or ':' in name or name.startswith('/'):
        raise ValueError('Invalid release path')
    for part in name.split('/'):
        if not part or part in {'.', '..'} or part.endswith((' ', '.')) or any(ord(c) < 32 or c in '<>"|?*' for c in part):
            raise ValueError('Invalid release path')
        if re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', part):
            raise ValueError('Reserved Windows release path')
    return name


def _regular_path(root: Path, path: Path) -> None:
    try:
        path.relative_to(root)
        for item in (path, *path.parents):
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError('Release contains a link or reparse point')
            if item == root:
                break
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('Release path escapes its root')
    except (OSError, ValueError) as exc:
        raise ValueError('Unsafe or missing release path: ' + str(path)) from exc


def _file_entry(path: Path, name: str) -> dict:
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'path': name, 'size': path.stat().st_size, 'sha256': digest}


def _require_payload(names: set[str], *, wheels: bool) -> None:
    if not (set(ROOT_FILES) | {'web/ui/dist/index.html', 'web/ui/dist/runtime.json'}).issubset(names):
        raise ValueError('Missing required release inputs or frontend build')
    for folder in (*SOURCE_DIRS, 'scripts', 'prompts'):
        if not any(name.startswith(folder + '/') for name in names):
            raise ValueError('Missing runtime package: ' + folder)
    if wheels and not any(name.startswith('wheelhouse/') and name.endswith('.whl') for name in names):
        raise ValueError('Missing dependency wheelhouse')


def _selected(root: Path, *, frontend_inputs: bool) -> list[Path]:
    selected = [root / name for name in ROOT_FILES if (root / name).is_file()]
    for folder in (*SOURCE_DIRS, 'scripts', 'prompts', 'web/ui' if frontend_inputs else 'web/ui/dist'):
        base = root / folder
        if not base.is_dir():
            continue
        for path in base.rglob('*'):
            relative = path.relative_to(base)
            if any(part in EXCLUDED_PARTS or part.startswith('.') for part in relative.parts):
                continue
            if not path.is_file():
                continue
            if folder in SOURCE_DIRS and path.suffix != '.py':
                continue
            if folder == 'scripts' and path.suffix not in {'.bat', '.ps1'}:
                continue
            if folder == 'prompts' and path.suffix not in {'.md', '.txt', '.json'}:
                continue
            if frontend_inputs and folder == 'web/ui':
                if (set(relative.parts) & {'dist', 'coverage', '__tests__', '__fixtures__'} or
                        path.name == 'vitest.config.ts' or '.test.' in path.name or '.spec.' in path.name):
                    continue
                if path.suffix not in {'.ts', '.tsx', '.js', '.jsx', '.json', '.css', '.html', '.svg', '.png', '.ico'}:
                    continue
            selected.append(path)
    return sorted(selected, key=lambda path: path.relative_to(root).as_posix())


def _fingerprint(root: Path, paths: list[Path]) -> str:
    root = Path(root)
    digest = hashlib.sha256()
    for path in sorted(paths):
        _regular_path(root, path)
        digest.update(path.relative_to(root).as_posix().encode('utf-8') + b'\0')
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def runtime_fingerprint(root: Path) -> str:
    """Hash runtime sources and frontend inputs; generated assets never feed the build ID."""
    return _fingerprint(root, _selected(root, frontend_inputs=True))


def controller_fingerprint(root: Path) -> str:
    """Fixed controller code and its shared dependencies require explicit technical upgrade."""
    paths = [path for path in (root / 'deployment').glob('*.py') if path.name != 'worker.py']
    paths += [root / 'core' / name for name in (
        '__init__.py', 'maintenance.py', 'process_identity.py', 'paid_model.py', 'config.py',
        'runtime_identity.py', 'operator_preferences.py', 'web_access.py') if (root / 'core' / name).is_file()]
    return _fingerprint(root, paths)


def _expose_payload(staging: Path, destination: Path) -> None:
    # Windows scanners can briefly hold freshly copied/extracted directories open.
    # Retry only this local rename; never replace an existing version or repeat business work.
    deadline = time.monotonic() + 3
    while True:
        if destination.exists():
            raise FileExistsError('Release destination already exists')
        try:
            staging.rename(destination)
            return
        except OSError as exc:
            if (os.name != 'nt' or getattr(exc, 'winerror', None) not in {5, 32, 33}
                    or time.monotonic() >= deadline):
                raise
            time.sleep(.1)


def _manifest(root: Path, expected_sha: str | None) -> dict:
    path = root / 'release.json'
    _regular_path(root, path)
    try:
        manifest = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError('Invalid release manifest') from exc
    if not isinstance(manifest, dict) or any(type(manifest.get(k)) is not type(v) or manifest.get(k) != v
                                             for k, v in COMPATIBILITY.items()):
        raise ValueError('Unsupported release compatibility')
    if manifest.get('repository') != REPOSITORY:
        raise ValueError('Unexpected release repository')
    if not isinstance(manifest.get('sha'), str) or not re.fullmatch('[a-f0-9]{40}', manifest['sha']):
        raise ValueError('Release SHA must be full lowercase hex')
    if expected_sha is not None and manifest['sha'] != expected_sha:
        raise ValueError('Release SHA does not match expected commit')
    if not isinstance(manifest.get('runtime_id'), str) or not re.fullmatch('[a-f0-9]{64}', manifest['runtime_id']):
        raise ValueError('Invalid runtime identity')
    if not isinstance(manifest.get('controller_id'), str) or not re.fullmatch('[a-f0-9]{64}', manifest['controller_id']):
        raise ValueError('Invalid controller identity')
    if any(type(manifest.get(key)) is not int or manifest[key] < 1 for key in ('run_id', 'run_attempt')):
        raise ValueError('Invalid workflow run identity')
    if not isinstance(manifest.get('files'), list) or not manifest['files']:
        raise ValueError('Empty release payload')
    names = set()
    for entry in manifest['files']:
        if not isinstance(entry, dict) or set(entry) != {'path', 'size', 'sha256'}:
            raise ValueError('Invalid release file record')
        name = _safe_name(entry['path'])
        if name.casefold() in names or name.casefold() == 'release.json':
            raise ValueError('Duplicate release path')
        names.add(name.casefold())
        if (type(entry['size']) is not int or entry['size'] < 0 or not isinstance(entry['sha256'], str)
                or not re.fullmatch('[a-f0-9]{64}', entry['sha256'])):
            raise ValueError('Invalid file size or hash')
    _require_payload({entry['path'] for entry in manifest['files']}, wheels=True)
    return manifest


def verify_release(root: Path, *, expected_sha: str | None = None) -> dict:
    """Verify every packaged byte, allowing only local configuration and Python generated files."""
    root = Path(root).absolute()
    manifest = _manifest(root, expected_sha)
    names = {entry['path'] for entry in manifest['files']}
    for entry in manifest['files']:
        path = root / entry['path']
        _regular_path(root, path)
        if not path.is_file() or _file_entry(path, entry['path']) != entry:
            raise ValueError('Release payload hash or size mismatch: ' + entry['path'])
    frontend = json.loads((root / 'web/ui/dist/runtime.json').read_text(encoding='utf-8'))
    if not isinstance(frontend, dict) or frontend.get('runtime_id') != manifest['runtime_id']:
        raise ValueError('Frontend build does not match runtime sources')
    if controller_fingerprint(root) != manifest['controller_id']:
        raise ValueError('Controller payload does not match declared identity')
    for path in root.rglob('*'):
        relative = path.relative_to(root)
        if relative.parts[0] == '.venv' or '__pycache__' in relative.parts or relative.as_posix() == 'config.local.toml':
            continue
        _regular_path(root, path)
        if path.is_file() and relative.as_posix() not in names | {'release.json'}:
            raise ValueError('Unlisted release payload: ' + relative.as_posix())
    return manifest


def build_release(root: Path, output: Path, *, sha: str, repository: str, run_id: int,
                  run_attempt: int = 1, wheelhouse: Path) -> dict:
    """Create an upload-artifact directory; failed builds never publish a manifest."""
    root, output, wheelhouse = Path(root).absolute(), Path(output).absolute(), Path(wheelhouse).absolute()
    if output.exists():
        raise ValueError('Release output already exists')
    selected = _selected(root, frontend_inputs=False)
    selected_names = {path.relative_to(root).as_posix() for path in selected}
    _require_payload(selected_names, wheels=False)
    wheels = sorted(wheelhouse.glob('*.whl'))
    if not wheels:
        raise ValueError('Missing dependency wheelhouse')
    manifest = dict(COMPATIBILITY, sha=sha, repository=repository, run_id=run_id,
                    run_attempt=run_attempt, runtime_id=runtime_fingerprint(root),
                    controller_id=controller_fingerprint(root), files=[])
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.release-build-', dir=output.parent) as temporary:
        staging = Path(temporary) / 'payload'
        staging.mkdir()
        for source in selected + wheels:
            source_root = wheelhouse if source in wheels else root
            _regular_path(source_root, source)
            name = 'wheelhouse/' + source.name if source in wheels else source.relative_to(root).as_posix()
            _safe_name(name)
            destination = staging / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            manifest['files'].append(_file_entry(destination, name))
        manifest['files'].sort(key=lambda item: item['path'])
        (staging / 'release.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8', newline='')
        verify_release(staging, expected_sha=sha)
        _expose_payload(staging, output)
    return manifest


def extract_release(archive: Path, destination: Path, *, expected_sha: str | None = None) -> dict:
    """Validate a GitHub artifact ZIP before exposing an installed version directory."""
    destination = Path(destination).absolute()
    if destination.exists():
        raise ValueError('Release destination already exists')
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive) as zipped, tempfile.TemporaryDirectory(
                prefix='.release-extract-', dir=destination.parent) as temporary:
            staging = Path(temporary) / 'payload'
            staging.mkdir()
            names = set()
            files = set()
            entries = zipped.infolist()
            for entry in entries:
                name = _safe_name(entry.filename.removesuffix('/') if entry.is_dir() else entry.filename)
                if name.casefold() in names:
                    raise ValueError('Duplicate or case-colliding archive entry')
                names.add(name.casefold())
                mode = entry.external_attr >> 16
                if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise ValueError('Archive contains a link or special file')
                if entry.external_attr & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    raise ValueError('Archive contains a Windows reparse point')
                if not entry.is_dir():
                    files.add(name)
            for entry in entries:
                target = staging / entry.filename
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zipped.open(entry) as source, target.open('xb') as output:
                        shutil.copyfileobj(source, output)
            manifest = verify_release(staging, expected_sha=expected_sha)
            if files != {item['path'] for item in manifest['files']} | {'release.json'}:
                raise ValueError('Archive contains unlisted generated files')
            _expose_payload(staging, destination)
            return manifest
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise ValueError('Invalid release archive') from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    fingerprint = commands.add_parser('fingerprint')
    fingerprint.add_argument('--root', type=Path, default=Path.cwd())
    build = commands.add_parser('build')
    build.add_argument('--root', type=Path, default=Path.cwd())
    build.add_argument('--output', type=Path, required=True)
    build.add_argument('--sha', required=True)
    build.add_argument('--repository', default=REPOSITORY)
    build.add_argument('--run-id', type=int, required=True)
    build.add_argument('--run-attempt', type=int, default=1)
    build.add_argument('--wheelhouse', type=Path, required=True)
    verify = commands.add_parser('verify')
    verify.add_argument('root', type=Path)
    verify.add_argument('--expected-sha')
    args = parser.parse_args(argv)
    if args.command == 'fingerprint':
        print(runtime_fingerprint(args.root))
    elif args.command == 'build':
        print(json.dumps(build_release(args.root, args.output, sha=args.sha, repository=args.repository,
                                       run_id=args.run_id, run_attempt=args.run_attempt, wheelhouse=args.wheelhouse)))
    else:
        print(json.dumps(verify_release(args.root, expected_sha=args.expected_sha)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
