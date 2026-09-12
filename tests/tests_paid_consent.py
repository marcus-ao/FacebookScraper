"""单帖付费授权的真实文件边界；只有临时归档，无外部调用。"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import config, paid_consent, review, translated
from pipeline import engine


class ConsentFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.account = self.root / 'archive' / 'fa_neakasaofficial'
        self.post_dir = self.account / 'posts' / '2026-09-11_1234567890'
        self.post_dir.mkdir(parents=True)
        Image.new('RGB', (1080, 1080), 'blue').save(self.post_dir / '01.jpg')
        self.source = {
            'post_id': '1234567890', 'platform': 'facebook', 'account': 'neakasaofficial',
            'owner': 'thirdparty', 'coauthors': ['neakasaofficial'],
            'text': 'A clean home. #Neakasa', 'created_at': '2026-09-11T08:00:00Z',
            'permalink': 'https://example.test/post/1234567890', 'media_complete': True,
            'media': [{'kind': 'image', 'url': 'https://example.test/01.jpg',
                       'local_path': 'posts/2026-09-11_1234567890/01.jpg'}],
        }
        self.write_source()
        test_config = config.Config()
        test_config._d['paths'] = {'archive': str(self.root / 'archive'), 'state': str(self.root / 'state')}
        config_patch = patch.object(config, '_cfg', test_config)
        config_patch.start()
        self.addCleanup(config_patch.stop)

    def write_source(self):
        (self.post_dir / 'post.json').write_text(json.dumps(self.source), encoding='utf-8')
        (self.account / 'manifest.jsonl').write_text(json.dumps(self.source) + '\n', encoding='utf-8')

    def grant(self, **changes):
        args = dict(source_fingerprint=paid_consent.fingerprint(self.source, self.account),
                    source_text_sha256=translated.source_text_sha256(self.source['text']),
                    review_revision=None, human_revision=None)
        args.update(changes)
        return paid_consent.grant(self.account, self.source, **args)

    def candidate(self):
        source = engine.SourcePost('facebook', self.account, dict(self.source),
            datetime.now(timezone.utc), 'facebook:' + self.source['post_id'])
        return engine.Candidate(source, (source,), 'independent')


class PaidConsentTests(ConsentFixture):
    def test_consent_is_per_source_revision_and_never_changes_trusted_owners(self):
        rules = engine.publish_rules()
        self.assertEqual(engine.prepaid_issue(self.candidate(), rules).kind, 'unknown_collaborator')
        row = self.grant()
        self.assertIsNone(row['actor'])
        self.assertTrue(paid_consent.is_current(self.account, self.source))
        self.assertIsNone(engine.prepaid_issue(self.candidate(), rules))
        self.assertNotIn('thirdparty', engine.publish_rules().trusted_owners['facebook'])
        self.source['text'] += ' '
        self.write_source()
        self.assertFalse(paid_consent.is_current(self.account, self.source), '完整原文变化必须重新授权')
        self.assertEqual(engine.prepaid_issue(self.candidate(), rules).kind, 'unknown_collaborator')

    def test_replacing_original_bytes_invalidates_consent_even_when_url_stays_equal(self):
        self.grant()
        Image.new('RGB', (1080, 1080), 'green').save(self.post_dir / '01.jpg')
        self.assertFalse(paid_consent.is_current(self.account, self.source))

    def test_incomplete_coauthor_relationship_cannot_be_bypassed(self):
        self.source['coauthors'] = []
        self.write_source()
        self.grant()
        self.assertEqual(engine.prepaid_issue(self.candidate(), engine.publish_rules()).kind, 'unknown_collaborator')

    def test_snoozed_or_changed_page_cannot_grant_consent(self):
        with self.assertRaises(review.ReviewConflict):
            self.grant(source_fingerprint='a' * 64)
        review.transition(self.account, self.source, 'snoozed', expected_revision=None,
                          expected_source_sha256=translated.source_text_sha256(self.source['text']))
        with self.assertRaises(review.ReviewConflict):
            self.grant()
        self.assertFalse(paid_consent.history(self.account))


if __name__ == '__main__':
    unittest.main()
