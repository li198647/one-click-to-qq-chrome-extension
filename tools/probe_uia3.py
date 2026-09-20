# -*- coding: utf-8 -*-
"""
QQ UI Automation 探针 v3
思路：QQ 的窗口全是隐藏的（UIA 不暴露隐藏窗口），所以先把窗口显示出来，再看 UIA 树。
副作用：会在桌面上把 QQ 窗口显示出来（不会抢焦点）。可接受。

输出：同目录 uia_report3.txt (UTF-8)
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

import uiautomation as auto

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsIconic.argtypes = [wt.HWND]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]

SW_HIDE, SW_SHOW, SW_SHOWNA, SW_RESTORE = 0, 5, 8, 9

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


def wtext(h):
    n = user32.GetWindowTextLengthW(h)
    b = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(h, b, n + 1)
    return b.value


def wclass(h):
    b = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(h, b, 256)
    return b.value


def wrect(h):
    r = wt.RECT()
    user32.GetWindowRect(h, ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def qq_top_windows():
    out = []

    def cb(h, lp):
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if exe_of(pid.value).lower().endswith("qq.exe"):
            out.append({
                "hwnd": h, "pid": pid.value, "cls": wclass(h),
                "title": wtext(h), "vis": bool(user32.IsWindowVisible(h)),
                "iconic": bool(user32.IsIconic(h)), "rect": wrect(h),
            })
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def dump(ctrl, out, depth=0, maxdepth=6, cap=None):
    if cap is None:
        cap = {"n": 0}
    if depth > maxdepth or cap["n"] >= 600:
        return
    try:
        kids = ctrl.GetChildren()
    except Exception:
        return
    for k in kids:
        if cap["n"] >= 600:
            return
        cap["n"] += 1
        try:
            r = k.BoundingRectangle
            out.append("%s%-16s name=%r cls=%r off=%s rect=(%d,%d,%d,%d)" % (
                "  " * (depth + 1), k.ControlTypeName, (k.Name or "")[:60],
                (k.ClassName or "")[:30], getattr(k, "IsOffscreen", "?"),
                int(r.left), int(r.top), int(r.right), int(r.bottom)))
        except Exception as e:
            out.append("%s<ERR %s>" % ("  " * (depth + 1), type(e).__name__))
        dump(k, out, depth + 1, maxdepth, cap)


def kw_hits(ctrl, hits, depth=0, maxdepth=6, cap=None):
    if cap is None:
        cap = {"n": 0}
    if depth > maxdepth or cap["n"] >= 900:
        return
    try:
        kids = ctrl.GetChildren()
    except Exception:
        return
    for k in kids:
        if cap["n"] >= 900:
            return
        cap["n"] += 1
        try:
            nm = k.Name or ""
            if nm and len(nm) < 60:
                hits.append("%-16s %r" % (k.ControlTypeName, nm))
        except Exception:
            pass
        kw_hits(k, hits, depth + 1, maxdepth, cap)


def main():
    add("=" * 78)
    add("QQ UIA 探针 v3  (先显示窗口, 再读 UIA 树)")
    add("time : %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    add("=" * 78)
    add("")

    wins = qq_top_windows()
    add("### 1. Win32 看到的 QQ 顶层窗口: %d 个" % len(wins))
    for w in wins:
        add("  hwnd=%-8s vis=%-5s iconic=%-5s cls=%-22s rect=%s" % (
            w["hwnd"], w["vis"], w["iconic"], w["cls"], w["rect"]))
    add("")

    cands = [w for w in wins if w["cls"] == "Chrome_WidgetWin_1" and w["rect"][2] - w["rect"][0] > 300]
    add("### 2. 候选主窗口 (Chrome_WidgetWin_1 且宽度>300): %d 个" % len(cands))
    for w in cands:
        add("  hwnd=%-8s rect=%s vis=%s" % (w["hwnd"], w["rect"], w["vis"]))
    add("")

    add("### 3. 尝试显示窗口 (SW_SHOWNA, 不抢焦点)")
    for w in cands:
        try:
            r = user32.ShowWindow(w["hwnd"], SW_SHOWNA)
            add("  hwnd=%s ShowWindow 返回=%s" % (w["hwnd"], r))
        except Exception as e:
            add("  hwnd=%s 失败 %r" % (w["hwnd"], e))
    time.sleep(2.0)
    for w in cands:
        add("  显示后 hwnd=%s vis=%s" % (w["hwnd"], bool(user32.IsWindowVisible(w["hwnd"]))))
    add("")

    add("### 4. UIA 顶层窗口全清单")
    root = auto.GetRootControl()
    try:
        top = root.GetChildren()
    except Exception as e:
        add("  GetChildren 失败 %r" % (e,))
        top = []
    add("  数量: %d" % len(top))
    for w in top:
        try:
            add("  pid=%-6s %-20s name=%r cls=%r" % (
                w.ProcessId, os.path.basename(exe_of(w.ProcessId) or "?"),
                (w.Name or "")[:40], w.ClassName))
        except Exception:
            add("  <ERR>")
    add("")

    add("### 5. 从 UIA 里找 QQ 窗口并导出界面树")
    found = []
    for w in top:
        try:
            if exe_of(w.ProcessId).lower().endswith("qq.exe"):
                found.append(w)
        except Exception:
            continue
    add("  UIA 中属于 QQ.exe 的窗口数: %d" % len(found))
    for i, w in enumerate(found[:3], 1):
        add("")
        add("  ===== QQ 窗口 %d: name=%r cls=%r =====" % (i, (w.Name or "")[:40], w.ClassName))
        tree = []
        dump(w, tree, 0, 6)
        add("  节点总数: %d" % len(tree))
        for x in tree[:120]:
            add(x)
        if len(tree) > 120:
            add("  ... 其余 %d 行省略" % (len(tree) - 120))
        hits = []
        kw_hits(w, hits)
        add("  --- 全部可读文本 (%d) ---" % len(hits))
        seen = set()
        for h in hits:
            if h in seen:
                continue
            seen.add(h)
            add("   " + h)
            if len(seen) > 60:
                break
    add("")
    add("=" * 78)
    add("报告结束")

    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uia_report3.txt")
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("OK -> %s" % p)


if __name__ == "__main__":
    main()
