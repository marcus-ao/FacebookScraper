"""人工优化编排：测试不调用网络，不使用真实归档。"""
import hashlib
import io
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests_web_review as fixtures
from localize import images as images
from core import review, translated
from core.config import cfg
from pipeline import engine, refinement
from publish import compose


class RefinementTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.WebReviewTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.account, self.source = self.fixture.account, self.fixture.source
        self.executor = Mock()
        # Web夹具译文是无usage的展示数据；真实预算计算由paid_requests测试覆盖。
        patch.object(engine, 'budget_preflight', return_value=None).start()

    def submit(self, kind='text', **kwargs):
        return refinement.submit(self.account, self.source, kind=kind, instruction='更自然，保留型号',
            source_text_sha256=translated.source_text_sha256(self.source['text']),
            human_revision=None, review_revision=None, media_index=0 if kind == 'image' else None,
            executor=self.executor, **kwargs)

    def test_double_click_does_not_start_another_paid_job(self):
        first = self.submit()
        with self.assertRaises(review.ReviewConflict):
            self.submit()
        self.executor.submit.assert_called_once()
        self.assertEqual(refinement.job_result(first['job_id'])['status'], 'pending')

    def test_budget_rejection_does_not_queue_a_job(self):
        with patch.object(engine, 'budget_preflight', side_effect=engine.BudgetStopped('budget')):
            with self.assertRaises(engine.BudgetStopped):
                self.submit()
        self.executor.submit.assert_not_called()
        self.assertFalse(refinement.latest())

    def test_executor_failure_closes_job_and_allows_a_new_request(self):
        self.executor.submit.side_effect = RuntimeError('executor stopped')
        with self.assertRaises(review.ReviewConflict):
            self.submit()
        job = next(iter(refinement.latest().values()))
        self.assertEqual(job['status'], 'failed')
        self.assertEqual(job['error'], 'executor_unavailable')
        self.executor.submit.side_effect = None
        next_job = self.submit()
        self.assertNotEqual(next_job['job_id'], job['job_id'])

    def test_fourth_image_refinement_is_rejected(self):
        for _ in range(3):
            row = self.submit('image')
            refinement._append(dict(row, status='succeeded'))
        with self.assertRaisesRegex(review.ReviewConflict, '次数上限'):
            self.submit('image')
        self.assertEqual(self.executor.submit.call_count, 3)

    def test_source_change_before_worker_means_zero_model_calls(self):
        row = self.submit()
        self.fixture.source['text'] = 'Changed source'
        self.fixture.write_source()
        translator = Mock()
        result = refinement.execute(row, self.source, translator=translator)
        self.assertEqual(result['status'], 'failed')
        translator.translate.assert_not_called()

    def test_prompt_change_while_queued_stops_before_model_call(self):
        row = self.submit()
        self.assertEqual(row['prompt_version'], translated.PROMPT_VERSION)
        before = (self.account / 'translated.jsonl').read_bytes()
        translator = Mock()
        with patch.object(translated, 'PROMPT_VERSION', translated.PROMPT_VERSION + 1):
            result = refinement.execute(row, self.source, translator=translator)
            public = refinement.job_result(row['job_id'])
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(public['prompt_current'])
        translator.translate.assert_not_called()
        self.assertEqual((self.account / 'translated.jsonl').read_bytes(), before)
        self.assertFalse((cfg().state_dir / 'paid_requests.jsonl').exists())

    def test_legacy_job_without_prompt_version_is_not_current_or_replayed(self):
        row = self.submit()
        row.pop('prompt_version')
        refinement._append(row)
        self.assertFalse(refinement.job_result(row['job_id'])['prompt_current'])
        translator = Mock()
        self.assertEqual(refinement.execute(row, self.source, translator=translator)['status'], 'failed')
        translator.translate.assert_not_called()

    def test_text_refinement_is_machine_candidate_and_keeps_human_version(self):
        human = translated.append_human_translation(self.account / 'translated_human.jsonl', self.source,
            'Von Hand. #Neakasa', expected_revision=None)
        row = refinement.submit(self.account, self.source, kind='text', instruction='语气轻松',
            source_text_sha256=translated.source_text_sha256(self.source['text']),
            human_revision=human['revision'], review_revision=None, executor=self.executor)
        translator = SimpleNamespace(translate=lambda text, system: 'Modell. #Neakasa')
        result = refinement.execute(row, self.source, translator=translator)
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['prompt_version'], row['prompt_version'])
        self.assertTrue(refinement.job_result(row['job_id'])['prompt_current'])
        human_before = (self.account / 'translated_human.jsonl').read_bytes()
        with patch.object(translated, 'PROMPT_VERSION', translated.PROMPT_VERSION + 1):
            polled = self.fixture.client.get('/api/refinements/jobs/' + row['job_id']).json()
            self.assertFalse(polled['prompt_current'])
            self.assertEqual(polled['prompt_version'] + 1, polled['current_prompt_version'])
            self.assertEqual(polled['body_de'], 'Modell.')
        self.assertEqual((self.account / 'translated_human.jsonl').read_bytes(), human_before)
        machine = translated.load_translated(self.account / 'translated.jsonl')[self.source['post_id']]
        self.assertEqual(machine['refine_instruction'], '语气轻松')
        self.assertEqual(translated.load_human_translated(self.account / 'translated_human.jsonl')[
            self.source['post_id']]['text_de'], 'Von Hand. #Neakasa')

    def test_image_refinement_keeps_original_and_uses_human_text_with_versioned_output(self):
        human = translated.append_human_translation(self.account / 'translated_human.jsonl', self.source,
            'Ein von Hand bearbeiteter Text. #Neakasa', expected_revision=None)
        source_before = (self.fixture.post_dir / '01.jpg').read_bytes()
        row = refinement.submit(self.account, self.source, kind='image', instruction='保留原图排版',
            source_text_sha256=translated.source_text_sha256(self.source['text']),
            human_revision=human['revision'], review_revision=None, media_index=0, executor=self.executor)
        editor = Mock()
        # 编辑器假结果；验证器返回已验证字节，单独的K5测试覆盖实际图片契约。
        image = Image.new('RGB', (1088, 1088), 'blue')
        ImageDraw.Draw(image).rectangle((100, 100, 900, 900), fill='white')
        buffer = io.BytesIO()
        image.save(buffer, 'JPEG')
        validated = images.ValidatedImage(buffer.getvalue(), 1088, 1088, 'JPEG', 1, 0.02)
        editor.edit.return_value = images.EditResult('', 'gpt-image-2', {}, 'response')
        editor.paid_request_id = ''
        with patch.object(images, 'validate_output', return_value=validated):
            result = refinement.execute(row, self.source, editor=editor)
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual((self.fixture.post_dir / '01.jpg').read_bytes(), source_before)
        self.assertIn('_v' + row['job_id'], result['out_path'])
        output = self.account / result['out_path']
        self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(), hashlib.sha256(validated.data).hexdigest())
        jobs, _, stats = images.build_jobs(images.Settings(), self.account, [self.source])
        self.assertFalse(jobs, '人工稿生成的图片不能被后台按旧机器稿再次付费生成')
        self.assertEqual(stats.skipped_current, 1)
        machine = translated.load_translated(self.account / 'translated.jsonl')[self.source['post_id']]
        pairs = images.review_image_pairs(self.account, self.source,
            translated.image_translation(self.source, machine, human))
        self.assertEqual(self.account / pairs[0].localized_rel, output)
        post = compose.compose_post(self.source['post_id'], datetime.now(timezone.utc),
                                    archive_root=cfg().archive_dir, account=self.account.name,
                                    warning_sink=None)
        self.assertEqual(post.image_paths[0], output)
        ledger = [json.loads(line) for line in (self.account / 'images_de.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual(ledger[-1]['refine_instruction'], row['instruction'])


if __name__ == '__main__':
    unittest.main()
