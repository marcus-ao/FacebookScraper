"""Probe portability and static labels, using isolated files and Chromium only."""
from __future__ import annotations

import asyncio
import io
import json
import shutil
import sys
import tempfile
import unittest
from datetime import timedelta
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
from tools import publish_post


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
                self.assertIsNone(constraints.max_images)
                self.assertIsNone(window.max_ahead)
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

    def test_empty_observations_do_not_block_g1_but_wrong_browser_role_does(self):
        self.save_paths(r'D:\old\publish_probe_fixture_screenshots\masked.png')
        self.data['observations'] = {}
        self.path.write_text(json.dumps(self.data), encoding='utf-8')
        self.config._d['publish']['ui_timezone'] = 'Asia/Shanghai'
        with patch.object(compose, 'cfg', return_value=self.config):
            limits, window = compose.verified_constraints_from_config('instagram')
            self.assertIsNone(limits.max_caption_length)
            self.assertIsNone(limits.min_aspect_ratio)
            self.assertEqual(window.min_ahead, timedelta(0))
            self.assertIsNone(window.max_ahead)
            self.assertEqual(window.ui_timezone, 'Asia/Shanghai')
            with patch.object(publish_post, 'cfg', return_value=self.config):
                self.assertEqual(publish_post._resolve_ui_timezone(True), 'Asia/Shanghai')
            compose._match_probe_measurements(self.data, limits, window)
            with self.assertRaisesRegex(compose.ComposeError, '实测数字'):
                compose._match_probe_measurements(self.data,
                    compose.InstagramConstraints(self.path.name, max_images=100), None)
        self.data['profile_dir'] = str(Path(self.temp.name) / 'other-profile')
        self.path.write_text(json.dumps(self.data), encoding='utf-8')
        with patch.object(compose, 'cfg', return_value=self.config):
            with self.assertRaisesRegex(compose.ComposeError, '专用 profile'):
                compose.verified_constraints_from_config('instagram')

    def test_copied_probe_keeps_publish_role_across_host_usernames(self):
        self.data['profile_dir'] = r'C:\Users\another-user\publish-profile'
        self.path.write_text(json.dumps(self.data), encoding='utf-8')
        with patch.object(compose, 'cfg', return_value=self.config):
            self.assertEqual(compose.require_probe_evidence()['cdp_port'], 9223)

    def test_missing_state_preflight_does_not_create_a_directory(self):
        missing = Path(self.temp.name) / 'absent-state'
        self.config._d['paths']['state'] = str(missing)
        with patch.object(compose, 'cfg', return_value=self.config):
            with self.assertRaises(compose.ComposeError):
                compose.require_probe_evidence()
        self.assertFalse(missing.exists())

    def test_signal_report_does_not_request_optional_measurements(self):
        self.data['observations'] = {'instagram_max_images': '10'}
        self.path.write_text(json.dumps(self.data), encoding='utf-8')
        before = self.path.read_bytes()
        output = io.StringIO()
        with redirect_stdout(output):
            result = probe_signals.main(['--report', str(self.path)])
        self.assertEqual(result, 0)
        self.assertIn('不阻塞', output.getvalue())
        self.assertNotIn('--fill-notes', output.getvalue())
        self.assertEqual(self.path.read_bytes(), before)

    def test_facebook_detail_can_supply_shared_structure_without_claiming_instagram_receipt(self):
        template = self.data['snapshots'][0]
        self.data['snapshots'] = [dict(template,
            page_url='https://business.facebook.com/latest/content_calendar',
            semantic_items=[
                {'role': 'heading', 'accessible_name': 'September'},
                {'role': 'heading', 'accessible_name': '2026'},
                {'role': 'link', 'accessible_name': 'Sample caption September 30, 2026 5:30 PM'},
                {'role': 'dialog', 'accessible_name': "Post details ID: 123456789 Facebook's Feed Test Page Sample caption"},
            ])]
        self.path.write_text(json.dumps(self.data), encoding='utf-8')
        result = probe_signals.derive_planner_card(self.data, self.path.name,
            'Test Page', 'test.ig', 'Sample caption')
        self.assertTrue(result.ok, result.detail)
        spec = result.spec
        self.assertEqual(spec.attributes['instagram_detail_basis'], 'shared_facebook_structure')
        self.assertEqual(evidence.verify_signal(spec, self.state), (True, ''))
        self.assertEqual(evidence._channel_dialogs(self.data['snapshots'], spec, 'instagram'), [])
        self.data['snapshots'][0]['semantic_items'].pop()
        self.path.write_text(json.dumps(self.data), encoding='utf-8')
        self.assertFalse(evidence.verify_signal(spec, self.state)[0])


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
