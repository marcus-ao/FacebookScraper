"""Probe portability and static labels, using isolated files and Chromium only."""
from __future__ import annotations

import asyncio
import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.config import cfg
from publish import compose, evidence
from publish.locator_types import EvidenceSignal, Locator
from publish_fixtures import verified_probe_config
from tools._scaffolding import probe_publish, probe_signals


class ProbeFilesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / 'state'
        self.config = verified_probe_config(cfg(), self.state)
        self.path = self.state / 'publish_probe_fixture.json'
        self.data = json.loads(self.path.read_text('utf-8'))
        self.shot = self.state / 'publish_probe_fixture_screenshots' / 'masked.png'
        evidence.clear_dump_cache()

    def save_paths(self, raw):
        for row in self.data['interactions'] + self.data['snapshots']:
            row['screenshot'] = raw
        self.path.write_text(json.dumps(self.data), encoding='utf-8')
        evidence.clear_dump_cache()

    def test_copied_windows_and_posix_evidence_works_without_rewriting_dump(self):
        for raw in (
            r'D:\Code\FacebookScraper\state\publish_probe_fixture_screenshots\masked.png',
            '/srv/publisher/state/publish_probe_fixture_screenshots/masked.png',
            'publish_probe_fixture_screenshots/masked.png',
        ):
            with self.subTest(raw=raw):
                self.save_paths(raw)
                before = self.path.read_bytes()
                data, detail = evidence.validate_v2_dump(self.path.name, self.state)
                self.assertIsNotNone(data, detail)
                with patch.object(compose, 'cfg', return_value=self.config):
                    constraints, window = compose.verified_constraints_from_config('instagram')
                self.assertEqual(constraints.max_images, 5)
                self.assertEqual(self.path.read_bytes(), before)

    def test_missing_screenshot_invalidates_a_cached_success(self):
        self.assertIsNotNone(evidence.validate_v2_dump(self.path.name, self.state)[0])
        self.shot.unlink()
        self.assertIsNone(evidence.validate_v2_dump(self.path.name, self.state)[0])

    def test_copied_screenshots_also_pass_the_final_publication_causal_chain(self):
        self.save_paths(r'D:\old\publish_probe_fixture_screenshots\masked.png')
        composer = 'business.facebook.com/latest/composer'
        planner = 'business.facebook.com/latest/content_calendar'
        common = {'source_dump': self.path.name, 'step': 'offline', 'breaks_when': 'offline'}
        button = Locator(key='composer_submit_button', surface=composer,
                         role='button', name='Schedule', name_source='visible-text',
                         sequences=(1,), **common)
        account = EvidenceSignal(key='composer_account_context', kind='semantic',
            surface=composer, role='heading', name='Test Page', sequences=(1,),
            attributes={'facebook_account_token': 'Test Page',
                        'facebook_account_regex': '(?P<account>.+)'}, **common)
        success = EvidenceSignal(key='composer_success_signal', kind='semantic',
            surface=composer, role='heading', name='Your post is scheduled', sequences=(2,), **common)
        loaded = EvidenceSignal(key='planner_loaded_signal', kind='semantic',
            surface=planner, role='heading', name='September', sequences=(3,), **common)
        card = EvidenceSignal(key='planner_scheduled_card', kind='semantic',
            surface=planner, role='link', name='Sample caption', sequences=(4,),
            attributes={'entry_probe_text': 'Sample caption', 'entry_role': 'link',
                        'date_format': '%Y-%m-%d', 'time_format': '%H:%M',
                        'datetime_regex': r'(?P<date>\d{4}-\d{2}-\d{2}) (?P<time>\d{2}:\d{2})'}, **common)
        click = self.data['interactions'][0]
        click.update(evidence_order=2, page_url='https://' + composer,
                     target={'role': 'button', 'accessible_name': 'Schedule'})
        self.data['interactions'] = [click]
        template = self.data['snapshots'][0]
        snapshots = []
        for spec, order, text in ((account, 1, account.name), (success, 3, success.name),
                                 (loaded, 4, loaded.name),
                                 (card, 5, 'Sample caption 2026-09-30 17:30')):
            snapshots.append(dict(template, sequence=spec.sequences[0], reason='periodic',
                evidence_order=order, page_url='https://' + spec.surface,
                semantic_items=[{'role': spec.role, 'accessible_name': text}]))
        snapshots.append(dict(template, sequence=5, evidence_order=6, reason='final'))
        self.data['snapshots'] = snapshots
        self.path.write_text(json.dumps(self.data), encoding='utf-8')
        passed, detail = evidence.verify_publish_chain(button, account, success, loaded, card, self.state)
        self.assertTrue(passed, detail)
        self.shot.unlink()
        self.assertFalse(evidence.verify_publish_chain(button, account, success, loaded, card, self.state)[0])

    def test_other_sessions_and_traversal_do_not_borrow_a_local_screenshot(self):
        for raw in (
            r'D:\old\other_probe_screenshots\masked.png',
            r'D:\old\publish_probe_fixture_screenshots\..\masked.png',
            r'D:\old\publish_probe_fixture_screenshots\nested\masked.png',
            r'D:\old\publish_probe_fixture_screenshots\masked.png:stream',
        ):
            with self.subTest(raw=raw):
                self.save_paths(raw)
                self.assertIsNone(evidence.validate_v2_dump(self.path.name, self.state)[0])

    def test_original_host_screenshot_cannot_replace_missing_local_bundle(self):
        original = Path(self.temp.name) / 'other-host' / self.shot.parent.name
        original.mkdir(parents=True)
        shutil.copy2(self.shot, original / self.shot.name)
        self.save_paths(str(original / self.shot.name))
        self.shot.unlink()
        self.assertIsNone(evidence.validate_v2_dump(self.path.name, self.state)[0])

    def test_portability_does_not_approve_missing_observations_or_another_profile(self):
        self.save_paths(r'D:\old\publish_probe_fixture_screenshots\masked.png')
        observations = self.data['observations']
        self.data['observations'] = {}
        self.path.write_text(json.dumps(self.data), encoding='utf-8')
        with patch.object(compose, 'cfg', return_value=self.config):
            with self.assertRaisesRegex(compose.ComposeError, '必填观察'):
                compose.verified_constraints_from_config('instagram')
        self.data['observations'] = observations
        self.data['profile_dir'] = str(Path(self.temp.name) / 'other-profile')
        self.path.write_text(json.dumps(self.data), encoding='utf-8')
        with patch.object(compose, 'cfg', return_value=self.config):
            with self.assertRaisesRegex(compose.ComposeError, '专用 profile'):
                compose.verified_constraints_from_config('instagram')

    def test_signal_report_lists_missing_g1_observations_without_modifying_evidence(self):
        self.data['observations'] = {'instagram_max_images': '10'}
        self.path.write_text(json.dumps(self.data), encoding='utf-8')
        before = self.path.read_bytes()
        output = io.StringIO()
        with redirect_stdout(output):
            result = probe_signals.main(['--report', str(self.path)])
        self.assertEqual(result, 0)
        self.assertIn('instagram_max_caption_length', output.getvalue())
        self.assertIn('schedule_min_ahead_seconds', output.getvalue())
        self.assertEqual(self.path.read_bytes(), before)


class ProbeLabelsTest(unittest.TestCase):
    def test_notes_without_a_dump_are_rejected_before_browser_recording(self):
        with self.assertRaises(SystemExit) as raised:
            probe_publish._parse_args(['--set-note', 'instagram_max_images=10'])
        self.assertEqual(raised.exception.code, 2)

    def test_real_browser_keeps_static_control_names_but_never_input_values(self):
        asyncio.run(self.record_labels())

    async def record_labels(self):
        records = []
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.route('**/*', lambda route: route.abort())
                await page.expose_function(probe_publish.BINDING_NAME, lambda raw: records.append(raw))
                await page.set_content('''
                    <div role="group" aria-label="PRIVATE-ANCESTOR">
                      <div id="caption" contenteditable="true" role="combobox"
                        aria-label="Write into the dialogue box to include text with your post.">PRIVATE-CAPTION</div>
                    </div>
                    <span id="date-label" hidden>Date picker</span>
                    <input id="date" aria-labelledby="date-label" placeholder="mm/dd/yyyy" value="PRIVATE-DATE">
                    <label>Set date and time <input id="schedule" type="checkbox" role="switch"></label>
                    <input id="minutes" role="spinbutton" aria-label="minutes" value="PRIVATE-MINUTES">
                    <div role="application" aria-labelledby="time-label">
                      <span id="time-label" hidden>Time input</span>
                      <input id="hours" role="spinbutton" aria-label="hours" value="PRIVATE-HOURS">
                    </div>
                    <div id="unsafe-label" contenteditable="true">PRIVATE-REFERENCE</div>
                    <input id="unsafe" aria-labelledby="unsafe-label" value="PRIVATE-INPUT">
                ''')
                await page.evaluate(probe_publish.INSTALL_FUNCTION, {
                    'sessionId': 'offline-labels', 'bindingName': probe_publish.BINDING_NAME})
                for selector in ('#caption', '#date', '#schedule', '#minutes', '#hours', '#unsafe'):
                    await page.locator(selector).click()
                await page.wait_for_timeout(150)
                payloads = [probe_publish._safe_payload(raw, 'offline-labels') for raw in records]
                clicks = [row for row in payloads if row and row['event_type'] == 'click']
                self.assertEqual(len(clicks), 6)
                self.assertEqual([row['target']['accessible_name'] for row in clicks], [
                    'Write into the dialogue box to include text with your post.',
                    'Date picker', 'Set date and time', 'minutes', 'hours', ''])
                self.assertEqual(clicks[1]['target']['accessible_name_source'], 'aria-labelledby')
                self.assertTrue(any(row['role'] == 'application' and row['accessible_name'] == 'Time input'
                                    for row in clicks[4]['candidates']))
                self.assertNotIn('PRIVATE-', json.dumps(payloads))
            finally:
                await browser.close()


if __name__ == '__main__':
    unittest.main()
