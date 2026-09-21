# -*- coding: utf-8 -*-
r"""QQ 转发助手 · 桥的托盘图标（纯 ctypes，零依赖）  v1.0.6

为什么要有这个：桥是个控制台程序，窗口只能"关掉"（关掉 = 桥死）。
木木的诉求是**平时不占地方、不会手滑关掉、只有主动在托盘点才真退**。

⚠️ 先说清楚一件做不到的事：**"按 X 变成最小化到托盘"实现不了。**
   控制台窗口不属于我们这个进程，它属于 `conhost.exe`；
   Windows 明确禁止跨进程安装窗口过程（实测 `SetWindowLongPtr(GWLP_WNDPROC)`
   返回 0、`GetLastError()=5` ERROR_ACCESS_DENIED），也没法取消关闭。
   所以走的是等价路线：**把窗口藏起来 + 把标题栏上的 X 摘掉**，
   于是"只能在托盘右键退出"这个效果成立了。

⚠️ **铁律：只有托盘真的建起来了，才允许藏窗口、才允许摘 X。**
   否则万一托盘图标没出现（资源管理器异常、Explorer 重启……），
   用户就再也找不到任何退出入口了 —— 那比"手滑关掉"糟糕得多。
   所以 `start()` 返回 False 时，调用方必须老老实实把窗口留在任务栏。

对外只有三个函数：
    available()          -> bool        这个平台能不能用
    Tray(...).start()    -> bool        建托盘（成功后才藏窗口 / 摘 X）
    Tray(...).cleanup()                 退出前摘图标、还回窗口样式
"""
import ctypes
import ctypes.wintypes as wt
import os
import threading

u32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32
shell32 = ctypes.windll.shell32

# ---------------------------------------------------------------- 常量
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_NULL = 0x0000
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_APP = 0x8000
WM_TRAY = WM_APP + 1          # 托盘回调消息

GWL_STYLE = -16
WS_SYSMENU = 0x00080000       # 标题栏上的 X / 系统菜单 / 最小化按钮
SW_HIDE = 0
SW_SHOW = 5
SW_RESTORE = 9

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x1, 0x2, 0x4

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
IDI_APPLICATION = 32512
SM_CXSMICON = 49

TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x0002, 0x0100
MF_STRING, MF_SEPARATOR = 0x0000, 0x0800

CMD_SHOW, CMD_HIDE, CMD_LOGS, CMD_QUIT = 1001, 1002, 1003, 1004
CMD_NAMES = {CMD_SHOW: "show", CMD_HIDE: "hide",
             CMD_LOGS: "logs", CMD_QUIT: "quit"}


# ---------------------------------------------------------------- 结构体
class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.UINT), ("style", wt.UINT),
        ("lpfnWndProc", ctypes.c_void_p), ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE),
        ("hIcon", wt.HICON), ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR), ("hIconSm", wt.HICON),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT),
        ("uFlags", wt.UINT), ("uCallbackMessage", wt.UINT),
        ("hIcon", wt.HICON), ("szTip", wt.WCHAR * 128),
        ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD),
        ("szInfo", wt.WCHAR * 256), ("uVersion", wt.UINT),
        ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
        ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wt.HICON),
    ]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wt.HWND, ctypes.c_uint,
                             ctypes.c_ulonglong, ctypes.c_longlong)

# ---------------------------------------------------------------- 声明
# ⚠️ 全部显式声明 argtypes/restype。不声明的话有两个坑：
#   ① 64 位下指针/句柄被当 32 位 int → 静默截断
#   ② 窗口过程里调 DefWindowProcW 会因 LPARAM 溢出抛 ArgumentError，
#      而 ctypes 会把这个异常**静默吞掉、只打到 stderr** —— 看起来像"什么都没发生"
u32.DefWindowProcW.restype = ctypes.c_longlong
u32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
u32.RegisterClassExW.restype = wt.ATOM
u32.RegisterClassExW.argtypes = [ctypes.c_void_p]
u32.CreateWindowExW.restype = wt.HWND
u32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, wt.HWND, wt.HMENU,
                                wt.HINSTANCE, ctypes.c_void_p]
u32.DestroyWindow.argtypes = [wt.HWND]
u32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
u32.IsWindowVisible.argtypes = [wt.HWND]
u32.IsWindowVisible.restype = wt.BOOL
u32.SetForegroundWindow.argtypes = [wt.HWND]
u32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
u32.PostQuitMessage.argtypes = [ctypes.c_int]
u32.GetSystemMetrics.argtypes = [ctypes.c_int]
u32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
u32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
u32.GetMessageW.restype = ctypes.c_int
u32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
u32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
u32.DispatchMessageW.restype = ctypes.c_longlong
u32.RegisterWindowMessageW.restype = wt.UINT
u32.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
u32.SetWindowLongPtrW.restype = ctypes.c_longlong
u32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_longlong]
u32.GetWindowLongPtrW.restype = ctypes.c_longlong
u32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
u32.LoadImageW.restype = wt.HANDLE
u32.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int,
                           ctypes.c_int, wt.UINT]
u32.LoadIconW.restype = wt.HANDLE
u32.LoadIconW.argtypes = [wt.HINSTANCE, wt.LPCWSTR]
u32.CreatePopupMenu.restype = wt.HMENU
u32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
u32.TrackPopupMenu.restype = ctypes.c_int
u32.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, wt.HWND, ctypes.c_void_p]

# ⚠️ GetConsoleWindow 在 **kernel32**，不在 user32（想当然放 user32 会 AttributeError）
k32.GetConsoleWindow.restype = wt.HWND
k32.GetModuleHandleW.restype = wt.HINSTANCE
k32.GetModuleHandleW.argtypes = [wt.LPCWSTR]

shell32.Shell_NotifyIconW.restype = wt.BOOL
shell32.Shell_NotifyIconW.argtypes = [wt.DWORD,
                                      ctypes.POINTER(NOTIFYICONDATAW)]


def available():
    """这个平台能不能用。"""
    return os.name == "nt" and hasattr(ctypes, "windll")


# ---------------------------------------------------------------- 控制台窗口
def console_hwnd():
    try:
        return k32.GetConsoleWindow() or None
    except Exception:
        return None


def show_console():
    h = console_hwnd()
    if not h:
        return False
    u32.ShowWindow(h, SW_SHOW)
    u32.ShowWindow(h, SW_RESTORE)
    try:
        u32.SetForegroundWindow(h)
    except Exception:
        pass
    return True


def hide_console():
    h = console_hwnd()
    if not h:
        return False
    u32.ShowWindow(h, SW_HIDE)
    return not u32.IsWindowVisible(h)


def strip_close_button():
    """摘掉标题栏上的 X（系统菜单、最小化按钮一并去掉）。

    这是跨进程操作 —— 但只改**样式**、不装窗口过程，所以 Windows 放行
    （实测 `GWL_STYLE` 生效；`GWLP_WNDPROC` 会被 ERROR_ACCESS_DENIED 拒掉）。

    返回原来的样式（退出时用 `restore_style` 还回去）；没控制台就返回 None。
    """
    h = console_hwnd()
    if not h:
        return None
    try:
        style = u32.GetWindowLongPtrW(h, GWL_STYLE)
        if not (style & WS_SYSMENU):
            return style          # 已经摘过了
        u32.SetWindowLongPtrW(h, GWL_STYLE, style & ~WS_SYSMENU)
        return style
    except Exception:
        return None


def restore_style(style):
    h = console_hwnd()
    if not h or style is None:
        return
    try:
        u32.SetWindowLongPtrW(h, GWL_STYLE, style)
    except Exception:
        pass


# ---------------------------------------------------------------- 托盘本体
class Tray:
    """托盘图标 + 右键菜单。

    `on_command(name)` 的 name ∈ {'show','hide','logs','quit'}；
    `on_message(text)` 用来把"非致命但值得知道"的事（比如 Explorer 重启后
    图标重新挂上了）交给调用方记日志。
    """

    def __init__(self, tooltip, icon_path=None, on_command=None,
                 on_message=None):
        self.tooltip = (tooltip or "")[:127]
        self.icon_path = icon_path
        self.on_command = on_command
        self.on_message = on_message

        self._hwnd = None
        self._hicon = None
        self._menu = None
        self._wndproc = None       # ⚠️ 必须留引用，被 GC 掉 = 进程崩
        self._thread = None
        self._ready = threading.Event()
        self._ok = False
        self._taskbar_created = 0
        self._old_style = None
        self._stripped = False

    # -------------------------------------------------- 对外
    def start(self, timeout=5.0):
        """起托盘。只有返回 True 才代表图标真的挂上了。"""
        if not available():
            return False
        self._thread = threading.Thread(target=self._run, name="qq-tray",
                                        daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        return self._ok

    def stop(self):
        if self._hwnd:
            try:
                u32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
            except Exception:
                pass

    def cleanup(self):
        """退出前擦干净：摘图标、还回窗口样式、把窗口叫回来。"""
        self._restore_ui()

    def _restore_ui(self):
        """把"被托盘接管"的痕迹全部撤销：摘图标 + 还原窗口样式 + 显示窗口。

        ⚠️ 为什么 WM_CLOSE 那条路也要调它：万一托盘消息循环死了（外面有人
        发 WM_CLOSE、或者出异常），图标就没了；这时如果窗口还藏着、X 还摘着，
        用户就又没有任何出口了。所以"托盘一消失，窗口就必须回来"。
        """
        self._remove()
        if self._stripped:
            restore_style(self._old_style)
            self._stripped = False
        show_console()

    # -------------------------------------------------- 内部
    def _note(self, text):
        if self.on_message:
            try:
                self.on_message(text)
            except Exception:
                pass

    def _icon(self):
        if self.icon_path and os.path.exists(self.icon_path):
            size = u32.GetSystemMetrics(SM_CXSMICON) or 16
            h = u32.LoadImageW(None, self.icon_path, IMAGE_ICON, size, size,
                               LR_LOADFROMFILE)
            if h:
                return h
            self._note("托盘图标 LoadImage 失败，改用系统默认图标: %s"
                       % self.icon_path)
        return u32.LoadIconW(None, ctypes.c_wchar_p(IDI_APPLICATION))

    def _nid(self):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self._hicon
        nid.szTip = self.tooltip
        return nid

    def _add(self):
        """加图标。先 DELETE 再 ADD —— Explorer 重启后会留一个"幽灵图标"，
        直接 ADD 会失败。"""
        nid = self._nid()
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        if shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            return True
        return bool(shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid)))

    def _remove(self):
        if not self._hwnd:
            return
        try:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid()))
        except Exception:
            pass

    def _build_menu(self):
        if not self._menu:
            m = u32.CreatePopupMenu()
            u32.AppendMenuW(m, MF_STRING, CMD_SHOW, "显示日志窗口")
            u32.AppendMenuW(m, MF_STRING, CMD_HIDE, "隐藏窗口（缩回托盘）")
            u32.AppendMenuW(m, MF_SEPARATOR, 0, None)
            u32.AppendMenuW(m, MF_STRING, CMD_LOGS, "打开日志文件夹")
            u32.AppendMenuW(m, MF_SEPARATOR, 0, None)
            u32.AppendMenuW(m, MF_STRING, CMD_QUIT, "退出（关掉桥）")
            self._menu = m
        return self._menu

    def _popup(self):
        # ⚠️ 弹菜单前必须 SetForegroundWindow，否则点菜单外面它不会消失
        try:
            u32.SetForegroundWindow(self._hwnd)
        except Exception:
            pass
        pt = wt.POINT()
        u32.GetCursorPos(ctypes.byref(pt))
        cmd = u32.TrackPopupMenu(self._build_menu(),
                                 TPM_RIGHTBUTTON | TPM_RETURNCMD,
                                 pt.x, pt.y, 0, self._hwnd, None)
        try:
            u32.PostMessageW(self._hwnd, WM_NULL, 0, 0)
        except Exception:
            pass

        name = CMD_NAMES.get(cmd)
        if name and self.on_command:
            try:
                self.on_command(name)
            except Exception as e:
                self._note("托盘命令 %s 处理出错: %r" % (name, e))

    def _on_message(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAY:
                if lparam == WM_RBUTTONUP:
                    self._popup()
                elif lparam in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    if self.on_command:
                        self.on_command("show")
                return 0
            if self._taskbar_created and msg == self._taskbar_created:
                # 资源管理器重启了，图标被清掉 —— 重新挂一个
                if self._add():
                    self._note("资源管理器重启过，托盘图标已重新挂上")
                else:
                    self._note("资源管理器重启后，托盘图标没能重新挂上")
                return 0
            if msg == WM_CLOSE:
                self._restore_ui()
                u32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                u32.PostQuitMessage(0)
                return 0
        except Exception as e:
            self._note("托盘消息处理出错: %r" % e)
        return u32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _run(self):
        try:
            self._taskbar_created = u32.RegisterWindowMessageW("TaskbarCreated")
            hinst = k32.GetModuleHandleW(None)
            cls = "QQBridgeTrayWnd_%d" % os.getpid()
            self._wndproc = WNDPROC(self._on_message)

            wc = WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
            wc.lpfnWndProc = ctypes.cast(self._wndproc, ctypes.c_void_p)
            wc.hInstance = hinst
            wc.lpszClassName = cls
            if not u32.RegisterClassExW(ctypes.byref(wc)):
                self._note("RegisterClassExW 失败，托盘没起来")
                return

            self._hwnd = u32.CreateWindowExW(0, cls, "QQBridgeTray", 0,
                                             0, 0, 0, 0, None, None, hinst, None)
            if not self._hwnd:
                self._note("CreateWindowExW 失败，托盘没起来")
                return

            self._hicon = self._icon()
            if not self._add():
                self._note("Shell_NotifyIcon(NIM_ADD) 失败，托盘没起来")
                # 窗口已经建出来了，但没人跑消息循环了 —— 赶紧销毁，
                # 免得留下一个"没有窗口过程"的窗口（收到消息会崩）
                u32.DestroyWindow(self._hwnd)
                self._hwnd = None
                return

            self._ok = True
            self._ready.set()      # ← 到这一步才算"托盘真的好了"

            # 铁律：只有到这里才允许藏窗口 / 摘 X（详见文件头）
            self._old_style = strip_close_button()
            self._stripped = self._old_style is not None
            hide_console()

            msg = wt.MSG()
            while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                u32.TranslateMessage(ctypes.byref(msg))
                u32.DispatchMessageW(ctypes.byref(msg))
        except Exception as e:
            self._note("托盘线程异常: %r" % e)
        finally:
            self._ready.set()      # 任何失败路径都要放行 start()，免得它干等
