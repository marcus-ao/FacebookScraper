"""Native Story identity observed on the newly opened insights detail page."""
import asyncio
import re
from urllib.parse import urlsplit

from core.meta_json import response_documents


class InsightsResponseBudget:
    """Reserve bounded response reads for initial load and each channel selection."""
    per_view_limit = 40

    def __init__(self):
        self.counts = dict.fromkeys(('initial', 'facebook', 'instagram'), 0)
        self.dropped = dict.fromkeys(self.counts, 0)

    def accept(self, view):
        key = view.lower() if view.lower() in {'facebook', 'instagram'} else 'initial'
        if self.counts[key] >= self.per_view_limit:
            self.dropped[key] += 1
            return False
        self.counts[key] += 1
        return True

    def summary(self):
        return {'observed': sum(self.counts.values()), 'limit': len(self.counts) * self.per_view_limit,
                'per_view_limit': self.per_view_limit, 'by_view': dict(self.counts),
                'dropped_by_view': dict(self.dropped)}


class InsightsEvidence:
    """Bind native Story entities through the root IG media, never business content IDs."""
    def __init__(self, page, source_id):
        self.page, self.source_id = page, source_id
        self.tasks, self.identities = [], []
        self.facebook_identities = []
        self.media_owners = []
        self.story_identities = []
        self.story_actors = []
        self.aggregate_members = []
        self.view, self.on_channel = 'initial', None
        self.budget = InsightsResponseBudget()

    @property
    def media_identities(self):
        """Instagram accounts observed on this detail's own media ID."""
        return [value for value in self.media_owners if value['remote_id'] == self.source_id]

    def aggregate_owner(self, channel):
        """Join this detail's cross-posted member for `channel` to its account name.

        The member list is bound to the request's content_id, so joining a name
        onto a member's own entity and owner ID is what binds the name here; an
        account observed on some other ID never reaches a member.
        """
        if len(self.aggregate_members) != 1:
            return None
        member = self.aggregate_members[0].get(channel)
        named = self.story_actors if channel == 'facebook' else self.media_owners
        for value in named if member else ():
            if value['remote_id'] == member['remote_id'] and value['owner_id'] == member['owner_id']:
                return {'channel': channel, 'remote_id': member['remote_id'],
                        'owner': value['owner'], 'owner_id': member['owner_id']}
        return None

    def start(self):
        self.page.on('response', self.observe)

    def begin_channel(self, channel):
        # Selection reserves collection capacity, never establishes identity.
        # Counts persist when revisiting a channel; repeated clicks add no budget.
        self.view = channel
        if self.on_channel:
            self.on_channel(channel)

    def observe(self, response):
        parsed = urlsplit(response.url)
        if (parsed.hostname == 'business.facebook.com' and parsed.path.rstrip('/') in
                {'/api/graphql', '/graphql'} and self.budget.accept(self.view)):
            self.tasks.append(asyncio.create_task(self.collect(response)))

    def observe_media(self, document):
        """A published post or reel names its own account on the media ID itself.

        ⚠️ `viewer.username` in the same document is the signed-in account, not
        the author; bind only through `instagram_post.id`.
        """
        post = (document.get('data') or {}).get('instagram_post')
        if not isinstance(post, dict) or not re.fullmatch(r'\d{6,}', str(post.get('id', ''))):
            return
        actor = post.get('bizlink_instagram_actor') or {}
        if (not isinstance(actor.get('username'), str) or not actor['username'].strip()
                or not re.fullmatch(r'\d{6,}', str(actor.get('id', '')))):
            return
        value = {'channel': 'instagram', 'remote_id': str(post['id']),
                 'owner': actor['username'], 'owner_id': str(actor['id'])}
        if value not in self.media_owners:
            self.media_owners.append(value)

    def observe_story_actor(self, document):
        """A published Facebook post names its author on the story holding its ID.

        ⚠️ The same document's `viewer_actor` is the signed-in profile, not the
        author; only `owning_profile` and `actors`, agreeing with each other, do.
        """
        story = (((document.get('data') or {}).get('tofu_entity') or {})
                 .get('entity_info') or {}).get('story')
        if not isinstance(story, dict) or str(story.get('post_id', '')) != self.source_id:
            return
        actors = story.get('actors')
        profile = story.get('feedback', {}).get('owning_profile') or {}
        actor = actors[0] if isinstance(actors, list) and len(actors) == 1 and isinstance(actors[0], dict) else {}
        if (not re.fullmatch(r'\d{6,}', str(actor.get('id', ''))) or str(profile.get('id', '')) != str(actor.get('id'))
                or not isinstance(actor.get('name'), str) or not actor['name'].strip()
                or profile.get('name') != actor['name'] or type(story.get('creation_time')) is not int):
            return
        value = {'channel': 'facebook', 'remote_id': self.source_id, 'owner': actor['name'],
                 'owner_id': str(actor['id']), 'created_at': story['creation_time']}
        if value not in self.story_actors:
            self.story_actors.append(value)

    def observe_aggregate(self, document):
        """An aggregate detail lists its channels on the root entity it names.

        The root repeats the request's content_id and each cross-posted entry
        carries its own entity and owner ID. Only IDs are taken here: the names
        arrive in other documents and are joined back through `aggregate_owner`.
        """
        entity = (document.get('data') or {}).get('tofu_entity') or {}
        info = entity.get('entity_info') or {}
        if (info.get('__typename') != 'TofuFBStoryEntityInfo'
                or str(entity.get('entity_id', '')) != self.source_id):
            return
        related = info.get('cross_posted_entities')
        if not isinstance(related, list) or not related:
            return
        members = {}
        for entry in related:
            if not isinstance(entry, dict):
                return
            nested = entry.get('entity_info') or {}
            channel = {'TofuFBStoryEntityInfo': 'facebook',
                       'TofuIGPostEntityInfo': 'instagram'}.get(nested.get('__typename'))
            remote = str(entry.get('entity_id', ''))
            owner = str((nested.get('owner') or {}).get('entity_id', ''))
            # The Facebook member is this detail itself; a different ID there
            # would mean the root is not the entity this request asked for.
            if (channel is None or channel in members or not re.fullmatch(r'\d{6,}', remote)
                    or not re.fullmatch(r'\d{6,}', owner)
                    or (channel == 'facebook' and remote != self.source_id)):
                return
            members[channel] = {'channel': channel, 'remote_id': remote, 'owner_id': owner}
        if members not in self.aggregate_members:
            self.aggregate_members.append(members)

    def observe_story_page(self, document):
        """A Story published to Facebook alone repeats this detail's own content_id.

        Its entity carries the publishing page only as `lwi_info.page_id`, so the
        name is taken from a page node in the same document whose id equals it.
        ⚠️ `supported_actions[*].entity.entity_info.owner.entity_id` is a profile
        identifier, not that page; `owning_page_for_graphql` arrives in a document
        that never names the entity and cannot bind on its own.
        """
        data = document.get('data') or {}
        insights = data.get('tofu_object_insights')
        if not isinstance(insights, dict) or insights.get('__typename') != 'BizWebFBStoryObjectInsights':
            return
        entity = insights.get('entity') or {}
        info = entity.get('entity_info') or {}
        if (info.get('__typename') != 'TofuFBStoryEntityInfo'
                or str(entity.get('entity_id', '')) != self.source_id
                or str(info.get('entity_id', '')) != self.source_id
                or not isinstance(info.get('title'), str)):
            return
        page_id = str((info.get('lwi_info') or {}).get('page_id', ''))
        if not re.fullmatch(r'\d{6,}', page_id):
            return
        named = {node['name'] for node in (data.get('page'), insights.get('owning_page_for_graphql'))
                 if isinstance(node, dict) and str(node.get('id', '')) == page_id
                 and isinstance(node.get('name'), str) and node['name'].strip()}
        if len(named) != 1:
            return
        value = {'channel': 'facebook', 'remote_id': self.source_id, 'owner': next(iter(named)),
                 'owner_id': page_id, 'title': info['title']}
        if value not in self.story_identities:
            self.story_identities.append(value)

    async def collect(self, response):
        try:
            raw = await asyncio.wait_for(response.body(), 5)
            if response.status != 200 or len(raw) > 4_000_000:
                return
            for document in response_documents(raw):
                self.observe_media(document)
                self.observe_story_page(document)
                self.observe_story_actor(document)
                self.observe_aggregate(document)
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
                         'permalink': media.get('permalink'),
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
