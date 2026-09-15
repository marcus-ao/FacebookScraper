"""静态原图的字节识别与完整解码；不访问网络或归档目录。"""
from __future__ import annotations

import hashlib
import io
import warnings

from PIL import Image

_MIMES = {'image/jpeg': 'image/jpeg', 'image/jpg': 'image/jpeg', 'image/png': 'image/png',
          'image/webp': 'image/webp', 'image/gif': 'image/gif', 'image/avif': 'image/avif'}


def normalized_image_content_type(value: str | None) -> str | None:
    return _MIMES.get(value.split(';', 1)[0].strip().lower()) if value else None


def detected_image_content_type(data: bytes) -> str | None:
    if data.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    if data.startswith((b'GIF87a', b'GIF89a')):
        return 'image/gif'
    if len(data) >= 16 and data[4:8] == b'ftyp':
        box_size = int.from_bytes(data[:4], 'big')
        end = min(len(data), box_size if box_size >= 16 else len(data))
        brands = [data[i:i + 4] for i in range(8, end - 3, 4) if i != 12]
        if any(brand in {b'avif', b'avis'} for brand in brands):
            return 'image/avif'
    return None


def validated_image_content_type(value: str | None, data: bytes) -> str | None:
    declared = normalized_image_content_type(value)
    return declared if declared and detected_image_content_type(data) == declared else None


def image_facts(data: bytes, declared_type: str | None = None) -> dict | None:
    """签名和完整解码均通过后返回实际元信息；截断文件不能成为可复用原图。"""
    mime = (validated_image_content_type(declared_type, data) if declared_type is not None
            else detected_image_content_type(data))
    if not mime:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                width, height = image.size
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        return None
    return {'content_type': mime, 'byte_size': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
            'width': width, 'height': height}
