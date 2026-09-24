"""One Ctrl+C stops a foreground child without a traceback or a second key."""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

COOPERATIVE = textwrap.dedent("""
    import os, sys, time
    from pathlib import Path
    Path(os.environ['QUIET_READY']).write_text('ready', encoding='ascii')
    try:
        time.sleep(30)
    except KeyboardInterrupt:
        Path(os.environ['QUIET_DONE']).write_text('done', encoding='ascii')
        sys.exit(0)
""")

SLOW_GRACEFUL = textwrap.dedent("""
    import os, time
    from pathlib import Path
    Path(os.environ['QUIET_READY']).write_text('ready', encoding='ascii')
    try:
        time.sleep(30)
    except KeyboardInterrupt:
        time.sleep(9)
        Path(os.environ['QUIET_DONE']).write_text('done', encoding='ascii')
        print('cleanup-complete', flush=True)
""")


def _ctrl_break_off():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.SetConsoleCtrlHandler(None, True)
    return kernel


def send_ctrl_c(kernel) -> None:
    if not kernel.GenerateConsoleCtrlEvent(0, 0):
        raise ctypes.WinError(ctypes.get_last_error())


def run_captured(argv, *, env) -> dict:
    """Run argv on this console, send one Ctrl+C, and return its captured text."""
    import time

    with tempfile.TemporaryDirectory(prefix="quiet-stop-") as raw:
        folder = Path(raw)
        ready = folder / "ready.txt"
        done = folder / "done.txt"
        out_path, err_path = folder / "out.txt", folder / "err.txt"
        with out_path.open("w", encoding="utf-8") as out_handle, \
             err_path.open("w", encoding="utf-8") as err_handle:
            process = subprocess.Popen(argv, cwd=ROOT,
                                       env=dict(env, QUIET_READY=str(ready), QUIET_DONE=str(done)),
                                       stdout=out_handle, stderr=err_handle)
            deadline = time.monotonic() + 12
            while not ready.exists():
                if process.poll() is not None:
                    raise AssertionError(f"launcher exited before child was ready: {process.returncode}")
                if time.monotonic() >= deadline:
                    raise TimeoutError("child did not become ready")
                time.sleep(0.05)
            # Ignoring Ctrl+C is inherited. Do it only after the child is already running.
            kernel = _ctrl_break_off()
            send_ctrl_c(kernel)
            code = process.wait(timeout=25)
        return {
            "code": code,
            "done": done.exists(),
            "out": out_path.read_text(encoding="utf-8", errors="replace"),
            "err": err_path.read_text(encoding="utf-8", errors="replace"),
        }


def run_isolated_web_launcher(*, lan: bool, python: str, env: dict,
                              real_uvicorn: bool = False) -> dict:
    env = dict(env)
    env.pop("FBSCRAPER_RUNTIME_CONFIG", None)
    with tempfile.TemporaryDirectory(prefix="quiet-web-") as raw:
        root = Path(raw) / "source checkout"
        for directory in ("scripts", "tools", "core", "ops", "web/ui"):
            (root / directory).mkdir(parents=True)
        for name in ("scripts/run_python.bat", "scripts/run_web.bat", "scripts/run_web_lan.bat",
                     "tools/runtime.py", "tools/source_web.py", "core/console.py", "core/web_access.py"):
            shutil.copyfile(ROOT / name, root / name)
        (root / "core/__init__.py").write_text("", encoding="ascii")
        (root / "tools/__init__.py").write_text("", encoding="ascii")
        (root / "config.local.toml").write_text("[runtime]\npython = " + json.dumps(python) + "\n",
                                               encoding="utf-8", newline="\n")
        if real_uvicorn:
            (root / "web/api").mkdir()
            (root / "web/__init__.py").write_text("", encoding="ascii")
            (root / "web/api/__init__.py").write_text("", encoding="ascii")
            (root / "web/api/app.py").write_text(textwrap.dedent("""
                import os
                from pathlib import Path

                async def app(scope, receive, send):
                    if scope['type'] != 'lifespan':
                        return
                    while True:
                        message = await receive()
                        if message['type'] == 'lifespan.startup':
                            Path(os.environ['QUIET_READY']).write_text('ready', encoding='ascii')
                            await send({'type': 'lifespan.startup.complete'})
                        elif message['type'] == 'lifespan.shutdown':
                            Path(os.environ['QUIET_DONE']).write_text('done', encoding='ascii')
                            await send({'type': 'lifespan.shutdown.complete'})
                            return
            """), encoding="utf-8", newline="\n")
        else:
            (root / "uvicorn.py").write_text(COOPERATIVE, encoding="utf-8", newline="\n")
        if lan:
            network = {"web_host": "0.0.0.0", "web_port": 9876,
                       "public_base_url": "http://10.66.6.3:9876",
                       "allowed_client_cidrs": ["10.66.6.0/24"]}
            (root / "ops/service-machine.network.json").write_text(json.dumps(network), encoding="ascii")
            fake_bin = root / "bin"
            fake_bin.mkdir()
            (fake_bin / "npm.cmd").write_bytes(b"@echo off\r\nexit /b 0\r\n")
            env = dict(env, PATH=str(fake_bin) + os.pathsep + env["PATH"])
        else:
            (root / "release.json").write_text("{}", encoding="ascii")
        launcher = root / "scripts" / ("run_web_lan.bat" if lan else "run_web.bat")
        argv = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(launcher)]
        if real_uvicorn:
            argv.extend(["--port", "0"])
        return run_captured(argv, env=env)


def probe(kind: str) -> int:
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
    env.pop("FBSCRAPER_STOP_NOTICE", None)
    python = sys.executable
    if kind == "exit-code":
        completed = subprocess.run(
            [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c",
             str(ROOT / "scripts" / "run_python.bat"), "-c", "import sys; sys.exit(3)"],
            cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
        print(json.dumps({"code": completed.returncode, "out": completed.stdout, "err": completed.stderr},
                         ensure_ascii=True))
        return 0
    if kind == "literal-bang":
        completed = subprocess.run(
            [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c",
             str(ROOT / "scripts" / "run_python.bat"), "-c", "import sys; print(sys.argv[1])", "a!b"],
            cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
        print(json.dumps({"code": completed.returncode, "out": completed.stdout, "err": completed.stderr},
                         ensure_ascii=True))
        return 0
    if kind == "cooperative":
        child = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(ROOT)!r})
            from core.console import run_foreground
            raise SystemExit(run_foreground([{python!r}, "-c", {COOPERATIVE!r}]))
        """)
        result = run_captured([python, "-c", child], env=env)
    elif kind == "slow-graceful":
        child = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(ROOT)!r})
            from core.console import run_foreground
            raise SystemExit(run_foreground([{python!r}, "-c", {SLOW_GRACEFUL!r}]))
        """)
        result = run_captured([python, "-c", child], env=env)
    elif kind == "batch":
        script = Path(tempfile.mkdtemp(prefix="quiet-batch-")) / "sleep_once.py"
        script.write_text(COOPERATIVE, encoding="utf-8", newline="\n")
        result = run_captured(
            [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c",
            str(ROOT / "scripts" / "run_python.bat"), str(script)],
            env=env)
    elif kind in {"web", "web-lan"}:
        result = run_isolated_web_launcher(lan=kind == "web-lan", python=python, env=env)
    elif kind == "web-uvicorn":
        result = run_isolated_web_launcher(lan=False, python=python, env=env, real_uvicorn=True)
    else:
        raise SystemExit("unknown probe " + kind)
    print(json.dumps(result, ensure_ascii=True))
    return 0


@unittest.skipUnless(os.name == "nt", "Windows console Ctrl+C")
class InterruptQuietTests(unittest.TestCase):
    def probe(self, kind: str) -> dict:
        # The probe owns a console so GenerateConsoleCtrlEvent does not hit this runner.
        completed = subprocess.run(
            [sys.executable, str(Path(__file__)), "--probe", kind],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=40, creationflags=subprocess.CREATE_NEW_CONSOLE)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        return json.loads(completed.stdout)

    def assert_stopped_once(self, result: dict, *, code: int | None = 0) -> None:
        text = result["out"] + result["err"]
        self.assertNotIn("Traceback", text)
        self.assertNotIn("Terminate batch job", text)
        self.assertEqual(text.count("已停止。"), 1)
        self.assertTrue(result["done"], result)
        if code is not None:
            self.assertEqual(result["code"], code)

    def test_one_ctrl_c_stops_a_cooperative_child(self):
        self.assert_stopped_once(self.probe("cooperative"))

    def test_batch_launcher_stops_on_one_ctrl_c_without_a_prompt(self):
        self.assert_stopped_once(self.probe("batch"))

    def test_source_web_launcher_stops_on_one_ctrl_c(self):
        self.assert_stopped_once(self.probe("web"))

    def test_real_uvicorn_shuts_down_through_the_source_launcher(self):
        self.assert_stopped_once(self.probe("web-uvicorn"))

    def test_source_lan_web_launcher_stops_on_one_ctrl_c(self):
        self.assert_stopped_once(self.probe("web-lan"))

    def test_batch_launcher_keeps_the_child_exit_code(self):
        self.assertEqual(self.probe("exit-code")["code"], 3)

    def test_batch_launcher_preserves_literal_exclamation_mark(self):
        result = self.probe("literal-bang")
        self.assertEqual(result["code"], 0, result)
        self.assertIn("a!b", result["out"])

    def test_ctrl_c_waits_for_graceful_cleanup_beyond_eight_seconds(self):
        result = self.probe("slow-graceful")
        self.assert_stopped_once(result)
        self.assertIn("cleanup-complete", result["out"])

class DeploymentInterruptTests(unittest.TestCase):
    def test_direct_controller_interrupt_reports_a_short_stop_line(self):
        from deployment import cli

        with patch.object(cli, "main", side_effect=KeyboardInterrupt), \
             patch.dict(os.environ, {}, clear=False), \
             redirect_stderr(StringIO()) as err:
            os.environ.pop("FBSCRAPER_STOP_NOTICE", None)
            self.assertEqual(cli.entrypoint(), 130)
        self.assertEqual(err.getvalue().strip().splitlines(), ["已停止。"])


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--probe":
        raise SystemExit(probe(sys.argv[2]))
    unittest.main()
