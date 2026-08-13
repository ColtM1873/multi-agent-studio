"""动态生成 LangGraph state class。

规则（重要）：
- 主图消息键固定为 `messages`。
- 每个子图的消息键 `state_messages_key` 必须全局唯一（父子消息键不同，否则冲突）。
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

    return MainAgentState


def make_sub_agent_state(messages_key: str) -> type:
    """子图 state，等价于原来的 DocsState / WebSearchState 等。"""
    fields: dict[str, Any] = {
        messages_key: Annotated[list[AnyMessage], add_messages],
        **SHARED_FIELDS,
    }
    return type(
        f"SubAgentState_{messages_key}",
        (),
        {"__annotations__": fields},
    )


def assign_state_messages_keys(config: MultiAgentConfig) -> None:
    """为每个子 agent 生成唯一的消息通道键名（原地写入 config）。"""
    seen: set[str] = set()
    used_by_main = {"messages"}
    for sub in config.sub_agents:
        if sub.state_messages_key and sub.state_messages_key not in seen and sub.state_messages_key not in used_by_main:
            seen.add(sub.state_messages_key)
            continue
        base = f"{slugify(sub.name)}_messages"
        key = base
        i = 2
        while key in seen or key in used_by_main:
            key = f"{base}_{i}"
            i += 1
        sub.state_messages_key = key
        seen.add(key)
