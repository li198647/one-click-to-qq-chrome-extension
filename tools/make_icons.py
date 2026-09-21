# -*- coding: utf-8 -*-
"""生成扩展图标：黑色企鹅（16 / 32 / 48 / 128）。

设计目标：
  - 一眼是"企鹅"，但在**造型上与 QQ 的企鹅明确区分开**：
    QQ 是圆胖正面、红围巾、眨眼；这里用**扁平的几何剪影 + 侧身朝右**
    （或正面但无围巾、几何分段的白脸/白肚），不带任何 QQ 的辨识特征。
  - 16px 下也要能认出来：形状尽量简单，细碎特征（眼睛、翅尖）都放大。

跑法：
    <venv>/python.exe tools/make_icons.py            # 写入 extension/icons
    <venv>/python.exe tools/make_icons.py --preview  # 只出对比图，不覆盖正式图标
对比图写到 tools/icon_preview.png（tools/*.png 已在 .gitignore 里）。
"""
import argparse
import os

from PIL import Image, ImageDraw

OUT = r"E:\workbuddywork\一键发到qq\extension\icons"
TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
# 桥那个托盘图标（v1.0.6）从这里取，见 write_tray_icon()
TRAY_ICO = os.path.join(ROOT, "bridge", "qq.ico")

BLACK = (28, 28, 30, 255)
WHITE = (255, 255, 255, 255)
ORANGE = (245, 150, 40, 255)
TILE_BLUE = (230, 241, 251, 255)
TILE_GRAY = (245, 244, 240, 255)
BLUE = (24, 95, 165, 255)


# ------------------------------------------------------------ 通用零件

def _ell(d, box, fill):
    d.ellipse([int(box[0]), int(box[1]), int(box[2]), int(box[3])], fill=fill)


def _poly(d, pts, fill):
    d.polygon([(int(x), int(y)) for x, y in pts], fill=fill)


def _tile(d, s, color, radius=0.22):
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * radius), fill=color)


def _feet(d, s, color, y=0.80, spread=0.11):
    """两只小脚：两个朝外的三角。"""
    for sign in (-1, 1):
        cx = 0.5 + sign * spread
        _poly(d, [
            ((cx - 0.10) * s, y * s),
            ((cx + 0.10) * s, y * s),
            ((cx + sign * 0.13) * s, (y + 0.08) * s),
        ], color)


# ------------------------------------------------------------ 造型 A：正面几何企鹅

def draw_front(d, s, bg=None, chest_arrow=False):
    if bg:
        _tile(d, s, bg)

    # 身体（一枚略方的蛋）
    _ell(d, [0.22 * s, 0.16 * s, 0.78 * s, 0.84 * s], BLACK)

    # 白色脸盘（只到眼睛下方一点，和肚子之间留一道黑边 —— 这是与 QQ 最明显的差别）
    _ell(d, [0.31 * s, 0.21 * s, 0.69 * s, 0.50 * s], WHITE)

    # 白肚
    _ell(d, [0.34 * s, 0.56 * s, 0.66 * s, 0.80 * s], WHITE)

    # 眼睛：够大，缩到 16px 还能看见两点
    for x in (0.415, 0.585):
        _ell(d, [(x - 0.037) * s, (0.295 - 0.037) * s,
                 (x + 0.037) * s, (0.295 + 0.037) * s], BLACK)

    # 喙：朝下的三角
    _poly(d, [(0.435 * s, 0.385 * s), (0.565 * s, 0.385 * s), (0.50 * s, 0.475 * s)], ORANGE)

    _feet(d, s, ORANGE)

    if chest_arrow:
        # 肚子上一个朝上的箭头（沿用旧图标的含义：这一键是"发出去"）
        _poly(d, [(0.50 * s, 0.575 * s), (0.585 * s, 0.675 * s), (0.415 * s, 0.675 * s)], BLUE)
        d.rounded_rectangle([0.465 * s, 0.665 * s, 0.535 * s, 0.755 * s],
                            radius=int(s * 0.02), fill=BLUE)


# ------------------------------------------------------------ 造型 B：侧身剪影

def draw_side(d, s, bg=None, chest_arrow=False):
    if bg:
        _tile(d, s, bg)

    # 头 + 身体：两个椭圆叠成一个"保龄球瓶"式的侧影
    _ell(d, [0.28 * s, 0.12 * s, 0.72 * s, 0.52 * s], BLACK)
    _ell(d, [0.22 * s, 0.32 * s, 0.76 * s, 0.86 * s], BLACK)

    # 白脸盘 + 白肚**连通**成一片（真实企鹅就是这样，而且 16px 下白色面积
    # 越大越不容易糊成一团黑）。做法：两个椭圆 + 中间一块白色矩形接起来。
    _ell(d, [0.44 * s, 0.20 * s, 0.76 * s, 0.50 * s], WHITE)
    _ell(d, [0.38 * s, 0.54 * s, 0.72 * s, 0.84 * s], WHITE)
    d.rectangle([0.50 * s, 0.34 * s, 0.72 * s, 0.66 * s], fill=WHITE)

    # 眼睛（黑点落在白脸盘里）
    _ell(d, [0.545 * s, 0.265 * s, 0.625 * s, 0.345 * s], BLACK)

    # 喙：朝右的三角
    _poly(d, [(0.72 * s, 0.325 * s), (0.90 * s, 0.375 * s), (0.72 * s, 0.425 * s)], ORANGE)

    # 翅：身体左侧一片，略外撇才有"企鹅"味
    _poly(d, [(0.30 * s, 0.44 * s), (0.40 * s, 0.48 * s), (0.35 * s, 0.80 * s), (0.22 * s, 0.70 * s)],
          BLACK)

    # 脚
    _poly(d, [(0.36 * s, 0.82 * s), (0.60 * s, 0.82 * s), (0.66 * s, 0.90 * s), (0.40 * s, 0.90 * s)],
          ORANGE)

    if chest_arrow:
        # 白肚里一个朝上的箭头 —— 沿用旧图标的含义：这一键是"发出去"。
        _poly(d, [(0.545 * s, 0.555 * s), (0.625 * s, 0.645 * s), (0.465 * s, 0.645 * s)], BLUE)
        d.rounded_rectangle([0.515 * s, 0.635 * s, 0.575 * s, 0.715 * s],
                            radius=int(s * 0.02), fill=BLUE)


VARIANTS = {
    "A": ("正面·白底", lambda d, s: draw_front(d, s, bg=WHITE)),
    "B": ("正面·浅蓝底", lambda d, s: draw_front(d, s, bg=TILE_BLUE)),
    "C": ("正面·肚上箭头", lambda d, s: draw_front(d, s, bg=WHITE, chest_arrow=True)),
    "D": ("侧身·白底", lambda d, s: draw_side(d, s, bg=WHITE)),
    "E": ("侧身·浅蓝底", lambda d, s: draw_side(d, s, bg=TILE_BLUE)),
    "F": ("侧身·透明无底", lambda d, s: draw_side(d, s, bg=None)),
    "G": ("侧身·白底+箭头", lambda d, s: draw_side(d, s, bg=WHITE, chest_arrow=True)),
    "H": ("侧身·浅灰底", lambda d, s: draw_side(d, s, bg=TILE_GRAY)),
}


def render(key, size):
    s = size * 8
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    VARIANTS[key][1](d, s)
    return img.resize((size, size), Image.LANCZOS)


def write_icons(key="A"):
    os.makedirs(OUT, exist_ok=True)
    for n in (16, 32, 48, 128):
        render(key, n).save(os.path.join(OUT, "icon%d.png" % n), optimize=True)
    print("icons written to " + OUT + "  (variant " + key + ")")
    write_tray_icon()


def write_tray_icon():
    """把图标打包成一个多尺寸 .ico，给桥的托盘图标用（v1.0.6）。

    为什么是打包而不是重新 render：直接从 `extension/icons/*.png` 读，
    托盘图标和扩展图标就**必然是同一份**。以后改图标造型也不会漏掉它。

    为什么托盘非得用 .ico：`LoadImageW(..., IMAGE_ICON, LR_LOADFROMFILE)`
    只认 .ico / .bmp / .cur，**不认 PNG**。
    """
    os.makedirs(os.path.dirname(TRAY_ICO), exist_ok=True)
    big = Image.open(os.path.join(OUT, "icon48.png")).convert("RGBA")
    big.save(TRAY_ICO, format="ICO", sizes=[(16, 16), (32, 32), (48, 48)])
    print("tray icon written to " + TRAY_ICO)


def write_preview():
    """对比图：每行一个方案。
    第 1 列 = 128px（浅色工具栏），第 2 列 = 128px（深色工具栏），
    第 3 列 = 16px 放大 8 倍 —— 真正在地址栏里就是这个大小。"""
    col = 160
    row_h = 172
    img = Image.new("RGB", (col * 3 + 40, row_h * len(VARIANTS) + 16), (255, 255, 255))
    d = ImageDraw.Draw(img)

    for i, key in enumerate(sorted(VARIANTS)):
        y = 16 + i * row_h
        big = render(key, 128)

        light = Image.new("RGB", (128, 128), (255, 255, 255))
        light.paste(big, (0, 0), big)
        img.paste(light, (20, y + 22))

        dark = Image.new("RGB", (128, 128), (43, 43, 43))
        dark.paste(big, (0, 0), big)
        img.paste(dark, (col + 20, y + 22))

        small = render(key, 16).resize((128, 128), Image.NEAREST)
        cell = Image.new("RGB", (128, 128), (255, 255, 255))
        cell.paste(small, (0, 0), small)
        img.paste(cell, (col * 2 + 20, y + 22))

        d.text((22, y), key + " " + VARIANTS[key][0], fill=(30, 30, 30))
        d.text((col + 22, y), "深色工具栏", fill=(120, 120, 120))
        d.text((col * 2 + 22, y), "16px 放大 8 倍", fill=(120, 120, 120))
        d.line([(0, y + row_h - 8), (img.width, y + row_h - 8)], fill=(228, 228, 228))

    path = os.path.join(TOOLS, "icon_preview.png")
    img.save(path)
    print("preview written to " + path)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--preview", action="store_true", help="只出对比图")
    p.add_argument("--variant", default="A", help="写入正式图标时用哪个方案")
    a = p.parse_args()
    if a.preview:
        write_preview()
    else:
        write_icons(a.variant)
