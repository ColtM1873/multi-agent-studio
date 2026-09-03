"""配置编辑约束：创建后哪些字段可改、哪些不可改。"""

from __future__ import annotations

from app.config.models import MultiAgentConfig


class EditRuleViolation(Exception):
    pass


def validate_edits(existing: MultiAgentConfig, incoming: MultiAgentConfig) -> None:
    """校验编辑是否违反"身份绑定"约束。不合法则抛 EditRuleViolation。"""
    if incoming.agent_id != existing.agent_id:
        raise EditRuleViolation("agent_id 不可修改")
    if incoming.name != existing.name:
        raise EditRuleViolation("name 不可修改（名称与历史会话绑定）")
    if incoming.postgres.checkpoint_database != existing.postgres.checkpoint_database:
        raise EditRuleViolation("checkpoint_database 不可修改（会话历史归属该库）")

    existing_names = [s.name for s in existing.sub_agents]
    incoming_names = [s.name for s in incoming.sub_agents]
    if existing_names != incoming_names:
        raise EditRuleViolation("子 agent 不允许增删或改名")


def apply_edits(existing: MultiAgentConfig, incoming: MultiAgentConfig) -> MultiAgentConfig:
    """校验并返回可保存的新配置。

    规则：agent_id / name / checkpoint_database / 子 agent 集合（名字）不可变；
    其余（主/子 prompt、description、MCP、模型、API key、阈值、store_database、
    连接凭据等）均可变。
    """
    validate_edits(existing, incoming)
    # 保留原有 state_messages_key / history_token_measure_key / extracted_summery_ai_msg_key（子 agent 名未变，键名也保持稳定）
    msg_key_map = {s.name: s.state_messages_key for s in existing.sub_agents}
    token_key_map = {s.name: s.history_token_measure_key for s in existing.sub_agents}
    summery_key_map = {s.name: s.extracted_summery_ai_msg_key for s in existing.sub_agents}
    for sub in incoming.sub_agents:
        if sub.state_messages_key is None:
            sub.state_messages_key = msg_key_map.get(sub.name)
        if sub.history_token_measure_key is None:
            sub.history_token_measure_key = token_key_map.get(sub.name)
        if sub.extracted_summery_ai_msg_key is None:
            sub.extracted_summery_ai_msg_key = summery_key_map.get(sub.name)
    return incoming
