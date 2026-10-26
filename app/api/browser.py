"""浏览器接管控制 REST API（ID91：按会话实例）。

每个 ``agent_id + thread_id`` 对应一个独立 Chromium profile / CDP 端口 / 暂停标志。
不传这两个参数时保持旧的单实例兼容路径（供未升级的调用方 / 单会话回归）。
"""

from __future__ import annotations

from contextlib import contextmanager

from fastapi import APIRouter
from pydantic import BaseModel

from app.runtime.browser_tools_privacy_mask import make_sensitive_masker
from app.runtime.browser_tools_wrap_up import (
    PAUSE_PROMPT,
    _apply_browser_image_tags,  # BID080
    _apply_browser_interactive_prefs,  # BID084
    _apply_browser_timing,
    is_paused,
    set_paused,
)
from app.runtime.browser_tools_api import (
    browser_status,
    warmup_browser,
)
from app.runtime.browser_sessions import current_browser_controller, use_browser_session
from app.runtime.floating_stop import FloatingStop

router = APIRouter(prefix="/api/browser", tags=["browser"])


class AgentBody(BaseModel):
    agent_id: str | None = None
    thread_id: str | None = None
    allow_open: bool = True


class PauseBody(BaseModel):
    paused: bool
    agent_id: str | None = None
    thread_id: str | None = None


class FloatingBody(BaseModel):
    agent_id: str | None = None
    thread_id: str | None = None
    show: bool = True


class OpenUrlBody(BaseModel):
    url: str
    agent_id: str | None = None
    thread_id: str | None = None


def _resolve_agent_name(agent_id: str | None) -> str:
    if not agent_id:
        return "agent"
    try:
        from app.deps import config_store

        return config_store.load(agent_id).name or "agent"
    except Exception:  # noqa: BLE001
        return "agent"


def _floating_pos_path():
    try:
        from app.deps import config_store

        return config_store._dir / "floating_stop_pos.json"
    except Exception:  # noqa: BLE001
        return None


@contextmanager
def _browser_ctx(agent_id: str | None, thread_id: str | None):
    if agent_id and thread_id:
        with use_browser_session(agent_id, thread_id):
            yield True
    else:
        yield False


def _controller():
    return current_browser_controller()


@router.get("/status")
def get_status(agent_id: str | None = None, thread_id: str | None = None):
    if agent_id and thread_id:
        from app.deps import browser_session_manager

        state = browser_session_manager.status(agent_id, thread_id)
        return {
            "connected": state["connected"],
            "port": state["port"],
            "paused": state["paused"],
            "agent_id": agent_id,
            "thread_id": thread_id,
        }
    connected, port = browser_status()
    return {"connected": connected, "port": port, "paused": is_paused()}


@router.post("/open")
def open_browser(body: AgentBody | None = None):
    body = body or AgentBody()
    try:
        if body.agent_id and body.thread_id:
            from app.deps import browser_session_manager

            existing = browser_session_manager.peek(body.agent_id, body.thread_id)
            if existing is not None and not browser_session_manager.probe(existing)["connected"]:
                # 上一次浏览器已经关闭：重新打开时不应沿用旧的暂停状态。
                existing.paused = False
        with _browser_ctx(body.agent_id, body.thread_id):
            _apply_browser_timing()
            _controller().ensure_connected()
            warmup_browser(body.agent_id, body.thread_id)
            if body.agent_id and body.thread_id:
                from app.deps import browser_session_manager

                fresh = browser_session_manager.peek(body.agent_id, body.thread_id)
                if fresh is not None:
                    fresh._probe_at = 0.0
            connected, port = browser_status(body.agent_id, body.thread_id)
        if connected:
            _show_floating(body.agent_id, body.thread_id)
        return {
            "ok": connected,
            "port": port,
            "error": "" if connected else "浏览器未就绪",
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "port": None, "error": f"{type(exc).__name__}: {exc}"}


@router.post("/pause")
def pause(body: PauseBody):
    """会话级暂停：只影响该会话自己的浏览器实例。"""
    if body.agent_id and body.thread_id:
        from app.deps import browser_session_manager

        paused = browser_session_manager.set_paused(
            body.agent_id, body.thread_id, body.paused
        )
        return {"paused": paused}
    set_paused(body.paused)
    return {"paused": is_paused()}


@router.post("/open-url")
def open_url(body: OpenUrlBody):
    url = (body.url or "").strip()
    if not url:
        return {"ok": False, "mode": "none", "error": "空 URL"}
    connected, port = browser_status(body.agent_id, body.thread_id)
    if connected:
        try:
            with _browser_ctx(body.agent_id, body.thread_id):
                _apply_browser_timing()
                result = _controller().open_url(url)
            if result.get("ok"):
                return {"ok": True, "mode": "browser", "port": port}
        except Exception:  # noqa: BLE001 — 受控浏览器打开失败则回退默认浏览器
            pass
    import webbrowser

    try:
        webbrowser.open(url)
        return {"ok": True, "mode": "external"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "mode": "external", "error": f"{type(exc).__name__}: {exc}"}


def _show_floating(agent_id: str | None, thread_id: str | None = None) -> None:
    """唤醒系统级暂停球（数据由 FloatingStop 的 provider 提供）。

    仍调用 ``show`` 以清除之前 ``hide`` 留下的手动隐藏状态；provider 模式下
    窗口内容/行数完全由 BrowserSessionManager 快照决定。
    """
    FloatingStop.instance().show(_resolve_agent_name(agent_id), _floating_pos_path())


@router.post("/floating")
def floating(body: FloatingBody):
    if body.show:
        _show_floating(body.agent_id, body.thread_id)
    else:
        FloatingStop.instance().hide()
    return {"ok": True}


@router.post("/viewport-dom")
def viewport_dom(body: AgentBody | None = None):
    body = body or AgentBody()
    with _browser_ctx(body.agent_id, body.thread_id):
        if body.agent_id and body.thread_id:
            from app.deps import browser_session_manager

            state = browser_session_manager.status(body.agent_id, body.thread_id)
            if state["paused"]:
                return {"ok": False, "content": "", "error": PAUSE_PROMPT}
        elif is_paused():
            return {"ok": False, "content": "", "error": PAUSE_PROMPT}
        allow_open = body.allow_open
        if not allow_open:
            connected, port = browser_status(body.agent_id, body.thread_id)
            if not connected:
                return {"ok": False, "content": "", "error": "浏览器未打开", "port": port}
        try:
            from browser_agent.controller import NOT_CALLED

            _apply_browser_timing()
            _apply_browser_image_tags()  # BID080
            _apply_browser_interactive_prefs()  # BID084
            result = _controller().full_dom(action_ok=NOT_CALLED)
            connected, port = browser_status(body.agent_id, body.thread_id)
            if connected:
                _show_floating(body.agent_id, body.thread_id)
            content = result.get("content") or ""
            error = result.get("error") or ""
            mask = make_sensitive_masker()
            if mask is not None:
                content = mask(content)
                error = mask(error)
            return {
                "ok": result.get("action_ok") != "失败",
                "content": content,
                "error": error,
                "port": port,
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "content": "", "error": f"{type(exc).__name__}: {exc}"}
