# -*- coding: utf-8 -*-
"""
真机验证：把一张本地图片通过 QQ 机器人单聊发出去。

两步（官方要求的两步）：
  1. POST /v2/users/{openid}/files   file_type=1 + file_data(base64)  → 拿 file_info
  2. POST /v2/users/{openid}/messages msg_type=7 + media.file_info    → 真正发到你手机

只用标准库，不依赖 botpy 的 token（botpy 的 post_c2c_file 包装函数不接受 file_data，
所以桥里以后也要走同样这条通用请求路径）。
"""

import base64
import json
import os
import socket
import urllib.error
import urllib.request

BASE = r"E:\workbuddywork\一键发到qq\bridge"
IMG = r"E:\workbuddywork\一键发到qq\extension\icons\icon128.png"

out = []


def log(*a):
    line = " ".join(str(x) for x in a)
    out.append(line)
    print(line)


def load(p):
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# 0) 桥在不在跑（只报状态，不依赖它）
try:
    s = socket.create_connection(("127.0.0.1", 18761), timeout=2)
    s.close()
    log("本地桥: 在跑（18761 端口可连）")
except Exception as e:
    log("本地桥: 没在跑（%s）—— 不影响本次测试" % e)
log("-" * 60)

cfg = load(os.path.join(BASE, "config.json"))
state = load(os.path.join(BASE, "state.json"))
appid = str(cfg.get("appid") or "").strip()
secret = str(cfg.get("secret") or "").strip()
openid = str(state.get("openid") or "").strip() or str(cfg.get("openid") or "").strip()

if not (appid and secret and openid):
    log("缺 appid / secret / openid，无法测试")
    raise SystemExit(0)

# 1) token
req = urllib.request.Request(
    "https://bots.qq.com/app/getAppAccessToken",
    data=json.dumps({"appId": appid, "clientSecret": secret}).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=20) as r:
    tok = json.loads(r.read().decode("utf-8"))
at = tok.get("access_token")
log("token 获取:", "OK" if at else "FAILED", "expires_in =", tok.get("expires_in"))
if not at:
    raise SystemExit(0)

HD = {
    "Authorization": "QQBot " + at,
    "Content-Type": "application/json",
    "X-Union-Appid": appid,
}


def post(url, body, timeout=60):
    r = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers=HD, method="POST"
    )
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw[:400]
    except Exception as e:
        return -1, repr(e)


raw = open(IMG, "rb").read()
b64 = base64.b64encode(raw).decode("ascii")
log("测试图:", os.path.basename(IMG), len(raw), "bytes ->", len(b64), "chars base64")
log("-" * 60)

# 2) 上传
log("【第 1 步】上传图片")
st, res = post(
    "https://api.sgroup.qq.com/v2/users/%s/files" % openid,
    {"file_type": 1, "file_data": b64, "file_name": "test-icon.png", "srv_send_msg": False},
)
log("  http =", st)
if isinstance(res, dict):
    log("  file_uuid =", str(res.get("file_uuid"))[:40])
    log("  ttl =", res.get("ttl"))
    log("  code =", res.get("code"), res.get("message"))
file_info = res.get("file_info") if isinstance(res, dict) else None
if not file_info:
    log("  !! 没拿到 file_info，中止。原始返回:", json.dumps(res, ensure_ascii=False)[:300])
    with open(r"E:\workbuddywork\一键发到qq\tools\test_send_image_out.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    raise SystemExit(0)
log("-" * 60)

# 3) 发送
log("【第 2 步】发消息出去 (msg_type=7)")
st2, res2 = post(
    "https://api.sgroup.qq.com/v2/users/%s/messages" % openid,
    {"msg_type": 7, "media": {"file_info": file_info}, "content": ""},
)
log("  http =", st2)
log("  body =", json.dumps(res2, ensure_ascii=False)[:400] if isinstance(res2, dict) else str(res2)[:400])
if isinstance(res2, dict) and res2.get("id"):
    log("  >>> 发送成功，msg_id =", res2.get("id"))
    log("  >>> 请到手机 QQ「我的速记本」会话里看看有没有这张图。")
else:
    log("  >>> 发送未成功，看上面的 code / message。")

with open(r"E:\workbuddywork\一键发到qq\tools\test_send_image_out.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(out))
