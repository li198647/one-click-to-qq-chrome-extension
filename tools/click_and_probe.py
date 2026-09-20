# -*- coding: utf-8 -*-
"""
决定性一测：点开 QQ 的「我的手机」会话，再看 UI Automation 能不能读到聊天窗口里的输入框。

步骤：截图(前) -> 把主面板提到前台 -> 点击「我的手机」那一行 -> 截图(后) -> 用 RawView 重新读 UIA 树

输出：shot_before.png / shot_after_*.png / uia_report7.txt
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
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
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


def capture(hwnd, path):
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return None
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
    img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
    img.save(path)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    return (r.left, r.top, r.right, r.bottom)


def qq_windows():
    out = []

    def cb(h, lp):
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if exe_of(pid.value).lower().endswith("qq.exe"):
            cls = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(h, cls, 128)
            r = wt.RECT()
            user32.GetWindowRect(h, ctypes.byref(r))
            out.append({"hwnd": h, "cls": cls.value,
                        "rect": (r.left, r.top, r.right, r.bottom),
                        "vis": bool(user32.IsWindowVisible(h))})
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(cb), 0)
    return [w for w in out if w["cls"] == "Chrome_WidgetWin_1"
            and w["rect"][2] - w["rect"][0] > 300]


def walk_names(uia, hwnd):
    """RawView 遍历，返回 (节点数, 带文字节点列表, Edit节点列表)"""
    walker = uia.RawViewWalker
    try:
        el = uia.ElementFromHandle(hwnd)
    except Exception as e:
        return -1, [], [], "ElementFromHandle失败 %r" % (e,)
    out = []
    cap = [0]

    def w(e, depth):
        if depth > 14 or cap[0] > 3000:
            return
        try:
            c = walker.GetFirstChildElement(e)
        except Exception:
            return
        while c is not None:
            if cap[0] > 3000:
                return
            cap[0] += 1
            try:
                ct = c.CurrentControlType
                nm = (c.CurrentName or "").strip()
                cls = (c.CurrentClassName or "")
                aid = (c.CurrentAutomationId or "")
                rr = c.CurrentBoundingRectangle
                rec = (ct, nm, cls, aid, (rr.left, rr.top, rr.right, rr.bottom))
            except Exception:
                rec = (0, "", "<err>", "", None)
            out.append((depth, rec))
            w(c, depth + 1)
            try:
                c = walker.GetNextSiblingElement(c)
            except Exception:
                break

    w(el, 0)
    named = [r for _, r in out if r[1]]
    edits = [r for _, r in out if r[0] == 50004]
    return cap[0], named, edits, "ok"


CTN = {50000: "Button", 50004: "Edit", 50006: "Image", 50007: "ListItem",
       50008: "List", 50020: "Text", 50025: "Custom", 50026: "Group",
       50030: "Document", 50032: "Window", 50033: "Pane", 50037: "TitleBar"}


def main():
    add("=" * 78)
    add("决定性一测：点开「我的手机」会话 -> 看 UIA 能否读到输入框")
    add("time : %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    add("=" * 78)
    add("")

    wins = qq_windows()
    add("### QQ 窗口")
    for w in wins:
        add("  hwnd=%-8s vis=%-5s rect=%s" % (w["hwnd"], w["vis"], w["rect"]))
    add("")

    # 主面板 = 宽度 400~520 的那个（446）
    panel = None
    for w in wins:
        wd = w["rect"][2] - w["rect"][0]
        if 400 <= wd <= 520:
            panel = w
            break
    if panel is None:
        add("找不到主面板窗口，退出")
        _flush()
        return
    add("主面板: hwnd=%s rect=%s" % (panel["hwnd"], panel["rect"]))
    add("")

    before = os.path.join(HERE, "shot_before.png")
    rect = capture(panel["hwnd"], before)
    add("已截图(前): %s  实时rect=%s" % (os.path.basename(before), rect))
    add("")

    x0, y0, x1, y1 = rect
    w_px, h_px = x1 - x0, y1 - y0
    # 依据截图观测：「我的手机」这一行位于窗口高度的约 82.9%，横向约 44.8%
    cx = int(x0 + w_px * 0.448)
    cy = int(y0 + h_px * 0.829)
    add("点击目标屏幕坐标: (%d, %d)" % (cx, cy))
    add("")

    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    old = (pt.x, pt.y)

    user32.SetForegroundWindow(panel["hwnd"])
    time.sleep(0.6)
    user32.SetCursorPos(cx, cy)
    time.sleep(0.3)
    user32.mouse_event(0x0002, 0, 0, 0, None)   # LEFTDOWN
    time.sleep(0.08)
    user32.mouse_event(0x0004, 0, 0, 0, None)   # LEFTUP
    add("已发送单击")
    time.sleep(2.5)
    user32.SetCursorPos(old[0], old[1])
    add("")

    add("### 点击后的截图")
    for i, w in enumerate(qq_windows(), 1):
        p = os.path.join(HERE, "shot_after_%d_%s.png" % (i, w["hwnd"]))
        try:
            capture(w["hwnd"], p)
            add("  hwnd=%-8s rect=%s -> %s" % (w["hwnd"], w["rect"], os.path.basename(p)))
        except Exception as e:
            add("  hwnd=%s 截图失败 %r" % (w["hwnd"], e))
    add("")

    add("### 点击后重新读 UIA 树")
    uia = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
    for i, w in enumerate(qq_windows(), 1):
        n, named, edits, msg = walk_names(uia, w["hwnd"])
        add("  --- 窗口%d hwnd=%s ---" % (i, w["hwnd"]))
        add("      节点数=%s  %s" % (n, msg))
        add("      带文字节点=%d   Edit输入框=%d" % (len(named), len(edits)))
        for ct, nm, cls, aid, rr in edits[:8]:
            add("      EDIT name=%r cls=%r rect=%s" % (nm[:40], cls[:30], rr))
        seen = set()
        shown = 0
        for ct, nm, cls, aid, rr in named:
            k = (ct, nm)
            if k in seen:
                continue
            seen.add(k)
            add("      %-8s %r" % (CTN.get(ct, str(ct)), nm[:60]))
            shown += 1
            if shown >= 45:
                add("      ...更多省略")
                break
        add("")

    _flush()


def _flush():
    add("=" * 78)
    add("报告结束")
    with open(os.path.join(HERE, "uia_report7.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("OK")


if __name__ == "__main__":
    main()
