"""流式聊天 WebSocket 端点（ID91：附着 / 回放 / 命令薄层）。

运行所有权在 ``app.services.session_hub.SessionHub``，本端点只负责：

1. 确保运行时就绪（下发 loading / ready / sub_agents）；
2. 收客户端 ``attach``，向 hub 注册订阅者并回放当前 run 事件；
3. 把 send / stop / edit / resume 等命令转给 hub；
4. 连接断开只退订，**不取消运行**（页面刷新 / 托盘隐藏后可重新 attach）。

客户端→服务端：
  {"type": "attach", "run_id": "...", "since": 123}
  {"type": "send", "content": "..."}
  {"type": "proactive_summarize", "percent": 20}
  {"type": "proactive_summarize_sub", "sub_agent": "...", "percent": 20}
  {"type": "edit", "request": {...}}
  {"type": "clean_all", "archive": false}
  {"type": "resume", "value": "yes"}
  {"type": "stop"}

服务端→客户端（运行事件带 run_id + seq）：
  run_state / replay_done / text / reasoning / phase / subgraph_start / subgraph_end /
  tool_call / tool_result / interrupt / snapshot_saved / status / sub_agents /
  stopping / backfill / done / error

  run_state 是状态快照/状态变更；attach 时先发 snapshot=true 的 run_state，
  再回放事件，最后 replay_done。
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.deps import chat_manager, session_hub
from app.services.session_hub import _format_build_error

router = APIRouter()

logger = logging.getLogger(__name__)


@router.websocket("/api/agents/{agent_id}/threads/{thread_id}/chat")
async def chat_ws(websocket: WebSocket, agent_id: str, thread_id: str):
    await websocket.accept()

    try:
        if not chat_manager.has_runtime(agent_id):
            await websocket.send_json({"type": "status", "status": "loading"})
        runtime = await chat_manager.get_runtime(agent_id)
        await websocket.send_json({"type": "status", "status": "ready"})
        await websocket.send_json(
            {"type": "sub_agents", "names": [s.name for s in runtime.config.sub_agents]}
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("构建运行时失败 (agent_id=%s, thread_id=%s)", agent_id, thread_id)
        await websocket.send_json({"type": "error", "message": _format_build_error(e)})
        await websocket.close()
        return

    # 第一条消息应当是 attach；兼容旧客户端直接发命令（视为 since=0 全量附着）。
    first: dict | None = None
    try:
        first = await websocket.receive_json()
    except WebSocketDisconnect:
        return

    attach = first if first.get("type") == "attach" else {}
    pending_command = None if first.get("type") == "attach" else first
    run_id = str(attach.get("run_id") or "")
    try:
        since = int(attach.get("since") or 0)
    except (TypeError, ValueError):
        since = 0

    sid, queue = await session_hub.attach(agent_id, thread_id, run_id, since)

    async def writer() -> None:
        while True:
            event = await queue.get()
            if event is None:
                return
            try:
                await websocket.send_json(event)
            except Exception:  # noqa: BLE001
                return

    writer_task = asyncio.create_task(writer())

    async def notify(event: dict) -> None:
        """命令层提示也走同一条队列，保证与事件发送严格串行。"""
        queue.put_nowait(event)

    async def handle(data: dict) -> None:
        mtype = data.get("type")
        if mtype == "send":
            ok, err = await session_hub.start(
                agent_id, thread_id, content=data.get("content", ""), kind="send"
            )
            if not ok and err:
                await notify({"type": "error", "message": err})
        elif mtype == "proactive_summarize":
            ok, err = await session_hub.start(
                agent_id,
                thread_id,
                proactive=True,
                summary_percent=_to_percent(data.get("percent")),
                kind="proactive",
            )
            if not ok and err:
                await notify({"type": "error", "message": err})
        elif mtype == "proactive_summarize_sub":
            ok, err = await session_hub.start(
                agent_id,
                thread_id,
                sub_agent=data.get("sub_agent", ""),
                summary_percent=_to_percent(data.get("percent")),
                kind="proactive_sub",
            )
            if not ok and err:
                await notify({"type": "error", "message": err})
        elif mtype == "edit":
            ok, err = await session_hub.start(
                agent_id,
                thread_id,
                kind="edit",
                edit_request=data.get("request", {}),
            )
            if not ok and err:
                await notify({"type": "error", "message": err})
        elif mtype == "clean_all":
            ok, err = await session_hub.start(
                agent_id,
                thread_id,
                clean_all=True,
                clean_all_archive=bool(data.get("archive")),
                kind="clean_all",
            )
            if not ok and err:
                await notify({"type": "error", "message": err})
        elif mtype == "resume":
            await session_hub.resume(agent_id, thread_id, data.get("value", ""))
        elif mtype == "stop":
            await session_hub.stop(agent_id, thread_id)

    try:
        if pending_command is not None:
            await handle(pending_command)
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "attach":
                # 同一连接重复 attach：不需要重新注册订阅者。
                continue
            await handle(data)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        logger.exception("聊天 WebSocket 处理失败 (agent_id=%s, thread_id=%s)", agent_id, thread_id)
    finally:
        writer_task.cancel()
        try:
            await writer_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        await session_hub.detach(sid)


def _to_percent(value) -> float | None:
    if value is None:
        return None
    try:
        percent = float(value)
    except (TypeError, ValueError):
        return None
    return percent if percent > 0 else None
