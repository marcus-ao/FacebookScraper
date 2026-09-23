"""品牌账号作为合作者放行；来源角色仍只认当前监测目标。隔离目录，不碰真实归档。"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from core import account_roles, operating_settings
from core.config import cfg
from core.index_db import display_account_dirs
from core.integrity import split_suspect_sources
from core.operator_preferences import validate
from pipeline import approval, engine, initial_translation, refinement
from routes import backfill, delta


class BrandCollaboratorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def candidate(self, collaborator: str, *, platform="instagram", account="neakasa.global"):
        prefix = "fa_" if platform == "facebook" else "in_"
        account_dir = self.root / (prefix + account)
        post_id = "collab-" + collaborator.replace(" ", "-").replace(".", "-")
        folder = account_dir / "posts" / ("2026-09-21_" + post_id)
        folder.mkdir(parents=True)
        Image.new("RGB", (64, 64), "white").save(folder / "01.jpg")
        row = {
            "post_id": post_id,
            "platform": platform,
            "account": account,
            "owner": account,
            "coauthors": [account, collaborator],
            "text": "A clean home.",
            "created_at": "2026-09-21T08:00:00Z",
            "media_complete": True,
            "media": [{"kind": "image", "local_path": "posts/2026-09-21_%s/01.jpg" % post_id}],
        }
        source = engine.SourcePost(
            platform, account_dir, row,
            datetime(2026, 9, 21, tzinfo=timezone.utc), platform + ":" + post_id)
        return engine.Candidate(source, (source,), "independent")

    def test_brand_collaborators_pass_and_outsiders_stay_blocked(self):
        rules = engine.publish_rules()
        self.assertIsNone(engine.prepaid_issue(self.candidate("neakasa.tech"), rules))
        self.assertIsNone(engine.prepaid_issue(self.candidate("neakasa.de"), rules))
        self.assertIsNone(engine.prepaid_issue(
            self.candidate("Neakasa Deutschland", platform="facebook", account="neakasaofficial"),
            rules))
        issue = engine.prepaid_issue(self.candidate("neakasa_fans"), rules)
        self.assertEqual(issue.kind, "unknown_collaborator")
        self.assertEqual(issue.details["collaborators"], ["neakasa_fans"])
        self.assertNotIn("neakasa.de", rules.trusted_owners["instagram"])
        self.assertIn("neakasa.de", rules.brand_accounts["instagram"])

    def test_active_accounts_remain_the_monitoring_targets(self):
        self.assertEqual(cfg().active_accounts(), ("fa_neakasaofficial", "in_neakasa.global"))

    def test_frozen_source_directory_is_still_refused(self):
        account = Path("in_neakasa.tech")

        async def approve():
            await approval.approve(
                account, {}, scheduled_at="2026-09-22T10:00:00+08:00",
                source_text_sha256="a" * 64, human_revision=None, review_revision=None,
                content_fingerprint="b" * 64)

        with self.assertRaises(approval.ApprovalConflict) as approval_error:
            asyncio.run(approve())
        self.assertEqual(str(approval_error.exception), "此来源账号已冻结或未配置")
        with self.assertRaises(initial_translation.review.ReviewConflict) as translation_error:
            initial_translation._check(account, {})
        self.assertEqual(str(translation_error.exception), "此来源账号已冻结或未配置")
        with self.assertRaises(refinement.review.ReviewValidationError) as refinement_error:
            refinement.submit(
                account, {}, kind="text", instruction="改一下",
                source_text_sha256="a" * 64, human_revision=None, review_revision=None)
        self.assertIn("已冻结或未配置", str(refinement_error.exception))

    def test_frozen_history_stays_readable(self):
        archive = self.root / "archive"
        for name in ("in_neakasa.tech", "in_neakasa.global"):
            (archive / name / "posts").mkdir(parents=True)
        self.assertNotIn("in_neakasa.tech",
                         {path.name for path in display_account_dirs(archive)})
        self.assertIn("in_neakasa.tech",
                      {path.name for path in display_account_dirs(archive, include_frozen=True)})

    def test_targeting_a_frozen_brand_account_fails_before_the_browser(self):
        config = cfg()
        original = config._d["targets"]["instagram"]
        config._d["targets"]["instagram"] = "neakasa.tech"

        async def attach():
            raise AssertionError("browser attached")

        try:
            with self.assertRaises(engine.PipelineRunError) as caught:
                engine.publish_rules()
            message = str(caught.exception)
            self.assertIn("neakasa.tech", message)
            self.assertIn("frozen_sources", message)
            self.assertIn("brand_accounts", message)
            self.assertIn("instagram", message)
            with patch.object(backfill, "attach", attach):
                self.assertEqual(asyncio.run(backfill.run("instagram")), 1)
            self.assertEqual(delta.main(["--preview"]), 1)
        finally:
            config._d["targets"]["instagram"] = original

    def test_broken_brand_table_stops_paid_processing_and_not_the_sentinel(self):
        config = cfg()
        publish = config._d["publish"]
        saved = publish["brand_accounts"]
        de = {"post_id": "de", "owner": "neakasa.de"}
        tech = {"post_id": "tech", "owner": "neakasa.tech"}
        outsider = {"post_id": "out", "owner": "pets_qtr"}
        try:
            authorized, third_party = split_suspect_sources([de, tech, outsider], "instagram")
            self.assertEqual({row["post_id"] for row in authorized}, {"de", "tech"})
            self.assertEqual({row["post_id"] for row in third_party}, {"out"})
            for broken in (
                    "坏配置",
                    {"instagram": "neakasa.de", "facebook": []},
                    {"instagram": ["neakasa.de", ""], "facebook": ["neakasaofficial"]}):
                publish["brand_accounts"] = broken
                with self.assertRaises(engine.PipelineRunError):
                    engine.publish_rules()
                authorized, third_party = split_suspect_sources([de, tech, outsider], "instagram")
                self.assertIn("tech", {row["post_id"] for row in authorized})
                if broken == "坏配置" or not isinstance(broken.get("instagram") if isinstance(broken, dict) else None, list):
                    self.assertNotIn("de", {row["post_id"] for row in authorized})
                    self.assertIn("de", {row["post_id"] for row in third_party})
        finally:
            publish["brand_accounts"] = saved

    def test_normalization_blank_names_and_unreadable_tables(self):
        rules = engine.publish_rules()
        self.assertIsNone(engine.prepaid_issue(self.candidate("  Neakasa.DE "), rules))
        self.assertIsNone(engine.prepaid_issue(self.candidate(""), rules))
        fans = engine.prepaid_issue(self.candidate(" Neakasa_Fans "), rules)
        self.assertEqual(fans.details["collaborators"], ["neakasa_fans"])

        config = cfg()
        publish = config._d["publish"]
        saved = {
            "brand_accounts": publish["brand_accounts"],
            "frozen_sources": publish["frozen_sources"],
            "instagram": config._d["targets"]["instagram"],
        }
        try:
            publish["frozen_sources"] = {"facebook": [], "instagram": ["  Neakasa.Tech "]}
            config._d["targets"]["instagram"] = "NEAKASA.TECH"
            with self.assertRaises(engine.PipelineRunError) as caught:
                engine.publish_rules()
            self.assertIn("neakasa.tech", str(caught.exception))
            self.assertIn("neakasa.tech", account_roles.frozen_target_message(config))

            publish["brand_accounts"] = saved["brand_accounts"]
            publish["frozen_sources"] = saved["frozen_sources"]
            config._d["targets"]["instagram"] = saved["instagram"]
            del publish["brand_accounts"]
            del publish["frozen_sources"]
            rules = engine.publish_rules()
            self.assertIsNone(engine.prepaid_issue(self.candidate("neakasa.tech"), rules))
            self.assertEqual(
                engine.prepaid_issue(self.candidate("neakasa.de"), rules).kind,
                "unknown_collaborator")
            self.assertIsNone(account_roles.frozen_target_message(config))
            authorized, third_party = split_suspect_sources(
                [{"post_id": "de", "owner": "neakasa.de"},
                 {"post_id": "tech", "owner": "neakasa.tech"}],
                "instagram")
            self.assertEqual([row["post_id"] for row in authorized], ["tech"])
            self.assertEqual([row["post_id"] for row in third_party], ["de"])

            config._d["publish"] = "坏配置"
            self.assertIsNone(account_roles.frozen_target_message(config))
            with self.assertRaises(engine.PipelineRunError):
                engine.publish_rules()
            _, third_party = split_suspect_sources(
                [{"post_id": "tech", "owner": "neakasa.tech"}], "instagram")
            self.assertEqual([row["post_id"] for row in third_party], ["tech"])
        finally:
            config._d["publish"] = publish
            publish["brand_accounts"] = saved["brand_accounts"]
            publish["frozen_sources"] = saved["frozen_sources"]
            config._d["targets"]["instagram"] = saved["instagram"]

    def test_settings_show_role_tables_as_read_only(self):
        snapshot = operating_settings.read()
        self.assertEqual(
            snapshot["controlled"]["brand_accounts"]["facebook"],
            ["neakasaofficial", "Neakasa Deutschland"])
        self.assertEqual(snapshot["controlled"]["frozen_sources"]["instagram"], ["neakasa.tech"])
        self.assertNotIn("brand_accounts", snapshot["editable"])
        self.assertNotIn("frozen_sources", snapshot["editable"])
        for values in ({"brand_accounts": {"instagram": ["neakasa_fans"]}},
                       {"frozen_sources": {"instagram": ["neakasa.tech"]}}):
            with self.assertRaises(ValueError):
                validate(values)


if __name__ == "__main__":
    unittest.main()
