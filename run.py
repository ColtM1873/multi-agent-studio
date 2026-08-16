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
    """等服务就绪后再打开浏览器（避免过早打开导致「无法访问」）。"""

    def _open():
        _wait_for_server(url, timeout)
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
