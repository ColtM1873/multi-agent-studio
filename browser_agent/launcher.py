"""Launch / reuse a Chromium-based browser with the DevTools protocol enabled.

Key requirements (see ID03):
  * ``--remote-debugging-port=0`` -> real port is written to ``DevToolsActivePort``.
  * ``--user-data-dir`` must point to a NON-default directory (Chrome 136+).
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import requests

from . import config as cfgmod
from . import timing


class BrowserLaunchError(RuntimeError):
    pass


class BrowserLauncher:
    def __init__(self) -> None:
        self.process: Optional[subprocess.Popen] = None
        self.port: Optional[int] = None
        self.ws_url: Optional[str] = None
        self.cdp_url: Optional[str] = None
        self.user_data_dir: Optional[str] = None

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #
    def ensure_browser(self) -> dict:
        """Reuse a live browser if possible, otherwise launch a fresh one."""
        existing = self._reuse_existing()
        if existing:
            return existing
        return self.launch()

    def launch(self) -> dict:
        browser_path, _browser_type = cfgmod.discover_browser()
        user_data_dir = cfgmod.get_user_data_dir()
        self.user_data_dir = user_data_dir

        active_port_file = Path(user_data_dir) / "DevToolsActivePort"
        if active_port_file.exists():
            try:
                active_port_file.unlink()
            except OSError:
                pass

        args = [
            browser_path,
            "--remote-debugging-port=0",
            f"--user-data-dir={user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--disable-features=Translate,BackForwardCache,AcceptCHFrame,MediaRouter",
            "--disable-popup-blocking",
            "--disable-prompt-on-repost",
            "--disable-sync",
            "--metrics-recording-only",
            "--password-store=basic",
            "--use-mock-keychain",
            "--remote-allow-origins=*",
            "about:blank",
        ]

        creationflags = 0
        if sys.platform == "win32":
            creationflags = 0x00000008  # DETACHED_PROCESS (do not block console)

        try:
            self.process = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except OSError as exc:  # pragma: no cover - platform dependent
            raise BrowserLaunchError(f"启动浏览器失败: {exc}") from exc

        port = self._read_devtools_port(
            active_port_file, timeout=timing.get().launch.ready_timeout
        )
        self.port = port
        self.cdp_url = f"http://127.0.0.1:{port}/"
        ws_url = self._wait_until_ready(port, timeout=timing.get().launch.ready_timeout)
        self.ws_url = ws_url

        cfgmod.update_config(
            last_port=port,
            last_cdp_url=self.cdp_url,
            browser_path=browser_path,
        )
        return {"port": port, "cdp_url": self.cdp_url, "ws_url": ws_url}

    def close(self) -> None:
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
            except OSError:
                pass
        self.process = None

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #
    def _reuse_existing(self) -> Optional[dict]:
        if self.port and self.ws_url and self._is_port_ready(self.port):
            return {"port": self.port, "cdp_url": self.cdp_url, "ws_url": self.ws_url}

        cfg = cfgmod.load_config()
        port = cfg.get("last_port")
        if isinstance(port, int) and self._is_port_ready(port):
            try:
                ws_url = self._wait_until_ready(port, timeout=timing.get().launch.reuse_timeout)
            except BrowserLaunchError:
                return None
            self.port = port
            self.cdp_url = f"http://127.0.0.1:{port}/"
            self.ws_url = ws_url
            return {"port": port, "cdp_url": self.cdp_url, "ws_url": ws_url}
        return None

    @staticmethod
    def _read_devtools_port(active_port_file: Path, timeout: float) -> int:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if active_port_file.is_file():
                try:
                    text = active_port_file.read_text(encoding="utf-8").strip()
                except OSError:
                    text = ""
                if text:
                    first_line = text.splitlines()[0].strip()
                    if first_line.isdigit():
                        return int(first_line)
            time.sleep(timing.get().launch.devtools_poll_interval)
        raise BrowserLaunchError(
            "等待 DevToolsActivePort 超时；请确认浏览器已启动且 --user-data-dir 为非默认目录。"
        )

    @staticmethod
    def _is_port_ready(port: int) -> bool:
        try:
            resp = requests.get(
                f"http://127.0.0.1:{port}/json/version",
                timeout=timing.get().launch.port_probe_timeout,
            )
            return resp.status_code == 200
        except requests.RequestException:
            return False

    @staticmethod
    def _wait_until_ready(port: int, timeout: float) -> str:
        deadline = time.time() + timeout
        last_err: Optional[Exception] = None
        while time.time() < deadline:
            try:
                resp = requests.get(
                    f"http://127.0.0.1:{port}/json/version",
                    timeout=timing.get().launch.port_probe_timeout,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    ws_url = data.get("webSocketDebuggerUrl")
                    if ws_url:
                        return ws_url
            except (requests.RequestException, ValueError) as exc:
                last_err = exc
            time.sleep(timing.get().launch.cdp_poll_interval)
        raise BrowserLaunchError(f"等待 CDP 就绪超时: {last_err}")
