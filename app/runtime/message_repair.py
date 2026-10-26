"""修复 provider 不接受的 tool_call / ToolMessage 配对。

背景：硬取消（前端断连、进程退出、旧版本点「停止」）会让「AI 带 tool_calls」的
超级步已经提交、而 tool 节点那一超级步没有提交，于是历史里留下「有 tool_call、
没有对应 ToolMessage」的缺口。下一次把 messages 原样发给 provider 时，OpenAI 兼容
接口（含 DeepSeek）/ Anthropic 等会以 400 拒绝。

软取消（RunControl.request_drain）能消掉大部分来源，但「drain 落在 LLM 节点之后、
tool 节点之前」时仍会留下尾巴；旧 thread 里也可能已经有脏数据。所以这里提供一层
与图无关的纯函数修复：
- 发给 provider 之前：补缺失的 ToolMessage、丢弃孤儿/重复的 ToolMessage（不改 state）；
- drain 结束后：只修最后一条 AI 消息的尾巴（写回 state，让历史也干净）。
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, AnyMessage, ToolMessage

# 合成结果的文本：用户明确点击中止，工具调用取消。
CANCELLED_TOOL_CONTENT = "用户点击中止按钮，工具调用取消"


def make_cancelled_tool_message(tool_call: dict) -> ToolMessage | None:
    """给一个未完成的 tool_call 造一条「已取消」的 ToolMessage。"""
    tool_call_id = tool_call.get("id")
    if not tool_call_id:
        return None
    return ToolMessage(
        content=CANCELLED_TOOL_CONTENT,
        tool_call_id=tool_call_id,
        name=tool_call.get("name") or "unknown",
    )


def _tool_calls_of(msg: AnyMessage) -> list[dict]:
    """取这条 AI 消息里所有会被发给 provider 的 tool_call。

    注意：langchain-openai 会把 `invalid_tool_calls`（参数被截断/非法）也序列化进
    请求的 `tool_calls` 字段，所以它们同样需要 ToolMessage 应答，否则一样 400。
    """
    if not isinstance(msg, AIMessage):
        return []
    calls = list(msg.tool_calls or []) + list(
        getattr(msg, "invalid_tool_calls", None) or []
    )
    out: list[dict] = []
    seen: set[str] = set()
    for tc in calls:
        tool_call_id = tc.get("id")
        if not tool_call_id or tool_call_id in seen:
            continue
        seen.add(tool_call_id)
        out.append(tc)
    return out


def repair_tool_pairs(messages: list[AnyMessage]) -> list[AnyMessage]:
    """返回一份可直接发给 provider 的消息副本。

    规则：
    - 缺 ToolMessage 的 tool_call：紧跟在该 AI 消息之后补一条「工具调用取消」的结果；
    - ToolMessage 的 tool_call_id 对不上任何「尚未应答」的 tool_call：丢掉（孤儿/重复）；
    - 其它消息与顺序保持不变（SystemMessage 由调用方自己拼在最前）。

    只在请求副本上做，不回写 state —— 不污染历史、不影响编辑功能的消息下标。
    """
    out: list[AnyMessage] = []
    pending: list[dict] = []

    def flush_pending() -> None:
        for tool_call in pending:
            synthetic = make_cancelled_tool_message(tool_call)
            if synthetic is not None:
                out.append(synthetic)
        pending.clear()

    for msg in messages:
        if isinstance(msg, ToolMessage):
            tool_call_id = getattr(msg, "tool_call_id", None)
            if tool_call_id and any(tc["id"] == tool_call_id for tc in pending):
                pending[:] = [tc for tc in pending if tc["id"] != tool_call_id]
                out.append(msg)
            # 孤儿 / 重复应答：静默丢弃，否则 provider 会报「tool 消息没有对应 tool_call」
            continue
        if pending:
            flush_pending()
        out.append(msg)
        calls = _tool_calls_of(msg)
        if calls:
            pending = calls

    if pending:
        flush_pending()
    return out


def missing_tail_tool_calls(messages: list[AnyMessage]) -> list[dict]:
    """只看最后一条消息：若它是带 tool_calls 的 AI 消息，返回其中没有应答的 call。

    用于 drain 结束后的「尾巴修复」——此时 AI 消息是最后一条，追加 ToolMessage 顺序天然正确。
    更早历史里的悬空由 repair_tool_pairs 在请求时兜底。
    """
    if not messages:
        return []
    last = messages[-1]
    calls = _tool_calls_of(last)
    if not calls:
        return []
    answered = {
        msg.tool_call_id for msg in messages if isinstance(msg, ToolMessage)
    }
    return [tc for tc in calls if tc["id"] not in answered]
