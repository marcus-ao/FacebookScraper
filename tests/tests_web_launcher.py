"""Run the actual Windows launcher against isolated npm/Python boundaries."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
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
                                LAUNCH_EVENTS=str(self.events))

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


if __name__ == '__main__':
    unittest.main()
