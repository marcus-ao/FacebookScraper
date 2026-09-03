r"""dHash：项目里唯一一份感知哈希实现。

**为什么单开一个模块。** 2026-09-02 的架构审查发现仓库里有两份 dHash：

    pipeline_assisted.py:313   .resize((9, 8), Image.Resampling.BILINEAR)
    localize_images.py:873     .resize((9, 8), Image.Resampling.LANCZOS)

同名、同算法、**不同的重采样滤波器**，因此对同一张图给出不同的 64 位值。
两者的消费者当时不重叠所以没炸，但这是一颗定时炸弹：任何人把其中一个的
距离阈值搬给另一个都会得到错误结论。

**统一到 BILINEAR**，依据是实测而不是偏好。2026-09-02 拿 8/27 那组真实
FB/IG 配对（5 张）两个滤波器各量了一遍：

    BILINEAR   [0, 0, 0, 0, 0]
    LANCZOS    [0, 0, 1, 0, 0]

配对闸是 ``DHASH_DISTANCE = 1``，两者都过得去；但 BILINEAR 是这条路径当初
标定用的滤波器，而且不吃掉阈值预算。另一边（图片产出的结构相似度）现在是
``[image].dhash_max_distance = -1``——闸关着，那些数字不驱动任何决策，
所以不该由它来定这个选择。

**只依赖 Pillow，不引 numpy**：一个汉明距离不值得多一个依赖。
"""
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
