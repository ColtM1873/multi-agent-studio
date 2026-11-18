"""由配置构建 LangGraph 主图 + 子图（重构自 World_function_new.py / sub_agent_sub_graph_factory_new.py）。"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any, Literal

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage
from langchain_core.runnables.config import RunnableConfig
from langchain_core.tools import tool as langchain_tool
from langchain_core.tools.structured import StructuredTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.graph.state import CompiledStateGraph, StateGraph, START, END
from langgraph.types import Command, RetryPolicy, interrupt
from openai import DefaultAsyncHttpxClient, DefaultHttpxClient

from app.config.models import (
    DEFAULT_HTML_REPORT_PROMPT,
    MCPServerConfig,
    ModelConfig,
    MultiAgentConfig,
    SubAgentConfig,
)
from app.runtime.file_tools import build_file_tools
from app.runtime.deepseek_reasoning_fix import apply_deepseek_reasoning_fix, is_deepseek_config
from app.runtime.openai_compat_reasoning import apply_openai_compat_reasoning_capture
from app.runtime.browser_tools import return_browser_tools

from app.runtime.prompts import (
    MEMORY_ATTACH_MARKER, USER_MSG_PREFIX, 
    ReAct_system_prompt, summary_prompt_generator, 
    subagent_call_prompt, summary_prompt_prefix,trimmed_summary_prompt,
    browser_tools_debug_phrase_prompt,
    user_choose_stop, solid_go_on_prompt,
)
from app.runtime.state_factory import make_main_state, make_sub_agent_state
from app.services import snapshot as snapshot_service

from browser_agent import bid065_trace  # BID065

logger = logging.getLogger(__name__)

SEARCH_MEMORY_THRESHOLD = 0.5
ATTACH_MEMORY_THRESHOLD = 0.7


def _resolve_summary_percent(state, default_percent: float) -> float:
    """优先用「本次主动总结」指定的百分比，否则回退全局默认。"""
    percent = state.get("proactive_summary_percent")
    if percent is None:
        return default_percent
    try:
        percent = float(percent)
    except (TypeError, ValueError):
        return default_percent
    return percent if percent > 0 else default_percent


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


def describe_mcp_error(exc: BaseException) -> str:
    """把 anyio TaskGroup 的 ExceptionGroup 剥到最内层，给出可读错误。"""
    current: BaseException = exc
    seen: set[int] = set()
    while True:
        seen.add(id(current))
        subs = getattr(current, "exceptions", None)
        if not subs:
            break
        nxt = next((s for s in subs if id(s) not in seen), None)
        if nxt is None:
            break
        current = nxt
    return f"{type(current).__name__}: {current}"


def build_mcp_client(servers: list[MCPServerConfig]) -> MultiServerMCPClient:
    config: dict[str, Any] = {}
    for s in servers:
        if s.transport == "http":
            entry: dict[str, Any] = {"transport": "http", "url": s.url}
            if s.headers:
                entry["headers"] = s.headers
            config[s.name] = entry
        else:
            entry = {"transport": "stdio", "command": s.command}
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
    - openai_compatible 且 base_url_no_proxy 时，给该模型实例配一个
      trust_env=False 的 httpx client，让请求不读取环境变量 / 系统全局代理。
      主/子 agent 各有自己的 ModelConfig，因此两边可以一个走代理、一个不走。
    - openai_compatible 模式自动捕获三方端点的 reasoning_content（ChatOpenAI
      默认丢弃非标准字段，见 app/runtime/openai_compat_reasoning.py）。
    - 检测到 DeepSeek 时自动应用 reasoning_content 出站回填补丁（无需用户配置）。
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
        compat_kwargs: dict[str, Any] = {}
        # 流式也要带回 usage：ChatOpenAI 只在「未传 base_url / 自定义 client」时才把
        # stream_usage 默认置 True（langchain_openai base.py 的 validate_environment），
        # 而 openai_compatible 必然传 base_url，于是请求里没有
        # stream_options={"include_usage": True}，端点不会在最后一帧附带 usage，
        # AIMessage.usage_metadata 就是 None，聊天界面（历史/编辑模式）便没有 ↑↓ 用量。
        # 合规的 OpenAI 兼容端点都支持 include_usage；本地网关实测有效。
        compat_kwargs["stream_usage"] = True
        if model_cfg.base_url_no_proxy:
            # httpx 默认 trust_env=True，会读取 HTTP(S)_PROXY 与 Windows 系统代理；
            # 本地部署的模型（如 http://127.0.0.1:8000/v1）会被全局代理拦截。
            # 用 openai 的默认 client 子类，只覆盖 trust_env，其余（timeout/重定向/
            # 连接池）保持与默认行为一致。同步与异步都传，避免只覆盖一半调用路径。
            compat_kwargs["http_client"] = DefaultHttpxClient(trust_env=False)
            compat_kwargs["http_async_client"] = DefaultAsyncHttpxClient(trust_env=False)
        model = init_chat_model(
            model=llm_provider_name,
            api_key=api_key,
            model_provider="openai",
            base_url=model_cfg.base_url,
            **compat_kwargs,
            **kwargs,
        )
        # 官方 ChatOpenAI 会丢弃三方端点的非标准 reasoning_content，
        # 这里补上入站解析，否则思考过程既进不了 v3 流式事件，也进不了历史渲染。
        apply_openai_compat_reasoning_capture(model)
    else:
        model = init_chat_model(model=llm_provider_name, api_key=api_key, **kwargs)

    # DeepSeek 自动修复：检测到 DeepSeek 就回填历史 assistant 的 reasoning_content，
    # 不再要求用户手动勾选（见 inner_docs/ID29）。
    if is_deepseek_config(llm_provider_name, model_cfg):
        apply_deepseek_reasoning_fix(model)

    return model


async def build_sub_agent(
    spec: SubAgentConfig,
    tools_mcp_client,
    state_messages_key: str,
    history_token_measure_key: str,
    extracted_summary_ai_msg_key: str,
    checkpoint_conn_string: str,
    sub_solid_summarize_threshold : str,
    sub_solid_summarize_mode : str,
    agent_id: str,
    state_type,
    default_summary_percent: float = 20.0,
    builtin_tools: list[Any] | None = None,
) -> CompiledStateGraph:
    """构建单个子 agent 的子图（checkpointer=True）。

    ``builtin_tools``：程序自带的、已经绑定好工作目录的工具（目前即文件读写工具）。
    它们和 MCP 工具一起 bind_tools 给子 agent 的模型；传空列表即「该子 agent 没有
    文件读写能力」（老版本行为，也是「简化文件读写功能配置」开启时的行为）。
    """
    sub_agent_name = spec.name
    agent_system_prompt = spec.system_prompt
    llm_provider_name = spec.llm_provider_name
    model_api_key = spec.api_key
    flush_threshold = spec.summary.flush_history_tokenwise
    reserve_rounds = spec.summary.reserve_message_round
    solid_cap = spec.summary.solid_summarize_tokenwise


    tools: list[Any] = []
    failed: list[str] = []
    error_details: list[str] = []
    for server in spec.mcp_servers:
        try:
            tools.extend(await tools_mcp_client.get_tools(server_name=server.name))
        except Exception as e:  # noqa: BLE001
            failed.append(f"{server.name} ({server.transport})")
            error_details.append(f"{server.name}: {describe_mcp_error(e)}")
    if failed:
        raise RuntimeError(
            f"子 agent [{sub_agent_name}] 的 MCP 服务器连接失败：[{', '.join(failed)}]，"
            f"请确认这些服务已启动且鉴权配置正确。原始错误: {' | '.join(error_details)}"
        )
    # 程序自带的文件读写工具（已按该子 agent 的工作目录绑定好闭包）。
    # 放在 MCP 工具之后：同名时以程序自带工具为准。
    if builtin_tools:
        tools.extend(builtin_tools)
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
        summary_prompt = summary_prompt_generator(usage_count=usage_count , proactive_flush= False,
                                                  summary_percent=default_summary_percent)
        return {state_messages_key: [HumanMessage(content=summary_prompt)]}
    
    async def proactive_final_summarization_prompt(state, config: RunnableConfig):
        await _try_capture_snapshot(config, checkpoint_conn_string, agent_id)
        usage_count = state[history_token_measure_key]
        summary_prompt = summary_prompt_generator(
            usage_count=usage_count,
            proactive_flush=True,
            summary_percent=_resolve_summary_percent(state, default_summary_percent),
        )
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
        # if beginning_human_iter <= 0: # reserve too much
        #     return {}
        msg_list_to_delete = (
            msg_list[0:beginning_human_iter] + msg_list[summary_human_msg_iter:] 
            if beginning_human_iter > 0
            else msg_list[summary_human_msg_iter:]
        )

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
        # 用 .get 兜底：final_history_flush 在「保留轮数用不完」时会提前 return {}，
        # 不会写入总结文本；此时回填空串好过 KeyError 把整轮会话打断。
        summary_text = state.get(extracted_summary_ai_msg_key) or ""
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
        if state.get(sub_solid_summarize_mode , False) == True:
            return{
                state_messages_key : [HumanMessage (content = solid_go_on_prompt)],
                sub_solid_summarize_mode : False,
                sub_solid_summarize_threshold: solid_cap,
            }
        else:
            instruction = state["instructions_for_subagents"][sub_agent_name]["args"]["instruction"]
            return {state_messages_key: [HumanMessage(content=instruction + "\n")],
                    sub_solid_summarize_mode : False,
                    sub_solid_summarize_threshold :solid_cap,
                    }


    async def route_to_proactive_summary(state):
        proactive_summary_bool = (
            state
            .get("instructions_for_subagents", {})
            .get(sub_agent_name, {})
            .get("proactive_summary", False)
        )
        reset_solid_summarize_mode = { sub_solid_summarize_mode : False}

        if state.get("to_complete_tool_results") == True:
            return Command(goto = "peel_off_human_msg" , update= reset_solid_summarize_mode)
        elif proactive_summary_bool:
            return Command(goto="proactive_summary_confirm", update= reset_solid_summarize_mode)
        elif ( state.get("request_to_edit_msg_in_the_past") != None 
              and  state["request_to_edit_msg_in_the_past"].get("request_or_not") 
              and  state["request_to_edit_msg_in_the_past"].get("request_for_subagent") ):
            return Command(goto = "edit_msg_in_the_past_sub", update= reset_solid_summarize_mode)
        else:
            return Command(goto="period_summarize_evaluate", update= reset_solid_summarize_mode)
        
    async def peel_off_human_msg(state):
        msg_list = state.get(state_messages_key, [])
        last_indice_non_human = -1
        for i in range(len(msg_list) - 1, -1 , -1):
            if not isinstance(msg_list[i] , HumanMessage):
                last_indice_non_human = i
                break
        return Command(goto = "complete_tool_results" , update = {
            state_messages_key : [ RemoveMessage(id=msg.id) for msg in msg_list[last_indice_non_human + 1: ] ]
        })

    async def complete_tool_results(state):
        msg_list = state.get(state_messages_key, [])
        tool_msg_ids_before_ai_msg_set = set()
        ai_msg_indice = -1

        for i in range(len(msg_list) - 1, -1 , -1):
            if isinstance(msg_list[i] , ToolMessage):
                tool_msg_ids_before_ai_msg_set.add(  msg_list[i].tool_call_id )
            elif isinstance(msg_list[i] , AIMessage):
                ai_msg_indice = i
                break

        if ai_msg_indice >= 0:
            tool_call_list = msg_list[ai_msg_indice].tool_calls
            tool_msg_list_to_construct = []
            
            for tool_call_dict in tool_call_list:
                if tool_call_dict["id"] in tool_msg_ids_before_ai_msg_set:
                    continue
                else:
                    tool_msg_list_to_construct.append(ToolMessage(
                        tool_call_id =  tool_call_dict["id"] ,
                        name =  tool_call_dict["name"] ,
                        content = user_choose_stop,
                        id = str(uuid.uuid4())  ))
            return Command(goto = END , update = {state_messages_key : tool_msg_list_to_construct})
        
    (
        builder
        .add_node("complete_tool_results",complete_tool_results)
        .add_node("peel_off_human_msg",peel_off_human_msg)

    )

    async def proactive_summary_confirm(state):
        # 前端已弹「百分比设置框」并确认过时，直接进入总结，不再二次 interrupt。
        if state.get("proactive_summary_percent") is not None:
            return Command(goto="proactive_summary_get_usage_count")
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

    async def edit_msg_in_the_past_sub(state):
        msg_list = state[state_messages_key]
        len_msg_list = len(msg_list)
        msg_indice_to_change = state["request_to_edit_msg_in_the_past"]["msg_indice"]
        if msg_indice_to_change < 0 or msg_indice_to_change >= len_msg_list:
            print("msg_indice out of range.")
            return Command(goto= END)
        else:
            msg_original = msg_list[msg_indice_to_change]
            msg_substitute = state["request_to_edit_msg_in_the_past"]["substitute_msg"]
            if not (  type(msg_original) is type(msg_substitute)  ):
                print("substitute_msg type mismatch.")
                return Command(goto= END)
            else:
                msg_list_to_peel = msg_list[msg_indice_to_change :]
                return Command(goto = "refill_msg_list_after_edit",
                               update= {
                                   state_messages_key: [ RemoveMessage(id=msg.id) for msg in msg_list_to_peel  ],
                                   "request_to_edit_msg_in_the_past" :{
                                       "substitute_msg" : msg_substitute,
                                       "msg_list_cache" : msg_list_to_peel,
                                       }
                                   })
    async def refill_msg_list_after_edit(state):
        edit_dict = state["request_to_edit_msg_in_the_past"]
        msg_list_to_peel = edit_dict["msg_list_cache"][:]
        msg_substitute = edit_dict["substitute_msg"]
        msg_list_to_peel[0] = msg_substitute
        return Command(goto= END ,
                       update= {
                           state_messages_key : msg_list_to_peel,
                           "request_to_edit_msg_in_the_past" : {"request_for_subagent" : True},
                           })
    (
        builder
        .add_node("refill_msg_list_after_edit", refill_msg_list_after_edit)
        .add_node("edit_msg_in_the_past_sub", edit_msg_in_the_past_sub)
    )


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

        last_ai_msg = state[state_messages_key][-1]
        assert isinstance(last_ai_msg , AIMessage)
        total_token_usage = last_ai_msg.usage_metadata["total_tokens"] if last_ai_msg.usage_metadata else None
        solid_summary_cap = state.get(sub_solid_summarize_threshold,None)
        if total_token_usage and solid_summary_cap and total_token_usage >= solid_summary_cap:
            return Command(
                goto= "final_summarization_prompt" ,
                update={
                    state_messages_key : result,
                    history_token_measure_key : total_token_usage,
                    sub_solid_summarize_mode : True,
                },
            )
        
        else:            
            return Command(
                goto= "call_llm" ,
                update={
                    state_messages_key: result,
                },
            )

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
    default_summary_percent: float = 20.0,
    search_memory_threshold: float = SEARCH_MEMORY_THRESHOLD,
    attach_memory_threshold: float = ATTACH_MEMORY_THRESHOLD,
    pdf_table_extraction: bool = True,
    simplified_file_tools: bool = False,
) -> CompiledStateGraph:
    """构建 Supervisor-Worker 主图（等价于原来的 TheWorld）。

    调用方需确保 checkpointer/store 已 setup。
    mcp_client_factory: 可选，用于注入 MCP 客户端（默认 build_mcp_client），
    便于离线测试时替换为假客户端。
    simplified_file_tools: 进阶设置里的「简化 文件读写功能 配置选项」。为 True 时回到
    老版本方式——主 agent 恒注入文件读写工具、子 agent 恒不注入（忽略各 agent 自己的
    ``file_tools.enabled``）；为 False 时主 / 子 agent 各自按自己的开关与工作目录生效。
    """
    store_namespace = config.postgres.store_namespace

    # 为每个子 agent 生成唯一消息键（若尚未生成）
    from app.runtime.state_factory import (
        assign_extracted_summary_ai_msg_keys,
        assign_history_token_measure_keys,
        assign_state_messages_keys,
        assign_solid_summarize_keys,
    )

    assign_state_messages_keys(config)
    assign_history_token_measure_keys(config)
    assign_extracted_summary_ai_msg_keys(config)
    assign_solid_summarize_keys(config)


    factory = mcp_client_factory or build_mcp_client

    main_spec = config.main_agent
    html_report = main_spec.html_report
    html_report_prompt = main_spec.html_report_prompt or DEFAULT_HTML_REPORT_PROMPT

    snapshot_conn_string = config.checkpoint_conn_string
    snapshot_agent_id = config.agent_id

    # ── 文件读写工具（主 / 子 agent 各自可开关，工作目录也可以各不相同）──
    # 「简化文件读写功能配置」开启时回到老版本方式：主 agent 恒开启、子 agent 恒关闭。
    file_tools_simplified = bool(simplified_file_tools)
    main_file_tools = main_spec.file_tools
    main_file_tools_on = True if file_tools_simplified else bool(main_file_tools.enabled)
    main_root_dir = (main_file_tools.root_dir or "").strip()

    pass_in_tools = (
        build_file_tools(main_root_dir, pdf_table_extraction) if main_file_tools_on else []
    )

    def sub_file_tools_of(spec: SubAgentConfig) -> list[Any]:
        """按该子 agent 的配置构建文件读写工具；关闭 / 简化模式返回空列表。

        「使用主agent工作目录」是**实时跟随**：只在主 agent 的文件读写功能开启且目录
        已填时生效，此时忽略子 agent 自己那份 root_dir（它只被保留着，等开关关掉再用）。
        """
        if file_tools_simplified:
            return []
        sub_file_tools = spec.file_tools
        if not sub_file_tools.enabled:
            return []
        root_dir = (sub_file_tools.root_dir or "").strip()
        if sub_file_tools.use_main_root and main_file_tools_on and main_root_dir:
            root_dir = main_root_dir
        return build_file_tools(root_dir, pdf_table_extraction)

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
            spec.solid_summarize_threshold_key,
            spec.solid_summarize_mode_key,
        )
        sub_agent_sub_graphs.append(
            await build_sub_agent(
                spec,
                mcp_client,
                spec.state_messages_key,
                spec.history_token_measure_key,
                spec.extracted_summary_ai_msg_key,
                snapshot_conn_string,
                spec.solid_summarize_threshold_key,
                spec.solid_summarize_mode_key,
                snapshot_agent_id,
                state_type,
                default_summary_percent=default_summary_percent,
                builtin_tools=sub_file_tools_of(spec),
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
            if piece.score is None or piece.score < search_memory_threshold:
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
            if piece.score is None or piece.score < attach_memory_threshold:
                continue
            for key, value in piece.value.items():
                memories = memories + "\n" + key + ":" + value
        return memories

    memory_tools = [write_memory, read_memory]
    # 浏览器接管：开启后把 browser_agent 的 14 个工具并入主 agent 的非子图工具，
    # 从而被 bind_tools 注入、并由 tool_node_front 执行（见 inner_docs/ID56）。
    # BID086：把本 multi-agent 的「允许 Agent 保留的最大标签页数」交给工具层——
    # click / navigate 的描述里要写这个数字，运行时也按它收敛标签页（浏览器进程级）。
    browser_tools = await return_browser_tools(main_spec.browser_tab_max_number) if main_spec.browser_takeover else []  # BID086
    # 记忆读写：关闭后不把记忆工具并入主 agent 的工具集（模型无法读写记忆）。
    selected_memory_tools = memory_tools if main_spec.memory_tools_enabled else []
    non_agent_tools = selected_memory_tools + pass_in_tools + browser_tools
    main_nonagent_tools_by_name = {tool.name: tool for tool in non_agent_tools}
    main_model_with_tools = main_model.bind_tools(sub_agent_tools + non_agent_tools)

    main_state = make_main_state()
    main_agent_builder = StateGraph(main_state)

    sum_gap = main_spec.summary.summarize_gap_tokenwise
    flush_threshold = main_spec.summary.flush_history_tokenwise
    reserve_rounds = main_spec.summary.reserve_message_round
    solid_cap = main_spec.summary.solid_summarize_tokenwise


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
        return {"messages": [RemoveMessage(id=human_message.id)], 
                "extracted_human_message": human_message.content,
                "solid_summarize_mode" : False}


    async def merge_human_message_with_memory(state):
        if state.get("solid_summarize_mode",False) == True:
            return Command(
                goto= "call_main_llm",
                update= {"messages" : [HumanMessage(content= solid_go_on_prompt )],
                         "solid_summarize_mode" : False,}
            )
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
        return Command(update= {"messages": [human_message]} , goto= "call_main_llm")

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
        summary_prompt = summary_prompt_generator(usage_count=usage_count , proactive_flush= False,
                                                  summary_percent=default_summary_percent)
        return {"messages": [HumanMessage(content=summary_prompt)]}
    
    async def proactive_final_summarization_prompt(state, config: RunnableConfig):
        await _try_capture_snapshot(config, snapshot_conn_string, snapshot_agent_id)
        usage_count = state["current_history_token_volume"]
        summary_prompt = summary_prompt_generator(
            usage_count=usage_count,
            proactive_flush=True,
            summary_percent=_resolve_summary_percent(state, default_summary_percent),
        )
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
        # if beginning_human_iter <= 0: # reserve too much
        #     return {}
        msg_list_to_delete = (
            msg_list[0:beginning_human_iter] + msg_list[summary_human_msg_iter:] 
            if beginning_human_iter > 0
            else msg_list[summary_human_msg_iter:]
        )
        
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
        summary_text = state.get("extracted_summary_ai_msg_as_str") or ""
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
        _bid065_t0 = bid065_trace.perf()  # BID065
        bid065_trace.mark("node call_main_llm START")  # BID065
        existing = state.get("messages", [])
        subagent_call_prompt_here = subagent_call_prompt if sub_agent_dict else ""
        react_prompt = ReAct_system_prompt if main_spec.react_prompt else ""
        browser_tools_debug_phrase_prompt_here = browser_tools_debug_phrase_prompt if main_spec.browser_takeover else ""
        concate_sys_messages = [SystemMessage(content=main_system_prompt + "\n" + subagent_call_prompt_here + react_prompt + browser_tools_debug_phrase_prompt_here)] + existing
        response = await main_model_with_tools.ainvoke(concate_sys_messages)
        bid065_trace.mark_elapsed("node call_main_llm LLM调用", _bid065_t0)  # BID065
        return {"messages": [response]}

    
    async def peel_off_human_msg(state):
        msg_list = state.get("messages", [])
        last_indice_non_human = -1
        for i in range(len(msg_list) - 1, -1 , -1):
            if not isinstance(msg_list[i] , HumanMessage):
                last_indice_non_human = i
                break
        return Command(goto = "complete_tool_results" , update = {
            "messages" : [ RemoveMessage(id=msg.id) for msg in msg_list[last_indice_non_human + 1: ] ]
        })

    async def complete_tool_results(state):
        msg_list = state.get("messages", [])
        tool_msg_ids_before_ai_msg_set = set()
        ai_msg_indice = -1

        for i in range(len(msg_list) - 1, -1 , -1):
            if isinstance(msg_list[i] , ToolMessage):
                tool_msg_ids_before_ai_msg_set.add(  msg_list[i].tool_call_id )
            elif isinstance(msg_list[i] , AIMessage):
                ai_msg_indice = i
                break

        if ai_msg_indice >= 0:
            tool_call_list = msg_list[ai_msg_indice].tool_calls
            tool_msg_list_to_construct = []

            for tool_call_dict in tool_call_list:
                if tool_call_dict["id"] in tool_msg_ids_before_ai_msg_set:
                    continue
                else:
                    tool_msg_list_to_construct.append(ToolMessage(
                        tool_call_id =  tool_call_dict["id"] ,
                        name =  tool_call_dict["name"] ,
                        content = user_choose_stop,
                        id = str(uuid.uuid4())  ))
            return {"messages" : tool_msg_list_to_construct}
        

    async def get_status_reset_tool_complete(state):
        return Command(goto = END , update = {"to_complete_tool_results" : False} )

    async def in_case_no_sub_agents (state):
        return

    (
        main_agent_builder
        .add_node("complete_tool_results",complete_tool_results)
        .add_node("peel_off_human_msg",peel_off_human_msg)
        .add_node("get_status_reset_tool_complete",get_status_reset_tool_complete)
        .add_node("in_case_no_sub_agents",in_case_no_sub_agents)
        .add_edge("complete_tool_results","in_case_no_sub_agents")
        .add_edge("in_case_no_sub_agents","get_status_reset_tool_complete")
    )

    async def query_to_proactive_summary(state):
        if state.get("to_complete_tool_results") == True:
            return Command(goto = "peel_off_human_msg")
        elif state.get("proactive_summary_requested") == True:
            return Command(goto="proactive_summary_confirm", update={"proactive_summary_requested": False})
        elif state.get("proactive_summary_requested_for_specified_sub_agent") != None:
            return Command(goto="summary_requested_for_sub_agent" )
                # update={"proactive_summary_requested_for_specified_sub_agent": None})
        elif ( state.get("request_to_edit_msg_in_the_past") != None 
              and  state["request_to_edit_msg_in_the_past"].get("request_or_not") ):
            return Command(goto = "edit_msg_in_the_past")
        elif (state.get("clean_every_and_all") == True):
            return Command(goto = "clean_all_msg")
        return Command(goto="is_human_msg_or_not")
    
    async def clean_all_msg (state):
        msg_list = state["messages"]
        return Command(goto = END,
                        update= {  "messages": [ RemoveMessage(id=msg.id) for msg in msg_list ],
                                 "clean_every_and_all" : False  })
        
    (
        main_agent_builder
        .add_node("clean_all_msg" , clean_all_msg)
    )

    async def edit_msg_in_the_past (state):
        request_for_subagent_or_not = state["request_to_edit_msg_in_the_past"]["request_for_subagent"]
        if request_for_subagent_or_not:
            return Command(goto= state["request_to_edit_msg_in_the_past"]["subagent_name"])
        else:
            return Command(goto= "edit_msg_in_the_past_main")

    async def edit_msg_in_the_past_main(state):
        msg_list = state["messages"]
        len_msg_list = len(msg_list)
        msg_indice_to_change = state["request_to_edit_msg_in_the_past"]["msg_indice"]
        if msg_indice_to_change < 0 or msg_indice_to_change >= len_msg_list:
            print("msg_indice out of range.")
            return Command(goto= END,
                           update={ "request_to_edit_msg_in_the_past" : None })
        else:
            msg_original = msg_list[msg_indice_to_change]
            msg_substitute = state["request_to_edit_msg_in_the_past"]["substitute_msg"]
            if not (  type(msg_original) is type(msg_substitute)  ):
                print("substitute_msg type mismatch.")
                return Command(goto= END,
                               update={ "request_to_edit_msg_in_the_past" : None })
            else:
                msg_list_to_peel = msg_list[msg_indice_to_change :]
                return Command(goto = "refill_msg_list_after_edit",
                               update= {
                                   "messages": [ RemoveMessage(id=msg.id) for msg in msg_list_to_peel  ],
                                   "request_to_edit_msg_in_the_past" :{
                                       "substitute_msg" : msg_substitute,
                                       "msg_list_cache" : msg_list_to_peel,
                                       }
                                   })
    async def refill_msg_list_after_edit(state):
        edit_dict = state["request_to_edit_msg_in_the_past"]
        msg_list_to_peel = edit_dict["msg_list_cache"][:]
        msg_substitute = edit_dict["substitute_msg"]
        msg_list_to_peel[0] = msg_substitute
        return Command(goto= END ,
                       update= {
                           "messages" : msg_list_to_peel,
                           "request_to_edit_msg_in_the_past" : None,
                           })
    (
        main_agent_builder
        .add_node("edit_msg_in_the_past" , edit_msg_in_the_past)
        .add_node("edit_msg_in_the_past_main" , edit_msg_in_the_past_main)
        .add_node("refill_msg_list_after_edit" , refill_msg_list_after_edit)

    )



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
        # 前端已弹「百分比设置框」并确认过时，直接进入总结，不再二次 interrupt。
        if state.get("proactive_summary_percent") is not None:
            return Command(goto="proactive_summary_get_usage_count")
        user_opinion = interrupt("是否进行主动全量总结？注意：会剥离目前所有会话历史（除了设置的保留会话轮数）。")
        if user_opinion == "yes":
            return Command(goto="proactive_summary_get_usage_count")
        return Command(goto=END)

    for name, subgraph in sub_agent_dict.items():
        main_agent_builder.add_node(name, subgraph, retry_policy=RetryPolicy(max_attempts=3))

    for name in sub_agent_dict.keys():
        main_agent_builder.add_edge("complete_tool_results", name)


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
    )

    async def delegate_instructions(state):
        _bid065_t0 = bid065_trace.perf()  # BID065
        bid065_trace.mark("node delegate_instructions START")  # BID065
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
        bid065_trace.mark_elapsed("node delegate_instructions END", _bid065_t0)  # BID065
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
        # 子 agent 编辑请求的收尾：编辑就是「改写+停」（见 ID25 §8.2），完成后必须
        # 直接 END。这里必须用 Command 做动态路由，且不能再给该节点挂静态边——
        # 否则静态边会覆盖 Command(goto=END)，导致编辑后仍跑一次 call_main_llm，
        # 甚至让主 agent 重新调用子 agent、触发子图总结而把刚编辑的内容清掉。
        if (  state.get("request_to_edit_msg_in_the_past") 
            and state["request_to_edit_msg_in_the_past"].get("request_for_subagent") ):
            return Command(goto = END,
                           update = { "request_to_edit_msg_in_the_past" : None})
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

        last_ai_msg = None
        for message in reversed(state["messages"]):
            if isinstance(message, AIMessage):
                last_ai_msg = message
                break
        total_token_usage = (
            last_ai_msg.usage_metadata.get("total_tokens")
            if last_ai_msg and last_ai_msg.usage_metadata
            else None
        )
        solid_summary_cap = solid_cap
        
        if total_token_usage and solid_summary_cap and total_token_usage >= solid_summary_cap:
            return Command(
                goto= "final_summarization_prompt" ,
                update={
                    "messages": tool_message_list_to_return,
                    "subagents_reports_submit": initialized_reports_dict,
                    "current_history_token_volume" : total_token_usage,
                    "solid_summarize_mode" : True,
                },
            )
        
        else:            
            return Command(
                goto= "call_main_llm" ,
                update={
                    "messages": tool_message_list_to_return,
                    "subagents_reports_submit": initialized_reports_dict,
                },
            )

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
        _bid065_t0 = bid065_trace.perf()  # BID065
        bid065_trace.mark("node produce_html_call_llm START")  # BID065
        existing = state.get("messages", [])
        subagent_call_prompt_here = subagent_call_prompt if sub_agent_dict else ""
        react_prompt = ReAct_system_prompt if main_spec.react_prompt else ""
        browser_tools_debug_phrase_prompt_here = browser_tools_debug_phrase_prompt if main_spec.browser_takeover else ""
        concate_sys_messages = [SystemMessage(content=main_system_prompt + "\n" + subagent_call_prompt_here + react_prompt + browser_tools_debug_phrase_prompt_here)] + existing
        response = await main_model_with_tools.ainvoke(concate_sys_messages)
        bid065_trace.mark_elapsed("node produce_html_call_llm LLM调用", _bid065_t0)  # BID065
        return {"messages": [response]}

    async def tool_node_front(state):
        _bid065_node_t0 = bid065_trace.perf()  # BID065
        bid065_trace.mark("node tool_node_front START")  # BID065
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
            _bid065_tc0 = bid065_trace.perf()  # BID065
            bid065_trace.mark(f"tool START {tool_name}")  # BID065
            try:
                observation = await tool.ainvoke(tool_call["args"])
                result.append(ToolMessage(content=observation, tool_call_id=tool_call["id"], name=tool_name))
            except Exception as e:
                result.append(ToolMessage(content=str(e), tool_call_id=tool_call["id"], name=tool_name))
            bid065_trace.mark_elapsed(f"tool END {tool_name}", _bid065_tc0)  # BID065
        bid065_trace.mark_elapsed("node tool_node_front END", _bid065_node_t0)  # BID065
        return {"messages": result}

    async def tool_node(state):
        _bid065_node_t0 = bid065_trace.perf()  # BID065
        bid065_trace.mark("node tool_node START")  # BID065
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
            _bid065_tc0 = bid065_trace.perf()  # BID065
            bid065_trace.mark(f"tool START {tool_name}")  # BID065
            try:
                observation = await tool.ainvoke(tool_call["args"])
                result.append(ToolMessage(content=observation, tool_call_id=tool_call["id"], name=tool_name))
            except Exception as e:
                result.append(ToolMessage(content=str(e), tool_call_id=tool_call["id"], name=tool_name))
            bid065_trace.mark_elapsed(f"tool END {tool_name}", _bid065_tc0)  # BID065
        bid065_trace.mark_elapsed("node tool_node END", _bid065_node_t0)  # BID065
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

    for name in sub_agent_dict.keys():  #其实没有什么魔法
        async def route_after_sub_agent(state, _name=name):
            if state.get("to_complete_tool_results"):
                return "get_status_reset"
            else:
                instructions = state.get("instructions_for_subagents", {}).get(_name, {})
                if instructions.get("proactive_summary"):
                    return "proactive_done"
                return "consume_reports"

        main_agent_builder.add_conditional_edges(
            name,
            route_after_sub_agent,
            {"proactive_done": END, "consume_reports": "consume_submitted_reports" ,
             "get_status_reset" : "get_status_reset_tool_complete"},
        )

    main_agent_builder.add_edge("tool_node_front", "consume_submitted_reports")
    main_agent_builder.add_conditional_edges("produce_html_call_llm", should_continue_main)
    main_agent_builder.add_edge("tool_node", "produce_html_call_llm")

    return main_agent_builder.compile(checkpointer=checkpointer, store=store)
