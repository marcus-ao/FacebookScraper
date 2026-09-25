"""Keep the original failure visible when shutdown errors fill a child test log."""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import test_offline


class OfflineRunnerTests(unittest.TestCase):
    def test_failed_child_reports_original_error_and_cleanup_tail_with_full_log(self):
        original = "BrowserType.launch: Executable doesn't exist at fixture-browser.exe"
        tail = 'RuntimeError: Event loop is closed'
        output = original + '\n' + 'cleanup pipe error\n' * 700 + tail + '\n'
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / 'tests').mkdir()
            (root / 'tests' / 'tests_failure.py').write_text(
                'import sys\nprint(' + repr(output) + ', end="")\nsys.exit(1)\n', encoding='utf-8')
            console = io.StringIO()
            with patch.object(test_offline, 'ROOT', root), redirect_stdout(console):
                code = test_offline.main(['--only', 'tests_failure'])
            self.assertEqual(code, 1)
            evidence = next((root / 'state').iterdir())
            self.assertEqual((evidence / 'tests_failure.log').read_text('utf-8'), output)
            self.assertEqual(json.loads((evidence / 'results.json').read_text('utf-8'))[0]['exit_code'], 1)
            self.assertIn(original, console.getvalue())
            self.assertIn(tail, console.getvalue())
            self.assertIn(str(evidence / 'tests_failure.log'), console.getvalue())
            self.assertLess(len(console.getvalue()), len(output))


if __name__ == '__main__':
    unittest.main()
