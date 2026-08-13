"""Checkpoint → Markdown 字符串渲染（迁自 view_checkpoint_md.py，改为返回字符串供 GUI/API 使用）。"""

from __future__ import annotations

import io
from datetime import datetime

from langgraph.checkpoint.base import CheckpointTuple


def _find_messages_channel(channel_values: dict):
    """自动探测 channel_values 里哪个 key 是消息列表。"""
    for key, val in channel_values.items():
        if isinstance(val, list) and val and hasattr(val[0], "content"):
            return key, val
    return None, []


def render_checkpoint_to_markdown_string(
    cp_tuple: CheckpointTuple,
    *,
    show_reasoning: bool = True,
    show_tool_calls: bool = True,
    max_tool_result_lines: int = 50,
    messages_key=None,
    title: str = "Checkpoint",
) -> str:
    buf = io.StringIO()
    render_checkpoint_to_markdown(
        cp_tuple,
        buf,
        show_reasoning=show_reasoning,
        show_tool_calls=show_tool_calls,
        max_tool_result_lines=max_tool_result_lines,
        messages_key=messages_key,
        title=title,
    )
    return buf.getvalue()


def render_checkpoint_to_markdown(
    cp_tuple: CheckpointTuple,
    md_file,
    *,
    show_reasoning: bool = True,
    show_tool_calls: bool = True,
    max_tool_result_lines: int = 50,
    messages_key=None,
    title: str = "Checkpoint",
):
    w = md_file.write
    cp = cp_tuple.checkpoint
    metadata = cp_tuple.metadata or {}

    ts_raw = cp.get("ts", "")
    try:
        dt = datetime.fromisoformat(ts_raw)
        local_dt = dt.astimezone()
        ts_str = local_dt.strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        ts_str = ts_raw

    source = metadata.get("source", "?")
    step = metadata.get("step", "?")

    w(f"## 📋 {title}\n\n")
    w("| 属性 | 值 |\n")
    w("|---|---|\n")
    w(f"| 时间 | {ts_str} |\n")
    w(f"| Source | `{source}` |\n")
    w(f"| Step | `{step}` |\n")

    tid = cp_tuple.config.get("configurable", {}).get("thread_id", "")
    cid = cp_tuple.config.get("configurable", {}).get("checkpoint_id", "")
    if tid:
        w(f"| Thread | `{tid}` |\n")
    if cid:
        w(f"| Checkpoint ID | `{cid}` |\n")
    w("\n---\n\n")

    channel_values = cp.get("channel_values", {})
    if messages_key:
        messages = channel_values.get(messages_key, [])
    else:
        messages = channel_values.get("messages", [])
        if not messages:
            _, messages = _find_messages_channel(channel_values)

    render_messages(
        messages,
        md_file,
        show_reasoning=show_reasoning,
        show_tool_calls=show_tool_calls,
        max_tool_result_lines=max_tool_result_lines,
    )


def render_messages(
    messages,
    md_file,
    *,
    title: str | None = None,
    show_reasoning: bool = True,
    show_tool_calls: bool = True,
    max_tool_result_lines: int = 50,
):
    """渲染一个消息列表（供单 checkpoint 与聚合子图历史复用）。"""
    w = md_file.write
    if title:
        w(f"## {title}\n\n")

    counts = {"HumanMessage": 0, "AIMessage": 0, "ToolMessage": 0, "SystemMessage": 0}
    human_index = 0
    for msg in messages:
        msg_type = type(msg).__name__
        counts[msg_type] = counts.get(msg_type, 0) + 1
        if msg_type == "HumanMessage":
            _render_human(msg, w, human_index)
            human_index += 1
        elif msg_type == "AIMessage":
            _render_ai(msg, w, show_reasoning, show_tool_calls)
        elif msg_type == "ToolMessage":
            _render_tool(msg, w, max_tool_result_lines)
        elif msg_type == "SystemMessage":
            w("<details><summary>📌 SystemMessage</summary>\n\n")
            w(f"{_escape_md(str(msg.content))}\n\n")
            w("</details>\n\n")
        else:
            w(f"<details><summary>📦 {msg_type}</summary>\n\n")
            w(f"```\n{_escape_md(str(msg.content))[:2000]}\n```\n\n")
            w("</details>\n\n")

    w('<div id="stats-anchor" class="msg-stats">📊 消息统计 · ' + " · ".join(
        f"{mtype} {cnt}" for mtype, cnt in counts.items() if cnt > 0
    ))

    total_input = 0
    total_output = 0
    for msg in messages:
        if type(msg).__name__ == "AIMessage":
            um = getattr(msg, "usage_metadata", {}) or {}
            total_input += um.get("input_tokens", 0)
            total_output += um.get("output_tokens", 0)
    if total_input or total_output:
        w(f" · Input {total_input:,} tok · Output {total_output:,} tok")
    w("</div>\n\n")


def render_messages_to_markdown_string(
    messages,
    *,
    title: str | None = None,
    show_reasoning: bool = True,
    show_tool_calls: bool = True,
    max_tool_result_lines: int = 50,
) -> str:
    buf = io.StringIO()
    render_messages(
        messages,
        buf,
        title=title,
        show_reasoning=show_reasoning,
        show_tool_calls=show_tool_calls,
        max_tool_result_lines=max_tool_result_lines,
    )
    return buf.getvalue()


def _render_human(msg, w, idx: int):
    content = str(msg.content)
    escaped = _escape_html(content).replace("\n", "<br>")
    w(f'<div id="user-msg-{idx}" class="user-msg-block">\n')
    w('<div class="user-msg-head">🧑 <strong>用户</strong></div>\n')
    w(f'<blockquote class="user-msg-quote">{escaped}</blockquote>\n')
    w("</div>\n")


def _render_ai(msg, w, show_reasoning, show_tool_calls):
    content = msg.content
    tool_calls = getattr(msg, "tool_calls", []) or []
    um = getattr(msg, "usage_metadata", {}) or {}
    input_tok = um.get("input_tokens", "?")
    output_tok = um.get("output_tokens", "?")
    cache = um.get("input_token_details", {}).get("cache_read", 0)

    w('<div style="border-left: 3px solid #4CAF50; padding-left: 12px;">\n\n')
    w("**🤖 Assistant**  ")
    tok_str = f"↑{input_tok} ↓{output_tok}"
    if cache:
        tok_str += f"  💾cache:{cache}"
    w(f"<sub>{tok_str}</sub>\n\n")

    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "")
            if btype == "reasoning" and show_reasoning:
                r = block.get("reasoning", "")
                w("<details>\n<summary>🧠 思考过程</summary>\n\n")
                w(f"{r}\n\n")
                w("</details>\n\n")
            elif btype == "text":
                w(f"{block.get('text', '')}\n\n")
            elif btype == "tool_call" and show_tool_calls:
                pass
    elif isinstance(content, str):
        w(f"{content}\n\n")

    if show_tool_calls and tool_calls:
        for tc in tool_calls:
            tc_name = tc.get("name", "?")
            w(f"<details>\n<summary>🔧 `{tc_name}`</summary>\n\n")
            w(f"```json\n{_format_args(tc.get('args', {}))}\n```\n")
            w("</details>\n\n")
    w("</div>\n\n")


def _render_tool(msg, w, max_lines):
    name = getattr(msg, "name", "") or "unknown_tool"
    content = msg.content
    if isinstance(content, list):
        text_parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif block.get("type") == "reasoning":
                    text_parts.append(f"[reasoning: {block.get('reasoning', '')[:200]}...]")
        content = "\n\n".join(text_parts)

    content_str = str(content)
    lines = content_str.split("\n")
    line_count = len(lines)
    if line_count <= max_lines:
        w(f"<details open>\n<summary>✅ Tool 结果: `{name}` ({line_count} 行)</summary>\n\n")
        w(f"{content_str}\n\n")
        w("</details>\n\n")
    else:
        preview = "\n".join(lines[:max_lines])
        w(f"<details>\n<summary>✅ Tool 结果: `{name}` ({line_count} 行 — 点击展开)</summary>\n\n")
        w(f"{preview}\n\n")
        w(f"...（共 {line_count} 行，仅显示前 {max_lines} 行）\n\n")
        w("</details>\n\n")


def _format_args(args: dict, max_str_len: int = 500) -> str:
    import json

    formatted = {}
    for k, v in args.items():
        if isinstance(v, str) and len(v) > max_str_len:
            formatted[k] = v[:max_str_len] + f"...（截断，原 {len(v)} 字符）"
        else:
            formatted[k] = v
    return json.dumps(formatted, indent=2, ensure_ascii=False)


def _escape_md(text: str) -> str:
    return text


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;").replace("'", "&#39;")
    )
