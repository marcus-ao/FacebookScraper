"""Inspect one already-open Planner detail and its channel tabs without editing content."""
import argparse
import asyncio
import json
import re
import sys
import time as monotonic_time
from datetime import date, time
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.chrome import attach
from core.config import cfg
from core.console import force_utf8
from publish.journal import PublishOperationLock
from tools.calendar_detail_response import ResponseEvidence


SNAPSHOT = r'''() => {
 const visible=n=>!!n.getClientRects().length&&getComputedStyle(n).visibility!=='hidden';
 const text=n=>(n.innerText||n.textContent||'').replace(/\s+/g,' ').trim();
 const fixed=/^(This content has no text|Feed preview|Loading(?: preview)?(?:\.{3}|…)?|Total performance|Facebook|Instagram|Neakasa Deutschland|neakasa\.(de|global|tech))$/;
 const meta=/(?:Post|Story|Reel|Video|Live)\s*·?\s*Published on:\s*\w{3}\s+\w{3}\s+\d{1,2},\s*\d{1,2}:\d{2}\s*[ap]m/i;
 const safe=t=>fixed.test(t)?t:(t.match(meta)?.[0]||'');
 const link=raw=>{try{
   const u=new URL(raw,location.href);
   if(!/(^|\.)(facebook|instagram)\.com$/.test(u.hostname))return '';
   let s=u.origin+u.pathname;
   for(const k of ['content_id','id','story_fbid']){
     const v=u.searchParams.get(k);
     if(v&&/^\d+$/.test(v))s+=(s.includes('?')?'&':'?')+k+'='+v;
   }
   for(const k of ['platform','content_type']){
     const v=u.searchParams.get(k);
     if(v&&/^(FACEBOOK|INSTAGRAM|FB|IG|STORY|POST|REEL|VIDEO)$/i.test(v))s+=(s.includes('?')?'&':'?')+k+'='+v;
   }
   return s;
 }catch{return ''}};
 const info=n=>{
   const t=text(n),a=n.getAttribute('aria-label')||'';
   let target='';try{const u=new URL(n.getAttribute('href'),location.href);
     target=u.hostname==='l.facebook.com'?link(u.searchParams.get('u')||''):'';
   }catch{}
   return {tag:n.tagName,role:n.getAttribute('role'),id:n.id,
     controls:n.getAttribute('aria-controls'),labelledby:n.getAttribute('aria-labelledby'),
     level:n.getAttribute('aria-level'),selected:n.getAttribute('aria-selected'),
     text:safe(t),text_length:t.length,aria:safe(a),aria_length:a.length,
     alt:fixed.test(n.getAttribute('alt')||'')?n.getAttribute('alt'):'',
     href:n.hasAttribute('href')?link(n.getAttribute('href')):'',target};
 };
 const nodes=[...document.querySelectorAll('body *')].filter(visible);
 const anchors=nodes.filter(n=>safe(text(n))&&![...n.children].some(c=>text(c)===text(n)));
 const metadata=nodes.filter(n=>meta.test(text(n))&&![...n.children].some(c=>meta.test(text(c))));
 const headers=metadata.map(n=>{
   for(let p=n.parentElement,depth=0;p&&depth<6;p=p.parentElement,depth++){
     const h=[...p.querySelectorAll('[role=heading],h1,h2,h3,h4,h5,h6')].filter(visible);
     if(h.length>1)break;
     if(h.length===1){const caption=text(h[0]);return {metadata:safe(text(n)),caption_length:caption.length,
       caption_empty:caption==='This content has no text',caption_ready:!!caption&&!/^Loading\b/i.test(caption),
       platforms:[...p.querySelectorAll('img[alt]')].filter(visible).map(x=>x.alt).filter(x=>['Facebook','Instagram'].includes(x))};}
   }
   return {metadata:safe(text(n)),caption_ready:false};
 });
 const header=headers.length===1?headers[0]:null;
 const previews=nodes.filter(n=>n.id==='instagram_story_preview_frame');
 return {page:link(location.href),anchors:anchors.slice(0,20).map(n=>({node:info(n),
   ancestors:[n.parentElement,n.parentElement?.parentElement,n.parentElement?.parentElement?.parentElement]
     .filter(Boolean).map(p=>({node:info(p),children:[...p.children].slice(0,12).map(info)}))})),
   headings:nodes.filter(n=>n.matches('[role=heading],h1,h2,h3')).slice(0,12).map(info),
   tabs:nodes.filter(n=>n.matches('[role=tab],[role=tabpanel]')).map(info),
   links:nodes.filter(n=>n.matches('a[href]')).slice(0,40).map(info),
   story_previews:previews.map(n=>({node:info(n),children:[...n.querySelectorAll('*')].filter(visible).slice(0,30).map(info)})),
   readiness:{...header,caption_ready:!!header?.caption_ready,
     selected_channels:nodes.filter(n=>n.matches('[role=tab][aria-selected=true]')&&/^(Total performance|Facebook|Instagram)$/.test(text(n))).map(text)},
   query_keys:[...new URL(location.href).searchParams.keys()].filter(k=>/^[a-z_]{1,40}$/.test(k)&&!/(token|secret|auth|session|cookie)/i.test(k))};
}'''


def emit(value):
    # ASCII JSON survives Windows PowerShell decoding a UTF-8 subprocess as GBK.
    print(json.dumps(value, ensure_ascii=True), flush=True)


async def wait_view(page, channel, *, timeout):
    """Observe header/selection stability without waiting for unrelated metrics."""
    deadline = monotonic_time.monotonic() + timeout
    previous, since = None, monotonic_time.monotonic()
    while True:
        value = await asyncio.wait_for(page.evaluate(SNAPSHOT), min(8, max(.1, deadline-monotonic_time.monotonic())))
        readiness = value['readiness']
        signature = (value['page'], readiness, value['story_previews'])
        if signature != previous:
            previous, since = signature, monotonic_time.monotonic()
        selected = not channel or readiness['selected_channels'] == [channel]
        if selected and readiness['caption_ready'] and monotonic_time.monotonic()-since >= .6:
            return value
        if monotonic_time.monotonic() >= deadline:
            raise TimeoutError('detail view not ready')
        await asyncio.sleep(.2)


async def inspect(browser, *, day, clock, kind, timeout=30, responses=False):
    target = re.compile(re.escape(kind) + r'\s*·?\s*Published on:\s*\w{3}\s+'
        + day.strftime('%b') + r'\s+' + str(day.day) + r',\s*'
        + clock.strftime('%I:%M').lstrip('0') + r'\s*' + clock.strftime('%p'), re.I)
    candidates = []
    for context in browser.contexts:
        for page in context.pages:
            if urlsplit(page.url).hostname != 'business.facebook.com':
                continue
            try:
                value = await asyncio.wait_for(page.evaluate(SNAPSHOT), 8)
                marks = [anchor['node']['text'] for anchor in value['anchors']]
                emit({'OPEN_PAGE': value['page'],
                      'metadata': [mark for mark in marks if 'Published on:' in mark]})
                if any(target.search(mark) for mark in marks):
                    candidates.append(page)
            except Exception as exc:
                emit({'OPEN_PAGE_PATH': urlsplit(page.url).path, 'read_error_type': type(exc).__name__})
    if len(candidates) != 1:
        print(f'STOP: matching detail pages: {len(candidates)}; see OPEN_PAGE entries above', flush=True)
        return False
    page = candidates[0]
    print(f'FOUND: {day.isoformat()} {clock:%H:%M} {kind}', flush=True)
    names = ('Total performance', 'Facebook', 'Instagram')
    selected = [name.strip() for name in await page.get_by_role('tab', selected=True).all_inner_texts()
                if name.strip() in names]
    original = selected[0] if len(selected) == 1 else None
    if original is None and await page.get_by_role('tab').count():
        print('STOP: selected channel tab is unknown; no tabs were changed', flush=True)
        return False
    evidence = ResponseEvidence(page, emit) if responses else None
    if evidence:
        evidence.start()
    complete = True
    try:
        for channel in (None, 'Facebook', 'Instagram'):
            view = channel or 'initial'
            if evidence:
                evidence.view = view
            if channel:
                if original is None:
                    print('SKIP: no initial channel selection; tabs left unchanged', flush=True)
                    complete = False
                    break
                tab = page.get_by_role('tab', name=channel, exact=True)
                # A click temporarily removes all channel tabs in the live Story.
                # Absence during hydration does not prove the other channel absent.
                await tab.click(timeout=timeout*1000)
            print(f'READ: {view}; waiting for header and channel selection', flush=True)
            initial = await asyncio.wait_for(page.evaluate(SNAPSHOT), 8)
            initial.update(view=view, phase='immediate', frame=0)
            emit(initial)
            try:
                value = await wait_view(page, channel or original, timeout=timeout)
            except TimeoutError:
                print(f'STOP: {view} did not become ready; inspection is partial', flush=True)
                complete = False
                break
            for sample in range(2):
                if sample:
                    value = await wait_view(page, channel or original, timeout=timeout)
                value = dict(value, view=view, phase='settled', sample=sample, frame=0)
                emit(value)
                for index, frame in enumerate(page.frames):
                    if frame == page.main_frame:
                        continue
                    try:
                        value = await asyncio.wait_for(frame.evaluate(SNAPSHOT), 8)
                        value.update(view=view, phase='settled', sample=sample, frame=index)
                        emit(value)
                    except Exception as exc:
                        complete = False
                        emit({'view': view, 'frame': index, 'read_error_type': type(exc).__name__})
    except Exception as exc:
        complete = False
        emit({'stage':'channel_inspection','read_error_type':type(exc).__name__})
    finally:
        try:
            if original:
                if evidence:
                    evidence.view = 'restore'
                await page.get_by_role('tab', name=original, exact=True).click(timeout=timeout*1000)
                await wait_view(page, original, timeout=timeout)
                print(f'RESTORED: {original}', flush=True)
        except Exception as exc:
            complete = False
            emit({'stage':'restore_selection','read_error_type':type(exc).__name__})
        finally:
            if evidence:
                await evidence.finish()
    if not complete:
        print('PARTIAL: keep this evidence; not all channel views were captured', flush=True)
        return False
    print('DONE: read-only inspection complete', flush=True)
    return True


async def run(args):
    print('START: discover the open calendar detail, read-only', flush=True)
    config = cfg()
    config.assert_publish_chrome_isolated()
    with PublishOperationLock(config.state_dir / 'publish.lock'):
        pw, browser, _context = await attach(port=config.publish_debug_port, profile=config.publish_profile_dir,
            start_script=r'scripts\start_chrome_publish.bat', login_hint='DE publish')
        try:
            return await inspect(browser, day=args.date, clock=args.time, kind=args.kind,
                                 responses=args.responses)
        finally:
            await pw.stop()


def main():
    force_utf8()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', type=date.fromisoformat, required=True)
    parser.add_argument('--time', type=time.fromisoformat, required=True, help='UI local time, e.g. 18:39')
    parser.add_argument('--kind', choices=('Post', 'Story', 'Reel', 'Video', 'Live'), default='Story')
    parser.add_argument('--responses', action='store_true',
                        help='Passively record allowlisted content fields from channel-switch responses; no extra requests')
    args = parser.parse_args()
    try:
        return 0 if asyncio.run(run(args)) else 2
    except Exception as exc:
        # Browser exceptions can embed URLs or page text; disclose the category only.
        print(f'STOP: {type(exc).__name__}; inspection did not complete', flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
