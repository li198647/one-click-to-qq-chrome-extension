# -*- coding: utf-8 -*-
"""发版前暂存区密钥复查（1.0.4）

规则：**脚本里绝不许出现密钥字面量**。
做法：从 bridge/config.json + bridge/state.json **现读**敏感值，
      再去扫"即将入库的每一个文件"，只报"哪个文件命中了哪个键"，绝不打印值本身。
"""
import json
import os
import subprocess
import sys

GIT = r"C:\Users\Administrator\.workbuddy\binaries\mingit\cmd\git.exe"
ROOT = r"E:\workbuddywork\一键发到qq"

# 这些键的值算敏感（命中即报警）
SECRET_KEYS = {
    "secret", "appsecret", "app_secret", "token", "access_token",
    "password", "passwd", "pwd", "authcode", "auth_code",
    "openid", "user_openid", "client_secret", "apikey", "api_key",
}
# 这些键的值是公开信息，命中也无所谓（但仍会记录，便于人工判断）
BENIGN_KEYS = {"appid", "app_id", "bot_appid", "port", "host", "version"}


def load_pairs():
    """现读凭据。返回 [(key, value, 来源文件)]"""
    pairs = []
    for name in ("config.json", "state.json"):
        p = os.path.join(ROOT, "bridge", name)
        if not os.path.exists(p):
            continue
        try:
            data = json.load(open(p, encoding="utf-8"))
        except Exception as e:
            print("[WARN] 读不动 %s: %s" % (name, e))
            continue
        stack = [("", data)]
        while stack:
            prefix, cur = stack.pop()
            if isinstance(cur, dict):
                for k, v in cur.items():
                    stack.append((k if not prefix else prefix + "." + k, v))
            elif isinstance(cur, list):
                for i, v in enumerate(cur):
                    stack.append(("%s[%d]" % (prefix, i), v))
            else:
                if isinstance(cur, str) and len(cur) >= 8:
                    pairs.append((prefix, cur, name))
    return pairs


def list_files():
    """git 即将入库的文件：已跟踪 + 未跟踪但未被 ignore"""
    out = subprocess.run(
        [GIT, "-C", ROOT, "ls-files", "--cached", "--others", "--exclude-standard"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    files = [l.strip() for l in out.stdout.splitlines() if l.strip()]
    return files


def main():
    pairs = load_pairs()
    files = list_files()
    print("凭据条目：%d 条（来自 config.json / state.json）" % len(pairs))
    print("待入库文件：%d 个" % len(files))
    print("-" * 64)

    # 读入所有文件内容（二进制安全）
    blobs = {}
    for rel in files:
        p = os.path.join(ROOT, rel.replace("/", os.sep))
        if not os.path.isfile(p):
            continue
        try:
            blobs[rel] = open(p, "rb").read()
        except Exception:
            pass

    hard_hits = []   # 敏感键命中
    soft_hits = []   # 公开键命中（仅提示）
    for key, val, src in pairs:
        needle = val.encode("utf-8")
        if len(needle) < 6:
            continue
        leaf = key.split(".")[-1].lower()
        is_secret = leaf in SECRET_KEYS
        for rel, blob in blobs.items():
            # 密钥从不该出现在任何入库文件里；但 config.example.json 是空模板，跳过自身
            if rel.endswith("config.example.json"):
                continue
            if needle in blob:
                (hard_hits if is_secret else soft_hits).append((rel, key, src))

    if soft_hits:
        print("[提示] 以下文件含**公开**配置值（一般正常，确认一下即可）：")
        for rel, key, src in soft_hits:
            print("       %s  <- %s.%s" % (rel, src, key))
        print()

    if hard_hits:
        print("[FAIL] 发现**敏感值**出现在即将入库的文件里：")
        for rel, key, src in hard_hits:
            print("       %s  <- %s.%s" % (rel, src, key))
        print("\n必须先把这些字面量从文件中删掉，再提交。")
        return 1

    print("[OK] 没有任何敏感值出现在即将入库的文件里。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
