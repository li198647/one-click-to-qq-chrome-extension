# -*- coding: utf-8 -*-
"""
QQ 转发助手 · 本地桥程序  v1.0.5

职责：
  1. 用官方 SDK 连上 QQ 机器人（WebSocket，不需要公网 IP、不需要备案域名）
  2. 在本机开一个只监听 127.0.0.1 的小 HTTP 服务，让浏览器插件把内容丢过来
  3. 收到内容 -> 调官方接口发到你 QQ

端口接口：
  GET  /health    查看桥的状态
  POST /send      {"content": "要发的文字"}
              或  {"image": {"data_url": "data:image/png;base64,...", "name": "..."}}
  GET  /lastlog   看最近日志

v1.0.1 新增图片发送。图片相关的三件事全部放在这一侧做，扩展那边只负责
读图、转 base64、读出宽高：
  · 用 Pillow 探测**真实**格式（剪贴板和某些服务器给的 type 经常不准）
  · 超过 20MB 就缩尺寸（腾讯会把超限的图降级成"文件卡片"，手机上要
    点开下载才看得见，不再是直接显示的大图）
  · 原格式被接口拒了就转 PNG 再试一次
放这边的另一个理由：只有一条实现，两条来源（剪贴板图片 / 右键网页图片）
的行为必然一致。

启动：双击 start_bridge.bat
"""

import asyncio
import base64
import io
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

VERSION = "1.0.5"

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


# ---------------------------------------------------------------- 控制台窗口

# 窗口标题。用 SetConsoleTitleW 而不是在 .bat 里写 `title`：
# 批处理是按 **OEM 代码页** 读盘解析的，往里塞中文就是在赌代码页；
# 这里走的是 UTF-16 的宽字符 API，跟代码页完全无关。
#
# ⚠️ 顺带说明：如果这个控制台是提权开的（本机账号就是内置 Administrator，
#    Windows 会在标题前面自己加「管理员: 」），那串前缀是系统加的，去不掉，
#    也不是我们标题的一部分。
CONSOLE_TITLE = "这是转发QQ的桥文件，保持开启，最小化就好"


def set_console_title(title=CONSOLE_TITLE):
    """把控制台窗口标题改成中文。没有控制台（比如 pythonw）时静默跳过。"""
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW(ctypes.c_wchar_p(title))
        return True
    except Exception:
        return False


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
import aiohttp  # noqa: E402
from aiohttp import web  # noqa: E402

# Pillow 只在发图片时用得上。没装也不该让整个桥起不来 —— 文字那套照旧，
# 只是图片这条路会给出"没装 Pillow"的明确错误。
try:
    from PIL import Image
    HAVE_PIL = True
except Exception:  # pragma: no cover
    Image = None
    HAVE_PIL = False

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


# ---------------------------------------------------------------- 图片
#
# 官方接口要两步：先上传拿到 file_info，再用 msg_type=7 把 file_info 发出去。
#
# ⚠️ 上传走的是自建的 HTTP 请求（aiohttp），**不是** botpy 的
#    post_c2c_file()。原因：那个包装函数的签名里根本没有 file_data ——
#    它只认 url / upload_id / file_name，走的是"腾讯自己去下载这个地址"
#    或"分片上传"两条路。本机没有公网地址，url 那条实测回
#    400「上传URL错误」（说明确实是腾讯的服务器去下这个地址）；
#    分片上传能用，但要 4 次调用。
#    实测 file_data（base64 直传）一次调用就成，所以走它。
#
# ⚠️ 已知风险记在这里：file_data 在官方文档的请求体参数表里**没写**，
#    属于"能跑但没承诺"。哪天真被关掉，备用路径是改成分片上传。

SOFT_LIMIT = 20 * 1024 * 1024      # 超过它，腾讯把图片降级成"文件卡片"
HARD_LIMIT = 190 * 1024 * 1024     # 再大接口直接拒
SHRINK_STEPS = [2560, 1600, 1024]  # 超软限时逐级降长边，先试 2560

# aiohttp 默认只收 1MB 的请求体，而一张 4MB 的截图转成 base64 就是 5.3MB
# —— 不放开的话图片请求会被直接挡成 413，连日志里都看不出原因。
# 抽成模块级常量是为了让测试能引用同一个值，不用抄一遍数字。
MAX_BODY_BYTES = 256 * 1024 * 1024

_TOKEN = {"value": "", "expire_at": 0.0}


async def get_access_token():
    """自己取 access_token 并缓存到过期前 60 秒。

    ⚠️ 为什么不用 botpy 内部那个 _token：那是私有结构（`CLIENT.http._token`
    / `Route` / `check_session`），SDK 一升级就可能改名，桥会莫名其妙地挂。
    这里走的是腾讯**公开文档**的取 token 接口，稳得多。

    ⚠️ 那么"自己再取一次 token，会不会把 botpy 正在用的那个顶掉、害它掉线"？
    查过桥自己的日志（bridge/log/bridge_YYYYMMDD.log）：botpy **本来就在
    大约每小时重连一次，并且每次重连都重新取一次 token**（日志里
    `[botpy] 重连启动...` + `access_token expires_in N` 成对出现，一天几十次）。
    也就是说"新旧 token 并存"是 botpy 一直在经历的状态，不是我们引入的。
    另外 WebSocket 一旦建连就不靠这个 token 做心跳了，token 只用于建连和
    REST 调用 —— 所以即便真有影响，后果也是"掉线后自动重连一次"，而不是
    永久失效。实测：连发多次真图（每次新进程都会取一次 token）之后查
    /health，`bot_ready` 仍为 true、`send_fail` 为 0。
    """
    now = time.time()
    if _TOKEN["value"] and now < _TOKEN["expire_at"] - 60:
        return _TOKEN["value"]

    appid = str(CFG.get("appid") or "").strip()
    secret = str(CFG.get("secret") or "").strip()
    if not (appid and secret):
        raise RuntimeError("config.json 里没填 appid / secret")

    async with aiohttp.ClientSession() as s:
        async with s.post(
            "https://bots.qq.com/app/getAppAccessToken",
            json={"appId": appid, "clientSecret": secret},
            timeout=aiohttp.ClientTimeout(total=20),
        ) as r:
            data = await r.json(content_type=None)

    tok = (data or {}).get("access_token")
    if not tok:
        raise RuntimeError("取 access_token 失败: %s" % (str(data)[:200],))
    _TOKEN["value"] = tok
    _TOKEN["expire_at"] = now + int((data or {}).get("expires_in") or 7200)
    return tok


async def api_post(url, body, timeout=180):
    """带鉴权 POST 到 openapi。返回 (http_status, body)。"""
    tok = await get_access_token()
    appid = str(CFG.get("appid") or "").strip()
    headers = {
        "Authorization": "QQBot " + tok,
        "Content-Type": "application/json",
        "X-Union-Appid": appid,
    }
    async with aiohttp.ClientSession() as s:
        async with s.post(
            url, json=body, headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as r:
            status = r.status
            raw = await r.text()
    try:
        return status, json.loads(raw)
    except Exception:
        return status, {"_raw": raw[:400]}


def human_size(n):
    n = float(n or 0)
    if n < 1024:
        return "%d B" % n
    if n < 1024 * 1024:
        return "%d KB" % round(n / 1024)
    return "%.1f MB" % (n / 1024.0 / 1024.0)


def sanitize_name(name, ext):
    """洗出一个能安全当文件名的名字。扩展名一律以探测到的真实格式为准，
    不信原始文件名 —— 后者经常是假的（比如 .jpg 里面其实是 webp）。"""
    base = re.sub(r'[\\/:*?"<>|\s]+', "_", str(name or "").strip())
    base = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", base)
    base = base[-60:] if len(base) > 60 else base
    if not base:
        base = "image"
    return "%s.%s" % (base, ext)


def decode_data_url(data_url):
    """拆 dataURL -> (mime, bytes)。解不开时 bytes 为 None。"""
    m = re.match(r"^data:([^;,]*)(;base64)?,([\s\S]*)$", str(data_url or ""))
    if not m:
        return "", None
    mime = (m.group(1) or "").strip().lower()
    b64 = re.sub(r"\s+", "", m.group(3) or "")
    try:
        return mime, base64.b64decode(b64)
    except Exception:
        return mime, None


def guess_ext(raw):
    """没有 Pillow 时的兜底：按文件头猜。"""
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if raw[:3] == b"\xff\xd8\xff":
        return "jpg"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    if raw[:2] == b"BM":
        return "bmp"
    return "png"


def normalize_image(raw):
    """探测真实格式；超过软限就缩尺寸。

    返回 (bytes, ext, note)。note 非空表示动过原图，必须告诉木木。

    · 格式以**图片字节头**为准（PIL 探测），不信 dataURL 上那个 mime
    · 超限时只降分辨率、不换格式：截图上的小字用 JPEG 压会糊，
      而缩尺寸只掉分辨率，字还是那个字。原图是 jpg 的才存回 jpg。
    """
    if not HAVE_PIL:
        return raw, guess_ext(raw), ""

    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except Exception:
        return raw, guess_ext(raw), ""

    fmt = (im.format or "").upper()
    ext = {"PNG": "png", "JPEG": "jpg", "GIF": "gif",
           "WEBP": "webp", "BMP": "bmp"}.get(fmt, "png")

    if len(raw) <= SOFT_LIMIT:
        return raw, ext, ""

    w0, h0 = im.size
    cur0 = max(w0, h0)
    best = None

    for long_edge in SHRINK_STEPS:
        if cur0 <= long_edge:
            continue
        ratio = float(long_edge) / float(cur0)
        nw = max(1, int(round(w0 * ratio)))
        nh = max(1, int(round(h0 * ratio)))
        try:
            small = im.resize((nw, nh), Image.LANCZOS)
        except Exception:
            continue
        save_fmt = fmt if fmt in ("PNG", "JPEG", "WEBP", "BMP") else "PNG"
        if save_fmt == "JPEG" and small.mode not in ("RGB", "L"):
            small = small.convert("RGB")
        buf = io.BytesIO()
        try:
            small.save(buf, format=save_fmt)
        except Exception:
            continue
        data = buf.getvalue()
        best = (data, ext, "长边 %d → %d" % (cur0, long_edge))
        if len(data) <= SOFT_LIMIT:
            return best

    return best if best else (raw, ext, "")


def to_png(raw):
    """转成 PNG。给"原格式被接口拒了"兜底用。转不了返回 None。"""
    if not HAVE_PIL:
        return None
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except Exception:
        return None
    try:
        if im.mode in ("P", "LA"):
            im = im.convert("RGBA")
        elif im.mode not in ("RGB", "RGBA", "L"):
            im = im.convert("RGBA")
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


async def do_send_image(image):
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

    _mime, raw = decode_data_url(image.get("data_url"))
    if raw is None:
        return {"ok": False, "error": "bad_image",
                "message": "图片数据解不开，请重新复制一次图片再试。"}
    if not raw:
        return {"ok": False, "error": "empty_image", "message": "图片是空的。"}
    if len(raw) > HARD_LIMIT:
        return {"ok": False, "error": "too_large",
                "message": "图片有 %s，超过接口能接受的上限，发不了。" % human_size(len(raw))}

    original_bytes = len(raw)
    data, ext, shrink_note = normalize_image(raw)
    name_hint = str(image.get("name") or "")

    notes = []
    if shrink_note:
        notes.append(
            "原图 %s 超过 20MB（会被 QQ 降级成「文件」卡片），"
            "已缩尺寸发出（%s）。" % (human_size(original_bytes), shrink_note)
        )

    async def upload(payload_bytes, fext):
        b64 = base64.b64encode(payload_bytes).decode("ascii")
        return await api_post(
            "https://api.sgroup.qq.com/v2/users/%s/files" % openid,
            {
                "file_type": 1,
                "file_data": b64,
                "file_name": sanitize_name(name_hint, fext),
                "srv_send_msg": False,
            },
        )

    try:
        st, res = await upload(data, ext)
    except Exception as e:
        err = "%s: %s" % (type(e).__name__, e)
        _log.error("图片上传异常: %s", err)
        return {"ok": False, "error": "api_exception", "message": err}

    file_info = res.get("file_info") if isinstance(res, dict) else None

    # 官方文档对图片格式的说法自相矛盾：一处写「只支持 png/jpg」，另一处
    # 写「支持 jpg/png/gif/webp/bmp」。所以先按原格式试，被拒就转 PNG 再来
    # 一次。转换只在真失败时发生 —— 动图 GIF 因此不会白掉帧。
    if not file_info and ext != "png":
        old_ext = ext
        conv = to_png(data)
        if conv:
            _log.info("原格式(%s)被拒，转成 PNG 重试一次", old_ext)
            try:
                st2, res2 = await upload(conv, "png")
            except Exception as e:
                st2, res2 = -1, {"message": str(e)}
            if isinstance(res2, dict) and res2.get("file_info"):
                st, res, file_info = st2, res2, res2["file_info"]
                ext, data = "png", conv
                notes.append(
                    "原格式（%s）QQ 不收，已转成 PNG 发出%s。"
                    % (old_ext, "（动图会变成静态图）" if old_ext == "gif" else "")
                )
        else:
            _log.warning("原格式(%s)被拒，但没有 Pillow 转不了 PNG", old_ext)

    if not file_info:
        if isinstance(res, dict):
            code = res.get("code")
            m = res.get("message") or res.get("_raw") or ""
            msg = "图片上传失败（HTTP %s，code=%s）：%s" % (st, code, str(m)[:200])
        else:
            msg = "图片上传失败：%s" % (str(res)[:200],)
        _log.error(msg)
        return {"ok": False, "error": "upload_failed", "message": msg}

    try:
        st3, res3 = await api_post(
            "https://api.sgroup.qq.com/v2/users/%s/messages" % openid,
            {"msg_type": 7, "media": {"file_info": file_info}, "content": ""},
        )
    except Exception as e:
        err = "%s: %s" % (type(e).__name__, e)
        _log.error("图片发送异常: %s", err)
        return {"ok": False, "error": "api_exception", "message": err}

    msg_id = res3.get("id") if isinstance(res3, dict) else None
    if not msg_id:
        return {
            "ok": False,
            "error": "no_msg_id",
            "message": "图片上传成功，但发送没返回消息 id，无法确认是否真的发出去了。"
                       "原始返回：%s" % (str(res3)[:200],),
        }

    out = {
        "ok": True,
        "kind": "image",
        "msg_id": str(msg_id),
        "bytes": len(data),
        "original_bytes": original_bytes,
        "format": ext,
        "shrunk": bool(shrink_note) or len(data) != original_bytes,
    }
    if notes:
        out["warning"] = "image_adjusted"
        out["warning_message"] = "".join(notes)
    _log.info(
        "图片发送成功 msg_id=%s (%s %s%s)",
        msg_id, ext, human_size(len(data)), "，已调整" if notes else "",
    )
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
            "image_support": True,
            "pillow": HAVE_PIL,
        }
    )


async def h_send(request):
    try:
        data = await request.json()
    except Exception:
        return web.json_response(
            {"ok": False, "error": "bad_json", "message": "请求体不是合法 JSON。"}
        )

    image = data.get("image")
    content = (data.get("content") or "").strip()

    t0 = time.time()
    if isinstance(image, dict) and image.get("data_url"):
        r = await do_send_image(image)
        r.setdefault("kind", "image")
    elif content:
        r = await do_send(content)
        r.setdefault("kind", "text")
    else:
        return web.json_response(
            {"ok": False, "error": "empty_content", "message": "内容为空，没东西可发。"}
        )
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


# ---------------------------------------------------------------- 自登记宿主

def self_register_host():
    """把"宿主程序在哪"登记到注册表里，让扩展那个按钮能拉起桥。

    为什么放在桥里做：桥一启动就知道自己的绝对路径，写出来的登记信息
    必然是对的（搬了文件夹也不会失联）；换成让木木去双击一个脚本，
    就多了一次"我忘了放哪"的机会。

    ⚠️ 这里**绝不能**让桥启动失败 —— 登记只是副产品，出任何问题都只
    记一条日志。真的登记不上，双击 bridge\\重新登记.bat 还能补救。"""
    try:
        if BASE not in sys.path:
            sys.path.insert(0, BASE)
        import qq_native_host
        ok, failed = qq_native_host.register_host(logger=lambda m: None)
        if ok:
            _log.info("扩展启动入口已登记 (%d/%d): %s",
                      len(ok), len(ok) + len(failed), "; ".join(ok))
        else:
            _log.warning("扩展启动入口登记失败，将在 --status 里可见")
        for k, m in failed:
            _log.warning("  登记失败 HKCU\\%s -> %s", k, m)
        return bool(ok)
    except Exception:
        _log.warning("自登记出错（不影响桥运行）:\n%s", traceback.format_exc())
        return False


# ---------------------------------------------------------------- 主流程

async def main():
    global CLIENT

    appid = str(CFG.get("appid") or "").strip()
    secret = str(CFG.get("secret") or "").strip()
    sandbox = bool(CFG.get("sandbox", True))
    listen = str(CFG.get("listen") or "127.0.0.1").strip()
    port = int(CFG.get("port") or 18761)

    # 1) 先起本地 HTTP 服务
    #    client_max_size 必须放开，理由见 MAX_BODY_BYTES 那里。
    app = web.Application(middlewares=[cors_mw], client_max_size=MAX_BODY_BYTES)
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
    set_console_title()
    setup_logging()
    _log.info("=" * 64)
    _log.info("QQ 转发助手 · 本地桥 v%s", VERSION)
    _log.info("日志文件: %s", LOG_PATH)
    _log.info("=" * 64)

    # 顺手让扩展那个「启动本地桥」按钮能生效。失败也不拦着桥启动。
    self_register_host()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        _log.info("已手动停止")
    except Exception:
        _log.error("启动失败:\n%s", traceback.format_exc())
        time.sleep(5)
