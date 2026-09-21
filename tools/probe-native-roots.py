# -*- coding: utf-8 -*-
"""探针：这台浏览器**到底**在注册表哪几个位置找 Native Messaging 宿主？

为什么需要它：Chrome 系浏览器各自看自己那一处，写错位置的后果极隐蔽 ——
浏览器只会回一句英文错误（"Native messaging host xxx is not registered"），
扩展那边什么都不报，用户看到的就是"点了按钮没反应"。

而"到底看哪几处"**不能靠文档猜**，因为各分支会把产品名换掉。可靠做法是
直接在浏览器主程序里搜路径字面量：这些注册表根是硬编码的宽字符串
（UTF-16LE），在 chrome.dll 里搜 "NativeMessagingHosts" 就能连根一起捞出来。

    实测（2026-09-21，Thorium 138.0.7204.303）：
        SOFTWARE\\Thorium\\NativeMessagingHosts
        SOFTWARE\\Google\\Chrome\\NativeMessagingHosts
    → **没有** Software\\Chromium 这一条。当初只写 Chromium + Google\\Chrome
      还能跑通，纯属运气（靠 Google\\Chrome 兜底）。本脚本就是防止这种
      "靠运气"再发生一次。

跑法：
    python tools/probe-native-roots.py            # 默认查 Thorium
    python tools/probe-native-roots.py <chrome.dll 的路径>
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HOST_PY = os.path.join(ROOT, "bridge", "qq_native_host.py")

# 没给参数时查这个（木木在用的浏览器）。取目录下最大的那个 .dll ——
# chrome.dll 有 260MB，一眼就能认出来，也不怕哪天改了文件名。
DEFAULT_DIR = r"D:\钍浏览器\BIN\138.0.7204.303"


def find_main_dll(d):
    if os.path.isfile(d):
        return d
    cands = []
    for n in os.listdir(d):
        p = os.path.join(d, n)
        if n.lower().endswith(".dll"):
            cands.append((os.path.getsize(p), p))
    if not cands:
        return None
    return max(cands)[1]


def scan_roots(dll):
    """把 chrome.dll 里所有 *NativeMessagingHosts 的注册表根捞出来。"""
    data = open(dll, "rb").read()
    needle = "NativeMessagingHosts".encode("utf-16-le")
    found = set()
    for m in re.finditer(re.escape(needle), data):
        i = m.start()
        seg = data[max(0, i - 200):i + 40]
        try:
            s = seg.decode("utf-16-le", "replace")
        except Exception:
            continue
        for piece in re.findall(r"[ -~]{6,80}", s):
            if "NativeMessagingHosts" in piece:
                # 归一化成 "Software\X\NativeMessagingHosts"
                p = piece.strip("\\")
                k = p.upper().find("SOFTWARE")
                if k > 0:
                    p = p[k:]
                found.add(p)

    # ⚠️ 去截断：回溯窗口可能正好切在字符串中间，于是摘出 "TWARE\Thorium\…"
    #    这种半截货。判据很简单 —— 短的那条如果正好是长那条的**尾巴**，
    #    它一定是截断产物，丢掉。
    pruned = set(p for p in found
                 if not any(q != p and q.endswith(p) for q in found))
    return sorted(pruned), len(data)


def registered_roots():
    """从 qq_native_host.py 里读回我们实际登记了哪几处（不 import，避免
    在非 Windows 上被 winreg 挡住）。"""
    src = open(HOST_PY, "r", encoding="utf-8").read()
    seg = src[src.find("REG_SUBKEYS = ["):]
    seg = seg[:seg.find("]")]
    out = []
    for m in re.finditer(r'"([^"]+)"', seg):
        v = m.group(1).replace("\\\\", "\\")
        # 去掉末尾的 "\<HOST_NAME>"
        v = v.rsplit("\\", 1)[0]
        out.append(v)
    return out


def norm(s):
    return s.replace("\\", "/").lower()


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DIR
    dll = find_main_dll(target)
    if not dll or not os.path.isfile(dll):
        print("找不到主程序 dll：%s" % target)
        return 2

    print("目标: %s" % dll)
    roots, size = scan_roots(dll)
    print("大小: %.0f MB" % (size / 1048576.0))
    print("\n这台浏览器实际会在这些位置找宿主（从主程序里搜出来的）：")
    for r in roots:
        print("  HKCU\\" + r)

    mine = registered_roots()
    print("\n我们实际登记了这些位置：")
    for r in mine:
        print("  HKCU\\" + r)

    sroots = set(norm(x) for x in roots)
    smine = set(norm(x) for x in mine)

    missing = [r for r in roots if norm(r) not in smine]
    extra = [r for r in mine if norm(r) not in sroots]
    hit = [r for r in roots if norm(r) in smine]

    print("")
    bad = False
    if hit:
        print("[OK]   %d 处对上了：" % len(hit))
        for r in hit:
            print("         " + r)
    else:
        print("[FAIL] 一处都没对上 —— 那个按钮点了必然没反应。")
        bad = True
    if missing:
        print("[FAIL] 浏览器会看、但我们**没登记**的位置（补进 REG_SUBKEYS）：")
        for r in missing:
            print("         " + r)
        bad = True
    if extra:
        print("[note] 我们登记了、但这台浏览器不看的位置（换个浏览器就用得上，无害）：")
        for r in extra:
            print("         " + r)

    print("\n" + ("结论：登记位置覆盖了这台浏览器的全部查找点。" if not bad
                  else "结论：有缺口，必须补。"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
