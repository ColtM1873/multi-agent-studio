"""把 LangGraph 事件流转换为结构化事件 dict（重构自 astream_consume_new.py）。

emit: async callable，接收事件 dict；on_interrupt: async callable，接收中断提示字符串，
返回用户恢复值。两者由调用方（WebSocket / CLI）提供。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

Emit = Callable[[dict[str, Any]], Awaitable[None]]
InterruptHandler = Callable[[str], Awaitable[str]]


async def run_agent_stream(
    main_agent: CompiledStateGraph,
    thread_id_config: dict,
    user_input: dict[str, Any],
    emit: Emit,
    on_interrupt: InterruptHandler | None = None,
) -> str:
    """运行一次 agent 调用，把 token/子图/tool 事件流式 emit 出去。

    返回最终的 state 字符串（与旧 stream_ainvoke 一致）。
    """
    while True:
        stream = await main_agent.astream_events(input=user_input, config=thread_id_config, version="v3")

        await asyncio.gather(
            _consume_messages(stream, emit),
            _consume_subgraphs(stream, emit),
            _consume_values(stream, emit),
        )

        if not await stream.interrupted():
            final_state = await stream.output()
            return str(final_state)

        interrupt_info = (await stream.interrupts())[0].value
        if on_interrupt is None:
            resume_value = ""
        else:
            resume_value = await on_interrupt(interrupt_info)
        user_input = Command(resume=resume_value)


async def _emit_message_stream(message, source: str, emit: Emit):
    """按时间顺序 emit 一条消息的思考过程与正文。

    直接迭代 message 的原始协议事件（replay-buffer），这样思考与正文会严格按
    模型输出的先后顺序交错发出（推理模型会出现「正文—思考—正文」的停顿），
    而不是像 projection 那样先整段思考、再整段正文。普通模型没有 reasoning
    事件，自动跳过。
    """
    async for event in message:
        if event.get("event") != "content-block-delta":
            continue
        delta = event.get("delta")
        if not isinstance(delta, dict):
            block = event.get("content_block")
            if isinstance(block, dict):
                btype = block.get("type")
                if btype == "text":
                    delta = {"type": "text-delta", "text": block.get("text", "")}
                elif btype == "reasoning":
                    delta = {"type": "reasoning-delta", "reasoning": block.get("reasoning", "")}
        if not isinstance(delta, dict):
            continue
        dtype = delta.get("type", "")
        if dtype == "text-delta":
            text = delta.get("text", "")
            if text:
                await emit({"type": "text", "source": source, "text": text})
        elif dtype == "reasoning-delta":
            r = delta.get("reasoning", "")
            if r:
                await emit({"type": "reasoning", "source": source, "text": r})


async def _consume_messages(stream, emit: Emit):
    """主图（主 agent）的文本流（含思考过程）。"""
    async for message in stream.messages:
        is_user = message.node == "merge_human_message_with_memory"
        await _emit_message_stream(message, "main_user" if is_user else "main", emit)


async def _consume_subgraphs(stream, emit: Emit):
    """子图（子 agent）的文本流（含思考过程）。"""
    async for subgraph in stream.subgraphs:
        name = subgraph.graph_name
        await emit({"type": "subgraph_start", "name": name})
        async for message in subgraph.messages:
            await _emit_message_stream(message, f"sub:{name}", emit)
        await emit({"type": "subgraph_end", "name": name})


async def _consume_values(stream, emit: Emit):
    """主图的 tool call / tool 结果事件。"""
    seen_tool_ids: set[str] = set()
    seen_tool_result_ids: set[str] = set()
    async for snapshot in stream.values:
        msgs = snapshot.get("messages", [])
        if not msgs:
            continue
        last = msgs[-1]
        if not hasattr(last, "type"):
            continue
        if last.type == "tool":
            # 同一条 ToolMessage 会出现在相邻的多个状态快照里（tool_node_front →
            # consume_submitted_reports 之间没有新增消息），导致 tool_result 被 emit 两次。
            # 用 tool_call_id 去重（与 tool_call 的 seen_tool_ids 对称）。
            result_id = getattr(last, "tool_call_id", None) or getattr(last, "id", None)
            if result_id and result_id in seen_tool_result_ids:
                continue
            if result_id:
                seen_tool_result_ids.add(result_id)
            content = last.content
            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            parts.append(block.get("text", ""))
                        elif block.get("type") == "reasoning":
                            parts.append(f"[reasoning: {str(block.get('reasoning', ''))[:200]}...]")
                content = "\n\n".join(parts)
            await emit(
                {"type": "tool_result", "name": getattr(last, "name", "unknown"), "content": str(content)[:2000]}
            )
        elif last.type == "ai" and getattr(last, "tool_calls", None):
            for tc in last.tool_calls:
                tid = tc.get("id", "")
                if tid and tid not in seen_tool_ids:
                    seen_tool_ids.add(tid)
                    await emit({"type": "tool_call", "name": tc.get("name", "?"), "args": tc.get("args", {})})
