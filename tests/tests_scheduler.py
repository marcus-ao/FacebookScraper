"""单轮调度等待内容就绪并完成通知；隔离归档、假模型和假飞书传输。"""
from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests_web_review as fixtures
from core.config import cfg
from core.feishu import FeishuSettings, Outbox
from core.translated import source_text_sha256
from pipeline import engine, scheduler
from pipeline.service import Runtime


class SchedulerEntryTests(unittest.TestCase):
    def runner(self):
        runner = MagicMock()
        runner.__enter__.return_value = runner
        runner.tick.return_value = []
        runner.preview.return_value = {'jobs': {}}
        return runner

    def test_process_once_delivers_new_ready_before_closing(self):
        fixture = fixtures.WebReviewTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        now = datetime(2026, 9, 15, 2, tzinfo=timezone.utc)  # 上海在岗窗
        with patch.object(engine, 'activation_time', return_value=now):
            runtime = Runtime(process=True, detector=Mock())
        self.addCleanup(runtime.close)
        runtime.clock = lambda: now
        runtime.settings = FeishuSettings(True, 'http://review.invalid')
        runtime.outbox = Outbox(cfg().state_dir / 'feishu_outbox.json', runtime.settings)
        runtime.refresh_calendar = Mock()
        runtime.refresh_hashtags = Mock()
        runtime.heartbeat.tick = Mock()
        runtime.mirror_settings = Mock(enabled=False)
        timeline = []
        runtime.client = Mock()
        runtime.client.send.side_effect = lambda role, _card, delivery_id: (
            timeline.append(('sent', role, delivery_id)) or 'bot-accepted:fixture')
        runtime.client.close.side_effect = lambda: timeline.append('closed')
        early_delivery = Event()
        deliver = runtime._deliver

        def record_delivery(at):
            deliver(at)
            early_delivery.set()

        def complete_content(**_kwargs):
            self.assertTrue(early_delivery.wait(5), '首轮抓取通知未完成')
            source = fixture.source
            engine.append_human_item(cfg().state_dir, engine.HumanItem(
                'single-cycle-ready', 'ready_to_publish', ('facebook:' + source['post_id'],),
                'ready', {'source_text_sha256': source_text_sha256(source['text']),
                          'canonical_ref': 'facebook:' + source['post_id'],
                          'source_created_at': source['created_at']}), now)
            timeline.append('content-ready')
            return 0

        runner = self.runner()

        def tick():
            runtime.processing.request(now, 'facebook', 'delta', 1, {}, image_count=1)
            runtime.maintenance(now)
            return []

        runner.tick.side_effect = tick
        with patch.object(scheduler, 'Scheduler', return_value=runner), \
             patch.object(scheduler, 'datetime', Mock(now=Mock(return_value=now))), \
             patch('pipeline.service.Runtime', return_value=runtime), \
             patch.object(runtime, '_deliver', side_effect=record_delivery), \
             patch.object(engine, 'run', side_effect=complete_content) as process, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(scheduler.main(['--run', '--process', '--once']), 0)
            # 失败版本已先 close；仍等假处理线程退出，避免清理临时目录时产生竞态。
            runtime.processing_future.result(timeout=5)
        ready = [(delivery_id, row) for delivery_id, row in
                 runtime.outbox._load()['deliveries'].items()
                 if row['kind'] == 'ready' and row['status'] == 'sent']
        self.assertEqual(len(ready), 1, timeline)
        sent_ready = ('sent', 'publish', ready[0][0])
        self.assertEqual(timeline.count(sent_ready), 1, timeline)
        self.assertLess(timeline.index('content-ready'), timeline.index(sent_ready))
        self.assertLess(timeline.index(sent_ready), timeline.index('closed'))
        runner.tick.assert_called_once()
        process.assert_called_once()
        runtime.refresh_hashtags.assert_called_once()

    def test_once_without_process_only_drains_existing_delivery(self):
        runner, runtime = self.runner(), Mock()
        runtime.start_processing.return_value = None
        with patch.object(scheduler, 'Scheduler', return_value=runner), \
             patch('pipeline.service.Runtime', return_value=runtime) as factory:
            self.assertEqual(scheduler.main(['--run', '--once']), 0)
        self.assertFalse(factory.call_args.kwargs['process'])
        runtime.delivery_executor.submit.assert_not_called()
        runtime.await_delivery.assert_called_once_with()
        runtime.close.assert_called_once()
        runner.tick.assert_called_once()

    def test_default_preview_never_constructs_runtime(self):
        with patch.object(scheduler, 'Scheduler', return_value=self.runner()), \
             patch('pipeline.service.Runtime') as runtime, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(scheduler.main([]), 0)
        runtime.assert_not_called()

    def test_process_still_requires_explicit_run(self):
        with patch('pipeline.service.Runtime') as runtime, \
             contextlib.redirect_stderr(io.StringIO()), \
             self.assertRaises(SystemExit) as stopped:
            scheduler.main(['--process'])
        self.assertEqual(stopped.exception.code, 2)
        runtime.assert_not_called()

    def test_continuous_run_does_not_wait_for_processing(self):
        runtime = Mock()
        stopped = Event()
        with patch.object(scheduler, 'Scheduler', return_value=self.runner()), \
             patch('pipeline.service.Runtime', return_value=runtime), \
             patch.object(stopped, 'wait', side_effect=lambda _: stopped.set()):
            self.assertEqual(scheduler.main(['--run', '--process'], stop_event=stopped), 0)
        runtime.start_processing.return_value.result.assert_not_called()
        runtime.await_delivery.assert_not_called()
        runtime.close.assert_called_once()

    def test_ctrl_c_returns_zero_and_one_stop_line(self):
        runner = self.runner()
        runner.tick.side_effect = KeyboardInterrupt
        runtime = Mock()
        with patch.object(scheduler, 'Scheduler', return_value=runner), \
             patch('pipeline.service.Runtime', return_value=runtime), \
             patch.dict(os.environ, {}, clear=False), \
             contextlib.redirect_stderr(io.StringIO()) as err:
            os.environ.pop('FBSCRAPER_STOP_NOTICE', None)
            self.assertEqual(scheduler.main(['--run']), 0)
        self.assertEqual(err.getvalue().strip().splitlines(), ['已停止。'])
        runtime.close.assert_called_once()

    def test_ctrl_c_stays_quiet_when_a_parent_will_announce(self):
        runner = self.runner()
        runner.tick.side_effect = KeyboardInterrupt
        with patch.object(scheduler, 'Scheduler', return_value=runner), \
             patch('pipeline.service.Runtime', return_value=Mock()), \
             patch.dict(os.environ, {'FBSCRAPER_STOP_NOTICE': '1'}), \
             contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(scheduler.main(['--run']), 0)
        self.assertNotIn('已停止。', err.getvalue())


if __name__ == '__main__':
    unittest.main()
