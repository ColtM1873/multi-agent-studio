"""openai_compatible（ChatOpenAI）捕获三方 reasoning_content 的离线验证（不调 LLM）。

覆盖：
1. 流式 chunk：reasoning_content 进入 additional_kwargs / content_blocks；
2. 同一 chunk 的 tool_call_chunks 不被新增的 translator 破坏；
3. 非流式响应（_create_chat_result）同样捕获；
4. 端到端：假 SSE 端点 + LangGraph + run_agent_stream，确认真的 emit 出
   {"type": "reasoning"}，且最终消息 content 里带 reasoning block；
5. 官方内置 deepseek 路径不受影响（ChatDeepSeek 自己解析）。

运行: python scripts/verify_openai_compat_reasoning.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Annotated, TypedDict

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.graph.message import add_messages

from app.runtime.openai_compat_reasoning import apply_openai_compat_reasoning_capture


def _chunk(**delta) -> dict:
    return {
        "id": "chatcmpl-x",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "fake",
        "choices": [{"index": 0, "delta": {"role": "assistant", **delta}, "finish_reason": None}],
    }


def check_chunk_capture(model) -> None:
    gen = model._convert_chunk_to_generation_chunk(
        _chunk(content="", reasoning_content="先想一下"), AIMessageChunk, None
    )
    message = gen.message
    assert message.additional_kwargs.get("reasoning_content") == "先想一下", message.additional_kwargs
    assert message.content_blocks == [
        {"type": "reasoning", "reasoning": "先想一下"}
    ], message.content_blocks
    print("[OK] 流式 chunk：reasoning_content → additional_kwargs + reasoning block")

    tool_gen = model._convert_chunk_to_generation_chunk(
        _chunk(
            content="",
            reasoning_content="查天气",
            tool_calls=[
                {
                    "index": 0,
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "weather", "arguments": '{"city":'},
                }
            ],
        ),
        AIMessageChunk,
        None,
    )
    block_types = [b.get("type") for b in tool_gen.message.content_blocks]
    assert "reasoning" in block_types and "tool_call_chunk" in block_types, block_types
    print("[OK] 同一 chunk 的 tool_call_chunk 与 reasoning block 共存（未破坏官方 translator）")


def check_nonstream_capture(model) -> None:
    response = {
        "id": "chatcmpl-x",
        "object": "chat.completion",
        "created": 0,
        "model": "fake",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "正文", "reasoning_content": "思考"},
                "finish_reason": "stop",
            }
        ],
    }
    result = model._create_chat_result(response)
    message = result.generations[0].message
    assert message.additional_kwargs.get("reasoning_content") == "思考", message.additional_kwargs
    assert message.content_blocks[0] == {"type": "reasoning", "reasoning": "思考"}, message.content_blocks
    print("[OK] 非流式响应：reasoning_content → additional_kwargs + reasoning block")


# --- 端到端：假 SSE 端点 -------------------------------------------------------

_SSE_CHUNKS = [
    {"delta": {"content": "", "reasoning_content": "让我想想"}, "finish_reason": None},
    {"delta": {"content": "", "reasoning_content": "……想清楚了。"}, "finish_reason": None},
    {"delta": {"content": "你好，"}, "finish_reason": None},
    {"delta": {"content": "我是助手。"}, "finish_reason": None},
    {"delta": {"content": ""}, "finish_reason": "stop"},
]


class _SSEHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("content-length") or 0))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for c in _SSE_CHUNKS:
            body = {
                "id": "chatcmpl-x",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "fake",
                "choices": [{"index": 0, **c}],
            }
            self.wfile.write(("data: " + json.dumps(body, ensure_ascii=False) + "\n\n").encode())
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, *args):  # noqa: D401
        pass


class _State(TypedDict):
    messages: Annotated[list, add_messages]


def _build_graph(base_url: str, patch: bool):
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph

    model = ChatOpenAI(model="fake", api_key="x", base_url=base_url)
    assert getattr(model, "_openai_compat_reasoning_capture_applied", False) is False
    if patch:
        apply_openai_compat_reasoning_capture(model)
        assert getattr(model, "_openai_compat_reasoning_capture_applied", False) is True

    # 生产链路用的是 bind_tools 后的 RunnableBinding；补丁挂在同一实例上，必须穿透。
    runner = model.bind_tools([])
    assert getattr(runner, "bound", None) is model, "补丁应穿透 bind_tools"

    async def node(state):
        return {"messages": [await runner.ainvoke(state["messages"])]}

    graph = StateGraph(_State)
    graph.add_node("llm", node)
    graph.add_edge(START, "llm")
    graph.add_edge("llm", END)
    return graph.compile(checkpointer=MemorySaver())


async def _run_graph(app) -> tuple[list[dict], AIMessage]:
    from app.runtime.streaming import run_agent_stream

    events: list[dict] = []

    async def emit(event: dict) -> None:
        events.append(event)

    await run_agent_stream(
        app, {"configurable": {"thread_id": "t1"}}, {"messages": [HumanMessage("hi")]}, emit
    )
    snapshot = await app.aget_state({"configurable": {"thread_id": "t1"}})
    return events, snapshot.values["messages"][-1]


def main() -> None:
    model = ChatOpenAI(model="fake", api_key="x")
    apply_openai_compat_reasoning_capture(model)
    check_chunk_capture(model)
    check_nonstream_capture(model)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _SSEHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    try:
        # 未打补丁：不应该出现 reasoning 事件（复现原缺陷）
        plain_events, plain_message = asyncio.run(_run_graph(_build_graph(base_url, patch=False)))
        assert not [e for e in plain_events if e.get("type") == "reasoning"], plain_events
        assert isinstance(plain_message.content, list)
        assert all(b.get("type") != "reasoning" for b in plain_message.content), plain_message.content
        print("[OK] 未打补丁（基线）：流式无 reasoning 事件、最终消息无 reasoning block")

        # 打补丁：流式有 reasoning 事件，最终消息带 reasoning block
        events, message = asyncio.run(_run_graph(_build_graph(base_url, patch=True)))
        reasoning = [e for e in events if e.get("type") == "reasoning"]
        assert [e["text"] for e in reasoning] == ["让我想想", "……想清楚了。"], reasoning
        assert all(e.get("source") == "main" for e in reasoning), reasoning
        text = [e for e in events if e.get("type") == "text"]
        assert "".join(e["text"] for e in text) == "你好，我是助手。", text
        assert isinstance(message.content, list), message.content
        assert message.content[0] == {
            "type": "reasoning",
            "reasoning": "让我想想……想清楚了。",
            "index": 0,
        }, message.content
        assert message.additional_kwargs.get("reasoning_content") == "让我想想……想清楚了。"
        print("[OK] 打补丁：流式 emit reasoning 事件 + 最终消息 content 带 reasoning block")

        # 官方内置 deepseek 不受影响（ChatDeepSeek 自己解析，没有我们的标记）
        from app.config.models import ModelConfig
        from app.runtime.graph_builder import _init_model

        official = _init_model("deepseek:deepseek-v4-pro", "fake", ModelConfig())
        assert getattr(official, "_openai_compat_reasoning_capture_applied", False) is False
        print("[OK] 官方内置 deepseek 路径未被改动")

        # openai_compatible 经 _init_model 自动挂补丁（主/子 agent 同一入口）
        compat = _init_model(
            "some-model", "fake",
            ModelConfig(provider_mode="openai_compatible", base_url="https://example.com/v1"),
        )
        assert getattr(compat, "_openai_compat_reasoning_capture_applied", False) is True
        print("[OK] openai_compatible 经 _init_model 自动挂补丁")
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
