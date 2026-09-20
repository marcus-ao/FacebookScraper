"""Isolated capture facts written through the same archive and lifecycle as production."""
from datetime import timedelta
from core.capture_state import CaptureState
from core.config import cfg
from core.store import Archive, Post, Media
from core.media import image_facts
from image_fixtures import image_bytes


def record_capture_rows(runtime, rows, now, platform, *, code=0):
    ledger = CaptureState(cfg().state_dir)
    if not ledger.path.exists():
        ledger.initialize(cfg().archive_dir, cfg()['targets'], 'fixture backfill', now=now - timedelta(days=30))
    account = cfg()['targets'][platform]
    arc = Archive(cfg().archive_dir, platform[:2] + '_' + account)
    found = [fields for event, fields in rows if event == 'post_discovered']
    posts = [Post(fields['post_id'], platform, account, fields.get('head', ''), fields['created_at'],
                  owner=fields.get('owner', account), coauthors=fields.get('coauthors', []),
                  permalink=fields.get('permalink') or f'https://www.{platform}.com/p/' + fields['post_id'],
                  source_media_complete=True, source_media_count=fields['images'],
                  media=[Media('https://cdn.invalid/' + fields['post_id'] + '/' + str(i), 'image')
                         for i in range(fields['images'])]) for fields in found]
    def detector(_kind, _platform):
        candidates = ledger.begin('fixture-scan', posts, {r['post_id']: r for r in arc.rows()}, now)
        for post in candidates:
            ledger.started(post, now)
            captured = next((fields for event, fields in rows if event == 'post_captured' and fields['post_id'] == post.post_id), None)
            if not captured:
                continue
            for i, media in enumerate(post.media):
                data = image_bytes()
                arc.save_media(post, i, data, image_facts(data, 'image/jpeg'))
            arc.append(post)
            ledger.finish(post, arc, now, archived=True)
        ledger.interrupt('fixture-scan', now, '素材未补全，请人工处理')
        return code
    runtime.clock = lambda: now
    runtime.detector = detector
