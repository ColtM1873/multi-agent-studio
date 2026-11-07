"""Windows 原生文件对话框（Win32 通用对话框 `GetOpenFileNameW` / `GetSaveFileNameW`）。

为什么需要它：有些操作必须让用户**在本机资源管理器里浏览并选定路径**，而浏览器里的
`<input type="file">` 拿不到完整路径（安全限制），也无法决定「保存到哪」。用到它的功能：

- 会话页「将本地md转换为HTML」：选一个 .md 文件（`open_file_dialog`）；
- 会话页「导出会话」：决定导出的 zip 存到哪里（`save_file_dialog`）；
- 主界面「导入会话」：找到要导入的会话文件（`open_file_dialog`）。

实现取舍（与既有踩坑文档 ID67 / ID82 对齐）：

- **纯 ctypes 标准库实现**，不引入新依赖；
- 不用 `tkinter.filedialog`：Tk 解释器有「必须在创建线程内销毁」的生命周期约束
  （见 `app/runtime/floating_stop.py` 与 ID67 §三），在 HTTP 线程里反复建/销 Tk
  容易踩 `Tcl_AsyncDelete`；
- 不用 `pywebview` 的 WinForms 对话框：它要求 UI 线程 / STA，而后端端点在
  FastAPI 线程池线程里执行；
- 通用对话框会自建消息循环，**可在任意线程调用**。调用方要么放在同步 `def` 端点里
  （自动落到线程池），要么用 `asyncio.to_thread` 包起来；**不要**直接在 `async def`
  里调用它，否则会阻塞事件循环；
- **必须带 `OFN_NOCHANGEDIR`**：本项目 `configs/` 等大量使用相对路径，绝不能让
  对话框把进程当前工作目录改掉；
- owner 窗口句柄由 `owner_hwnd()` 尽量取（桌面版 pywebview 有窗口），这样对话框
  是该窗口的模态子窗口、不会被压到后面；拿不到（浏览器标签页模式 / 纯后端调试）就
  传 0，对话框仍能正常弹出并返回结果；
- 保存对话框带 `OFN_OVERWRITEPROMPT`：覆盖已有文件由系统弹确认，代码里不静默覆盖。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

# 通用对话框标志（Win32 commdlg.h）
OFN_OVERWRITEPROMPT = 0x00000002
OFN_HIDEREADONLY = 0x00000004
OFN_NOCHANGEDIR = 0x00000008
OFN_PATHMUSTEXIST = 0x00000800
OFN_FILEMUSTEXIST = 0x00001000
OFN_EXPLORER = 0x00080000

# 路径缓冲区长度：Win32 上限 32767 字符 + 结尾 NUL，留一点余量
_MAX_PATH_CHARS = 32768


class _OPENFILENAMEW(ctypes.Structure):
    """`OPENFILENAMEW` 结构体（字段顺序必须与 commdlg.h 完全一致）。"""

    _fields_ = [
        ("lStructSize", wintypes.DWORD),
        ("hwndOwner", wintypes.HWND),
        ("hInstance", wintypes.HINSTANCE),
        ("lpstrFilter", wintypes.LPCWSTR),
        ("lpstrCustomFilter", wintypes.LPWSTR),
        ("nMaxCustFilter", wintypes.DWORD),
        ("nFilterIndex", wintypes.DWORD),
        ("lpstrFile", wintypes.LPWSTR),
        ("nMaxFile", wintypes.DWORD),
        ("lpstrFileTitle", wintypes.LPWSTR),
        ("nMaxFileTitle", wintypes.DWORD),
        ("lpstrInitialDir", wintypes.LPCWSTR),
        ("lpstrTitle", wintypes.LPCWSTR),
        ("Flags", wintypes.DWORD),
        ("nFileOffset", wintypes.WORD),
        ("nFileExtension", wintypes.WORD),
        ("lpstrDefExt", wintypes.LPCWSTR),
        ("lCustData", wintypes.LPARAM),
        ("lpfnHook", ctypes.c_void_p),
        ("lpTemplateName", wintypes.LPCWSTR),
        ("pvReserved", ctypes.c_void_p),
        ("dwReserved", wintypes.DWORD),
        ("FlagsEx", wintypes.DWORD),
    ]


def supported() -> bool:
    """当前系统是否支持弹出该对话框（仅 Windows）。"""
    return sys.platform == "win32"


def owner_hwnd() -> int:
    """尽量拿到桌面版（pywebview / WebView2）主窗口句柄，让对话框成为它的模态子窗口。

    拿不到（浏览器标签页模式 / 纯后端调试 / 窗口已销毁）时返回 0，对话框仍能正常
    弹出并返回结果，只是可能被压到其它窗口后面。
    """
    try:
        import ctypes

        import desktop_app

        window = getattr(desktop_app, "_window", None)
        native = getattr(window, "native", None)
        if native is not None:
            hwnd = int(native.Handle.ToInt64())
            # 窗口可能已销毁/隐藏到托盘；只有仍有效的句柄才交给通用对话框
            if hwnd and ctypes.windll.user32.IsWindow(hwnd):
                return hwnd
    except Exception:  # noqa: BLE001 - 浏览器标签页模式 / 窗口尚未创建
        pass
    return 0


def _commdlg():
    """加载 comdlg32 并返回 (模块句柄, 扩展错误函数)。"""
    comdlg32 = ctypes.WinDLL("comdlg32", use_last_error=True)
    comm_dlg_extended_error = comdlg32.CommDlgExtendedError
    comm_dlg_extended_error.argtypes = []
    comm_dlg_extended_error.restype = wintypes.DWORD
    return comdlg32, comm_dlg_extended_error


def open_file_dialog(
    *,
    title: str = "",
    initial_dir: str = "",
    owner: int = 0,
    filters: str = "",
) -> str | None:
    """弹系统「打开文件」对话框，返回用户选定的**绝对路径**；取消则返回 None。

    filters 用 Win32 的口径书写：每段以 ``\\0`` 分隔（说明文字、通配符、说明文字、
    通配符…），整体以双 ``\\0`` 结尾；为空则只提供 Markdown + 所有文件两项。
    """
    if not supported():
        raise RuntimeError("当前系统不是 Windows，无法打开系统文件选择窗口")

    comdlg32, comm_dlg_extended_error = _commdlg()
    get_open_file_name = comdlg32.GetOpenFileNameW
    get_open_file_name.argtypes = [ctypes.POINTER(_OPENFILENAMEW)]
    get_open_file_name.restype = wintypes.BOOL

    file_buffer = ctypes.create_unicode_buffer(_MAX_PATH_CHARS)
    if not filters:
        filters = (
            "Markdown 文件 (*.md;*.markdown)\0*.md;*.markdown\0"
            "所有文件 (*.*)\0*.*\0\0"
        )

    ofn = _OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(_OPENFILENAMEW)
    ofn.hwndOwner = owner or None
    ofn.lpstrFilter = filters
    ofn.nFilterIndex = 1
    ofn.lpstrFile = ctypes.cast(file_buffer, wintypes.LPWSTR)
    ofn.nMaxFile = _MAX_PATH_CHARS
    ofn.lpstrInitialDir = initial_dir or None
    ofn.lpstrTitle = title or None
    ofn.Flags = (
        OFN_EXPLORER
        | OFN_FILEMUSTEXIST
        | OFN_PATHMUSTEXIST
        | OFN_NOCHANGEDIR
        | OFN_HIDEREADONLY
    )

    if not get_open_file_name(ctypes.byref(ofn)):
        err = comm_dlg_extended_error()
        if err:
            raise OSError(f"系统文件选择窗口出错（CommDlgExtendedError=0x{err:04X}）")
        return None

    selected = file_buffer.value
    return selected or None


def save_file_dialog(
    *,
    title: str = "",
    initial_dir: str = "",
    owner: int = 0,
    filters: str = "",
    default_name: str = "",
    default_ext: str = "",
) -> str | None:
    """弹系统「保存文件」对话框（资源管理器风格），返回用户选定的**绝对路径**。

    与 `open_file_dialog` 相同的实现取舍（纯 ctypes、可在任意线程调用、
    必须带 ``OFN_NOCHANGEDIR``）。区别：

    - 用 `GetSaveFileNameW`，文件**可以不存在**（本来就是要新建的），因此不设
      ``OFN_FILEMUSTEXIST``；
    - 带 ``OFN_OVERWRITEPROMPT``：用户选到已存在的文件时由系统弹「是否替换」，
      绝不在代码里静默覆盖；
    - ``default_name`` 为预填文件名（可含中文），``default_ext`` 为缺省扩展名
      （用户没写扩展名时由系统补上）。

    取消返回 None；对话框本身出错抛 OSError。
    """
    if not supported():
        raise RuntimeError("当前系统不是 Windows，无法打开系统文件保存窗口")

    comdlg32, comm_dlg_extended_error = _commdlg()
    get_save_file_name = comdlg32.GetSaveFileNameW
    get_save_file_name.argtypes = [ctypes.POINTER(_OPENFILENAMEW)]
    get_save_file_name.restype = wintypes.BOOL

    file_buffer = ctypes.create_unicode_buffer(_MAX_PATH_CHARS)
    if default_name:
        # 预填文件名：缓冲区是可变宽字符数组，直接写入即可
        file_buffer.value = default_name[: _MAX_PATH_CHARS - 1]

    ofn = _OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(_OPENFILENAMEW)
    ofn.hwndOwner = owner or None
    ofn.lpstrFilter = filters or "所有文件 (*.*)\0*.*\0\0"
    ofn.nFilterIndex = 1
    ofn.lpstrFile = ctypes.cast(file_buffer, wintypes.LPWSTR)
    ofn.nMaxFile = _MAX_PATH_CHARS
    ofn.lpstrInitialDir = initial_dir or None
    ofn.lpstrTitle = title or None
    ofn.lpstrDefExt = (default_ext or "").lstrip(".") or None
    ofn.Flags = (
        OFN_EXPLORER
        | OFN_OVERWRITEPROMPT
        | OFN_PATHMUSTEXIST
        | OFN_NOCHANGEDIR
        | OFN_HIDEREADONLY
    )

    if not get_save_file_name(ctypes.byref(ofn)):
        err = comm_dlg_extended_error()
        if err:
            raise OSError(f"系统文件保存窗口出错（CommDlgExtendedError=0x{err:04X}）")
        return None

    selected = file_buffer.value
    return selected or None

