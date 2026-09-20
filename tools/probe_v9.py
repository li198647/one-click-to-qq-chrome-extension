# -*- coding: utf-8 -*-
"""
探针 v9 —— 最后一次可行性验证
用 UIA Invoke 模式触发「我的手机」按钮（绕开"第一次点击只用于激活窗口"的问题），
然后看聊天输入框（Edit）到底会不会出现在 UIA 树里。

输出：uia_report9.txt + shot_v9_*.png
"""
import ctypes
import ctypes.wintypes as wt
import os
import time

from PIL import Image
import comtypes.client
from comtypes.gen import UIAutomationClient as UIA

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM), wt.LPARAM]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindowDC.argtypes = [wt.HWND]
user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, ctypes.c_uint]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.mouse_event.argtypes = [wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_void_p]
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, ctypes.c_uint, ctypes.c_uint,
                            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]

UIA_InvokePatternId = 10000
HERE = os.path.dirname(os.path.abspath(__file__))
lines = []


def add(s=""):
    lines.append(str(s))


class BIH(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class BI(ctypes.Structure):
    _fields_ = [("bmiHeader", BIH), ("bmiColors", wt.DWORD * 3)]


def exe_of(pid):
    h = kernel32.OpenProcess(0x1000, False, pid)
    n = ""
    if h:
        try:
            b = ctypes.create_unicode_buffer(1024)
            s = wt.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(h, 0, b, ctypes.byref(s)):
                n = b.value
        finally:
            kernel32.CloseHandle(h)
    return n


def qq_wins():
    out = []

    def cb(h, lp):
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if exe_of(pid.value).lower().endswith("qq.exe"):
            cls = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(h, cls, 128)
            r = wt.RECT()
            user32.GetWindowRect(h, ctypes.byref(r))
            if cls.value == "Chrome_WidgetWin_1" and (r.right - r.left) > 300:
                out.append({"hwnd": h, "rect": (r.left, r.top, r.right, r.bottom)})
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(cb), 0)
    return out


def capture(hwnd, path):
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    user32.PrintWindow(hwnd, mem, 2)
    bi = BI()
    bi.bmiHeader.biSize = ctypes.sizeof(BIH)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
    Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB").save(path)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)


CTN = {50000: "Button", 50004: "Edit", 50006: "Image", 50007: "ListItem",
       50008: "List", 50020: "Text", 50025: "Custom", 50026: "Group",
       50030: "Document", 50032: "Window", 50033: "Pane"}


def collect(uia, hwnd):
    """广度优先收集元素对象 + 属性"""
    try:
        root = uia.ElementFromHandle(hwnd)
    except Exception as e:
        return [], "失败 %r" % (e,)
    walker = uia.RawViewWalker
    out = []
    cap = [0]

    def w(e, depth):
        if depth > 16 or cap[0] > 4000:
            return
        try:
            c = walker.GetFirstChildElement(e)
        except Exception:
            return
        while c is not None:
            if cap[0] > 4000:
                return
            cap[0] += 1
            rec = {"el": c}
            try:
                rec["ct"] = c.CurrentControlType
                rec["name"] = (c.CurrentName or "").strip()
                rec["cls"] = c.CurrentClassName or ""
                rr = c.CurrentBoundingRectangle
                rec["rect"] = (rr.left, rr.top, rr.right, rr.bottom)
                rec["depth"] = depth
            except Exception:
                rec["ct"] = 0
                rec["name"] = ""
                rec["cls"] = "<err>"
                rec["rect"] = None
                rec["depth"] = depth
            out.append(rec)
            w(c, depth + 1)
            try:
                c = walker.GetNextSiblingElement(c)
            except Exception:
                break

    w(root, 0)
    return out, "ok"


def main():
    add("=" * 78)
    add("探针 v9 —— UIA Invoke 触发「我的手机」")
    add("time : %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    add("=" * 78)
    add("")

    uia = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
    panel = None
    data = {}
    for w in qq_wins():
        nodes, msg = collect(uia, w["hwnd"])
        data[w["hwnd"]] = nodes
        if any(n["ct"] == 50004 and n["name"] == "搜索" for n in nodes):
            panel = w
    if panel is None:
        add("找不到主面板，退出")
        _flush()
        return
    add("主面板 hwnd=%s rect=%s" % (panel["hwnd"], panel["rect"]))
    add("")

    cands = [n for n in data[panel["hwnd"]] if n["name"] == "我的手机"]
    add("『我的手机』元素 %d 个" % len(cands))
    if not cands:
        _flush()
        return
    tgt = cands[0]
    add("取第一个: %s rect=%s" % (CTN.get(tgt["ct"], tgt["ct"]), tgt["rect"]))
    add("")

    ok = False
    try:
        p = tgt["el"].GetCurrentPattern(UIA_InvokePatternId)
        inv = p.QueryInterface(UIA.IUIAutomationInvokePattern)
        inv.Invoke()
        add("✅ Invoke 模式调用成功")
        ok = True
    except Exception as e:
        add("❌ Invoke 失败: %r" % (e,))
        add("   退回『点两次』方案（第一次被 Chromium 拿去激活窗口）")

    if not ok:
        r = qq_wins()
        pw = [w for w in r if w["hwnd"] == panel["hwnd"]][0]
        x0, y0, x1, y1 = pw["rect"]
        # 重新取一次坐标（窗口可能已移动）
        nodes, _ = collect(uia, panel["hwnd"])
        c2 = [n for n in nodes if n["name"] == "我的手机" and n["rect"]]
        if c2:
            rr = c2[0]["rect"]
            cx, cy = (rr[0] + rr[2]) // 2, (rr[1] + rr[3]) // 2
            for k in range(2):
                user32.SetCursorPos(int(cx), int(cy))
                time.sleep(0.25)
                user32.mouse_event(0x0002, 0, 0, 0, None)
                time.sleep(0.08)
                user32.mouse_event(0x0004, 0, 0, 0, None)
                add("   第 %d 次点击 (%d,%d)" % (k + 1, cx, cy))
                time.sleep(0.8)
    time.sleep(3.0)
    add("")

    add("### 截图")
    for i, w in enumerate(qq_wins(), 1):
        p = os.path.join(HERE, "shot_v9_%d_%s.png" % (i, w["hwnd"]))
        capture(w["hwnd"], p)
        add("  hwnd=%-8s rect=%s -> %s" % (w["hwnd"], w["rect"], os.path.basename(p)))
    add("")

    add("### 结果：各窗口 Edit（输入框）清单")
    for i, w in enumerate(qq_wins(), 1):
        nodes, msg = collect(uia, w["hwnd"])
        edits = [n for n in nodes if n["ct"] == 50004]
        named = [n for n in nodes if n["name"]]
        add("  --- 窗口%d hwnd=%s 节点=%d 带文字=%d Edit=%d ---" % (
            i, w["hwnd"], len(nodes), len(named), len(edits)))
        for n in edits:
            add("      EDIT %r rect=%s" % (n["name"][:50], n["rect"]))
        for n in named:
            if any(k in n["name"] for k in ("发送", "输入", "手机", "会话")):
                add("      %-8s %r rect=%s" % (CTN.get(n["ct"], str(n["ct"])), n["name"][:40], n["rect"]))
        add("")

    _flush()


def _flush():
    add("=" * 78)
    add("报告结束")
    with open(os.path.join(HERE, "uia_report9.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("OK")


if __name__ == "__main__":
    main()
