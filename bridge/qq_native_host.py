# -*- coding: utf-8 -*-
r"""
QQ 转发助手 · Native Messaging 宿主  v1.0.5

这个文件只有一个职责：让浏览器扩展能"喊一声"就把本地桥拉起来。

为什么需要它
------------
Chrome 扩展**不能**运行本地程序 —— 没有这种 API。官方给的唯一通道叫
Native Messaging：扩展先跟一个"宿主程序"说话，由宿主去启动别的东西。
所以链路是：

    扩展（点「启动本地桥」）
      → chrome.runtime.sendNativeMessage('com.mumu.qq_bridge', {cmd:'start'})
        → Chrome 按注册表找到 qq_host.bat 并把它拉起来
          → 它执行本文件
            → 本文件先看 18761 端口在不在
              → 不在才拉起 start_bridge.bat（最小化到任务栏）

协议（Chrome 官方规定，不能改）
------------------------------
· 输入输出都走 stdin / stdout
· 每条消息 = 4 字节**小端**长度 + 该长度的 UTF-8 JSON
· 单条上限 1 MB（这里只传"启动 / 在跑吗"，远用不到）
· stdout 上除了消息帧**一个字节都不能多写** —— 多一个字符整个协议就乱掉。
  所以本文件全程只往 stderr 和 log/native_host.log 写东西。

自己登记自己
------------
本文件还带一个 --register：写好宿主清单 json，并在注册表里登记。登记的
位置见下面 REG_SUBKEYS（Thorium / Google\Chrome / Chromium 三处都写 ——
每个浏览器只看自己那一处，多写无害，写漏一个就是"点了没反应"）。
桥每次启动都会自己调一次，正常情况下你一次都不用管。

复核"这台浏览器到底看哪几处"：python tools\probe-native-roots.py

手动重新登记：双击 bridge\\重新登记.bat

命令行自检：
    python qq_native_host.py --register   登记（并打印结果）
    python qq_native_host.py --status     看看登记成什么样了
"""

import json
import os
import struct
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))

HOST_NAME = "com.mumu.qq_bridge"

# 扩展 ID。宿主清单里的 allowed_origins 必须写死它 —— 这是 Chrome 的
# 硬性要求，写错了浏览器会回 "Access to the specified native messaging
# host is forbidden."（扩展不会因此报错，只是永远连不上宿主）。
EXT_ID = "onpgmgnpdgkhebogdflhbojdchcbegcg"

HOST_BAT = os.path.join(BASE, "qq_host.bat")
MANIFEST_PATH = os.path.join(BASE, HOST_NAME + ".json")
LAUNCH_BAT = os.path.join(BASE, "start_bridge.bat")
CONFIG_PATH = os.path.join(BASE, "config.json")
LOG_DIR = os.path.join(BASE, "log")
HOST_LOG = os.path.join(LOG_DIR, "native_host.log")

VERSION = "1.0.5"
DEFAULT_PORT = 18761
MAX_MSG = 1024 * 1024

# Windows 常量。SW_SHOWMINNOACTIVE = 7：新开的控制台**最小化到任务栏**
# 且不抢焦点 —— 就是木木选的"任务栏留图标"那种。
SW_SHOWMINNOACTIVE = 7
CREATE_NEW_CONSOLE = 0x00000010

# Chrome 系浏览器在注册表哪个位置找宿主。**必须按实际浏览器写对** ——
# 写错位置的后果很隐蔽：浏览器只会回一句英文错误，扩展那边什么都不报，
# 看起来就是"点了按钮没反应"。
#
# 这三条不是猜的，是核实过的：
#   · Software\Thorium —— 木木用的就是这个浏览器（Chromium 138 分支）。
#     直接拿它的 chrome.dll 全文搜 "NativeMessagingHosts"，搜出来**只有
#     两条**路径字面量：SOFTWARE\Thorium\NativeMessagingHosts 和
#     SOFTWARE\Google\Chrome\NativeMessagingHosts。也就是说它**不看**
#     Software\Chromium —— 一开始只写了 Chromium + Google\Chrome，
#     能跑通纯属"Google\Chrome 兜底"这条运气。
#     复核脚本：python tools\probe-native-roots.py
#   · Software\Google\Chrome —— Thorium 的兜底项，也是真正 Chrome 的唯一位置。
#   · Software\Chromium —— 品牌为 Chromium 的构建看这个（将来换浏览器不会失联）。
REG_SUBKEYS = [
    "Software\\Thorium\\NativeMessagingHosts\\" + HOST_NAME,
    "Software\\Google\\Chrome\\NativeMessagingHosts\\" + HOST_NAME,
    "Software\\Chromium\\NativeMessagingHosts\\" + HOST_NAME,
]


# ---------------------------------------------------------------- 日志

def log(msg):
    """记一行日志。

    ⚠️ 绝对不能写 stdout —— 那是给 Chrome 的消息通道。stderr 可以写
    （Chrome 会把宿主的 stderr 收进扩展的日志里，正好方便排查）。"""
    line = "%s [host] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(HOST_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
    try:
        sys.stderr.write(line)
        sys.stderr.flush()
    except Exception:
        pass


# ---------------------------------------------------------------- 端口 / 桥

def read_port():
    """从 config.json 读端口。读不到就用默认值 —— 不能因为配置坏了
    就让整个"启动桥"功能失灵。"""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return int(json.load(f).get("port") or DEFAULT_PORT)
    except Exception:
        return DEFAULT_PORT


def probe_bridge(port, timeout=2.0):
    """问一句 /health：桥在不在。

    刻意发一条真的 HTTP 请求，而不是只 connect 一下 socket —— 后者会让
    aiohttp 在日志里留下一堆"连接被对端关闭"的噪音。顺便还能确认对面
    确实是我们的桥（响应里有 qq-bridge 这个词）。
    """
    import urllib.request
    url = "http://127.0.0.1:%d/health" % port
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read(4096).decode("utf-8", "replace")
        return ("qq-bridge" in body), body[:200]
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)


# ---------------------------------------------------------------- 拉起桥

def launch_bridge(bat_path=None):
    """把 start_bridge.bat 拉起来（最小化窗口），返回新进程 pid。

    为什么用 cmd.exe 包一层：CreateProcess 不认 .bat（它只认 exe）。
    `/d /s /c "…"` 这套写法的 `/s` 是必须的 —— 没有它，cmd 会按自己的
    规则去啃引号，路径里一旦有空格就会出错。

    为什么要显式给 startupinfo 的 wShowWindow：这样新开的控制台窗口
    直接以"最小化、不抢焦点"的状态出现，而不是先弹出来再缩回去。
    """
    bat = bat_path or LAUNCH_BAT
    comspec = os.environ.get("COMSPEC") or os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe"
    )
    cmdline = '"%s" /d /s /c ""%s""' % (comspec, bat)

    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = SW_SHOWMINNOACTIVE

    p = subprocess.Popen(
        cmdline,
        cwd=os.path.dirname(os.path.abspath(bat)),
        creationflags=CREATE_NEW_CONSOLE,
        startupinfo=si,
        close_fds=True,
    )
    return p.pid


# ---------------------------------------------------------------- 登记

def host_manifest_obj():
    return {
        "name": HOST_NAME,
        "description": "QQ Bridge launcher (one-click-to-qq chrome extension)",
        "path": HOST_BAT,
        "type": "stdio",
        "allowed_origins": ["chrome-extension://%s/" % EXT_ID],
    }


def write_manifest():
    """把宿主清单写到 bridge\\ 里。桥知道自己住在哪，所以路径一定是准的。"""
    obj = host_manifest_obj()
    tmp = MANIFEST_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, MANIFEST_PATH)
    return MANIFEST_PATH


def register_host(logger=None):
    """登记宿主。返回 (ok_paths, failed) —— 两边都是列表，便于逐条报告。

    只动 HKCU（当前用户），不碰 HKLM —— 不需要管理员权限，卸载时也只
    要删掉自己的那两项，不会影响别的程序。
    """
    say = logger or log
    mf = write_manifest()
    say("宿主清单已写入: %s" % mf)

    ok, failed = [], []
    try:
        import winreg
    except Exception as e:                                       # 非 Windows
        return [], [(k, "winreg 不可用: %s" % e) for k in REG_SUBKEYS]

    for sub in REG_SUBKEYS:
        try:
            key = winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER, sub, 0, winreg.KEY_WRITE
            )
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, mf)
            winreg.CloseKey(key)
            ok.append(sub)
            say("注册表已登记: HKCU\\%s" % sub)
        except Exception as e:
            failed.append((sub, str(e)))
            say("注册表登记失败: HKCU\\%s -> %s" % (sub, e))

    return ok, failed


def read_registered():
    """看一眼现在登记成什么样了（--status 用）。"""
    out = []
    try:
        import winreg
    except Exception:
        return [("<winreg 不可用>", "")]
    for sub in REG_SUBKEYS:
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub, 0, winreg.KEY_READ)
            val, _ = winreg.QueryValueEx(key, "")
            winreg.CloseKey(key)
            out.append((sub, val))
        except FileNotFoundError:
            out.append((sub, None))
        except Exception as e:
            out.append((sub, "读取失败: %s" % e))
    return out


# ---------------------------------------------------------------- 消息处理

def handle(msg):
    cmd = str((msg or {}).get("cmd") or "").strip().lower()
    port = read_port()

    if cmd in ("ping", "hello"):
        return {"ok": True, "action": "pong", "port": port}

    if cmd == "register":
        ok, failed = register_host()
        return {
            "ok": bool(ok),
            "action": "registered",
            "registered": ok,
            "failed": [{"key": k, "error": m} for k, m in failed],
        }

    if cmd == "status":
        return {"ok": True, "action": "status", "registry": read_registered(),
                "manifest": MANIFEST_PATH,
                "manifest_exists": os.path.isfile(MANIFEST_PATH),
                "host_bat_exists": os.path.isfile(HOST_BAT),
                "launch_bat_exists": os.path.isfile(LAUNCH_BAT),
                "port": port}

    if cmd != "start":
        return {"ok": False, "reason": "unknown-cmd",
                "message": "不认识的指令：%s" % (cmd or "(空)")}

    # ---- start ----
    # 幂等：已经在跑就直接回话，绝不重复拉起第二个桥（第二个会撞端口，
    # 表现成一堆看不懂的报错）。
    alive, detail = probe_bridge(port)
    if alive:
        log("收到 start：桥已经在跑（端口 %d），不重复启动" % port)
        return {"ok": True, "action": "already-running", "port": port}

    if not os.path.isfile(LAUNCH_BAT):
        log("收到 start：找不到 %s" % LAUNCH_BAT)
        return {
            "ok": False, "reason": "missing-bat",
            "message": "找不到启动脚本：%s" % LAUNCH_BAT,
        }

    try:
        pid = launch_bridge()
    except Exception as e:
        log("拉起桥失败: %s" % e)
        return {"ok": False, "reason": "launch-failed",
                "message": "启动桥时出错：%s" % e}

    log("收到 start：已拉起 start_bridge.bat（pid=%s）" % pid)

    # 稍等一两秒，尽量在回复里就把"到底起来没有"说准。等不到也不报错 ——
    # 桥连机器人要几秒，扩展那边还会自己接着轮询。
    listening = False
    deadline = time.time() + 2.0
    while time.time() < deadline:
        time.sleep(0.25)
        listening, _ = probe_bridge(port, timeout=0.8)
        if listening:
            break

    return {
        "ok": True,
        "action": "launching",
        "pid": pid,
        "port": port,
        "listening": listening,
    }


# ---------------------------------------------------------------- 协议读写

def _binary_stdio():
    """把 stdin/stdout 切到二进制模式。

    Python 在 Windows 上默认会做 CRLF 转换；虽然 sys.stdin.buffer 本身
    不过 TextIOWrapper，但显式设一次最保险 —— 这条通道上一个多余字节都
    不能有。拿不到 fileno（比如被重定向成 StringIO）就跳过。
    """
    try:
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    except Exception:
        pass


def _read_exact(fp, n):
    buf = b""
    while len(buf) < n:
        chunk = fp.read(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def serve():
    """主循环：读一帧 -> 处理 -> 回一帧，直到 stdin 关闭。"""
    _binary_stdio()
    inp = getattr(sys.stdin, "buffer", sys.stdin)
    out = getattr(sys.stdout, "buffer", sys.stdout)

    log("宿主启动 v%s（pid=%s）" % (VERSION, os.getpid()))

    while True:
        head = _read_exact(inp, 4)
        if not head:
            log("stdin 已关闭，宿主退出")
            return
        n = struct.unpack("<I", head)[0]
        if n <= 0 or n > MAX_MSG:
            log("消息长度非法（%d），退出" % n)
            return
        body = _read_exact(inp, n)
        if body is None:
            log("消息体读不全，退出")
            return

        try:
            msg = json.loads(body.decode("utf-8"))
        except Exception as e:
            log("消息不是合法 JSON: %s" % e)
            resp = {"ok": False, "reason": "bad-json", "message": str(e)}
        else:
            try:
                resp = handle(msg)
            except Exception as e:
                log("处理消息出错: %s" % e)
                resp = {"ok": False, "reason": "host-error", "message": str(e)}

        resp["host"] = HOST_NAME
        resp["version"] = VERSION
        payload = json.dumps(resp, ensure_ascii=False).encode("utf-8")
        out.write(struct.pack("<I", len(payload)) + payload)
        out.flush()


# ---------------------------------------------------------------- 命令行

def cli(argv):
    """--register / --status。这两个是给「重新登记.bat」和你手动排查用的，
    此时 Chrome 不在对话里，所以可以正常往屏幕上打人话。"""
    if len(argv) < 2:
        return None

    if argv[1] == "--register":
        print("正在登记 Native Messaging 宿主…")
        print("  宿主清单: %s" % MANIFEST_PATH)
        print("  宿主程序: %s" % HOST_BAT)
        ok, failed = register_host(logger=lambda m: None)
        for k in ok:
            print("  [OK]   HKCU\\%s" % k)
        for k, m in failed:
            print("  [失败] HKCU\\%s  ->  %s" % (k, m))
        print("")
        if ok:
            print("登记完成。回到浏览器，重开插件弹窗再点「启动本地桥」试试。")
        else:
            print("登记没成功，把上面的 [失败] 那几行发给我。")
            return 1
        return 0

    if argv[1] == "--status":
        print("宿主清单: %s" % MANIFEST_PATH)
        print("  存在: %s" % os.path.isfile(MANIFEST_PATH))
        print("宿主程序: %s" % HOST_BAT)
        print("  存在: %s" % os.path.isfile(HOST_BAT))
        print("启动脚本: %s" % LAUNCH_BAT)
        print("  存在: %s" % os.path.isfile(LAUNCH_BAT))
        print("端口: %d  ->  %s" % (read_port(), "在跑" if probe_bridge(read_port())[0] else "没在跑"))
        print("注册表:")
        for k, v in read_registered():
            print("  HKCU\\%s" % k)
            print("      = %s" % ("(未登记)" if v is None else v))
        return 0

    print("用法: python qq_native_host.py [--register|--status]")
    return 2


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(cli(sys.argv) or 0)
    try:
        serve()
    except KeyboardInterrupt:
        pass
    except Exception as e:                                       # 兜底
        log("宿主异常退出: %s" % e)
        sys.exit(1)
