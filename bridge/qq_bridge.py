# -*- coding: utf-8 -*-
"""
QQ 转发助手 · 本地桥程序  v1.0.0

职责：
  1. 用官方 SDK 连上 QQ 机器人（WebSocket，不需要公网 IP、不需要备案域名）
  2. 在本机开一个只监听 127.0.0.1 的小 HTTP 服务，让浏览器插件把内容丢过来
  3. 收到内容 -> 调官方接口发到你 QQ

端口接口：
  GET  /health    查看桥的状态
  POST /send      {"content": "要发的内容"}  -> 真正发出去
  GET  /lastlog   看最近日志

启动：双击 start_bridge.bat
"""

import asyncio
import json
import os
import re
import sys
import time
import logging
import traceback
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(BASE, "config.json")
STATE_PATH = os.path.join(BASE, "state.json")
LOG_DIR = os.path.join(BASE, "log")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, "bridge_%s.log" % datetime.now().strftime("%Y%m%d"))

VERSION = "1.0.0"

_log = logging.getLogger("bridge")


# ---------------------------------------------------------------- 日志

def setup_logging():
    """接管 root logger，让 botpy 的日志一起落到同一个文件里。"""
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)

    root.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s | %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logging.INFO)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    sh.setLevel(logging.INFO)

    root.addHandler(fh)
    root.addHandler(sh)
    return root


# ---------------------------------------------------------------- 读写 json

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


CFG = load_json(CFG_PATH, {})
STATE = load_json(STATE_PATH, {})

STATE.setdefault("openid", "")
STATE.setdefault("last_inbound", 0)
STATE.setdefault("last_outbound", 0)
STATE.setdefault("proactive_rejected", False)
STATE.setdefault("send_ok", 0)
STATE.setdefault("send_fail", 0)
STATE.setdefault("last_error", "")

RT = {"ready": False, "started_at": time.time()}


# ---------------------------------------------------------------- 内容判断

URL_FULL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)
URL_BARE_RE = re.compile(
    r"\b[\w-]{2,}\.(?:com|cn|net|org|io|me|cc|top|xyz|shop|store|app|dev|link|info|tv|fm|ai|edu|gov)\b",
    re.I,
)


def content_has_url(text):
    """判断内容里是否含链接。QQ 机器人对链接有额外限制，需要提前预警。"""
    return bool(URL_FULL_RE.search(text) or URL_BARE_RE.search(text))


# ---------------------------------------------------------------- 机器人

import botpy  # noqa: E402
from botpy.message import C2CMessage  # noqa: E402
from aiohttp import web  # noqa: E402

CLIENT = None


class BridgeClient(botpy.Client):
    """只管收事件，发送走 /send 接口。"""

    async def on_ready(self):
        RT["ready"] = True
        try:
            name = self.robot.name if self.robot else "?"
        except Exception:
            name = "?"
        _log.info("机器人已上线，WebSocket 就绪 (name=%s)", name)

    async def on_c2c_message_create(self, message: C2CMessage):
        try:
            openid = message.author.user_openid
        except Exception:
            _log.warning("收到消息但读不到 openid: %r", message)
            return

        text = (message.content or "").strip()
        _log.info("收到你的消息 (openid=%s...): %s", openid[:8], text[:80])

        changed = STATE.get("openid") != openid
        STATE["openid"] = openid
        STATE["last_inbound"] = time.time()
        # 能收到你发的消息，说明关系链正常
        STATE["proactive_rejected"] = False
        save_json(STATE_PATH, STATE)

        if changed:
            _log.info(">> 已记录 openid 到 state.json，现在可以主动发消息了")

        try:
            await message.reply(
                content="✅ 桥已连通，openid 已记录。现在在浏览器里按快捷键就能把内容发到这里了。"
            )
        except Exception as e:
            _log.warning("回执发送失败: %s", e)

    async def on_c2c_msg_reject(self, *args, **kwargs):
        """用户在 QQ 客户端关闭了「接收主动消息」——这会直接导致发送失败。"""
        STATE["proactive_rejected"] = True
        save_json(STATE_PATH, STATE)
        _log.warning("!! 你关闭了对本机器人的主动消息接收，主动发送会失败")

    async def on_c2c_msg_receive(self, *args, **kwargs):
        STATE["proactive_rejected"] = False
        save_json(STATE_PATH, STATE)
        _log.info("你已允许接收本机器人的主动消息")

    async def on_friend_add(self, *args, **kwargs):
        _log.info("有人添加了机器人")

    async def on_friend_del(self, *args, **kwargs):
        _log.warning("有人删除了机器人")


# ---------------------------------------------------------------- 发送逻辑

def get_openid():
    return (STATE.get("openid") or "").strip() or str(CFG.get("openid") or "").strip()


async def do_send(content):
    openid = get_openid()
    if not openid:
        return {
            "ok": False,
            "error": "no_openid",
            "message": "还没拿到你的 openid。请先在 QQ 里给机器人发任意一句话，程序会自动记住。",
        }
    if not RT["ready"]:
        return {
            "ok": False,
            "error": "bot_offline",
            "message": "机器人未在线（可能凭证不对或网络不通），无法发送。",
        }
    if STATE.get("proactive_rejected"):
        return {
            "ok": False,
            "error": "proactive_rejected",
            "message": "你在 QQ 客户端关闭了本机器人的主动消息接收。去机器人资料卡里打开即可。",
        }

    has_url = content_has_url(content)

    try:
        res = await CLIENT.api.post_c2c_message(
            openid=openid, msg_type=0, content=content
        )
    except Exception as e:
        err = "%s: %s" % (type(e).__name__, e)
        _log.error("发送异常: %s", err)
        return {"ok": False, "error": "api_exception", "message": err, "has_url": has_url}

    # 解析返回
    msg_id = None
    if isinstance(res, dict):
        msg_id = res.get("id") or res.get("msg_id")
        code = res.get("code")
        if msg_id is None and code not in (0, None):
            _log.error("发送被拒绝: %s", res)
            return {
                "ok": False,
                "error": "api_rejected",
                "code": code,
                "message": str(res.get("message") or res),
                "has_url": has_url,
            }
    else:
        msg_id = getattr(res, "id", None)

    if msg_id is None:
        return {
            "ok": False,
            "error": "no_msg_id",
            "message": "接口返回里没有消息 id，无法确认是否真的发出去了。原始返回：%s" % (str(res)[:200],),
            "has_url": has_url,
        }

    out = {"ok": True, "msg_id": str(msg_id), "has_url": has_url}
    if has_url:
        out["warning"] = "content_has_url"
        out["warning_message"] = (
            "这条内容里有链接。QQ 对机器人发链接有额外限制，如果手机没收到，原因多半在此。"
        )
    _log.info("发送成功 msg_id=%s%s", msg_id, " (含链接，已预警)" if has_url else "")
    return out


# ---------------------------------------------------------------- HTTP 服务

@web.middleware
async def cors_mw(request, handler):
    if request.method == "OPTIONS":
        resp = web.Response(status=204)
    else:
        try:
            resp = await handler(request)
        except web.HTTPException as e:
            resp = e
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return resp


async def h_health(request):
    openid = get_openid()
    return web.json_response(
        {
            "ok": True,
            "service": "qq-bridge",
            "version": VERSION,
            "bot_ready": RT["ready"],
            "has_openid": bool(openid),
            "openid_prefix": (openid[:6] + "...") if openid else "",
            "sandbox": bool(CFG.get("sandbox", True)),
            "proactive_rejected": bool(STATE.get("proactive_rejected")),
            "send_ok": STATE.get("send_ok", 0),
            "send_fail": STATE.get("send_fail", 0),
            "last_error": STATE.get("last_error", ""),
            "uptime_sec": int(time.time() - RT["started_at"]),
        }
    )


async def h_send(request):
    try:
        data = await request.json()
    except Exception:
        return web.json_response(
            {"ok": False, "error": "bad_json", "message": "请求体不是合法 JSON。"}
        )

    content = (data.get("content") or "").strip()
    if not content:
        return web.json_response(
            {"ok": False, "error": "empty_content", "message": "内容为空，没东西可发。"}
        )

    t0 = time.time()
    r = await do_send(content)
    r["elapsed_ms"] = int((time.time() - t0) * 1000)

    if r.get("ok"):
        STATE["send_ok"] = STATE.get("send_ok", 0) + 1
        STATE["last_outbound"] = time.time()
    else:
        STATE["send_fail"] = STATE.get("send_fail", 0) + 1
        STATE["last_error"] = "%s | %s" % (r.get("error"), r.get("message", ""))
    save_json(STATE_PATH, STATE)

    return web.json_response(r)


async def h_lastlog(request):
    try:
        n = int(request.query.get("n", "60"))
    except Exception:
        n = 60
    n = max(1, min(n, 500))
    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-n:]
        text = "".join(lines)
    except Exception as e:
        text = "(读日志失败: %s)" % e
    return web.Response(text=text, content_type="text/plain", charset="utf-8")


# ---------------------------------------------------------------- 主流程

async def main():
    global CLIENT

    appid = str(CFG.get("appid") or "").strip()
    secret = str(CFG.get("secret") or "").strip()
    sandbox = bool(CFG.get("sandbox", True))
    listen = str(CFG.get("listen") or "127.0.0.1").strip()
    port = int(CFG.get("port") or 18761)

    # 1) 先起本地 HTTP 服务
    app = web.Application(middlewares=[cors_mw])
    app.add_routes(
        [
            web.get("/health", h_health),
            web.post("/send", h_send),
            web.get("/lastlog", h_lastlog),
        ]
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, listen, port)
    await site.start()
    _log.info("本地服务已启动: http://%s:%d  (/health /send /lastlog)", listen, port)

    # 2) 没凭证就只跑本地服务
    if not appid or not secret:
        _log.error("=" * 64)
        _log.error("config.json 里还没填 appid / secret，机器人连不上。")
        _log.error("填好后重新启动本程序即可。本地服务保持运行，可访问 /health 自检。")
        _log.error("=" * 64)
        await asyncio.Event().wait()
        return

    # 3) 连机器人
    #    bot_log=None  -> 清掉 botpy 自带 handler，日志冒泡到 root（我们的文件里）
    #    ext_handlers=False -> 不要在 cwd 里生成 botpy.log
    CLIENT = BridgeClient(
        intents=botpy.Intents(public_messages=True),
        is_sandbox=sandbox,
        bot_log=None,
        ext_handlers=False,
        log_level=logging.INFO,
    )

    _log.info("正在连接 QQ 机器人 (appid=%s, sandbox=%s) ...", appid, sandbox)
    try:
        await CLIENT.start(appid=appid, secret=secret)
    except Exception:
        _log.error("机器人连接中断:\n%s", traceback.format_exc())
        await asyncio.Event().wait()


if __name__ == "__main__":
    setup_logging()
    _log.info("=" * 64)
    _log.info("QQ 转发助手 · 本地桥 v%s", VERSION)
    _log.info("日志文件: %s", LOG_PATH)
    _log.info("=" * 64)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        _log.info("已手动停止")
    except Exception:
        _log.error("启动失败:\n%s", traceback.format_exc())
        time.sleep(5)
