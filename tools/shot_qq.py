# -*- coding: utf-8 -*-
"""
把 QQ 各个窗口截成 PNG，供人眼/模型直接判断 QQ 当前到底是什么状态（已登录？扫码？加载中？）
用 PrintWindow(hwnd, hdc, PW_RENDERFULLCONTENT=2)，即使窗口被遮挡也能截到内容。

输出：同目录 shot_qq_<hwnd>.png
"""
import ctypes
import ctypes.wintypes as wt
import os

from PIL import Image

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindowDC.argtypes = [wt.HWND]
user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, ctypes.c_uint]
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, ctypes.c_uint, ctypes.c_uint,
                            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
        ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


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
        return None, "窗口尺寸无效 w=%s h=%s" % (w, h)

    hdc_win = user32.GetWindowDC(hwnd)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_win)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_win, w, h)
    gdi32.SelectObject(hdc_mem, hbmp)

    ok = user32.PrintWindow(hwnd, hdc_mem, 2)

    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = 0
    bi.bmiHeader.biSizeImage = w * h * 4

    buf = ctypes.create_string_buffer(w * h * 4)
    got = gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bi), 0)

    img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
    img.save(path)

    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(hwnd, hdc_win)

    # 统计是否全黑（PrintWindow 失败时会全黑）
    ext = img.convert("L").getextrema()
    return ok, "size=%dx%d GetDIBits=%s 灰度范围=%s" % (w, h, got, ext)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    res = []
    wins = []

    def cb(h, lp):
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if exe_of(pid.value).lower().endswith("qq.exe"):
            r = wt.RECT()
            user32.GetWindowRect(h, ctypes.byref(r))
            cls = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(h, cls, 128)
            if cls.value == "Chrome_WidgetWin_1" and (r.right - r.left) > 300:
                wins.append((h, pid.value, (r.left, r.top, r.right, r.bottom)))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)

    for i, (h, pid, rect) in enumerate(wins, 1):
        p = os.path.join(here, "shot_qq_%d_%s.png" % (i, h))
        try:
            ok, info = capture(h, p)
            res.append("窗口%d hwnd=%s rect=%s PrintWindow=%s %s -> %s" % (
                i, h, rect, ok, info, os.path.basename(p)))
        except Exception as e:
            res.append("窗口%d hwnd=%s 截图失败: %r" % (i, h, e))

    with open(os.path.join(here, "shot_log.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(res) if res else "没有找到候选 QQ 窗口")
    print("done")


if __name__ == "__main__":
    main()
