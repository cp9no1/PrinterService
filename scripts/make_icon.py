# -*- coding: utf-8 -*-
"""生成本项目 exe 图标 app.ico（多尺寸）。"""
import os
from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app.ico")


def draw_icon(size=256):
    scale = size / 64.0
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def box(x1, y1, x2, y2, **kwargs):
        d.rounded_rectangle([x1 * scale, y1 * scale, x2 * scale, y2 * scale], **kwargs)

    # 蓝色圆角底板
    box(2, 2, 62, 62, radius=14 * scale, fill=(37, 99, 235, 255))
    # 打印机机身
    box(10, 24, 54, 46, radius=4 * scale, fill=(255, 255, 255, 255))
    # 进纸
    box(16, 8, 48, 24, radius=2 * scale, fill=(219, 234, 254, 255))
    # 出纸
    box(18, 36, 46, 50, radius=2 * scale, fill=(30, 64, 175, 255))
    # 指示灯
    d.ellipse([44 * scale, 28 * scale, 50 * scale, 34 * scale], fill=(34, 197, 94, 255))
    return img


def main():
    images = [draw_icon(s) for s in (16, 24, 32, 48, 64, 128, 256)]
    out = os.path.normpath(OUT)
    images[-1].save(out, format="ICO", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    print("saved", out)


if __name__ == "__main__":
    main()
