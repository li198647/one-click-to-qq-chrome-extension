# -*- coding: utf-8 -*-
"""
探针 v8 —— 正式打法的第一次验证
1. 用 UIA 找到名字为「我的手机」的元素（不猜坐标，读它的真实矩形）
2. 点它的中心
3. 重新读所有 QQ 窗口的 UIA 树，找聊天输入框（Edit）

输出：uia_report8.txt + shot_v8_*.png
"""
import ctypes
import ctypes.wintypes as wt
import os
import time

from PIL import Image
import uiautomation as auto
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
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.mouse_event.argtypes = [wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_void_p]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, ctypes.c_uint, ctypes.c_uint,
                            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]

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


def full_walk(uia, hwnd, maxdepth=16):
    walker = uia.RawViewWalker
    try:
        el = uia.ElementFromHandle(hwnd)
    except Exception as e:
        return [], "ElementFromHandle失败 %r" % (e,)
    out = []
    cap = [0]

    def w(e, depth):
        if depth > maxdepth or cap[0] > 4000:
            return
        try:
            c = walker.GetFirstChildElement(e)
        except Exception:
            return
        while c is not None:
            if cap[0] > 4000:
                return
            cap[0] += 1
            try:
                ct = c.CurrentControlType
                nm = (c.CurrentName or "").strip()
                cls = (c.CurrentClassName or "")
                rr = c.CurrentBoundingRectangle
                out.append({"ct": ct, "name": nm, "cls": cls,
                            "rect": (rr.left, rr.top, rr.right, rr.bottom), "d": depth})
            except Exception:
                pass
            w(c, depth + 1)
            try:
                c = walker.GetNextSiblingElement(c)
            except Exception:
                break

    w(el, 0)
    return out, "ok"


def mouse_click(x, y):
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.25)
    user32.mouse_event(0x0002, 0, 0, 0, None)
    time.sleep(0.08)
    user32.mouse_event(0x0004, 0, 0, 0, None)


def main():
    add("=" * 78)
    add("探针 v8 —— 用 UIA 读出的真实元素坐标去点「我的手机」")
    add("time : %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    add("=" * 78)
    add("")

    uia = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
    wins = qq_wins()
    # 主面板 = 含 Edit '搜索' 的那个
    panel = None
    all_nodes = {}
    for w in wins:
        nodes, msg = full_walk(uia, w["hwnd"])
        all_nodes[w["hwnd"]] = nodes
        if any(n["ct"] == 50004 and n["name"] == "搜索" for n in nodes):
            panel = w
    add("候选窗口与其节点数:")
    for w in wins:
        add("  hwnd=%-8s rect=%-26s 节点=%d" % (w["hwnd"], w["rect"], len(all_nodes[w["hwnd"]])))
    add("")

    if panel is None:
        add("找不到主面板（没有 搜索 输入框）。退出。")
        _flush()
        return
    add("主面板: hwnd=%s rect=%s" % (panel["hwnd"], panel["rect"]))
    add("")

    nodes = all_nodes[panel["hwnd"]]
    hits = [n for n in nodes if n["name"] == "我的手机"]
    add("### 名字等于『我的手机』的元素: %d 个" % len(hits))
    for n in hits:
        add("  %-8s rect=%s depth=%d" % (CTN.get(n["ct"], str(n["ct"])), n["rect"], n["d"]))
    add("")

    if not hits:
        add("### 没有直接命中。列出所有含『手机/设备』的元素:")
        for n in nodes:
            if "手机" in n["name"] or "设备" in n["name"]:
                add("  %-8s %r rect=%s" % (CTN.get(n["ct"], str(n["ct"])), n["name"], n["rect"]))
        add("### 另外列出『会话列表』Pane 的所有子节点:")
        for i, n in enumerate(nodes):
            if n["name"] == "会话列表":
                for m in nodes:
                    if m["d"] > n["d"] and m["d"] <= n["d"] + 3:
                        add("  %-8s %r rect=%s" % (CTN.get(m["ct"], str(m["ct"])), m["name"], m["rect"]))
                break
        _flush()
        return

    tgt = hits[0]["rect"]
    cx = (tgt[0] + tgt[2]) // 2
    cy = (tgt[1] + tgt[3]) // 2
    add("### 点击其中心 (%d, %d)" % (cx, cy))

    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    old = (pt.x, pt.y)
    user32.SetForegroundWindow(panel["hwnd"])
    time.sleep(0.5)
    mouse_click(cx, cy)
    time.sleep(3.0)
    user32.SetCursorPos(old[0], old[1])
    add("点击完成，等待 3 秒")
    add("")

    add("### 点击后各窗口截图")
    for i, w in enumerate(qq_wins(), 1):
        p = os.path.join(HERE, "shot_v8_%d_%s.png" % (i, w["hwnd"]))
        capture(w["hwnd"], p)
        add("  hwnd=%-8s rect=%s -> %s" % (w["hwnd"], w["rect"], os.path.basename(p)))
    add("")

    add("### 点击后读 UIA：重点找 Edit（输入框）")
    for i, w in enumerate(qq_wins(), 1):
        nodes2, msg = full_walk(uia, w["hwnd"])
        edits = [n for n in nodes2 if n["ct"] == 50004]
        named = [n for n in nodes2 if n["name"]]
        add("  --- 窗口%d hwnd=%s 节点=%d 带文字=%d Edit=%d ---" % (
            i, w["hwnd"], len(nodes2), len(named), len(edits)))
        for n in edits:
            add("      EDIT name=%r cls=%r rect=%s" % (n["name"][:40], n["cls"][:30], n["rect"]))
        for n in named:
            if any(k in n["name"] for k in ("发送", "输入", "手机", "会话", "消息列表")):
                add("      %-8s %r rect=%s" % (CTN.get(n["ct"], str(n["ct"])), n["name"][:40], n["rect"]))
        add("")

    _flush()


def _flush():
    add("=" * 78)
    add("报告结束")
    with open(os.path.join(HERE, "uia_report8.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("OK")


if __name__ == "__main__":
    main()
