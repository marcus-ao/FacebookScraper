"""人工正文在发布组装和付费前对账中的真实文件集成测试。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from activation_fixtures import activate as fixture_activate

from core import translated  # noqa: E402
from core.console import force_utf8  # noqa: E402
from core.store import Archive, post_dirname  # noqa: E402
from pipeline import engine  # noqa: E402
from publish.compose import ComposeError, _load_current_translation  # noqa: E402

force_utf8()


class HumanPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.arc = Archive(self.root / 'archive', 'fa_acme')
        self.now = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
        self.row = {
            'post_id': 'human-case', 'platform': 'facebook', 'account': 'acme',
            'owner': 'acme', 'coauthors': [], 'text': 'Clean home. #Neakasa',
            'created_at': '2026-09-03T12:00:00Z', 'media_complete': True,
        }
        folder = 'posts/' + post_dirname(self.row['post_id'], self.row['created_at'])
        self.row['media'] = [{'kind': 'image', 'local_path': folder + '/01.jpg'}]
        post_dir = self.arc.base / folder
        post_dir.mkdir(parents=True)
        Image.new('RGB', (100, 100), 'red').save(post_dir / '01.jpg')
        self.write_source()
        self.write_machine('Maschinelle Fassung. #Neakasa')
        self.human_path = self.arc.base / 'translated_human.jsonl'

    def write_source(self):
        self.arc.manifest.write_text(json.dumps(self.row) + '\n', encoding='utf-8')
        folder = Path(self.row['media'][0]['local_path']).parent
        (self.arc.base / folder / 'post.json').write_text(json.dumps(self.row), encoding='utf-8')

    def write_machine(self, text):
        translated.append_translated(self.arc.base / 'translated.jsonl', {
            'post_id': self.row['post_id'], 'text_de': text,
            'model': 'offline-fixture', 'translated_at': self.now.isoformat(),
            'prompt_version': translated.PROMPT_VERSION,
            'source_text_sha256': translated.source_text_sha256(self.row['text']),
        })

    def source(self):
        return engine.SourcePost('facebook', self.arc.base, dict(self.row),
                                 datetime(2026, 9, 3, 12, tzinfo=timezone.utc),
                                 'facebook:acme:human-case')

    def save_human(self, text='Von Hand verbessert. #Neakasa'):
        return translated.append_human_translation(self.human_path, self.row, text, now=self.now)

    def test_publish_uses_human_even_after_machine_regeneration(self):
        self.save_human()
        self.write_machine('Noch eine maschinelle Fassung. #Neakasa')
        self.assertEqual(_load_current_translation(self.arc, self.row),
                         'Von Hand verbessert. #Neakasa')

    def test_current_human_does_not_require_machine_or_prompt_version(self):
        self.save_human()
        (self.arc.base / 'translated.jsonl').unlink()
        self.assertFalse(engine.translation_needed(self.source()))
        self.assertEqual(_load_current_translation(self.arc, self.row),
                         'Von Hand verbessert. #Neakasa')

    def test_stale_human_cannot_fall_back_to_current_machine_for_publish(self):
        self.save_human()
        self.row['text'] = 'Changed campaign. #Neakasa'
        self.write_source()
        self.write_machine('Aktuelle Maschinenfassung. #Neakasa')
        with self.assertRaisesRegex(ComposeError, '人工.*复核'):
            _load_current_translation(self.arc, self.row)

    def test_stale_human_stops_before_any_paid_stage_and_reopens_review(self):
        self.save_human()
        self.row['text'] = 'Changed campaign. #Neakasa'
        self.write_source()
        rules = replace(engine.publish_rules(),
                        trusted_owners={'facebook': frozenset({'acme'}), 'instagram': frozenset()},
                        source_accounts={'facebook': 'acme', 'instagram': 'acme'})
        state = self.root / 'state'
        fixture_activate(engine, state, g8_verified=True, now=datetime(2026, 9, 1, tzinfo=timezone.utc))
        settings = dict(engine.pipeline_settings(), autonomy='assisted')

        class NoPaidCalls:
            def delta(self, **kwargs):
                return 0

            def translate(self, source):
                raise AssertionError('stale human must not cause a paid translation')

            def image(self, source, index):
                raise AssertionError('stale human must not cause a paid image request')

        with patch.object(engine, 'publish_rules', return_value=rules), \
                patch.object(engine, 'active_account_dirs', return_value=[self.arc.base]):
            result = engine.run(account_dirs=[self.arc.base], state_dir=state, settings=settings,
                                runner=NoPaidCalls(), now=self.now, report=lambda _: None)
        self.assertEqual(result, 0)
        items = engine.latest_human_items(state)
        stale = [v for v in items.values() if v.get('kind') == 'human_translation_stale']
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]['status'], 'open')
        self.assertIn('复核', stale[0]['summary'])
        self.save_human('Erneut geprüft. #Neakasa')
        source = self.source()
        self.assertIsNone(engine.prepaid_issue(engine.Candidate(source, (source,), 'single'), rules))

    def test_human_priority_does_not_remove_existing_money_gate(self):
        self.row['text'] = 'Save $50. #Neakasa'
        self.save_human('Jetzt für €45. #Neakasa')
        with self.assertRaisesRegex(ComposeError, '金额硬闸'):
            _load_current_translation(self.arc, self.row)

    def test_pipeline_reads_post_truth_when_manifest_has_not_caught_up(self):
        self.save_human()
        new_row = dict(self.row, text='Changed source in post.json. #Neakasa')
        folder = Path(self.row['media'][0]['local_path']).parent
        (self.arc.base / folder / 'post.json').write_text(json.dumps(new_row), encoding='utf-8')
        boundary = datetime(2026, 9, 1, tzinfo=timezone.utc)
        sources, issues, skipped = engine.load_sources([self.arc.base], boundary)
        self.assertFalse(issues)
        self.assertFalse(skipped)
        self.assertEqual(sources[0].text, new_row['text'])
        rules = replace(engine.publish_rules(),
                        trusted_owners={'facebook': frozenset({'acme'}), 'instagram': frozenset()},
                        source_accounts={'facebook': 'acme', 'instagram': 'acme'})
        candidate = engine.Candidate(sources[0], (sources[0],), 'single')
        self.assertEqual(engine.prepaid_issue(candidate, rules).kind, 'human_translation_stale')
        translated.append_human_translation(self.human_path, new_row,
                                             'Neu geprüft. #Neakasa', now=self.now)
        sources, _, _ = engine.load_sources([self.arc.base], boundary)
        candidate = engine.Candidate(sources[0], (sources[0],), 'single')
        self.assertIsNone(engine.prepaid_issue(candidate, rules))


if __name__ == '__main__':
    unittest.main()
