"""由配置构建 LangGraph 主图 + 子图（重构自 World_function_new.py / sub_agent_sub_graph_factory_new.py）。"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any, Literal

from langchain.chat_models import init_chat_model
from langchain_community.agent_toolkits.file_management.toolkit import FileManagementToolkit
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage
from langchain_core.runnables.config import RunnableConfig
from langchain_core.tools import tool as langchain_tool
from langchain_core.tools.structured import StructuredTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.graph.state import CompiledStateGraph, StateGraph, START, END
from langgraph.types import Command, RetryPolicy, interrupt

from app.config.models import (
    DEFAULT_HTML_REPORT_PROMPT,
    MCPServerConfig,
    ModelConfig,
    MultiAgentConfig,
    SubAgentConfig,
)
from app.runtime.prompts import MEMORY_ATTACH_MARKER, USER_MSG_PREFIX, ReAct_system_prompt, summary_prompt_generator, subagent_call_prompt, summary_prompt_prefix,trimmed_summary_prompt
from app.runtime.state_factory import make_main_state, make_sub_agent_state
from app.services import snapshot as snapshot_service

logger = logging.getLogger(__name__)

SEARCH_MEMORY_THRESHOLD = 0.5
ATTACH_MEMORY_THRESHOLD = 0.7


async def _try_capture_snapshot(runnable_config, conn_string: str, agent_id: str) -> None:
    """在真正清空历史前保存一份快照；任何异常都吞掉，绝不影响总结主流程。"""
    try:
        configurable = (runnable_config or {}).get("configurable", {})
        thread_id = configurable.get("thread_id")
        if not thread_id:
            return
        await snapshot_service.capture_snapshot(conn_string, thread_id, agent_id)
    except Exception:  # noqa: BLE001
        logger.exception("生成快照失败 (agent_id=%s)", agent_id)

def make_sub_agent_tool(name: str, description: str) -> StructuredTool:
    if not name or not name.strip():
        raise ValueError("子 agent 名称不能为空（name 为空会导致工具名回退为函数名）")

    async def fake_function(instruction: Annotated[str, "用自然语言描述你要委托给子 agent 的任务"]) -> str:
        pass

    return StructuredTool.from_function(
        name=name,
        description=description,
        coroutine=fake_function,
    )


def build_mcp_client(servers: list[MCPServerConfig]) -> MultiServerMCPClient:
    config: dict[str, Any] = {}
    for s in servers:
        if s.transport == "http":
            config[s.name] = {"transport": "http", "url": s.url}
        else:
            entry: dict[str, Any] = {"transport": "stdio", "command": s.command}
            if s.args:
                entry["args"] = s.args
            if s.env:
                entry["env"] = s.env
            config[s.name] = entry
    return MultiServerMCPClient(config)


def _init_model(llm_provider_name: str, api_key: str, model_cfg: ModelConfig):
    """按 ModelConfig 构造 init_chat_model。

    - 标准采样参数（temperature/top_p/max_tokens）作为顶层 kwargs 传入。
    - 非标准参数（top_k/repetition_penalty）通过 model_kwargs 透传，
      交由底层 API 自行解释（各 provider 支持程度不一）。
    - openai_compatible 模式固定 model_provider="openai" 并携带 base_url。
    """
    kwargs: dict[str, Any] = {}
    if model_cfg.temperature is not None:
        kwargs["temperature"] = model_cfg.temperature
    if model_cfg.top_p is not None:
        kwargs["top_p"] = model_cfg.top_p
    if model_cfg.max_tokens is not None:
        kwargs["max_tokens"] = model_cfg.max_tokens

    model_kwargs: dict[str, Any] = {}
    if model_cfg.top_k is not None:
        model_kwargs["top_k"] = model_cfg.top_k
    if model_cfg.repetition_penalty is not None:
        model_kwargs["repetition_penalty"] = model_cfg.repetition_penalty
    if model_kwargs:
        kwargs["model_kwargs"] = model_kwargs

    if model_cfg.provider_mode == "openai_compatible":
        return init_chat_model(
            model=llm_provider_name,
            api_key=api_key,
            model_provider="openai",
            base_url=model_cfg.base_url,
            **kwargs,
        )
    return init_chat_model(model=llm_provider_name, api_key=api_key, **kwargs)


async def build_sub_agent(
    spec: SubAgentConfig,
    tools_mcp_client,
    state_messages_key: str,
    history_token_measure_key: str,
    extracted_summary_ai_msg_key: str,
    checkpoint_conn_string: str,
    agent_id: str,
    state_type,
) -> CompiledStateGraph:
    """构建单个子 agent 的子图（checkpointer=True）。"""
    sub_agent_name = spec.name
    agent_system_prompt = spec.system_prompt
    llm_provider_name = spec.llm_provider_name
    model_api_key = spec.api_key
    flush_threshold = spec.summary.flush_history_tokenwise
    reserve_rounds = spec.summary.reserve_message_round

    tools = None
    try:
        tools = await tools_mcp_client.get_tools()
    except Exception as e:
        servers = ", ".join(f"{s.name} ({s.transport})" for s in spec.mcp_servers)
        raise RuntimeError(
            f"子 agent [{sub_agent_name}] 的 MCP 服务器连接失败：[{servers}]，请确认这些服务已启动。原始错误: {e}"
        ) from e
    model = _init_model(llm_provider_name, model_api_key, spec.model)
    model_with_tools = model.bind_tools(tools)
    tools_by_name = {tool.name: tool for tool in tools}

    react_prompt = ReAct_system_prompt if spec.react_prompt else ""

    async def call_llm(state):
        existing = state.get(state_messages_key, [])
        concate = [SystemMessage(content=agent_system_prompt + react_prompt)] + existing
        response = await model_with_tools.ainvoke(concate)
        return {state_messages_key: [response]}

    builder = StateGraph(state_type)
    builder.add_node("call_llm", call_llm)

    async def period_summarize_evaluate(state):
        msg_list = state[state_messages_key]
        last_ai_msg = None
        for message_iter in range(len(msg_list) - 1, -1, -1):
            msg_here = msg_list[message_iter]
            if isinstance(msg_here, AIMessage):
                last_ai_msg = msg_here
                break
        if not last_ai_msg:
            return Command(goto="receiving_instruction")
        usage_metadata = last_ai_msg.usage_metadata or {}
        usage_count = usage_metadata.get("total_tokens", 0)
        if usage_count >= flush_threshold:
            return Command(goto="final_summarization_prompt",
                           update= {history_token_measure_key :usage_count })
        return Command(goto="receiving_instruction")

    async def final_summarization_prompt(state, config: RunnableConfig):
        await _try_capture_snapshot(config, checkpoint_conn_string, agent_id)
        usage_count = state[history_token_measure_key]
        summary_prompt = summary_prompt_generator(usage_count=usage_count , proactive_flush= False)
        return {state_messages_key: [HumanMessage(content=summary_prompt)]}
    
    async def proactive_final_summarization_prompt(state, config: RunnableConfig):
        await _try_capture_snapshot(config, checkpoint_conn_string, agent_id)
        usage_count = state[history_token_measure_key]
        summary_prompt = summary_prompt_generator(usage_count=usage_count , proactive_flush= True)
        return {state_messages_key: [HumanMessage(content=summary_prompt)]}


    async def final_history_flush(state): 
        reserve_msg_rounds = reserve_rounds
        msg_list = state[state_messages_key]
        len_msg_list = len(msg_list)
        summary_human_msg_iter = None
        # locate last summary HumanMessage
        for msg_iter in range(len_msg_list - 1, -1 , -1):
            msg_here = msg_list[msg_iter]
            if isinstance(msg_here, HumanMessage):
                msg_str = msg_here.content
                if msg_str.strip().startswith(
                    summary_prompt_prefix.strip()
                ):
                    summary_human_msg_iter = msg_iter
                    break
                
        beginning_human_iter = summary_human_msg_iter
        if reserve_msg_rounds:
            for msg_iter in range(summary_human_msg_iter - 1, -1, -1): #倒序查找HumanMessage
                if isinstance(msg_list[msg_iter], HumanMessage):
                    reserve_msg_rounds -= 1
                    if not reserve_msg_rounds:
                        beginning_human_iter = msg_iter  #beginning_human_iter mains after flush, the beginning human msg iter
                        break
        if beginning_human_iter <= 0: # reserve too much
            return {}
        msg_list_to_delete = msg_list[0:beginning_human_iter] + msg_list[summary_human_msg_iter:]
        extract_ai_summary_msg = msg_list[-1].content
        extract_ai_summary_msg_as_str = None
        for msg_dict in extract_ai_summary_msg:
            if msg_dict["type"] == "text":
                extract_ai_summary_msg_as_str = msg_dict["text"]
                break

        return {
            state_messages_key: [ RemoveMessage(id=msg.id) for msg in msg_list_to_delete  ],
            extracted_summary_ai_msg_key : extract_ai_summary_msg_as_str,
            history_token_measure_key :0,
            }
    async def refill_summary_msg(state):
        summary_text = state[extracted_summary_ai_msg_key] or ""
        return {
            state_messages_key:[
                HumanMessage(content= trimmed_summary_prompt),
                AIMessage(content=summary_text),
            ]
        }
    async def should_continue_main_summary_final(state):
        if state[state_messages_key][-1].tool_calls:
            return Command(goto="kill_tools_final")
        return Command(goto="final_history_flush")
    
    async def proactive_should_continue_main_summary_final(state):
        if state[state_messages_key][-1].tool_calls:
            return Command(goto="proactive_kill_tools_final")
        return Command(goto="proactive_final_history_flush")
    
    async def kill_tool(state):
        result = []
        for tool_call in state[state_messages_key][-1].tool_calls:
            observation = "Tool call is not allowed during the stage of making summarization."
            result.append(
                ToolMessage(content=observation, tool_call_id=tool_call["id"], name=tool_call["name"])
            )
        return {state_messages_key: result}

#instruction 不用消费后置空，因为如果 新的instruction没有overwrite，意味着 主agent根本没有 toolcall指向该子agent
#该子agent也就不会被调用，也就不会取到 过时的或者已经消费的 instruction

    async def receiving_instruction(state):
        instruction = state["instructions_for_subagents"][sub_agent_name]["args"]["instruction"]
        return {state_messages_key: [HumanMessage(content=instruction + "\n")]}

    async def route_to_proactive_summary(state):
        proactive_summary_bool = state["instructions_for_subagents"][sub_agent_name]["proactive_summary"]
        if proactive_summary_bool:
            return Command(goto="proactive_summary_confirm")
        return Command(goto="period_summarize_evaluate")


    async def proactive_summary_confirm(state):
        user_opinion = interrupt(f"是否对子agent,{sub_agent_name},进行主动全量总结？注意：会剥离目前所有会话历史（除了设置的保留会话轮数）。")
        if user_opinion == "yes":
            return Command(goto="proactive_summary_get_usage_count")
        return Command(goto=END)

    async def proactive_summary_get_usage_count(state):
        msg_list = state[state_messages_key]
        last_ai_msg = None
        for message_iter in range(len(msg_list) - 1, -1, -1):
            msg_here = msg_list[message_iter]
            if isinstance(msg_here, AIMessage):
                last_ai_msg = msg_here
                break
        usage_metadata = (last_ai_msg.usage_metadata or {}) if last_ai_msg else {}
        usage_count = usage_metadata.get("total_tokens", 0)
        return Command(goto="proactive_final_summarization_prompt",
                        update= {history_token_measure_key :usage_count })

    builder.add_node("receiving_instruction", receiving_instruction)

    (
        builder
        .add_node("period_summarize_evaluate", period_summarize_evaluate)
        .add_node("proactive_summary_get_usage_count",proactive_summary_get_usage_count)
        .add_node("route_to_proactive_summary",route_to_proactive_summary)
        .add_node("should_continue_main_summary_final",should_continue_main_summary_final)
        .add_node("proactive_should_continue_main_summary_final",proactive_should_continue_main_summary_final)

        .add_node("proactive_summary_confirm",proactive_summary_confirm)
        .add_node("final_summarization_prompt", final_summarization_prompt)
        .add_node("proactive_final_summarization_prompt",proactive_final_summarization_prompt)
        .add_node("proactive_final_summarize", call_llm)
        .add_node("final_summarize", call_llm)

        .add_node("kill_tools_final", kill_tool)
        .add_node("proactive_kill_tools_final", kill_tool)

        .add_node("final_history_flush", final_history_flush)
        .add_node("proactive_final_history_flush", final_history_flush)

        .add_node("refill_summary_msg",refill_summary_msg)
        .add_node("proactive_refill_summary_msg",refill_summary_msg)


        .add_edge(START, "route_to_proactive_summary")
        .add_edge("final_summarization_prompt", "final_summarize")
        .add_edge("proactive_final_summarization_prompt", "proactive_final_summarize")

        .add_edge("final_summarize", "should_continue_main_summary_final")
        .add_edge("proactive_final_summarize", "proactive_should_continue_main_summary_final")


        .add_edge("final_history_flush","refill_summary_msg")
        .add_edge("proactive_final_history_flush","proactive_refill_summary_msg")

        .add_edge("kill_tools_final", "final_summarize")
        .add_edge("proactive_kill_tools_final", "proactive_final_summarize")

        .add_edge("proactive_refill_summary_msg", END)
        .add_edge("refill_summary_msg", "receiving_instruction")
    )

    async def tool_node_fn(state):
        result = []
        for tc in state[state_messages_key][-1].tool_calls:
            if tc["name"] not in tools_by_name:
                result.append(
                    ToolMessage(
                        content="tool call invalid, tools could be dynamically attached or removed, please make sure this tool still remains in your tool_list",
                        tool_call_id=tc["id"],
                        name=tc["name"],
                    )
                )
                continue
            tool = tools_by_name[tc["name"]]
            try:
                observation = await tool.ainvoke(tc["args"])
                result.append(ToolMessage(content=observation, tool_call_id=tc["id"], name=tc["name"]))
            except Exception as e:
                result.append(ToolMessage(content=str(e), tool_call_id=tc["id"], name=tc["name"]))
        return {state_messages_key: result}

    async def should_continue(state) -> Literal["tool_node", "submit_report"]:
        if state[state_messages_key][-1].tool_calls:
            return "tool_node"
        return "submit_report"

    async def submit_report(state):
        return {"subagents_reports_submit": {sub_agent_name: state[state_messages_key][-1].content}}

    builder.add_node("tool_node", tool_node_fn)
    builder.add_node("submit_report", submit_report)
    builder.add_edge("receiving_instruction", "call_llm")
    builder.add_conditional_edges("call_llm", should_continue, {"tool_node": "tool_node", "submit_report": "submit_report"})
    builder.add_edge("tool_node", "call_llm")
    builder.add_edge("submit_report", END)

    return builder.compile(checkpointer=True)


async def build_world(
    config: MultiAgentConfig,
    *,
    store,
    checkpointer,
    mcp_client_factory=None,
    memory_attach: bool = False,
    num_memories_attached: int = 3,
) -> CompiledStateGraph:
    """构建 Supervisor-Worker 主图（等价于原来的 TheWorld）。

    调用方需确保 checkpointer/store 已 setup。
    mcp_client_factory: 可选，用于注入 MCP 客户端（默认 build_mcp_client），
    便于离线测试时替换为假客户端。
    """
    store_namespace = config.postgres.store_namespace

    # 为每个子 agent 生成唯一消息键（若尚未生成）
    from app.runtime.state_factory import (
        assign_extracted_summary_ai_msg_keys,
        assign_history_token_measure_keys,
        assign_state_messages_keys,
    )

    assign_state_messages_keys(config)
    assign_history_token_measure_keys(config)
    assign_extracted_summary_ai_msg_keys(config)

    factory = mcp_client_factory or build_mcp_client

    main_spec = config.main_agent
    html_report = main_spec.html_report
    html_report_prompt = main_spec.html_report_prompt or DEFAULT_HTML_REPORT_PROMPT

    snapshot_conn_string = config.checkpoint_conn_string
    snapshot_agent_id = config.agent_id

    file_toolkit = FileManagementToolkit(root_dir=main_spec.file_tools.root_dir)
    pass_in_tools = file_toolkit.get_tools()

    sub_agent_specs_list = config.sub_agents
    num_of_sub_agents = len(sub_agent_specs_list)

    sub_agent_tools = [make_sub_agent_tool(s.name, s.description) for s in sub_agent_specs_list]
# tool_name is exactly subagent name, is exactly the sub_graph node name


    sub_agent_sub_graphs = []
    for spec in sub_agent_specs_list:
        mcp_client = factory(spec.mcp_servers)
        state_type = make_sub_agent_state(
            spec.state_messages_key,
            spec.history_token_measure_key,
            spec.extracted_summary_ai_msg_key,
        )
        sub_agent_sub_graphs.append(
            await build_sub_agent(
                spec,
                mcp_client,
                spec.state_messages_key,
                spec.history_token_measure_key,
                spec.extracted_summary_ai_msg_key,
                snapshot_conn_string,
                snapshot_agent_id,
                state_type,
            )
        )

    sub_agent_dict = {
        sub_agent_specs_list[i].name: sub_agent_sub_graphs[i] for i in range(num_of_sub_agents)
    }
    main_model = _init_model(main_spec.llm_provider_name, main_spec.api_key, main_spec.model)

    main_system_prompt = main_spec.system_prompt

    # ── 记忆工具 ────────────────────────────────────────────────
    @langchain_tool
    async def write_memory(
        subject: Annotated[str, "What this memory is about (the subject)"],
        content: Annotated[str, "The plain content of the memory"],
    ) -> str:
        """用于写入记忆，可以写入用户偏好，重要知识，或者任何你认为需要长期存储，需要时复用的信息。支持一轮会话多次调用该工具"""
        await store.aput(store_namespace, str(uuid.uuid4()), {subject: content})
        return "write_in success"

    @langchain_tool
    async def read_memory(
        query: Annotated[str, "what memory you want to read about?"],
        number_of_retrieval: Annotated[int, "how many semantic-similar memories to return"],
    ) -> str:
        """用于读取记忆，给出你想要读取什么记忆（采用语义相似匹配（Maximun Inner Product Search））支持一轮会话多次调用该工具"""
        # SEARCH_MEMORY_THRESHOLD = 0.6
        outcome = await store.asearch(store_namespace, query=query, limit=number_of_retrieval)
        memories = ""
        for piece in outcome:
            if piece.score is None or piece.score < SEARCH_MEMORY_THRESHOLD:
                continue
            for key, value in piece.value.items():
                memories = memories + "\n" + key + ":" + value
        return memories if memories else "还没有相关记忆存在。"

    @langchain_tool
    async def attach_memory_automatically(
        query: Annotated[str, "what memory you want to read about?"],
        number_of_retrieval: Annotated[int, "how many semantic-similar memories to return"],
    ) -> str:
        """用于读取记忆，给出你想要读取什么记忆（采用语义相似匹配（Maximun Inner Product Search））支持一轮会话多次调用该工具"""
        # ATTACH_MEMORY_THRESHOLD = 0.6
        outcome = await store.asearch(store_namespace, query=query, limit=number_of_retrieval)
        memories = ""
        for piece in outcome:
            if piece.score is None or piece.score < ATTACH_MEMORY_THRESHOLD:
                continue
            for key, value in piece.value.items():
                memories = memories + "\n" + key + ":" + value
        return memories

    memory_tools = [write_memory, read_memory]
    non_agent_tools = memory_tools + pass_in_tools
    main_nonagent_tools_by_name = {tool.name: tool for tool in non_agent_tools}
    main_model_with_tools = main_model.bind_tools(sub_agent_tools + non_agent_tools)

    main_state = make_main_state()
    main_agent_builder = StateGraph(main_state)

    sum_gap = main_spec.summary.summarize_gap_tokenwise
    flush_threshold = main_spec.summary.flush_history_tokenwise
    reserve_rounds = main_spec.summary.reserve_message_round

    async def is_human_msg_or_not(state):
        human_message = state["messages"][-1]
        if not isinstance(human_message, HumanMessage):
            return Command(goto ="call_main_llm" )
        return Command(goto = "we_dont_want_unresponded_human_msg")

    async def we_dont_want_unresponded_human_msg (state):
        msg_list = state["messages"]
        len_msg_list = len(msg_list)
        if len_msg_list >= 2:
            last_second_msg = msg_list[-2]
            if isinstance(last_second_msg , HumanMessage):
                return Command(goto = "we_dont_want_unresponded_human_msg",
                               update= {        "messages" : [RemoveMessage ( id = last_second_msg.id)]      })
            else:
                return Command(goto= "extract_human_message")
        else:
            return Command(goto= "extract_human_message")

    async def extract_human_message(state):
        human_message = state["messages"][-1]
        return {"messages": [RemoveMessage(id=human_message.id)], "extracted_human_message": human_message.content}

    async def merge_human_message_with_memory(state):
        human_message_content = state["extracted_human_message"]
        if memory_attach:
            retrieved_memory = await attach_memory_automatically.coroutine(
                query=human_message_content, number_of_retrieval=num_memories_attached
            )
        else:
            retrieved_memory = None
        if retrieved_memory:
            human_message = HumanMessage(
                content=USER_MSG_PREFIX + human_message_content + MEMORY_ATTACH_MARKER + retrieved_memory + "\n"
            )
        else:
            human_message = HumanMessage(content=human_message_content + "\n")
        return {"messages": [human_message]}

    async def period_summarize_evaluate(state):
        msg_list = state["messages"]
        last_ai_msg = None
        for message_iter in range(len(msg_list) - 1, -1, -1):
            msg_here = msg_list[message_iter]
            if isinstance(msg_here, AIMessage):
                last_ai_msg = msg_here
                break
        if not last_ai_msg:
            return Command(goto="merge_human_message_with_memory")
        usage_metadata = last_ai_msg.usage_metadata or {}
        usage_count = usage_metadata.get("total_tokens", 0)
        if usage_count >= flush_threshold:
            return Command(goto="final_summarization_prompt",
                           update= {"current_history_token_volume" :usage_count })

        summarize_thresh_hold = state.get("next_summarize_thresh_hold", sum_gap)
        if usage_count >= summarize_thresh_hold:
            return Command(
                goto="period_summarization_prompt",
                update={"next_summarize_thresh_hold": summarize_thresh_hold + sum_gap},
            )
        return Command(goto="merge_human_message_with_memory")

    async def period_summarization_prompt(state):
        return {
            "messages": [
                HumanMessage(
                    content="请对上一次阶段性总结以来所发生的所有新的对话内容进行一次新的阶段性总结。\n"
                    + "回答中请用<对话内容阶段性总结>\n为开始（标志着总结开始），以\n</对话内容阶段性总结>为结束（标志总结结束）\n"
                    + "为未来的阶段性总结给出明确分割点"
                    + "\n##重要## 在本次回答中你不应当调用任何工具\n\n"
                )
            ]
        }
    async def proactive_summary_get_usage_count(state):
        msg_list = state["messages"]
        last_ai_msg = None
        for message_iter in range(len(msg_list) - 1, -1, -1):
            msg_here = msg_list[message_iter]
            if isinstance(msg_here, AIMessage):
                last_ai_msg = msg_here
                break
        usage_metadata = (last_ai_msg.usage_metadata or {}) if last_ai_msg else {}
        usage_count = usage_metadata.get("total_tokens", 0)
        return Command(goto="proactive_final_summarization_prompt",
                        update= {"current_history_token_volume" :usage_count })

    
    async def final_summarization_prompt(state, config: RunnableConfig):
        await _try_capture_snapshot(config, snapshot_conn_string, snapshot_agent_id)
        usage_count = state["current_history_token_volume"]
        summary_prompt = summary_prompt_generator(usage_count=usage_count , proactive_flush= False)
        return {"messages": [HumanMessage(content=summary_prompt)]}

    async def proactive_final_summarization_prompt(state, config: RunnableConfig):
        await _try_capture_snapshot(config, snapshot_conn_string, snapshot_agent_id)
        usage_count = state["current_history_token_volume"]
        summary_prompt = summary_prompt_generator(usage_count=usage_count , proactive_flush= True)
        return {"messages": [HumanMessage(content=summary_prompt)]}
    
    async def final_history_flush(state): 
        reserve_msg_rounds = reserve_rounds
        msg_list = state["messages"]
        len_msg_list = len(msg_list)
        summary_human_msg_iter = None
        # locate last summary HumanMessage
        for msg_iter in range(len_msg_list - 1, -1 , -1):
            msg_here = msg_list[msg_iter]
            if isinstance(msg_here, HumanMessage):
                msg_str = msg_here.content
                if msg_str.strip().startswith(
                    summary_prompt_prefix.strip()
                ):
                    summary_human_msg_iter = msg_iter
                    break
                
        beginning_human_iter = summary_human_msg_iter
        if reserve_msg_rounds:
            for msg_iter in range(summary_human_msg_iter - 1, -1, -1): #倒序查找HumanMessage
                if isinstance(msg_list[msg_iter], HumanMessage):
                    reserve_msg_rounds -= 1
                    if not reserve_msg_rounds:
                        beginning_human_iter = msg_iter  #beginning_human_iter mains after flush, the beginning human msg iter
                        break
        if beginning_human_iter <= 0: # reserve too much
            return {}
        
        msg_list_to_delete = msg_list[0:beginning_human_iter] + msg_list[summary_human_msg_iter:]

        extract_ai_summary_msg = msg_list[-1].content
        extract_ai_summary_msg_as_str = None
        for msg_dict in extract_ai_summary_msg:
            if msg_dict["type"] == "text":
                extract_ai_summary_msg_as_str = msg_dict["text"]
                break

        return {
            "messages": [ RemoveMessage(id=msg.id) for msg in msg_list_to_delete  ],
            "extracted_summary_ai_msg_as_str" : extract_ai_summary_msg_as_str,
            "current_history_token_volume" :0,
            "next_summarize_thresh_hold": sum_gap,
            }
    
    async def refill_summary_msg(state):
        summary_text = state["extracted_summary_ai_msg_as_str"] or ""
        return {
            "messages":[
                HumanMessage(content= trimmed_summary_prompt),
                AIMessage(content=summary_text),
            ]
        }

    async def should_continue_main_summary_period(state):
        if state["messages"][-1].tool_calls:
            return "kill_tools_period"
        return "merge_human_message_with_memory"

    async def should_continue_main_summary_final(state):
        if state["messages"][-1].tool_calls:
            return "kill_tools_final"
        return "final_history_flush"
    
    async def proactive_should_continue_main_summary_final(state):
        if state["messages"][-1].tool_calls:
            return "proactive_kill_tools_final"
        return "proactive_final_history_flush"
    
    async def kill_tool(state):
        result = []
        for tool_call in state["messages"][-1].tool_calls:
            observation = "Tool call is not allowed during the stage of making summarization."
            result.append(ToolMessage(content=observation, tool_call_id=tool_call["id"], name=tool_call["name"]))
        return {"messages": result}

    async def call_main_llm(state):
        existing = state.get("messages", [])
        subagent_call_prompt_here = subagent_call_prompt if sub_agent_dict else ""
        react_prompt = ReAct_system_prompt if main_spec.react_prompt else ""
        concate_sys_messages = [SystemMessage(content=main_system_prompt + "\n" + subagent_call_prompt_here + react_prompt)] + existing
        response = await main_model_with_tools.ainvoke(concate_sys_messages)
        return {"messages": [response]}
    
    async def query_to_proactive_summary(state):
        if state.get("proactive_summary_requested") == True:
            return Command(goto="proactive_summary_confirm", update={"proactive_summary_requested": False})
        elif state.get("proactive_summary_requested_for_specified_sub_agent") != None:
            return Command(goto="summary_requested_for_sub_agent" )
                # update={"proactive_summary_requested_for_specified_sub_agent": None})
        return Command(goto="is_human_msg_or_not")

    async def summary_requested_for_sub_agent(state):
        instructions_for_subagents = {}
        sub_agent_name = state.get("proactive_summary_requested_for_specified_sub_agent")
        instructions_for_subagents[sub_agent_name] = {}
        instructions_for_subagents[sub_agent_name]["proactive_summary"] = True
        return Command( goto=sub_agent_name,
                       update= {
                           "instructions_for_subagents": instructions_for_subagents,
                           "proactive_summary_requested_for_specified_sub_agent" : None,
                            }
                        )


    async def proactive_summary_confirm(state):
        user_opinion = interrupt("是否进行主动全量总结？注意：会剥离目前所有会话历史（除了设置的保留会话轮数）。")
        if user_opinion == "yes":
            return Command(goto="proactive_summary_get_usage_count")
        return Command(goto=END)

    for name, subgraph in sub_agent_dict.items():
        main_agent_builder.add_node(name, subgraph, retry_policy=RetryPolicy(max_attempts=3))
        
    (
        main_agent_builder
        .add_node("extract_human_message", extract_human_message)
        .add_node("call_main_llm", call_main_llm, retry_policy=RetryPolicy(max_attempts=3))
        .add_node("summary_requested_for_sub_agent",summary_requested_for_sub_agent)
        .add_node("kill_tools_period", kill_tool)
        .add_node("kill_tools_final", kill_tool)
        .add_node("proactive_kill_tools_final", kill_tool)
        .add_node("query_to_proactive_summary",query_to_proactive_summary)
        .add_node("proactive_summary_confirm",proactive_summary_confirm)
        .add_node("refill_summary_msg",refill_summary_msg)
        .add_node("proactive_refill_summary_msg",refill_summary_msg)
        .add_node("period_summarize_evaluate", period_summarize_evaluate)
        .add_node("period_summarization_prompt", period_summarization_prompt)
        .add_node("period_summarization", call_main_llm, retry_policy=RetryPolicy(max_attempts=3))
        .add_node("proactive_final_summarization_prompt", proactive_final_summarization_prompt)
        .add_node("proactive_final_summarization", call_main_llm, retry_policy=RetryPolicy(max_attempts=3))
        .add_node("proactive_final_history_flush", final_history_flush)
        .add_node("final_summarization_prompt", final_summarization_prompt)
        .add_node("final_summarization", call_main_llm, retry_policy=RetryPolicy(max_attempts=3))
        .add_node("final_history_flush", final_history_flush)
        .add_node("merge_human_message_with_memory", merge_human_message_with_memory)
        .add_node("we_dont_want_unresponded_human_msg",we_dont_want_unresponded_human_msg)
        .add_node("is_human_msg_or_not",is_human_msg_or_not)
        .add_node("proactive_summary_get_usage_count",proactive_summary_get_usage_count)
        .add_edge(START, "query_to_proactive_summary")
        .add_edge("proactive_final_summarization_prompt","proactive_final_summarization")
        .add_edge("extract_human_message", "period_summarize_evaluate")
        .add_edge("period_summarization_prompt", "period_summarization")
        .add_conditional_edges("period_summarization", should_continue_main_summary_period, ["kill_tools_period", "merge_human_message_with_memory"])
        .add_edge("kill_tools_period", "period_summarization")
        .add_edge("final_summarization_prompt", "final_summarization")
        .add_conditional_edges("final_summarization", should_continue_main_summary_final, ["kill_tools_final", "final_history_flush"])
        .add_conditional_edges("proactive_final_summarization", proactive_should_continue_main_summary_final, ["proactive_kill_tools_final", "proactive_final_history_flush"])
        .add_edge("kill_tools_final", "final_summarization")
        .add_edge("final_history_flush","refill_summary_msg")
        .add_edge("refill_summary_msg", "merge_human_message_with_memory")
        .add_edge("proactive_kill_tools_final", "proactive_final_summarization")
        .add_edge("proactive_final_history_flush","proactive_refill_summary_msg")
        .add_edge("proactive_refill_summary_msg", END)
        .add_edge("merge_human_message_with_memory", "call_main_llm")
    )

    async def delegate_instructions(state):
        instructions_for_subagents = {}
        instructions_ids = {}
        for agent_call in state["messages"][-1].tool_calls:
            agent_name = agent_call["name"]
            if agent_name not in sub_agent_dict:
                continue
            instructions_for_subagents[agent_name] = {}
            instructions_for_subagents[agent_name]["args"] = agent_call["args"]
            instructions_for_subagents[agent_name]["proactive_summary"] = False
            instructions_ids[agent_name] = agent_call["id"]
        return {
            "instructions_for_subagents": instructions_for_subagents,
            "instructions_ids": instructions_ids,
        }

    async def route_to_func(state):
        node_list = []
        tool_node_bool = False
        for agent_call in state["messages"][-1].tool_calls:
            agent_name = agent_call["name"]
            if agent_name not in sub_agent_dict:
                tool_node_bool = True
                continue
            node_list.append(agent_name)
        if tool_node_bool:
            node_list.append("tool_node_front")
        if not node_list:
            if html_report:
                return "query_to_produce_html"
            return END
        return node_list

    async def consume_submitted_reports(state):
        tool_message_list_to_return = []
        reports_dict = state.get("subagents_reports_submit", {})
        for agent_name, report in reports_dict.items():
            if report:
                tool_message_list_to_return.append(
                    ToolMessage(
                        tool_call_id=state["instructions_ids"][agent_name],
                        status="success",
                        content=report,
                        name=agent_name,
                    )
                )
        initialized_reports_dict = {agent_name: None for agent_name in reports_dict}
        return {"messages": tool_message_list_to_return, "subagents_reports_submit": initialized_reports_dict}

    async def query_to_produce_html(state):
        user_opinion = interrupt("你需要对所给内容输出一份html报告吗？")
        if user_opinion == "yes":
            return Command(
                update={
                    "messages": [
                        HumanMessage(content=html_report_prompt)
                    ]
                },
                goto="produce_html_call_llm",
            )
        return Command(goto=END)

    async def produce_html_call_llm(state):
        existing = state.get("messages", [])
        subagent_call_prompt_here = subagent_call_prompt if sub_agent_dict else ""
        react_prompt = ReAct_system_prompt if main_spec.react_prompt else ""
        concate_sys_messages = [SystemMessage(content=main_system_prompt + "\n" + subagent_call_prompt_here + react_prompt)] + existing
        response = await main_model_with_tools.ainvoke(concate_sys_messages)
        return {"messages": [response]}

    async def tool_node_front(state):
        result = []
        for tool_call in state["messages"][-1].tool_calls:
            tool_name = tool_call["name"]
            if tool_name in sub_agent_dict:
                continue
            if tool_name not in main_nonagent_tools_by_name:
                result.append(
                    ToolMessage(
                        content="tool call invalid, tools could be dynamically attached or removed, please make sure this tool still remains in your tool_list",
                        tool_call_id=tool_call["id"],
                        name=tool_name,
                    )
                )
                continue
            tool = main_nonagent_tools_by_name[tool_name]
            try:
                observation = await tool.ainvoke(tool_call["args"])
                result.append(ToolMessage(content=observation, tool_call_id=tool_call["id"], name=tool_name))
            except Exception as e:
                result.append(ToolMessage(content=str(e), tool_call_id=tool_call["id"], name=tool_name))
        return {"messages": result}

    async def tool_node(state):
        result = []
        for tool_call in state["messages"][-1].tool_calls:
            tool_name = tool_call["name"]
            if tool_name not in main_nonagent_tools_by_name:
                result.append(
                    ToolMessage(
                        content="You are not supposed to call this tool when depicting HTML report.",
                        tool_call_id=tool_call["id"],
                        name=tool_name,
                    )
                )
                continue
            tool = main_nonagent_tools_by_name[tool_name]
            try:
                observation = await tool.ainvoke(tool_call["args"])
                result.append(ToolMessage(content=observation, tool_call_id=tool_call["id"], name=tool_name))
            except Exception as e:
                result.append(ToolMessage(content=str(e), tool_call_id=tool_call["id"], name=tool_name))
        return {"messages": result}

    async def should_continue_main(state):
        if state["messages"][-1].tool_calls:
            return "tool_node"
        return END


    main_agent_builder.add_node("query_to_produce_html", query_to_produce_html)
    main_agent_builder.add_node("produce_html_call_llm", produce_html_call_llm, retry_policy=RetryPolicy(max_attempts=3))
    main_agent_builder.add_node("tool_node", tool_node, retry_policy=RetryPolicy(max_attempts=3))
    main_agent_builder.add_node("delegate_instructions", delegate_instructions)
    main_agent_builder.add_node("consume_submitted_reports", consume_submitted_reports)
    main_agent_builder.add_node("tool_node_front", tool_node_front, retry_policy=RetryPolicy(max_attempts=3))

    main_agent_builder.add_edge("call_main_llm", "delegate_instructions")
    main_agent_builder.add_conditional_edges("delegate_instructions", route_to_func)
    for name in sub_agent_dict.keys():
        async def route_after_sub_agent(state, _name=name):
            instructions = state.get("instructions_for_subagents", {}).get(_name, {})
            if instructions.get("proactive_summary"):
                return "proactive_done"
            return "consume_reports"

        main_agent_builder.add_conditional_edges(
            name,
            route_after_sub_agent,
            {"proactive_done": END, "consume_reports": "consume_submitted_reports"},
        )
    main_agent_builder.add_edge("tool_node_front", "consume_submitted_reports")
    main_agent_builder.add_edge("consume_submitted_reports", "call_main_llm")
    main_agent_builder.add_conditional_edges("produce_html_call_llm", should_continue_main)
    main_agent_builder.add_edge("tool_node", "produce_html_call_llm")

    return main_agent_builder.compile(checkpointer=checkpointer, store=store)
