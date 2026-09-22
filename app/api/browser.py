"""浏览器接管控制 REST API。

浏览器为**全局单例**（跨会话/跨 agent 存活），暂停标志也是全局的。
所有端点用同步 ``def`` 定义：FastAPI 会在线程池执行，避免阻塞事件循环
（browser_agent 的工具是阻塞式同步调用）。
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.runtime.browser_takeover import (
    STOP_TEXT,
    browser_status,
    is_paused,
    set_paused,
)

router = APIRouter(prefix="/api/browser", tags=["browser"])


class PauseBody(BaseModel):
    paused: bool


@router.get("/status")
def get_status():
    """浏览器连接状态 + 当前暂停状态（前端据此置灰按钮 / 显示小球）。"""
    connected, port = browser_status()
    return {"connected": connected, "port": port, "paused": is_paused()}


@router.post("/open")
def open_browser():
    """打开（或复用）浏览器。返回是否就绪与实际端口。"""
    try:
        from browser_agent import tool_1_open_browser

        tool_1_open_browser()  # ensure_connected：复用或启动
        connected, port = browser_status()
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


@router.post("/viewport-dom")
def viewport_dom():
    """调用 tool-3：返回当前聚焦标签页 viewport 的全量 DOM（前端拼进输入框）。"""
    if is_paused():
        return {"ok": False, "content": "", "error": STOP_TEXT}
    try:
        from browser_agent import tool_3_get_viewport_dom

        result = tool_3_get_viewport_dom()
        return {
            "ok": result.get("action_ok") != "失败",
            "content": result.get("content") or "",
            "error": result.get("error") or "",
            "port": browser_status()[1],
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "content": "", "error": f"{type(exc).__name__}: {exc}"}
