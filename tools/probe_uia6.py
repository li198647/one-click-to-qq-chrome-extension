# -*- coding: utf-8 -*-
"""
QQ UIA 探针 v6 —— 一次性批量抓取 (FindAllBuildCache)
思路：逐层遍历太快导致 Chromium 节点来不及物化，改成一次请求整棵子树并缓存属性。

关键要看的：
  - 能不能找到 Edit（输入框）
  - 能不能找到会话项 / "我的电脑"
  - 带文字的节点到底有多少

输出：同目录 uia_report6.txt
"""
import ctypes
import ctypes.wintypes as wt
import os
import time

import uiautomation as auto
import comtypes.client
from comtypes.gen import UIAutomationClient as UIA

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

PID_NAME = 30005
PID_CTRLTYPE = 30003
PID_CLS = 30012
PID_AID = 30011
PID_RECT = 30001
PID_OFF = 30022
PID_ENABLED = 30010

TREE_DESCENDANTS = 4
TREE_SUBTREE = 7
TREE_ELEMENT = 1

CT = {
    50000: "Button", 50002: "CheckBox", 50003: "ComboBox", 50004: "Edit",
    50005: "Hyperlink", 50006: "Image", 50007: "ListItem", 50008: "List",
    50009: "Menu", 50010: "MenuBar", 50011: "MenuItem", 50012: "Progress",
    50013: "RadioButton", 50014: "ScrollBar", 50015: "Slider",
    50017: "StatusBar", 50018: "Tab", 50019: "TabItem", 50020: "Text",
    50021: "ToolBar", 50022: "ToolTip", 50023: "Tree", 50024: "TreeItem",
    50025: "Custom", 50026: "Group", 50028: "DataGrid", 50029: "DataItem",
    50030: "Document", 50032: "Window", 50033: "Pane", 50034: "Header",
    50036: "Table", 50037: "TitleBar", 50038: "Separator",
}

lines = []


def add(s=""):
    lines.append(str(s))


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


def main():
    add("=" * 78)
    add("QQ UIA 探针 v6 (FindAllBuildCache 批量抓取)")
    add("time : %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    add("=" * 78)
    add("")

    uia = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
    uia2 = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)

    cache = uia2.CreateCacheRequest()
    for pid in (PID_NAME, PID_CTRLTYPE, PID_CLS, PID_AID, PID_RECT, PID_OFF, PID_ENABLED):
        try:
            cache.AddProperty(pid)
        except Exception as e:
            add("AddProperty(%s) 失败 %r" % (pid, e))
    cache.TreeScope = TREE_ELEMENT

    cond = uia.CreateTrueCondition()

    root = auto.GetRootControl()
    qq = []
    for w in root.GetChildren():
        try:
            if exe_of(w.ProcessId).lower().endswith("qq.exe"):
                qq.append(w)
        except Exception:
            continue
    add("QQ 窗口数: %d" % len(qq))
    add("")

    for i, w in enumerate(qq[:3], 1):
        hwnd = w.NativeWindowHandle
        add("=" * 70)
        add("QQ 窗口 %d   hwnd=%s   rect=%s" % (i, hwnd, w.BoundingRectangle))
        add("=" * 70)
        try:
            el = uia.ElementFromHandle(hwnd)
        except Exception as e:
            add("ElementFromHandle 失败 %r" % (e,))
            continue

        try:
            el2 = uia2.ElementFromHandle(hwnd)
            found = el2.FindAllBuildCache(TREE_DESCENDANTS, cond, cache)
            cnt = found.Length
        except Exception as e:
            add("FindAllBuildCache 失败: %r" % (e,))
            continue

        add("后代元素总数: %d" % cnt)
        add("")

        rows = []
        errs = 0
        for k in range(cnt):
            try:
                e = found.GetElement(k)
                nm = (e.get_CachedName() or "").strip()
                ct = e.get_CachedControlType()
                cls = (e.get_CachedClassName() or "")
                aid = (e.get_CachedAutomationId() or "")
                r = e.get_CachedBoundingRectangle()
                off = e.get_CachedIsOffscreen()
                en = e.get_CachedIsEnabled()
                rows.append({
                    "name": nm, "ct": ct, "cls": cls, "aid": aid,
                    "rect": (r.left, r.top, r.right, r.bottom),
                    "off": off, "en": en,
                })
            except Exception:
                errs += 1
        add("属性读取失败的元素: %d" % errs)
        add("")

        named = [r for r in rows if r["name"]]
        add("--- 带文字的元素 (%d 个) ---" % len(named))
        seen = set()
        shown = 0
        for r in named:
            key = (r["ct"], r["name"])
            if key in seen:
                continue
            seen.add(key)
            add("  %-10s %-42r cls=%s" % (
                CT.get(r["ct"], str(r["ct"])), r["name"][:40], (r["cls"] or "")[:34]))
            shown += 1
            if shown >= 120:
                add("  ...更多省略")
                break
        add("")

        edits = [r for r in rows if r["ct"] == 50004]
        add("--- Edit 输入框 (%d 个) ---" % len(edits))
        for r in edits[:10]:
            add("  Edit name=%r cls=%r rect=%s en=%s off=%s" % (
                r["name"][:40], r["cls"][:30], r["rect"], r["en"], r["off"]))
        add("")

        kw = ("我的", "电脑", "设备", "手机", "传输")
        khits = [r for r in rows if any(x in (r["name"] + r["cls"] + r["aid"]) for x in kw)]
        add("--- 命中 我的/电脑/设备/手机/传输 的元素 (%d 个) ---" % len(khits))
        for r in khits[:30]:
            add("  %-10s name=%r cls=%r id=%r rect=%s" % (
                CT.get(r["ct"], str(r["ct"])), r["name"][:40], r["cls"][:30],
                r["aid"][:24], r["rect"]))
        add("")

        cts = {}
        for r in rows:
            cts[CT.get(r["ct"], str(r["ct"]))] = cts.get(CT.get(r["ct"], str(r["ct"])), 0) + 1
        add("--- 控件类型分布 ---")
        for k2 in sorted(cts, key=lambda x: -cts[x]):
            add("  %-12s %d" % (k2, cts[k2]))
        add("")

    add("=" * 78)
    add("报告结束")

    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uia_report6.txt")
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("OK -> %s" % p)


if __name__ == "__main__":
    main()
