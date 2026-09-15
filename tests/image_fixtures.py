"""可完整解码的微型图片，用于离线模拟 CDN 响应。"""
import io

from PIL import Image


def image_bytes(format='JPEG', color='red'):
    buffer = io.BytesIO()
    Image.new('RGB', (4, 3), color).save(buffer, format=format)
    return buffer.getvalue()
