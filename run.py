"""启动入口：
  python run.py              默认托盘模式（后台常驻 + 右下角图标 + 独立应用窗口；推荐用 pythonw 无窗口）
  python run.py --console    前台模式：起 uvicorn 并打开独立应用窗口（控制台可见日志）
"""

from __future__ import annotations

import asyncio
import selectors
import sys
import threading
import time
import webbrowser
from pathlib import Path

from uvicorn import Config, Server

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import desktop_app

HOST = "127.0.0.1"
PORT = 8000
URL = f"http://{HOST}:{PORT}"

_MUTEX_NAME = "MultiAgentStudio.SingleInstance"
_mutex_handle = None


def _acquire_single_instance() -> bool:
    """用 Windows 命名互斥体保证只有一个后台实例；已是第一实例返回 True。

    非第一实例会**立即关闭**自己打开的句柄：命名互斥体在最后一个句柄关闭时才销毁，
    若第二实例持有句柄不放，旧实例退出后互斥体仍"存在"，会让后续启动一直被误判为
    「已有实例」，形成"怎么双击都起不来"的级联卡死。
    """
    global _mutex_handle
    if sys.platform != "win32":
        return True
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    last_error = ctypes.get_last_error()
    if not handle:
        return True
    if last_error == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return False
    _mutex_handle = handle
    return True


def _wait_or_become_primary(timeout: float = 25.0) -> bool:
    """已有实例存在时：优先唤出它的窗口；若它已消失则接管成为主实例。

    返回 True = 应作为主实例继续启动；False = 已唤出旧实例或超时放弃。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _focus_existing(1.0):
            return False
        if _acquire_single_instance():
            return True
        time.sleep(0.4)
    return False


def _focus_existing(timeout: float = 1.5) -> bool:
    """请求已在运行的实例把桌面窗口唤到前台；确认是本项目实例才返回 True。"""
    import json
    import urllib.request

    try:
        req = urllib.request.Request(f"{URL}/api/focus", data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("app") == "Multi-Agent Studio"
    except Exception:
        return False


def _loop_factory():
    if sys.platform == "win32":
        return lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
    return None


def _wait_for_server(url: str, timeout: float = 30.0) -> bool:
    """轮询 /api/health 直到服务就绪或超时。"""
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=1.0) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def open_browser_later(url: str, timeout: float = 30.0):
    """等服务就绪后再打开浏览器（桌面窗口不可用时的回退路径）。"""

    def _open():
        _wait_for_server(url, timeout)
        webbrowser.open(url)

    threading.Thread(target=_open, daemon=True).start()


def _serve(host: str, port: int, log_level: str) -> None:
    config = Config(app="app.main:app", host=host, port=port, loop="none", log_level=log_level)
    asyncio.run(Server(config).serve(), loop_factory=_loop_factory())


def main() -> None:
    if _focus_existing(1.0):
        if sys.stdout is not None:
            print("检测到后台实例已在运行，已唤出它的窗口。", flush=True)
        return

    if not _acquire_single_instance():
        if _wait_or_become_primary(25.0):
            if sys.stdout is not None:
                print("后台实例已退出，接管启动。", flush=True)
        else:
            if sys.stdout is not None:
                print("后台实例仍在运行/启动中，已尝试唤出窗口后退出。", flush=True)
            return

    if "--console" not in sys.argv:
        if sys.stdout is not None:
            print("已进入系统托盘模式（右下角图标）。前台调试请用：python run.py --console", flush=True)
        import tray

        tray.main()
        return

    server_thread = threading.Thread(target=_serve, args=(HOST, PORT, "info"), daemon=True, name="uvicorn")
    server_thread.start()
    _wait_for_server(URL)

    if desktop_app.available():
        try:
            desktop_app.create_main_window(URL, hide_on_close=False)
            desktop_app.start(debug=True)
            return
        except Exception as exc:
            print(f"桌面窗口启动失败，回退到默认浏览器：{exc}", flush=True)

    open_browser_later(URL)
    try:
        server_thread.join()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
