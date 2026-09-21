# -*- coding: utf-8 -*-
"""看一眼桥的健康状态（不走系统代理）。"""
import json
import urllib.request

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    with opener.open("http://127.0.0.1:18761/health", timeout=5) as r:
        d = json.loads(r.read().decode("utf-8"))
    lines = [
        "桥状态:",
        "  version      = %s" % d.get("version"),
        "  bot_ready    = %s" % d.get("bot_ready"),
        "  has_openid   = %s" % d.get("has_openid"),
        "  sandbox      = %s" % d.get("sandbox"),
        "  send_ok      = %s" % d.get("send_ok"),
        "  send_fail    = %s" % d.get("send_fail"),
        "  last_error   = %s" % d.get("last_error"),
        "  uptime_sec   = %s" % d.get("uptime_sec"),
    ]
except Exception as e:
    lines = ["读 /health 失败: %r" % (e,)]

with open(r"E:\workbuddywork\一键发到qq\tools\bridge_health_now.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
