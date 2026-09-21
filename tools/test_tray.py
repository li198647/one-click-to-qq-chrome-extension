# -*- coding: utf-8 -*-
r"""托盘（v1.0.6）的运行时自检

静态断言只能证明"源码里调了 Shell_NotifyIcon"。这里真的起一个**带控制台的桥**，
然后**从外面**（另一个进程）去验它到底做对了什么：

  [1] 正常路径 —— 托盘挂上之后，窗口必须
        · 被藏起来（IsWindowVisible == 0）
        · 标题栏的 X 被摘掉（WS_SYSMENU 清掉）
        · 多出一个托盘消息窗口（类名 QQBridgeTrayWnd_<pid>）
  [2] 托盘消失后必须"把窗口还回来" —— 给托盘窗口发 WM_CLOSE，
        窗口要重新可见、X 要还回来。否则用户就永远没有退出入口了。
  [3] **铁律**：托盘起不来时（把 _add 打桩成失败），窗口必须
        **原封不动留在任务栏**，X 也不能摘。这条比什么都重要 ——
        "没有入口"比"手滑关掉"糟糕得多。

⚠️ 两个本机特有的坑，这个测试都绕开了：
   ① 这台机器上**通常有一个真实在跑的桥**（木木的常驻桥），它也有同前缀的
      托盘窗口 —— 不按 pid 认领就会去动别人的窗口：既测不准，又会误伤真桥。
   ② `subprocess.Popen().pid` 拿到的是**外层包装进程**，真正的 python 是它的
      孙进程（实测 wrapper=5820 / 真身=20260），而托盘窗口属于真身 ——
      所以 pid 一律用**子进程自报**的那个（它写进 result.json 的 "pid"）。

跑法：
    python tools/test_tray.py
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BRIDGE = os.path.join(ROOT, "bridge")

PY = r"C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not os.path.exists(PY):
    PY = sys.executable

SANDBOX = os.path.join(HERE, "_tray_sandbox")
TITLE = "ZZ-TRAY-TEST-CONSOLE-ZZ"

u32 = ctypes.windll.user32
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
u32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
u32.GetWindowTextLengthW.argtypes = [wt.HWND]
u32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u32.IsWindowVisible.argtypes = [wt.HWND]
u32.IsWindowVisible.restype = wt.BOOL
u32.GetWindowLongPtrW.restype = ctypes.c_longlong
u32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
u32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
u32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]

GWL_STYLE = -16
WS_SYSMENU = 0x00080000
WM_CLOSE = 0x0010

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name,
                           (" — " + str(extra)) if extra else ""))


def windows():
    found = []

    def cb(hwnd, _):
        n = u32.GetWindowTextLengthW(hwnd)
        title = ""
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(hwnd, buf, n + 1)
            title = buf.value
        cbuf = ctypes.create_unicode_buffer(256)
        u32.GetClassNameW(hwnd, cbuf, 256)
        found.append({"hwnd": hwnd, "title": title, "cls": cbuf.value,
                      "vis": bool(u32.IsWindowVisible(hwnd))})
        return True

    u32.EnumWindows(WNDENUMPROC(cb), 0)
    return found


def find(pred):
    for w in windows():
        if pred(w):
            return w
    return None


def console_win():
    return find(lambda w: TITLE in w["title"])


def window_pid(hwnd):
    """窗口是**哪个进程**建的。托盘那个消息窗口由桥自己 CreateWindowEx 出来，
    所以它的 pid 就是桥的 pid。"""
    p = wt.DWORD()
    u32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
    return p.value


def tray_win(pid=None):
    """找托盘消息窗口（类名 QQBridgeTrayWnd_<pid>）。

    ⚠️ 必须按 pid 认领 —— 这台机器上**真有个常驻的桥**（木木在用），它也有一个
    同前缀的托盘窗口。不按 pid 过滤的后果是双重的：
      · 测不准：找得到别人的那个，就永远"没消失"、"铁律那一支也有托盘窗口"；
      · 会误伤：WM_CLOSE 会被发给**真实运行的桥**，把人家藏好的窗口弹出来。
    所以这里一律只认自己那个 pid 建的窗口。
    """
    for w in windows():
        if not w["cls"].startswith("QQBridgeTrayWnd_"):
            continue
        if pid is None or window_pid(w["hwnd"]) == pid:
            return w
    return None


def has_sysmenu(hwnd):
    return bool(u32.GetWindowLongPtrW(hwnd, GWL_STYLE) & WS_SYSMENU)


def kill_pid(pid):
    """按 pid 结束进程。

    为什么不直接用 proc.kill()：本机 Popen 拿到的是外层包装进程，杀它杀不掉
    真正的 python（孙进程），那个孩子会把托盘窗口/控制台窗口多留 45 秒。
    所以两个都杀：先按真身 pid 杀，再让 proc.kill() 收尾。
    """
    if not pid:
        return False
    k32 = ctypes.windll.kernel32
    PROCESS_TERMINATE = 0x0001
    k32.OpenProcess.restype = wt.HANDLE
    k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    k32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
    k32.CloseHandle.argtypes = [wt.HANDLE]
    h = k32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not h:
        return False
    k32.TerminateProcess(h, 0)
    k32.CloseHandle(h)
    return True


# ---------------------------------------------------------------- 子进程脚本
CHILD = r'''# -*- coding: utf-8 -*-
import json, os, sys, time
sys.path.insert(0, r"%(bridge)s")
MODE = %(mode)r
RESULT = r"%(result)s"
# ⚠️ 必须让子进程**自己报 pid**：本机（带沙箱外壳）里 subprocess.Popen().pid 拿到
#    的是外层包装进程，真正的 python 是它的孙进程 —— 实测 wrapper=5820 而真身=20260。
#    托盘窗口属于真身，所以认领窗口要用这里报的 pid，不能用 proc.pid。
out = {"tray_ok": None, "error": None, "pid": os.getpid()}
try:
    import qq_bridge
    qq_bridge.set_console_title(%(title)r)
    qq_bridge.setup_logging()
    if MODE == "fail":
        # 打桩：让托盘加图标这一步失败，验"铁律"那一支
        import qq_tray
        qq_tray.Tray._add = lambda self: False
    out["tray_ok"] = bool(qq_bridge.start_tray())
except Exception as e:
    import traceback
    out["error"] = traceback.format_exc()
finally:
    with open(RESULT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
time.sleep(45)
'''


def run_case(mode, label):
    print("\n[%s] mode=%s" % (label, mode))
    if os.path.isdir(SANDBOX):
        shutil.rmtree(SANDBOX, ignore_errors=True)
    os.makedirs(SANDBOX, exist_ok=True)
    child_py = os.path.join(SANDBOX, "child.py")
    result = os.path.join(SANDBOX, "result.json")
    with open(child_py, "w", encoding="utf-8") as f:
        f.write(CHILD % {"bridge": BRIDGE, "mode": mode, "result": result,
                         "title": TITLE})

    proc = subprocess.Popen([PY, child_py], cwd=SANDBOX,
                            creationflags=subprocess.CREATE_NEW_CONSOLE)

    # 等子进程把结果写出来
    info = None
    for _ in range(80):
        time.sleep(0.25)
        if os.path.exists(result):
            try:
                info = json.load(open(result, encoding="utf-8"))
                break
            except Exception:
                pass
    if info is None:
        proc.kill()
        check("%s: 子进程写出了结果" % label, False, "45 秒内没看到 result.json")
        return None

    # 给它一点时间把窗口藏好 / 摘 X
    time.sleep(1.5)
    pid = info.get("pid")
    print("    proc.pid=%s（外层包装）  子进程自报 pid=%s（按这个认领窗口）"
          % (proc.pid, pid))
    con = console_win()
    tray = tray_win(pid)
    state = {
        "info": info,
        "child_pid": pid,
        "console_found": bool(con),
        "console_visible": con["vis"] if con else None,
        "console_sysmenu": has_sysmenu(con["hwnd"]) if con else None,
        "tray_found": bool(tray),
        "tray_hwnd": tray["hwnd"] if tray else None,
    }
    state["proc"] = proc
    return state


def main():
    print("=" * 66)
    print("托盘自检（真的起一个带控制台的桥，从外面验）")
    print("=" * 66)

    # ---------------------------------------------- [1] 正常路径
    st = run_case("ok", "1 正常路径")
    if not st:
        return 1
    info = st["info"]
    check("1: 子进程没报错", not info.get("error"), (info.get("error") or "")[:200])
    check("1: start_tray() 返回 True（托盘挂上了）", info.get("tray_ok") is True,
          info.get("tray_ok"))
    check("1: 能找到它的控制台窗口", st["console_found"])
    check("1: 托盘挂上后，控制台窗口被藏起来了",
          st["console_visible"] is False, "visible=%s" % st["console_visible"])
    check("1: 标题栏的 X 被摘掉（WS_SYSMENU 已清）",
          st["console_sysmenu"] is False, "含 SYSMENU=%s" % st["console_sysmenu"])
    check("1: 多出了一个托盘消息窗口（QQBridgeTrayWnd_*）", st["tray_found"])

    # ---------------------------------------------- [2] 托盘消失 → 窗口还回来
    if st["tray_found"]:
        pid = st["child_pid"]
        u32.PostMessageW(st["tray_hwnd"], WM_CLOSE, 0, 0)
        back = None
        for _ in range(24):
            time.sleep(0.25)
            con = console_win()
            if con and con["vis"] and has_sysmenu(con["hwnd"]) and not tray_win(pid):
                back = con
                break
        check("2: 托盘消息窗口已消失", tray_win(pid) is None)
        check("2: 窗口重新可见了（没有把用户锁死）",
              bool(back), "" if back else "等了 6 秒窗口也没回来")
        x_back = bool(back) and has_sysmenu(back["hwnd"])
        check("2: 标题栏的 X 也还回来了", x_back)
    kill_pid(st["child_pid"])
    st["proc"].kill()

    # ---------------------------------------------- [3] 铁律
    st = run_case("fail", "3 铁律：托盘起不来")
    if not st:
        return 1
    info = st["info"]
    check("3: start_tray() 老实返回 False", info.get("tray_ok") is False,
          info.get("tray_ok"))
    check("3: 窗口**仍然可见**（没把用户锁死，也没白藏）",
          st["console_visible"] is True, "visible=%s" % st["console_visible"])
    check("3: 没有摘掉 X（既然没有托盘，就得留着正常的关闭方式）",
          st["console_sysmenu"] is True,
          "" if st["console_sysmenu"] else "X 被摘了，用户没出口了")
    check("3: 确实没有托盘窗口", not st["tray_found"])
    kill_pid(st["child_pid"])
    st["proc"].kill()

    shutil.rmtree(SANDBOX, ignore_errors=True)

    print("\n" + "=" * 66)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    if FAIL:
        for n in FAIL:
            print("   没过: " + n)
    print("=" * 66)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
