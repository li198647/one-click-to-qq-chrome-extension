# -*- coding: utf-8 -*-
"""
命令行测试工具：直接问本地桥要状态 / 发一条消息。
用法（在 bridge 目录下）：
    python send_test.py                    # 只看状态
    python send_test.py "要发的内容"        # 发一条
    python send_test.py --log 30           # 看最近 30 行日志
"""
import json
import sys
import urllib.error
import urllib.request

HOST = "127.0.0.1"


def load_port():
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            return int(json.load(f).get("port") or 18761)
    except Exception:
        return 18761


PORT = load_port()
BASE = "http://%s:%d" % (HOST, PORT)


def show(title, text):
    print("-" * 60)
    print(title)
    print("-" * 60)
    print(text)
    print()


def main():
    args = sys.argv[1:]

    if args and args[0] == "--log":
        n = args[1] if len(args) > 1 else "40"
        try:
            with urllib.request.urlopen(BASE + "/lastlog?n=" + n, timeout=10) as r:
                show("最近日志", r.read().decode("utf-8", "replace"))
        except Exception as e:
            show("读日志失败", "%s: %s" % (type(e).__name__, e))
        return

    # 1) 状态
    try:
        with urllib.request.urlopen(BASE + "/health", timeout=10) as r:
            health = r.read().decode("utf-8")
        try:
            pretty = json.dumps(json.loads(health), ensure_ascii=False, indent=2)
        except Exception:
            pretty = health
        show("桥的状态", pretty)
    except Exception as e:
        show(
            "连不上本地桥",
            "错误: %s: %s\n\n说明 bridge 没在运行，或端口不是 %d。\n请先双击 start_bridge.bat。"
            % (type(e).__name__, e, PORT),
        )
        return

    if not args:
        return

    # 2) 发送
    content = " ".join(args)
    body = json.dumps({"content": content}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        BASE + "/send",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            resp = r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        resp = e.read().decode("utf-8", "replace")
    except Exception as e:
        resp = "%s: %s" % (type(e).__name__, e)

    try:
        resp = json.dumps(json.loads(resp), ensure_ascii=False, indent=2)
    except Exception:
        pass
    show("发送结果", resp)


if __name__ == "__main__":
    main()
