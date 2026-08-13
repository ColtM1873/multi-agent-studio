"""启动入口：
  python run.py              默认托盘模式（后台常驻 + 右下角图标；推荐用 pythonw 无窗口）
  python run.py --console    前台模式：起 uvicorn 并自动打开浏览器（控制台可见日志）
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


def _loop_factory():
    if sys.platform == "win32":
        return lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
    return None


def open_browser_later(url: str, delay: float = 1.5):
    def _open():
        time.sleep(delay)
        webbrowser.open(url)

    threading.Thread(target=_open, daemon=True).start()


def main() -> None:
    if "--console" not in sys.argv:
        if sys.stdout is not None:
            print("已进入系统托盘模式（右下角图标）。前台调试请用：python run.py --console", flush=True)
        import tray

        tray.main()
        return

    host = "127.0.0.1"
    port = 8000
    url = f"http://{host}:{port}"
    open_browser_later(url)

    config = Config(app="app.main:app", host=host, port=port, loop="none", log_level="info")
    server = Server(config)
    asyncio.run(server.serve(), loop_factory=_loop_factory())


if __name__ == "__main__":
    main()
