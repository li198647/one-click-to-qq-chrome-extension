# -*- coding: utf-8 -*-
"""
QQ UIA 探针 v5 —— 用 RawView 走一遍 + 找文字节点
v4 已证明：打开读屏标志后 QQ 的 DOM 结构开始出现（q-theme-tokens-*）。
本版：绕过"控件视图"的过滤，用原始视图(RawView)导出尽可能完整的节点，并统计带文字的节点。

输出：同目录 uia_report5.txt
"""
import ctypes
import ctypes.wintypes as wt
import os
import time

import uiautomation as auto
import comtypes.client
from comtypes.gen import UIAutomationClient as UIA

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)

CT = {
    50000: "Button", 50001: "Calendar", 50002: "CheckBox", 50003: "ComboBox",
    50004: "Edit", 50005: "Hyperlink", 50006: "Image", 50007: "ListItem",
    50008: "List", 50009: "Menu", 50010: "MenuBar", 50011: "MenuItem",
    50012: "ProgressBar", 50013: "RadioButton", 50014: "ScrollBar",
    50015: "Slider", 50016: "Spinner", 50017: "StatusBar", 50018: "Tab",
    50019: "TabItem", 50020: "Text", 50021: "ToolBar", 50022: "ToolTip",
    50023: "Tree", 50024: "TreeItem", 50025: "Custom", 50026: "Group",
    50027: "Thumb", 50028: "DataGrid", 50029: "DataItem", 50030: "Document",
    50031: "SplitButton", 50032: "Window", 50033: "Pane", 50034: "Header",
    50035: "HeaderItem", 50036: "Table", 50037: "TitleBar", 50038: "Separator",
}

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


def main():
    add("=" * 78)
    add("QQ UIA 探针 v5 (RawView 全量遍历)")
    add("time : %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    add("=" * 78)
    add("")

    add("### 等待 8 秒，让 QQ 渲染进程有时间把无障碍树建起来")
    time.sleep(8.0)

    uia = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
    walker = uia.RawViewWalker

    root = auto.GetRootControl()
    qq = []
    for w in root.GetChildren():
        try:
            if exe_of(w.ProcessId).lower().endswith("qq.exe"):
                qq.append(w)
        except Exception:
            continue
    add("### UIA 可见 QQ 窗口数: %d" % len(qq))
    add("")

    for i, w in enumerate(qq[:3], 1):
        hwnd = w.NativeWindowHandle
        add("  ===== QQ 窗口 %d  hwnd=%s =====" % (i, hwnd))
        try:
            el = uia.ElementFromHandle(hwnd)
        except Exception as e:
            add("  ElementFromHandle 失败: %r" % (e,))
            continue

        out = []
        cap = [0]
        text_nodes = []

        def walk(e, depth):
            if depth > 14 or cap[0] > 2500:
                return
            try:
                child = walker.GetFirstChildElement(e)
            except Exception:
                return
            while child is not None:
                if cap[0] > 2500:
                    return
                cap[0] += 1
                try:
                    ct = child.CurrentControlType
                    nm = (child.CurrentName or "").strip()
                    cls = (child.CurrentClassName or "")[:30]
                    aid = (child.CurrentAutomationId or "")[:24]
                except Exception:
                    ct, nm, cls, aid = 0, "", "<err>", ""
                out.append("%s%-10s name=%r cls=%r id=%r" % (
                    "  " * (depth + 1), CT.get(ct, str(ct)), nm[:60], cls, aid))
                if nm:
                    text_nodes.append((CT.get(ct, str(ct)), nm[:80]))
                try:
                    walk(child, depth + 1)
                except Exception:
                    pass
                try:
                    child = walker.GetNextSiblingElement(child)
                except Exception:
                    break

        walk(el, 0)
        add("  原始视图节点总数: %d" % len(out))
        add("  其中有文字的节点: %d" % len(text_nodes))
        add("")
        add("  --- 前 130 行 ---")
        for x in out[:130]:
            add(x)
        if len(out) > 130:
            add("  ... 省略 %d 行" % (len(out) - 130))
        add("")
        add("  --- 全部带文字的节点 (去重, 最多 100) ---")
        seen = set()
        n = 0
        for ct, nm in text_nodes:
            key = (ct, nm)
            if key in seen:
                continue
            seen.add(key)
            add("    %-10s %r" % (ct, nm))
            n += 1
            if n >= 100:
                add("    ... 更多省略")
                break
        if not text_nodes:
            add("    (无 —— 无障碍树依然是空的)")
        add("")

    add("=" * 78)
    add("报告结束")

    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uia_report5.txt")
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("OK -> %s" % p)


if __name__ == "__main__":
    main()
