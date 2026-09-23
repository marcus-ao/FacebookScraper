"""Target-detail media observations and durable comparison evidence.

Only a layout adapter with independent completeness and ordering evidence may
set MediaCapture.complete. DOM image candidates alone never establish that.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit

from PIL import Image

from core.paid_model import atomic_write_json
from publish import media
from publish.business_suite import PublishStepError, SENSITIVE_INPUT_SELECTOR


# Diagnostic only: img nodes include avatars, thumbnails and hidden duplicates.
# No recorded scheduled layout yet proves which of them form the complete post.
_STRUCTURE = r'''root => {
 const visible=n=>!!n.getClientRects().length&&getComputedStyle(n).visibility!=='hidden';
 const info=n=>({tag:n.tagName,role:n.getAttribute('role')||'',visible:visible(n),
   child_count:n.children.length,text_length:(n.textContent||'').length,
   label_length:(n.getAttribute('aria-label')||'').length,
   position:n.getAttribute('aria-posinset'),set_size:n.getAttribute('aria-setsize')});
 const path=n=>{const parts=[];for(let p=n;p&&p!==root;p=p.parentElement)
   parts.unshift([...p.parentElement.children].indexOf(p));return parts;};
 const images=[...root.querySelectorAll('img')];
 const controls=[...root.querySelectorAll('button,[role=button],[role=tab]')];
 return {image_nodes:images.length,images:images.slice(0,64).map(n=>({...info(n),path:path(n),
   url:n.currentSrc||'',complete:n.complete,natural_width:n.naturalWidth,natural_height:n.naturalHeight,
   ancestors:[n.parentElement,n.parentElement?.parentElement,n.parentElement?.parentElement?.parentElement]
     .filter(p=>p&&root.contains(p)).map(info)})),
   controls:controls.slice(0,64).map(n=>({...info(n),path:path(n),disabled:n.disabled===true,
     navigation_label:/^(Next|Previous)( (photo|image|slide))?$|^\d+ (of|\/) \d+$/i.test(
       n.getAttribute('aria-label')||n.innerText||'')?(n.getAttribute('aria-label')||n.innerText):''}))};
}'''


@dataclass(frozen=True)
class MediaCapture:
    bodies: tuple[bytes, ...]
    structure: dict
    screenshot: bytes = b''
    complete: bool = False
    image_count: int | None = None
    order_basis: str = ''
    error: str = 'layout_unverified'


async def collect(dialog, *, timeout=30):
    """Read bounded candidates from the already identity-checked target dialog.

    This diagnostic collector never clicks navigation or claims completeness.
    A real channel layout and its count/end-of-list evidence are still required.
    """
    bodies, structure, screenshot = [], {}, b''
    error = 'layout_unverified'
    deadline = time.monotonic() + timeout
    try:
        async with asyncio.timeout(timeout):
            previous, stable_since = None, time.monotonic()
            while True:
                raw = await dialog.evaluate(_STRUCTURE)
                # Keep the last bounded observation even if loading never completes.
                # Signed URLs remain temporary inputs, never evidence fields.
                structure = {**raw, 'images': [{key: value for key, value in item.items() if key != 'url'}
                                               for item in raw['images']]}
                if raw != previous:
                    previous, stable_since = raw, time.monotonic()
                loaded = all(item['complete'] and item['natural_width'] > 0 and item['url']
                             for item in raw['images'])
                if raw['image_nodes'] > 64:
                    error = 'image_limit_exceeded'
                    break
                if loaded and time.monotonic() - stable_since >= .4:
                    break
                error = 'image_not_loaded' if not loaded else 'media_unstable'
                await asyncio.sleep(.05)
            if loaded and raw['image_nodes'] <= 64:
                error = 'layout_unverified'
                total = 0
                for item, saved in zip(raw['images'], structure['images']):
                    url = item['url']
                    saved['url_sha256'] = hashlib.sha256(url.encode('utf-8')).hexdigest()
                    parsed = urlsplit(url)
                    if (parsed.scheme != 'https' or parsed.username or parsed.password
                            or not (parsed.hostname or '').endswith('.fbcdn.net')):
                        error = saved['error'] = 'image_source_unverified'
                        continue
                    saved['host'] = parsed.hostname
                    response = None
                    try:
                        response = await dialog.page.request.get(url, max_redirects=0,
                            timeout=max(1, (deadline - time.monotonic()) * 1000))
                        if response.status != 200:
                            error = saved['error'] = 'image_download_failed'
                            saved['http_status'] = response.status
                            continue
                        if int(response.headers.get('content-length', '0')) > media.MAX_BYTES - total:
                            error = saved['error'] = 'image_limit_exceeded'
                            break
                        body = await response.body()
                        if not body:
                            error = saved['error'] = 'image_empty'
                            continue
                        if len(body) > media.MAX_BYTES - total:
                            error = saved['error'] = 'image_limit_exceeded'
                            break
                        saved['body_index'] = len(bodies)
                        saved['rendered_sha256'] = hashlib.sha256(body).hexdigest()
                        bodies.append(body)
                        total += len(body)
                        try:
                            with Image.open(io.BytesIO(body)) as picture:
                                picture.verify()
                        except (OSError, ValueError, Image.DecompressionBombError):
                            error = saved['error'] = 'image_decode_failed'
                    except (ValueError, OSError):
                        error = saved['error'] = 'image_download_failed'
                    finally:
                        if response is not None:
                            await response.dispose()
                if await dialog.evaluate(_STRUCTURE) != raw:
                    error = 'media_list_changed'
    except TimeoutError:
        if error not in {'image_not_loaded', 'media_unstable'}:
            error = 'media_read_timeout'
    except Exception:
        error = 'media_read_failed'
    try:
        screenshot = await dialog.screenshot(timeout=max(100, timeout * 1000),
            mask=[dialog.page.locator(SENSITIVE_INPUT_SELECTOR)])
    except Exception:
        structure['screenshot_error'] = 'detail_screenshot_failed'
    structure['missing_fields'] = ['media_container', 'remote_total_or_end', 'ordered_media',
                                   'carousel_loading_and_thumbnail_relationship']
    return MediaCapture(tuple(bodies), structure, screenshot=screenshot, error=error)


def binding_for(row):
    """Bind observations to this attempt's frozen version, never the current draft."""
    keys = ('attempt_id', 'snapshot_id', 'platform', 'post_id', 'scheduled_at',
            'ui_timezone', 'final_text_sha256', 'source_fingerprint', 'remote_id')
    return {**{key: row.get(key, '') for key in keys},
            'source_fingerprint_version': row.get('source_fingerprint_version', 1),
            'target_channels': list(row.get('target_channels') or ()),
            'image_sha256': list(row.get('image_sha256') or ())}


def verify_capture(capture, paths, evidence_dir, binding):
    """Persist bytes and diagnostics before returning a successful media receipt."""
    count = capture.image_count
    result = {'image_count': count if type(count) is int and count >= 0 else None,
              'expected_image_count': len(paths), 'order_verified': False,
              'capture_complete': capture.complete is True,
              'order_basis': capture.order_basis, 'images': [], 'error': capture.error}
    complete = (capture.complete is True and type(count) is int and count > 0
                and count == len(capture.bodies) and bool(capture.order_basis) and not capture.error)
    if complete:
        try:
            comparison = media.compare_ordered(paths, capture.bodies, surface='排期详情')
            if [item['source_sha256'] for item in comparison['images']] != binding.get('image_sha256'):
                raise PublishStepError('冻结图片与本次 attempt 清单不一致')
            result.update(comparison, error='')
        except (OSError, ValueError, PublishStepError) as exc:
            result.update(error='comparison_failed', comparison_error=str(exc))
    elif not result['error']:
        result['error'] = 'incomplete_remote_list'
    if not capture.screenshot:
        result.update(order_verified=False, error='detail_screenshot_missing')
    directory = Path(evidence_dir) / uuid4().hex
    try:
        directory.mkdir(parents=True)
        files = {}
        payloads = {f'image-{index:03d}.bin': body for index, body in enumerate(capture.bodies)}
        if capture.screenshot:
            payloads['detail.png'] = capture.screenshot
        for name, body in payloads.items():
            with (directory / name).open('xb') as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            files[name] = hashlib.sha256(body).hexdigest()
        manifest = directory / 'media.json'
        atomic_write_json(manifest, {'schema_version': 1,
            'observed_at': datetime.now(timezone.utc).isoformat(), 'binding': binding,
            'structure': capture.structure, 'files': files, 'remote_media': result})
        result = dict(result, evidence_version=1, evidence_file=str(manifest),
                      evidence_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest())
        if result['order_verified'] and not evidence_intact(result, binding):
            raise OSError('evidence verification failed')
    except (OSError, ValueError, TypeError):
        result.update(order_verified=False, error='evidence_write_failed')
    return {'remote_images_verified': result['order_verified'] is True, 'remote_media': result}


def evidence_intact(proof, binding):
    """A retained receipt cannot certify media after its evidence was lost or changed."""
    try:
        if proof.get('evidence_version') != 1 or proof.get('order_verified') is not True:
            return False
        path = Path(proof['evidence_file'])
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != proof['evidence_sha256']:
            return False
        manifest = json.loads(body)
        expected = {key: value for key, value in proof.items()
                    if key not in {'evidence_version', 'evidence_file', 'evidence_sha256'}}
        if (manifest.get('schema_version') != 1 or manifest.get('binding') != binding
                or manifest.get('remote_media') != expected):
            return False
        files = manifest['files']
        needed = {'detail.png', *(f'image-{i:03d}.bin' for i in range(proof['image_count']))}
        if set(files) != needed:
            return False
        for name, digest in files.items():
            if hashlib.sha256((path.parent / name).read_bytes()).hexdigest() != digest:
                return False
        return all(files[f'image-{i:03d}.bin'] == item['rendered_sha256']
                   and item['index'] == i for i, item in enumerate(proof['images']))
    except (OSError, ValueError, KeyError, TypeError):
        return False
