# -*- coding: utf-8 -*-
r"""
Native Messaging 宿主的完整自检（v1.0.4）

测什么：真的把 bridge\\qq_host.bat / qq_native_host.py 起起来，按 Chrome
官方的帧格式喂它消息，看它回什么。不是"另抄一份逻辑来测"—— 跑的就是
产品本身那条路。

四条必须钉死的事（任何一条出错，用户那边都只会看到"点了按钮没反应"，
极难排查）：

  ① **stdout 上只能有消息帧**。宿主只要多写一个字节（一句 echo、一个
     启动横幅），Chrome 那边整个协议就乱掉 —— 报出来的还只会是
     "native host has exited" 这种跟真实原因毫无关系的错。
     所以这里对 stdout 做**逐字节**校验：必须正好是 4+len 个字节。

  ② **含中文的路径能穿过 cmd.exe**。本工程就在
     E:\\workbuddywork\\一键发到qq\\bridge\\ 下，而 Chrome 是用
     `cmd.exe /d /s /c "…\\qq_host.bat"` 这种形式拉起宿主的。批处理是
     cmd 按 OEM 代码页（这台机器是 936/GBK）读的 → 内容必须纯 ASCII；
     路径里的中文靠 CreateProcessW 的 UTF-16 命令行传过去。

  ③ **新窗口是最小化的**（木木选的"任务栏留图标、不抢焦点"）。

  ④ **宿主进程被 Chrome 杀掉之后，它拉起的桥要活着**。Chrome 收到回复
     就把宿主进程 TerminateProcess 掉 —— 如果桥还在宿主的进程树里被
     一起带走，这个功能就是"点了没反应"。

另外 [1b] 会按**浏览器自己的规则**把"查找宿主"走一遍（先 Thorium 那个根、
再 Google\Chrome 兜底），把清单逐条验掉。那是模拟，不是真的让浏览器加载
扩展 —— 原因是自动化加载未打包扩展这条路走不通：
  · `--load-extension` 在 Chromium 137+ 已被移除（本机实测无效）
  · 新的 CDP `Extensions.loadUnpacked` 域在这版 Chromium 里也不可用
所以"浏览器 → 宿主"这最后一跳只能手工验一次（重载扩展 → 重开弹窗 →
看有没有那个按钮）。本脚本负责把那一步之前的全部前置条件验干净。

跑法：
    python tools/test_native_host.py
"""

import json
import os
import shutil
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BRIDGE = os.path.join(ROOT, "bridge")

PY = r"C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
COMSPEC = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")
HOST_PY = os.path.join(BRIDGE, "qq_native_host.py")
HOST_BAT = os.path.join(BRIDGE, "qq_host.bat")
LAUNCH_BAT = os.path.join(BRIDGE, "start_bridge.bat")
HOST_MANIFEST = os.path.join(BRIDGE, "com.mumu.qq_bridge.json")

SANDBOX = os.path.join(HERE, "_nm_sandbox")
STUB_TITLE = "QQ Bridge - STUB FOR TEST"

passed = []
failed = []


def check(name, cond, extra=""):
    (passed if cond else failed).append(name)
    suffix = "" if cond else ("  " + str(extra))
    print(("  [PASS] " if cond else "  [FAIL] ") + name + suffix)


def frame(obj):
    b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return struct.pack("<I", len(b)) + b


def read_frame(raw, pos=0):
    if len(raw) < pos + 4:
        return None, pos
    n = struct.unpack("<I", raw[pos:pos + 4])[0]
    if len(raw) < pos + 4 + n:
        return None, pos
    body = raw[pos + 4:pos + 4 + n]
    return json.loads(body.decode("utf-8")), pos + 4 + n


def run_host(cmdline, msg, timeout=30, cwd=None):
    """跑一条**整串**命令行（不是列表），喂一条消息，把 stdout 原样收回。

    ⚠️ 必须传字符串：用列表时 Python 会走 subprocess.list2cmdline，把内层
    引号转义成 \\" —— cmd 不认这种写法，会报
    「'\\"E:\\...\\qq_host.bat\\"' 不是内部或外部命令」。
    Chrome 也是自己拼一整条命令行（base::CommandLine），不是列表。"""
    p = subprocess.Popen(
        cmdline,
        cwd=cwd or BRIDGE,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        out, err = p.communicate(frame(msg), timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        out, err = p.communicate()
    return out, err


# ---------------------------------------------------------------- ① 静态检查

def test_static():
    print("\n[1] 静态检查")

    check("qq_host.bat 存在", os.path.isfile(HOST_BAT))
    check("重新登记.bat 存在", os.path.isfile(os.path.join(BRIDGE, "重新登记.bat")))

    raw = open(HOST_BAT, "rb").read()
    check("qq_host.bat 内容是纯 ASCII（GBK 代码页下不会乱码）",
          all(b < 128 for b in raw),
          "非 ASCII 字节数=%d" % sum(1 for b in raw if b >= 128))
    check("qq_host.bat 是 CRLF 行尾",
          raw.count(b"\n") == raw.count(b"\r\n") and raw.count(b"\n") > 0)
    low = raw.lower()
    check("chcp 带 >nul 重定向（否则会往 stdout 吐一行）", b"chcp 65001 >nul" in low)
    check("没有裸 echo", b"\necho " not in low)

    rb = open(os.path.join(BRIDGE, "重新登记.bat"), "rb").read()
    check("重新登记.bat 纯 ASCII + CRLF",
          all(b < 128 for b in rb) and rb.count(b"\n") == rb.count(b"\r\n"))

    check("宿主清单已生成", os.path.isfile(HOST_MANIFEST))
    mf = json.load(open(HOST_MANIFEST, "r", encoding="utf-8"))
    check("清单 name 正确", mf.get("name") == "com.mumu.qq_bridge", mf.get("name"))
    check("清单 type = stdio", mf.get("type") == "stdio")
    check("清单 path 指向 qq_host.bat", mf.get("path") == HOST_BAT, mf.get("path"))
    check("清单 path 是绝对路径", os.path.isabs(mf.get("path") or ""))
    check("清单 allowed_origins 写死扩展 ID",
          mf.get("allowed_origins") == ["chrome-extension://onpgmgnpdgkhebogdflhbojdchcbegcg/"],
          mf.get("allowed_origins"))

    try:
        import winreg
        sys.path.insert(0, BRIDGE)
        import qq_native_host as hreg

        # 登记位置必须**覆盖这台浏览器实际会看的那几处**。
        # 实测（把 Thorium 的 chrome.dll 全文搜了一遍，见
        # tools/probe-native-roots.py）：Thorium 只看
        #   SOFTWARE\Thorium\NativeMessagingHosts
        #   SOFTWARE\Google\Chrome\NativeMessagingHosts
        # **不看** Software\Chromium —— 早先只写了 Chromium + Google\Chrome
        # 还能通，纯属兜底那一条救了命。这里把三条都钉住，防止哪天被删。
        want = {
            "Software\\Thorium\\NativeMessagingHosts",
            "Software\\Google\\Chrome\\NativeMessagingHosts",
            "Software\\Chromium\\NativeMessagingHosts",
        }
        got = set(s.rsplit("\\", 1)[0] for s in hreg.REG_SUBKEYS)
        check("登记位置覆盖 Thorium / Google\\Chrome / Chromium 三处（一个都不能少）",
              got == want, "缺: %s 多: %s" % (sorted(want - got), sorted(got - want)))

        for sub in hreg.REG_SUBKEYS:
            label = sub.split("\\")[1]
            try:
                k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub, 0, winreg.KEY_READ)
                v, _ = winreg.QueryValueEx(k, "")
                winreg.CloseKey(k)
                check("注册表已登记 HKCU\\%s" % label, v == HOST_MANIFEST, v)
            except Exception as e:
                check("注册表已登记 HKCU\\%s" % label, False, e)
    except Exception as e:
        check("winreg 可用", False, e)

    # 桥自己的代码里必须真的调了自登记
    src = open(os.path.join(BRIDGE, "qq_bridge.py"), "r", encoding="utf-8").read()
    check("qq_bridge.py 启动时会自登记",
          "self_register_host()" in src and "register_host" in src)
    check("qq_bridge.py 版本已到 1.0.4", 'VERSION = "1.0.4"' in src)


# ---------------------------------------------------------------- ①b 模拟浏览器查找

# 这台浏览器实际会按顺序查这几个根（从 Thorium 的 chrome.dll 里搜出来的，
# 见 tools/probe-native-roots.py）。顺序不影响结果 —— 因为我们对每个根写
# 的是同一份清单路径。
BROWSER_ROOTS = [
    "SOFTWARE\\Thorium\\NativeMessagingHosts",
    "SOFTWARE\\Google\\Chrome\\NativeMessagingHosts",
]


def test_browser_lookup():
    """按浏览器自己的规则走一遍查找，看它能不能找到一份**合法**的宿主清单。

    ⚠️ 这一段是**模拟**，不是真的让浏览器去加载扩展。原因值得写下来：
    自动化加载未打包扩展这条路走不通 —— `--load-extension` 在 Chromium 137+
    已被移除（本机实测：带上它、并把 Playwright 默认的 --disable-extensions
    排除掉，chrome://extensions 里依然是 0 个扩展），而新的 CDP
    Extensions.loadUnpacked 域在这版 Chromium 里也不可用（"Method not
    available"）。所以"浏览器 → 宿主"这最后一跳只能由木木手工验一次
    （重载扩展 → 重开弹窗 → 看有没有那个按钮）。
    这里做的是把那一步之前的**全部前置条件**都验掉，把手工验证的失败面
    缩到最小。"""
    print("\n[1b] 模拟浏览器查找宿主（按 Thorium 实际的两个根）")
    try:
        import winreg
    except Exception as e:
        check("winreg 可用", False, e)
        return

    hit_root, hit_path = None, None
    for root in BROWSER_ROOTS:
        sub = root + "\\com.mumu.qq_bridge"
        try:
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub, 0, winreg.KEY_READ)
            val, _ = winreg.QueryValueEx(k, "")
            winreg.CloseKey(k)
            if not hit_root:
                hit_root, hit_path = root, val
        except FileNotFoundError:
            continue
        except Exception as e:
            check("能打开 HKCU\\%s" % root, False, e)

    check("按浏览器的规则能找到一个已登记的宿主", bool(hit_root), BROWSER_ROOTS)
    if not hit_root:
        return
    print("  命中：HKCU\\%s  ->  %s" % (hit_root, hit_path))
    check("命中位置就是 Thorium 自己那个根（不靠兜底）",
          hit_root == BROWSER_ROOTS[0], hit_root)

    check("默认值指向的清单文件存在", os.path.isfile(hit_path or ""), hit_path)
    if not os.path.isfile(hit_path or ""):
        return

    mf = json.load(open(hit_path, "r", encoding="utf-8"))
    check("清单 name 与注册表子键名一致（不一致浏览器直接不认）",
          mf.get("name") == "com.mumu.qq_bridge", mf.get("name"))
    check("清单 type = stdio", mf.get("type") == "stdio", mf.get("type"))
    p = mf.get("path") or ""
    check("清单里的宿主路径是绝对路径（Chrome 会拒相对路径）", os.path.isabs(p), p)
    check("清单里的宿主程序真的在", os.path.isfile(p), p)
    check("allowed_origins 里含本扩展的 ID",
          ("chrome-extension://onpgmgnpdgkhebogdflhbojdchcbegcg/" in
           (mf.get("allowed_origins") or [])),
          mf.get("allowed_origins"))


# ---------------------------------------------------------------- ② 协议

def test_protocol_direct():
    print("\n[2] 协议（直接跑 python，不经 cmd）")
    out, err = run_host([PY, HOST_PY], {"cmd": "ping"})
    resp, endpos = read_frame(out)
    check("ping 有回应", isinstance(resp, dict), out[:80])
    if isinstance(resp, dict):
        check("ping -> pong", resp.get("action") == "pong", resp.get("action"))
        check("ping 带上版本号 1.0.4", resp.get("version") == "1.0.4", resp.get("version"))
    check("stdout 正好一帧、零多余字节",
          resp is not None and endpos == len(out),
          "用掉 %d / 共 %d 字节" % (endpos, len(out)))

    out, _ = run_host([PY, HOST_PY], {"cmd": "status"})
    resp, _ = read_frame(out)
    check("status 有回应", isinstance(resp, dict) and resp.get("ok") is True)
    if isinstance(resp, dict):
        check("status 报告宿主程序存在", resp.get("host_bat_exists") is True)
        check("status 报告启动脚本存在", resp.get("launch_bat_exists") is True)

    out, _ = run_host([PY, HOST_PY], {"cmd": "definitely-not-a-cmd"})
    resp, _ = read_frame(out)
    check("不认识的指令 -> ok:false + 人话",
          isinstance(resp, dict) and resp.get("ok") is False
          and resp.get("reason") == "unknown-cmd", resp)

    # start：桥此刻正在跑，必须幂等 —— 不能重复拉起第二个桥
    out, _ = run_host([PY, HOST_PY], {"cmd": "start"})
    resp, _ = read_frame(out)
    check("start 有回应", isinstance(resp, dict) and resp.get("ok") is True, resp)
    check("桥已在跑时不重复启动（幂等）",
          isinstance(resp, dict) and resp.get("action") == "already-running",
          resp.get("action") if isinstance(resp, dict) else resp)


# ---------------------------------------------------------------- ③ 穿 cmd

def test_via_cmd():
    print("\n[3] 经 cmd.exe 包一层（Chrome 就是这么拉起宿主的）")
    cmdline = '"%s" /d /s /c ""%s""' % (COMSPEC, HOST_BAT)
    print("  命令行: %s" % cmdline)
    out, err = run_host(cmdline, {"cmd": "ping"}, cwd="C:\\")
    resp, endpos = read_frame(out)
    check("经 cmd + bat 拿到响应（中文路径穿过去了）",
          isinstance(resp, dict) and resp.get("action") == "pong",
          "stdout=%r stderr=%r" % (out[:120], err[-400:]))
    check("经 cmd 时 stdout 依然只有一帧",
          resp is not None and endpos == len(out),
          "%d / %d" % (endpos, len(out)))


# ---------------------------------------------------------------- 沙箱

def build_sandbox(port=18999):
    """造一个假 bridge 目录：宿主用它的逻辑跑，但只认一个没人监听的端口，
    所以"启动桥"会真的去拉起那个 stub，不会碰到真桥。"""
    if os.path.isdir(SANDBOX):
        shutil.rmtree(SANDBOX, ignore_errors=True)
    os.makedirs(SANDBOX)

    shutil.copy2(HOST_PY, os.path.join(SANDBOX, "qq_native_host.py"))

    with open(os.path.join(SANDBOX, "config.json"), "w", encoding="utf-8") as f:
        json.dump({"port": port}, f)

    stub = os.path.join(SANDBOX, "start_bridge.bat")
    with open(stub, "w", encoding="ascii", newline="") as f:
        f.write("@echo off\r\n")
        f.write("title %s\r\n" % STUB_TITLE)
        f.write("cd /d \"%~dp0\"\r\n")
        # 自己找死：跑 40 秒左右就退出，绝不留孤儿进程
        f.write("for /L %%i in (1,1,40) do (\r\n")
        f.write("  echo tick %%i >> \"%~dp0ticks.txt\"\r\n")
        f.write("  ping -n 2 127.0.0.1 >nul\r\n")
        f.write(")\r\n")
        f.write("exit /b 0\r\n")
    return SANDBOX


def ticks():
    try:
        with open(os.path.join(SANDBOX, "ticks.txt"), "r", encoding="utf-8") as f:
            return len([x for x in f.read().splitlines() if x.strip()])
    except Exception:
        return 0


def find_window(title_sub):
    """按标题**子串**找窗口。

    ⚠️ 不能用 FindWindowW 精确匹配：提权进程开的控制台，Windows 会在标题
    前面自动加"管理员: "（本测试进程就是提权的，所以真实标题是
    "管理员:  QQ Bridge - ..."）。Chrome 正常跑时不提权，不会有这个前缀 ——
    所以这里必须按子串找，两种情况下都稳。"""
    import ctypes
    from ctypes import wintypes

    u32 = ctypes.windll.user32
    u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u32.IsWindowVisible.argtypes = [wintypes.HWND]
    u32.IsIconic.argtypes = [wintypes.HWND]
    u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    found = []

    def cb(hwnd, lparam):
        n = u32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 2)
        u32.GetWindowTextW(hwnd, buf, n + 2)
        if title_sub in buf.value:
            pid = wintypes.DWORD(0)
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            found.append({
                "hwnd": hwnd,
                "title": buf.value,
                "pid": pid.value,
                "visible": bool(u32.IsWindowVisible(hwnd)),
                "minimized": bool(u32.IsIconic(hwnd)),
            })
        return True

    u32.EnumWindows(WNDENUMPROC(cb), 0)
    return found


def close_stub_windows():
    """把 stub 的控制台窗口关掉（等于按了窗口右上角的 ×）。返回关了几个。

    关控制台窗口会给进程发 CTRL_CLOSE_EVENT，conhost 会顺手把整棵进程树
    收掉 —— 比 taskkill 干净，也不会误伤别的进程。"""
    import ctypes
    n = 0
    for w in find_window(STUB_TITLE):
        try:
            ctypes.windll.user32.PostMessageW(w["hwnd"], 0x0010, 0, 0)  # WM_CLOSE
            print("  已发出关闭指令：%r" % w["title"])
            n += 1
        except Exception as e:
            print("  关闭窗口失败：%s" % e)
    return n


def wait_ticks_frozen(timeout=12.0):
    """等 tick 数字不再增长，说明上一个 stub 真的死透了。
    返回 (是否冻结, 最终计数)。"""
    last = ticks()
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(1.2)
        now = ticks()
        if now == last:
            return True, now
        last = now
    return False, last


# ---------------------------------------------------------------- ④ 窗口

def test_launch_and_window():
    print("\n[4] 拉起桥 & 窗口状态")
    build_sandbox()
    sys.path.insert(0, SANDBOX)
    if "qq_native_host" in sys.modules:
        del sys.modules["qq_native_host"]
    import qq_native_host as h

    check("沙箱宿主读到的是测试端口", h.read_port() == 18999, h.read_port())
    alive, _ = h.probe_bridge(18999, timeout=0.6)
    check("测试端口上确实没有别的东西在跑", not alive)

    pid = h.launch_bridge()
    check("launch_bridge 返回了 pid", isinstance(pid, int) and pid > 0, pid)

    for _ in range(60):
        if ticks() > 0:
            break
        time.sleep(0.25)
    check("批处理真的被执行了（目录含中文）", ticks() > 0, "ticks=%d" % ticks())

    wins = []
    for _ in range(40):
        wins = find_window(STUB_TITLE)
        if wins:
            break
        time.sleep(0.25)

    check("新开的控制台窗口存在", bool(wins), "没找到标题含 %r 的窗口" % STUB_TITLE)
    if wins:
        w = wins[0]
        check("窗口是最小化的（任务栏留图标、不抢焦点）", w["minimized"], w)
        check("窗口可见（不是被藏起来）", w["visible"], w)
        try:
            import ctypes
            check("窗口没有抢到前台焦点",
                  ctypes.windll.user32.GetForegroundWindow() != w["hwnd"])
        except Exception as e:
            check("窗口没有抢到前台焦点", False, e)


# ---------------------------------------------------------------- ⑤ 宿主死后桥还在

def test_survives_host_exit():
    print("\n[5] 宿主进程退出后，桥必须还活着")

    # 先把第 4 步那个 stub 关掉 —— 否则下面"tick 还在涨"可能是它贡献的，
    # 那就等于什么都没测到（这是个很容易自己骗自己的地方）。
    close_stub_windows()
    frozen, base = wait_ticks_frozen()
    check("第 4 步的 stub 已经死透（tick 不再增长）", frozen, "最终 ticks=%d" % base)
    print("  基线 tick 数：%d" % base)

    out, err = run_host([PY, os.path.join(SANDBOX, "qq_native_host.py")],
                        {"cmd": "start"}, timeout=30)
    resp, endpos = read_frame(out)
    check("start 通过沙箱宿主走完了（真的去拉起了 stub）",
          isinstance(resp, dict) and resp.get("ok") is True
          and resp.get("action") == "launching", resp)
    check("沙箱宿主 stdout 依然只有一帧",
          resp is not None and endpos == len(out), "%d / %d" % (endpos, len(out)))

    # 此刻宿主进程已经结束（communicate 返回 = 进程已退出）。
    # 现在唯一还在写 ticks 的来源，只能是"宿主临死前拉起来的那个 stub"。
    time.sleep(5)
    after = ticks()
    print("  宿主退出后 5 秒：ticks %d -> %d" % (base, after))
    check("宿主被回收后，它拉起的桥仍在运行（tick 还在涨）", after > base)
    check("增长量合理（≥3 次，排除计数误差）", after - base >= 3,
          "涨了 %d" % (after - base))

    close_stub_windows()


def cleanup():
    time.sleep(0.8)
    shutil.rmtree(SANDBOX, ignore_errors=True)


if __name__ == "__main__":
    print("=" * 66)
    print(" Native Messaging 宿主自检")
    print("=" * 66)
    try:
        test_static()
        test_browser_lookup()
        test_protocol_direct()
        test_via_cmd()
        test_launch_and_window()
        test_survives_host_exit()
    finally:
        cleanup()

    print("\n" + "=" * 66)
    print(" 通过 %d 项，失败 %d 项" % (len(passed), len(failed)))
    if failed:
        print(" 失败项：")
        for f in failed:
            print("   - " + f)
    print("=" * 66)
    sys.exit(1 if failed else 0)
