"""浏览器接管控制 REST API。

浏览器为**全局单例**（跨会话/跨 agent 存活），暂停标志也是全局的。
所有端点用同步 ``def`` 定义：FastAPI 会在线程池执行，避免阻塞事件循环
（browser_agent 的工具是阻塞式同步调用）。

系统级「停止/继续」浮动按钮由后端 ``FloatingStop``（tkinter 置顶窗口）负责，
不依赖网页；这里提供打开/显示/隐藏它的入口。
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.runtime.browser_tools_privacy_mask import make_sensitive_masker
from app.runtime.browser_tools_wrap_up import (
    PAUSE_PROMPT,
    _apply_browser_timing,
    is_paused,
    set_paused,
)
from app.runtime.browser_tools_api import (
    browser_status,
    warmup_browser,
)


from app.runtime.floating_stop import FloatingStop

router = APIRouter(prefix="/api/browser", tags=["browser"])


class AgentBody(BaseModel):
    agent_id: str | None = None
    # 前端「实时预览」轮询时传 False：浏览器未连接则直接返回，避免轮询触发自动启动。
    allow_open: bool = True


class PauseBody(BaseModel):
    paused: bool


class FloatingBody(BaseModel):
    agent_id: str | None = None
    show: bool = True


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


def _show_floating(agent_id: str | None) -> None:
    FloatingStop.instance().show(_resolve_agent_name(agent_id), _floating_pos_path())


@router.get("/status")
def get_status():
    """浏览器连接状态 + 当前暂停状态（前端据此置灰按钮 / 同步提示按钮）。"""
    connected, port = browser_status()
    return {"connected": connected, "port": port, "paused": is_paused()}


@router.post("/open")
def open_browser(body: AgentBody | None = None):
    """打开（或复用）浏览器，并显示系统级浮动按钮。返回是否就绪与实际端口。"""
    try:
        from browser_agent.controller import BrowserController

        _apply_browser_timing()
        # 只做「连接/复用浏览器」——不要在这里抓取整页 DOM：启动时浏览器往往是
        # 空白页，full_dom 会空等 15s 才返回，使「打开浏览器」卡住约半分钟。
        # 页面内容由随后的「提示接管」预览（viewport-dom）按需抓取。
        BrowserController.instance().ensure_connected()
        # 提前完成首次互动所需的一次性加载/准备（预导入、attach、轻量 evaluate）。
        warmup_browser()
        connected, port = browser_status()
        if connected:
            _show_floating(body.agent_id if body else None)
        return {
            "ok": connected,
            "port": port,
            "error": "" if connected else "浏览器未就绪",
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "port": None, "error": f"{type(exc).__name__}: {exc}"}


@router.post("/pause")
def pause(body: PauseBody):
    """客户点「停止」→ paused=true；点「继续」→ paused=false。"""
    set_paused(body.paused)
    return {"paused": is_paused()}


@router.post("/floating")
def floating(body: FloatingBody):
    """显示 / 隐藏系统级浮动按钮（进入会话且浏览器已连接时调用，保证按钮出现）。"""
    if body.show:
        _show_floating(body.agent_id)
    else:
        FloatingStop.instance().hide()
    return {"ok": True}


@router.post("/viewport-dom")
def viewport_dom(body: AgentBody | None = None):
    """返回当前聚焦标签页 viewport 的全量 DOM（前端拼进输入框）。"""
    if is_paused():
        return {"ok": False, "content": "", "error": PAUSE_PROMPT}
    allow_open = True if body is None else body.allow_open
    if not allow_open:
        connected, port = browser_status()
        if not connected:
            return {"ok": False, "content": "", "error": "浏览器未打开", "port": port}
    try:
        from browser_agent.controller import BrowserController,NOT_CALLED

        _apply_browser_timing()
        result = BrowserController.instance().full_dom(action_ok=NOT_CALLED)
        if browser_status()[0]:
            _show_floating(body.agent_id if body else None)
        content = result.get("content") or ""
        error = result.get("error") or ""
        # 隐私遮蔽模式：预览块会随用户消息一起发给 LLM，同样需要遮蔽真实敏感值。
        mask = make_sensitive_masker()
        if mask is not None:
            content = mask(content)
            error = mask(error)
        return {
            "ok": result.get("action_ok") != "失败",
            "content": content,
            "error": error,
            "port": browser_status()[1],
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "content": "", "error": f"{type(exc).__name__}: {exc}"}
