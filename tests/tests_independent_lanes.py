"""平台独立消费：相同内容也各自处理，冻结账号不进入自动工作。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import engine as engine
from publish import workflow

NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)


def source(platform, account, post_id):
    return engine.SourcePost(
        platform, Path(account),
        {"post_id": post_id, "platform": platform, "account": account,
         "text": "The same source caption", "media": []},
        NOW - timedelta(minutes=5), f"{platform}:{post_id}")


class IndependentLaneTests(unittest.TestCase):
    def test_identical_posts_never_merge_or_request_selection(self):
        posts = [source("facebook", "neakasaofficial", "f1"),
                 source("instagram", "neakasa.global", "i1")]
        result = engine.reconcile(posts)
        self.assertEqual(len(result.candidates), 2)
        self.assertEqual(result.human_items, ())
        self.assertTrue(all(c.relation == "independent" and len(c.sources) == 1
                            for c in result.candidates))

    def test_legacy_pair_selection_cannot_recombine_platforms(self):
        posts = [source("facebook", "neakasaofficial", "f1"),
                 source("instagram", "neakasa.global", "i1")]
        result = engine.reconcile(posts, selected={"old-pair": "facebook:f1"},
                                  account_pairs={("neakasaofficial", "neakasa.global")})
        self.assertEqual(len(result.candidates), 2)
        self.assertEqual(result.human_items, ())

    def test_only_active_accounts_enter_automatic_source_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folders = [root / name for name in (
                "fa_neakasaofficial", "in_neakasa.global", "in_neakasa.tech")]
            with patch.object(engine.cfg(), "active_accounts", return_value=(
                    "fa_neakasaofficial", "in_neakasa.global")):
                self.assertEqual(engine.active_account_dirs(folders), folders[:2])

    def test_attempt_defaults_to_source_platform(self):
        from types import SimpleNamespace
        post = SimpleNamespace(post_id="f1", platform="facebook", text_de="Hallo",
                               source_text="Hello", original_text_de="Hallo",
                               image_paths=(), image_sources=(), warnings=())
        attempt = workflow.new_attempt(post, NOW, ui_timezone="America/Los_Angeles")
        self.assertEqual(attempt.target_channels, ("facebook",))

    def test_missing_channel_evidence_stops_before_browser_attach(self):
        """替换共享 config._cfg，使所有能力与快照读取均使用隔离状态。"""
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from core import config
        from publish.business_suite import ProbeRequired
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "state").mkdir()
            isolated = config.Config()
            isolated._d["paths"] = {"archive": str(root / "archive"),
                                    "state": str(root / "state")}
            post = SimpleNamespace(post_id="f1", platform="facebook")
            with patch.object(config, "_cfg", isolated), \
                    patch.object(workflow, "attach", new_callable=AsyncMock) as attach:
                with self.assertRaisesRegex(ProbeRequired, "证据"):
                    asyncio.run(workflow.execute(
                        post, NOW, ui_timezone="America/Los_Angeles", timeout=1,
                        stamp="offline", submit_enabled=True))
                attach.assert_not_called()

    def test_new_attempt_cannot_cover_another_source_or_target_channel(self):
        from types import SimpleNamespace
        from publish.business_suite import PublishStepError
        post = SimpleNamespace(post_id='f1', platform='facebook')
        for refs, channels in [(('facebook:f1', 'instagram:i1'), ('facebook',)),
                               (('facebook:f1',), ('instagram',))]:
            with self.assertRaises(PublishStepError):
                workflow.new_attempt(post, NOW, ui_timezone='America/Los_Angeles',
                                     source_refs=refs, target_channels=channels)


if __name__ == "__main__":
    unittest.main()
