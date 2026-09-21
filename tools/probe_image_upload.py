# -*- coding: utf-8 -*-
"""
探针：确认 QQ 机器人单聊(C2C)能不能用「本地图片」发出富媒体消息。

要回答的唯一问题：
  /v2/users/{openid}/files 到底接不接受 file_data(base64)？
官方文档的请求体只列了 url / upload_id(分片合并)，没有 file_data；
但社区 SDK 里有人传 file_data 并且说能用。这条假设决定整个功能的架构，
所以必须实测，不能靠记忆。

本脚本只做「上传」，srv_send_msg=false —— 不会往你 QQ 发任何消息，
也不占用主动消息频次。
"""

import base64
import json
import os
import urllib.error
import urllib.request

BASE = r"E:\workbuddywork\一键发到qq\bridge"
ICON = r"E:\workbuddywork\一键发到qq\extension\icons\icon48.png"

out = []


def log(*a):
    line = " ".join(str(x) for x in a)
    out.append(line)
    print(line)


def load(p, d=None):
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return d if d is not None else {}


cfg = load(os.path.join(BASE, "config.json"))
state = load(os.path.join(BASE, "state.json"))
appid = str(cfg.get("appid") or "").strip()
secret = str(cfg.get("secret") or "").strip()
openid = (str(state.get("openid") or "").strip() or str(cfg.get("openid") or "").strip())
sandbox = bool(cfg.get("sandbox", True))

log("appid_set =", bool(appid))
log("secret_set =", bool(secret))
log("openid_prefix =", openid[:6] if openid else "(none)")
log("sandbox =", sandbox)
log("-" * 60)

if not (appid and secret and openid):
    log("缺 appid / secret / openid，无法继续。")
    raise SystemExit(0)

# 1) 取 access_token
try:
    req = urllib.request.Request(
        "https://bots.qq.com/app/getAppAccessToken",
        data=json.dumps({"appId": appid, "clientSecret": secret}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        tok = json.loads(r.read().decode("utf-8"))
except Exception as e:
    log("取 token 失败:", repr(e))
    raise SystemExit(0)

at = tok.get("access_token")
log("token_ok =", bool(at), " expires_in =", tok.get("expires_in"))
if not at:
    log("token 返回:", json.dumps(tok, ensure_ascii=False)[:300])
    raise SystemExit(0)

HD = {
    "Authorization": "QQBot " + at,
    "Content-Type": "application/json",
    "X-Union-Appid": appid,
}


def post(url, body, timeout=40):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers=HD, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw[:400]
    except Exception as e:
        return -1, repr(e)


raw = open(ICON, "rb").read()
b64 = base64.b64encode(raw).decode("ascii")
log("测试图片:", os.path.basename(ICON), len(raw), "bytes -> base64", len(b64), "chars")
log("-" * 60)

# 2) 试验 A：file_data(base64) 直传 —— 官方文档未列，社区说可用
log("【A】file_data 直传")
st, res = post(
    "https://api.sgroup.qq.com/v2/users/%s/files" % openid,
    {
        "file_type": 1,
        "file_data": b64,
        "file_name": "probe-icon.png",
        "srv_send_msg": False,
    },
)
log("  http =", st)
log("  body =", json.dumps(res, ensure_ascii=False)[:400] if isinstance(res, dict) else str(res)[:400])
log("-" * 60)

# 3) 试验 B：upload_prepare 分片预上传是否存在、返回什么
log("【B】upload_prepare（分片上传第一步）")
st2, res2 = post(
    "https://api.sgroup.qq.com/v2/users/%s/upload_prepare" % openid,
    {
        "file_type": 1,
        "file_name": "probe-icon.png",
        "file_size": len(raw),
        "md5_10m": __import__("hashlib").md5(raw[:10002432]).hexdigest(),
    },
)
log("  http =", st2)
log("  body =", json.dumps(res2, ensure_ascii=False)[:600] if isinstance(res2, dict) else str(res2)[:600])
log("-" * 60)

# 4) 试验 C：url 传一个本机地址 —— 验证平台是否真由服务端去下载
log("【C】url 传 127.0.0.1（预期失败，用来证明平台是服务端拉取）")
st3, res3 = post(
    "https://api.sgroup.qq.com/v2/users/%s/files" % openid,
    {"file_type": 1, "url": "http://127.0.0.1:18761/none.png", "srv_send_msg": False},
)
log("  http =", st3)
log("  body =", json.dumps(res3, ensure_ascii=False)[:300] if isinstance(res3, dict) else str(res3)[:300])

with open(r"E:\workbuddywork\一键发到qq\tools\probe_image_upload_out.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(out))
