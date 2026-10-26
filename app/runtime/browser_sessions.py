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

import contextlib
import contextvars
import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from browser_agent.config import ROOT
from browser_agent.controller import BrowserController
from browser_agent.launcher import BrowserLauncher

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


def _slug(value: str, limit: int = 28) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fa5_-]+", "-", text)
    text = text.strip("-_")
    if not text:
        text = "session"
    return text[:limit]


@dataclass
class BrowserSession:
    key: SessionKey
    agent_id: str
    thread_id: str
    agent_name: str
    profile_dir: str
    controller: BrowserController
    paused: bool = False
    created_at: str = field(default_factory=_now)
    _probe_at: float = 0.0
    _probe_connected: bool = False
    _probe_port: int | None = None


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
        self._load_profiles()

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

    def _profile_dir_for(self, agent_id: str, thread_id: str, agent_name: str) -> str:
        pkey = self._profile_key(agent_id, thread_id)
        item = self._profiles.get(pkey)
        if item and item.get("profile_dir"):
            path = Path(str(item["profile_dir"]))
            if not path.is_absolute():
                path = ROOT / path
            path.mkdir(parents=True, exist_ok=True)
            return str(path)

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
            self._sessions[key] = session
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
            self._monitor_stop.wait(1.5)

    def shutdown(self) -> None:
        self._monitor_stop.set()

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
        if not connected:
            # 浏览器关闭：暂停状态复位，下次重新打开时不会仍被短路。
            session.paused = False
        return {"connected": connected, "port": port, "paused": session.paused}

    def status(self, agent_id: str, thread_id: str) -> dict:
        session = self.peek(agent_id, thread_id)
        if session is None:
            return {"connected": False, "port": None, "paused": False}
        return self.probe(session)

    def sessions(self) -> list[BrowserSession]:
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
