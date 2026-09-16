"""Release contracts use tiny isolated packages, never business data."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deployment.release import build_release, controller_fingerprint, extract_release, runtime_fingerprint, verify_release


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'source'
        self.root.mkdir()

    def put(self, path, data=b'content\n'):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def build(self):
        for folder in ('core', 'routes', 'localize', 'publish', 'pipeline', 'web/api', 'tools', 'deployment'):
            self.put(folder + '/__init__.py')
        for path in ('config.toml', 'requirements.txt', 'requirements.lock', '.env.example',
                     'prompts/translate.md', 'web/ui/dist/index.html', 'scripts/run_web.bat'):
            self.put(path, b'package\r\n' if path.endswith('.bat') else b'package\n')
        self.wheels = Path(self.temp.name) / 'wheels'
        self.wheels.mkdir(exist_ok=True)
        (self.wheels / 'example-1-py3-none-any.whl').write_bytes(b'wheel')
        self.output = Path(self.temp.name) / 'release'
        self.put('web/ui/dist/runtime.json', json.dumps({'runtime_id': runtime_fingerprint(self.root)}).encode())
        return build_release(self.root, self.output, sha='a' * 40,
                             repository='marcus-ao/FacebookScraper', run_id=123,
                             wheelhouse=self.wheels)

    def test_package_copies_allowlist_byte_for_byte_and_verifies_installed_directory(self):
        for path in ('.env', 'config.local.toml', 'state/paid_requests.jsonl', 'archive/photo.jpg',
                     'core/.env', 'core/secret.json', 'web/ui/node_modules/private.js'):
            self.put(path, b'private')
        manifest = self.build()
        self.assertEqual(verify_release(self.output, expected_sha='a' * 40), manifest)
        self.assertEqual(manifest['runtime_id'], runtime_fingerprint(self.root))
        self.assertEqual(manifest['run_id'], 123)
        self.assertEqual(manifest['workflow'], 'release.yml')
        names = {entry['path'] for entry in manifest['files']}
        self.assertIn('requirements.lock', names)
        self.assertIn('wheelhouse/example-1-py3-none-any.whl', names)
        self.assertNotIn('release.json', names)
        self.assertFalse(any('private' in p.read_text(errors='ignore') for p in self.output.rglob('*') if p.is_file()))
        for name in names - {'wheelhouse/example-1-py3-none-any.whl'}:
            self.assertEqual((self.output / name).read_bytes(), (self.root / name).read_bytes())
        (self.output / 'config.local.toml').write_bytes(b'generated')
        (self.output / '.venv').mkdir()
        (self.output / '.venv/python.exe').write_bytes(b'generated')
        (self.output / 'core/__pycache__').mkdir()
        (self.output / 'core/__pycache__/cache.pyc').write_bytes(b'generated')
        self.assertEqual(verify_release(self.output), manifest)
        (self.output / 'core/__init__.py').write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            verify_release(self.output)

    def test_extract_complete_artifact_and_reject_unsafe_or_extra_files(self):
        manifest = self.build()
        archive = Path(self.temp.name) / 'artifact.zip'
        destination = Path(self.temp.name) / 'installed'

        def write_archive(extra=None, link=False):
            with zipfile.ZipFile(archive, 'w') as zipped:
                for path in self.output.rglob('*'):
                    if path.is_file():
                        zipped.write(path, path.relative_to(self.output).as_posix())
                if extra:
                    info = zipfile.ZipInfo(extra)
                    if link:
                        info.create_system = 3
                        info.external_attr = 0o120777 << 16
                    zipped.writestr(info, b'bad')

        write_archive()
        self.assertEqual(extract_release(archive, destination, expected_sha='a' * 40), manifest)
        with self.assertRaises(ValueError):
            extract_release(archive, destination)
        for extra, link in (('../escape', False), ('/absolute', False), ('C:/drive', False),
                            ('core\\escape.py', False), ('Core/__INIT__.py', False),
                            ('config.local.toml', False), ('.venv/malware.py', False),
                            ('extra.py', False), ('symlink', True), ('aux.txt', False)):
            with self.subTest(extra=extra):
                write_archive(extra, link)
                target = Path(self.temp.name) / 'rejected'
                with self.assertRaises(ValueError):
                    extract_release(archive, target)
                self.assertFalse(target.exists())

    def test_reject_wrong_sha_compatibility_traversal_and_extra_payload(self):
        manifest = self.build()
        with self.assertRaises(ValueError):
            verify_release(self.output, expected_sha='b' * 40)
        for key, value in (('protocol', 2), ('truth_contract', 2), ('python', '3.13.0'),
                           ('platform', 'linux'), ('run_id', True), ('repository', 'other/repo'),
                           ('sha', int('1' * 40)), ('runtime_id', int('1' * 64))):
            changed = dict(manifest, **{key: value})
            (self.output / 'release.json').write_text(json.dumps(changed))
            with self.assertRaises(ValueError, msg=key):
                verify_release(self.output)
        changed = dict(manifest, files=[dict(manifest['files'][0], path='../outside')])
        (self.output / 'release.json').write_text(json.dumps(changed))
        with self.assertRaises(ValueError):
            verify_release(self.output)
        (self.output / 'release.json').write_text(json.dumps(manifest))
        (self.output / 'unexpected.py').write_bytes(b'extra')
        with self.assertRaises(ValueError):
            verify_release(self.output)

    def test_failed_build_has_no_final_manifest(self):
        self.build()
        (self.root / 'web/ui/dist/index.html').unlink()
        target = Path(self.temp.name) / 'incomplete'
        with self.assertRaises(ValueError):
            build_release(self.root, target, sha='a' * 40,
                          repository='marcus-ao/FacebookScraper', run_id=1, wheelhouse=self.wheels)
        self.assertFalse(target.exists())

    def test_stale_frontend_build_cannot_be_packaged_for_new_backend(self):
        self.build()
        self.put('core/__init__.py', b'new backend')
        target = Path(self.temp.name) / 'stale-ui'
        with self.assertRaises(ValueError):
            build_release(self.root, target, sha='b' * 40, repository='marcus-ao/FacebookScraper',
                          run_id=124, wheelhouse=self.wheels)
        self.assertFalse(target.exists())

    def test_reject_directory_reparse_escape(self):
        self.build()
        original = self.output / 'core'
        outside = Path(self.temp.name) / 'outside-core'
        original.rename(outside)
        if os.name == 'nt':
            subprocess.run(['cmd', '/c', 'mklink', '/J', str(original), str(outside)],
                           check=True, capture_output=True)
        else:
            original.symlink_to(outside, target_is_directory=True)
        try:
            with self.assertRaises(ValueError):
                verify_release(self.output)
        finally:
            original.rmdir() if os.name == 'nt' else original.unlink()

    def test_missing_and_corrupt_artifact_payload_never_becomes_installable(self):
        self.build()
        archive = Path(self.temp.name) / 'artifact.zip'
        destination = Path(self.temp.name) / 'installed'
        for kind in ('missing', 'corrupt'):
            with zipfile.ZipFile(archive, 'w') as zipped:
                for path in self.output.rglob('*'):
                    if not path.is_file():
                        continue
                    name = path.relative_to(self.output).as_posix()
                    if name == 'core/__init__.py':
                        if kind == 'corrupt':
                            zipped.writestr(name, b'corrupt')
                    else:
                        zipped.write(path, name)
            with self.assertRaises(ValueError):
                extract_release(archive, destination)
            self.assertFalse(destination.exists())

    def test_manifest_cannot_omit_required_runtime_inputs(self):
        manifest = self.build()
        manifest['files'] = [entry for entry in manifest['files'] if entry['path'] != 'requirements.lock']
        (self.output / 'requirements.lock').unlink()
        (self.output / 'release.json').write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            verify_release(self.output)

    def test_fingerprint_tracks_runtime_inputs_but_not_docs_secrets_or_outputs(self):
        for path in ('core/__init__.py', 'config.toml', 'prompts/translate.md',
                     'requirements.lock', 'web/ui/src/App.tsx', 'web/ui/package-lock.json'):
            self.put(path)
        before = runtime_fingerprint(self.root)
        self.assertRegex(before, r'^[a-f0-9]{64}$')
        for path in ('docs/HANDOFF.md', 'tests/tests_example.py', '.env', 'config.local.toml',
                     'state/paid_requests.jsonl', 'web/ui/dist/app.js', 'web/ui/node_modules/a.js',
                     'core/__pycache__/cached.pyc', 'web/ui/src/App.test.tsx',
                     'web/ui/src/types/__fixtures__/detail.json',
                     'web/ui/vitest.config.ts', 'web/ui/coverage/report.json'):
            self.put(path, b'ignored')
        self.assertEqual(runtime_fingerprint(self.root), before)
        for path in ('config.toml', 'prompts/translate.md', 'web/ui/src/App.tsx', 'requirements.lock'):
            target = self.root / path
            original = target.read_bytes()
            target.write_bytes(b'changed')
            self.assertNotEqual(runtime_fingerprint(self.root), before, path)
            target.write_bytes(original)

    def test_controller_fingerprint_distinguishes_runtime_and_controller_upgrades(self):
        self.put('deployment/controller.py', b'controller')
        self.put('core/maintenance.py', b'protocol')
        before = controller_fingerprint(self.root)
        self.put('pipeline/engine.py', b'business change')
        self.put('config.toml', b'policy change')
        self.put('deployment/worker.py', b'application worker change')
        self.assertEqual(controller_fingerprint(self.root), before)
        self.put('core/maintenance.py', b'controller protocol change')
        self.assertNotEqual(controller_fingerprint(self.root), before)

    @unittest.skipUnless(os.name == 'nt', 'Windows sharing semantics')
    def test_transient_windows_rename_handles_publish_verified_payload_only_once(self):
        rename = Path.rename
        calls = []
        def transient(path, target):
            calls.append((path, target))
            if len(calls) == 1:
                error = PermissionError('Simulated Windows scanner handle')
                error.winerror = 5
                raise error
            return rename(path, target)
        with patch.object(Path, 'rename', transient):
            manifest = self.build()
        self.assertEqual(len(calls), 2)
        self.assertEqual(verify_release(self.output), manifest)
        archive = Path(self.temp.name) / 'artifact.zip'
        with zipfile.ZipFile(archive, 'w') as zipped:
            for path in self.output.rglob('*'):
                if path.is_file():
                    zipped.write(path, path.relative_to(self.output).as_posix())
        calls.clear()
        destination = Path(self.temp.name) / 'installed'
        with patch.object(Path, 'rename', transient):
            self.assertEqual(extract_release(archive, destination), manifest)
        self.assertEqual(len(calls), 2)


if __name__ == '__main__':
    unittest.main()
