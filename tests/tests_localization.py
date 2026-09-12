"""平台本地化的纯规则与追加账本；临时源帖，不调用网络。"""
from __future__ import annotations
import json
import sys
import tempfile
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import localization as loc, translated  # noqa: E402
from core.store import post_dirname  # noqa: E402

KEEP = {"brands": ["Neakasa"], "models": ["M1 Pro"]}

class LocalizationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.account = Path(self.temporary.name) / "fa_example"
        self.source = {"post_id": "123", "platform": "facebook", "account": "example",
                       "created_at": "2026-09-12T02:00:00Z",
                       "text": "Clean now https://us.example/p#buy #Neakasa #M1Pro #CatLover"}
        self.post = self.account / "posts" / post_dirname("123", self.source["created_at"])
        self.post.mkdir(parents=True)
        self.write_source()
        self.human = None
        self.machine = {"post_id": "123", "text_de": "Sauber jetzt #Neakasa #M1Pro #CatLover",
                        "source_text_sha256": translated.source_text_sha256(self.source["text"]),
                        "prompt_version": translated.PROMPT_VERSION}

    def write_source(self):
        (self.post / "post.json").write_text(json.dumps(self.source), encoding="utf-8")

    def draft(self, **kwargs):
        effective = translated.effective_translation(self.source, self.machine, self.human)
        return loc.draft_for(self.source, effective, keep_verbatim=KEEP, **kwargs)

    def test_split_excludes_url_fragments_from_tags_and_preserves_unicode(self):
        text = "See (https://example.com/a(b)?utm=x#buy). #Neakasa #Café 😀 www.example.com/p!"
        result = loc.split_content(text)
        self.assertEqual(result["links"], ["https://example.com/a(b)?utm=x#buy", "www.example.com/p"])
        self.assertEqual(result["tags"], ["#Neakasa", "#Café"])
        self.assertIn("😀", result["body"])
        self.assertNotIn("http", result["body"])
        self.assertIn("#Neakasa", loc.without_urls(text))
        self.assertEqual(loc.extract_urls(loc.without_urls(text)), [])

    def test_missing_mapping_and_semantic_tags_require_confirmation(self):
        draft = self.draft()
        self.assertEqual(draft["protected_tags"], ["#Neakasa", "#M1Pro"])
        self.assertEqual(draft["links"][0]["target_url"], "")
        result = loc.validate(draft)
        self.assertFalse(result["ready"])
        self.assertTrue({"links_unconfirmed", "hashtags_unconfirmed"} <= {r["code"] for r in result["issues"]})

    def test_config_mapping_is_accepted_override_requires_confirmation(self):
        draft = self.draft(link_map={"https://us.example/p#buy": "https://de.example/p"})
        draft["tags"] = ["#Neakasa", "#M1Pro", "#Katzenliebe"]
        draft["hashtags_confirmed"] = True
        self.assertTrue(loc.validate(draft)["ready"])
        rendered = loc.render(draft)
        self.assertIn("https://de.example/p", rendered)
        self.assertNotIn("us.example", rendered)
        draft["links"][0].update(target_url="https://de.example/other", confirmed=False)
        self.assertFalse(loc.validate(draft)["ready"])

    def test_brand_case_is_preserved_but_semantic_replacement_allowed(self):
        draft = self.draft(link_map={"https://us.example/p#buy": "https://de.example/p"})
        draft.update(tags=["#neakasa", "#M1Pro", "#Katzenliebe"], hashtags_confirmed=True)
        self.assertIn("protected_tags_changed", {r["code"] for r in loc.validate(draft)["issues"]})

    def test_instagram_removes_urls_adds_cta_and_bio_missing_is_not_blocking(self):
        self.source["platform"] = "instagram"
        draft = self.draft()
        draft.update(hashtags_confirmed=True, ig_cta="Mehr dazu im Profil 🔗")
        rendered = loc.render(draft)
        self.assertEqual(loc.extract_urls(rendered), [])
        self.assertIn("Mehr dazu im Profil 🔗", rendered)
        self.assertEqual(draft["ig_bio_url"], "")
        self.assertTrue(loc.validate(draft)["ready"])

    def test_counts_use_codepoints_and_limits_only_warn(self):
        draft = self.draft(link_map={"https://us.example/p#buy": "https://de.example/p"})
        draft.update(platform="instagram", body_de="😀" * 2201,
                     tags=["#Neakasa", "#M1Pro"] + ["#tag%d" % n for n in range(29)],
                     hashtags_confirmed=True, ig_cta="Link in Bio 🔗")
        result = loc.validate(draft)
        self.assertTrue(result["ready"])
        self.assertEqual(result["hashtag_count"], 31)
        self.assertEqual(result["char_count"], len(loc.render(draft)))
        self.assertEqual(len(draft["body_de"]), 2201)
        self.assertTrue({"caption_length", "hashtag_count"} <= {r["code"] for r in result["warnings"]})

    def test_existing_translation_with_zero_tags_must_not_silently_refill_source(self):
        self.machine["text_de"] = "Caption with every hashtag missing."
        draft = self.draft()
        self.assertEqual(draft["tags"], [])
        self.assertIn("protected_tags_changed", {row["code"] for row in loc.validate(draft)["issues"]})

    def test_body_urls_or_tags_and_unsafe_link_are_explicit_issues(self):
        draft = self.draft()
        draft["body_de"] = "Bitte https://us.example/new #new"
        draft["links"][0].update(target_url="javascript:alert(1)", confirmed=True)
        codes = {r["code"] for r in loc.validate(draft)["issues"]}
        self.assertTrue({"body_urls", "body_hashtags", "invalid_link"} <= codes)

    def save_local(self, draft, expected_revision=None):
        self.human = translated.append_human_translation(self.account / "translated_human.jsonl",
                                                         self.source, loc.render(draft))
        return loc.append_localization(self.account, self.source, draft,
            human_revision=self.human["revision"], expected_revision=expected_revision,
            expected_source_sha256=translated.source_text_sha256(self.source["text"]))

    def test_roundtrip_links_tags_body_and_revision_are_bound_to_human_truth(self):
        self.source["platform"] = "instagram"
        self.write_source()
        draft = self.draft()
        draft.update(body_de="Von Hand formuliert.", ig_cta="Link in Bio 🔗", hashtags_confirmed=True)
        row = self.save_local(draft)
        loaded = loc.load_localizations(self.account)["123"]
        self.assertEqual(loaded["revision"], row["revision"])
        self.assertIsNone(row["actor"])
        current = self.draft(record=loaded)
        self.assertEqual(current["body_de"], "Von Hand formuliert.")
        self.assertEqual(loc.render(current).count("Link in Bio"), 1)
        self.assertTrue(current["hashtags_confirmed"])
        with self.assertRaises(loc.LocalizationConflict):
            loc.append_localization(self.account, self.source, draft,
                human_revision=self.human["revision"], expected_revision=None,
                expected_source_sha256=translated.source_text_sha256(self.source["text"]))

    def test_new_human_or_changed_source_does_not_reuse_old_confirmation(self):
        row = self.save_local(self.draft())
        self.human = translated.append_human_translation(self.account / "translated_human.jsonl",
            self.source, "Neuer manueller Text. #Neakasa #M1Pro #Neu")
        current = self.draft(record=row)
        self.assertTrue(current["record_stale"])
        self.assertEqual(current["body_de"], "Neuer manueller Text.")
        self.assertIn("#Neu", current["tags"])
        self.assertFalse(current["hashtags_confirmed"])
        self.source["text"] += " Updated"
        self.write_source()
        self.assertTrue(self.draft(record=row)["source_stale"])
        with self.assertRaises(loc.LocalizationConflict):
            loc.append_localization(self.account, self.source, current,
                human_revision=self.human["revision"], expected_revision=row["revision"],
                expected_source_sha256="0" * 64)

    def test_bad_tail_does_not_hide_saved_record(self):
        row = self.save_local(self.draft())
        with (self.account / "localization.jsonl").open("ab") as f:
            f.write(b'\n{"broken":')
        self.assertEqual(loc.load_localizations(self.account)["123"]["revision"], row["revision"])

if __name__ == "__main__":
    unittest.main()
