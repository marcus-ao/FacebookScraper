"""Bounded, passive content-identity evidence for the Planner detail diagnostic."""
import asyncio
import base64
import json
import re
from urllib.parse import parse_qs, urlsplit, urlunsplit, urlencode

from core.meta_json import response_documents


IDS = {'id', 'entity_id', 'content_id', 'post_id', 'story_id', 'media_id', 'owner_id', 'actor_id',
       'page_id', 'instagram_id', 'instagram_account_id', 'facebook_id', 'fbid', 'pk',
       'story_fbid', 'legacy_fbid', 'ig_media_id', 'instagram_media_id'}
TIMES = {'creation_time', 'created_time', 'publish_time', 'published_time', 'scheduled_time',
         'timestamp', 'taken_at', 'taken_at_timestamp'}
KINDS = {'__typename', 'media_type', 'content_type', 'platform', 'status', 'is_published',
         'is_story', 'is_video', 'is_reel', 'is_crosspost', 'is_crossposted'}
NAMES = {'Neakasa Deutschland', 'neakasa.de', 'neakasa.global', 'neakasa.tech'}

# Five preview selectors in a row were written from screenshots and each failed on
# the server. Report what the reader's own region search sees, so one run decides
# instead of the next guess. Bounded to 6 levels x 20 nodes; text only when the
# account is already allowlisted, otherwise its length.
PREVIEW_STRUCTURE = r'''(names) => {
  const visible=n=>!!n.getClientRects().length&&getComputedStyle(n).visibility!=='hidden';
  const own=n=>(n.innerText||'').trim();
  const headings=()=>[...document.querySelectorAll('h1,h2,h3,h4,h5,h6,[role="heading"]')].filter(visible);
  const frames=[...document.querySelectorAll('#instagram_story_preview_frame')];
  const label=headings().filter(n=>own(n)==='Feed preview');
  const result={preview_headings:label.length, instagram_frames:frames.length,
    instagram_frames_visible:frames.filter(visible).length,
    loading_preview:headings().some(n=>/^Loading preview/i.test(own(n))), levels:[]};
  if(label.length!==1) return result;
  for(let root=label[0].parentElement,depth=0;root&&depth<6;root=root.parentElement,depth++) {
    const stop=!!root.querySelector('[role="tab"]');
    const nodes=[...root.querySelectorAll('div,span')].filter(n=>visible(n)&&own(n)
        &&![...n.children].some(c=>own(c)===own(n))).slice(0,20).map(n=>{
      let image=false;
      for(let row=n.parentElement,up=0;row&&up<3&&!image;row=row.parentElement,up++)
        image=[...row.querySelectorAll('img,svg,image,[role="img"]')].some(visible);
      return {tag:n.tagName, text:names.includes(own(n))?own(n):null,
              text_length:own(n).length, image_within_3:image};
    });
    result.levels.push({depth, stops_at_tabs:stop, tightest_nodes:nodes});
    if(stop) break;
  }
  return result;
}'''


def safe_field(key):
    return bool(re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]{0,79}', key)) and not re.search(
        r'token|secret|auth|session|cookie|password|credential', key, re.I)


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
            if not safe_field(key):
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


def entity_identity_fields(document):
    """Expose missing identity field names/types only on the root entity relation.

    Opaque IDs may encode JSON. Decode bounded JSON data (never execute it), then
    apply the same redaction; decoded values are diagnostic evidence, not native IDs.
    """
    entity = (document.get('data') or {}).get('tofu_entity') if isinstance(document, dict) else None
    budget = 500

    def shape(node, key='', depth=0):
        nonlocal budget
        budget -= 1
        if budget < 0 or depth > 8:
            return {'type':'limit'}
        if isinstance(node, dict):
            return {name:shape(value,name,depth+1) for name,value in list(node.items())[:80]
                    if safe_field(name) and name not in {'entity_insights','ads_info','lwi_info'}}
        if isinstance(node, list):
            return [shape(value,depth=depth+1) for value in node[:5]]
        identity_key = key in IDS or bool(re.search(r'_id$|ID$',key))
        if identity_key and re.fullmatch(r'\d{6,30}', str(node)):
            return str(node)
        if isinstance(node, str):
            clean = content_fields({key:node})
            if clean:
                return clean[key] if key in clean else clean
            result = {'type':'string','length':len(node)}
            if identity_key and len(node) <= 8192:
                candidates = [node]
                try:
                    candidates.append(base64.b64decode(node, validate=True).decode('utf-8'))
                except (ValueError, UnicodeError):
                    pass
                for candidate in candidates:
                    try:
                        decoded = json.loads(candidate)
                    except ValueError:
                        continue
                    if isinstance(decoded, (dict,list)):
                        result['decoded'] = shape(decoded, depth=depth+1)
                        break
            return result
        return {'type':type(node).__name__}

    return shape(entity) if isinstance(entity, dict) else None


class ResponseEvidence:
    """Listen only to responses already caused by the UI; arrival is not provenance."""
    def __init__(self, page, emit, *, identity_only=False):
        self.page, self.emit = page, emit
        self.identity_only = identity_only
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
            for document in response_documents(raw):
                if self.identity_only:
                    fields = entity_identity_fields(document)
                    if fields:
                        self.emit({'ENTITY_IDENTITY':fields, 'response_status':response.status})
                    continue
                fields = content_fields(document)
                if fields:
                    self.emit({'RESPONSE_EVIDENCE': fields, 'during_view': view,
                               'response_status': response.status})
        except Exception as exc:
            self.emit({'RESPONSE_EVIDENCE': 'unreadable', 'during_view': view,
                       'read_error_type': type(exc).__name__})

    async def embedded(self):
        """Inspect inert JSON already in this detail document; never execute scripts."""
        snapshot = await asyncio.wait_for(self.page.locator('script[type="application/json"]').evaluate_all('''nodes => {
          let characters=0;const records=[];
          for(const [index,node] of nodes.entries()){
            const text=node.textContent||'';
            if(index>=100||characters+text.length>4000000)continue;
            characters+=text.length;records.push({index,text});
          }
          return {found:nodes.length,records};
        }'''), 8)
        emitted, invalid = 0, 0
        for record in snapshot['records']:
            try:
                fields = content_fields(json.loads(record['text']))
            except ValueError:
                invalid += 1
                continue
            if fields:
                emitted += 1
                self.emit({'EMBEDDED_EVIDENCE': fields, 'script_index': record['index'],
                           'during_view': self.view})
        self.emit({'EMBEDDED_SUMMARY': {'found': snapshot['found'],
                  'inspected': len(snapshot['records']), 'emitted': emitted, 'invalid': invalid,
                  'skipped': snapshot['found']-len(snapshot['records'])}})

    async def finish(self):
        self.page.remove_listener('response', self.observe)
        if self.tasks:
            await asyncio.gather(*self.tasks)
        self.emit({'RESPONSE_SUMMARY': {'observed': len(self.tasks), 'limit': 40}})
