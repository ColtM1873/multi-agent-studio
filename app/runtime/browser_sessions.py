"""按会话隔离的浏览器实例（ID91）。

每个 ``(agent_id, thread_id)`` 拥有独立的：

* Chromium profile（``.chrome-profiles/<agent>/<thread>-<hash>/``）；
* ``BrowserLauncher`` / ``BrowserController``；
* CDP 端口（各自 ``--remote-debugging-port=0``）；
* 标签页 LRU 表（仍在每个浏览器进程内部，不分桶）；
* 暂停标志。

contextvar 只用于把一次工具调用路由到正确的 controller / 暂停标志；
**不**参与标签页 LRU 或配额（见 BID086）。
"""

from __future__ import annotations

import colorsys
import contextlib
import contextvars
import hashlib
import json
import logging
import os
import random
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from browser_agent.config import ROOT
from browser_agent.controller import BrowserController
from browser_agent.launcher import BrowserLauncher

from app.config.settings import (
    DEFAULT_BROWSER_BORDER_COLOR,
    DEFAULT_BROWSER_TEXT_COLOR,
    load_settings,
    normalize_hex_color,
)

logger = logging.getLogger(__name__)

SessionKey = tuple[str, str]

_PROFILES_FILE = "browser_profiles.json"

_current_session_key: contextvars.ContextVar[SessionKey | None] = contextvars.ContextVar(
    "current_browser_session_key", default=None
)
_current_controller: contextvars.ContextVar[BrowserController | None] = contextvars.ContextVar(
    "current_browser_controller", default=None
)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _now_us() -> str:
    """带微秒的时间戳：profile 池按 ``released_at`` 做 FIFO，秒级会分不出先后。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")


def _slug(value: str, limit: int = 28) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fa5_-]+", "-", text)
    text = text.strip("-_")
    if not text:
        text = "session"
    return text[:limit]


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    text = color.lstrip("#")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def _rgb_to_hex(r: float, g: float, b: float) -> str:
    return "#" + "".join(f"{max(0, min(255, int(round(v)))):02x}" for v in (r, g, b))


def _auto_text_color(border: str) -> str:
    """给随机生成的边框色配一个可读的字体色（白 / 深灰）。"""
    r, g, b = _hex_to_rgb(border)
    brightness = 0.299 * r + 0.587 * g + 0.114 * b
    return "#ffffff" if brightness < 150 else "#1f2937"


def _random_browser_theme(used_borders: set[str]) -> dict:
    """生成一个边框色不与当前会话重复的随机配色。"""
    for _ in range(256):
        hue = random.random()
        saturation = random.uniform(0.58, 0.78)
        lightness = random.uniform(0.36, 0.56)
        r, g, b = colorsys.hls_to_rgb(hue, lightness, saturation)
        border = _rgb_to_hex(r * 255, g * 255, b * 255)
        if border not in used_borders:
            return {"border_color": border, "text_color": _auto_text_color(border)}
    # 极端情况下（随机空间被占满）用黄金角兜底，仍保证不与已用边框重复。
    for i in range(360):
        hue = (i * 0.61803398875) % 1.0
        r, g, b = colorsys.hls_to_rgb(hue, 0.48, 0.68)
        border = _rgb_to_hex(r * 255, g * 255, b * 255)
        if border not in used_borders:
            return {"border_color": border, "text_color": _auto_text_color(border)}
    return {"border_color": DEFAULT_BROWSER_BORDER_COLOR, "text_color": DEFAULT_BROWSER_TEXT_COLOR}


def _normalize_theme(theme: object) -> dict | None:
    if not isinstance(theme, dict):
        return None
    border = normalize_hex_color(theme.get("border_color"), DEFAULT_BROWSER_BORDER_COLOR)
    text = normalize_hex_color(theme.get("text_color"), DEFAULT_BROWSER_TEXT_COLOR)
    return {"border_color": border, "text_color": text}


@dataclass
class BrowserSession:
    key: SessionKey
    agent_id: str
    thread_id: str
    agent_name: str
    profile_dir: str
    controller: BrowserController
    paused: bool = False
    # 当前浏览器正在使用的配色（浏览器关闭后由 probe 清空，释放给后续会话）。
    theme: dict | None = None
    # 最近一次分配/确认配色的时间；probe 用它给「浏览器正在启动」留出宽限期。
    theme_at: float = 0.0
    # 该配色是否已经被一个真正连上的浏览器确认过。启动期（还没连上）和
    # 运行后再关闭要区别对待：前者保留 30 秒宽限，后者连续两次探测失败即可释放。
    _theme_connected: bool = False
    created_at: str = field(default_factory=_now)
    _probe_at: float = 0.0
    _probe_connected: bool = False
    _probe_port: int | None = None
    _disconnect_count: int = 0


@contextlib.contextmanager
def use_browser_session(agent_id: str, thread_id: str, agent_name: str = ""):
    """把当前上下文路由到某会话的浏览器 controller。"""
    try:
        from app.deps import browser_session_manager, config_store

        try:
            cfg = config_store.load(agent_id)
        except Exception:  # noqa: BLE001
            # 离线测试 / 配置已被删除：不要把测试会话写成真实 profile。
            yield None
            return
        if not getattr(cfg.main_agent, "browser_takeover", False):
            yield None
            return
        agent_name = agent_name or cfg.name or agent_id
    except Exception:  # noqa: BLE001
        yield None
        return
    session = browser_session_manager.get(agent_id, thread_id, agent_name)
    token_key = _current_session_key.set(session.key)
    token_ctrl = _current_controller.set(session.controller)
    try:
        yield session
    finally:
        _current_controller.reset(token_ctrl)
        _current_session_key.reset(token_key)


def current_browser_session() -> BrowserSession | None:
    key = _current_session_key.get()
    if not key:
        return None
    try:
        from app.deps import browser_session_manager

        return browser_session_manager.peek(*key)
    except Exception:  # noqa: BLE001
        return None


def current_browser_controller() -> BrowserController:
    ctrl = _current_controller.get()
    if ctrl is not None:
        return ctrl
    return BrowserController.instance()


def current_browser_key() -> SessionKey | None:
    return _current_session_key.get()


class BrowserSessionManager:
    def __init__(self, config_store) -> None:
        self.config_store = config_store
        self._sessions: dict[SessionKey, BrowserSession] = {}
        self._lock = threading.RLock()
        self._profiles_path = Path(config_store._dir) / _PROFILES_FILE
        self._profiles: dict[str, dict] = {}
        self._monitor_thread: threading.Thread | None = None
        self._monitor_stop = threading.Event()
        # 程序重启后，内存里的 ``_sessions`` 是空的，但浏览器进程可能还活着。
        # 启动时后台扫一遍带主题的 profile，把「仍在监听」的浏览器找回来，
        # 这样会话列表不必先进入会话再染色。条目只用于 UI 查询，不创建 controller。
        self._live_profiles: dict[SessionKey, dict] = {}
        self._discovery_thread: threading.Thread | None = None
        self._load_profiles()
        self._start_profile_discovery()

    # ------------------------------------------------------------------ #
    # profile 映射
    # ------------------------------------------------------------------ #
    @staticmethod
    def _profile_key(agent_id: str, thread_id: str) -> str:
        return f"{agent_id}\u241f{thread_id}"

    def _load_profiles(self) -> None:
        try:
            data = json.loads(self._profiles_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._profiles = {
                    str(k): v for k, v in data.items() if isinstance(v, dict)
                }
        except Exception:  # noqa: BLE001
            self._profiles = {}

    def _save_profiles(self) -> None:
        try:
            self._profiles_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._profiles_path.with_name(self._profiles_path.name + ".tmp")
            tmp.write_text(
                json.dumps(self._profiles, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self._profiles_path)
        except Exception:  # noqa: BLE001
            logger.exception("保存 browser_profiles.json 失败")

    @staticmethod
    def _profile_path(profile_dir: object) -> Path | None:
        text = str(profile_dir or "").strip()
        if not text:
            return None
        path = Path(text)
        return path if path.is_absolute() else (ROOT / path)

    @classmethod
    def _port_from_profile_dir(cls, profile_dir: object) -> int | None:
        """从某个 profile 的 DevToolsActivePort 读端口（只读文件，不发请求）。"""
        path = cls._profile_path(profile_dir)
        if path is None:
            return None
        active = path / "DevToolsActivePort"
        try:
            if not active.is_file():
                return None
            first = active.read_text(encoding="utf-8").splitlines()[0].strip()
            return int(first) if first.isdigit() else None
        except Exception:  # noqa: BLE001
            return None

    def _start_profile_discovery(self) -> None:
        if self._monitor_stop.is_set():
            return
        if self._discovery_thread is not None and self._discovery_thread.is_alive():
            return
        # 只要还有带主题的 profile 就值得扫描：颜色跟「存活的浏览器进程」走，
        # 已释放回池的 profile 的浏览器可能仍开着并继续占着颜色。
        if not any(_normalize_theme(item.get("theme")) for item in self._profiles.values()):
            return
        self._discovery_thread = threading.Thread(
            target=self._discover_live_profiles,
            name="browser-profile-discovery",
            daemon=True,
        )
        self._discovery_thread.start()

    def _discover_live_profiles(self) -> None:
        """找出程序重启后仍在监听的会话浏览器（后台线程，慢一点没关系）。"""
        try:
            with self._lock:
                items = [(pkey, dict(item)) for pkey, item in self._profiles.items()]
            for pkey, item in items:
                if self._monitor_stop.is_set():
                    return
                theme = _normalize_theme(item.get("theme"))
                if not theme:
                    continue
                agent_id = str(item.get("agent_id") or "")
                thread_id = str(item.get("thread_id") or "")
                if (not agent_id or not thread_id) and "\u241f" in pkey:
                    agent_id, thread_id = pkey.split("\u241f", 1)
                if not agent_id or not thread_id:
                    continue
                key = (agent_id, thread_id)
                with self._lock:
                    if key in self._sessions or key in self._live_profiles:
                        continue
                port = self._port_from_profile_dir(item.get("profile_dir"))
                if port is None:
                    continue
                try:
                    connected = BrowserLauncher._is_port_ready(port)
                except Exception:  # noqa: BLE001
                    connected = False
                if not connected:
                    continue
                with self._lock:
                    if key in self._sessions or key in self._live_profiles:
                        continue
                    self._live_profiles[key] = {
                        "agent_id": agent_id,
                        "thread_id": thread_id,
                        "port": port,
                        "theme": theme,
                        "checked_at": time.monotonic(),
                    }
                # 发现存活浏览器后让后台监控持续复核，关掉时及时释放颜色。
                self._ensure_monitor()
        except Exception:  # noqa: BLE001
            logger.exception("扫描重启后仍存活的浏览器 profile 失败")

    def _profile_dir_for(self, agent_id: str, thread_id: str, agent_name: str) -> str:
        """给会话分配一个 profile 目录。

        分配顺序（ID91 之后的「profile 池」语义）：

        1. 该会话自己名下已有条目：直接用（``released`` 的顺手认领回来，
           同名会话重建因此会拿回原 profile 与原配色）；
        2. 池子里已释放（``released=true``）的 profile：按 FIFO 认领最早的；
        3. 池子为空：新建目录与条目。
        """
        with self._lock:
            pkey = self._profile_key(agent_id, thread_id)
            item = self._profiles.get(pkey)
            if item and item.get("profile_dir"):
                path = self._profile_path(item["profile_dir"])
                if path is not None:
                    path.mkdir(parents=True, exist_ok=True)
                    if item.get("released"):
                        # 同名会话重建：认领自己释放的 profile（配色一起回来）。
                        self._bind_profile(pkey, item, agent_id, thread_id, agent_name)
                    return str(path)

            reused = self._take_released_profile(agent_id, thread_id, agent_name)
            if reused is not None:
                return reused

            digest = hashlib.sha1(pkey.encode("utf-8")).hexdigest()[:8]
            folder = Path(".") / ".chrome-profiles" / _slug(agent_id) / f"{_slug(thread_id)}-{digest}"
            path = (ROOT / folder).resolve()
            path.mkdir(parents=True, exist_ok=True)
            self._profiles[pkey] = {
                "agent_id": agent_id,
                "thread_id": thread_id,
                "agent_name": agent_name,
                "profile_dir": str(path.relative_to(ROOT)).replace("\\", "/"),
                "created_at": _now(),
            }
            self._save_profiles()
            return str(path)

    def _bind_profile(
        self,
        old_key: str,
        item: dict,
        agent_id: str,
        thread_id: str,
        agent_name: str,
    ) -> None:
        """把一条 profile 记录改绑到新会话（清掉释放标记，颜色等属性原样保留）。"""
        new_key = self._profile_key(agent_id, thread_id)
        if old_key != new_key:
            self._profiles.pop(old_key, None)
            # 浏览器进程还活着时它在 _live_profiles 里占着颜色；profile 被认领后
            # 这条记录要跟着改绑到新会话，否则颜色会「失联」。
            if "\u241f" in old_key:
                old_tuple = tuple(old_key.split("\u241f", 1))
                live = self._live_profiles.pop(old_tuple, None)
                if live is not None:
                    live["agent_id"] = agent_id
                    live["thread_id"] = thread_id
                    self._live_profiles[(agent_id, thread_id)] = live
        item.pop("released", None)
        item.pop("released_at", None)
        item["agent_id"] = agent_id
        item["thread_id"] = thread_id
        if agent_name:
            item["agent_name"] = agent_name
        item["reused_at"] = _now()
        self._profiles[new_key] = item
        self._save_profiles()

    def _take_released_profile(
        self, agent_id: str, thread_id: str, agent_name: str
    ) -> str | None:
        """从池子里按 FIFO 认领一个已释放的 profile；没有则返回 ``None``。

        FIFO 用 ``released_at`` 升序（最早释放的先被复用，时间戳带微秒）；
        同一时刻释放的条目保持 ``browser_profiles.json`` 里的书写顺序，结果确定。
        """
        candidates = [
            (pkey, item)
            for pkey, item in self._profiles.items()
            if item.get("released") and item.get("profile_dir")
        ]
        candidates.sort(key=lambda pair: str(pair[1].get("released_at") or ""))
        for pkey, item in candidates:
            path = self._profile_path(item["profile_dir"])
            if path is None:
                continue
            path.mkdir(parents=True, exist_ok=True)
            self._bind_profile(pkey, item, agent_id, thread_id, agent_name)
            return str(path)
        return None

    # ------------------------------------------------------------------ #
    # profile 池：会话删除 = 释放（不关浏览器、不删目录）
    # ------------------------------------------------------------------ #
    def release(self, agent_id: str, thread_id: str) -> bool:
        """把会话的 profile 释放回池（资源复用语义）。

        * **不关闭**浏览器进程、**不删除** profile 目录（登录态/主题都留着）；
        * 只摘掉内存里的 ``BrowserSession``；
        * 若浏览器进程还活着，把它转入 ``_live_profiles``（颜色仍算程序级占用，
          直到进程关闭）；进程已关则颜色随之收回；
        * 在 ``browser_profiles.json`` 里打 ``released`` / ``released_at`` 标记，
          供下一个会话按 FIFO 认领。

        返回是否真的标记了一条 profile 记录（从未创建过 profile 的会话返回 False）。
        """
        key = (agent_id, thread_id)
        keep_live = False
        with self._lock:
            session = self._sessions.pop(key, None)
            self._live_profiles.pop(key, None)
            pkey = self._profile_key(agent_id, thread_id)
            item = self._profiles.get(pkey)
            if item is None:
                return False
            item["released"] = True
            item["released_at"] = _now_us()
            if session is not None and session.theme:
                item["theme"] = dict(session.theme)
                # 浏览器进程还开着：颜色是「程序的活动进程资源」，不能因为删了
                # 会话就释放给别的浏览器（否则两个窗口同色）。
                port = session._probe_port
                if port is None:
                    try:
                        port = session.controller.browser_port()
                    except Exception:  # noqa: BLE001
                        port = None
                if port:
                    self._live_profiles[key] = {
                        "agent_id": agent_id,
                        "thread_id": thread_id,
                        "port": port,
                        "theme": dict(session.theme),
                        "checked_at": time.monotonic(),
                    }
                    keep_live = True
            self._save_profiles()
        if keep_live:
            # 让后台监控继续探测它；浏览器关掉时颜色才会真正收回。
            self._ensure_monitor()
        return True

    def release_agent(self, agent_id: str) -> int:
        """删除 multi-agent 时释放它名下全部 profile（同样不关浏览器、不删目录）。"""
        keys = [key for key in list(self._sessions) if key[0] == agent_id]
        released = 0
        for key in keys:
            if self.release(*key):
                released += 1
        prefix = agent_id + "\u241f"
        with self._lock:
            for pkey, item in self._profiles.items():
                if not pkey.startswith(prefix) or item.get("released"):
                    continue
                item["released"] = True
                item["released_at"] = _now_us()
                released += 1
            if released:
                self._save_profiles()
        return released

    # ------------------------------------------------------------------ #
    # 浏览器配色（按会话取色，互不重复）
    # ------------------------------------------------------------------ #
    def _color_presets(self) -> list[dict]:
        try:
            settings = load_settings(self.config_store._dir)
        except Exception:  # noqa: BLE001
            return []
        out: list[dict] = []
        for item in settings.browser_color_presets or []:
            raw = item.model_dump() if hasattr(item, "model_dump") else item
            theme = _normalize_theme(raw)
            if theme:
                out.append(theme)
        return out

    @staticmethod
    def _launch_pending(session: BrowserSession) -> bool:
        """浏览器进程已经拉起、但 CDP 端口还没就绪（或启动较慢）。

        全新 profile 首次启动 Chrome 可能远超 30 秒；端口没就绪并不等于启动失败。
        这时如果按「超过 30 秒就释放」处理，另一个会话会拿到同一个预设色（撞色）。
        只要 ``launcher.process`` 还活着，就视为「仍在启动、继续占色」。
        """
        launcher = getattr(session.controller, "launcher", None)
        process = getattr(launcher, "process", None)
        if process is None:
            return False
        try:
            return process.poll() is None
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _used_borders(sessions, exclude: BrowserSession | None = None) -> set[str]:
        """只统计「确实还占着」的颜色。

        * 端口已连上：占用；
        * 刚分配、浏览器还在启动（还没连上）：30 秒内占用，避免启动期撞色；
          进程仍活着时继续占用（慢启动不能算失败）；
        * 曾经连上、之后关闭：连续两次探测失败后就不再占用，让预设色能尽快复用；
        * 从未连上且超过 30 秒（且进程已退出）：视为启动失败，释放。
        """
        now = time.monotonic()
        used: set[str] = set()
        for item in sessions:
            if item is exclude or not item.theme:
                continue
            occupied = bool(item._probe_connected)
            if not occupied:
                if item._theme_connected:
                    # 曾经连上过：连续两次探测失败才视为真正关闭。
                    occupied = item._disconnect_count < 2
                else:
                    # 还没连上：30 秒启动宽限 + 进程仍存活时继续占色，
                    # 不能被启动期的探测次数提前打破。
                    occupied = (
                        now - item.theme_at <= 30.0
                    ) or BrowserSessionManager._launch_pending(item)
            if not occupied:
                continue
            border = item.theme.get("border_color")
            if border:
                used.add(str(border).lower())
        return used

    def _saved_theme(self, agent_id: str, thread_id: str) -> dict | None:
        item = self._profiles.get(self._profile_key(agent_id, thread_id))
        if not item:
            return None
        return _normalize_theme(item.get("theme"))

    def _save_profile_theme(self, session: BrowserSession, theme: dict) -> None:
        item = self._profiles.get(self._profile_key(session.agent_id, session.thread_id))
        if item is None:
            return
        item["theme"] = dict(theme)
        self._save_profiles()

    def _allocate_theme(self, session: BrowserSession) -> dict:
        """给「即将启动的浏览器进程」分配一个配色。

        颜色是**程序级的活动进程资源**，不是 profile / 会话的持久属性：

        * 占用集合 = 当前所有仍存活的浏览器进程（活跃会话 + 程序重启后
          发现仍在监听的浏览器）；
        * 按用户预设列表顺序取第一个没被占用的；列表用尽则随机不重复；
        * 进程关闭后颜色立即回到池子（``probe`` / ``_refresh_live_profiles``
          把它从占用集合里摘掉），下一次启动重新按列表分配，不沿用旧色。
        """
        with self._lock:
            used = self._used_borders(self._sessions.values(), exclude=session)
            # 程序重启后发现的存活浏览器不在 _sessions 里，但颜色仍被它们占着。
            for item in self._live_profiles.values():
                theme = _normalize_theme(item.get("theme"))
                if theme:
                    used.add(theme["border_color"].lower())

            for preset in self._color_presets():
                if preset["border_color"].lower() in used:
                    continue
                session.theme = preset
                session.theme_at = time.monotonic()
                session._theme_connected = False
                session._disconnect_count = 0
                self._save_profile_theme(session, preset)
                return preset

            theme = _random_browser_theme(used)
            session.theme = theme
            session.theme_at = time.monotonic()
            session._theme_connected = False
            session._disconnect_count = 0
            self._save_profile_theme(session, theme)
            return theme

    def _browser_connected(self, controller: BrowserController) -> bool:
        try:
            # browser_port() 本身只在端口可用时才返回非空值。
            return bool(controller.browser_port())
        except Exception:  # noqa: BLE001
            return False

    def rename_agent(self, old_id: str, new_id: str) -> bool:
        """agent 改名时同步 profile 映射的键；不移动正在使用的 profile 目录。"""
        if old_id == new_id:
            return False
        changed = False
        marker = "\u241f"
        with self._lock:
            for pkey, item in list(self._profiles.items()):
                if not pkey.startswith(old_id + marker):
                    continue
                thread_id = pkey[len(old_id) + 1:]
                new_key = self._profile_key(new_id, thread_id)
                item["agent_id"] = new_id
                self._profiles.pop(pkey, None)
                self._profiles[new_key] = item
                changed = True
            if changed:
                self._save_profiles()
        return changed

    # ------------------------------------------------------------------ #
    # session 获取 / 状态
    # ------------------------------------------------------------------ #
    def get(
        self, agent_id: str, thread_id: str, agent_name: str = ""
    ) -> BrowserSession:
        key = (agent_id, thread_id)
        with self._lock:
            session = self._sessions.get(key)
            if session is not None:
                if agent_name and session.agent_name != agent_name:
                    session.agent_name = agent_name
                self._apply_tab_limit(session)
                # 颜色只跟「仍存活的浏览器进程」走：进程在，恢复它当前的颜色；
                # 进程已关，旧色作废（下次启动按预设列表重新分配）。
                if session.theme is None:
                    saved = self._saved_theme(agent_id, thread_id)
                    if saved and self._browser_connected(session.controller):
                        session.theme = saved
                        session.theme_at = time.monotonic()
                        session._theme_connected = True
                        session._disconnect_count = 0
                        session.controller.launcher.theme = saved
                if session.controller.launcher.theme_provider is None:
                    session.controller.launcher.theme_provider = (
                        lambda s=session: self._allocate_theme(s)
                    )
                return session
            if not agent_name:
                try:
                    cfg = self.config_store.load(agent_id)
                    agent_name = cfg.name or agent_id
                except Exception:  # noqa: BLE001
                    agent_name = agent_id
            profile_dir = self._profile_dir_for(agent_id, thread_id, agent_name)
            launcher = BrowserLauncher(profile_dir=profile_dir, persist_global=False)
            controller = BrowserController(launcher=launcher)
            controller.tab_limit_override = self._tab_limit_for(agent_id)
            session = BrowserSession(
                key=key,
                agent_id=agent_id,
                thread_id=thread_id,
                agent_name=agent_name or agent_id,
                profile_dir=profile_dir,
                controller=controller,
            )
            # 复用的池中 profile：只有它背后的浏览器进程还活着，才继承它的颜色；
            # 进程已关则视为「颜色已收回」，等真正 launch 时按预设列表重新分配。
            saved = self._saved_theme(agent_id, thread_id)
            if saved and self._browser_connected(controller):
                session.theme = saved
                session.theme_at = time.monotonic()
                session._theme_connected = True
                session._disconnect_count = 0
                launcher.theme = saved
            launcher.theme_provider = lambda s=session: self._allocate_theme(s)
            self._sessions[key] = session
            self._live_profiles.pop(key, None)
            self._ensure_monitor()
            try:
                from app.runtime.floating_stop import FloatingStop

                FloatingStop.instance().ensure_started()
            except Exception:  # noqa: BLE001
                pass
            return session

    def _ensure_monitor(self) -> None:
        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            return
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, name="browser-session-monitor", daemon=True
        )
        self._monitor_thread.start()

    def _monitor_loop(self) -> None:
        """后台探测各会话浏览器端口；Tk 线程只读缓存，不做 HTTP/CDP 探测。"""
        while not self._monitor_stop.is_set():
            for session in self.sessions():
                try:
                    self.probe(session, max_age=0.0)
                except Exception:  # noqa: BLE001
                    pass
            try:
                self._refresh_live_profiles()
            except Exception:  # noqa: BLE001
                pass
            self._monitor_stop.wait(1.5)

    def _refresh_live_profiles(self) -> None:
        """复核程序重启后发现的浏览器；关掉后不能继续占着颜色。"""
        with self._lock:
            items = list(self._live_profiles.items())
        now = time.monotonic()
        for key, item in items:
            if now - float(item.get("checked_at") or 0.0) < 1.0:
                continue
            try:
                connected = BrowserLauncher._is_port_ready(int(item["port"]))
            except Exception:  # noqa: BLE001
                connected = False
            item["checked_at"] = now
            if not connected:
                with self._lock:
                    self._live_profiles.pop(key, None)

    def shutdown(self) -> None:
        self._monitor_stop.set()
        thread = self._discovery_thread
        if thread is not None and thread.is_alive():
            # 启动扫描最多卡在一次 HTTP 探测上；退出时等一小会儿，避免
            # 临时目录/测试环境已经销毁后线程还在读 profile。
            thread.join(timeout=2.0)

    def peek(self, agent_id: str, thread_id: str) -> BrowserSession | None:
        return self._sessions.get((agent_id, thread_id))

    def _tab_limit_for(self, agent_id: str) -> int | None:
        try:
            cfg = self.config_store.load(agent_id)
            return int(cfg.main_agent.browser_tab_max_number)
        except Exception:  # noqa: BLE001
            return None

    def _apply_tab_limit(self, session: BrowserSession) -> None:
        limit = self._tab_limit_for(session.agent_id)
        if limit is not None:
            session.controller.tab_limit_override = limit

    def probe(self, session: BrowserSession, *, max_age: float = 1.0) -> dict:
        now = time.monotonic()
        if now - session._probe_at < max_age:
            return {
                "connected": session._probe_connected,
                "port": session._probe_port,
                "paused": session.paused,
            }
        port = session.controller.browser_port()
        connected = bool(port and BrowserLauncher._is_port_ready(port))
        session._probe_at = now
        session._probe_connected = connected
        session._probe_port = port
        if connected:
            session._disconnect_count = 0
            session._theme_connected = True
        else:
            # 浏览器关闭：暂停状态复位，下次重新打开时不会仍被短路。
            session.paused = False
            # 浏览器进程还活着（只是 CDP 端口尚未就绪 / 启动较慢）时不算「断开」，
            # 否则慢启动会被误判成启动失败并释放颜色，导致两个会话撞色。
            if not self._launch_pending(session):
                session._disconnect_count += 1
            # 同时释放颜色，让后续会话可以按优先级重新使用它。
            # - 浏览器真正连过：连续两次探测失败即可释放（约 3 秒），
            #   不要让它继续白占预设色；
            # - 还没连上：给 30 秒启动宽限（覆盖 launch ready_timeout=25s），
            #   超过仍连不上且进程已退出才视为启动失败并释放。
            if session._disconnect_count >= 2:
                if session._theme_connected or (
                    time.monotonic() - session.theme_at > 30.0
                ):
                    session.theme = None
                    session.theme_at = 0.0
                    session._theme_connected = False
        return {"connected": connected, "port": port, "paused": session.paused}

    def status(self, agent_id: str, thread_id: str) -> dict:
        """实时状态：必要时真的去连一次 CDP 端口（会阻塞到 connect 超时）。

        UI 的 2 秒轮询不要直接用它，见 ``cached_status()``。
        """
        session = self.peek(agent_id, thread_id)
        if session is None:
            return {"connected": False, "port": None, "paused": False}
        return self.probe(session)

    def cached_status(self, agent_id: str, thread_id: str) -> dict:
        """只读后台监控线程维护的探测缓存，**绝不发网络请求**。

        会话选择页 / 悬浮球这类每 2 秒一次的高频接口必须走这里：真实探测要为每个
        会话连一次 CDP 端口，浏览器关掉之后端口 connect 会等满超时（约 1 秒），
        多浏览器任务下 ``/api/sessions`` 会被拖到数秒，而页面渲染又在等这个响应。
        缓存由 ``_monitor_loop`` 每 1.5 秒刷新一次，对「列表着色」这种派生 UI 来说
        延迟完全够用。
        """
        session = self.peek(agent_id, thread_id)
        if session is None:
            return {"connected": False, "port": None, "paused": False}
        return {
            "connected": bool(session._probe_connected),
            "port": session._probe_port,
            "paused": session.paused,
        }

    def browser_theme_entries(self, *, live: bool = False) -> list[dict]:
        """返回「浏览器仍开着」的会话及其配色，供会话列表着色。

        默认 ``live=False``：只读监控线程的探测缓存，不联网，接口因此是毫秒级。
        ``live=True`` 保留旧行为（当场探测，可能阻塞 1~2 秒/会话），供离线验证或
        需要「立刻拿到真实连接状态」的调用方使用。
        """
        entries: list[dict] = []
        seen: set[SessionKey] = set()
        for session in sorted(self.sessions(), key=lambda s: s.created_at):
            seen.add(session.key)
            try:
                if live:
                    state = self.probe(session, max_age=1.0)
                else:
                    state = {
                        "connected": bool(session._probe_connected),
                        "port": session._probe_port,
                    }
            except Exception:  # noqa: BLE001
                continue
            if not state.get("connected"):
                continue
            theme = _normalize_theme(session.theme)
            if theme is None:
                # 兼容旧 profile / 程序重启后尚未触发 launch 的极少数情况。
                theme = self._saved_theme(session.agent_id, session.thread_id)
                if theme is None:
                    continue
                session.theme = theme
                session.theme_at = time.monotonic()
                session._theme_connected = True
            entries.append(
                {
                    "agent_id": session.agent_id,
                    "thread_id": session.thread_id,
                    "border_color": theme["border_color"],
                    "text_color": theme["text_color"],
                }
            )
        # 程序重启后发现的存活浏览器：不创建 BrowserSession，只做列表着色。
        with self._lock:
            discovered = list(self._live_profiles.items())
        now = time.monotonic()
        for key, item in discovered:
            if key in seen:
                continue
            if live and now - float(item.get("checked_at") or 0.0) >= 1.0:
                try:
                    connected = BrowserLauncher._is_port_ready(int(item["port"]))
                except Exception:  # noqa: BLE001
                    connected = False
                item["checked_at"] = now
                if not connected:
                    with self._lock:
                        self._live_profiles.pop(key, None)
                    continue
            theme = _normalize_theme(item.get("theme"))
            if theme is None:
                continue
            entries.append(
                {
                    "agent_id": item["agent_id"],
                    "thread_id": item["thread_id"],
                    "border_color": theme["border_color"],
                    "text_color": theme["text_color"],
                }
            )
        return entries

    def sessions(self) -> list[BrowserSession]:
        with self._lock:
            return list(self._sessions.values())

    def toggle_pause(self, agent_id: str, thread_id: str) -> bool:
        session = self.peek(agent_id, thread_id)
        if session is None:
            return False
        session.paused = not session.paused
        return session.paused

    def set_paused(self, agent_id: str, thread_id: str, paused: bool) -> bool:
        session = self.get(agent_id, thread_id)
        session.paused = bool(paused)
        return session.paused

    def is_current_paused(self) -> bool:
        key = _current_session_key.get()
        if key:
            session = self.peek(*key)
            return bool(session and session.paused)
        try:
            from app.runtime.browser_tools_wrap_up import _legacy_is_paused

            return bool(_legacy_is_paused())
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------ #
    # OS 悬浮球数据源
    # ------------------------------------------------------------------ #
    def floating_entries(self) -> list[dict]:
        """返回当前应显示在系统级暂停球里的会话（按创建顺序）。"""
        try:
            from app.deps import session_hub
        except Exception:  # noqa: BLE001
            session_hub = None
        pending_entries: list[dict] = []
        active_entry: dict | None = None
        for session in sorted(self.sessions(), key=lambda s: s.created_at):
            # 使用后台监控线程维护的缓存，避免 Tk 线程里做阻塞探测。
            state = self.probe(session, max_age=5.0)
            if not state["connected"]:
                continue
            pending = bool(
                session_hub and session_hub.is_pending(session.agent_id, session.thread_id)
            )
            active = bool(
                session_hub and session_hub.active_view == session.key
            )
            entry = {
                "key": session.key,
                "agent_name": session.agent_name,
                "thread_id": session.thread_id,
                "paused": bool(state["paused"]),
                "toggle": (
                    lambda a=session.agent_id, t=session.thread_id: self.toggle_pause(a, t)
                ),
            }
            if pending:
                pending_entries.append(entry)
            elif active:
                active_entry = entry
        # 验收后立刻从“多项列表”里去掉该会话；若此时已经没有待验收会话，
        # 只剩当前正在看的浏览器，则回退到原来的单实例悬浮球样式。
        if pending_entries:
            return pending_entries
        return [active_entry] if active_entry is not None else []
