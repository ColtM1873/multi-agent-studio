"""ChatManager：按 agent 管理常驻运行时，并驱动流式对话。"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack

from langchain_core.messages import HumanMessage

from app.config.store import ConfigStore
from app.config.models import MultiAgentConfig
from app.runtime.graph_builder import build_world
from app.runtime.persistence import build_persistence
from app.runtime.streaming import run_agent_stream
from app.services import threads as threads_service


def make_user_input(text: str) -> dict:
    return {
        "messages": [HumanMessage(content=text)],
        "instructions_for_subagents": {},
        "instructions_ids": {},
        "subagents_reports_submit": {},
    }


def make_proactive_summary_input() -> dict:
    return {
        "messages": [],
        "proactive_summary_requested": True,
        "instructions_for_subagents": {},
        "instructions_ids": {},
        "subagents_reports_submit": {},
    }


def make_proactive_summary_input_for_sub_agent(sub_agent_name: str) -> dict:
    return {
        "messages": [],
        "proactive_summary_requested_for_specified_sub_agent": sub_agent_name,
        "instructions_for_subagents": {},
        "instructions_ids": {},
        "subagents_reports_submit": {},
    }


def _load_history_settings(config_dir) -> dict:
    """读取影响历史浏览默认展开/折叠的全局设置。"""
    from app.config.settings import load_settings

    settings = load_settings(config_dir)
    return {
        "reasoning_expanded": settings.reasoning_expanded,
        "tool_call_expanded": settings.tool_call_expanded,
        "tool_result_expanded": settings.tool_result_expanded,
        "export_html": settings.export_html,
    }


class AgentRuntime:
    def __init__(self, config: MultiAgentConfig, graph, stack: AsyncExitStack):
        self.config = config
        self.graph = graph
        self._stack = stack

    async def run(self, thread_id: str, user_input: dict, emit, on_interrupt=None) -> str:
        config_dict = {"configurable": {"thread_id": thread_id}}
        return await run_agent_stream(self.graph, config_dict, user_input, emit, on_interrupt)

    async def close(self) -> None:
        await self._stack.aclose()


class ChatManager:
    def __init__(self, config_store: ConfigStore):
        self.config_store = config_store
        self._runtimes: dict[str, AgentRuntime] = {}
        self._build_locks: dict[str, asyncio.Lock] = {}

    def _lock(self, agent_id: str) -> asyncio.Lock:
        if agent_id not in self._build_locks:
            self._build_locks[agent_id] = asyncio.Lock()
        return self._build_locks[agent_id]

    async def get_runtime(self, agent_id: str) -> AgentRuntime:
        if agent_id in self._runtimes:
            return self._runtimes[agent_id]

        async with self._lock(agent_id):
            if agent_id in self._runtimes:
                return self._runtimes[agent_id]
            config = self.config_store.load(agent_id)
            runtime = await self._build_runtime(config)
            self._runtimes[agent_id] = runtime
            return runtime

    async def _build_runtime(self, config: MultiAgentConfig) -> AgentRuntime:
        from app.config.settings import load_settings

        settings = load_settings(self.config_store._dir)
        saver, store = await build_persistence(config)
        stack = AsyncExitStack()
        checkpointer = await stack.enter_async_context(saver)
        store_ctx = await stack.enter_async_context(store)
        await checkpointer.setup()
        await store_ctx.setup()
        graph = await build_world(
            config,
            store=store_ctx,
            checkpointer=checkpointer,
            memory_attach=settings.memory_attach,
            num_memories_attached=settings.num_memories_attached,
        )
        return AgentRuntime(config, graph, stack)

    async def invalidate(self, agent_id: str) -> None:
        """配置变更后使运行时失效，下次访问重建。"""
        runtime = self._runtimes.pop(agent_id, None)
        if runtime is not None:
            await runtime.close()

    async def invalidate_all(self) -> None:
        """使所有运行时失效（全局设置变更时使用）。"""
        runtimes = list(self._runtimes.values())
        self._runtimes.clear()
        for runtime in runtimes:
            await runtime.close()

    # ── 线程数据访问（转发） ────────────────────────────────────
    async def list_threads(self, agent_id: str) -> list[dict]:
        config = self.config_store.load(agent_id)
        return await threads_service.list_threads(config.checkpoint_conn_string)

    async def delete_thread(self, agent_id: str, thread_id: str) -> None:
        config = self.config_store.load(agent_id)
        await threads_service.delete_thread(config.checkpoint_conn_string, thread_id)

    async def thread_history(self, agent_id: str, thread_id: str) -> str | None:
        config = self.config_store.load(agent_id)
        settings = _load_history_settings(self.config_store._dir)
        return await threads_service.get_thread_history_markdown(
            config.checkpoint_conn_string, thread_id, **settings
        )

    async def thread_subgraphs(self, agent_id: str, thread_id: str) -> list[dict]:
        config = self.config_store.load(agent_id)
        return await threads_service.list_subgraph_nodes(config.checkpoint_conn_string, thread_id)

    async def subgraph_history(self, agent_id: str, thread_id: str, node_name: str) -> str | None:
        config = self.config_store.load(agent_id)
        settings = _load_history_settings(self.config_store._dir)
        return await threads_service.get_subgraph_history_by_node(
            config.checkpoint_conn_string, thread_id, node_name, **settings
        )
