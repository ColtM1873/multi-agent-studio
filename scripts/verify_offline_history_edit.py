"""离线验证：子 agent 的 MCP 连不上时，历史编辑 / 删除全部历史仍然可执行。

背景（本次改动）：编辑历史消息、删除全部历史（含「归档为快照后删除」）都是**纯本地
图操作**——只跑主图里的改写 / 清空节点，一个工具都不调用。但它们以前和聊天共用
``ChatManager.get_runtime``，而 ``build_world`` 建图时必须先向每个子 agent 的 MCP
server 要工具列表；MCP 一断线，这两条链路就整条不可用，用户只看到一句笼统的
「无法初始化该会话的运行环境」。

现在 ``build_world`` 支持 ``mcp_tools_mode="skip"``：完全不连接任何 MCP、不注入
任何工具，只建「主图 + 子图」骨架；``ChatManager.get_local_runtime`` 用它给编辑 /
删除提供一个独立的本地运行时。

本脚本不联网、不连数据库、不调 LLM：

1. ``mcp_tools_mode="strict"``（普通聊天）：假 MCP 一抛错，建图必须失败——
   保住「聊天缺工具不能静默」的现状；
2. ``mcp_tools_mode="skip"``：**一次都不调用 get_tools**，建图成功；
3. 用 skip 模式的图真正跑一遍：
   - 编辑主图历史消息（并且验证没有多跑 LLM）；
   - 编辑子 agent 历史消息；
   - 删除全部历史，之后还能继续对话。

运行：venv\\Scripts\\python.exe scripts\\verify_offline_history_edit.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from langchain_core.messages import AIMessage  # noqa: E402
from langgraph.checkpoint.memory import MemorySaver  # noqa: E402
from langgraph.store.memory import InMemoryStore  # noqa: E402

import app.runtime.graph_builder as gb  # noqa: E402
from app.config.models import (  # noqa: E402
    FileToolsConfig,
    MCPServerConfig,
    MainAgentConfig,
    MultiAgentConfig,
    PostgresConfig,
    SubAgentConfig,
)
from app.services.chat import (  # noqa: E402
    make_clean_all_input,
    make_edit_input,
    make_user_input,
)
from app.services.messages_json import message_to_dict  # noqa: E402

SUB_AGENT = "web_search_agent"
BROKEN_URL = "https://mcp.invalid.example/mcp"


class _ScriptedModel:
    """假模型：第 1 次让主 agent 调子 agent，第 2 次是子 agent 的回答，之后是主 agent 收尾。"""

    def __init__(self) -> None:
        self.calls = 0

    def bind_tools(self, tools):  # noqa: ANN001 - 兼容真实模型接口
        return self

    async def ainvoke(self, messages, **kwargs):  # noqa: ANN001
        self.calls += 1
        if self.calls == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": SUB_AGENT,
                        "args": {"instruction": "查一下明天的天气"},
                        "id": "call_sub_1",
                    }
                ],
            )
        if self.calls == 2:
            return AIMessage(content="子 agent 的回答：明天晴。")
        return AIMessage(content="主 agent 的收尾回答。")


class _BrokenMCPClient:
    """每次 get_tools 都抛错，模拟「MCP 服务器连不上」。"""

    def __init__(self) -> None:
        self.calls = 0

    async def get_tools(self, *, server_name=None):  # noqa: ANN001
        self.calls += 1
        raise ConnectionError("All connection attempts failed")


def _make_config() -> MultiAgentConfig:
    return MultiAgentConfig(
        agent_id="offline_history_edit",
        name="离线历史编辑验证",
        postgres=PostgresConfig(checkpoint_database="offline_history_edit"),
        main_agent=MainAgentConfig(
            system_prompt="你是主 agent。",
            api_key="sk-offline-test",
            file_tools=FileToolsConfig(enabled=False),
            memory_tools_enabled=False,
        ),
        sub_agents=[
            SubAgentConfig(
                name=SUB_AGENT,
                description="搜索工具",
                system_prompt="你是子 agent。",
                api_key="sk-offline-test",
                mcp_servers=[
                    MCPServerConfig(name="web_search", transport="http", url=BROKEN_URL)
                ],
            )
        ],
    )


def _install_model(model: _ScriptedModel) -> _ScriptedModel:
    """把 graph_builder 里的真实模型替换成脚本模型（主图 / 子图共用同一个实例）。"""
    gb._init_model = lambda *a, **k: model  # noqa: ARG005
    return model


async def _build(
    config: MultiAgentConfig,
    *,
    mode: str,
    client: _BrokenMCPClient,
    checkpointer=None,
):
    checkpointer = checkpointer if checkpointer is not None else MemorySaver()
    graph = await gb.build_world(
        config,
        store=InMemoryStore(),
        checkpointer=checkpointer,
        mcp_client_factory=lambda _servers: client,
        memory_attach=False,
        mcp_tools_mode=mode,
    )
    return graph, checkpointer


def _cfg(thread: str) -> dict:
    return {"configurable": {"thread_id": thread}}


async def _seed_history(graph, thread: str) -> None:
    """跑一轮「主 agent 委派子 agent」的正常对话，种下主图 + 子图历史。"""
    await graph.ainvoke(make_user_input("让子 agent 查一下明天的天气"), _cfg(thread))
    state = await graph.aget_state(_cfg(thread))
    main_msgs = state.values["messages"]
    assert len(main_msgs) >= 3, [type(m).__name__ for m in main_msgs]
    assert any(type(m).__name__ == "ToolMessage" for m in main_msgs), (
        "子 agent 报告没有回到主图"
    )


def _find_sub_namespace(checkpointer, thread: str) -> str:
    """在内存 checkpointer 里找子图的 checkpoint namespace。

    InMemorySaver.storage 是 ``thread_id -> checkpoint_ns -> checkpoint_id -> ...``，
    子图每跑一次都会在 ``<子 agent 名>:<任务 uuid>`` 这个 ns 下落 checkpoint。
    （主图 state 的 ``tasks`` 只在运行中才有，跑完就空了，所以不能靠它找。）
    """
    per_thread = getattr(checkpointer, "storage", {}).get(thread) or {}
    for ns in per_thread:
        if ns.startswith(SUB_AGENT):
            return ns
    raise AssertionError(f"找不到子图 namespace：{list(per_thread)}")


async def main() -> int:
    # 1) strict：MCP 一断，聊天用的运行时必须建不起来（现状不能改）
    _install_model(_ScriptedModel())
    broken = _BrokenMCPClient()
    try:
        await _build(_make_config(), mode="strict", client=broken)
    except RuntimeError as exc:
        text = str(exc)
        assert "MCP 服务器连接失败" in text, text
        assert SUB_AGENT in text and "web_search" in text, text
    else:
        raise AssertionError("strict 模式下 MCP 连不上，build_world 居然成功了")
    assert broken.calls == 1, broken.calls
    print("[OK] strict：MCP 连不上 → 建图失败（错误里带子 agent 与 server 名）")

    # 2) skip：完全不碰 MCP，图能建出来
    client = _BrokenMCPClient()
    config = _make_config()
    model = _install_model(_ScriptedModel())
    graph, checkpointer = await _build(config, mode="skip", client=client)
    assert client.calls == 0, f"skip 模式不应调用 get_tools，实际调用 {client.calls} 次"
    print("[OK] skip：一次都没调用 get_tools，图构建成功")

    # 3) 种历史（一轮正常对话）→ 编辑主图消息
    thread = "t-edit"
    await _seed_history(graph, thread)
    calls_before_edit = model.calls

    state = await graph.aget_state(_cfg(thread))
    main_msgs = state.values["messages"]
    first_human = main_msgs[0]
    assert type(first_human).__name__ == "HumanMessage", type(first_human).__name__
    substitute = message_to_dict(first_human)
    substitute["content"] = "【已编辑】让子 agent 查一下后天的天气"
    await graph.ainvoke(make_edit_input(False, None, 0, substitute), _cfg(thread))

    state = await graph.aget_state(_cfg(thread))
    edited = state.values["messages"][0]
    assert edited.content == "【已编辑】让子 agent 查一下后天的天气", edited.content
    assert state.values.get("request_to_edit_msg_in_the_past") is None
    assert len(state.values["messages"]) == len(main_msgs), (
        len(state.values["messages"]),
        len(main_msgs),
    )
    assert model.calls == calls_before_edit, "编辑主图消息不应该再调 LLM（改写+停）"
    print("[OK] skip：主图历史消息编辑成功（改完即停、无多余调用）")

    # 4) 编辑子 agent 消息
    sub_ns = _find_sub_namespace(checkpointer, thread)
    sub_key = config.sub_agents[0].state_messages_key
    assert sub_key, "state_messages_key 未分配"
    sub_state = await graph.aget_state(
        {"configurable": {"thread_id": thread, "checkpoint_ns": sub_ns}}
    )
    sub_msgs = sub_state.values.get(sub_key) or []
    assert len(sub_msgs) >= 2, [type(m).__name__ for m in sub_msgs]
    ai_index = next(i for i, m in enumerate(sub_msgs) if type(m).__name__ == "AIMessage")
    sub_ai = message_to_dict(sub_msgs[ai_index])
    sub_ai["content"] = "【已编辑】子 agent：后天有雨。"
    await graph.ainvoke(make_edit_input(True, SUB_AGENT, ai_index, sub_ai), _cfg(thread))

    state = await graph.aget_state(_cfg(thread))
    sub_ns = _find_sub_namespace(checkpointer, thread)
    sub_state = await graph.aget_state(
        {"configurable": {"thread_id": thread, "checkpoint_ns": sub_ns}}
    )
    sub_msgs_after = sub_state.values.get(sub_key) or []
    assert sub_msgs_after[ai_index].content == "【已编辑】子 agent：后天有雨。", (
        sub_msgs_after[ai_index].content
    )
    assert state.values.get("request_to_edit_msg_in_the_past") is None
    assert model.calls == calls_before_edit, "编辑子 agent 消息不应该再调 LLM（改写+停）"
    print("[OK] skip：子 agent 历史消息编辑成功")

    # 5) 删除全部历史（归档快照由 snapshot_service 负责、与图无关，这里只验证清空本身）
    await graph.ainvoke(make_clean_all_input(), _cfg(thread))
    state = await graph.aget_state(_cfg(thread))
    assert state.values["messages"] == [], state.values["messages"]
    assert state.values.get("clean_every_and_all") is False
    print("[OK] skip：删除全部历史成功")

    # 6) 清空后仍可继续对话，说明图结构完整、没有把别的路径弄坏
    await graph.ainvoke(make_user_input("清空后再问一次"), _cfg(thread))
    state = await graph.aget_state(_cfg(thread))
    assert len(state.values["messages"]) >= 1, state.values["messages"]
    print("[OK] skip：清空后仍可继续对话（图结构完整）")

    print("verify_offline_history_edit: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
