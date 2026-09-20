# -*- coding: utf-8 -*-
"""
QQ UIA 探针 v4 —— 决定性一测
思路：Chromium/Electron 的无障碍树默认不开启，它会检测 Windows 的"读屏软件正在运行"系统标志
      (SPI_GETSCREENREADER)。把该标志置为 TRUE，再读一次 UIA 树。
      若树里出现文字/列表项 => UI 自动化路线成立。
      这是可逆的用户级设置，不写系统文件。

输出：同目录 uia_report4.txt
"""
import ctypes
import ctypes.wintypes as wt
import os
import time

import uiautomation as auto

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

SPI_GETSCREENREADER = 0x0046
SPI_SETSCREENREADER = 0x0047
SPIF_UPDATEINIFILE = 0x01
SPIF_SENDCHANGE = 0x02

lines = []


def add(s=""):
    lines.append(str(s))


def exe_of(pid):
    h = kernel32.OpenProcess(0x1000, False, pid)
    name = ""
    if h:
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wt.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                name = buf.value
        finally:
            kernel32.CloseHandle(h)
    return name


def dump(ctrl, out, depth=0, maxdepth=9, cap=None):
    if cap is None:
        cap = {"n": 0, "text": 0}
    if depth > maxdepth or cap["n"] >= 1200:
        return
    try:
        kids = ctrl.GetChildren()
    except Exception:
        return
    for k in kids:
        if cap["n"] >= 1200:
            return
        cap["n"] += 1
        try:
            nm = (k.Name or "")[:60]
            if nm:
                cap["text"] += 1
            r = k.BoundingRectangle
            out.append("%s%-15s name=%r cls=%r rect=(%d,%d)" % (
                "  " * (depth + 1), k.ControlTypeName, nm,
                (k.ClassName or "")[:26], int(r.left), int(r.top)))
        except Exception as e:
            out.append("%s<ERR %s>" % ("  " * (depth + 1), type(e).__name__))
        dump(k, out, depth + 1, maxdepth, cap)
    return cap


def all_texts(ctrl, out, depth=0, maxdepth=9, cap=None):
    if cap is None:
        cap = {"n": 0}
    if depth > maxdepth or cap["n"] >= 1500:
        return
    try:
        kids = ctrl.GetChildren()
    except Exception:
        return
    for k in kids:
        if cap["n"] >= 1500:
            return
        cap["n"] += 1
        try:
            nm = (k.Name or "").strip()
            if nm:
                out.append("%-15s %r" % (k.ControlTypeName, nm[:70]))
        except Exception:
            pass
        all_texts(k, out, depth + 1, maxdepth, cap)


def main():
    add("=" * 78)
    add("QQ UIA 探针 v4 (读屏标志 -> 强制开启 Electron 无障碍树)")
    add("time : %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    add("=" * 78)
    add("")

    pv = wt.BOOL()
    ok = user32.SystemParametersInfoW(SPI_GETSCREENREADER, 0, ctypes.byref(pv), 0)
    add("### 1. 当前 SPI_GETSCREENREADER = %s (调用返回 %s)" % (bool(pv.value), ok))

    newv = wt.BOOL(True)
    r = user32.SystemParametersInfoW(
        SPI_SETSCREENREADER, 1, ctypes.byref(newv),
        SPIF_UPDATEINIFILE | SPIF_SENDCHANGE)
    add("### 2. 置为 TRUE  ->  返回 %s,  GetLastError=%s" % (
        r, ctypes.get_last_error()))
    pv2 = wt.BOOL()
    user32.SystemParametersInfoW(SPI_GETSCREENREADER, 0, ctypes.byref(pv2), 0)
    add("    复查 SPI_GETSCREENREADER = %s" % bool(pv2.value))
    add("")

    add("### 3. 等待 3 秒让 QQ 的渲染进程切换无障碍模式")
    time.sleep(3.0)
    add("")

    root = auto.GetRootControl()
    top = root.GetChildren()
    qq = []
    for w in top:
        try:
            if exe_of(w.ProcessId).lower().endswith("qq.exe"):
                qq.append(w)
        except Exception:
            continue
    add("### 4. UIA 可见 QQ 窗口数: %d" % len(qq))
    add("")

    for i, w in enumerate(qq[:3], 1):
        add("  ===== QQ 窗口 %d: name=%r =====" % (i, (w.Name or "")[:40]))
        tree = []
        cap = dump(w, tree, 0, 9)
        add("  节点数=%d  其中有文字=%d" % (len(tree), cap.get("text", 0)))
        for x in tree[:100]:
            add(x)
        if len(tree) > 100:
            add("  ... 省略 %d 行" % (len(tree) - 100))
        add("")
        txt = []
        all_texts(w, txt)
        add("  --- 该窗口全部可读文本 (%d 条) ---" % len(txt))
        seen = set()
        shown = 0
        for t in txt:
            if t in seen:
                continue
            seen.add(t)
            add("    " + t)
            shown += 1
            if shown >= 80:
                add("    ... 更多省略")
                break
        add("")

    add("=" * 78)
    add("报告结束")

    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uia_report4.txt")
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("OK -> %s" % p)


if __name__ == "__main__":
    main()
