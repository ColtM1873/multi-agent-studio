"""把 LangGraph 事件流转换为结构化事件 dict（重构自 astream_consume_new.py）。

emit: async callable，接收事件 dict；on_interrupt: async callable，接收中断提示字符串，
返回用户恢复值。两者由调用方（WebSocket / CLI）提供。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from langgraph.errors import GraphDrained
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import RunControl
from langgraph.types import Command

from browser_agent import bid065_trace  # BID065
from app.runtime.message_repair import (
    CANCELLED_TOOL_CONTENT,
    make_cancelled_tool_message,
    missing_tail_tool_calls,
)

logger = logging.getLogger(__name__)

Emit = Callable[[dict[str, Any]], Awaitable[None]]
InterruptHandler = Callable[[str], Awaitable[str]]


@dataclass
class RunResult:
    """一次 run 的结果：文本 + 是否被「软取消（drain）」提前结束。"""

    text: str
    drained: bool = False


async def _state_text(main_agent: CompiledStateGraph, thread_id_config: dict) -> str:
    try:
        snapshot = await main_agent.aget_state(thread_id_config)
        return str((snapshot.values or {}))
    except Exception:  # noqa: BLE001
        logger.exception("读取 drain 后的 state 失败")
        return ""


async def _repair_drained_tail(
    main_agent: CompiledStateGraph,
    thread_id_config: dict,
    *,
    messages_key: str = "messages",
    checkpoint_ns: str | None = None,
) -> list[dict]:
    """drain 结束后修「最后一条 AI 消息的尾巴」。

    为什么需要：drain 只在 superstep 边界生效。若 drain 落在 call_llm 之后、tool 节点之前，
    AI 的 tool_calls 已提交、ToolMessage 永远不会有 → 下一次调用 provider 会 400。
    这里给这些 call 追加一条「已取消」结果，历史与下一轮都干净。

    `checkpoint_ns` 不为空时修的是某个子图的 checkpoint（子 agent 的 drain 会下沉到
    它自己的 superstep 边界，同样可能留下悬空）。

    返回成功写入的 tool_call 列表（用于给前端补一条 tool_result 事件）。
    """
    config = thread_id_config
    if checkpoint_ns:
        config = {
            **thread_id_config,
            "configurable": {
                **(thread_id_config.get("configurable") or {}),
                "checkpoint_ns": checkpoint_ns,
            },
        }
    try:
        snapshot = await main_agent.aget_state(config)
        messages = (snapshot.values or {}).get(messages_key, [])
        missing = missing_tail_tool_calls(messages)
        if not missing:
            return []
        synthetic = [
            msg
            for msg in (make_cancelled_tool_message(tc) for tc in missing)
            if msg is not None
        ]
        if not synthetic:
            return []
        # 只追加、不重写；messages 是 add_messages，追加即可（此时 AI 消息就是最后一条）。
        await main_agent.aupdate_state(config, {messages_key: synthetic})
        return missing
    except Exception:  # noqa: BLE001
        logger.exception(
            "drain 后修复悬空 tool_call 失败（ns=%s，不影响停止本身）", checkpoint_ns
        )
        return []


async def run_agent_stream(
    main_agent: CompiledStateGraph,
    thread_id_config: dict,
    user_input: dict[str, Any],
    emit: Emit,
    on_interrupt: InterruptHandler | None = None,
    sub_agent_names: set[str] | None = None,
    sub_agent_state_keys: dict[str, str] | None = None,
    control: RunControl | None = None,
) -> RunResult:
    """运行一次 agent 调用，把 token/子图/tool 事件流式 emit 出去。

    sub_agent_names: 主图里作为「工具」暴露的子 agent 名字集合，用于把主 agent 正在
    生成的 tool_call 区分为「给子 agent 发布任务」还是「编辑普通工具调取指令」。

    sub_agent_state_keys: 子 agent 名 → 该子图 state 里消息列表的键（`state_messages_key`），
    用于从子图状态快照里抠出子 agent 自己的 tool_call / tool_result。

    control: 每次 run 新建的 `RunControl`。调用方（WebSocket）在用户点「停止」时调用
    `control.request_drain(...)`，图会在**当前节点跑完之后的下一个 superstep 边界**停下，
    而不是把正在跑的工作硬掐断。

    返回 `RunResult`（文本 + 是否 drained）。
    """
    sub_names = sub_agent_names or set()
    sub_state_keys = sub_agent_state_keys or {}
    while True:
        stream = await main_agent.astream_events(
            input=user_input,
            config=thread_id_config,
            version="v3",
            control=control,
        )

        # 不能用默认的 gather：drain 时三路消费协程都会抛 GraphDrained，
        # 默认 gather 只取第一个异常，其余异常会变成 "never retrieved" 警告。
        results = await asyncio.gather(
            _consume_messages(stream, emit, sub_names),
            _consume_subgraphs(stream, emit, sub_names, sub_state_keys),
            _consume_values(stream.values, emit),
            return_exceptions=True,
        )

        if any(isinstance(r, GraphDrained) for r in results):
            missing = await _repair_drained_tail(main_agent, thread_id_config)
            # 前端已经渲染了这条 AI 的 tool_call；补一条 tool_result，避免界面看起来断掉。
            for tool_call in missing:
                try:
                    await emit(
                        {
                            "type": "tool_result",
                            "source": "main",
                            "name": tool_call.get("name") or "unknown",
                            "content": CANCELLED_TOOL_CONTENT,
                        }
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("补发 drain 的 tool_result 失败")
            # drain 会继承到子图：子 agent 也可能停在自己的「LLM 之后、tool 之前」，
            # 于是子图 namespace 里留下悬空 tool_call。逐个补一条取消结果。
            # 这里不补前端事件（子图分区此时通常已 subgraph_end，补事件会多出一个分区；
            # 刷新历史即可看到；400 由请求前 repair_tool_pairs 双保险）。
            for sub_name in sorted(sub_names):
                sub_key = sub_state_keys.get(sub_name)
                if not sub_key:
                    continue
                await _repair_drained_tail(
                    main_agent,
                    thread_id_config,
                    messages_key=sub_key,
                    checkpoint_ns=sub_name,
                )
            return RunResult(text=await _state_text(main_agent, thread_id_config), drained=True)

        errors = [r for r in results if isinstance(r, BaseException)]
        if errors:
            raise errors[0]

        if not await stream.interrupted():
            final_state = await stream.output()
            return RunResult(text=str(final_state))

        interrupt_info = (await stream.interrupts())[0].value
        if on_interrupt is None:
            resume_value = ""
        else:
            resume_value = await on_interrupt(interrupt_info)
        user_input = Command(resume=resume_value)


async def _emit_message_stream(message, source: str, emit: Emit, sub_agent_names: set[str]):
    """按时间顺序 emit 一条消息的思考过程、正文，以及状态栏的「阶段」事件。

    直接迭代 message 的原始协议事件（replay-buffer），这样思考与正文会严格按
    模型输出的先后顺序交错发出（推理模型会出现「正文—思考—正文」的停顿），
    而不是像 projection 那样先整段思考、再整段正文。普通模型没有 reasoning
    事件，自动跳过。

    阶段（phase）判定：
    - message-start              → thinking（LLM 开始）
    - 首个 text-delta            → answering
    - 首个 tool_call_chunk       → 名称属于子 agent 则 delegate，否则 tool_edit
    - message-finish（含非子 agent 工具） → tool_wait
    """
    is_user = source == "main_user"
    text_started = False
    tool_names: list[str] = []
    tool_phase: str | None = None

    async def emit_phase(phase: str):
        if not is_user:
            await emit({"type": "phase", "source": source, "phase": phase})

    def note_tool_name(name: str) -> str | None:
        """记录 tool 名并返回该名对应的阶段；同类型阶段不重复发。"""
        nonlocal tool_phase
        if not name or name in tool_names:
            return None
        tool_names.append(name)
        phase = "delegate" if name in sub_agent_names else "tool_edit"
        if phase != tool_phase:
            tool_phase = phase
            return phase
        return None

    async for event in message:
        etype = event.get("event")
        if etype == "message-start":
            await emit_phase("thinking")
            continue
        if etype == "content-block-finish":
            block = event.get("content") or event.get("content_block")
            if isinstance(block, dict) and block.get("type") == "tool_call":
                phase = note_tool_name(block.get("name") or "")
                if phase:
                    await emit_phase(phase)
            continue
        if etype != "content-block-delta":
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
                if not text_started:
                    text_started = True
                    await emit_phase("answering")
                await emit({"type": "text", "source": source, "text": text})
        elif dtype == "reasoning-delta":
            r = delta.get("reasoning", "")
            if r:
                await emit({"type": "reasoning", "source": source, "text": r})
        elif dtype in ("block-delta", "legacy-block-delta"):
            fields = delta.get("fields")
            if isinstance(fields, dict) and fields.get("type") == "tool_call_chunk":
                phase = note_tool_name(fields.get("name") or "")
                if phase:
                    await emit_phase(phase)

    # 消息结束：只有「非子 agent 工具」才会让主 agent 停下来等工具执行；
    # 若全是子 agent，则保持 delegate，等子 agent 自己的阶段事件（状态下沉）。
    if tool_names and any(n not in sub_agent_names for n in tool_names):
        await emit_phase("tool_wait")


async def _consume_messages(stream, emit: Emit, sub_agent_names: set[str]):
    """主图（主 agent）的文本流（含思考过程）。"""
    async for message in stream.messages:
        is_user = message.node == "merge_human_message_with_memory"
        await _emit_message_stream(
            message, "main_user" if is_user else "main", emit, sub_agent_names
        )


async def _drain_subgraph(cursor, name: str, emit: Emit, sub_agent_names: set[str]):
    """消费单个子图 handle 的消息流（在独立任务里跑，实现并行子图并发接收）。"""
    try:
        async for message in cursor:
            await _emit_message_stream(message, f"sub:{name}", emit, sub_agent_names)
    finally:
        await emit({"type": "subgraph_end", "name": name})


async def _drain_subgraph_values(
    values_cursor, name: str, messages_key: str, emit: Emit
):
    """消费单个子图 handle 的状态快照，emit 子 agent 自己的 tool_call / tool_result。

    与主图 `_consume_values` 同一套逻辑，只是消息键换成子图的 `state_messages_key`，
    并把事件 source 标成 `sub:<name>`，前端据此放进对应的子 agent 分区。
    """
    await _consume_values(
        values_cursor, emit, source=f"sub:{name}", messages_key=messages_key, emit_phase=False
    )


async def _consume_subgraphs(
    stream, emit: Emit, sub_agent_names: set[str], sub_agent_state_keys: dict[str, str]
):
    """子图（子 agent）的文本流（含思考过程）与工具事件，支持多个子图并行。

    关键：LangGraph 的 StreamChannel 是 lazy-subscribe —— 只有在消息被推入其缓冲
    **之前**已订阅，才拿得到；否则并行子图早期的消息会被丢弃。因此这里在「读到
    handle」与「下一次 pump」之间**同步**调用 `__aiter__()` 完成订阅，再为每个
    handle 起独立任务并发消费（messages 与 values 各一个任务）。
    """
    tasks: list[asyncio.Task] = []
    try:
        async for subgraph in stream.subgraphs:
            name = subgraph.graph_name
            await emit({"type": "subgraph_start", "name": name})
            cursor = subgraph.messages.__aiter__()  # 同步订阅，防止并行子图丢消息
            tasks.append(
                asyncio.create_task(_drain_subgraph(cursor, name, emit, sub_agent_names))
            )
            # 子 agent 的工具事件同样要「读到 handle 立即同步订阅」，否则早期快照会丢。
            values_cursor = subgraph.values.__aiter__()
            messages_key = sub_agent_state_keys.get(name, "messages")
            tasks.append(
                asyncio.create_task(
                    _drain_subgraph_values(values_cursor, name, messages_key, emit)
                )
            )
        if tasks:
            await asyncio.gather(*tasks)
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()


async def _consume_values(
    values_iter,
    emit: Emit,
    *,
    source: str = "main",
    messages_key: str = "messages",
    emit_phase: bool = True,
):
    """从状态快照里抠 tool_call / tool 结果事件。

    主图与子图共用：主图 `messages_key="messages"`、`source="main"`；子图传入各自的
    `state_messages_key` 与 `sub:<name>`。只有主图需要 `emit_phase`（工具结束后补一条
    「回到思考中」的状态事件；子 agent 的状态由 `_emit_message_stream` 自己推进）。
    """
    seen_tool_ids: set[str] = set()
    seen_tool_result_ids: set[str] = set()
    first_snapshot = True
    async for snapshot in values_iter:
        msgs = snapshot.get(messages_key, [])
        if first_snapshot:
            # 第一个 values 快照是「本轮开始前的状态」（含历史）。把历史里已经存在的
            # tool_call / tool_result 记为已见，避免下一轮把它们当成本轮新事件再发一遍：
            # 典型场景是「上一轮停止时补了一条取消 ToolMessage」，用户再发消息时前端
            # 会先闪出一条旧的工具结果。
            # 只对主图做：子图的 values 首帧不保证是「输入前状态」，跳过可能丢首帧工具事件。
            first_snapshot = False
            if emit_phase:
                for msg in msgs:
                    mtype = getattr(msg, "type", "")
                    if mtype == "tool":
                        rid = getattr(msg, "tool_call_id", None) or getattr(msg, "id", None)
                        if rid:
                            seen_tool_result_ids.add(rid)
                    elif mtype == "ai":
                        for tc in (getattr(msg, "tool_calls", None) or []):
                            tid = tc.get("id")
                            if tid:
                                seen_tool_ids.add(tid)
                continue
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
            bid065_trace.mark(f"emit tool_result name={getattr(last, 'name', 'unknown')}")  # BID065
            await emit(
                {
                    "type": "tool_result",
                    "source": source,
                    "name": getattr(last, "name", "unknown"),
                    # 不再在此截断：展示长度交由前端「工具调用结果显示设置」控制
                    # （流式默认展示完整结果）。
                    "content": str(content),
                }
            )
            # 工具结果回流后，主 agent 即将再次 call_main_llm；显式回到「思考中」，
            # 消除「工具执行完毕」到「下一条 LLM 首个 token」之间的状态空档。
            if emit_phase:
                await emit({"type": "phase", "source": "main", "phase": "thinking"})
        elif last.type == "ai" and getattr(last, "tool_calls", None):
            for tc in last.tool_calls:
                tid = tc.get("id", "")
                if tid and tid not in seen_tool_ids:
                    seen_tool_ids.add(tid)
                    bid065_trace.mark(f"emit tool_call name={tc.get('name', '?')}")  # BID065
                    await emit(
                        {
                            "type": "tool_call",
                            "source": source,
                            "name": tc.get("name", "?"),
                            "args": tc.get("args", {}),
                        }
                    )
