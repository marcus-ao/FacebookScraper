"""Read the recorded scheduled Facebook article, excluding media and comment UI."""
import asyncio
import re
import time
from datetime import datetime

from publish.planner_content import DetailReadError


PREVIEW_DATE = re.compile(r'^[A-Z][a-z]+ \d{1,2}(?:, \d{4})? at \d{1,2}:\d{2}\s*[AP]M$')


async def expand(dialog, *, timeout, owner):
    deadline = time.monotonic() + timeout
    article = dialog.get_by_role('article')
    await article.wait_for(state='visible', timeout=timeout * 1000)
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
