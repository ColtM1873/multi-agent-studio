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


async def _consume_messages(stream, emit: Emit):
    """主图（主 agent）的文本流。"""
    async for message in stream.messages:
        is_user = message.node == "merge_human_message_with_memory"
        async for token in message.text:
            await emit({
                "type": "text",
                "source": "main_user" if is_user else "main",
                "text": token,
            })


async def _consume_subgraphs(stream, emit: Emit):
    """子图（子 agent）的文本流。"""
    async for subgraph in stream.subgraphs:
        name = subgraph.graph_name
        await emit({"type": "subgraph_start", "name": name})
        async for message in subgraph.messages:
            async for token in message.text:
                await emit({"type": "text", "source": f"sub:{name}", "text": token})
        await emit({"type": "subgraph_end", "name": name})


async def _consume_values(stream, emit: Emit):
    """主图的 tool call / tool 结果事件。"""
    seen_tool_ids: set[str] = set()
    async for snapshot in stream.values:
        msgs = snapshot.get("messages", [])
        if not msgs:
            continue
        last = msgs[-1]
        if not hasattr(last, "type"):
            continue
        if last.type == "tool":
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
