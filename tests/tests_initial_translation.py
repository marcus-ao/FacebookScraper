"""第三方单帖处理：只有临时归档和注入模型，无网络、费用或发布。"""
import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests_paid_consent import ConsentFixture
from localize import images as image_de
from core import paid_consent, review, translated
from pipeline import engine, initial_translation, refinement
from web.api.app import app


class InitialTranslationTests(ConsentFixture):
    def setUp(self):
        super().setUp()
        self.executor = Mock()
        check = patch.object(engine, 'budget_preflight')
        check.start()
        self.addCleanup(check.stop)

    def submit(self, **changes):
        params = dict(source_fingerprint=paid_consent.fingerprint(self.source, self.account),
            source_text_sha256=translated.source_text_sha256(self.source['text']),
            human_revision=None, review_revision=None, executor=self.executor)
        params.update(changes)
        return initial_translation.submit(self.account, self.source, **params)

    def test_capability_is_read_only_and_does_not_grant_permission(self):
        capability = initial_translation.capabilities(self.account, self.source)
        self.assertTrue(capability['available'])
        self.assertTrue(capability['needs_consent'])
        self.assertFalse(paid_consent.history(self.account))

    def test_double_click_queues_once_and_preserves_author_whitelist(self):
        first = self.submit()
        with self.assertRaises(review.ReviewConflict):
            self.submit()
        self.executor.submit.assert_called_once()
        self.assertEqual(len(paid_consent.history(self.account)), 1)
        self.assertEqual(initial_translation.job_result(first['job_id'])['status'], 'pending')
        self.assertNotIn('thirdparty', engine.publish_rules().trusted_owners['facebook'])

    def test_material_or_price_rejection_precedes_permission_and_enqueue(self):
        self.source['text'] += ' $123.47'
        self.write_source()
        with self.assertRaises(review.ReviewConflict):
            self.submit()
        self.assertFalse(paid_consent.history(self.account))
        self.executor.submit.assert_not_called()

    def test_author_change_while_waiting_prevents_every_model_request(self):
        job = self.submit()
        self.source['owner'] = 'different_creator'
        self.write_source()
        translator, editor = Mock(), Mock()
        result = initial_translation.execute(job, self.source, translator=translator, editor=editor)
        self.assertEqual(result['status'], 'failed')
        translator.translate.assert_not_called()
        editor.edit.assert_not_called()

    def test_budget_rejection_does_not_grant_permission(self):
        with patch.object(engine, 'budget_preflight', side_effect=engine.BudgetStopped('budget')):
            with self.assertRaises(engine.BudgetStopped):
                self.submit()
        self.assertFalse(paid_consent.history(self.account))

    def test_processing_uses_existing_ledgers_and_returns_to_review(self):
        job = self.submit()
        calls = []
        translator = SimpleNamespace(translate=lambda text, system: (
            calls.append('translate') or 'Ein sauberes Zuhause. #Neakasa'))
        def scan(**kwargs):
            calls.append('risk_scan')
            return {'status': 'completed', 'risks': []}
        buffer = io.BytesIO()
        Image.new('RGB', (1088, 1088), 'blue').save(buffer, 'JPEG')
        validated = image_de.ValidatedImage(buffer.getvalue(), 1088, 1088, 'JPEG', 0, 0.02)
        editor = Mock()
        editor.edit.return_value = image_de.EditResult('', 'gpt-image-2', {}, 'response')
        editor.paid_request_id = ''
        with patch.object(image_de, 'validate_output', return_value=validated):
            result = initial_translation.execute(job, self.source, translator=translator, editor=editor,
                                                 risk_scanner=scan)
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual(calls[:2], ['risk_scan', 'translate'])
        self.assertTrue(translated.load_translated(self.account / 'translated.jsonl'))
        self.assertTrue(image_de.load_image_state(self.account / 'images_de.jsonl').latest)
        self.assertFalse(translated.load_human_translated(self.account / 'translated_human.jsonl'))
        self.assertEqual(review.state_for(self.account, self.source)['status'], 'pending_review')
        self.assertTrue(engine.latest_human_items(engine.cfg().state_dir))
        self.assertFalse(refinement.capabilities(self.account, self.source['post_id'])['jobs'])

    def test_http_requires_explicit_consent_and_queues_without_blocking_the_page(self):
        url = '/api/initial-translation/task/' + self.account.name + '/' + self.source['post_id']
        with TestClient(app, base_url='http://127.0.0.1:8765', client=('127.0.0.1', 41000)) as client, patch.object(refinement, '_executor', self.executor):
            capability = client.get(url)
            self.assertEqual(capability.status_code, 200)
            body = dict(source_fingerprint=capability.json()['source_fingerprint'],
                source_text_sha256=translated.source_text_sha256(self.source['text']),
                human_revision=None, review_revision=None)
            self.assertEqual(client.post(url, json=body).status_code, 400)
            self.executor.submit.assert_not_called()
            accepted = client.post(url, json=dict(body, consent=True))
            self.assertEqual(accepted.status_code, 202, accepted.text)
            job = client.get('/api/initial-translation/jobs/' + accepted.json()['job_id'])
            self.assertEqual(job.json()['status'], 'pending')


if __name__ == '__main__':
    unittest.main()
