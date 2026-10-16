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

HOST = "127.0.0.1"
PORT = 8000
URL = f"http://{HOST}:{PORT}"

_server: Server | None = None
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


def _run_server() -> None:
    global _server
    loop_factory = (lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())) if sys.platform == "win32" else None
    config = Config(app="app.main:app", host=HOST, port=PORT, loop="none", log_level="warning")
    _server = Server(config)
    asyncio.run(_server.serve(), loop_factory=loop_factory)


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


def _navigate_when_ready(url: str, timeout: float = 300.0) -> None:
    """等后端就绪后，把桌面窗口从「启动页」导航到应用界面。

    窗口刚创建、GUI 循环可能尚未就绪，故失败时短暂重试。
    """
    if not _wait_for_server(url, timeout):
        return
    for _ in range(30):
        if _stopped.is_set():
            return
        if desktop_app.navigate(url):
            return
        time.sleep(0.5)


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


def main() -> None:
    _redirect_std_streams()

    server_thread = threading.Thread(target=_run_server, daemon=True, name="uvicorn")
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
