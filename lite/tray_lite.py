"""Lite 版系统托盘入口（无模型下载菜单）。"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tray as full_tray
import runtime_port
import pystray
from uvicorn import Config, Server

HOST = full_tray.HOST
PORT = full_tray.PORT
URL = full_tray.URL


def _run_server(host: str, port: int) -> None:
    import asyncio
    import selectors

    loop_factory = (
        (lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
        if sys.platform == "win32"
        else None
    )
    config = Config(
        app="lite.app.main_lite:app",
        host=host,
        port=port,
        loop="none",
        log_level="warning",
    )
    server = Server(config)
    full_tray._server = server
    try:
        asyncio.run(server.serve(), loop_factory=loop_factory)
    except BaseException as exc:  # 端口被占用时 uvicorn 会 sys.exit(1)，不能静默
        full_tray._server_error.set()
        print(f"[tray-lite] 后端启动失败（{host}:{port}）：{exc}", flush=True)
    finally:
        if not getattr(server, "started", False):
            full_tray._server_error.set()


def main(port: int | None = None) -> None:
    global PORT, URL

    full_tray._redirect_std_streams()

    # 端口冲突检测：8000 被占用时依次尝试 8001、8002……（含刚退出的 TIME_WAIT）。
    if port is None:
        port = runtime_port.pick_port()
    if port is None:
        full_tray._show_message("Multi-Agent Studio Lite", full_tray._port_unavailable_message())
        return
    if port != runtime_port.DEFAULT_PORT:
        print(f"[tray-lite] 端口 {runtime_port.DEFAULT_PORT} 被占用，已改用 {port}。", flush=True)
    PORT = port
    URL = f"http://{HOST}:{PORT}"
    full_tray.PORT = port
    full_tray.URL = URL

    server_thread = threading.Thread(target=_run_server, args=(HOST, PORT), daemon=True, name="uvicorn")
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
