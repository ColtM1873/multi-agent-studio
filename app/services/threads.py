"""线程（thread）数据访问层：列表 / 删除 / 历史渲染 / 子图命名空间。"""

from __future__ import annotations

import asyncio

from psycopg import AsyncConnection
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.services.history_render import (
    _find_messages_channel,
    render_checkpoint_to_markdown_string,
    render_messages_to_markdown_string,
)


async def test_connection(conn_string: str, timeout: int = 5) -> tuple[bool, str]:
    """测试数据库连接（用于创建 multi-agent 时的建库提醒）。"""
    try:
        async with await asyncio.wait_for(
            AsyncConnection.connect(conn_string, connect_timeout=timeout), timeout=timeout + 2
        ):
            return True, ""
    except Exception as e:
        return False, str(e)


async def list_threads(conn_string: str) -> list[dict]:
    async with await AsyncConnection.connect(conn_string) as conn:
        await conn.set_autocommit(True)
        rows = await conn.execute(
            """
            SELECT thread_id,
                   COUNT(*) AS checkpoints,
                   to_char(MAX((checkpoint->>'ts')::timestamptz), 'YYYY-MM-DD HH24:MI') AS last_updated
            FROM checkpoints
            GROUP BY thread_id
            ORDER BY last_updated DESC
            """
        )
        return [
            {"thread_id": r[0], "checkpoints": r[1], "last_updated": r[2]}
            for r in await rows.fetchall()
        ]


async def delete_thread(conn_string: str, thread_id: str) -> None:
    async with await AsyncConnection.connect(conn_string) as conn:
        await conn.set_autocommit(True)
        await conn.execute("DELETE FROM checkpoints WHERE thread_id = %s", (thread_id,))
        await conn.execute("DELETE FROM checkpoint_blobs WHERE thread_id = %s", (thread_id,))
        await conn.execute("DELETE FROM checkpoint_writes WHERE thread_id = %s", (thread_id,))


async def get_thread_history_markdown(conn_string: str, thread_id: str) -> str | None:
    async with AsyncPostgresSaver.from_conn_string(conn_string) as cp:
        await cp.setup()
        cp_tuple = await cp.aget_tuple({"configurable": {"thread_id": thread_id}})
    if not cp_tuple:
        return None
    return render_checkpoint_to_markdown_string(cp_tuple)


async def list_subgraph_nodes(conn_string: str, thread_id: str) -> list[dict]:
    """按子 agent 名（node_name）去重聚合，返回唯一子图节点。"""
    async with await AsyncConnection.connect(conn_string) as conn:
        await conn.set_autocommit(True)
        rows = await conn.execute(
            """
            SELECT split_part(checkpoint_ns, ':', 1) AS node_name,
                   COUNT(*),
                   to_char(MAX((checkpoint->>'ts')::timestamptz), 'YYYY-MM-DD HH24:MI') AS last_updated
            FROM checkpoints
            WHERE thread_id = %s AND checkpoint_ns != ''
            GROUP BY node_name
            ORDER BY MAX(checkpoint_id) DESC
            """,
            (thread_id,),
        )
        return [
            {"node_name": r[0], "checkpoints": r[1], "last_updated": r[2]}
            for r in await rows.fetchall()
        ]


async def get_subgraph_history_by_node(conn_string: str, thread_id: str, node_name: str) -> str | None:
    """按子 agent 名聚合其所有调用（namespace）的历史，按时间排序合并。"""
    async with await AsyncConnection.connect(conn_string) as conn:
        await conn.set_autocommit(True)
        rows = await conn.execute(
            "SELECT DISTINCT checkpoint_ns FROM checkpoints "
            "WHERE thread_id = %s AND (checkpoint_ns = %s OR checkpoint_ns LIKE %s)",
            (thread_id, node_name, node_name + ":%"),
        )
        namespaces = [r[0] for r in await rows.fetchall()]

    if not namespaces:
        return None

    tuples = []
    async with AsyncPostgresSaver.from_conn_string(conn_string) as cp:
        await cp.setup()
        for ns in namespaces:
            t = await cp.aget_tuple({"configurable": {"thread_id": thread_id, "checkpoint_ns": ns}})
            if t:
                tuples.append(t)

    if not tuples:
        return None

    tuples.sort(key=lambda t: t.checkpoint.get("ts", ""))

    merged = []
    for t in tuples:
        channel_values = t.checkpoint.get("channel_values", {})
        msg_key, msgs = _find_messages_channel(channel_values)
        if not msg_key:
            msgs = channel_values.get("messages", [])
        merged.extend(msgs)

    if not merged:
        return None

    return render_messages_to_markdown_string(merged, title=f"子图 [{node_name}] 历史")
