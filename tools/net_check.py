# -*- coding: utf-8 -*-
"""
连通性自检：QQ 开放平台 API 域名在本机（fake-IP 代理环境）能否直连。
结果写入 net_check_report.txt，不依赖控制台回显。
"""
import socket
import ssl
import sys
import time

OUT = r"E:\workbuddywork\一键发到qq\tools\net_check_report.txt"

HOSTS = [
    ("bots.qq.com", 443),
    ("api.sgroup.qq.com", 443),
    ("sandbox.api.sgroup.qq.com", 443),
]

lines = []
lines.append("=== QQ API 连通性自检 ===")
lines.append("time: %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
lines.append("python: %s" % sys.version.replace("\n", " "))
lines.append("")

for host, port in HOSTS:
    lines.append("--- %s:%s ---" % (host, port))

    # 1) DNS
    ips = None
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        ips = sorted({i[4][0] for i in infos})
        lines.append("  DNS      : OK -> %s" % ", ".join(ips))
        fake = [ip for ip in ips if ip.startswith("198.18.") or ip.startswith("198.19.")]
        if fake:
            lines.append("  !! 命中 fake-IP 网段: %s (Clash 接管)" % ", ".join(fake))
        else:
            lines.append("  DNS 类型  : 真实 IP（未走 fake-IP）")
    except Exception as e:
        lines.append("  DNS      : FAIL %s: %s" % (type(e).__name__, e))
        lines.append("")
        continue

    # 2) TCP 连接
    try:
        t0 = time.time()
        sock = socket.create_connection((host, port), timeout=8)
        dt = (time.time() - t0) * 1000
        lines.append("  TCP 连接  : OK (%.0f ms)" % dt)
    except Exception as e:
        lines.append("  TCP 连接  : FAIL %s: %s" % (type(e).__name__, e))
        lines.append("")
        continue

    # 3) TLS 握手
    try:
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(sock, server_hostname=host) as ssock:
            cert = ssock.getpeercert()
            subj = dict(x[0] for x in cert.get("subject", []))
            lines.append("  TLS 握手  : OK, CN=%s" % subj.get("commonName"))
            lines.append("  证书到期  : %s" % cert.get("notAfter"))
    except Exception as e:
        lines.append("  TLS 握手  : FAIL %s: %s" % (type(e).__name__, e))
        try:
            sock.close()
        except Exception:
            pass

    lines.append("")

# 4) 真实 HTTP 请求验证（拿一个必然 4xx/200 的响应，证明应用层通）
lines.append("--- HTTP 应用层验证 ---")
try:
    import urllib.request
    import json

    req = urllib.request.Request(
        "https://bots.qq.com/app/getAppAccessToken",
        data=json.dumps({"appId": "0", "clientSecret": "0"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read(400).decode("utf-8", "replace")
        lines.append("  HTTP 状态 : %s" % resp.status)
        lines.append("  响应片段  : %s" % body)
    except urllib.error.HTTPError as he:
        body = he.read(400).decode("utf-8", "replace")
        lines.append("  HTTP 状态 : %s (预期内，说明链路通)" % he.code)
        lines.append("  响应片段  : %s" % body)
except Exception as e:
    lines.append("  HTTP 请求 : FAIL %s: %s" % (type(e).__name__, e))

# 5) 环境变量代理检查
lines.append("")
lines.append("--- 代理相关环境变量 ---")
import os

for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
          "http_proxy", "https_proxy", "all_proxy", "no_proxy"):
    v = os.environ.get(k)
    if v:
        lines.append("  %s = %s" % (k, v))
lines.append("  (以上为空则 aiohttp 直连，不走代理)")

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print("done -> " + OUT)
