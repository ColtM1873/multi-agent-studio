"""Lite 版系统托盘入口（无模型下载菜单）。"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tray as full_tray
import pystray
from uvicorn import Config, Server

HOST = full_tray.HOST
PORT = full_tray.PORT
URL = full_tray.URL


def _run_server() -> None:
    import asyncio
    import selectors

    loop_factory = (
        (lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
        if sys.platform == "win32"
        else None
    )
    config = Config(
        app="lite.app.main_lite:app",
        host=HOST,
        port=PORT,
        loop="none",
        log_level="warning",
    )
    server = Server(config)
    full_tray._server = server
    asyncio.run(server.serve(), loop_factory=loop_factory)


def main() -> None:
    full_tray._redirect_std_streams()

    server_thread = threading.Thread(target=_run_server, daemon=True, name="uvicorn")
    server_thread.start()

    icon = pystray.Icon(
        "multi_agent_studio_lite",
        full_tray._create_image(),
        "Multi-Agent Studio",
        menu=pystray.Menu(
            pystray.MenuItem("打开", full_tray._open, default=True),
            pystray.MenuItem("查看状态", full_tray._status),
            pystray.MenuItem("退出", full_tray._quit),
        ),
    )

    if not full_tray.desktop_app.available():
        full_tray._open_browser_later(URL, 180.0)
        icon.run()
        return

    icon.run_detached()
    if full_tray._stopped.is_set():
        return
    try:
        full_tray.desktop_app.create_boot_window(URL)
        threading.Thread(
            target=full_tray._navigate_when_ready, args=(URL,), daemon=True
        ).start()
        full_tray.desktop_app.start()
    except Exception as exc:
        print(f"[tray-lite] 桌面窗口启动失败，回退到默认浏览器：{exc}", flush=True)
        if not full_tray._stopped.is_set():
            full_tray._open_browser_later(URL, 180.0)
        full_tray._stopped.wait()
    finally:
        try:
            icon.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()
