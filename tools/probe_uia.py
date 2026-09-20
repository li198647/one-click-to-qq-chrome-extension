# -*- coding: utf-8 -*-
"""
QQ UI Automation 探针 v2 —— 决定项目生死的一测
问题：QQ NT（Electron）到底有没有向 UI Automation 暴露界面结构？
如果有，我们就能靠它找到「我的电脑」会话并输入内容；如果没有，整条 UI 自动化路线作废。

输出：同目录 uia_report.txt (UTF-8)
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

import uiautomation as auto

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)


def exe_of(pid):
    name = ""
    h = kernel32.OpenProcess(0x1000, False, pid)
    if h:
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wt.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                name = buf.value
        finally:
            kernel32.CloseHandle(h)
    return name


KEYWORDS = ("我的电脑", "我的设备", "我的手机", "登录", "扫码", "好友", "消息", "搜索")

lines = []


def add(s=""):
    lines.append(str(s))


def dump(ctrl, out, depth=0, maxdepth=5, cap=None):
    """递归导出控件树，返回本次新增节点数"""
    if cap is None:
        cap = {"n": 0}
    if depth > maxdepth or cap["n"] >= 500:
        return 0
    n = 0
    try:
        kids = ctrl.GetChildren()
    except Exception:
        return 0
    for k in kids:
        if cap["n"] >= 500:
            break
        cap["n"] += 1
        n += 1
        try:
            line = "%s%s  name=%r  cls=%r  id=%r  off=%s  rect=%s" % (
                "  " * (depth + 1),
                k.ControlTypeName,
                (k.Name or "")[:70],
                (k.ClassName or "")[:40],
                (k.AutomationId or "")[:30],
                getattr(k, "IsOffscreen", "?"),
                tuple(int(v) for v in (k.BoundingRectangle.left, k.BoundingRectangle.top,
                                       k.BoundingRectangle.right, k.BoundingRectangle.bottom)),
            )
        except Exception as e:
            line = "%s<TYPE_ERR %s>" % ("  " * (depth + 1), type(e).__name__)
        out.append(line)
        n += dump(k, out, depth + 1, maxdepth, cap)
    return n


def collect_keyword_hits(ctrl, hits, depth=0, maxdepth=6, cap=None):
    if cap is None:
        cap = {"n": 0}
    if depth > maxdepth or cap["n"] >= 800:
        return
    try:
        kids = ctrl.GetChildren()
    except Exception:
        return
    for k in kids:
        if cap["n"] >= 800:
            return
        cap["n"] += 1
        try:
            nm = k.Name or ""
            if nm and any(kw in nm for kw in KEYWORDS):
                hits.append("%s  %-16s name=%r" % (
                    "  " * depth, k.ControlTypeName, nm[:60]))
        except Exception:
            pass
        collect_keyword_hits(k, hits, depth + 1, maxdepth, cap)


def main():
    add("=" * 78)
    add("QQ UI Automation 探针报告")
    add("time   : %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    add("python : %s" % sys.version.replace("\n", " "))
    add("uiautomation: %s" % getattr(auto, "VERSION", "?"))
    add("=" * 78)
    add("")

    root = auto.GetRootControl()
    add("### 1. 顶层窗口 (root.GetChildren 能看到的)")
    try:
        top = root.GetChildren()
    except Exception as e:
        add("  GetChildren 失败: %r" % (e,))
        top = []
    add("  顶层窗口数: %d" % len(top))
    add("")

    qq_wins = []
    for w in top:
        try:
            pid = w.ProcessId
            exe = exe_of(pid)
            if exe.lower().endswith("qq.exe") or "qqnt" in exe.lower():
                qq_wins.append((w, pid, exe))
        except Exception:
            continue

    add("### 2. 属于 QQ.exe 的窗口: %d 个" % len(qq_wins))
    for w, pid, exe in qq_wins:
        try:
            add("  pid=%-6s name=%r cls=%r rect=%s off=%s" % (
                pid, (w.Name or "")[:40], w.ClassName,
                tuple(int(v) for v in (w.BoundingRectangle.left, w.BoundingRectangle.top,
                                       w.BoundingRectangle.right, w.BoundingRectangle.bottom)),
                getattr(w, "IsOffscreen", "?")))
        except Exception as e:
            add("  <ERR %r>" % (e,))
    if not qq_wins:
        add("  (无 —— UIA 层面看不到 QQ 窗口)")
    add("")

    add("### 3. 【核心问题】每个 QQ 窗口的界面树深度探测")
    for idx, (w, pid, exe) in enumerate(qq_wins[:5], 1):
        add("")
        add("  ===== 窗口 %d / pid=%s name=%r =====" % (idx, pid, (w.Name or "")[:40]))
        try:
            first = []
            n1 = dump(w, first, 0, 5)
            add("  第一遍遍历: 子节点 %d 个" % n1)
            if n1 <= 1:
                add("  (节点极少，可能 Chromium 的无障碍引擎还没被唤醒)")
                time.sleep(2.0)
                first = []
                n1b = dump(w, first, 0, 5)
                add("  等待 2 秒后第二遍: 子节点 %d 个" % n1b)
                pre = [x for x in first if x.strip().startswith(("Text", "List", "Button", "Edit", "Pane", "Document", "Group", "Image", "ListItem", "Tab"))]
                add("  其中非 Pane 类节点 %d 个" % len(pre))
                for x in pre[:35]:
                    add(x)
            else:
                add("  --- 前 60 行 ---")
                for x in first[:60]:
                    add(x)
                if len(first) > 60:
                    add("  ... 其余 %d 行省略" % (len(first) - 60))

            hits = []
            collect_keyword_hits(w, hits)
            add("  --- 关键词命中 (%d) ---" % len(hits))
            for h in hits[:40]:
                add("  " + h)
        except Exception as e:
            add("  探测失败: %r" % (e,))

    add("")
    add("=" * 78)
    add("报告结束")

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uia_report.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("OK -> %s" % out_path)


if __name__ == "__main__":
    main()
