"""Read insights metadata and independent channel views; unknown layouts stay incomplete."""
import asyncio
import re
import time

from publish.planner_content import DetailReadError, classify_published, LABELS, NO_TEXT


async def header_snapshot(page):
    labels = [node for node in await page.get_by_text(re.compile(r'Published on:')).all()
              if await node.is_visible()]
    if len(labels) != 1:
        return None
    # A caption belongs to the smallest header containing the metadata and one
    # heading. The heading's level is not a content-type contract.
    return await labels[0].evaluate(r'''el => {
      const visible=n=>!!n.getClientRects().length;
      const boundary=el.closest('[role="tabpanel"]');
      let caption=null, platforms=[];
      for(let root=el.parentElement, depth=0;root && (!boundary||boundary.contains(root)) && depth<6;root=root.parentElement,depth++) {
        const headings=[...root.querySelectorAll('h1,h2,h3,h4,h5,h6,[role="heading"]')].filter(visible);
        if(headings.length>1) break;
        if(headings.length===1) {
          caption=headings[0].innerText;
          platforms=[...root.querySelectorAll('img[alt]')].map(n=>n.alt).filter(v=>['Facebook','Instagram'].includes(v));
          break;
        }
      }
      const text=el.innerText;
      return {metadata:text,caption,platforms:[...new Set(platforms)]};
    }''')


async def preview_identity(page, channel, placement):
    """Only explicit author/profile nodes in the preview; caption mentions never qualify."""
    # Only this Feed structure has recorded preview/profile/permalink evidence.
    # A Story may embed another post: its source permalink is not the Story ID.
    # Do not invent an IG/Story identity adapter from the supplied screenshot.
    if channel != 'facebook' or placement != 'feed':
        if channel in {'facebook','instagram'} and placement != 'unknown':
            raise DetailReadError('unsupported_type', placement=placement,
                                  missing_fields=('channel_identity_adapter',))
        return {'owner':'', 'remote_id':''}
    preview = page.get_by_role('heading', name='Feed preview', exact=True)
    if await preview.count() != 1:
        return {'owner':'', 'remote_id':''}
    # Snapshot 200 records the Facebook preview author as a heading and profile
    # link, and its independent permalink. Do not use stats labels as owners.
    return await preview.evaluate(r'''(label, channel) => {
      const boundary=label.closest('[role="tabpanel"]');
      for(let root=label.parentElement,depth=0;root&&(!boundary||boundary.contains(root))&&depth<5;root=root.parentElement,depth++) {
        if([...root.querySelectorAll('[role="tab"]')].some(n=>n.innerText==='Total performance')) break;
        const links=[...root.querySelectorAll('a[href]')];
        const owners=new Map(), permalinks=[];
        for(const a of links) {
          let u;try{u=new URL(a.href)}catch{continue}
          const text=(a.innerText||'').trim();
          if(channel==='facebook' && /(^|\.)facebook\.com$/.test(u.hostname)) {
            const ownerId=u.searchParams.get('id')||'';
            if(u.pathname==='/profile.php' && /^\d+$/.test(ownerId) && text && (a.closest('h1,h2,h3,[role="heading"]') || a.querySelector('h1,h2,h3,[role="heading"]'))) owners.set(ownerId+'\0'+text,{id:ownerId,name:text});
            if(['/permalink.php','/story.php'].includes(u.pathname) && u.searchParams.has('story_fbid')) permalinks.push({ownerId,id:u.searchParams.get('story_fbid')});
          }
        }
        if(owners.size && permalinks.length) {
          if(owners.size!==1) return {owner:'',remote_id:''};
          const owner=[...owners.values()][0];
          const ids=new Set(permalinks.filter(p=>p.ownerId===owner.id).map(p=>p.id));
          return {owner:owner.name,remote_id:ids.size===1?[...ids][0]:''};
        }
      }
      return {owner:'',remote_id:''};
    }''', channel)


async def read_view(page, row, item, expected_accounts, *, channel='', aggregate=False, timeout=30):
    deadline = time.monotonic()+timeout
    previous, stable_since = None, time.monotonic()
    summary = await header_snapshot(page)
    placement_hint = LABELS.get((summary or {}).get('metadata','').split('·',1)[0].strip(), 'unknown')
    last_error = DetailReadError('structure_unknown', missing_fields=('published_header',))
    while True:
        scope = page
        if aggregate:
            tab = page.get_by_role('tab', name=channel.title(), exact=True)
            scope = None
            if await tab.count() == 1 and await tab.get_attribute('aria-selected') == 'true':
                panel_id = await tab.get_attribute('aria-controls')
                tab_id = await tab.get_attribute('id')
                other = page.get_by_role('tab', name='Facebook' if channel=='instagram' else 'Instagram', exact=True)
                shared = await other.count() and await other.get_attribute('aria-controls') == panel_id
                # A shared header can still show the previous channel after its tab
                # and preview have changed. Duration alone cannot prove provenance.
                for panel in await page.get_by_role('tabpanel').all():
                    if (panel_id and tab_id and not shared and await panel.get_attribute('id') == panel_id
                            and await panel.get_attribute('aria-labelledby') == tab_id and await panel.is_visible()):
                        scope = panel
                        break
            if scope is None:
                last_error = DetailReadError('missing_fields', placement=placement_hint,
                                            missing_fields=('channel_detail_scope',))
        header = await header_snapshot(scope) if scope is not None else None
        if header:
            chosen = channel
            if not chosen and len(header['platforms'])==1:
                chosen = {'Facebook':'facebook','Instagram':'instagram'}[header['platforms'][0]]
            placement = LABELS.get(header['metadata'].split('·',1)[0].strip(), 'unknown')
            identity = await preview_identity(scope, chosen, placement)
            # Metadata <strong> nodes also contain collaborators in the recording.
            # Neither those names nor content_id establish a channel's identity.
            observation = {**header, **identity, 'channel':chosen,
                           'media_kind':'video' if header['metadata'].split('·')[0].strip() in {'Reel','Video','Live'} else 'unknown'}
            try:
                material = classify_published(observation, row['date'], expected_accounts)
            except DetailReadError as exc:
                last_error = exc
                material = None
            signature = material or (last_error.code, last_error.missing_fields, observation)
            if signature != previous:
                previous, stable_since = signature, time.monotonic()
            if material and time.monotonic()-stable_since >= .4:
                return material
            if not material and time.monotonic()-stable_since >= .4 and last_error.code in {
                    'unsupported_type', 'identity_mismatch', 'time_mismatch'}:
                raise last_error
        else:
            previous = None
            if aggregate and scope is not None:
                last_error = DetailReadError('missing_fields', placement=placement_hint,
                                            missing_fields=('channel_metadata',))
        # Permission pages are distinct from a header still hydrating.
        if await page.get_by_text(re.compile(r'^(This content isn.t available|You don.t have permission|Content not available)[.!]?$')).count():
            raise DetailReadError('permission_denied', placement=placement_hint)
        if time.monotonic()>=deadline:
            if await page.locator('[aria-busy="true"]').count():
                raise DetailReadError('load_timeout', placement=last_error.placement,
                                      missing_fields=last_error.missing_fields)
            raise last_error
        await asyncio.sleep(min(.2,max(0,deadline-time.monotonic())))


async def read_story(page, row, item, expected_accounts, baseline, evidence, *, timeout):
    """Bind the recorded IG root media to its selected channel header and Story preview."""
    deadline = time.monotonic()+timeout
    tab = page.get_by_role('tab', name='Instagram', exact=True)
    while await tab.count() != 1 or not await tab.is_visible():
        if time.monotonic() >= deadline:
            raise DetailReadError('identity_unverified', placement='story', missing_fields=('instagram_channel_tab',))
        await asyncio.sleep(.2)
    await tab.click(timeout=max(1, (deadline-time.monotonic())*1000))
    previous, stable_since = None, time.monotonic()
    last_error = DetailReadError('identity_unverified', placement='story', missing_fields=('instagram_story_identity',))
    while True:
        if len(evidence.identities) > 1:
            raise DetailReadError('identity_unverified', placement='story', missing_fields=('consistent_story_identity',))
        header = await header_snapshot(page)
        identity = evidence.identities[0] if evidence.identities else None
        preview = await page.evaluate(r'''() => {
          const visible=n=>!!n.getClientRects().length && getComputedStyle(n).visibility!=='hidden';
          const frames=[...document.querySelectorAll('#instagram_story_preview_frame')].filter(visible);
          const loading=[...document.querySelectorAll('h1,h2,h3,[role="heading"]')]
            .some(n=>visible(n) && n.innerText.trim()==='Loading preview');
          if(frames.length!==1 || loading) return null;
          const frame=frames[0], media=[...frame.querySelectorAll('#instagram_story_preview_media')].filter(visible);
          if(media.length!==1) return null;
          return [...frame.querySelectorAll('div,span')].filter(n=>visible(n)&&!n.children.length)
            .map(n=>n.innerText.trim()).filter(Boolean);
        }''')
        selected = await tab.count()==1 and await tab.get_attribute('aria-selected')=='true'
        material = None
        if identity and header and preview and selected and header['platforms']==['Instagram']:
            caption = header['caption']
            # The initial root header and selected IG header must agree. A tab
            # switch alone cannot give a shared FB header IG provenance.
            if (header['metadata'] != baseline['metadata'] or caption is None or
                    (' '.join(caption.split()) == NO_TEXT and identity['title'] != '') or
                    (' '.join(caption.split()) != NO_TEXT and caption != identity['title'])):
                last_error = DetailReadError('identity_unverified', placement='story',
                                            missing_fields=('story_header_binding',))
            elif identity['owner'] not in preview:
                last_error = DetailReadError('identity_mismatch', placement='story')
            else:
                related = identity['related_kinds']
                observation = {**header, **identity, 'channel':'instagram', 'media_kind':'unknown',
                               'relationships': ('cross_platform',) if 'TofuFBStoryEntityInfo' in related else ()}
                try:
                    material = classify_published(observation, row['date'], expected_accounts)
                    if material['ui_at'].strftime('%I:%M %p').lstrip('0') != item['time'].lstrip('0'):
                        raise DetailReadError('time_mismatch', placement='story')
                except DetailReadError as exc:
                    last_error, material = exc, None
        signature = material
        if signature != previous:
            previous, stable_since = signature, time.monotonic()
        if material and time.monotonic()-stable_since >= .6:
            # Your Story is a UI title, not a verified FB caption or native ID.
            # BusinessFBStoryContent.id cannot replace a native Story ID: deletion
            # reconciliation compares native IDs and could otherwise infer absence.
            if ('TofuFBStoryEntityInfo' in identity['related_kinds'] or
                    await page.get_by_role('tab', name='Facebook', exact=True).count()):
                raise DetailReadError('identity_unverified', placement='story',
                                      missing_fields=('facebook_story_identity', 'facebook_story_caption'), variants=(material,))
            if set(identity['related_kinds']) - {'TofuIGPostEntityInfo'}:
                raise DetailReadError('identity_unverified', placement='story',
                                      missing_fields=('related_story_identity',), variants=(material,))
            return [material]
        if time.monotonic() >= deadline:
            raise last_error
        await asyncio.sleep(.2)


async def read(page, row, item, expected_accounts, *, timeout=30, evidence=None):
    # Tabs may hydrate after DOMContentLoaded. Wait for the header before deciding
    # whether this is a single-channel view or an aggregate requiring each tab.
    deadline = time.monotonic()+timeout
    header = await header_snapshot(page)
    while not header:
        if await page.get_by_text(re.compile(r'^(This content isn.t available|You don.t have permission|Content not available)[.!]?$')).count():
            raise DetailReadError('permission_denied')
        if time.monotonic()>=deadline:
            raise DetailReadError('structure_unknown', missing_fields=('published_header',))
        await asyncio.sleep(.2)
        header = await header_snapshot(page)
    if evidence is not None and header['metadata'].split('·',1)[0].strip()=='Story':
        return await read_story(page, row, item, expected_accounts, header, evidence, timeout=timeout)
    tabs = [(key,page.get_by_role('tab',name=label,exact=True))
            for key,label in [('facebook','Facebook'),('instagram','Instagram')]]
    available = [(key,node) for key,node in tabs if await node.count()==1]
    if not available:
        material = await read_view(page,row,item,expected_accounts,timeout=timeout)
        if any([await node.count() for _,node in tabs]) or await page.get_by_role('tab',name='Total performance',exact=True).count():
            raise DetailReadError('missing_fields', missing_fields=('channel_tabs',))
        if material['ui_at'].strftime('%I:%M %p').lstrip('0') != item['time'].lstrip('0'):
            raise DetailReadError('time_mismatch',placement=material['placement'])
        return [material]
    variants=[]
    for channel,node in available:
        await node.click(timeout=timeout*1000)
        deadline = time.monotonic()+timeout
        while await node.get_attribute('aria-selected') != 'true' and time.monotonic()<deadline:
            await asyncio.sleep(.2)
        if await node.get_attribute('aria-selected') != 'true':
            raise DetailReadError('identity_unverified',missing_fields=('selected_channel',))
        variants.append(await read_view(page,row,item,expected_accounts,channel=channel,aggregate=True,timeout=timeout))
    if [(key,await node.count()) for key,node in tabs] != [(key,int(any(key==found for found,_ in available))) for key,_ in tabs]:
        raise DetailReadError('missing_fields', missing_fields=('channel_tabs',))
    if not set(header['platforms']).issubset({channel.title() for channel,_ in available}):
        raise DetailReadError('missing_fields', missing_fields=('channel_tabs',))
    if not any(value['ui_at'].strftime('%I:%M %p').lstrip('0')==item['time'].lstrip('0') for value in variants):
        raise DetailReadError('time_mismatch')
    if len(variants)>1:
        for variant in variants:
            variant['relationships'] = (*variant.get('relationships',()),'cross_platform')
    return variants
