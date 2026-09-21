"""提交过程对页面可见：进度落盘、轮询、进程死掉也能判定，且不排队。"""
import asyncio
import json
import os
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests_approval as fixtures
from core import config, maintenance, review
from publish import (business_suite as bs, journal, local_schedule, observations,
                     operations, records, workflow)
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

    def test_accepted_http_submission_blocks_deployment_until_background_completion(self):
        import httpx
        from web.api.app import app
        locked = self.f.lock_content()
        control = config.cfg().state_dir / 'test-control'
        gate = maintenance.Gate(control)
        gate.initialize()
        network = {'web_host': '0.0.0.0', 'web_port': 8765,
                   'public_base_url': 'http://192.168.10.20:8765', 'allowed_client_cidrs': ['192.168.10.0/24']}
        (control / 'host.json').write_text(json.dumps(network), encoding='utf-8')

        async def scenario():
            started, finish = asyncio.Event(), asyncio.Event()
            async def approve(*args, **kwargs):
                with maintenance.operation('workflow'):
                    started.set()
                    await finish.wait()
                return {'ok': True, 'message': 'offline fixture'}
            source = SimpleNamespace(account_dir=self.f.account, row=self.f.source)
            with patch.dict(os.environ, FBSCRAPER_CONTROL_DIR=str(control)), \
                    patch.object(web_approval, '_source', return_value=source), \
                    patch.object(web_approval.approval, 'options', return_value={'available': True}), \
                    patch.object(web_approval.approval, 'approve', side_effect=approve):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('192.168.10.21', 41000)),
                                            base_url=network['public_base_url'],
                                            headers={'Origin': network['public_base_url']}) as client:
                    response = await client.post('/api/tasks/fa_example/x/approve', json={
                        'scheduled_at': fixtures.TARGET.isoformat(), 'content_fingerprint': 'fixture',
                        'review_revision': locked['revision']})
                self.assertEqual(response.status_code, 202, response.text)
                await asyncio.wait_for(started.wait(), timeout=3)
                pending = list(web_approval._running)
                try:
                    gate.announce(delay=0)
                    self.assertEqual(len(gate.status()['operations']), 1)
                    self.assertFalse(gate.try_quiesce())
                finally:
                    finish.set()
                    await asyncio.gather(*pending)
                self.assertTrue(gate.try_quiesce())
                self.assertEqual(operations.read(response.json()['operation_id'])['status'], 'succeeded')
        asyncio.run(scenario())

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


class UnscheduleTests(unittest.TestCase):
    """人已在后台手删之后来登记；系统只负责核实，不自己去删。"""

    def setUp(self):
        self.fixture = fixtures.ApprovalTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.f = self.fixture
        self.f.allow_fixture_evidence()

    def schedule(self, remote_id='facebook=987654'):
        async def execute(post, when, **kwargs):
            base = workflow.new_attempt(post, when, ui_timezone=kwargs['ui_timezone'])
            result = journal.transition(base, journal.STATUS_SCHEDULED,
                                        recorded_at=fixtures.NOW.isoformat(), remote_id=remote_id)
            journal.append(config.cfg().state_dir, result)
            return workflow.AttemptOutcome(0, result, 'fixture-confirmed')
        self.assertTrue(self.f.approve(
            inventory_reader=AsyncMock(return_value=self.f.inventory), executor=execute)['ok'])
        return journal.scheduled_record_for_refs(
            config.cfg().state_dir, (journal.source_ref('facebook', self.f.source['post_id']),))

    def inventory(self, *cards, loaded=True):
        from datetime import date
        return bs.RemoteSlotInventory(tuple(card.at for card in cards), 'America/Los_Angeles', date(2026, 9, 1),
                                      date(2026, 10, 31), cards=cards, cards_loaded=loaded)

    def unschedule(self, inventory, reason='业务改主意了，已在后台删除'):
        return asyncio.run(records.unschedule(
            self.f.account, self.f.source, reason=reason,
            inventory_reader=AsyncMock(return_value=inventory), now=fixtures.NOW))

    def test_the_card_disappearing_from_a_complete_read_releases_the_dedupe(self):
        row = self.schedule()
        self.assertIsNotNone(row)
        task_id = self.f.fixture.task_id
        frozen = review.latest(self.f.account)[self.f.source['post_id']]
        record = operations.start(task_id, platform='facebook',
                                  snapshot_id=frozen['snapshot_id'], scheduled_at=fixtures.TARGET)
        operations.finish(record['operation_id'], status=operations.SUCCEEDED, message='fixture')
        self.assertIsNotNone(self.f.fixture.client.get(self.f.fixture.url).json()['publish_operation'])
        result = self.unschedule(self.inventory())
        self.assertEqual(result['status'], 'pending_review')
        self.assertEqual(result['remote_ids'], ['987654'])
        ref = journal.source_ref('facebook', self.f.source['post_id'])
        self.assertIsNone(journal.scheduled_record_for_refs(config.cfg().state_dir, (ref,)))
        self.assertNotIn(ref, journal.scheduled_source_refs(config.cfg().state_dir))
        self.assertEqual(review.latest(self.f.account)[self.f.source['post_id']]['status'],
                         'pending_review')
        self.assertIsNone(self.f.fixture.client.get(self.f.fixture.url).json()['publish_operation'])
        # ⛔ 原始 scheduled 行必须还在：它是这篇曾经提交过的证据。
        statuses = [item['status'] for item in journal.load(config.cfg().state_dir)]
        self.assertEqual(statuses[-2:], [journal.STATUS_SCHEDULED, journal.STATUS_CANCELLED_REMOTE])

    def test_a_card_still_visible_in_the_backend_refuses_the_registration(self):
        row = self.schedule()
        card = bs.RemotePlannerCard(fixtures.TARGET, ('facebook',), (('facebook', '987654'),),
                                    'still there', 'sha', 'scheduled', placement='feed')
        with self.assertRaisesRegex(review.ReviewConflict, '仍能读到'):
            self.unschedule(self.inventory(card))
        self.assertIsNotNone(journal.scheduled_record_for_refs(
            config.cfg().state_dir, (journal.source_ref('facebook', self.f.source['post_id']),)))

    def test_an_incomplete_month_read_is_not_evidence_of_deletion(self):
        """⛔ 没读完就「没看见」，不等于「不存在」。"""
        self.schedule()
        with self.assertRaisesRegex(review.ReviewConflict, '没有读完整'):
            self.unschedule(self.inventory(loaded=False))
        from datetime import date
        narrow = bs.RemoteSlotInventory((), 'America/Los_Angeles', date(2026, 9, 1),
                                        date(2026, 9, 2), cards=(), cards_loaded=True)
        with self.assertRaisesRegex(review.ReviewConflict, '没有覆盖'):
            self.unschedule(narrow)

    def test_a_schedule_without_a_remote_id_cannot_be_verified_away(self):
        self.schedule(remote_id='')
        with self.assertRaisesRegex(review.ReviewConflict, '没有远端 ID'):
            self.unschedule(self.inventory())

    def test_registering_requires_a_reason_and_an_existing_schedule(self):
        with self.assertRaisesRegex(review.ReviewConflict, '没有可撤销的排期'):
            self.unschedule(self.inventory())
        self.schedule()
        with self.assertRaises(review.ReviewValidationError):
            self.unschedule(self.inventory(), reason='   ')

    def test_only_the_cancelled_attempt_is_released_not_the_whole_source(self):
        """同一来源另有一次未撤销的 scheduled，照样要拦住。"""
        first = self.schedule()
        self.unschedule(self.inventory())
        ref = journal.source_ref('facebook', self.f.source['post_id'])
        other = journal.transition(journal.attempt_from_row(dict(first, attempt_id='other-attempt')),
                                   journal.STATUS_SCHEDULED, recorded_at=fixtures.NOW.isoformat())
        journal.append(config.cfg().state_dir, other)
        blocking = journal.scheduled_record_for_refs(config.cfg().state_dir, (ref,))
        self.assertEqual(blocking['attempt_id'], 'other-attempt')
        self.assertIn(ref, journal.scheduled_source_refs(config.cfg().state_dir))


class OverduePublicationTests(unittest.TestCase):
    """到点之后有没有真的发出去，只能看远端观测，不能看时钟。"""

    def setUp(self):
        self.fixture = fixtures.ApprovalTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.f = self.fixture
        self.f.allow_fixture_evidence()
        self.state = config.cfg().state_dir
        async def execute(post, when, **kwargs):
            base = workflow.new_attempt(post, when, ui_timezone=kwargs['ui_timezone'])
            result = journal.transition(base, journal.STATUS_SCHEDULED,
                                        recorded_at=fixtures.NOW.isoformat(),
                                        remote_id='facebook=987654')
            journal.append(self.state, result)
            return workflow.AttemptOutcome(0, result, 'fixture-confirmed')
        self.f.approve(inventory_reader=AsyncMock(return_value=self.f.inventory), executor=execute)

    def test_nothing_is_overdue_before_its_moment_plus_grace(self):
        self.assertEqual(observations.overdue(self.state, now=fixtures.TARGET), [])
        just_after = fixtures.TARGET + timedelta(minutes=59)
        self.assertEqual(observations.overdue(self.state, now=just_after), [])

    def test_a_passed_moment_with_no_observation_is_reported_as_not_yet_seen(self):
        late = observations.overdue(self.state, now=fixtures.TARGET + timedelta(hours=2))
        self.assertEqual(len(late), 1)
        self.assertEqual(late[0]['platform'], 'facebook')
        self.assertEqual(late[0]['remote_id'], 'facebook=987654')

    def test_an_observed_publication_clears_it(self):
        from datetime import date
        card = bs.RemotePlannerCard(fixtures.TARGET, ('facebook',), (('facebook', '987654'),),
                                    'live', 'sha', 'published', placement='feed')
        inventory = bs.RemoteSlotInventory((), 'America/Los_Angeles', date(2026, 9, 1),
                                           date(2026, 10, 31), cards=(card,), cards_loaded=True)
        observations.record(self.state, inventory, fixtures.TARGET + timedelta(minutes=5))
        self.assertEqual(observations.overdue(self.state, now=fixtures.TARGET + timedelta(hours=2)), [])


class LocalScheduleTests(unittest.TestCase):
    """月历本地图层读的是审校账本和发布账本，不读远端。"""

    def setUp(self):
        self.fixture = fixtures.ApprovalTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.f = self.fixture

    def entries(self):
        return local_schedule.entries(account_dirs=[self.f.account])

    def test_nothing_local_before_anyone_confirms_anything(self):
        self.assertEqual(self.entries(), [])

    def test_a_frozen_post_shows_up_with_no_time_yet(self):
        locked = self.f.lock_content()
        entry, = self.entries()
        self.assertEqual(entry['kind'], local_schedule.LOCKED)
        self.assertIsNone(entry['at'])
        self.assertEqual(entry['snapshot_id'], locked['snapshot_id'])
        self.assertEqual(entry['platform'], 'facebook')

    def test_choosing_a_time_moves_it_to_submitting_before_any_readback(self):
        """时刻绑在快照上，此时发布账本里还没有任何尝试。"""
        from publish import snapshots
        locked = self.f.lock_content()
        snapshots.bind_schedule(locked['snapshot_id'], fixtures.TARGET)
        entry, = self.entries()
        self.assertEqual(entry['kind'], local_schedule.SUBMITTING)
        self.assertEqual(entry['at'], fixtures.TARGET.isoformat())

    def test_a_confirmed_schedule_reads_its_time_and_remote_id_from_the_journal(self):
        self.f.allow_fixture_evidence()
        async def execute(post, when, **kwargs):
            base = workflow.new_attempt(post, when, ui_timezone=kwargs['ui_timezone'])
            result = journal.transition(base, journal.STATUS_SCHEDULED,
                                        recorded_at=fixtures.NOW.isoformat(),
                                        remote_id='facebook=987654')
            journal.append(config.cfg().state_dir, result)
            return workflow.AttemptOutcome(0, result, 'fixture-confirmed')
        self.assertTrue(self.f.approve(
            inventory_reader=AsyncMock(return_value=self.f.inventory), executor=execute)['ok'])
        entry, = self.entries()
        self.assertEqual(entry['kind'], local_schedule.SCHEDULED)
        self.assertEqual(entry['at'], fixtures.TARGET.isoformat())
        self.assertEqual(entry['remote_id'], 'facebook=987654')

    def test_a_released_freeze_leaves_nothing_on_the_calendar(self):
        locked = self.f.lock_content()
        web_approval.approval.unlock(
            self.f.account, self.f.source, now=fixtures.NOW,
            source_text_sha256=self.f.params['source_text_sha256'],
            review_revision=locked['revision'])
        self.assertEqual(self.entries(), [])

    def test_a_broken_review_ledger_fails_instead_of_reading_as_empty(self):
        """⛔ 坏账本不能当空文件；当成「本地没有排期」会让人以为槽位空着。"""
        self.f.lock_content()
        path = self.f.account / 'review_items.jsonl'
        with path.open('ab') as handle:
            handle.write(b'\n{"broken":')
        with self.assertRaises(review.ReviewConflict):
            self.entries()


if __name__ == '__main__':
    unittest.main()
