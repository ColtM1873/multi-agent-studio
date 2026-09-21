"""流式聊天 WebSocket 端点。

客户端→服务端消息：
  {"type": "send", "content": "..."}   发送一轮消息
  {"type": "proactive_summarize", "percent": 20}           触发主动全量总结（主 agent）
  {"type": "proactive_summarize_sub", "sub_agent": "...", "percent": 20}
                                                          触发子 agent 主动全量总结
                                                          （percent 缺省 / 非法时走默认或图内确认）
  {"type": "resume", "value": "yes"}   回复 interrupt 中断
  {"type": "stop"}                     关闭

服务端→客户端事件：
  {"type": "text", "source": "main"|"sub:<name>", "text": token}
  {"type": "reasoning", "source": "main"|"sub:<name>", "text": token}
  {"type": "phase", "source": "main"|"sub:<name>",
   "phase": "thinking"|"answering"|"tool_edit"|"delegate"|"tool_wait"}  状态栏阶段
  {"type": "subgraph_start"|"subgraph_end", "name": ...}
  {"type": "tool_call", "source": "main"|"sub:<name>", "name", "args"}
  {"type": "tool_result", "source": "main"|"sub:<name>", "name", "content"}
  {"type": "interrupt", "prompt"}
  {"type": "status", "status": "loading"}  运行时构建中（embedding 模型加载等）
  {"type": "status", "status": "ready"}    运行时已就绪，图/LLM 即将开始
  {"type": "sub_agents", "names": [...]}   配置里的子 agent 名单（前端分区渲染用）
  {"type": "done", "final_state"}
  {"type": "error", "message"}
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.deps import chat_manager
from app.services.chat import (
    make_edit_input,
    make_proactive_summary_input,
    make_proactive_summary_input_for_sub_agent,
    make_user_input,
)

router = APIRouter()

logger = logging.getLogger(__name__)


def _to_percent(value) -> float | None:
    """解析前端传来的总结百分比；非法 / 缺省返回 None（走默认或图内确认）。"""
    if value is None:
        return None
    try:
        percent = float(value)
    except (TypeError, ValueError):
        return None
    return percent if percent > 0 else None


def _format_build_error(e: Exception) -> str:
    """运行时构建失败时，给出面向用户的人话提示。

    原始异常（含堆栈）由调用处 logger.exception 写入服务端日志（托盘模式为
    logs/server.log），方便后续排查。
    """
    text = str(e)
    lowered = text.lower()
    if "vector" in lowered and ("not available" in lowered or "extension" in lowered):
        return (
            "未检测到 PostgreSQL 的 pgvector 扩展，长期记忆/会话存储无法初始化。"
            "请双击运行项目根目录的 install_pgvector.bat 一键安装，"
            "完成后重启本程序并重试。"
        )
    return "无法初始化该会话的运行环境，请稍后重试；若持续出现，请查看服务端日志 logs/server.log。"


def _format_edit_error(e: Exception) -> str:
    """把「编辑历史消息」过程中抛出的异常翻译成给用户看的人话。

    只面向界面展示自然语言；原始异常（含堆栈）由调用处 logger.exception 写入
    服务端日志（托盘模式为 logs/server.log），方便后续排查。
    """
    text = str(e)
    lowered = text.lower()

    if "vector" in lowered and ("not available" in lowered or "extension" in lowered):
        return (
            "未检测到 PostgreSQL 的 pgvector 扩展，会话存储无法工作。"
            "请双击运行项目根目录的 install_pgvector.bat 一键安装，"
            "完成后重启本程序再重试。"
        )
    if isinstance(e, asyncio.CancelledError) or "cancelled" in lowered:
        return "写入被取消（连接已断开或页面已离开），本次改动未完成。"
    if "out of range" in lowered:
        return "要修改的消息序号超出当前会话范围，页面历史可能已与后端不同步，请刷新页面后重试。"
    if "type mismatch" in lowered:
        return "待替换的消息类型与原文不一致，无法替换，请刷新页面后重试。"
    if "unsupported message type" in lowered:
        return "该消息类型暂不支持编辑。"
    if "recursion" in lowered:
        return "图执行步数超出上限，本次写入未完成。"
    if "does not exist" in lowered or "undefinedtable" in lowered:
        return "会话数据表尚未就绪，请先在该会话发送一条消息，再进入编辑模式。"
    if "connection" in lowered or "could not connect" in lowered or "connection refused" in lowered:
        return "无法连接数据库，会话数据暂时无法写入，请确认数据库仍在运行后重试。"
    if "timeout" in lowered or "timed out" in lowered:
        return "写入数据库超时，本次改动未完成，请稍后重试。"
    if "concurrent" in lowered or "invalidupdate" in lowered:
        return "该会话正有另一个操作在进行，请等它结束后再重试。"
    return "写入过程中发生了未预期的错误，请稍后重试；若持续出现，请查看服务端日志 logs/server.log。"


@router.websocket("/api/agents/{agent_id}/threads/{thread_id}/chat")
async def chat_ws(websocket: WebSocket, agent_id: str, thread_id: str):
    await websocket.accept()

    try:
        if not chat_manager.has_runtime(agent_id):
            await websocket.send_json({"type": "status", "status": "loading"})
        runtime = await chat_manager.get_runtime(agent_id)
        # 运行时已就绪（embedding 已加载完成）。显式通知前端脱离「加载Embedding模型中」，
        # 否则在「构建完成」到「第一条 token」之间（思考模型可能先出 reasoning、或先跑工具）
        # 状态栏会一直停在 loading，与实际不符。
        await websocket.send_json({"type": "status", "status": "ready"})
        # 下发配置里的子 agent 名单：前端据此判断是否启用「子 agent 分区 + 下拉切换」
        # （配置里子 agent ≤1 时自动关闭该功能）。
        await websocket.send_json(
            {"type": "sub_agents", "names": [s.name for s in runtime.config.sub_agents]}
        )
    except Exception as e:
        logger.exception(
            "构建运行时失败 (agent_id=%s, thread_id=%s)", agent_id, thread_id
        )
        await websocket.send_json({"type": "error", "message": _format_build_error(e)})
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

    async def _run(
        content: str,
        proactive: bool = False,
        sub_agent: str | None = None,
        summary_percent: float | None = None,
    ):
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
            if sub_agent:
                user_input = make_proactive_summary_input_for_sub_agent(
                    sub_agent, summary_percent
                )
            elif proactive:
                user_input = make_proactive_summary_input(summary_percent)
            else:
                user_input = make_user_input(content)
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

    async def _run_edit(request: dict):
        try:
            user_input = make_edit_input(
                request_for_subagent=bool(request.get("request_for_subagent")),
                subagent_name=request.get("subagent_name") or None,
                msg_indice=int(request.get("msg_indice", -1)),
                substitute_msg_dict=request.get("substitute_msg") or {},
            )
            final_state = await runtime.run(thread_id, user_input, emit, on_interrupt)
            await emit({"type": "done", "final_state": final_state})
        except Exception as e:
            logger.exception(
                "编辑历史消息失败 (agent_id=%s, thread_id=%s)", agent_id, thread_id
            )
            await emit({"type": "error", "message": _format_edit_error(e)})

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
                current_run = asyncio.create_task(
                    _run("", proactive=True, summary_percent=_to_percent(data.get("percent")))
                )
            elif mtype == "proactive_summarize_sub":
                if current_run and not current_run.done():
                    await emit({"type": "error", "message": "上一轮仍在运行"})
                    continue
                current_run = asyncio.create_task(
                    _run(
                        "",
                        sub_agent=data.get("sub_agent", ""),
                        summary_percent=_to_percent(data.get("percent")),
                    )
                )
            elif mtype == "edit":
                if current_run and not current_run.done():
                    await emit({"type": "error", "message": "上一轮仍在运行"})
                    continue
                current_run = asyncio.create_task(_run_edit(data.get("request", {})))
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
