"""多实例会话状态 / 验收 API（ID91）。"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.deps import browser_session_manager, session_hub

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class ActiveViewBody(BaseModel):
    agent_id: str | None = None
    thread_id: str | None = None
    visible: bool = True


@router.get("")
async def list_sessions():
    import asyncio

    sessions, counts = await session_hub.snapshot_pending()

    def enrich():
        for item in sessions:
            # 只读监控线程的探测缓存：这个接口每 2 秒被轮询一次，绝不能在这里
            # 去连每个会话的 CDP 端口（浏览器关闭后 connect 要等满超时才失败，
            # 多浏览器任务下会把响应拖到数秒，会话选择页因此一直「加载中」）。
            item["browser"] = browser_session_manager.cached_status(
                item["agent_id"], item["thread_id"]
            )
            if not item.get("agent_name"):
                try:
                    from app.deps import config_store

                    item["agent_name"] = config_store.load(item["agent_id"]).name
                except Exception:  # noqa: BLE001
                    item["agent_name"] = item["agent_id"]
        return browser_session_manager.browser_theme_entries()

    # 上面的 enrich 现在只读缓存（毫秒级），仍放到线程池里避免未来改动阻塞事件循环。
    # 同时带回「浏览器仍开着」的会话配色（会话选择页按浏览器主色着色）。
    browsers = await asyncio.to_thread(enrich)
    active = session_hub.active_view
    return {
        "sessions": sessions,
        "counts": counts,
        "browsers": browsers,
        "active": (
            {"agent_id": active[0], "thread_id": active[1]}
            if active
            else None
        ),
    }


@router.post("/{agent_id}/threads/{thread_id}/accept")
async def accept_session(agent_id: str, thread_id: str):
    ok = await session_hub.accept(agent_id, thread_id)
    if not ok:
        return {"ok": False, "error": "该会话当前没有待验收的完成状态"}
    return {"ok": True}


@router.post("/active")
def set_active_view(body: ActiveViewBody):
    session_hub.set_active_view(body.agent_id, body.thread_id, body.visible)
    return {"ok": True}
