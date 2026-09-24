"""编辑器检查上传状态；远端图片比较单独记录，不阻断人工选图。"""
import hashlib
import io
import time
from collections import Counter

from PIL import Image, ImageChops, ImageOps, ImageStat
from playwright.async_api import expect

from core import imagehash
from publish.business_suite import PublishStepError

MAX_BYTES = 25 * 1024 * 1024
MAX_DISTANCE = 8
MAX_RGB_ERROR = 12


def compare_ordered(expected_paths, rendered, *, surface='编辑器'):
    if not expected_paths or len(expected_paths) != len(rendered):
        raise PublishStepError(surface + '图片数量与冻结图片不一致')
    expected, colours, sizes = [], [], []
    sources = [path.read_bytes() for path in expected_paths]
    for source in sources:
        with Image.open(io.BytesIO(source)) as picture:
            picture = ImageOps.exif_transpose(picture)
            expected.append(imagehash.dhash_value(picture))
            colours.append(picture.convert('RGB').resize((64, 64), Image.Resampling.BILINEAR))
            sizes.append(picture.size)
    rows = []
    for index, body in enumerate(rendered):
        if not body or len(body) > MAX_BYTES:
            raise PublishStepError(surface + '图片无法在读取上限内核验')
        try:
            with Image.open(io.BytesIO(body)) as picture:
                picture = ImageOps.exif_transpose(picture)
                observed = imagehash.dhash_value(picture)
                dimensions = picture.size
                colour = picture.convert('RGB').resize((64, 64), Image.Resampling.BILINEAR)
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise PublishStepError(surface + '图片无法解码') from exc
        error = sum(ImageStat.Stat(ImageChops.difference(colours[index], colour)).mean) / 3
        ratio = (dimensions[0] / dimensions[1]) / (sizes[index][0] / sizes[index][1])
        if error > MAX_RGB_ERROR or abs(ratio - 1) > 0.01:
            raise PublishStepError(surface + '第 %d 张图的颜色或宽高比与冻结版本不一致' % (index + 1))
        distances = [imagehash.hamming(value, observed) for value in expected]
        if distances[index] > MAX_DISTANCE or distances[index] != min(distances):
            raise PublishStepError(surface + '第 %d 张图与冻结版本的顺序或内容不一致' % (index + 1))
        # Different source files with indistinguishable hashes require human review.
        source_bytes = sources[index]
        if any(other != index and distance == distances[index]
               and sources[other] != source_bytes for other, distance in enumerate(distances)):
            raise PublishStepError('相似图片无法唯一确认顺序，请人工核对' + surface)
        rows.append({'index': index, 'source_sha256': hashlib.sha256(source_bytes).hexdigest(),
                     'rendered_sha256': hashlib.sha256(body).hexdigest(),
                     'dimensions': list(dimensions), 'distance': distances[index],
                     'rgb_mean_error': round(error, 4), 'aspect_ratio_relative': round(ratio, 4)})
    return {'image_count': len(rows), 'order_verified': True,
            'method': 'ordered_dhash_rgb', 'maximum_distance': MAX_DISTANCE,
            'maximum_rgb_mean_error': MAX_RGB_ERROR, 'images': rows}


async def verify_upload(page, paths, *, timeout=30, previous=None):
    """按冻结清单交图后核对附件数量；缩略图地址和像素不代表上传是否成功。"""
    paths = list(paths)
    if not paths:
        raise PublishStepError('没有选定上传图片；未提交')
    remove = page.get_by_role('button', name='Remove photo', exact=True)
    deadline = time.monotonic() + timeout
    remaining = lambda: max(1, (deadline - time.monotonic()) * 1000)
    try:
        await expect(remove).to_have_count(len(paths), timeout=remaining())
    except Exception as exc:
        raise PublishStepError('编辑器图片数量与已选图片不一致（应为 %d 张）；未提交' % len(paths)) from exc
    try:
        await page.get_by_text('Uploading media', exact=True).wait_for(state='hidden', timeout=remaining())
    except Exception as exc:
        raise PublishStepError('Business Suite 图片上传仍未完成；请查看上传提示，未提交') from exc
    try:
        await expect(remove).to_have_count(len(paths), timeout=remaining())
    except Exception as exc:
        raise PublishStepError('上传结束后的图片数量与已选图片不一致；未提交') from exc

    # 只识别同一组附件明确调序；blob/CDN 切换、缺缩略图和重新渲染不作为失败。
    # 地址只在内存中取摘要，不下载、不持久化签名 URL。
    urls = await remove.evaluate_all('''buttons => buttons.map(button => {
        const images = button.closest('[role="listitem"]')?.querySelectorAll('img');
        return images?.length === 1 ? images[0].currentSrc || images[0].getAttribute('src') : null;
    })''')
    keys = [hashlib.sha256(url.encode('utf-8')).hexdigest() if url else None for url in urls]
    prior = (previous or {}).get('attachment_keys')
    unchanged = None
    if prior and all(prior) and all(keys) and Counter(prior) == Counter(keys):
        unchanged = prior == keys
        if not unchanged:
            raise PublishStepError('编辑器中的图片顺序在准备后发生变化；未提交')
    return {'image_count': len(paths), 'order_verified': False,
            'method': 'file_chooser_attachment_count', 'attachment_keys': keys,
            'attachment_order_unchanged': unchanged,
            'images': [{'index': index, 'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                       for index, path in enumerate(paths)]}
