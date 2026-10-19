"""Lite 版运行时管理：只构建 checkpointer，不构建 embedding / store。"""

from __future__ import annotations

from contextlib import AsyncExitStack

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.config.settings import load_settings
from app.runtime.graph_builder import build_world
from app.services.chat import AgentRuntime, ChatManager


class LiteChatManager(ChatManager):
    async def _build_runtime(self, config):
        settings = load_settings(self.config_store._dir)

        lite_config = config.model_copy(deep=True)
        lite_config.main_agent.memory_tools_enabled = False

        saver = AsyncPostgresSaver.from_conn_string(lite_config.checkpoint_conn_string)
        stack = AsyncExitStack()
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
