"""Windows 原生「打开文件」对话框（Win32 通用对话框 `GetOpenFileNameW`）。

为什么需要它：会话页的「将本地md转换为HTML」按钮要让用户**在本机资源管理器里浏览并
选定一个 .md 文件**。浏览器里的 `<input type="file">` 拿不到完整路径（安全限制），
也就无法把结果写回「所选 md 文件所在目录」，所以改由后端直接调系统对话框，拿真实
绝对路径。

实现取舍（与既有踩坑文档 ID67 对齐）：

- **纯 ctypes 标准库实现**，不引入新依赖；
- 不用 `tkinter.filedialog`：Tk 解释器有「必须在创建线程内销毁」的生命周期约束
  （见 `app/runtime/floating_stop.py` 与 ID67 §三），在 HTTP 线程里反复建/销 Tk
  容易踩 `Tcl_AsyncDelete`；
- 不用 `pywebview` 的 WinForms 对话框：它要求 UI 线程 / STA，而后端端点在
  FastAPI 线程池线程里执行；
- 通用对话框会自建消息循环，可在任意线程调用；调用方务必放在 FastAPI 的**同步
  `def` 端点**里（自动落到线程池），不要放到 `async def` 里阻塞事件循环；
- **必须带 `OFN_NOCHANGEDIR`**：本项目 `configs/` 等大量使用相对路径，绝不能让
  对话框把进程当前工作目录改掉；
- `owner_hwnd` 由调用方尽量传入应用窗口句柄（桌面版 pywebview 有窗口），这样对话框
  是该窗口的模态子窗口、不会被压到后面；拿不到（浏览器标签页模式 / 纯后端调试）就
  传 0，对话框仍能正常弹出并返回结果。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

# 通用对话框标志（Win32 commdlg.h）
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


def open_file_dialog(
    *,
    title: str = "",
    initial_dir: str = "",
    owner_hwnd: int = 0,
    filters: str = "",
) -> str | None:
    """弹系统「打开文件」对话框，返回用户选定的**绝对路径**；取消则返回 None。

    filters 用 Win32 的口径书写：每段以 ``\\0`` 分隔（说明文字、通配符、说明文字、
    通配符…），整体以双 ``\\0`` 结尾；为空则只提供 Markdown + 所有文件两项。
    """
    if not supported():
        raise RuntimeError("当前系统不是 Windows，无法打开系统文件选择窗口")

    comdlg32 = ctypes.WinDLL("comdlg32", use_last_error=True)
    get_open_file_name = comdlg32.GetOpenFileNameW
    get_open_file_name.argtypes = [ctypes.POINTER(_OPENFILENAMEW)]
    get_open_file_name.restype = wintypes.BOOL
    comm_dlg_extended_error = comdlg32.CommDlgExtendedError
    comm_dlg_extended_error.argtypes = []
    comm_dlg_extended_error.restype = wintypes.DWORD

    file_buffer = ctypes.create_unicode_buffer(_MAX_PATH_CHARS)
    if not filters:
        filters = (
            "Markdown 文件 (*.md;*.markdown)\0*.md;*.markdown\0"
            "所有文件 (*.*)\0*.*\0\0"
        )

    ofn = _OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(_OPENFILENAMEW)
    ofn.hwndOwner = owner_hwnd or None
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
