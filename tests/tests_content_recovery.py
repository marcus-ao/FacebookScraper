"""中断恢复只闭合本地任务；不重发 HTTP，不忘记已经发生的费用。"""
import json
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests_web_review as fixtures
from core import paid_requests, review, translated
from pipeline import refinement, engine


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.WebReviewTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.executor = Mock()
        self.budget = patch.object(engine, 'budget_preflight', return_value=None)
        self.budget.start()

    def queue(self):
        f = self.fixture
        return refinement.submit(f.account, f.source, kind='text', instruction='更自然',
            source_text_sha256=translated.source_text_sha256(f.source['text']),
            human_revision=None, review_revision=None, executor=self.executor)

    def test_dead_worker_is_shown_interrupted_and_can_close_without_request(self):
        job = self.queue()
        with patch('pipeline.refinement.worker_alive', return_value=False):
            public = refinement.job_result(job['job_id'])
            self.assertEqual(public['status'], 'interrupted')
            closed = refinement.recover(job['job_id'], expected_updated_at=job['recorded_at'])
        self.assertEqual(closed['status'], 'failed')
        self.assertEqual(closed['error'], 'interrupted_before_request')
        self.assertEqual(closed['paid_request_ids'], [])
        self.assertNotEqual(self.queue()['job_id'], job['job_id'])

    def test_alive_worker_cannot_be_closed_by_another_tab(self):
        job = self.queue()
        with self.assertRaises(review.ReviewConflict):
            refinement.recover(job['job_id'], expected_updated_at=job['recorded_at'])
        self.assertEqual(refinement.latest()[job['job_id']]['status'], 'pending')

    def test_recovered_text_preserves_its_prompt_version_and_stays_expired(self):
        job = self.queue()
        path = self.fixture.account / 'translated.jsonl'
        machine = translated.load_translated(path)[self.fixture.post_id]
        translated.append_translated(path, dict(machine, refine_id=job['job_id']))
        before = path.read_bytes()
        with patch('pipeline.refinement.worker_alive', return_value=False), \
                patch.object(translated, 'PROMPT_VERSION', translated.PROMPT_VERSION + 1):
            recovered = refinement.recover(job['job_id'], expected_updated_at=job['recorded_at'])
            polled = self.fixture.client.get('/api/refinements/jobs/' + job['job_id']).json()
        self.assertEqual(recovered['status'], 'succeeded')
        self.assertEqual(recovered['prompt_version'], machine['prompt_version'])
        self.assertFalse(recovered['prompt_current'])
        self.assertEqual(polled['prompt_current'], recovered['prompt_current'])
        self.assertEqual(path.read_bytes(), before)
        self.executor.submit.assert_called_once()

    def test_unresolved_paid_call_blocks_recovery_after_crash(self):
        job = self.queue()
        event = {'schema_version': 1, 'request_id': 'paid-1', 'job_key': 'test', 'stage': 'translation',
                 'operation_id': job['job_id'], 'event': 'started', 'recorded_at': job['recorded_at']}
        path = self.fixture.root / 'state' / 'paid_requests.jsonl'
        path.write_text(json.dumps(event) + '\n', encoding='utf-8')
        with patch('pipeline.refinement.worker_alive', return_value=False):
            self.assertEqual(refinement.job_result(job['job_id'])['paid_request_ids'], ['paid-1'])
            with self.assertRaisesRegex(review.ReviewConflict, '付费'):
                refinement.recover(job['job_id'], expected_updated_at=job['recorded_at'])
        self.assertEqual(paid_requests.load_events(path.parent)[-1]['event'], 'started')
        self.assertEqual(refinement.latest()[job['job_id']]['status'], 'pending')

    def test_paid_request_association_survives_usage_and_finalization(self):
        state = self.fixture.root / 'state'
        caller = paid_requests.RequestController(state, preflight=lambda: None, operation_id='content-1')
        result, receipt = caller.run(stage='translation', job_key='test', source_ref='facebook:123',
            media_index=None, model='offline', request=lambda: 'result',
            usage_getter=lambda: {'tokens': 2}, usage_errors=lambda usage: [], usage_cost=lambda usage: 0.03)
        caller.finalize(receipt, accepted=True)
        self.assertEqual(result, 'result')
        rows = paid_requests.load_events(state)
        self.assertEqual([row['event'] for row in rows], ['started', 'usage_recorded', 'accepted'])
        self.assertEqual({row['operation_id'] for row in rows}, {'content-1'})

    def test_worker_identity_does_not_mistake_exited_process_for_live(self):
        from core.process_identity import current_worker, worker_alive
        self.assertTrue(worker_alive(current_worker()))
        child = subprocess.Popen([sys.executable, '-c', 'pass'])
        child.wait(timeout=10)
        self.assertFalse(worker_alive({'pid': child.pid, 'started': 'previous-instance'}))
        self.assertIsNone(worker_alive(None))

    def test_exited_process_with_retained_handle_is_not_alive(self):
        from core.process_identity import worker_alive
        child = subprocess.Popen([sys.executable, '-c',
            'import json,sys; from core.process_identity import current_worker; print(json.dumps(current_worker()),flush=True); sys.stdin.readline()'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: child.kill() if child.poll() is None else None)
        owner = json.loads(child.stdout.readline())
        self.assertTrue(worker_alive(owner))
        child.communicate('\n', timeout=5)
        self.assertEqual(child.returncode, 0)
        self.assertFalse(worker_alive(owner))

    def test_worker_claim_waits_for_submission_lock_and_does_not_disappear(self):
        from core.paid_model import FileLock
        from pipeline import initial_translation
        job = self.queue()
        for execute in (refinement.execute, initial_translation.execute):
            with self.subTest(execute=execute.__module__), ThreadPoolExecutor(max_workers=1) as pool, \
                    patch.object(engine, 'budget_preflight', side_effect=ValueError('offline stop')):
                current = dict(job, status='pending', kind='initial' if execute is initial_translation.execute else 'text')
                refinement._append(current)
                with FileLock(self.fixture.root / 'state' / 'refinement.lock', busy_message='fixture'):
                    future = pool.submit(execute, current, self.fixture.source)
                    self.assertEqual(refinement.latest()[job['job_id']]['status'], 'pending')
                # No model client is constructed: the preflight stops after a successful claim.
                self.assertEqual(future.result(timeout=5)['status'], 'failed')

    def test_recovery_endpoint_rejects_stale_tab_and_never_submits(self):
        job = self.queue()
        path = '/api/content-jobs/' + job['job_id'] + '/recover'
        with patch('pipeline.refinement.worker_alive', return_value=False):
            self.assertEqual(self.fixture.client.post(path, json={'expected_updated_at': 'old'}).status_code, 409)
            response = self.fixture.client.post(path, json={'expected_updated_at': job['recorded_at']})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['error'], 'interrupted_before_request')
        self.executor.submit.assert_called_once()


if __name__ == '__main__':
    unittest.main()
