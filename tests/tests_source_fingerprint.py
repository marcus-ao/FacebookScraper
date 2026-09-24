"""Fingerprint compatibility through real isolated archive writes, never live calls."""
import copy
import json
import sys
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests_paid_consent import ConsentFixture
from core import config, paid_consent, paid_requests, review, store
from pipeline import approval, engine
from publish import compose, snapshots

OLD_URL = 'https://fixture.fbcdn.net/photo.jpg?stp=dst-jpg_s1080&oh=old&oe=123&_nc_sid=one'
NEW_URL = 'https://fixture.fbcdn.net/photo.jpg?stp=dst-jpg_s1080&oh=new&oe=456&_nc_sid=two&_nc_cat=10'


class FingerprintTests(ConsentFixture):
    def setUp(self):
        super().setUp()
        body = (self.post_dir/'01.jpg').read_bytes()
        self.archive = store.Archive(self.root/'stored', 'fa_neakasaofficial')
        config.cfg()._d['paths']['archive'] = str(self.root/'stored')
        self.post = store.Post(post_id='1234567890', platform='facebook', account='neakasaofficial',
            text=self.source['text'], created_at=self.source['created_at'], owner='thirdparty',
            coauthors=['neakasaofficial'], media_complete=True,
            media=[store.Media(kind='image', url=OLD_URL)])
        self.archive.save_media(self.post, 0, body, store.image_facts(body))
        self.assertTrue(self.archive.append(self.post))
        self.account = self.archive.base
        self.source, self.post_dir = store.read_post_truth(self.account, self.post.to_row())

    def refresh(self):
        fresh = copy.deepcopy(self.post)
        fresh.media[0].url = NEW_URL
        self.assertTrue(self.archive.append(fresh), 'the real locator_refresh branch must write')
        self.source, _ = store.read_post_truth(self.account, fresh.to_row())
        self.assertEqual(self.source['media'][0]['url'], NEW_URL)

    def test_persisted_signature_refresh_keeps_permission_and_new_snapshot_valid(self):
        granted = self.grant()
        self.assertEqual(granted['source_fingerprint_version'], 2)
        ledger = (self.account/'paid_consent.jsonl').read_bytes()
        original = self.account/self.source['media'][0]['local_path']
        before = original.read_bytes()
        post = compose.DePost(self.source['post_id'], 'facebook', self.source['account'],
            self.source['text'], 'Deutsch', 'Deutsch', datetime(2026,9,25,12,tzinfo=timezone.utc),
            (original,), ('original_confirmed',), 7, 'thirdparty', None, (), self.post_dir)
        frozen, _, directory = snapshots.freeze(post, self.source, expected_fingerprint=engine._publish_fingerprint(post))
        frozen_files = {p.name:p.read_bytes() for p in directory.iterdir()}
        self.refresh()
        self.assertEqual(original.read_bytes(), before)
        self.assertTrue(paid_consent.is_current(self.account, self.source))
        self.assertEqual(paid_consent.fingerprint(self.source,self.account), granted['source_fingerprint'])
        bound = approval._bind(post, frozen.snapshot_id, self.source, self.account)
        self.assertEqual(bound.image_paths[0].read_bytes(), before)
        self.assertEqual(bound.source_fingerprint_version, 2)
        self.assertEqual((self.account/'paid_consent.jsonl').read_bytes(), ledger)
        for name, content in frozen_files.items():
            if name != 'snapshot.json':
                self.assertEqual((directory/name).read_bytes(), content)

    def test_legacy_grant_validates_original_digest_before_normalized_comparison(self):
        legacy = self.grant()
        legacy.pop('source_fingerprint_version')
        legacy['source_identity'] = paid_consent._identity(self.source, self.account, version=1)
        legacy['source_fingerprint'] = paid_consent._digest(legacy['source_identity'])
        path = self.account/'paid_consent.jsonl'
        raw = (json.dumps(legacy)+'\n').encode()
        path.write_bytes(raw)
        self.refresh()
        self.assertTrue(paid_consent.is_current(self.account, self.source))
        self.assertEqual(path.read_bytes(), raw)
        legacy['source_identity']['media'][0]['url'] = NEW_URL
        path.write_bytes((json.dumps(legacy)+'\n').encode())
        self.assertFalse(paid_consent.is_current(self.account,self.source), 'normalization must not hide a bad legacy digest')

    def test_content_identity_and_non_signature_parameters_still_invalidate(self):
        original = paid_consent.fingerprint(self.source, self.account)
        for key, value in (('text', self.source['text']+' '), ('owner','other'), ('coauthors',['other']),
                           ('media_complete',False)):
            changed = dict(self.source, **{key:value})
            self.assertNotEqual(paid_consent.fingerprint(changed,self.account), original, key)
        for url in (NEW_URL.replace('s1080','s640'), NEW_URL.replace('photo.jpg','other.jpg'),
                    NEW_URL.replace('fixture.fbcdn.net','other.fbcdn.net'), NEW_URL+'&other=1'):
            changed = copy.deepcopy(self.source)
            changed['media'][0]['url'] = url
            self.assertNotEqual(paid_consent.fingerprint(changed,self.account), original, url)
        changed = copy.deepcopy(self.source)
        changed['media'].append(dict(changed['media'][0], url=OLD_URL.replace('photo','second')))
        before = paid_consent.fingerprint(changed,self.account)
        changed['media'].reverse()
        self.assertNotEqual(paid_consent.fingerprint(changed,self.account), before)
        (self.account/self.source['media'][0]['local_path']).write_bytes(b'changed actual bytes')
        self.assertNotEqual(paid_consent.fingerprint(self.source,self.account), original)

    def test_legacy_snapshot_keeps_its_own_version_without_rewriting_files(self):
        original = self.account/self.source['media'][0]['local_path']
        post = compose.DePost(self.source['post_id'], 'facebook', self.source['account'], self.source['text'],
            'Deutsch','Deutsch',datetime(2026,9,25,12,tzinfo=timezone.utc), (original,), ('original_confirmed',),
            7, 'thirdparty', None, (), self.post_dir)
        frozen, _, directory = snapshots.freeze(post,self.source,expected_fingerprint=engine._publish_fingerprint(post))
        path = directory/'snapshot.json'
        metadata = json.loads(path.read_text('utf-8'))
        metadata.pop('source_fingerprint_version')
        metadata['source_fingerprint'] = paid_consent.fingerprint(self.source,self.account,version=1)
        path.write_text(json.dumps(metadata), encoding='utf-8', newline='')
        bound = approval._bind(post, frozen.snapshot_id, self.source,self.account)
        self.assertEqual(bound.source_fingerprint_version, 1)
        old_bytes = path.read_bytes()
        snapshots.ensure(bound)
        self.assertEqual(path.read_bytes(),old_bytes)
        self.refresh()
        with self.assertRaisesRegex(review.ReviewConflict, '来源指纹.*旧版.*重新冻结'):
            approval._bind(post, frozen.snapshot_id, self.source,self.account)

    def test_legacy_uncertain_fee_still_blocks_after_signature_refresh(self):
        self.grant()
        controller = paid_requests.RequestController(config.cfg().state_dir, preflight=lambda:None)
        args = dict(stage='image', job_key='legacy', source_ref='facebook:'+self.source['post_id'],
            media_index=0, model='fixture', usage_getter=lambda:{}, usage_errors=lambda _:['missing'],
            usage_cost=lambda _:None)
        with self.assertRaises(paid_requests.PaidRequestBlocked):
            controller.run(**args, request=lambda: (_ for _ in ()).throw(RuntimeError('lost reply')))
        before = (config.cfg().state_dir/'paid_requests.jsonl').read_bytes()
        self.refresh()
        self.assertTrue(paid_consent.is_current(self.account,self.source))
        request = Mock()
        with self.assertRaises(paid_requests.PaidRequestBlocked):
            controller.run(**dict(args,job_key='new-normalized-job'), request=request)
        request.assert_not_called()
        self.assertEqual((config.cfg().state_dir/'paid_requests.jsonl').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
