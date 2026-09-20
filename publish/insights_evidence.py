"""Native Story identity observed on the newly opened insights detail page."""
import asyncio
import re
from urllib.parse import urlsplit

from core.meta_json import response_documents


class InsightsEvidence:
    """Retain only the root IG media relation; business content IDs are another namespace."""
    def __init__(self, page, source_id):
        self.page, self.source_id = page, source_id
        self.tasks, self.identities = [], []

    def start(self):
        self.page.on('response', self.observe)

    def observe(self, response):
        parsed = urlsplit(response.url)
        if (parsed.hostname == 'business.facebook.com' and parsed.path.rstrip('/') in
                {'/api/graphql', '/graphql'} and len(self.tasks) < 40):
            self.tasks.append(asyncio.create_task(self.collect(response)))

    async def collect(self, response):
        try:
            raw = await asyncio.wait_for(response.body(), 5)
            if response.status != 200 or len(raw) > 4_000_000:
                return
            for document in response_documents(raw):
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
                value = {'remote_id': self.source_id, 'owner': match[1], 'title': info['title'],
                         'related_kinds': kinds}
                if value not in self.identities:
                    self.identities.append(value)
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
