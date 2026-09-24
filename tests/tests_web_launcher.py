"""Run the actual Windows launcher against isolated npm/Python boundaries."""
from __future__ import annotations

import os
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'nt', 'Windows batch entry point')
class WebLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='web-launch-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'source checkout'
        self.scripts = self.root / 'scripts'
        self.ui = self.root / 'web' / 'ui'
        self.bin = Path(self.temp.name) / 'bin'
        for directory in (self.scripts, self.ui / 'dist', self.bin):
            directory.mkdir(parents=True)
        self.launcher = self.scripts / 'run_web.bat'
        self.launcher.write_bytes((ROOT / 'scripts' / 'run_web.bat').read_bytes())
        self.events = self.root / 'events.txt'
        self.source = self.ui / 'source.txt'
        self.bundle = self.ui / 'dist' / 'bundle.txt'
        self.source.write_text('fresh-css', encoding='ascii')
        self.bundle.write_text('stale-css', encoding='ascii')
        self.write_bat(self.bin / 'npm.cmd', r'''@echo off
if "%~3"=="ci" (
  echo install>>"%LAUNCH_EVENTS%"
  if defined FAIL_INSTALL exit /b 17
  exit /b 0
)
if "%~3 %~4"=="run build" (
  echo build>>"%LAUNCH_EVENTS%"
  if defined FAIL_BUILD exit /b 19
  copy /y "%~2\source.txt" "%~2\dist\bundle.txt" >nul
  exit /b 0
)
exit /b 23
''')
        self.write_bat(self.scripts / 'run_python.bat', r'''@echo off
echo serve>>"%LAUNCH_EVENTS%"
type "%~dp0..\web\ui\dist\bundle.txt"
echo.
echo arguments: %*
exit /b 0
''')
        system32 = Path(os.environ['SystemRoot']) / 'System32'
        self.environment = dict(os.environ, PATH=str(self.bin) + os.pathsep + str(system32),
                                LAUNCH_EVENTS=str(self.events), FBSCRAPER_CONTROL_DIR='', FBSCRAPER_NETWORK_CONFIG='')

    @staticmethod
    def write_bat(path, content):
        path.write_bytes(content.replace('\n', '\r\n').encode('ascii'))

    def run_launcher(self, **environment):
        return subprocess.run([os.environ['COMSPEC'], '/d', '/c', str(self.launcher),
                               '--port', '18765'], cwd=self.temp.name,
                              env={**self.environment, **environment}, capture_output=True,
                              text=True, encoding='utf-8', errors='replace', timeout=15)

    def recorded(self):
        return self.events.read_text(encoding='ascii').splitlines() if self.events.exists() else []

    def test_source_restart_rebuilds_old_assets_before_serving(self):
        for content in ('fresh-css', 'next-pull-css'):
            self.source.write_text(content, encoding='ascii')
            result = self.run_launcher()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(content, result.stdout)
            self.assertEqual(self.bundle.read_text(encoding='ascii'), content)
            self.assertIn('-m uvicorn web.api.app:app --host 127.0.0.1 --port 8765 --port 18765', result.stdout)
        self.assertEqual(self.recorded(), ['install', 'build', 'serve'] * 2)

    def test_dependency_failure_never_serves_stale_assets(self):
        result = self.run_launcher(FAIL_INSTALL='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.recorded(), ['install'])
        self.assertEqual(self.bundle.read_text(encoding='ascii'), 'stale-css')

    def test_build_failure_never_starts_python(self):
        result = self.run_launcher(FAIL_BUILD='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.recorded(), ['install', 'build'])

    def test_release_package_uses_its_prebuilt_assets_without_node(self):
        (self.root / 'release.json').write_text('{}', encoding='ascii')
        result = self.run_launcher(PATH=str(Path(os.environ['SystemRoot']) / 'System32'))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.recorded(), ['serve'])
        self.assertIn('stale-css', result.stdout)

    def test_source_without_npm_reports_requirement_instead_of_serving_old_ui(self):
        result = self.run_launcher(PATH=str(Path(os.environ['SystemRoot']) / 'System32'))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Node.js', result.stdout + result.stderr)
        self.assertEqual(self.recorded(), [])

    def prepare_lan(self):
        for directory in ('tools', 'core', 'ops'):
            (self.root / directory).mkdir()
        for name in ('tools/source_web.py', 'core/console.py', 'core/web_access.py', 'scripts/run_web_lan.bat'):
            shutil.copyfile(ROOT / name, self.root / name)
        self.network = self.root / 'ops/service-machine.network.json'
        self.network.write_text(json.dumps({'web_host': '0.0.0.0', 'web_port': 9876,
            'public_base_url': 'http://10.66.6.3:9876', 'allowed_client_cidrs': ['10.66.6.0/24']}), encoding='ascii')
        self.write_bat(self.scripts / 'run_python.bat', r'''@echo off
if "%~2"=="tools.source_web" goto lan
echo serve>>"%LAUNCH_EVENTS%"
type "%~dp0..\web\ui\dist\bundle.txt"
echo arguments: %*
echo policy: %FBSCRAPER_NETWORK_CONFIG%
exit /b 0
:lan
cd /d "%~dp0.."
"%LAUNCH_PYTHON%" %*
exit /b %ERRORLEVEL%
''')
        self.environment.update(LAUNCH_PYTHON=sys.executable, PYTHONPATH=str(self.root), PYTHONIOENCODING='utf-8')
        self.launcher = self.scripts / 'run_web_lan.bat'

    def run_lan(self, **environment):
        return subprocess.run([os.environ['COMSPEC'], '/d', '/c', str(self.launcher)], cwd=self.temp.name,
                              env={**self.environment, **environment}, capture_output=True,
                              text=True, encoding='utf-8', errors='replace', timeout=15)

    def test_lan_reads_binding_and_policy_from_json_and_reuses_frontend_build(self):
        self.prepare_lan()
        result = self.run_lan()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.recorded(), ['install', 'build', 'serve'])
        self.assertIn('fresh-css', result.stdout)
        self.assertIn('--host 0.0.0.0 --port 9876 --no-proxy-headers', result.stdout)
        self.assertIn(str(self.network), result.stdout)

    def test_lan_invalid_policy_or_managed_terminal_stops_before_build(self):
        self.prepare_lan()
        result = self.run_lan(FBSCRAPER_CONTROL_DIR='installed/control')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.recorded(), [])
        self.network.write_text('{broken', encoding='ascii')
        result = self.run_lan()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.recorded(), [])

    def test_lan_propagates_frontend_failure(self):
        self.prepare_lan()
        result = self.run_lan(FAIL_BUILD='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.recorded(), ['install', 'build'])


if __name__ == '__main__':
    unittest.main()
