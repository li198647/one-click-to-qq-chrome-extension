# -*- coding: utf-8 -*-
"""
v1.0.1 图片通路 · 真机验证

为什么直接 import 函数来调，而不是 POST /send：
  木木的桥此刻正用 18761 端口跑着 v1.0.0，而且已经连着 QQ 十几个小时。
  为了测一次就把它关掉、或者在同一台机器上再起一个实例去抢 WebSocket
  连接，都不合适。把新写的函数 import 进来直接调，能验证"新代码本身
  对不对"，测完完全不影响正在跑的那个桥。
  代价：HTTP 那一层（client_max_size、路由分布）没被这次覆盖 ——
  那部分由 tools/verify-extension.js 的存在性断言兜着。

测三件事：
  A. 缩尺寸 —— 临时把软限压到 200KB，逼它走缩尺寸分支（纯本地，不发网络）
  B. 真机发一张图到手机（走完整的新代码：探测格式 → 上传 → msg_type=7）
  C. 非 PNG 格式的探测与转 PNG 兜底（纯本地）
"""

import asyncio
import base64
import importlib.util
import io
import json
import os
import random

BASE = r"E:\workbuddywork\一键发到qq"

out = []


def log(*a):
    line = " ".join(str(x) for x in a)
    out.append(line)
    print(line)


# ---------------------------------------------------------------- 加载桥模块
# 模块顶层只读 config / state 和建日志目录；起服务在 main() 里，不会被触发。
spec = importlib.util.spec_from_file_location(
    "qb", os.path.join(BASE, "bridge", "qq_bridge.py")
)
qb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qb)

log("桥模块加载 OK，VERSION =", qb.VERSION, "| Pillow =", qb.HAVE_PIL)
log("access_token 自建通路:", "已就绪" if hasattr(qb, "get_access_token") else "缺失")
log("-" * 62)

from PIL import Image  # noqa: E402

# ---------------------------------------------------------------- A. 缩尺寸
log("【A】缩尺寸逻辑（把软限临时压到 200KB 逼它触发）")

big = Image.new("RGB", (4000, 3000), (30, 30, 40))
px = big.load()
random.seed(7)
for y in range(0, 3000, 3):
    for x in range(0, 4000, 3):
        px[x, y] = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
buf = io.BytesIO()
big.save(buf, format="PNG")
raw_big = buf.getvalue()
log("  造图 4000x3000 =", qb.human_size(len(raw_big)))

old_limit = qb.SOFT_LIMIT
qb.SOFT_LIMIT = 200 * 1024
data_a, ext_a, note_a = qb.normalize_image(raw_big)
qb.SOFT_LIMIT = old_limit

log("  缩后 =", qb.human_size(len(data_a)), "| ext =", ext_a, "| note =", note_a)
log("  缩后像素 =", Image.open(io.BytesIO(data_a)).size)
if len(data_a) < len(raw_big) and note_a:
    log("  >>> A 通过：确实缩了，且给出了说明")
else:
    log("  >>> A 失败：没缩，或没给出说明")

# 不动原图时不该有 note（正常路径不能被这一段污染）
data_a2, ext_a2, note_a2 = qb.normalize_image(raw_big)
log("  恢复软限后再跑：note =", repr(note_a2), "| ext =", ext_a2, "（应为空串）")
log("-" * 62)

# ---------------------------------------------------------------- B. 真机发图
log("【B】真机发一张图到手机（走完整新代码）")

img_path = os.path.join(BASE, "extension", "icons", "icon128.png")
raw_b = open(img_path, "rb").read()
durl = "data:image/png;base64," + base64.b64encode(raw_b).decode("ascii")
log("  测试图:", os.path.basename(img_path), qb.human_size(len(raw_b)))

qb.RT["ready"] = True
qb.STATE["proactive_rejected"] = False

try:
    r = asyncio.run(qb.do_send_image({"data_url": durl, "name": "企鹅图标.png"}))
    log("  返回:", json.dumps(r, ensure_ascii=False))
    if r.get("ok"):
        log("  >>> B 通过：msg_id =", r.get("msg_id"), "| 格式 =", r.get("format"))
    else:
        log("  >>> B 失败：", r.get("error"), r.get("message"))
except Exception as e:
    import traceback
    log("  >>> B 抛异常:")
    log(traceback.format_exc())
log("-" * 62)

# ---------------------------------------------------------------- C. 格式兜底
log("【C】非 PNG 格式的探测 / 转 PNG 兜底（纯本地）")

wbuf = io.BytesIO()
Image.new("RGB", (200, 150), (200, 60, 60)).save(wbuf, format="WEBP")
raw_c = wbuf.getvalue()

data_c, ext_c, note_c = qb.normalize_image(raw_c)
log("  webp 探测: ext =", ext_c, "（应为 webp）| note =", repr(note_c))
conv = qb.to_png(raw_c)
log("  to_png:", ("OK " + qb.human_size(len(conv))) if conv else "FAIL")
if conv:
    back = Image.open(io.BytesIO(conv))
    log("  转出来的格式 =", back.format, back.size)

# dataURL 上写着 png、里面其实是 webp —— 必须以字节头为准
fake = "data:image/png;base64," + base64.b64encode(raw_c).decode("ascii")
_mime, _raw = qb.decode_data_url(fake)
_d, _e, _n = qb.normalize_image(_raw)
log("  谎报 mime 时探测到:", _e, "（应为 webp，证明不信 dataURL 上的 mime）")
log("  文件名清洗:", qb.sanitize_name("我的 截图:1*.png", "webp"))
log("  体积显示:", qb.human_size(25123456))
log("-" * 62)

with open(os.path.join(BASE, "tools", "test_image_101_out.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(out))
