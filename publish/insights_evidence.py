"""Native Story identity observed on the newly opened insights detail page."""
import asyncio
import re
from urllib.parse import urlsplit

from core.meta_json import response_documents


class InsightsEvidence:
    """Bind native Story entities through the root IG media, never business content IDs."""
    def __init__(self, page, source_id):
        self.page, self.source_id = page, source_id
        self.tasks, self.identities = [], []
        self.facebook_identities = []
        self.media_identities = []

    def start(self):
        self.page.on('response', self.observe)

    def observe(self, response):
        parsed = urlsplit(response.url)
        if (parsed.hostname == 'business.facebook.com' and parsed.path.rstrip('/') in
                {'/api/graphql', '/graphql'} and len(self.tasks) < 40):
            self.tasks.append(asyncio.create_task(self.collect(response)))

    def observe_media(self, document):
        """A published post or reel names its own account on the media ID itself.

        ⚠️ `viewer.username` in the same document is the signed-in account, not
        the author; bind only through `instagram_post.id`.
        """
        post = (document.get('data') or {}).get('instagram_post')
        if not isinstance(post, dict) or str(post.get('id', '')) != self.source_id:
            return
        actor = post.get('bizlink_instagram_actor') or {}
        if (not isinstance(actor.get('username'), str) or not actor['username'].strip()
                or not re.fullmatch(r'\d{6,}', str(actor.get('id', '')))):
            return
        value = {'channel': 'instagram', 'remote_id': self.source_id,
                 'owner': actor['username'], 'owner_id': str(actor['id'])}
        if value not in self.media_identities:
            self.media_identities.append(value)

    async def collect(self, response):
        try:
            raw = await asyncio.wait_for(response.body(), 5)
            if response.status != 200 or len(raw) > 4_000_000:
                return
            for document in response_documents(raw):
                self.observe_media(document)
                info = document.get('data', {}).get('tofu_entity', {}).get('entity_info', {})
                if info.get('__typename') != 'TofuIGPostEntityInfo':
                    continue
                media = info.get('ig_media') or {}
                if str(media.get('id')) != self.source_id:
                    continue
                link = urlsplit(media.get('permalink', ''))
                match = re.fullmatch(r'/stories/([\w.]+)/\d+/?', link.path)
                if link.scheme != 'https' or link.hostname not in {'instagram.com', 'www.instagram.com'} or not match:
                    continue
                related = info.get('cross_posted_entities')
                # Missing relation data cannot establish single-channel completeness.
                if not isinstance(related, list) or not isinstance(info.get('title'), str):
                    continue
                kinds = tuple(sorted({entry.get('entity_info', {}).get('__typename', '') for entry in related}))
                facebook = [entry for entry in related
                            if entry.get('entity_info', {}).get('__typename') == 'TofuFBStoryEntityInfo']
                value = {'remote_id': self.source_id, 'owner': match[1], 'title': info['title'],
                         'related_kinds': kinds,
                         'facebook_ids': tuple(sorted({str(entry.get('entity_id', '')) for entry in facebook})),
                         'instagram_ids': tuple(sorted({str((entry.get('entity_info', {}).get('ig_media') or {}).get('id', ''))
                             for entry in related if entry.get('entity_info', {}).get('__typename') == 'TofuIGPostEntityInfo'}))}
                if value not in self.identities:
                    self.identities.append(value)
                for entry in facebook:
                    fb = entry['entity_info']
                    owner = fb.get('owner') or {}
                    owner_info = owner.get('entity_info') or {}
                    remote = str(entry.get('entity_id', ''))
                    if (not re.fullmatch(r'\d{6,}', remote) or
                            str(fb.get('entity_id', remote)) != remote or
                            not re.fullmatch(r'\d{6,}', str(owner.get('entity_id', ''))) or
                            owner_info.get('__typename') != 'TofuFBProfileWithBizToolsEntityInfo' or
                            not isinstance(owner_info.get('title'), str) or
                            not isinstance(fb.get('title'), str) or type(fb.get('created_at')) is not int):
                        continue
                    candidate = {'remote_id': remote, 'owner': owner_info['title'],
                                 'owner_id': str(owner['entity_id']), 'title': fb['title'], 'created_at': fb['created_at']}
                    if candidate not in self.facebook_identities:
                        self.facebook_identities.append(candidate)
        except (ValueError, TypeError, AttributeError, TimeoutError):
            return
        except Exception:
            # A closed/unreadable browser response cannot establish identity.
            return

    async def finish(self):
        self.page.remove_listener('response', self.observe)
        for task in self.tasks:
            if not task.done():
                task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
