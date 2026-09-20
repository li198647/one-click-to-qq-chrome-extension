# -*- coding: utf-8 -*-
"""
链接探针 —— 一次回答「QQ 机器人到底能不能发链接」。

背景：官方文档说机器人下发消息里的链接域名必须提前报备且需 ICP 备案，
      第三方资料更说含 URL 的消息会被"静默过滤"。也就是说接口可能返回成功，
      但消息其实没到你手机上。所以这个探针发 5 种形态，用你的眼睛做最终判定。

用法（先把 start_bridge.bat 跑起来，并且已经在 QQ 里给机器人发过话）：
    python probe_url.py

发完请打开手机 QQ，数一数收到了第几条，告诉 WorkBuddy。
"""
import json
import sys
import time
import urllib.error
import urllib.request

PORT = 18761
BASE = "http://127.0.0.1:%d" % PORT

CASES = [
    ("1/5  纯文本（对照组）", "探针1：这是一条纯文字，不含任何链接。"),
    ("2/5  完整链接", "探针2：https://www.baidu.com"),
    ("3/5  去掉协议的链接", "探针3：www.baidu.com"),
    ("4/5  裸域名", "探针4：baidu.com"),
    ("5/5  文字+链接", "探针5：看看这个 https://item.taobao.com/item.htm?id=1"),
]

GAP_SECONDS = 3


def post_send(content):
    body = json.dumps({"content": content}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        BASE + "/send",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return -1, "%s: %s" % (type(e).__name__, e)


def main():
    print("=" * 64)
    print("链接探针开始，将连续发 %d 条，间隔 %d 秒" % (len(CASES), GAP_SECONDS))
    print("=" * 64)

    results = []
    for label, content in CASES:
        status, resp = post_send(content)
        try:
            parsed = json.loads(resp)
            brief = "ok=%s" % parsed.get("ok")
            if not parsed.get("ok"):
                brief += " error=%s msg=%s" % (parsed.get("error"), parsed.get("message"))
            else:
                brief += " msg_id=%s" % parsed.get("msg_id")
            if parsed.get("warning"):
                brief += " [%s]" % parsed.get("warning")
        except Exception:
            brief = resp[:200]

        line = "%-24s -> HTTP %s | %s" % (label, status, brief)
        print(line)
        sys.stdout.flush()
        results.append(line)
        time.sleep(GAP_SECONDS)

    print()
    print("=" * 64)
    print("发送完毕。现在请打开手机 QQ，数一下收到了哪几条（看开头的「探针N」编号）。")
    print("=" * 64)

    out_path = "probe_url_result.txt"
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("链接探针结果 %s\n\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
            f.write("\n".join(results))
            f.write("\n")
        print("接口侧结果已写入: " + out_path)
    except Exception as e:
        print("写结果文件失败: %s" % e)


if __name__ == "__main__":
    main()
