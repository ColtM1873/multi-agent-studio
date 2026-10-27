"""Lite 版运行时管理：只构建 checkpointer，不构建 embedding / store。

两种会话历史后端（安装时决定，见 ``app.config.storage_backend``）：

- ``postgres``：与普通版一致，使用 ``AsyncPostgresSaver``；
- ``sqlite``：使用 ``AsyncSqliteSaver``（每 agent 一个文件，单连接串行）。

SQLite 模式下线程列表 / 历史 / 快照等读操作也走同一条连接，避免跨连接写冲突，
见 ``lite.app.sqlite_store`` 的模块说明。
"""

from __future__ import annotations

from contextlib import AsyncExitStack

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.config import storage_backend as storage
from app.config.settings import load_settings
from app.runtime.graph_builder import build_world
from app.services.chat import AgentRuntime, ChatManager, _load_history_settings
from lite.app import sqlite_store


class LiteChatManager(ChatManager):
    def _use_sqlite(self) -> bool:
        return storage.is_sqlite(self.config_store._dir)

    async def _build_runtime(self, config):
        settings = load_settings(self.config_store._dir)

        lite_config = config.model_copy(deep=True)
        lite_config.main_agent.memory_tools_enabled = False

        stack = AsyncExitStack()
        if self._use_sqlite():
            # 连接由 sqlite_store 统一持有（跨运行时/线程查询复用），不放进 stack，
            # 否则 invalidate/重建运行时会把正在使用的连接关掉。
            checkpointer = await sqlite_store.get_saver(lite_config.agent_id)
        else:
            saver = AsyncPostgresSaver.from_conn_string(
                lite_config.checkpoint_conn_string
            )
            checkpointer = await stack.enter_async_context(saver)
            await checkpointer.setup()

        graph = await build_world(
            lite_config,
            store=None,
            checkpointer=checkpointer,
            memory_attach=False,
            num_memories_attached=0,
            default_summary_percent=settings.summary_token_percent,
            search_memory_threshold=settings.search_memory_threshold,
            attach_memory_threshold=settings.attach_memory_threshold,
            pdf_table_extraction=settings.pdf_table_extraction,
        )
        return AgentRuntime(lite_config, graph, stack)

    # ── 线程数据访问：SQLite 模式走 sqlite_store，PostgreSQL 模式继承普通实现 ──

    async def list_threads(self, agent_id: str) -> list[dict]:
        if self._use_sqlite():
            return await sqlite_store.list_threads(agent_id)
        return await super().list_threads(agent_id)

    async def register_thread(self, agent_id: str, thread_id: str) -> None:
        if self._use_sqlite():
            await sqlite_store.register_thread(agent_id, thread_id)
            return
        await super().register_thread(agent_id, thread_id)

    async def delete_thread(self, agent_id: str, thread_id: str) -> None:
        if self._use_sqlite():
            await sqlite_store.delete_thread(agent_id, thread_id)
            return
        await super().delete_thread(agent_id, thread_id)

    async def thread_history(self, agent_id: str, thread_id: str) -> str | None:
        if self._use_sqlite():
            settings = _load_history_settings(self.config_store._dir)
            return await sqlite_store.get_thread_history_markdown(
                agent_id, thread_id, **settings
            )
        return await super().thread_history(agent_id, thread_id)

    async def thread_subgraphs(self, agent_id: str, thread_id: str) -> list[dict]:
        if self._use_sqlite():
            return await sqlite_store.list_subgraph_nodes(agent_id, thread_id)
        return await super().thread_subgraphs(agent_id, thread_id)

    async def subgraph_history(
        self, agent_id: str, thread_id: str, node_name: str
    ) -> str | None:
        if self._use_sqlite():
            settings = _load_history_settings(self.config_store._dir)
            return await sqlite_store.get_subgraph_history_by_node(
                agent_id, thread_id, node_name, **settings
            )
        return await super().subgraph_history(agent_id, thread_id, node_name)

    async def main_thread_messages(self, agent_id: str, thread_id: str) -> list:
        if self._use_sqlite():
            return await sqlite_store.get_main_thread_messages(agent_id, thread_id)
        return await super().main_thread_messages(agent_id, thread_id)

    async def subgraph_messages_exact(
        self, agent_id: str, thread_id: str, node_name: str
    ) -> list:
        if self._use_sqlite():
            return await sqlite_store.get_subgraph_messages_exact(
                agent_id, thread_id, node_name
            )
        return await super().subgraph_messages_exact(agent_id, thread_id, node_name)

    async def history_token_usage(
        self, agent_id: str, thread_id: str, sub_agent: str | None = None
    ) -> int:
        if self._use_sqlite():
            return await sqlite_store.get_history_token_usage(
                agent_id, thread_id, sub_agent
            )
        return await super().history_token_usage(agent_id, thread_id, sub_agent)
