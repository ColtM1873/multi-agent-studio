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
import runtime_port

HOST = runtime_port.HOST
PORT = runtime_port.DEFAULT_PORT
URL = f"http://{HOST}:{PORT}"
_APP_TAG = runtime_port.APP_TAG   # Lite 版会覆盖成 "Multi-Agent Studio Lite"

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


def _stale_instance_detected(
    hits: dict[int, int],
    *,
    start: int | None = None,
    tries: int | None = None,
) -> bool:
    """互斥体被占时，判断旧实例是否「后端就绪、窗口却唤不出」（残留/卡死）。

    - `/api/focus` 明确返回 app 匹配但 `ok=False`：窗口已坏 → 连续 2 次判定残留；
    - `/api/focus` 返回 `ok=True` 但窗口 URL 还停在启动页（navigate 失败）：
      同样是残废实例 → 连续 2 次判定残留；
    - `/api/focus` 超时（GUI 线程卡死）：连续 3 次判定残留（避免把「刚就绪、
      窗口还没创建」的毫秒级窗口误判成残留）。

    判据只在「该端口 /api/health 已经就绪」时才看：uvicorn 是先跑 lifespan、
    再 bind 端口，所以 health 通了就说明它已经完整启动过，不存在「还在启动中」。
    """
    ports = runtime_port.ready_ports(
        start=runtime_port.DEFAULT_PORT if start is None else start,
        tries=runtime_port.PORT_TRIES if tries is None else tries,
    )
    for port in ports:
        state, shown, url = runtime_port.probe_focus(port, app_tag=_APP_TAG)
        if state == "app":
            # 窗口唤不出，或能唤出但还停在启动页（navigate 失败）→ 残废实例
            if not (shown and runtime_port.is_app_url(url)):
                hits[port] = hits.get(port, 0) + 1
                if hits[port] >= 2:
                    return True
        elif state == "unreachable":
            hits[port] = hits.get(port, 0) + 1
            if hits[port] >= 3:
                return True
    return False


def _wait_or_become_primary(timeout: float = 25.0) -> bool:
    """已有实例存在时：优先唤出它的窗口；若它已消失或已「残废」则接管。

    返回 True = 应作为主实例继续启动；False = 已唤出旧实例或超时放弃。
    """
    deadline = time.time() + timeout
    stale_hits: dict[int, int] = {}
    while time.time() < deadline:
        if _focus_existing(1.0):
            return False
        if _acquire_single_instance():
            return True
        # 旧实例后端还活着、但窗口已经唤不出来（残留/卡死）：不再空等，
        # 接管为新实例；端口选择会自动跳过它占用的端口（8000 → 8001）。
        if _stale_instance_detected(stale_hits):
            if sys.stdout is not None:
                print("检测到旧实例后端仍在运行但窗口无法唤出，将改用新端口启动。", flush=True)
            return True
        time.sleep(0.4)
    # 超时仍唤不出：若候选端口上根本没有「已就绪的后端」（旧实例的 uvicorn 可能
    # 已经死掉，只剩托盘/窗口线程），就接管启动；真有后端却唤不出时上面已经按
    # 「残留」接管了，这里不抢，避免双实例。
    if not runtime_port.ready_ports():
        if sys.stdout is not None:
            print("旧实例没有可用的后端，接管启动。", flush=True)
        return True
    return False


def _focus_existing(timeout: float = 3.0) -> bool:
    """请求已在运行的实例把桌面窗口唤到前台（扫描 8000-8019）。

    只有窗口**真的被唤出**（`/api/focus` 返回 ok=True）才算数：端口可能因冲突
    自动递增过，且有可能是「后端还活着、窗口已坏」的残留进程。
    """
    return runtime_port.find_existing(deadline=timeout, app_tag=_APP_TAG) is not None


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


def open_browser_later(url: str, timeout: float = 180.0):
    """等服务就绪后再打开浏览器（桌面窗口不可用时的回退路径）。

    低配机器上后端可能启动较慢，这里把等待时间放宽，避免过早打开导致「无法访问」。
    """

    def _open():
        _wait_for_server(url, timeout)
        webbrowser.open(url)

    threading.Thread(target=_open, daemon=True).start()


def _boot_error(message: str) -> None:
    """把「后端没起来」变成启动页上的可读提示，而不是无限读秒。"""
    try:
        desktop_app.show_boot_error(message)
    except Exception:
        pass
    if sys.stdout is not None:
        print(message, flush=True)


def _navigate_when_ready(url: str, timeout: float = 300.0) -> None:
    """等后端就绪后，把桌面窗口从「启动页」导航到应用界面。

    窗口刚创建、GUI 循环可能尚未就绪，故失败时短暂重试；后端一直起不来时把
    启动页换成可读的错误提示，避免「无限读秒」。
    """
    if not _wait_for_server(url, timeout):
        _boot_error(
            f"后端在 {int(timeout)} 秒内没有就绪。\n"
            "可能是端口被占用、或数据库服务启动失败。\n"
            "请从托盘退出程序，稍等几秒后重新启动；若反复出现，请查看 logs/server.log。"
        )
        return
    for _ in range(30):
        if desktop_app.navigate(url):
            return
        time.sleep(0.5)
    _boot_error(
        "后端已经就绪，但桌面窗口没能加载界面。\n"
        "请从托盘退出程序后重新启动；若反复出现，请查看 logs/server.log。"
    )


def _serve(host: str, port: int, log_level: str) -> None:
    config = Config(app="app.main:app", host=host, port=port, loop="none", log_level=log_level)
    asyncio.run(Server(config).serve(), loop_factory=_loop_factory())


def main() -> None:
    global PORT, URL

    if _focus_existing(3.0):
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

    # 端口冲突检测：8000 被占用时依次尝试 8001、8002……（含刚退出的 TIME_WAIT）。
    port = runtime_port.pick_port()
    if port is None:
        last = runtime_port.DEFAULT_PORT + runtime_port.PORT_TRIES - 1
        message = (
            f"端口 {runtime_port.DEFAULT_PORT}-{last} 均被占用，无法启动。\n"
            "请关闭占用这些端口的程序后重试。"
        )
        if sys.stdout is not None:
            print(message, flush=True)
        return
    if port != runtime_port.DEFAULT_PORT and sys.stdout is not None:
        print(f"端口 {runtime_port.DEFAULT_PORT} 被占用，已改用 {port}。", flush=True)
    PORT = port
    URL = f"http://{HOST}:{PORT}"

    server_thread = threading.Thread(target=_serve, args=(HOST, PORT, "info"), daemon=True, name="uvicorn")
    server_thread.start()

    if desktop_app.available():
        try:
            # 先开「启动页」，就绪后再导航到应用界面：低配机器上后端启动慢也不会
            # 出现「无法访问 / 连接错误」这种吓人的画面。
            desktop_app.create_boot_window(URL, hide_on_close=False)
            threading.Thread(target=_navigate_when_ready, args=(URL,), daemon=True).start()
            desktop_app.start(debug=True)
            return
        except Exception as exc:
            print(f"桌面窗口启动失败，回退到默认浏览器：{exc}", flush=True)

    open_browser_later(URL, 180.0)
    try:
        server_thread.join()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
