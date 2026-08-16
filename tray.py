"""系统托盘入口：后台常驻 + Windows 右下角托盘图标。

启动（无控制台）： pythonw tray.py
或（有控制台调试）： python tray.py

主线程跑 pystray（Windows 需主线程消息循环），uvicorn 跑在后台 daemon 线程。
"""

from __future__ import annotations

import asyncio
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

HOST = "127.0.0.1"
PORT = 8000
URL = f"http://{HOST}:{PORT}"

_server: Server | None = None


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
    webbrowser.open(URL)


def _status(_icon, _item) -> None:
    webbrowser.open(f"{URL}/api/health")


def _quit(icon, _item) -> None:
    if _server is not None:
        _server.should_exit = True
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
            else:
                item.text = "下载模型：就绪"
            icon.update_menu()
        except Exception:
            pass
        time.sleep(1)


def _open_browser_later(url: str, timeout: float = 30.0) -> None:
    """等服务就绪后再打开浏览器（避免过早打开导致「无法访问」）。"""

    def _open() -> None:
        _wait_for_server(url, timeout)
        webbrowser.open(url)

    threading.Thread(target=_open, daemon=True).start()


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


def main() -> None:
    _redirect_std_streams()

    server_thread = threading.Thread(target=_run_server, daemon=True, name="uvicorn")
    server_thread.start()

    _open_browser_later(URL, 30.0)

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
    icon.run()


if __name__ == "__main__":
    main()
