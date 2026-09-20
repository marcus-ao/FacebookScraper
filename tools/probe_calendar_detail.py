"""Inspect one already-open Planner detail and its channel tabs without editing content."""
import argparse
import asyncio
import json
import re
import sys
from datetime import date, time
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.chrome import attach
from core.config import cfg
from core.console import force_utf8
from publish.journal import PublishOperationLock


SNAPSHOT = r'''() => {
 const visible=n=>!!n.getClientRects().length&&getComputedStyle(n).visibility!=='hidden';
 const text=n=>(n.innerText||n.textContent||'').replace(/\s+/g,' ').trim();
 const fixed=/^(This content has no text|Feed preview|Loading preview|Total performance|Facebook|Instagram|Neakasa Deutschland|neakasa\.(de|global|tech))$/;
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
 return {page:link(location.href),anchors:anchors.slice(0,20).map(n=>({node:info(n),
   ancestors:[n.parentElement,n.parentElement?.parentElement,n.parentElement?.parentElement?.parentElement]
     .filter(Boolean).map(p=>({node:info(p),children:[...p.children].slice(0,12).map(info)}))})),
   headings:nodes.filter(n=>n.matches('[role=heading],h1,h2,h3')).slice(0,12).map(info),
   tabs:nodes.filter(n=>n.matches('[role=tab],[role=tabpanel]')).map(info),
   links:nodes.filter(n=>n.matches('a[href]')).slice(0,40).map(info)};
}'''


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


async def inspect(browser, *, day, clock, kind):
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
    try:
        for channel in (None, 'Facebook', 'Instagram'):
            if channel:
                if original is None:
                    print('SKIP: no initial channel selection; tabs left unchanged', flush=True)
                    break
                tab = page.get_by_role('tab', name=channel, exact=True)
                if await tab.count() != 1:
                    continue
                await tab.click(timeout=5000)
            for sample in range(2):
                if sample:
                    await asyncio.sleep(1)
                for index, frame in enumerate(page.frames):
                    try:
                        value = await asyncio.wait_for(frame.evaluate(SNAPSHOT), 8)
                        value.update(view=channel or 'initial', sample=sample, frame=index)
                        emit(value)
                    except Exception as exc:
                        emit({'view': channel, 'frame': index, 'read_error_type': type(exc).__name__})
    finally:
        if original:
            await page.get_by_role('tab', name=original, exact=True).click(timeout=5000)
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
            return await inspect(browser, day=args.date, clock=args.time, kind=args.kind)
        finally:
            await pw.stop()


def main():
    force_utf8()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', type=date.fromisoformat, required=True)
    parser.add_argument('--time', type=time.fromisoformat, required=True, help='UI local time, e.g. 18:39')
    parser.add_argument('--kind', choices=('Post', 'Story', 'Reel', 'Video', 'Live'), default='Story')
    args = parser.parse_args()
    try:
        return 0 if asyncio.run(run(args)) else 2
    except Exception as exc:
        # Browser exceptions can embed URLs or page text; disclose the category only.
        print(f'STOP: {type(exc).__name__}; inspection did not complete', flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
