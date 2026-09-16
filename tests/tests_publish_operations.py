"""提交过程对页面可见：进度落盘、轮询、进程死掉也能判定，且不排队。"""
import asyncio
import json
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests_approval as fixtures
from core import config, review
from publish import business_suite as bs, journal, operations, workflow
from web.api import approval as web_approval


class PublishOperationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ApprovalTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.f = self.fixture

    def start(self):
        locked = self.f.lock_content()
        return locked, operations.start(
            'fa_example/' + self.f.source['post_id'], platform='facebook',
            snapshot_id=locked['snapshot_id'], scheduled_at=fixtures.TARGET)

    def test_progress_is_durable_so_closing_the_page_loses_nothing(self):
        _, record = self.start()
        self.assertEqual(record['status'], operations.RUNNING)
        self.assertEqual(record['step_total'], len(operations.STEPS))
        operations.progress(record['operation_id'], 3, '交图（2 张）…')
        reread = operations.read(record['operation_id'])
        self.assertEqual((reread['step_index'], reread['step']), (3, '交图（2 张）…'))
        done = operations.finish(record['operation_id'], status=operations.SUCCEEDED,
                                 message='已排期', result={'ok': True})
        self.assertEqual(done['step_index'], done['step_total'])
        self.assertEqual(operations.read(record['operation_id'])['result'], {'ok': True})

    def test_a_dead_submitter_becomes_uncertain_rather_than_running_forever(self):
        """进程没了不等于远端没提交；必须停下等人工核对，不能自动重试。"""
        _, record = self.start()
        path = config.cfg().state_dir / 'publish_operations' / (record['operation_id'] + '.json')
        stored = json.loads(path.read_text(encoding='utf-8'))
        stored['worker'] = {'pid': 999999, 'started': '1'}
        path.write_text(json.dumps(stored), encoding='utf-8')
        with patch.object(operations, 'worker_alive', return_value=False):
            reread = operations.read(record['operation_id'])
        self.assertEqual(reread['status'], operations.UNCERTAIN)
        self.assertIn('不要直接重试', reread['message'])
        self.assertIsNone(operations.active())

    def test_a_progress_write_failure_never_interrupts_the_submission(self):
        """进度是给人看的；写不进去也不能让一次真实提交半途而废。"""
        _, record = self.start()
        operations.progress('0' * 32, 2, '打开编辑器')      # 记录不存在
        with patch.object(operations, '_write', side_effect=OSError('disk full')):
            operations.progress(record['operation_id'], 2, '打开编辑器')
        still = operations.read(record['operation_id'])
        self.assertEqual(still['status'], operations.RUNNING)
        self.assertEqual(still['step_index'], 0)

    def test_the_browser_reports_each_step_through_the_injected_callback(self):
        self.f.allow_fixture_evidence()
        seen = []
        async def execute(post, when, **kwargs):
            kwargs['report'](3, '交图（1 张）…')
            base = workflow.new_attempt(post, when, ui_timezone=kwargs['ui_timezone'])
            result = journal.transition(base, journal.STATUS_SCHEDULED,
                                        recorded_at=fixtures.NOW.isoformat())
            journal.append(config.cfg().state_dir, result)
            return workflow.AttemptOutcome(0, result, 'fixture-confirmed')
        locked = self.f.lock_content()
        params = dict(self.f.params, review_revision=locked['revision'])
        # executor 注入时 approve 不再自己补 report；这里显式传，模拟真实 workflow 的回调。
        async def executor(post, when, **kwargs):
            return await execute(post, when, **dict(kwargs, report=lambda i, t: seen.append((i, t))))
        result = asyncio.run(web_approval.approval.approve(
            self.f.account, self.f.source, **params,
            inventory_reader=AsyncMock(return_value=self.f.inventory), executor=executor))
        self.assertTrue(result['ok'])
        self.assertEqual(seen, [(3, '交图（1 张）…')])

    def test_a_second_submission_is_refused_instead_of_queued(self):
        """⛔ 不建发布队列：并发直接拒绝（FUNCTIONALITY F5-9）。"""
        _, record = self.start()
        self.assertEqual(operations.active()['operation_id'], record['operation_id'])
        operations.finish(record['operation_id'], status=operations.FAILED, message='fixture')
        self.assertIsNone(operations.active())

    def test_a_failed_submission_keeps_the_conflict_suggestions_readable(self):
        locked = self.f.lock_content()
        record = operations.start('fa_example/x', platform='facebook',
                                  snapshot_id=locked['snapshot_id'],
                                  scheduled_at=fixtures.TARGET + timedelta(hours=1))
        conflict = web_approval.approval.ApprovalConflict(
            '这个时刻暂不能排期', suggestions=(fixtures.TARGET,))
        with patch.object(web_approval.approval, 'approve', AsyncMock(side_effect=conflict)):
            asyncio.run(web_approval._run(record['operation_id'], self.f.account,
                                          self.f.source, {'scheduled_at': '2026-09-12T20:00'}, 'fp'))
        done = operations.read(record['operation_id'])
        self.assertEqual(done['status'], operations.FAILED)
        self.assertEqual(done['result']['suggestions'], [fixtures.TARGET.isoformat()])

    def test_an_unexpected_crash_is_reported_as_uncertain_not_failed(self):
        """未知结果不能说成失败——失败会诱导人再点一次。"""
        locked = self.f.lock_content()
        record = operations.start('fa_example/x', platform='facebook',
                                  snapshot_id=locked['snapshot_id'], scheduled_at=fixtures.TARGET)
        with patch.object(web_approval.approval, 'approve', AsyncMock(side_effect=RuntimeError('boom'))):
            asyncio.run(web_approval._run(record['operation_id'], self.f.account,
                                          self.f.source, {'scheduled_at': '2026-09-12T20:00'}, 'fp'))
        done = operations.read(record['operation_id'])
        self.assertEqual(done['status'], operations.UNCERTAIN)
        self.assertIn('不要直接重试', done['message'])

    def test_a_publish_step_error_is_a_plain_failure_with_its_reason(self):
        locked = self.f.lock_content()
        record = operations.start('fa_example/x', platform='facebook',
                                  snapshot_id=locked['snapshot_id'], scheduled_at=fixtures.TARGET)
        with patch.object(web_approval.approval, 'approve',
                          AsyncMock(side_effect=bs.PublishStepError('发布浏览器没开'))):
            asyncio.run(web_approval._run(record['operation_id'], self.f.account,
                                          self.f.source, {'scheduled_at': '2026-09-12T20:00'}, 'fp'))
        done = operations.read(record['operation_id'])
        self.assertEqual(done['status'], operations.FAILED)
        self.assertEqual(done['message'], '发布浏览器没开')

    def test_unknown_or_malformed_operation_ids_are_refused(self):
        self.assertIsNone(operations.read('0' * 32))
        for value in ('', 'nope', '../escape', 'A' * 32):
            with self.subTest(value=value), self.assertRaises(ValueError):
                operations._path(value)


if __name__ == '__main__':
    unittest.main()
