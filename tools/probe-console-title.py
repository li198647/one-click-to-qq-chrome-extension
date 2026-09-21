# -*- coding: utf-8 -*-
"""探针：控制台标题真的显示对了吗（中文会不会变乱码）

为什么需要它：verify-extension.js 只能断言**源码里**用的是 SetConsoleTitleW。
但"用了宽字符 API" != "窗口上显示正确" —— 而标题变乱码恰好是那种
**不看窗口就发现不了**的静默错误。

做法：新开一个控制台，在里面 import **真实的** qq_bridge 并调用**真实的**
      set_console_title()，然后从外面把窗口标题读回来逐字符核对。
      （标题字符串不另抄一份 —— 抄一遍只能证明"抄对了"。）

用法：python tools/probe-console-title.py
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
BRIDGE = os.path.join(ROOT, "bridge")

# 解释器：优先用桥自己那套 venv，找不到就退回当前解释器
PY = r"C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not os.path.exists(PY):
    PY = sys.executable

# 让子进程 import 真实模块、调真实函数，然后挂住几秒等我们把窗口标题读回来
CHILD = (
    "import sys; sys.path.insert(0, r'%s');"
    "import qq_bridge;"
    "print('SET_OK', qq_bridge.set_console_title(), flush=True);"
    "import time; time.sleep(8)" % BRIDGE
)

u32 = ctypes.windll.user32
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
u32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
u32.GetWindowTextLengthW.argtypes = [wt.HWND]
u32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]


def all_windows():
    found = []

    def cb(hwnd, _):
        n = u32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(hwnd, buf, n + 1)
            found.append((hwnd, buf.value))
        return True

    u32.EnumWindows(WNDENUMPROC(cb), 0)
    return found


def main():
    # 先把"标准答案"从真实源码里取出来
    sys.path.insert(0, BRIDGE)
    import qq_bridge

    want = qq_bridge.CONSOLE_TITLE
    print("真实源码里的标题（%d 字符）：" % len(want))
    print("   %r" % want)
    print("   码点: " + " ".join("U+%04X" % ord(c) for c in want))
    print("-" * 64)

    before = {h for h, _ in all_windows()}
    proc = subprocess.Popen([PY, "-c", CHILD], cwd=BRIDGE,
                            creationflags=subprocess.CREATE_NEW_CONSOLE)
    print("已开子控制台 pid = %d（它调用的是真实函数）" % proc.pid)

    hit = None
    for _ in range(40):
        time.sleep(0.25)
        for hwnd, title in all_windows():
            if hwnd in before:
                continue
            if want in title:
                hit = (hwnd, title)
                break
        if hit:
            break

    if not hit:
        proc.kill()
        print("[FAIL] 没找到标题含目标字符串的新窗口")
        return 1

    hwnd, title = hit
    print("从窗口读回来的标题：")
    print("   %r" % title)

    idx = title.index(want)
    got = title[idx:idx + len(want)]
    prefix = title[:idx]
    suffix = title[idx + len(want):]

    print("-" * 64)
    if prefix or suffix:
        print("窗口标题比源码多了内容：前缀 %r  后缀 %r" % (prefix, suffix))
        print("   ↑ 多为系统加的（提权控制台会加「管理员: 」），不是我们的 bug")
    same = (got == want)
    print("逐字符一致:", same)
    if not same:
        print("   期望:", " ".join("U+%04X" % ord(c) for c in want))
        print("   实际:", " ".join("U+%04X" % ord(c) for c in got))

    proc.kill()
    print("-" * 64)
    print("[PASS] 标题在真实窗口上显示正确、无乱码" if same else "[FAIL] 乱码或对不上")
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main())
