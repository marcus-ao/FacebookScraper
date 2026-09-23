"""Synthetic byte/observation contracts, never evidence of a live scheduled layout."""
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from publish import media
from publish.business_suite import PublishStepError


class ScheduledMediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paths, self.bodies = [], []
        for index in range(2):
            picture = Image.new('RGB', (240, 240), 'white')
            ImageDraw.Draw(picture).rectangle((index * 120, 0, index * 120 + 90, 239), fill='black')
            path = self.root / f'{index}.png'
            picture.save(path)
            encoded = io.BytesIO()
            picture.save(encoded, format='JPEG', quality=85)
            self.paths.append(path)
            self.bodies.append(encoded.getvalue())

    def test_remote_comparison_uses_actual_bytes_and_preserves_duplicate_positions(self):
        result = media.compare_ordered(self.paths, self.bodies, surface='排期详情')
        self.assertEqual(result['image_count'], 2)
        self.assertEqual([row['source_sha256'] for row in result['images']],
                         [hashlib.sha256(path.read_bytes()).hexdigest() for path in self.paths])
        self.assertEqual(result['images'][0]['rendered_sha256'], hashlib.sha256(self.bodies[0]).hexdigest())
        result = media.compare_ordered([self.paths[0]] * 2, [self.bodies[0]] * 2, surface='排期详情')
        self.assertEqual([row['index'] for row in result['images']], [0, 1])

    def test_invalid_or_ambiguous_remote_images_never_match(self):
        for bodies in ([], self.bodies[:1], self.bodies * 2, self.bodies[::-1],
                       [self.bodies[0]] * 2, [b'corrupt'] * 2, [b''] * 2):
            with self.subTest(count=len(bodies)), self.assertRaises(PublishStepError):
                media.compare_ordered(self.paths, bodies, surface='排期详情')
        similar = self.root / 'similar.png'
        with Image.open(self.paths[0]) as picture:
            picture.putpixel((10, 10), (12, 12, 12))
            picture.save(similar)
        with self.assertRaises(PublishStepError):
            media.compare_ordered([self.paths[0], similar], [self.bodies[0]] * 2, surface='排期详情')

    def test_complete_capture_saves_bytes_before_returning_proof_and_detects_lost_evidence(self):
        from publish import scheduled_media
        capture = scheduled_media.MediaCapture(tuple(self.bodies), {'fixture': 'byte-contract'},
            screenshot=self.paths[0].read_bytes(), complete=True, image_count=2,
            order_basis='isolated_complete_entity', error='')
        binding = {'attempt_id': 'fixture', 'image_sha256': [hashlib.sha256(p.read_bytes()).hexdigest() for p in self.paths]}
        result = scheduled_media.verify_capture(capture, self.paths, self.root / 'proof', binding)
        self.assertTrue(result['remote_images_verified'])
        proof = result['remote_media']
        self.assertTrue(scheduled_media.evidence_intact(proof, binding))
        self.assertFalse(scheduled_media.evidence_intact(proof, dict(binding, attempt_id='other')))
        manifest = json.loads(Path(proof['evidence_file']).read_text('utf-8'))
        self.assertEqual(manifest['remote_media']['image_count'], 2)
        self.assertEqual((Path(proof['evidence_file']).parent / 'image-000.bin').read_bytes(), self.bodies[0])
        (Path(proof['evidence_file']).parent / 'image-000.bin').unlink()
        self.assertFalse(scheduled_media.evidence_intact(proof, binding))

    def test_local_count_never_turns_unknown_or_incomplete_capture_into_proof(self):
        from publish import scheduled_media
        capture = scheduled_media.MediaCapture(tuple(self.bodies), {}, screenshot=b'fixture')
        for changed in ({}, {'complete': True}, {'complete': True, 'image_count': True, 'order_basis': 'fixture'},
                        {'complete': True, 'image_count': 1, 'order_basis': 'fixture'},
                        {'complete': True, 'image_count': 2, 'order_basis': ''}):
            with self.subTest(changed=changed):
                result = scheduled_media.verify_capture(replace(capture, **changed), self.paths,
                                                        self.root / 'proof', {})
                self.assertFalse(result['remote_images_verified'])
                self.assertFalse(result['remote_media']['order_verified'])

    def test_mismatch_and_failed_preservation_do_not_claim_media_success(self):
        from publish import scheduled_media
        capture = scheduled_media.MediaCapture(tuple(self.bodies[::-1]), {},
            screenshot=self.paths[0].read_bytes(), complete=True, image_count=2,
            order_basis='isolated_complete_entity', error='')
        result = scheduled_media.verify_capture(capture, self.paths, self.root / 'proof', {})
        self.assertFalse(result['remote_images_verified'])
        self.assertEqual(result['remote_media']['error'], 'comparison_failed')
        with patch('publish.scheduled_media.atomic_write_json', side_effect=OSError('disk full')):
            result = scheduled_media.verify_capture(replace(capture, bodies=tuple(self.bodies)),
                                                    self.paths, self.root / 'proof', {})
        self.assertFalse(result['remote_images_verified'])
        self.assertEqual(result['remote_media']['error'], 'evidence_write_failed')


if __name__ == '__main__':
    unittest.main()
