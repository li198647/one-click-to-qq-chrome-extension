# -*- coding: utf-8 -*-
"""生成扩展图标：蓝底 + 白色向上箭头（16 / 48 / 128）。"""
import os

from PIL import Image, ImageDraw

OUT = r"E:\workbuddywork\一键发到qq\extension\icons"

BLUE = (24, 95, 165, 255)
WHITE = (255, 255, 255, 255)


def make(size):
    s = size * 8
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.22), fill=BLUE)

    d.rounded_rectangle(
        [int(s * 0.43), int(s * 0.42), int(s * 0.57), int(s * 0.76)],
        radius=int(s * 0.05),
        fill=WHITE,
    )
    d.polygon(
        [
            (int(s * 0.50), int(s * 0.19)),
            (int(s * 0.75), int(s * 0.47)),
            (int(s * 0.25), int(s * 0.47)),
        ],
        fill=WHITE,
    )

    img.resize((size, size), Image.LANCZOS).save(
        os.path.join(OUT, "icon%d.png" % size)
    )


def main():
    os.makedirs(OUT, exist_ok=True)
    for n in (16, 48, 128):
        make(n)
    print("icons written to " + OUT)


if __name__ == "__main__":
    main()
