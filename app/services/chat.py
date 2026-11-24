"""ChatManager：按 agent 管理常驻运行时，并驱动流式对话。"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack

from langchain_core.messages import HumanMessage

from app.config.store import ConfigStore
from app.config.models import MultiAgentConfig
from app.runtime.graph_builder import build_world
from app.runtime.persistence import build_persistence
from app.runtime.streaming import repair_tool_results as run_graph_tool_repair
from app.runtime.streaming import run_agent_stream
from app.services import threads as threads_service
from app.services.messages_json import dict_to_message


def make_user_input(text: str) -> dict:
    return {
        "messages": [HumanMessage(content=text)],
        "instructions_for_subagents": {},
        "instructions_ids": {},
        "subagents_reports_submit": {},
        "clean_every_and_all": False,
    }


def make_proactive_summary_input(summary_percent: float | None = None) -> dict:
    return {
        "messages": [],
        "proactive_summary_requested": True,
        # 非 None 表示前端已弹「百分比设置框」并确认，图内不再二次确认。
        "proactive_summary_percent": summary_percent,
        "instructions_for_subagents": {},
        "instructions_ids": {},
        "subagents_reports_submit": {},
        "clean_every_and_all": False,
    }


def make_proactive_summary_input_for_sub_agent(
    sub_agent_name: str, summary_percent: float | None = None
) -> dict:
    return {
        "messages": [],
        "proactive_summary_requested_for_specified_sub_agent": sub_agent_name,
        "proactive_summary_percent": summary_percent,
        "instructions_for_subagents": {},
        "instructions_ids": {},
        "subagents_reports_submit": {},
        "clean_every_and_all": False,
    }


def make_clean_all_input() -> dict:
    """构造「删除全部历史消息」的图输入（空消息触发，删除主图全部消息）。"""
    return {
        "messages": [],
        "clean_every_and_all": True,
        "instructions_for_subagents": {},
        "instructions_ids": {},
        "subagents_reports_submit": {},
    }


def make_edit_input(
    request_for_subagent: bool,
    subagent_name: str | None,
    msg_indice: int,
    substitute_msg_dict: dict,
) -> dict:
    """构造「编辑历史消息」的图输入（空消息触发，只改 msg_indice 那一条）。"""
    return {
        "messages": [],
        "request_to_edit_msg_in_the_past": {
            "request_or_not": True,
            "request_for_subagent": request_for_subagent,
            "subagent_name": subagent_name,
            "msg_indice": msg_indice,
            "substitute_msg": dict_to_message(substitute_msg_dict),
            "msg_list_cache": None,
        },
        "instructions_for_subagents": {},
        "instructions_ids": {},
        "subagents_reports_submit": {},
        "clean_every_and_all": False,
    }


def _load_history_settings(config_dir) -> dict:
    """读取影响历史浏览默认展开/折叠的全局设置。"""
    from app.config.settings import load_settings

    settings = load_settings(config_dir)
    return {
        "reasoning_expanded": settings.reasoning_expanded,
        "tool_call_expanded": settings.tool_call_expanded,
        "tool_result_expanded": settings.tool_result_expanded,
        "tool_result_full": settings.tool_result_history_full,
        "tool_result_max_lines": settings.tool_result_history_max_lines,
        "tool_result_max_chars": settings.tool_result_history_max_chars,
        # export_html 参数控制「是否在 .ai-msg-block 埋 data-md-b64 原文」，
        # 供前端「转换 HTML / 导出 md 文件」两个按钮共用，任一开启即埋。
        "export_html": settings.export_html or settings.export_md,
        # 聊天界面「图标及称谓自定义」（历史由后端渲染，故必须从这里传入）
        "labels": settings.chat_identity(),
    }


class AgentRuntime:
    def __init__(self, config: MultiAgentConfig, graph, stack: AsyncExitStack):
        self.config = config
        self.graph = graph
        self._stack = stack

    async def run(
        self,
        thread_id: str,
        user_input: dict,
        emit,
        on_interrupt=None,
        control=None,
        echo_user: bool = True,
    ):
        config_dict = {"configurable": {"thread_id": thread_id}}
        return await run_agent_stream(
            self.graph,
            config_dict,
            user_input,
            emit,
            on_interrupt,
            sub_agent_names={s.name for s in self.config.sub_agents},
            sub_agent_state_keys={s.name: s.state_messages_key for s in self.config.sub_agents},
            control=control,
            echo_user=echo_user,
        )

    async def repair_tool_results(self, thread_id: str) -> list[str]:
        """图内修复悬空 tool_call（削尾部 Human + 补 ToolMessage + 子图 + 复位 flag）。

        必须在上一轮 run 完全结束后调用（同一 thread 不能并发跑）；不流式、不发事件。
        返回被削掉的尾部用户消息文本（空列表表示啥也没删）。
        """
        return await run_graph_tool_repair(
            self.graph, {"configurable": {"thread_id": thread_id}}
        )

    async def close(self) -> None:
        await self._stack.aclose()


class ChatManager:
    def __init__(self, config_store: ConfigStore):
        self.config_store = config_store
        self._runtimes: dict[str, AgentRuntime] = {}
        # 纯本地历史操作（编辑历史消息 / 删除全部历史）专用的「不连接 MCP」运行时。
        # 单独一张表：它建出来的子图工具集和常驻运行时不同，绝不能混用。
        self._local_runtimes: dict[str, AgentRuntime] = {}
        self._build_locks: dict[str, asyncio.Lock] = {}
        self._local_build_locks: dict[str, asyncio.Lock] = {}

    def _lock(self, agent_id: str) -> asyncio.Lock:
        if agent_id not in self._build_locks:
            self._build_locks[agent_id] = asyncio.Lock()
        return self._build_locks[agent_id]

    def _local_lock(self, agent_id: str) -> asyncio.Lock:
        """纯本地运行时的构建锁，**必须与常驻运行时的锁分开**。

        常驻运行时的构建可能卡在「MCP 连接超时」上（几十秒）。如果两者共用一把锁，
        用户点「编辑模式」提交时就会在锁上排队等 MCP 超时——等于换了个地方被拖住，
        「不联网也能编辑」就名存实亡了。
        """
        if agent_id not in self._local_build_locks:
            self._local_build_locks[agent_id] = asyncio.Lock()
        return self._local_build_locks[agent_id]

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

    async def get_local_runtime(self, agent_id: str) -> AgentRuntime:
        """取「纯本地历史操作」用的运行时（编辑历史消息 / 删除全部历史）。

        这两类操作是**纯本地**的：它们只跑主图里的改写节点（``edit_msg_in_the_past*``
        / ``clean_all_msg`` / ``refill_msg_list_after_edit``），一个工具都不调用。但
        它们以前和聊天共用 ``get_runtime``，而 ``build_world`` 建图时必须先向每个
        子 agent 的 MCP server 要工具列表——MCP 一断线，编辑与「删除全部历史并存快照」
        就整条链不可用，用户只看到一句「无法初始化该会话的运行环境」。

        这里给它们建一个**同构但不连接任何 MCP** 的运行时（``mcp_tools_mode="skip"``）：
        图结构、checkpointer、store 与常驻运行时一致，写到同一个会话，因此编辑/删除
        的结果和之后正常聊天看到的历史完全一致。断网也能用。

        常驻运行时已存在时直接复用它（省一份图与连接池）。
        """
        if agent_id in self._runtimes:
            return self._runtimes[agent_id]
        if agent_id in self._local_runtimes:
            return self._local_runtimes[agent_id]

        async with self._local_lock(agent_id):
            if agent_id in self._runtimes:
                return self._runtimes[agent_id]
            if agent_id in self._local_runtimes:
                return self._local_runtimes[agent_id]
            config = self.config_store.load(agent_id)
            runtime = await self._build_runtime(config, local_only=True)
            self._local_runtimes[agent_id] = runtime
            return runtime

    def has_runtime(self, agent_id: str) -> bool:
        """常驻运行时是否已构建并缓存（warmup 判重 / 握手决定是否补一个 ready）。"""
        return agent_id in self._runtimes

    async def _build_runtime(
        self, config: MultiAgentConfig, *, local_only: bool = False
    ) -> AgentRuntime:
        from app.config.settings import load_settings

        settings = load_settings(self.config_store._dir)
        saver, store = await build_persistence(config)
        stack = AsyncExitStack()
        try:
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
                default_summary_percent=settings.summary_token_percent,
                search_memory_threshold=settings.search_memory_threshold,
                attach_memory_threshold=settings.attach_memory_threshold,
                pdf_table_extraction=settings.pdf_table_extraction,
                simplified_file_tools=settings.simplified_file_tools,
                mcp_tools_mode="skip" if local_only else "strict",
            )
        except BaseException:
            # 建图失败（MCP 连不上、库没起来、子 agent 配置非法…）必须把已经打开的
            # checkpointer / store 连接关掉。以前直接往外抛，AsyncExitStack 没有执行，
            # 失败的那次构建留下的数据库连接会一直挂着（server.log 里累积了几百次
            # 「构建运行时失败」，每次都在漏连接）。
            await stack.aclose()
            raise
        return AgentRuntime(config, graph, stack)

    async def invalidate(self, agent_id: str) -> None:
        """配置变更后使运行时失效，下次访问重建。"""
        runtime = self._runtimes.pop(agent_id, None)
        local = self._local_runtimes.pop(agent_id, None)
        closed: set[int] = set()
        for rt in (runtime, local):
            if rt is not None and id(rt) not in closed:
                closed.add(id(rt))
                await rt.close()

    async def invalidate_all(self) -> None:
        """使所有运行时失效（全局设置变更时使用）。"""
        runtimes = list(self._runtimes.values()) + list(self._local_runtimes.values())
        self._runtimes.clear()
        self._local_runtimes.clear()
        closed: set[int] = set()
        for runtime in runtimes:
            if id(runtime) in closed:
                continue
            closed.add(id(runtime))
            await runtime.close()

    # ── 线程数据访问（转发） ────────────────────────────────────
    async def list_threads(self, agent_id: str) -> list[dict]:
        config = self.config_store.load(agent_id)
        return await threads_service.list_threads(config.checkpoint_conn_string)

    async def register_thread(self, agent_id: str, thread_id: str) -> None:
        config = self.config_store.load(agent_id)
        await threads_service.register_thread(config.checkpoint_conn_string, thread_id)

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

    async def main_thread_messages(self, agent_id: str, thread_id: str) -> list:
        config = self.config_store.load(agent_id)
        return await threads_service.get_main_thread_messages(
            config.checkpoint_conn_string, thread_id
        )

    async def subgraph_messages_exact(self, agent_id: str, thread_id: str, node_name: str) -> list:
        config = self.config_store.load(agent_id)
        return await threads_service.get_subgraph_messages_exact(
            config.checkpoint_conn_string, thread_id, node_name
        )

    async def history_token_usage(
        self, agent_id: str, thread_id: str, sub_agent: str | None = None
    ) -> int:
        """当前历史消息的 token 总数（取最后一条 AIMessage 的 usage_metadata.total_tokens）。

        与图内 proactive_summary_get_usage_count 的取法保持一致，供前端百分比设置框
        预估「压缩前 / 压缩后」的 token 数量。
        """
        config = self.config_store.load(agent_id)
        return await threads_service.get_history_token_usage(
            config.checkpoint_conn_string, thread_id, sub_agent
        )

    # ── 会话导出 / 导入（Lite 只在 SQLite 模式下覆盖这三个方法）────────────

    async def thread_namespaces(self, agent_id: str, thread_id: str) -> list[str]:
        """该会话出现过的 checkpoint namespace（`''` = 主图，其余为子 agent 名）。"""
        config = self.config_store.load(agent_id)
        return await threads_service.list_thread_namespaces(
            config.checkpoint_conn_string, thread_id
        )

    async def export_thread_checkpoints(self, agent_id: str, thread_id: str) -> dict:
        """导出会话：每个 namespace 的最新 checkpoint（后端中立的 dict）。"""
        config = self.config_store.load(agent_id)
        return await threads_service.export_thread_checkpoints(
            config.checkpoint_conn_string, thread_id
        )

    async def import_thread_checkpoints(
        self, agent_id: str, thread_id: str, payload: dict
    ) -> list[str]:
        """把导出的会话数据写进 ``thread_id``（调用方保证该名字在新库中不冲突）。"""
        config = self.config_store.load(agent_id)
        return await threads_service.import_thread_checkpoints(
            config.checkpoint_conn_string, thread_id, payload
        )
