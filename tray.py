"""系统托盘入口：后台常驻 + Windows 右下角托盘图标。

启动（无控制台）： pythonw tray.py
或（有控制台调试）： python tray.py

主线程跑桌面应用窗口（Edge WebView2 / pywebview 要求主线程 GUI 循环），
pystray 以 detached 方式跑在后台线程，uvicorn 也跑在后台 daemon 线程。
若 WebView2 / pywebview 不可用，则回退为「托盘 + 默认浏览器标签页」的旧模式。
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import selectors
import sys
import threading
import time
import webbrowser
from pathlib import Path

from PIL import Image, ImageDraw
import pystray
from uvicorn import Config, Server

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import desktop_app
import runtime_port

HOST = runtime_port.HOST
PORT = runtime_port.DEFAULT_PORT
URL = f"http://{HOST}:{PORT}"

_server: Server | None = None
_server_error = threading.Event()
_stopped = threading.Event()


def _redirect_std_streams() -> None:
    """pythonw 下 stdout/stderr 为 None，uvicorn 写日志会崩溃，重定向到日志文件。"""
    if sys.stdout is None or sys.stderr is None:
        log_dir = ROOT / "logs"
        log_dir.mkdir(exist_ok=True)
        logfile = open(log_dir / "server.log", "a", encoding="utf-8")
        if sys.stdout is None:
            sys.stdout = logfile
        if sys.stderr is None:
            sys.stderr = logfile


def _create_image() -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, 62, 62), radius=14, fill=(79, 70, 229, 255))
    d.ellipse((20, 20, 44, 44), fill=(255, 255, 255, 255))
    d.ellipse((26, 26, 38, 38), fill=(124, 58, 237, 255))
    return img


def _run_server(host: str, port: int) -> None:
    global _server
    loop_factory = (lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())) if sys.platform == "win32" else None
    config = Config(app="app.main:app", host=host, port=port, loop="none", log_level="warning")
    _server = Server(config)
    try:
        asyncio.run(_server.serve(), loop_factory=loop_factory)
    except BaseException as exc:  # 端口被占用时 uvicorn 会 sys.exit(1)，不能让主线程当没事
        _server_error.set()
        print(f"[tray] 后端启动失败（{host}:{port}）：{exc}", flush=True)
    finally:
        if not getattr(_server, "started", False):
            _server_error.set()


def _open(_icon, _item) -> None:
    # 桌面窗口（含启动页）还在 → 直接唤到前台即可，用户会看到「正在启动」而不是报错。
    if desktop_app.available() and desktop_app.show_window():
        return
    # 浏览器回退路径：等服务就绪再打开，避免「无法访问」。
    _open_browser_later(URL, 180.0)


def _status(_icon, _item) -> None:
    def _worker() -> None:
        def _show(text: str, flags: int) -> None:
            try:
                ctypes.windll.user32.MessageBoxW(0, text, "Multi-Agent Studio", flags)
            except Exception:
                webbrowser.open(f"{URL}/api/health")

        if _wait_for_server(URL, 1.2):
            _show("程序已就绪，可以正常使用。\n\n界面地址：" + URL, 0x40)
            return
        # 还没就绪：区分「仍在启动」与「真的没响应」，别把报错直接甩给用户。
        still_starting = _server is None or not getattr(_server, "started", False)
        if still_starting:
            _show(
                "程序正在启动中，请再稍等片刻。\n\n"
                "首次启动、或电脑配置较低时会久一些（可能需要 1～2 分钟）。\n"
                "启动完成后会自动打开界面，请不要关闭程序。",
                0x40,
            )
        else:
            _show(
                "程序暂时没有响应，但它仍在运行。\n\n"
                "请稍等一会儿再试；若长时间无响应，可在托盘菜单选择「退出」后重新启动程序。",
                0x30,
            )

    threading.Thread(target=_worker, daemon=True).start()


def _quit(icon, _item) -> None:
    if _server is not None:
        _server.should_exit = True
    # 先主动销毁桌面级「停止/继续」悬浮球：否则浏览器未关闭时它会残留，
    # 且 Tk 守护线程在解释器关闭阶段响应点击会报 "Python is shutting down"。
    try:
        from app.runtime.floating_stop import FloatingStop

        FloatingStop.instance().shutdown()
    except Exception:
        pass
    try:
        desktop_app.quit()
    except Exception:
        pass
    _stopped.set()
    icon.stop()


def _fmt_size(n) -> str:
    if not n or n <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024
        i += 1
    return f"{n:.1f} {units[i]}"


def _update_download_menu(icon, item) -> None:
    """每秒更新托盘菜单里的下载进度项。"""
    from app.services.downloads import download_manager

    while True:
        try:
            cur = download_manager.current()
            if cur and cur["status"] == "preparing":
                item.text = "下载模型：准备中…"
            elif cur and cur["status"] == "downloading":
                item.text = (
                    f"下载模型：{_fmt_size(cur['downloaded'])} / {_fmt_size(cur['total'])}"
                    f" · {_fmt_size(cur['speed'])}/s"
                )
            elif download_manager.has_error():
                item.text = "下载模型：上次下载失败，可在设置里重试"
            else:
                item.text = "下载模型：就绪"
            icon.update_menu()
        except Exception:
            pass
        time.sleep(1)


def _open_browser_later(url: str, timeout: float = 180.0) -> None:
    """等服务就绪后再打开浏览器（避免过早打开导致「无法访问」）。"""

    def _open() -> None:
        _wait_for_server(url, timeout)
        if _stopped.is_set():
            return
        webbrowser.open(url)

    threading.Thread(target=_open, daemon=True).start()


def _health_once(url: str, timeout: float = 1.0) -> bool:
    """单次探测 /api/health（供就绪轮询 + 后端线程死亡检测共用）。"""
    import urllib.request

    try:
        with urllib.request.urlopen(f"{url}/api/health", timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def _boot_error(message: str) -> None:
    """把「后端没起来」变成启动页上的可读提示，而不是无限读秒。"""
    try:
        desktop_app.show_boot_error(message)
    except Exception:
        pass
    print(message, flush=True)


def _navigate_when_ready(url: str, timeout: float = 300.0) -> None:
    """等后端就绪后，把桌面窗口从「启动页」导航到应用界面。

    窗口刚创建、GUI 循环可能尚未就绪，故失败时短暂重试；后端线程已经死掉或
    一直起不来时把启动页换成可读错误，避免「无限读秒」。
    """
    deadline = time.time() + timeout
    while time.time() < deadline and not _stopped.is_set():
        if _health_once(url):
            for _ in range(30):
                if _stopped.is_set():
                    return
                if desktop_app.navigate(url):
                    return
                time.sleep(0.5)
            _boot_error(
                "后端已经就绪，但桌面窗口没能加载界面。\n"
                "请从托盘退出程序后重新启动；若反复出现，请查看 logs/server.log。"
            )
            return
        if _server_error.is_set():
            _boot_error(
                "后端服务没能启动：端口可能被占用，或数据库服务启动失败。\n"
                "请从托盘退出程序，稍等几秒后重新启动；若反复出现，请查看 logs/server.log。"
            )
            return
        time.sleep(0.5)
    if not _stopped.is_set():
        _boot_error(
            f"后端在 {int(timeout)} 秒内没有就绪。\n"
            "请从托盘退出程序，稍等几秒后重新启动；若反复出现，请查看 logs/server.log。"
        )


def _wait_for_server(url: str, timeout: float = 30.0) -> bool:
    """轮询 /api/health 直到服务就绪、超时或用户已退出。"""
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline and not _stopped.is_set():
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=1.0) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _port_unavailable_message() -> str:
    last = runtime_port.DEFAULT_PORT + runtime_port.PORT_TRIES - 1
    return (
        f"端口 {runtime_port.DEFAULT_PORT}-{last} 均被占用，无法启动。\n\n"
        "请关闭占用这些端口的程序后重试（例如另一个副本、调试服务或其它软件）。"
    )


def _show_message(title: str, message: str, flags: int = 0x30) -> None:
    """托盘模式（pythonw）没有控制台，用系统弹窗把启动失败告诉用户。"""
    try:
        ctypes.windll.user32.MessageBoxW(0, message, title, flags)
    except Exception:
        print(message, flush=True)


def main(port: int | None = None) -> None:
    global PORT, URL

    _redirect_std_streams()

    # 端口冲突检测：8000 被占用时依次尝试 8001、8002……（包括刚退出的 TIME_WAIT）。
    if port is None:
        port = runtime_port.pick_port()
    if port is None:
        _show_message("Multi-Agent Studio", _port_unavailable_message())
        return
    if port != runtime_port.DEFAULT_PORT:
        print(f"[tray] 端口 {runtime_port.DEFAULT_PORT} 被占用，已改用 {port}。", flush=True)
    PORT = port
    URL = f"http://{HOST}:{PORT}"

    server_thread = threading.Thread(target=_run_server, args=(HOST, PORT), daemon=True, name="uvicorn")
    server_thread.start()

    download_item = pystray.MenuItem("下载模型：就绪", None, enabled=False)
    icon = pystray.Icon(
        "multi_agent_studio",
        _create_image(),
        "Multi-Agent Studio",
        menu=pystray.Menu(
            pystray.MenuItem("打开", _open, default=True),
            pystray.MenuItem("查看状态", _status),
            download_item,
            pystray.MenuItem("退出", _quit),
        ),
    )

    threading.Thread(target=_update_download_menu, args=(icon, download_item), daemon=True).start()

    if not desktop_app.available():
        _open_browser_later(URL, 180.0)
        icon.run()
        return

    icon.run_detached()
    if _stopped.is_set():
        return
    try:
        # 先开「启动页」，就绪后再导航到应用界面：低配机器上后端启动慢也不会
        # 出现「无法访问 / 连接错误」这种吓人的画面。
        desktop_app.create_boot_window(URL)
        threading.Thread(target=_navigate_when_ready, args=(URL,), daemon=True).start()
        desktop_app.start()
    except Exception as exc:
        print(f"[tray] 桌面窗口启动失败，回退到默认浏览器：{exc}", flush=True)
        if not _stopped.is_set():
            _open_browser_later(URL, 180.0)
        _stopped.wait()
    finally:
        try:
            icon.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()
