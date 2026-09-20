# -*- coding: utf-8 -*-
"""
QQ 窗口探针 v1 —— 只用标准库，不装任何依赖
目的：判定"UI 自动化操控桌面 QQ"这条路是否可行

检查四件事：
  1. 本机 QQ 相关进程是否在运行
  2. QQ 主窗口/会话窗口的 窗口标题 + 窗口类名（UI 自动化要靠它定位）
  3. 是否存在标题含"我的电脑 / 我的设备 / 我的手机"的窗口
  4. 枚举 QQ 窗口的子窗口，看 NT 版( Electron )暴露了多少结构

输出：脚本同目录下 probe_report.txt (UTF-8)
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
user32.EnumChildWindows.argtypes = [wt.HWND, WNDENUMPROC, wt.LPARAM]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]


def wtext(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def wclass(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def wpid(hwnd):
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def wrect(hwnd):
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


_EXE_CACHE = {}


def exe_of(pid):
    if pid in _EXE_CACHE:
        return _EXE_CACHE[pid]
    name = ""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if h:
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wt.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                name = buf.value
        finally:
            kernel32.CloseHandle(h)
    _EXE_CACHE[pid] = name
    return name


def top_level_windows():
    out = []

    def cb(hwnd, lparam):
        out.append({
            "hwnd": int(hwnd) if hwnd else 0,
            "pid": wpid(hwnd),
            "cls": wclass(hwnd),
            "title": wtext(hwnd),
            "visible": bool(user32.IsWindowVisible(hwnd)),
            "rect": wrect(hwnd),
        })
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def child_windows(hwnd, limit=150):
    out = []

    def cb(h, lparam):
        if len(out) < limit:
            out.append({
                "hwnd": int(h) if h else 0,
                "cls": wclass(h),
                "title": wtext(h),
                "visible": bool(user32.IsWindowVisible(h)),
            })
        return True

    user32.EnumChildWindows(hwnd, WNDENUMPROC(cb), 0)
    return out


def process_lines():
    try:
        r = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="gbk",
            errors="replace", timeout=30,
        )
        rows = []
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            low = line.lower()
            if any(k in low for k in ("qq.exe", "qqnt", "tencent", "weixin", "tim.exe", "ntqq")):
                rows.append(line)
        return rows
    except Exception as e:
        return ["tasklist 调用失败: %r" % (e,)]


def lib_status():
    res = []
    for m in ("win32gui", "pywinauto", "uiautomation", "psutil", "comtypes"):
        try:
            __import__(m)
            res.append("%-14s 可用" % m)
        except Exception as e:
            res.append("%-14s 不可用 (%s)" % (m, type(e).__name__))
    return res


def main():
    lines = []
    add = lines.append

    add("=" * 78)
    add("QQ 窗口探针报告")
    add("python : %s" % sys.version.replace("\n", " "))
    add("exe    : %s" % sys.executable)
    add("cwd    : %s" % os.getcwd())
    add("=" * 78)
    add("")

    add("### 0. 相关 Python 库可用性")
    for s in lib_status():
        add("  " + s)
    add("")

    procs = process_lines()
    add("### 1. QQ / 腾讯相关进程 (%d 条)" % len(procs))
    if procs:
        for p in procs:
            add("  " + p)
    else:
        add("  (无 —— QQ 可能没在运行，或没安装)")
    add("")

    wins = top_level_windows()
    add("### 2. 顶层窗口总数: %d" % len(wins))
    add("")

    KW = ("我的电脑", "我的设备", "我的手机", "qq", "tim")

    hits = []
    for w in wins:
        t = w["title"]
        if t and any(k in t.lower() for k in KW):
            hits.append(w)

    add("### 3. 标题命中关键词的窗口 (%d 个)" % len(hits))
    for w in hits:
        add("  hwnd=%-10s pid=%-6s vis=%-5s %s" % (
            w["hwnd"], w["pid"], w["visible"], w["cls"]))
        add("      title : %s" % w["title"])
        add("      exe   : %s" % (exe_of(w["pid"]) or "<取不到>"))
        add("      rect  : %s" % (w["rect"],))
    if not hits:
        add("  (无命中)")
    add("")

    qq_wins = [w for w in wins if "qq.exe" in (exe_of(w["pid"]) or "").lower()]
    add("### 4. 属于 QQ.exe 的顶层窗口 (%d 个)" % len(qq_wins))
    for w in qq_wins:
        add("  hwnd=%-10s vis=%-5s cls=%-28s title=%r" % (
            w["hwnd"], w["visible"], w["cls"], w["title"]))
    if not qq_wins:
        add("  (无 —— 说明 QQ 没运行，或 QQ 的进程名不是 QQ.exe)")
    add("")

    add("### 5. 每个 QQ 窗口的子窗口结构（UI 自动化能看到的层级）")
    for w in qq_wins[:6]:
        add("")
        add("  --- hwnd=%s title=%r ---" % (w["hwnd"], w["title"]))
        kids = child_windows(w["hwnd"])
        add("      子窗口数: %d" % len(kids))
        for k in kids[:40]:
            add("        %-34s vis=%-5s title=%r" % (k["cls"], k["visible"], k["title"]))
        if len(kids) > 40:
            add("        ... 其余 %d 个省略" % (len(kids) - 40))
    add("")

    add("### 6. 全部可看见且有标题的顶层窗口（人工排查用）")
    shown = [w for w in wins if w["visible"] and w["title"]]
    add("  共 %d 个" % len(shown))
    for w in shown:
        add("  pid=%-6s %-22s %r" % (
            w["pid"], os.path.basename(exe_of(w["pid"]) or "?"), w["title"]))
    add("")
    add("=" * 78)
    add("报告结束")

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_report.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print("OK -> %s" % out_path)
    print("QQ顶层窗口数=%d, 标题命中=%d, QQ相关进程=%d" % (len(qq_wins), len(hits), len(procs)))


if __name__ == "__main__":
    main()
