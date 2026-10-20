"""自测 Windows 文件选择对话框的 Win32 调用（弹出后 2 秒自动取消）。

验证点：
1. `OPENFILENAMEW` 结构体/参数正确，对话框能弹出（无崩溃、无 CommDlgExtendedError）；
2. 用户取消时返回 None（而不是异常或空字符串）；
3. 不会改变进程当前工作目录（OFN_NOCHANGEDIR）。

会短暂弹出一个标题为“__DSH_DIALOG_SELFTEST__”的对话框，脚本会自己把它关掉。
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import win_file_dialog  # noqa: E402

TITLE = "__DSH_DIALOG_SELFTEST__"
result: dict[str, object] = {}
cwd_before = Path.cwd()


def worker() -> None:
    try:
        result["value"] = win_file_dialog.open_file_dialog(title=TITLE)
        result["cwd_same"] = Path.cwd() == cwd_before
    except Exception as e:  # noqa: BLE001
        result["error"] = repr(e)


user32 = ctypes.WinDLL("user32", use_last_error=True)
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL

t = threading.Thread(target=worker, daemon=True)
t.start()

closed = False
deadline = time.time() + 8.0
while time.time() < deadline and not closed:
    hwnds: list[int] = []

    def _enum(hwnd, _lparam):
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value != "#32770":
            return True
        text = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, text, 512)
        if TITLE in text.value:
            hwnds.append(hwnd)
        return True

    user32.EnumWindows(EnumWindowsProc(_enum), 0)
    if hwnds:
        time.sleep(1.0)  # 让对话框完成显示，模拟“用户看到后取消”
        for h in hwnds:
            user32.PostMessageW(h, 0x0010, 0, 0)  # WM_CLOSE
        closed = True
    else:
        time.sleep(0.1)

t.join(timeout=8.0)
print("dialog_found:", closed)
print("thread_alive:", t.is_alive())
print("result:", result)
assert closed, "没有找到弹出的对话框窗口"
assert not t.is_alive(), "对话框没有在 WM_CLOSE 后结束"
assert "error" not in result, result
assert result.get("value") is None, f"取消后应返回 None，实际 {result.get('value')!r}"
assert result.get("cwd_same") is True, "对话框改变了进程当前工作目录"
print("DIALOG SELFTEST OK")
