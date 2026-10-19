"""Lite 版启动入口。"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import run as full_run
import desktop_app
from uvicorn import Config, Server

HOST = full_run.HOST
PORT = full_run.PORT
URL = full_run.URL
_MUTEX_NAME = "MultiAgentStudio.Lite.SingleInstance"

full_run._MUTEX_NAME = _MUTEX_NAME


def _focus_existing(timeout: float = 1.5) -> bool:
    import json
    import urllib.request

    try:
        req = urllib.request.Request(f"{URL}/api/focus", data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("app") == "Multi-Agent Studio Lite"
    except Exception:
        return False


full_run._focus_existing = _focus_existing


def _serve(host: str, port: int, log_level: str) -> None:
    config = Config(
        app="lite.app.main_lite:app",
        host=host,
        port=port,
        loop="none",
        log_level=log_level,
    )
    import asyncio

    asyncio.run(Server(config).serve(), loop_factory=full_run._loop_factory())


def main() -> None:
    if _focus_existing(1.0):
        if sys.stdout is not None:
            print("检测到 Lite 后台实例已在运行，已唤出它的窗口。", flush=True)
        return

    if not full_run._acquire_single_instance():
        if full_run._wait_or_become_primary(25.0):
            if sys.stdout is not None:
                print("Lite 后台实例已退出，接管启动。", flush=True)
        else:
            if sys.stdout is not None:
                print("Lite 后台实例仍在运行/启动中，已尝试唤出窗口后退出。", flush=True)
            return

    if "--console" not in sys.argv:
        if sys.stdout is not None:
            print(
                "已进入 Lite 系统托盘模式（右下角图标）。"
                "前台调试请用：python lite/run_lite.py --console",
                flush=True,
            )
        import lite.tray_lite as tray_lite

        tray_lite.main()
        return

    server_thread = threading.Thread(
        target=_serve, args=(HOST, PORT, "info"), daemon=True, name="uvicorn"
    )
    server_thread.start()

    if desktop_app.available():
        try:
            desktop_app.create_boot_window(URL, hide_on_close=False)
            threading.Thread(
                target=full_run._navigate_when_ready, args=(URL,), daemon=True
            ).start()
            desktop_app.start(debug=True)
            return
        except Exception as exc:
            print(f"桌面窗口启动失败，回退到默认浏览器：{exc}", flush=True)

    full_run.open_browser_later(URL, 180.0)
    try:
        server_thread.join()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
