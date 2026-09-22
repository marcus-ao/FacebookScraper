"""Planner content semantics; API taxonomies do not establish Business Suite DOM selectors."""
from datetime import datetime
import re

PLACEMENTS = {'feed', 'story', 'reel', 'live', 'ad', 'task', 'unknown'}
MEDIA_KINDS = {'text', 'link', 'image', 'carousel', 'mixed', 'video', 'unknown'}
DELIVERIES = {'published', 'scheduled', 'draft', 'failed', 'processing', 'unavailable', 'unknown'}
READ_STATUSES = {'complete', 'unsupported', 'unavailable', 'incomplete', 'legacy'}
CAPTION_STATUSES = {'present', 'empty', 'unknown'}
NO_TEXT = 'This content has no text'
LABELS = {'Post': 'feed', 'Story': 'story', 'Reel': 'reel', 'Video': 'feed', 'Live': 'live'}


class DetailReadError(ValueError):
    """A bounded diagnostic vocabulary without captions, page text or URLs."""
    def __init__(self, code, *, placement='unknown', missing_fields=(), variants=()):
        self.code, self.placement = code, placement
        self.missing_fields = tuple(missing_fields)
        self.variants = tuple(variants)
        super().__init__(code)


def classify_published(observation, day, expected_accounts):
    """Validate one independently observed channel; never expand aggregate platform icons."""
    metadata = ' '.join(observation.get('metadata', '').split())
    label = metadata.split('·', 1)[0].strip()
    placement = LABELS.get(label, 'unknown')
    if placement == 'unknown' and not observation.get('owner'):
        raise DetailReadError('unsupported_type')
    match = re.search(r'Published on: \w{3} (\w{3} \d{1,2}), (\d{1,2}:\d{2}\s*[ap]m)', metadata, re.I)
    if not match:
        raise DetailReadError('missing_fields', placement=placement, missing_fields=('published_at',))
    clock = re.sub(r'\s+', '', match[2])
    observed = datetime.strptime(f'{day.year} {match[1]} {clock}', '%Y %b %d %I:%M%p')
    if observed.date() != day:
        raise DetailReadError('time_mismatch', placement=placement)
    channel = observation.get('channel')
    if channel not in expected_accounts:
        raise DetailReadError('identity_unverified', placement=placement, missing_fields=('channel',))
    owner = observation.get('owner', '').strip()
    if not owner:
        raise DetailReadError('missing_fields', placement=placement, missing_fields=('owner',))
    # A collaborator's post appears in this asset's planner under its own author.
    # Occupancy is per channel slot, so a response-verified account that differs
    # from the configured one still takes the slot and is recorded as itself.
    # ⛔ Owners read off the DOM have no such proof and must match the config.
    if owner != expected_accounts[channel] and not observation.get('owner_verified'):
        raise DetailReadError('identity_mismatch', placement=placement)
    remote = observation.get('remote_id', '')
    if not re.fullmatch(r'\d{6,}', remote):
        raise DetailReadError('missing_fields', placement=placement, missing_fields=('remote_id',))
    caption = observation.get('caption')
    missing = ['placement'] if placement == 'unknown' else []
    if caption is None or not caption.strip():
        caption = ''
        missing.append('caption')
    caption_status = 'present'
    if channel == 'facebook' and placement == 'story' and ' '.join(caption.split()).casefold() == 'your story':
        if not observation.get('story_entity_verified'):
            raise DetailReadError('missing_fields', placement=placement, missing_fields=('caption',))
        # The verified published Story still occupies its slot; this UI title
        # proves neither a caption nor the absence of one.
        caption, caption_status = '', 'unknown'
    elif ' '.join(caption.split()) == NO_TEXT:
        caption = ''
        caption_status = 'empty'
    elif not caption.strip():
        caption_status = 'unknown'
    media_kind = observation.get('media_kind', 'unknown')
    if media_kind not in MEDIA_KINDS:
        media_kind = 'unknown'
    return {'placement': placement, 'media_kind': media_kind, 'text': caption,
            'caption_status': caption_status, 'delivery': 'published',
            'channels': (channel,), 'remote_ids': {channel: remote}, 'accounts': {channel: owner},
            'ui_at': observed, 'read_status': 'incomplete' if missing else 'complete',
            'missing_fields': tuple(missing),
            'relationships': tuple(value for value in observation.get('relationships', ())
                                   if value in {'collaboration', 'shared', 'cross_platform'})}
