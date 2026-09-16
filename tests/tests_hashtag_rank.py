"""语义候选不会因缺热度变成发布硬闸；不把全球累计数叫作德国热搜。"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests_web_review as fixtures
from core import hashtag_rank as rank, review, translated
from pipeline import engine, hashtag_suggestions
from routes import hashtags

NOW = datetime(2026, 9, 12, 10, tzinfo=timezone.utc)
KEEP = {'brands': ['Neakasa'], 'models': ['M1']}


class RankTests(unittest.TestCase):
    def test_twelve_source_tags_keep_twelve_defaults_without_samples(self):
        tags = ['#Tag%d' % i for i in range(10)]
        source = 'Hello ' + ' '.join(tags + ['#Neakasa', '#M1'])
        candidates = {tag: ['#Wort%d%s' % (i, suffix) for suffix in ('A', 'B', 'C')]
                      for i, tag in enumerate(tags)}
        parsed = rank.parse_candidates(json.dumps(candidates), tags)
        result = rank.recommend(source, parsed, keep_verbatim=KEEP, now=NOW)
        self.assertEqual(len(result['selected']), 12)
        self.assertEqual(result['selected'][-2:], ['#Neakasa', '#M1'])
        self.assertFalse(result['sampled'])
        self.assertIn('缺少同类账号', result['notice'])

    def test_stale_signal_is_displayed_but_does_not_rank(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'signals.jsonl'
            rank.append_samples(path, [{'tag': '#Katzen', 'media_count': 999999,
                'sampled_at': (NOW - timedelta(days=15)).isoformat(), 'source': 'recorded-instagram-search'}])
            result = rank.recommend('#Cats', {'#Cats': ['#Katzenliebe', '#Katzen', '#Hauskatzen']},
                keep_verbatim=KEEP, signals=rank.load_signals(path), now=NOW)
            self.assertEqual(result['selected'], ['#Katzenliebe'])
            old = result['groups'][0]['candidates'][1]
            self.assertTrue(old['signals'])
            self.assertFalse(old['current_signals'])

    def test_sampling_failure_and_empty_peer_list_are_explicit_degradation(self):
        sampler = Mock(side_effect=RuntimeError('offline'))
        self.assertEqual(hashtags.refresh_candidates(['#Katzen'], Path('unused'), sampler=sampler)['status'], 'unavailable')
        sampler.reset_mock()
        self.assertEqual(hashtags.refresh_peers([], Path('unused'), sampler=sampler)['status'], 'skipped')
        sampler.assert_not_called()

    def test_non_german_trends_cannot_be_saved_as_german_ranking(self):
        with self.assertRaisesRegex(ValueError, '德国'):
            rank.append_samples(Path('unused'), [{'tag': '#Katzen', 'trend_score': 12,
                'geo': 'US', 'sampled_at': NOW.isoformat(), 'source': 'google-trends'}])

    def test_incomplete_model_response_cannot_silently_drop_source_tags(self):
        with self.assertRaises(ValueError):
            rank.parse_candidates('{"#Cats":["#Katzen","#Hauskatzen","#Katzenliebe"]}', ['#Cats', '#Pets'])

    def test_production_suggestions_use_paid_context_and_degrade_after_generation(self):
        fixture = fixtures.WebReviewTests()
        fixture.setUp()
        try:
            fixture.source['text'] = 'Cats at home. #Cats #Neakasa'
            fixture.write_source()
            caller = Mock()
            caller.translate.return_value = '{"#Cats":["#Katzen","#Hauskatzen","#Katzenliebe"]}'
            caller.paid_request_id = 'offline-test'
            # 本轮 [hashtags].enabled = false 是业务决定；这条测的是开着时的付费契约。
            with (patch.object(engine, 'budget_preflight', return_value=None),
                  patch.object(hashtag_suggestions, 'suggestions_enabled', return_value=True)):
                result = hashtag_suggestions.suggest(fixture.account, fixture.source,
                    source_text_sha256=translated.source_text_sha256(fixture.source['text']),
                    human_revision=None, review_revision=None, translator=caller,
                    sampler=Mock(side_effect=OSError('network unavailable')))
            caller.set_paid_context.assert_called_once()
            self.assertEqual(result['selected'], ['#Katzen', '#Neakasa'])
            self.assertEqual(result['sampling']['status'], 'unavailable')
            caller.finalize_paid.assert_called_once_with(True, 'hashtag candidates fsynced')
        finally:
            fixture.doCleanups()

    def test_disabled_suggestions_refuse_before_spending_anything(self):
        fixture = fixtures.WebReviewTests()
        fixture.setUp()
        try:
            fixture.source['text'] = 'Cats at home. #Cats #Neakasa'
            fixture.write_source()
            caller, preflight = Mock(), Mock()
            with (patch.object(engine, 'budget_preflight', preflight),
                  patch.object(hashtag_suggestions, 'suggestions_enabled', return_value=False),
                  self.assertRaisesRegex(review.ReviewConflict, '手工选取')):
                hashtag_suggestions.suggest(fixture.account, fixture.source,
                    source_text_sha256=translated.source_text_sha256(fixture.source['text']),
                    human_revision=None, review_revision=None, translator=caller,
                    sampler=Mock())
            # 候选生成排在采样之前，所以关掉之后这一次模型调用一次都不能发出去。
            caller.translate.assert_not_called()
            caller.set_paid_context.assert_not_called()
            preflight.assert_not_called()
        finally:
            fixture.doCleanups()

    def test_disabled_sampling_stops_scheduling_the_weekly_task(self):
        c = SimpleNamespace(get=lambda section, key, default=None:
                            False if key == 'enabled' else default,
                            state_dir=Path('unused'))
        self.assertFalse(hashtag_suggestions.weekly_refresh_due(now=NOW, c=c))


if __name__ == '__main__':
    unittest.main()
