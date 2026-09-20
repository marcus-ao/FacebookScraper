"""Bounded, passive content-identity evidence for the Planner detail diagnostic."""
import asyncio
import json
import re
from urllib.parse import parse_qs, urlsplit, urlunsplit, urlencode


IDS = {'id', 'content_id', 'post_id', 'story_id', 'media_id', 'owner_id', 'actor_id',
       'page_id', 'instagram_id', 'instagram_account_id', 'facebook_id', 'fbid', 'pk',
       'story_fbid', 'legacy_fbid', 'ig_media_id', 'instagram_media_id'}
TIMES = {'creation_time', 'created_time', 'publish_time', 'published_time', 'scheduled_time',
         'timestamp', 'taken_at', 'taken_at_timestamp'}
KINDS = {'__typename', 'media_type', 'content_type', 'platform', 'status', 'is_published',
         'is_story', 'is_video', 'is_reel', 'is_crosspost', 'is_crossposted'}
NAMES = {'Neakasa Deutschland', 'neakasa.de', 'neakasa.global', 'neakasa.tech'}


def content_fields(payload):
    """Keep object paths and identity relations; discard all arbitrary string values."""
    count = 0

    def visit(value, depth=0):
        nonlocal count
        count += 1
        if count > 20000 or depth > 24:
            return None
        if isinstance(value, list):
            items = [{'index': index, 'value': cleaned} for index, child in enumerate(value[:100])
                     if (cleaned := visit(child, depth+1)) is not None]
            return items or None
        if not isinstance(value, dict):
            return None
        result = {}
        for key, child in value.items():
            if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]{0,79}', key) or re.search(
                    r'token|secret|auth|session|cookie|password|credential', key, re.I):
                continue
            if key in IDS and re.fullmatch(r'\d{6,30}', str(child)):
                result[key] = str(child)
            elif key in TIMES and isinstance(child, (int, float)) and not isinstance(child, bool):
                result[key] = child
            elif key in KINDS and (isinstance(child, bool) or
                    isinstance(child, str) and re.fullmatch(r'[A-Za-z_]{1,64}', child)):
                result[key] = child
            elif key in {'name', 'username'} and isinstance(child, str):
                result[key if child in NAMES else key+'_length'] = child if child in NAMES else len(child)
            elif key in {'caption', 'message', 'text', 'title'} and isinstance(child, str):
                result[key+'_length'] = len(child)
            elif key in {'url', 'permalink', 'permalink_url'} and isinstance(child, str):
                parsed = urlsplit(child)
                if parsed.hostname in {'www.facebook.com', 'facebook.com', 'www.instagram.com', 'instagram.com'} and re.fullmatch(
                        r'/(?:permalink\.php|story\.php|stories/[\w.]+/\d+/?|(?:p|reel)/[\w-]+/?)', parsed.path):
                    query = {k:v[0] for k,v in parse_qs(parsed.query).items()
                             if k in IDS and len(v)==1 and re.fullmatch(r'\d{6,30}',v[0])}
                    result[key] = urlunsplit(('https', parsed.hostname, parsed.path, urlencode(query), ''))
            elif isinstance(child, (dict, list)):
                cleaned = visit(child, depth+1)
                if cleaned is not None:
                    result[key] = cleaned
        return result or None

    return visit(payload)


class ResponseEvidence:
    """Listen only to responses already caused by the UI; arrival is not provenance."""
    def __init__(self, page, emit):
        self.page, self.emit = page, emit
        self.view = 'initial'
        self.tasks = []

    def start(self):
        self.page.on('response', self.observe)

    def observe(self, response):
        parsed = urlsplit(response.url)
        if (parsed.hostname != 'business.facebook.com' or parsed.path.rstrip('/') not in
                {'/api/graphql', '/graphql'} or len(self.tasks) >= 40):
            return
        # Capture arrival context, never claim the response belongs to this view.
        self.tasks.append(asyncio.create_task(self.collect(response, self.view)))

    async def collect(self, response, view):
        try:
            raw = await asyncio.wait_for(response.body(), 5)
            if len(raw) > 4_000_000:
                self.emit({'RESPONSE_EVIDENCE': 'size_limit', 'during_view': view})
                return
            text = raw.decode('utf-8').removeprefix('for (;;);').strip()
            try:
                documents = [json.loads(text)]
            except ValueError:
                documents = [json.loads(line) for line in text.splitlines() if line.strip()]
            for document in documents:
                fields = content_fields(document)
                if fields:
                    self.emit({'RESPONSE_EVIDENCE': fields, 'during_view': view,
                               'response_status': response.status})
        except Exception as exc:
            self.emit({'RESPONSE_EVIDENCE': 'unreadable', 'during_view': view,
                       'read_error_type': type(exc).__name__})

    async def finish(self):
        self.page.remove_listener('response', self.observe)
        if self.tasks:
            await asyncio.gather(*self.tasks)
        self.emit({'RESPONSE_SUMMARY': {'observed': len(self.tasks), 'limit': 40}})
