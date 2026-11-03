"""启动端口选择与「已有实例」探测。

默认绑定 127.0.0.1:8000；被占用（包括 Windows 刚退出时的 TIME_WAIT）时依次尝试
8001、8002……最多 `PORT_TRIES` 个，避免整机同时开着别的副本 / 调试服务时新实例
静默 bind 失败、启动页无限读秒。

只依赖标准库，供 `run.py` / `tray.py` 这类启动早期入口使用；**不要** import 项目
重模块（启动早期越轻越好）。
"""

from __future__ import annotations

import json
import socket
import sys
import time
import urllib.request

HOST = "127.0.0.1"
DEFAULT_PORT = 8000
PORT_TRIES = 20
APP_TAG = "Multi-Agent Studio"


def candidate_ports(start: int = DEFAULT_PORT, tries: int = PORT_TRIES) -> range:
    """候选端口：start, start+1, …（共 tries 个）。"""
    return range(start, start + tries)


def is_port_free(port: int, host: str = HOST) -> bool:
    """能否独占绑定该端口。

    Windows 下用 SO_EXCLUSIVEADDRUSE：即使占用者用了 SO_REUSEADDR、或端口处于
    TIME_WAIT（刚退出的旧实例），也判定为「不空闲」，从而自动落到下一个端口。
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform == "win32":
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            except (AttributeError, OSError):
                pass
        sock.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        try:
            sock.close()
        except OSError:
            pass


def pick_port(host: str = HOST, start: int = DEFAULT_PORT, tries: int = PORT_TRIES) -> int | None:
    """返回第一个可绑定的端口；全部被占用时返回 None。"""
    for port in candidate_ports(start, tries):
        if is_port_free(port, host):
            return port
    return None


def _request_json(
    port: int, path: str, host: str, timeout: float, method: str = "GET"
) -> object:
    url = f"http://{host}:{port}{path}"
    data = b"" if method == "POST" else None
    req = urllib.request.Request(url, data=data, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def health_ok(port: int, host: str = HOST, timeout: float = 0.35) -> bool:
    """只读探测：该端口上是不是本程序的 /api/health。"""
    try:
        data = _request_json(port, "/api/health", host, timeout)
        return isinstance(data, dict) and data.get("ok") is True
    except Exception:
        return False


def is_app_url(url: str) -> bool:
    """当前 URL 是否已经进入应用界面（而不是停在启动页 about:blank / data:）。

    拿不到 URL（空串）时按「已进入」处理，避免把正常实例误伤。
    """
    u = (url or "").strip().lower()
    if not u:
        return True
    return u.startswith(("http://127.0.0.1", "http://localhost"))


def probe_focus(
    port: int, host: str = HOST, timeout: float = 0.8, app_tag: str = APP_TAG
) -> tuple[str, bool, str]:
    """探测某端口的 /api/focus，返回 `(state, shown, url)`。

    - `state == "app"`：响应里的 app 字段匹配本程序；
    - `state == "not_app"`：有响应但不是本程序（404 / 其它 app 字段）；
    - `state == "unreachable"`：超时或连接失败（可能是 GUI 卡死的本程序）。

    `shown` 是 `/api/focus` 的 `ok`：窗口**真的被唤出**才为 True；
    `url` 是窗口当前加载的地址（用来识别「停在启动页」的残废实例）。
    """
    try:
        data = _request_json(port, "/api/focus", host, timeout, method="POST")
    except Exception:
        return "unreachable", False, ""
    if not isinstance(data, dict) or data.get("app") != app_tag:
        return "not_app", False, ""
    return "app", data.get("ok") is True, str(data.get("url") or "")


def focus_existing(port: int, host: str = HOST, timeout: float = 0.8, app_tag: str = APP_TAG) -> bool:
    """请求已有实例把桌面窗口唤到前台；**窗口真的唤出且已进入应用界面**才返回 True。

    只校验 app 字段是不够的：残留进程的 HTTP 可能还活着，但 `/api/focus` 可能返回
    `ok=False`（窗口已销毁/卡死），或者窗口能显示却还停在启动页（navigate 失败）。
    这两种都必须按「未打开」处理，否则新实例会直接退出、用户什么都看不到。
    """
    state, shown, url = probe_focus(port, host, timeout, app_tag)
    return state == "app" and shown and is_app_url(url)


def ready_ports(start: int = DEFAULT_PORT, tries: int = PORT_TRIES, host: str = HOST, deadline: float = 1.5) -> list[int]:
    """返回候选端口上 `/api/health` 已就绪的端口（只读探测，不触发任何动作）。"""
    ports: list[int] = []
    stop_at = time.monotonic() + max(0.3, float(deadline))
    for port in candidate_ports(start, tries):
        if time.monotonic() > stop_at:
            break
        if health_ok(port, host):
            ports.append(port)
    return ports


def find_existing(
    start: int = DEFAULT_PORT,
    tries: int = PORT_TRIES,
    host: str = HOST,
    deadline: float = 3.0,
    app_tag: str = APP_TAG,
) -> int | None:
    """扫描候选端口，找到已在本机运行的本程序实例并把它唤到前台。

    先 GET /api/health（只读）过滤，再 POST /api/focus（本程序的路由，避免误伤
    其它本地服务）；返回命中的端口，没找到返回 None。`app_tag` 用来区分普通版与
    Lite 版（两者的 /api/focus 返回不同的 app 字段）。超过 deadline 秒没扫完就放弃，
    避免拖慢启动。
    """
    stop_at = time.monotonic() + max(0.5, float(deadline))
    for port in candidate_ports(start, tries):
        if time.monotonic() > stop_at:
            return None
        if not health_ok(port, host):
            continue
        if focus_existing(port, host, app_tag=app_tag):
            return port
    return None
