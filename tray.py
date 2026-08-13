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


def main() -> None:
    _redirect_std_streams()

    server_thread = threading.Thread(target=_run_server, daemon=True, name="uvicorn")
    server_thread.start()

    icon = pystray.Icon(
        "multi_agent_studio",
        _create_image(),
        "Multi-Agent Studio",
        menu=pystray.Menu(
            pystray.MenuItem("打开", _open, default=True),
            pystray.MenuItem("查看状态", _status),
            pystray.MenuItem("退出", _quit),
        ),
    )
    icon.run()


if __name__ == "__main__":
    main()
