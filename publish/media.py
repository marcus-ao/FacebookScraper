"""只读核验编辑器缩略图数量及顺序；不代表后续排期或公开帖的媒体。"""
import hashlib
import io
from urllib.parse import urlsplit

from PIL import Image, ImageChops, ImageOps, ImageStat
from playwright.async_api import expect

from core import imagehash
from publish.business_suite import PublishStepError

MAX_BYTES = 25 * 1024 * 1024
MAX_DISTANCE = 8
MAX_RGB_ERROR = 12


def compare_ordered(expected_paths, rendered):
    if not expected_paths or len(expected_paths) != len(rendered):
        raise PublishStepError('编辑器缩略图数量与冻结图片不一致')
    expected, colours, sizes = [], [], []
    for path in expected_paths:
        with Image.open(path) as picture:
            picture = ImageOps.exif_transpose(picture)
            expected.append(imagehash.dhash_value(picture))
            colours.append(picture.convert('RGB').resize((64, 64), Image.Resampling.BILINEAR))
            sizes.append(picture.size)
    rows = []
    for index, body in enumerate(rendered):
        if not body or len(body) > MAX_BYTES:
            raise PublishStepError('编辑器图片无法在读取上限内核验')
        with Image.open(io.BytesIO(body)) as picture:
            picture = ImageOps.exif_transpose(picture)
            observed = imagehash.dhash_value(picture)
            dimensions = picture.size
            colour = picture.convert('RGB').resize((64, 64), Image.Resampling.BILINEAR)
        error = sum(ImageStat.Stat(ImageChops.difference(colours[index], colour)).mean) / 3
        ratio = (dimensions[0] / dimensions[1]) / (sizes[index][0] / sizes[index][1])
        if error > MAX_RGB_ERROR or abs(ratio - 1) > 0.01:
            raise PublishStepError('编辑器第 %d 张图的颜色或宽高比与冻结版本不一致' % (index + 1))
        distances = [imagehash.hamming(value, observed) for value in expected]
        if distances[index] > MAX_DISTANCE or distances[index] != min(distances):
            raise PublishStepError('编辑器第 %d 张图与冻结版本的顺序或内容不一致' % (index + 1))
        # Different source files with indistinguishable hashes require human review.
        source_bytes = expected_paths[index].read_bytes()
        if any(other != index and distance == distances[index]
               and expected_paths[other].read_bytes() != source_bytes for other, distance in enumerate(distances)):
            raise PublishStepError('相似图片无法唯一确认顺序，请人工核对编辑器')
        rows.append({'index': index, 'source_sha256': hashlib.sha256(source_bytes).hexdigest(),
                     'rendered_sha256': hashlib.sha256(body).hexdigest(),
                     'dimensions': list(dimensions), 'distance': distances[index],
                     'rgb_mean_error': round(error, 4), 'aspect_ratio_relative': round(ratio, 4)})
    return {'image_count': len(rows), 'order_verified': True,
            'method': 'ordered_composer_thumbnail_dhash_rgb', 'maximum_distance': MAX_DISTANCE,
            'maximum_rgb_mean_error': MAX_RGB_ERROR, 'images': rows}


async def verify_upload(page, paths, *, timeout=30):
    remove = page.get_by_role('button', name='Remove photo', exact=True)
    try:
        await expect(remove).to_have_count(len(paths), timeout=timeout * 1000)
        await page.get_by_text('Uploading media', exact=True).wait_for(state='hidden', timeout=timeout * 1000)
        rendered = []
        for index in range(len(paths)):
            # Anchor each image to its unique remove control's nearest listitem, avoiding nested duplicates.
            card = remove.nth(index).locator('xpath=ancestor::*[@role="listitem"][1]')
            img = card.locator('img')
            await expect(img).to_have_count(1, timeout=timeout * 1000)
            url = await img.evaluate('el => el.complete && el.naturalWidth > 0 ? el.currentSrc : null')
            parsed = urlsplit(url or '')
            if parsed.scheme != 'https' or not (parsed.hostname or '').endswith('.fbcdn.net'):
                raise PublishStepError('编辑器缩略图尚未成为可核验的 Meta 图片')
            response = await page.request.get(url, timeout=timeout * 1000, max_redirects=0)
            try:
                if response.status != 200:
                    raise PublishStepError('编辑器图片读取失败：HTTP %s' % response.status)
                rendered.append(await response.body())
            finally:
                await response.dispose()
        return compare_ordered(list(paths), rendered)
    except PublishStepError:
        raise
    except Exception as exc:
        raise PublishStepError('编辑器图片数量或顺序未能核验；未提交') from exc
