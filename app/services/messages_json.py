"""LangChain 消息对象 <-> JSON-safe dict 的序列化/反序列化。

供「历史消息编辑」使用：前端需要拿到完整消息数据（含 usage_metadata / tool_calls /
content 块结构）来渲染编辑态并构造 substitute_msg，后端需要把前端回传的 dict 还原成
真实的 Message 对象（graph 的 edit 节点用 type(a) is type(b) 做严格类型匹配）。

只做 faithful 往返，不做业务逻辑。
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


def message_to_dict(msg) -> dict[str, Any]:
    """Message -> dict（重点保留 content / tool_calls / usage_metadata / id）。"""
    t = type(msg).__name__
    d: dict[str, Any] = {"type": t, "content": msg.content}
    if getattr(msg, "id", None):
        d["id"] = msg.id
    if t == "AIMessage":
        d["tool_calls"] = list(getattr(msg, "tool_calls", []) or [])
        um = getattr(msg, "usage_metadata", {}) or {}
        if um:
            d["usage_metadata"] = dict(um)
    if t == "ToolMessage":
        d["name"] = getattr(msg, "name", "") or ""
        d["tool_call_id"] = getattr(msg, "tool_call_id", "") or ""
    if getattr(msg, "response_metadata", None):
        d["response_metadata"] = msg.response_metadata
    if getattr(msg, "additional_kwargs", None):
        d["additional_kwargs"] = msg.additional_kwargs
    return d


def dict_to_message(d: dict[str, Any]):
    """dict -> Message（按 type 决定构造哪个类，类型必须与原始严格一致）。"""
    t = d.get("type")
    content = d.get("content")
    kwargs: dict[str, Any] = {}
    if d.get("id"):
        kwargs["id"] = d["id"]
    if d.get("response_metadata"):
        kwargs["response_metadata"] = d["response_metadata"]
    if d.get("additional_kwargs"):
        kwargs["additional_kwargs"] = d["additional_kwargs"]

    if t == "HumanMessage":
        return HumanMessage(content=content, **kwargs)
    if t == "AIMessage":
        kwargs["tool_calls"] = d.get("tool_calls", [])
        if d.get("usage_metadata"):
            kwargs["usage_metadata"] = d["usage_metadata"]
        return AIMessage(content=content, **kwargs)
    if t == "ToolMessage":
        kwargs["name"] = d.get("name")
        kwargs["tool_call_id"] = d.get("tool_call_id")
        return ToolMessage(content=content, **kwargs)
    raise ValueError(f"unsupported message type: {t}")
