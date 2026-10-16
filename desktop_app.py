"""桌面应用窗口：用 Edge WebView2（pywebview）承载前端界面。

把原先「浏览器里多一个标签页」的前台，换成真正独立的应用窗口：
拥有自己的标题、任务栏图标/槽位与 Alt+Tab 切换项，不再依附浏览器。

约定：
- 本模块只负责「窗口」，不碰 uvicorn / 托盘。WebView2 的 GUI 主循环必须跑在
  主线程（pywebview 强制），因此调用方要保证在主线程调用 `start()`。
- 托盘（pystray）与 uvicorn 各自跑在后台线程。
- 窗口关闭默认「隐藏到托盘」，由托盘负责恢复；只有 `quit()` 才真正关闭。
"""

from __future__ import annotations

import ctypes
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ICON = ROOT / "icon.ico"
PROFILE_DIR = ROOT / "webview_profile"
APP_TITLE = "Multi-Agent Studio"
APP_ID = "Anomalyco.MultiAgentStudio"

_WEBVIEW2_KEY = r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"

# 启动页：在真正的界面（本地服务）就绪前先显示它，避免低配机器上直接呈现
# 「无法访问 / 连接错误」。纯内联 HTML，不依赖网络；就绪后由 navigate() 切到应用。
BOOT_HTML = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Multi-Agent Studio</title>
<style>
  html,body{height:100%;margin:0}
  body{background:#0b0d12;color:#e8eaf0;font-family:system-ui,"Segoe UI","Microsoft YaHei",sans-serif;
       display:flex;align-items:center;justify-content:center}
  .boot{max-width:560px;padding:32px;text-align:center}
  .spin{width:46px;height:46px;margin:0 auto 24px;border-radius:50%;
        border:4px solid rgba(255,255,255,.15);border-top-color:#7c4dff;animation:sp 1s linear infinite}
  @keyframes sp{to{transform:rotate(360deg)}}
  h1{font-size:20px;margin:0 0 14px;font-weight:600}
  p{color:#9aa3b2;line-height:1.85;margin:8px 0;font-size:14px}
  .t{margin-top:18px;color:#c7cbd6;font-size:13px}
</style></head>
<body><div class="boot">
  <div class="spin"></div>
  <h1>正在启动 Multi-Agent Studio…</h1>
  <p>程序正在加载运行环境与数据库服务，请稍候。</p>
  <p>首次启动、或电脑配置较低时会更久一些（可能需要 1～2 分钟）。</p>
  <p>此窗口就绪后会自动进入界面，请不要关闭。</p>
  <div class="t">已等待 <span id="s">0</span> 秒</div>
</div>
<script>
  (function(){var n=0;setInterval(function(){n++;var e=document.getElementById('s');if(e)e.textContent=n;},1000);})();
</script>
</body></html>"""

_window = None
_target_url = None
_quitting = False
_lock = threading.Lock()


def _set_app_user_model_id() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def has_webview2_runtime() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, _WEBVIEW2_KEY) as key:
                winreg.QueryValueEx(key, "pv")
                return True
        except OSError:
            continue
    return False


def available() -> bool:
    try:
        import webview  # noqa: F401
    except Exception:
        return False
    return has_webview2_runtime()


def _closing_handler(window):
    def _handler():
        global _quitting
        if _quitting:
            return True
        try:
            window.hide()
        except Exception:
            pass
        return False

    return _handler


def create_main_window(
    url: str,
    *,
    hide_on_close: bool = True,
    width: int = 1280,
    height: int = 880,
    maximized: bool = False,
):
    global _window
    import webview

    _set_app_user_model_id()
    window = webview.create_window(
        APP_TITLE,
        url,
        width=width,
        height=height,
        min_size=(900, 600),
        confirm_close=False,
        background_color="#0b0d12",
        maximized=maximized,
        text_select=True,
    )
    if hide_on_close:
        window.events.closing += _closing_handler(window)
    _window = window
    return window


def create_boot_window(
    url: str,
    *,
    hide_on_close: bool = True,
    width: int = 1280,
    height: int = 880,
    maximized: bool = False,
):
    """创建窗口并先显示「正在启动…」页；服务就绪后调用 navigate() 切到应用。"""
    global _window, _target_url
    import webview

    _set_app_user_model_id()
    _target_url = url
    window = webview.create_window(
        APP_TITLE,
        html=BOOT_HTML,
        width=width,
        height=height,
        min_size=(900, 600),
        confirm_close=False,
        background_color="#0b0d12",
        maximized=maximized,
        text_select=True,
    )
    if hide_on_close:
        window.events.closing += _closing_handler(window)
    _window = window
    return window


def navigate(url: str | None = None) -> bool:
    """把窗口从启动页导航到真正的界面地址（服务就绪后调用）。"""
    with _lock:
        window = _window
    target = url or _target_url
    if window is None or not target:
        return False
    try:
        window.load_url(target)
        return True
    except Exception:
        return False


def start(debug: bool = False) -> None:
    import webview

    try:
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    kwargs = {}
    if ICON.exists():
        kwargs["icon"] = str(ICON)

    webview.start(
        gui="edgechromium",
        debug=debug,
        private_mode=False,
        storage_path=str(PROFILE_DIR),
        **kwargs,
    )


def show_window() -> bool:
    with _lock:
        window = _window
    if window is None:
        return False
    try:
        window.show()
        window.restore()
        return True
    except Exception:
        return False


def quit() -> None:
    global _quitting
    with _lock:
        _quitting = True
        window = _window
    if window is not None:
        try:
            window.destroy()
        except Exception:
            pass
