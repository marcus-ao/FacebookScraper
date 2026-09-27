"""Read recorded Facebook and Instagram scheduled previews, excluding their controls."""
import asyncio
import re
import time
from datetime import datetime

from playwright.async_api import TimeoutError as BrowserTimeout

from publish.planner_content import DetailReadError


PREVIEW_DATE = re.compile(r'^[A-Z][a-z]+ \d{1,2}(?:, \d{4})? at \d{1,2}:\d{2}\s*[AP]M$')
INSTAGRAM_NOTICE = 'This view of your post may not represent exactly how it appears on your Instagram feed.'


async def expand(dialog, *, timeout, expected_accounts):
    deadline = time.monotonic() + timeout
    article = dialog.get_by_role('article')
    instagram = dialog.get_by_text(INSTAGRAM_NOTICE, exact=True)
    await article.or_(instagram).wait_for(state='visible', timeout=timeout * 1000)
    if await instagram.count():
        await expand_instagram(dialog, expected_accounts['instagram'], deadline)
        return
    owner = expected_accounts['facebook']
    await article.get_by_role('link', name=PREVIEW_DATE).wait_for(state='visible', timeout=timeout * 1000)
    previous, stable_since = None, time.monotonic()
    while time.monotonic() < deadline:
        more = article.get_by_role('button', name='See more', exact=True)
        if await more.count():
            await more.click(timeout=max(1, (deadline - time.monotonic()) * 1000))
            await more.wait_for(state='hidden', timeout=max(1, (deadline - time.monotonic()) * 1000))
            previous = None
        # Headers can precede the caption. Wait for the caption itself, not the
        # unrelated progressbar under the post's photo and comments.
        caption = await read_caption(article, owner)
        if caption != previous:
            previous, stable_since = caption, time.monotonic()
        if caption and time.monotonic() - stable_since >= .4:
            return
        await asyncio.sleep(.1)
    raise DetailReadError('missing_fields', placement='feed', missing_fields=('full_preview_caption',))


async def read(dialog, day, clock, ids, expected_accounts):
    if set(ids) == {'instagram'}:
        owner = expected_accounts['instagram']
        caption = await instagram_caption(dialog, owner)
        if not caption:
            raise DetailReadError('missing_fields', placement='feed', missing_fields=('full_preview_caption',))
        # This single-channel preview has no independent date label. Its time
        # belongs to the clicked day/card, checked before opening and by the
        # final grid sweep. Never copy this time to an aggregate channel.
        return {'channels': ('instagram',), 'remote_ids': ids, 'accounts': {'instagram': owner},
                'text': caption, 'delivery': 'scheduled', 'placement': 'feed',
                'caption_status': 'present',
                'ui_at': datetime.combine(day, datetime.strptime(clock, '%I:%M %p').time())}
    if set(ids) != {'facebook'}:
        raise DetailReadError('unsupported_type', placement='feed', missing_fields=('scheduled_preview_adapter',))
    article = dialog.get_by_role('article')
    owner = expected_accounts['facebook']
    if (await article.count() != 1
            or await article.get_by_role('heading', name=owner, exact=True).count() != 1):
        raise DetailReadError('identity_unverified', placement='feed', missing_fields=('preview_account',))
    dates = article.get_by_role('link', name=PREVIEW_DATE)
    if await dates.count() != 1:
        raise DetailReadError('missing_fields', placement='feed', missing_fields=('preview_time',))
    text = ' '.join((await dates.inner_text()).split())
    fmt = '%B %d, %Y at %I:%M %p' if ',' in text else '%Y %B %d at %I:%M %p'
    at = datetime.strptime(text if ',' in text else f'{day.year} {text}', fmt)
    if at != datetime.combine(day, datetime.strptime(clock, '%I:%M %p').time()):
        raise DetailReadError('time_mismatch', placement='feed')
    caption = await read_caption(article, owner)
    if not caption:
        raise DetailReadError('missing_fields', placement='feed', missing_fields=('full_preview_caption',))
    return {'channels': ('facebook',), 'remote_ids': ids, 'accounts': {'facebook': owner},
            'text': caption, 'delivery': 'scheduled', 'placement': 'feed',
            'caption_status': 'present', 'ui_at': at}


def instagram_caption_owner(dialog, owner):
    # Service DOM: a header DIV and a caption-author SPAN have the same text.
    # Restrict to the latter; the following siblings contain only the caption.
    return dialog.get_by_text(owner, exact=True).and_(dialog.locator('div > span:first-child'))


async def verify_preview_owner(dialog, ids, expected_accounts, *, timeout):
    """A mention in a caption cannot establish the preview's author."""
    if set(ids) == {'instagram'}:
        owner = expected_accounts['instagram']
        nodes = (dialog.get_by_text(INSTAGRAM_NOTICE, exact=True),
                 instagram_caption_owner(dialog, owner),
                 dialog.get_by_text(owner, exact=True).and_(dialog.locator('div')))
    elif set(ids) == {'facebook'}:
        nodes = (dialog.get_by_role('article'),
                 dialog.get_by_role('article').get_by_role('heading', name=expected_accounts['facebook'], exact=True))
    else:
        raise DetailReadError('identity_unverified', placement='feed', missing_fields=('preview_account',))
    deadline = time.monotonic() + timeout
    for node in nodes:
        try:
            await node.wait_for(state='visible', timeout=max(1, (deadline - time.monotonic()) * 1000))
        except BrowserTimeout as exc:
            raise DetailReadError('identity_unverified', placement='feed',
                                  missing_fields=('preview_account',)) from exc
        if await node.count() != 1:
            raise DetailReadError('identity_unverified', placement='feed', missing_fields=('preview_account',))


def _caption_words(value):
    return re.findall(r'\w+', value.casefold().replace('\u200b', ''))


async def wait_preview_caption(dialog, ids, expected_accounts, expected_caption, *, timeout):
    """Wait for this card's preview body after its early ID/author shell appears."""
    expected = _caption_words(expected_caption)
    if not expected:
        raise DetailReadError('missing_fields', placement='feed', missing_fields=('preview_caption',))
    channel = next(iter(ids)) if len(ids) == 1 else ''
    if channel == 'instagram':
        author = instagram_caption_owner(dialog, expected_accounts['instagram'])
        more = author.locator('xpath=..').get_by_role('button', name='more', exact=True)
    elif channel == 'facebook':
        article = dialog.get_by_role('article')
        more = article.get_by_role('button', name='See more', exact=True)
    else:
        raise DetailReadError('identity_unverified', placement='feed', missing_fields=('preview_account',))
    deadline = time.monotonic() + timeout
    previous, stable_since = None, time.monotonic()
    while time.monotonic() < deadline:
        if not await dialog.is_visible():
            raise DetailReadError('missing_fields', placement='feed', missing_fields=('preview_dialog',))
        if channel == 'instagram':
            if await author.count() != 1:
                sample = ''
            else:
                sample = await author.evaluate('''el => {
                  const siblings=[];
                  for(let n=el.nextElementSibling;n;n=n.nextElementSibling)
                    if(n.tagName==='SPAN') siblings.push(n.innerText);
                  return siblings.join(' ');
                }''')
        else:
            sample = await article.inner_text() if await article.count() == 1 else ''
        actual = _caption_words(sample)
        prefix = expected[:6]
        matched = any(actual[i:i+len(prefix)] == prefix for i in range(len(actual)-len(prefix)+1))
        full = any(actual[i:i+len(expected)] == expected for i in range(len(actual)-len(expected)+1))
        truncated = await more.count() == 1 and await more.is_visible()
        loading = dialog.get_by_role('heading', name='Loading preview', exact=True)
        ready = matched and (full or truncated) and not (await loading.count() and await loading.is_visible())
        observation = (sample, ready, truncated)
        if observation != previous:
            previous, stable_since = observation, time.monotonic()
        if ready and time.monotonic() - stable_since >= (1.0 if truncated else .4):
            return
        await asyncio.sleep(min(.1, max(0, deadline - time.monotonic())))
    raise DetailReadError('missing_fields', placement='feed', missing_fields=('preview_caption',))


async def instagram_caption(dialog, owner):
    author = instagram_caption_owner(dialog, owner)
    if (await dialog.get_by_text(INSTAGRAM_NOTICE, exact=True).count() != 1
            or await author.count() != 1
            or await dialog.get_by_text(owner, exact=True).and_(dialog.locator('div')).count() != 1):
        return None
    return await author.evaluate('''el => {
      const nodes=[];
      for(let n=el.nextElementSibling; n; n=n.nextElementSibling) nodes.push(n);
      if(!nodes.length || nodes.some(n=>n.tagName!=='SPAN')) return null;
      if(nodes.some(n=>n.querySelector('button,[role="button"],[role="textbox"]'))) return null;
      return nodes.map(n=>n.innerText).join('').trim() || null;
    }''')


async def expand_instagram(dialog, owner, deadline):
    previous, stable_since = None, time.monotonic()
    while time.monotonic() < deadline:
        author = instagram_caption_owner(dialog, owner)
        if await author.count() == 1:
            more = author.locator('xpath=..').get_by_role('button', name='more', exact=True)
            if await more.count() == 1:
                await more.click(timeout=max(1, (deadline - time.monotonic()) * 1000))
                await more.wait_for(state='hidden', timeout=max(1, (deadline - time.monotonic()) * 1000))
                previous = None
        caption = await instagram_caption(dialog, owner)
        if caption != previous:
            previous, stable_since = caption, time.monotonic()
        if caption and time.monotonic() - stable_since >= .4:
            return
        await asyncio.sleep(.1)
    raise DetailReadError('missing_fields', placement='feed', missing_fields=('full_preview_caption',))


async def read_caption(article, owner):
    # Service snapshot: author/date/actions precede the caption; the first media
    # follows it. Emoji are <img alt> nodes. Never include Boost or comment text,
    # and never wait for the unrelated progressbar below the media.
    actions = article.get_by_role('button', name='Actions for this post by ' + owner, exact=True)
    if await actions.count() != 1:
        return None
    return await actions.evaluate(r'''start => {
      const article=start.closest('article,[role="article"]');
      if(!article) return null;
      const visible=n=>!!n.getClientRects().length && getComputedStyle(n).visibility!=='hidden';
      const emoji=s=>/^(?:\p{Extended_Pictographic}|\p{Regional_Indicator}|\p{Emoji_Modifier}|[\u200d\ufe0f\u20e3]|[0-9#*])+$/u.test(s);
      const media=[...article.querySelectorAll('img')].find(n=>visible(n)
        && (start.compareDocumentPosition(n)&Node.DOCUMENT_POSITION_FOLLOWING) && !emoji(n.alt||''));
      if(!media) return null;
      const range=document.createRange(); range.setStartAfter(start); range.setEndBefore(media);
      const fragment=range.cloneContents();
      fragment.querySelectorAll('button,[role="button"]').forEach(n=>{
        if((n.getAttribute('aria-label')||n.textContent).trim()==='See less') n.remove();
      });
      if(fragment.querySelector('button,[role="button"],[role="textbox"]')) return null;
      const read=n=>{
        if(n.nodeType===Node.TEXT_NODE) return n.textContent;
        if(n.nodeType===Node.ELEMENT_NODE && (n.hidden||n.getAttribute('aria-hidden')==='true')) return '';
        if(n.nodeName==='IMG') return emoji(n.alt||'') ? n.alt : '';
        if(n.nodeName==='BR') return '\n';
        const value=[...n.childNodes].map(read).join('');
        return /^(DIV|P|LI)$/.test(n.nodeName) ? '\n'+value+'\n' : value;
      };
      return read(fragment).trim();
    }''')
