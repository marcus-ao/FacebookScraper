"""Channel-scoped preflight and synchronous evidence reuse with live invalidation."""
import json
import sys
import tempfile
import unittest
from time import perf_counter
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.config import cfg
from publish import capabilities, compose, evidence
from publish.selectors import EvidenceSignal
from publish_fixtures import verified_probe_config


class CapabilityTests(unittest.TestCase):
    def test_probe_scope_reuses_screenshot_paths_and_configuration_is_always_checked(self):
        with tempfile.TemporaryDirectory() as raw:
            config = verified_probe_config(cfg(), Path(raw) / 'state')
            with patch.object(compose, 'cfg', return_value=config):
                evidence.clear_dump_cache()
                measurements = []
                for scoped in (False, True):
                    from contextlib import nullcontext
                    with (evidence.validation_scope() if scoped else nullcontext()), \
                            patch.object(evidence, 'assert_physical_direct_path',
                                         wraps=evidence.assert_physical_direct_path) as paths:
                        start = perf_counter()
                        for _ in range(10):
                            compose.require_probe_evidence()
                        measurements.append((paths.call_count, (perf_counter() - start) * 1000))
                self.assertLess(measurements[1][0], measurements[0][0])
                print('probe paths/cost, 10 calls, separate vs same synchronous scope: ' + repr(measurements))
                with evidence.validation_scope():
                    compose.require_probe_evidence()
                    config._d['publish']['ui_constraints_verified'] = False
                    with self.assertRaises(compose.ComposeError):
                        compose.require_probe_evidence()

    def test_single_channel_checks_skip_other_controls_and_window_but_activation_checks_both(self):
        with tempfile.TemporaryDirectory() as raw, ExitStack() as stack:
            config = verified_probe_config(cfg(), Path(raw) / 'state')
            stack.enter_context(patch.object(capabilities, 'cfg', return_value=config))
            for name in ('require_account_context_evidence', 'require_submission_evidence', 'require_readback_evidence'):
                stack.enter_context(patch.object(capabilities.bs, name))
            stack.enter_context(patch.object(capabilities.month_inventory, 'require'))
            calls = []
            stack.enter_context(patch.object(capabilities.channels, 'require_independent_channel_evidence',
                                            side_effect=lambda channels: calls.append(('controls', channels))))
            stack.enter_context(patch.object(capabilities.planning, 'configured_window',
                                            side_effect=lambda channel: calls.append(('window', channel))))
            capabilities.require('facebook')
            self.assertEqual(calls, [('controls', ('facebook',)), ('window', 'facebook')])
            calls.clear()
            stack.enter_context(patch.object(capabilities, 'acceptance', return_value={}))
            self.assertEqual(capabilities.activation_blockers(config.state_dir), ())
            self.assertEqual(calls, [('controls', ('facebook',)), ('controls', ('instagram',)),
                                    ('window', 'facebook'), ('window', 'instagram')])

    def test_scoped_evidence_reuses_checks_but_rechecks_changed_dump_or_removed_screenshot(self):
        with tempfile.TemporaryDirectory() as raw:
            config = verified_probe_config(cfg(), Path(raw) / 'state')
            directory = config.state_dir
            path = directory / 'publish_probe_fixture.json'
            data = json.loads(path.read_text('utf-8'))
            data['snapshots'][0].update(page_url='https://example.test/planner',
                semantic_items=[{'role': 'status', 'visible_text': 'Ready'}])
            path.write_text(json.dumps(data), encoding='utf-8')
            spec = EvidenceSignal(key='fixture_ready', step='G6', kind='semantic', surface='example.test/planner',
                source_dump=path.name, sequences=(1,), breaks_when='fixture', role='status', name='Ready')
            with evidence.validation_scope(), patch.object(evidence, '_signal_hits', wraps=evidence._signal_hits) as hits:
                self.assertEqual(evidence.verify_signal(spec, directory), (True, ''))
                self.assertEqual(evidence.verify_signal(spec, directory), (True, ''))
                self.assertEqual(hits.call_count, 1)
                data['snapshots'][0]['semantic_items'][0]['visible_text'] = 'Changed'
                path.write_text(json.dumps(data), encoding='utf-8')
                self.assertIs(evidence.verify_signal(spec, directory)[0], False)
                data['snapshots'][0]['semantic_items'][0]['visible_text'] = 'Ready'
                path.write_text(json.dumps(data), encoding='utf-8')
                self.assertEqual(evidence.verify_signal(spec, directory), (True, ''))
                Path(data['snapshots'][0]['screenshot']).unlink()
                self.assertIs(evidence.verify_signal(spec, directory)[0], False)
            self.assertIs(evidence.verify_signal(spec, directory)[0], False)


if __name__ == '__main__':
    unittest.main()
