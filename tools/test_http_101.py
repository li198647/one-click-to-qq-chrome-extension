# -*- coding: utf-8 -*-
"""
v1.0.1 HTTP 层验证：起一个**临时**桥实例（另换端口），用扩展真正会发的那种
请求把它跑一遍。

为什么不在 18761 上测：木木的主桥正跑着 v1.0.0，不认识 image 字段。
这个临时实例**只起 HTTP 服务、不连 WebSocket**，所以不会和主桥抢 QQ 的
长连接，测完即销毁。

三件事：
  ① 4MB 级的请求体能不能穿过 HTTP 层（验证 client_max_size 真的放开了）
     —— 用临时压小的 HARD_LIMIT 在解码后立刻拦住，所以**不会真发出去**
  ② 小图走完整链路，真的发到手机一次
  ③ 老路径（文字）的错误分支没被改坏
"""

import asyncio
import base64
import importlib.util
import io
import json
import os
import random
import time

from aiohttp import ClientSession, web

BASE = r"E:\workbuddywork\一键发到qq"
TEST_PORT = 18762

out = []


def log(*a):
    line = " ".join(str(x) for x in a)
    out.append(line)
    print(line)


spec = importlib.util.spec_from_file_location(
    "qb", os.path.join(BASE, "bridge", "qq_bridge.py")
)
qb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qb)

from PIL import Image  # noqa: E402

SEND = "http://127.0.0.1:%d/send" % TEST_PORT
HEALTH = "http://127.0.0.1:%d/health" % TEST_PORT


def data_url(raw, mime="image/png"):
    return "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode("ascii"))


async def run():
    # 不连 WebSocket，只把 ready 手动立起来 —— 否则 do_send_image 会
    # 在第一步就返回 bot_offline，后面的通路根本走不到。
    qb.RT["ready"] = True
    qb.STATE["proactive_rejected"] = False

    app = web.Application(middlewares=[qb.cors_mw], client_max_size=qb.MAX_BODY_BYTES)
    app.add_routes([
        web.get("/health", qb.h_health),
        web.post("/send", qb.h_send),
    ])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", TEST_PORT)
    await site.start()
    log("临时实例已起在 127.0.0.1:%d（未连 WebSocket）" % TEST_PORT)
    log("client_max_size = %s" % qb.human_size(qb.MAX_BODY_BYTES))
    log("-" * 62)

    try:
        async with ClientSession() as s:
            # ---- 健康检查
            async with s.get(HEALTH) as r:
                d = await r.json()
                log("【health】HTTP %s | bot_ready=%s | image_support=%s | pillow=%s"
                    % (r.status, d.get("bot_ready"), d.get("image_support"), d.get("pillow")))

            # ---- ① 大 body 能不能穿过 HTTP 层
            log("")
            log("【①】4MB 级请求体（临时压小 HARD_LIMIT，让它解码后立刻被拒 —— 不会真发出去）")
            big = Image.new("RGB", (2400, 1800), (20, 20, 30))
            px = big.load()
            random.seed(3)
            for y in range(0, 1800, 3):
                for x in range(0, 2400, 3):
                    px[x, y] = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
            b1 = io.BytesIO()
            big.save(b1, format="PNG")
            raw_big = b1.getvalue()
            log("  造图 2400x1800 = %s" % qb.human_size(len(raw_big)))

            old_hard = qb.HARD_LIMIT
            qb.HARD_LIMIT = 1 * 1024 * 1024
            t0 = time.time()
            async with s.post(SEND, json={"image": {"data_url": data_url(raw_big), "name": "big.png"}}) as r:
                st1 = r.status
                d1 = await r.json()
            qb.HARD_LIMIT = old_hard
            log("  HTTP %s | error=%s | %s | 耗时 %.1fs"
                % (st1, d1.get("error"), str(d1.get("message"))[:90], time.time() - t0))
            if st1 == 200 and d1.get("error") == "too_large":
                log("  >>> ① 通过：%s 的请求体完整穿过了 HTTP 层（client_max_size 生效），且没有真发出去"
                    % qb.human_size(len(raw_big)))
            else:
                log("  >>> ① 失败：请求没能完整到达处理函数")

            # ---- ② 小图真发一次
            log("")
            log("【②】小图走完整链路（会真的发到手机）")
            raw_small = open(os.path.join(BASE, "extension", "icons", "icon128.png"), "rb").read()
            async with s.post(SEND, json={"image": {"data_url": data_url(raw_small), "name": "企鹅-http.png"}}) as r:
                st2 = r.status
                d2 = await r.json()
            log("  HTTP %s | %s" % (st2, json.dumps(d2, ensure_ascii=False)[:320]))
            log("  >>> ② %s" % ("通过" if d2.get("ok") else "失败"))

            # ---- ③ 老路径没被改坏：空 content 应当被拒
            log("")
            log("【③】老路径的错误分支（不该被新增的图片分支挤掉）")
            async with s.post(SEND, json={}) as r:
                st3 = r.status
                d3 = await r.json()
            log("  空请求体: HTTP %s | error=%s | %s" % (st3, d3.get("error"), d3.get("message")))

            async with s.post(SEND, json={"content": "   "}) as r:
                st4 = r.status
                d4 = await r.json()
            log("  空白 content: HTTP %s | error=%s" % (st4, d4.get("error")))

            ok3 = (st3 == 200 and d3.get("error") == "empty_content"
                   and st4 == 200 and d4.get("error") == "empty_content")
            log("  >>> ③ %s" % ("通过：文字那条路仍按原样拒绝空内容" if ok3 else "失败"))
    finally:
        await runner.cleanup()
        log("")
        log("临时实例已销毁")


if __name__ == "__main__":
    asyncio.run(run())
    with open(os.path.join(BASE, "tools", "test_http_101_out.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(out))
