"""Launch / reuse a Chromium-based browser with the DevTools protocol enabled.

Key requirements (see ID03):
  * ``--remote-debugging-port=0`` -> real port is written to ``DevToolsActivePort``.
  * ``--user-data-dir`` must point to a NON-default directory (Chrome 136+).

ID91：传入 ``profile_dir`` 时为该会话独立启动；只读写这个 profile 的
``DevToolsActivePort``，不碰全局 ``last_port``。
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import requests

from . import config as cfgmod
from . import studio_marker
from . import timing


class BrowserLaunchError(RuntimeError):
    pass


class BrowserLauncher:
    def __init__(
        self, profile_dir: Optional[str] = None, persist_global: bool = True
    ) -> None:
        self.process: Optional[subprocess.Popen] = None
        self.port: Optional[int] = None
        self.ws_url: Optional[str] = None
        self.cdp_url: Optional[str] = None
        self.user_data_dir: Optional[str] = None
        # ID91：按会话隔离浏览器时，每个实例有自己的 profile / 端口；
        # 不读写全局 config.json 的 last_port（那是单实例兼容路径）。
        self.profile_dir = str(profile_dir) if profile_dir else None
        self.persist_global = bool(persist_global)

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
        browser_path, browser_type = cfgmod.discover_browser()
        user_data_dir = self.profile_dir or cfgmod.get_user_data_dir()
        Path(user_data_dir).mkdir(parents=True, exist_ok=True)
        self.user_data_dir = user_data_dir

        studio_marker.apply_profile_theme(user_data_dir, browser_type)

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
            # 窗口被其它窗口完全遮挡时 Chrome 的 Windows 原生遮挡检测会把页面标成
            # ``document.hidden``，rAF 随之冻结：组件库的浮层入场过渡会永久停在首帧
            # （opacity 0 / 0×0），导致「可点击下拉」点不开、候选抓不到（BID072 实测）。
            # 关掉遮挡后台化 + 遮挡计算，使页面在仅被遮挡时仍保持可见/可渲染。
            "--disable-backgrounding-occluded-windows",  # BID072
            "--disable-features=Translate,BackForwardCache,AcceptCHFrame,MediaRouter,"  # BID072
            "CalculateNativeWinOcclusion",  # BID072
            "--disable-popup-blocking",
            "--disable-prompt-on-repost",
            "--disable-sync",
            "--metrics-recording-only",
            "--password-store=basic",
            "--use-mock-keychain",
            "--remote-allow-origins=*",
            "about:blank",
        ]

        if (browser_type or "").strip().lower() == "edge":
            args[1:1] = studio_marker.edge_extension_args(user_data_dir)

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

        if self.persist_global:
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

        if self.profile_dir:
            port = self._port_from_profile()
        else:
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

    def _port_from_profile(self) -> Optional[int]:
        """从该 profile 的 ``DevToolsActivePort`` 读出端口；不依赖全局 last_port。"""
        if not self.profile_dir:
            return None
        path = Path(self.profile_dir) / "DevToolsActivePort"
        try:
            if not path.is_file():
                return None
            first = path.read_text(encoding="utf-8").splitlines()[0].strip()
            return int(first) if first.isdigit() else None
        except Exception:  # noqa: BLE001
            return None

    def current_port(self) -> Optional[int]:
        """返回该 launcher 当前可用端口（不启动浏览器）。"""
        if self.port and self._is_port_ready(self.port):
            return self.port
        port = self._port_from_profile() if self.profile_dir else None
        if isinstance(port, int) and self._is_port_ready(port):
            self.port = port
            self.cdp_url = f"http://127.0.0.1:{port}/"
            return port
        if not self.profile_dir:
            cfg = cfgmod.load_config()
            port = cfg.get("last_port")
            if isinstance(port, int) and self._is_port_ready(port):
                self.port = port
                self.cdp_url = f"http://127.0.0.1:{port}/"
                return port
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
