"""流式聊天 WebSocket 端点。

客户端→服务端消息：
  {"type": "send", "content": "..."}   发送一轮消息
  {"type": "proactive_summarize"}      触发主动全量总结
  {"type": "resume", "value": "yes"}   回复 interrupt 中断
  {"type": "stop"}                     关闭

服务端→客户端事件：
  {"type": "text", "source": "main"|"sub:<name>", "text": token}
  {"type": "reasoning", "source": "main"|"sub:<name>", "text": token}
  {"type": "subgraph_start"|"subgraph_end", "name": ...}
  {"type": "tool_call", "name", "args"}
  {"type": "tool_result", "name", "content"}
  {"type": "interrupt", "prompt"}
  {"type": "done", "final_state"}
  {"type": "error", "message"}
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.deps import chat_manager
from app.services.chat import make_proactive_summery_input, make_user_input

router = APIRouter()


@router.websocket("/api/agents/{agent_id}/threads/{thread_id}/chat")
async def chat_ws(websocket: WebSocket, agent_id: str, thread_id: str):
    await websocket.accept()

    try:
        runtime = await chat_manager.get_runtime(agent_id)
    except Exception as e:
        await websocket.send_json({"type": "error", "message": f"无法构建 agent: {e}"})
        await websocket.close()
        return

    send_lock = asyncio.Lock()
    resume_future: asyncio.Future | None = None
    current_run: asyncio.Task | None = None

    async def emit(event: dict):
        async with send_lock:
            await websocket.send_json(event)

    async def on_interrupt(prompt: str) -> str:
        nonlocal resume_future
        await emit({"type": "interrupt", "prompt": prompt})
        resume_future = asyncio.get_running_loop().create_future()
        return await resume_future

    async def _run(content: str, proactive: bool = False):
        import os

        html_files: list[str] = []

        async def emit_checked(event: dict):
            if event.get("type") == "tool_call":
                name = event.get("name") or ""
                args = event.get("args") or {}
                fp = str(args.get("file_path") or "")
                if name == "write_file" and fp.lower().endswith(".html"):
                    html_files.append(fp)
            await emit(event)

        try:
            user_input = make_proactive_summery_input() if proactive else make_user_input(content)
            final_state = await runtime.run(
                thread_id, user_input, emit_checked, on_interrupt
            )
            await emit({"type": "done", "final_state": final_state})

            # HTML 报告生成后，用系统默认程序打开
            for fp in html_files:
                root = runtime.config.main_agent.file_tools.root_dir
                full = fp if os.path.isabs(fp) else os.path.join(root, fp)
                if os.path.exists(full):
                    try:
                        os.startfile(full)  # noqa: F821 — Windows
                    except Exception:
                        pass
        except Exception as e:
            await emit({"type": "error", "message": str(e)})

    try:
        while True:
            data = await websocket.receive_json()
            mtype = data.get("type")

            if mtype == "send":
                if current_run and not current_run.done():
                    await emit({"type": "error", "message": "上一轮仍在运行"})
                    continue
                current_run = asyncio.create_task(_run(data.get("content", "")))
            elif mtype == "proactive_summarize":
                if current_run and not current_run.done():
                    await emit({"type": "error", "message": "上一轮仍在运行"})
                    continue
                current_run = asyncio.create_task(_run("", proactive=True))
            elif mtype == "resume":
                if resume_future and not resume_future.done():
                    resume_future.set_result(data.get("value", ""))
            elif mtype == "stop":
                break
    except WebSocketDisconnect:
        pass
    finally:
        if current_run and not current_run.done():
            current_run.cancel()
