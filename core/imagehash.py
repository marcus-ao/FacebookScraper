"""统一使用 BILINEAR 重采样计算 dHash，保持距离阈值的比较口径。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

__all__ = ["dhash_value", "dhash_file", "hamming"]

# 采样到 9×8，逐行比较相邻像素得到 8×8 = 64 位。
_SAMPLE = (9, 8)
_FILTER = Image.Resampling.BILINEAR


def dhash_value(image: Image.Image) -> int:
    """一张已打开的图的 64 位 dHash。"""
    grayscale = image.convert("L").resize(_SAMPLE, _FILTER)
    pixels = list(grayscale.getdata())
    value = 0
    for row in range(8):
        start = row * 9
        for column in range(8):
            value = (value << 1) | int(
                pixels[start + column] > pixels[start + column + 1])
    return value


def dhash_file(path: Path) -> int:
    """从磁盘读一张图并算它的 dHash。"""
    with Image.open(path) as image:
        return dhash_value(image)


def hamming(left: int, right: int) -> int:
    """两个 dHash 之间的汉明距离，0..64。"""
    return (left ^ right).bit_count()
