"""动态生成 LangGraph state class。

规则（重要）：
- 主图消息键固定为 `messages`。
- 每个子图的消息键 `state_messages_key` 必须全局唯一（父子消息键不同，否则冲突）。
- 每个子图的 token 计数键 `history_token_measure_key` 同样必须全局唯一，
  且不能与主图保留字段 `current_history_token_volume` 重名。
- 每个子图的「提取总结 AI 消息」键 `extracted_summery_ai_msg_key` 同样必须全局唯一，
  且不能与主图保留字段 `extracted_summery_ai_msg_as_str` 重名。
- `subagents_reports_submit` / `instructions_for_subagents` 在父子图间故意同名，
  用 merge_dicts reducer 实现"信息穿透"（子→父报告、父→子指令）。
"""

from __future__ import annotations

from typing import Any, Annotated

from langchain_core.messages import AnyMessage
from langgraph.graph import MessagesState
from langgraph.graph.message import add_messages

from app.config.models import MultiAgentConfig
from app.config.store import slugify


def merge_dicts(left: dict, right: dict) -> dict:
    """合并两个 dict，right 覆盖 left 中同名的 key。"""
    return {**left, **right}


SHARED_FIELDS = {
    "subagents_reports_submit": Annotated[dict[str, Any], merge_dicts],
    "instructions_for_subagents": Annotated[dict[str, Any], merge_dicts],
}


def make_main_state() -> type:
    """主图 state，等价于原来的 ExtendedMessagesState。"""

    class MainAgentState(MessagesState):
        subagents_reports_submit: Annotated[dict[str, Any], merge_dicts]
        instructions_for_subagents: Annotated[dict[str, Any], merge_dicts]
        instructions_ids: Annotated[dict[str, Any], merge_dicts]
        extracted_human_message: str
        next_summerize_thresh_hold: int
        current_history_token_volume : int
        extracted_summery_ai_msg_as_str : str
        proactive_summery_requested : bool

    return MainAgentState


def make_sub_agent_state(messages_key: str, history_token_measure_key: str, extracted_summery_ai_msg_key: str) -> type:
    """子图 state，等价于原来的 DocsState / WebSearchState 等。"""
    fields: dict[str, Any] = {
        messages_key: Annotated[list[AnyMessage], add_messages],
        history_token_measure_key: int,
        extracted_summery_ai_msg_key: str,
        **SHARED_FIELDS,
    }
    return type(
        f"SubAgentState_{messages_key}",
        (),
        {"__annotations__": fields},
    )


def assign_state_messages_keys(config: MultiAgentConfig) -> None:  # 函数签名：入参是整份多智能体配置，返回 None——不产生新对象，直接原地修改 config 里的每个子 agent
    """为每个子 agent 生成唯一的消息通道键名（原地写入 config）。"""
    seen: set[str] = set()  # 用一个集合记录本轮已经占用（分配或复用）的消息键，防止两个子 agent 撞名
    used_by_main = {"messages"}  # 主图的消息键被 LangGraph 固定叫 "messages"，子 agent 的键绝不能和它重名，否则父子消息会串台
    for sub in config.sub_agents:  # 逐个遍历配置里的所有子 agent，依次给它们分配（或校验）消息键
        if sub.state_messages_key and sub.state_messages_key not in seen and sub.state_messages_key not in used_by_main:  
            # 三个条件同时成立才算"已有键可用"：① 已填写了非空键 ② 没和前面的子 agent 重复 ③ 没撞上主图保留的 "messages"
            seen.add(sub.state_messages_key)  # 该键合法，登记进 seen，表示这个键名已经被占用，后续子 agent 必须避开它
            continue  # 保留这个已有的键名，直接跳过当前子 agent，进入下一轮循环处理下一个
        base = f"{slugify(sub.name)}_messages"  # 生成基础键名：把子 agent 名字 slugify（转小写、非字母数字下划线替换为 _、去掉首尾 _、空名兜底为 "agent"）后拼上 "_messages" 后缀
        key = base  # 先假设基础键名没有冲突，把它当作第一个候选键名
        i = 2  # 冲突时的数字后缀从 2 开始，后续会生成 base_2、base_3 … 这样递增
        while key in seen or key in used_by_main:  # 只要当前候选键名与已占用键（子 agent 的 seen，或主图的 "messages"）冲突，就继续循环找下一个空位
            key = f"{base}_{i}"  # 给基础键名追加数字后缀（如 base_2）作为新的候选键名，避开冲突
            i += 1  # 编号自增，为下一轮（如果这个候选仍冲突）准备更大的后缀
        sub.state_messages_key = key  # 把最终确定且全局唯一、不与主图冲突的键名写回当前子 agent（原地修改配置）
        seen.add(key)  # 登记这个新分配出来的键名，供后续子 agent 判断时避免重复使用


def assign_history_token_measure_keys(config: MultiAgentConfig) -> None:
    """为每个子 agent 生成唯一的 token 计数键名（原地写入 config）。

    规则与 assign_state_messages_keys 完全一致，只是：
    - 主图保留字段是 `current_history_token_volume`；
    - 生成的基础键名是 `<slugified_name>_current_history_token_volume`。
    """
    seen: set[str] = set()
    used_by_main = {"current_history_token_volume"}
    for sub in config.sub_agents:
        if (
            sub.history_token_measure_key
            and sub.history_token_measure_key not in seen
            and sub.history_token_measure_key not in used_by_main
        ):
            seen.add(sub.history_token_measure_key)
            continue
        base = f"{slugify(sub.name)}_current_history_token_volume"
        key = base
        i = 2
        while key in seen or key in used_by_main:
            key = f"{base}_{i}"
            i += 1
        sub.history_token_measure_key = key
        seen.add(key)


def assign_extracted_summery_ai_msg_keys(config: MultiAgentConfig) -> None:
    """为每个子 agent 生成唯一的「提取总结 AI 消息」键名（原地写入 config）。

    规则与 assign_state_messages_keys 完全一致，只是：
    - 主图保留字段是 `extracted_summery_ai_msg_as_str`；
    - 生成的基础键名是 `<slugified_name>_extracted_summery_ai_msg`。
    """
    seen: set[str] = set()
    used_by_main = {"extracted_summery_ai_msg_as_str"}
    for sub in config.sub_agents:
        if (
            sub.extracted_summery_ai_msg_key
            and sub.extracted_summery_ai_msg_key not in seen
            and sub.extracted_summery_ai_msg_key not in used_by_main
        ):
            seen.add(sub.extracted_summery_ai_msg_key)
            continue
        base = f"{slugify(sub.name)}_extracted_summery_ai_msg"
        key = base
        i = 2
        while key in seen or key in used_by_main:
            key = f"{base}_{i}"
            i += 1
        sub.extracted_summery_ai_msg_key = key
        seen.add(key)
