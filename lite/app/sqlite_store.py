"""Lite 版 SQLite 会话历史存储。

为什么是「每个 agent 一个文件 + 单连接串行」：

- 实测（``AsyncSqliteSaver`` + aiosqlite）：**同一个 saver/连接**上多协程并发写
  完全安全（6 个会话并发跑图 0.1s 内完成），内部 ``asyncio.Lock`` 会把写串行化；
- 但**多个连接**同时写同一个 SQLite 文件会直接 ``database is locked``（默认日志
  模式与 WAL 都复现），即使写事务很短；
- 因此本模块让一个 agent 只保留一条连接（Lite 每个 agent 本来也只有一个运行时），
  写入天然串行。不同 agent 用不同文件，互不干扰；一个 agent 的多个会话并发时，
  所有 checkpoint 写入都排在这一条连接上，单次约 0.3~0.5ms，可接受。

读/删也复用这条连接（并在 ``saver.lock`` 内执行原生 SQL），避免与图内 checkpoint
写入跨连接竞争。**不要在持有 ``saver.lock`` 时调用 ``aget_tuple`` / ``alist`` 等
saver 方法**——它们内部会再次加锁（``asyncio.Lock`` 不可重入）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.config import storage_backend as storage
from app.services import snapshot as snapshot_service
from app.services.history_render import (
    _find_messages_channel,
    render_checkpoint_to_markdown_string,
    render_messages_to_markdown_string,
)

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]
CONFIG_DIR = BASE_DIR / "configs"

_THREAD_META_DDL = """
CREATE TABLE IF NOT EXISTS thread_meta (
    thread_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
)
"""

# 每个 agent 一条连接（AsyncSqliteSaver 内含 asyncio.Lock 串行化写）
_SAVERS: dict[str, AsyncSqliteSaver] = {}
_INIT_LOCKS: dict[str, asyncio.Lock] = {}
_EXIT_STACK: contextlib.AsyncExitStack | None = None


# ── 连接管理 ────────────────────────────────────────────────────────


async def _ensure_saver(agent_id: str) -> AsyncSqliteSaver:
    saver = _SAVERS.get(agent_id)
    if saver is not None:
        return saver

    # 首次并发访问（聊天 WS + 线程列表）时只创建一条连接
    init_lock = _INIT_LOCKS.setdefault(agent_id, asyncio.Lock())
    async with init_lock:
        saver = _SAVERS.get(agent_id)
        if saver is not None:
            return saver

        global _EXIT_STACK
        if _EXIT_STACK is None:
            _EXIT_STACK = contextlib.AsyncExitStack()
        db_path = storage.sqlite_db_path(CONFIG_DIR, agent_id)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        saver = await _EXIT_STACK.enter_async_context(
            AsyncSqliteSaver.from_conn_string(str(db_path))
        )
        await saver.setup()
        # WAL 提升「写的同时读」的稳定性（写-写仍必须走同一连接）。
        await _execute(saver, "PRAGMA journal_mode=WAL")
        await _execute(saver, _THREAD_META_DDL)
        _SAVERS[agent_id] = saver
        logger.info("Lite SQLite 会话库已就绪：%s", db_path)
        return saver


async def close_all() -> None:
    """关闭所有 SQLite 连接（应用退出时调用）。"""
    global _EXIT_STACK
    stack = _EXIT_STACK
    _EXIT_STACK = None
    _SAVERS.clear()
    _INIT_LOCKS.clear()
    if stack is not None:
        try:
            await stack.aclose()
        except Exception:  # noqa: BLE001
            logger.exception("关闭 Lite SQLite 连接失败")


# ── 原生 SQL（与 saver 共用一把锁）──────────────────────────────────


async def _execute(saver: AsyncSqliteSaver, sql: str, params: tuple = ()) -> None:
    async with saver.lock:
        cur = await saver.conn.execute(sql, params)
        await cur.close()
        await saver.conn.commit()


async def _fetchall(
    saver: AsyncSqliteSaver, sql: str, params: tuple = ()
) -> list[tuple]:
    async with saver.lock:
        cur = await saver.conn.execute(sql, params)
        try:
            return list(await cur.fetchall())
        finally:
            await cur.close()


async def get_saver(agent_id: str) -> AsyncSqliteSaver:
    """取（或惰性创建）该 agent 的 SQLite saver（供 LiteChatManager 编译图用）。"""
    return await _ensure_saver(agent_id)


def _fmt_ts(value: Any) -> str:
    """checkpoint 的 ISO 时间戳 → 本地 ``YYYY-MM-DD HH:MM``（与 PG 版展示口径一致）。"""
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        return str(value)[:16].replace("T", " ")


def _tuple_ts(tup) -> str:
    try:
        return str(tup.checkpoint.get("ts") or "")
    except Exception:  # noqa: BLE001
        return ""


# ── 会话列表 / 登记 / 删除 ──────────────────────────────────────────


async def register_thread(agent_id: str, thread_id: str) -> None:
    saver = await _ensure_saver(agent_id)
    await _execute(
        saver,
        "INSERT INTO thread_meta (thread_id) VALUES (?) "
        "ON CONFLICT(thread_id) DO NOTHING",
        (thread_id,),
    )


async def delete_thread(agent_id: str, thread_id: str) -> None:
    saver = await _ensure_saver(agent_id)
    async with saver.lock:
        await saver.conn.execute(
            "DELETE FROM thread_meta WHERE thread_id = ?", (thread_id,)
        )
        # SQLite saver 只有 checkpoints / writes 两张表（没有 PG 的 *_blobs 表）
        for table in ("checkpoints", "writes"):
            try:
                await saver.conn.execute(
                    f"DELETE FROM {table} WHERE thread_id = ?", (thread_id,)
                )
            except Exception:  # noqa: BLE001 — 表可能尚未创建
                logger.debug("删除 %s 中的 thread 失败（表可能不存在）", table)
        await saver.conn.commit()


async def list_threads(agent_id: str) -> list[dict]:
    saver = await _ensure_saver(agent_id)
    meta_rows = await _fetchall(
        saver,
        "SELECT thread_id, substr(created_at, 1, 16) FROM thread_meta",
    )
    meta = {r[0]: r[1] for r in meta_rows}

    checkpoint_rows: list[tuple] = []
    try:
        checkpoint_rows = await _fetchall(
            saver,
            "SELECT thread_id, COUNT(*) FROM checkpoints GROUP BY thread_id",
        )
    except Exception:  # noqa: BLE001 — checkpoints 表由 setup 创建，异常时视为空
        logger.exception("读取 SQLite checkpoints 列表失败")

    merged: dict[str, dict] = {
        tid: {"thread_id": tid, "checkpoints": 0, "last_updated": created}
        for tid, created in meta.items()
    }
    for tid, count in checkpoint_rows:
        # 最新 checkpoint 的 ts 需要反序列化，走 saver 接口（不要在 saver.lock 内调用）
        latest = await saver.aget_tuple({"configurable": {"thread_id": tid}})
        last_updated = _fmt_ts(_tuple_ts(latest)) if latest else ""
        merged[tid] = {
            "thread_id": tid,
            "checkpoints": int(count),
            "last_updated": last_updated
            or merged.get(tid, {}).get("last_updated", ""),
        }

    result = list(merged.values())
    result.sort(key=lambda x: x["last_updated"] or "", reverse=True)
    return result


# ── 子图 / 历史读取 ─────────────────────────────────────────────────


async def list_subgraph_nodes(agent_id: str, thread_id: str) -> list[dict]:
    saver = await _ensure_saver(agent_id)
    rows = await _fetchall(
        saver,
        "SELECT checkpoint_ns, COUNT(*) FROM checkpoints "
        "WHERE thread_id = ? AND checkpoint_ns != '' "
        "GROUP BY checkpoint_ns",
        (thread_id,),
    )

    # node_name = checkpoint_ns 的第一段（与 PG 版 split_part(ns, ':', 1) 一致）
    per_node: dict[str, dict] = {}
    for ns, count in rows:
        node_name = str(ns).split(":", 1)[0]
        item = per_node.setdefault(
            node_name,
            {"node_name": node_name, "checkpoints": 0, "last_updated": ""},
        )
        item["checkpoints"] += int(count)
        latest = await saver.aget_tuple(
            {"configurable": {"thread_id": thread_id, "checkpoint_ns": ns}}
        )
        ts = _fmt_ts(_tuple_ts(latest)) if latest else ""
        if ts > item["last_updated"]:
            item["last_updated"] = ts

    result = list(per_node.values())
    result.sort(key=lambda x: x["last_updated"] or "", reverse=True)
    return result


async def get_subgraph_messages(
    agent_id: str, thread_id: str, node_name: str
) -> list:
    """按子 agent 名聚合其所有调用（namespace）的历史，按时间排序合并。"""
    saver = await _ensure_saver(agent_id)
    rows = await _fetchall(
        saver,
        "SELECT DISTINCT checkpoint_ns FROM checkpoints "
        "WHERE thread_id = ? AND (checkpoint_ns = ? OR checkpoint_ns LIKE ?)",
        (thread_id, node_name, node_name + ":%"),
    )
    namespaces = [r[0] for r in rows]
    if not namespaces:
        return []

    tuples = []
    for ns in namespaces:
        t = await saver.aget_tuple(
            {"configurable": {"thread_id": thread_id, "checkpoint_ns": ns}}
        )
        if t:
            tuples.append(t)
    if not tuples:
        return []

    tuples.sort(key=_tuple_ts)
    merged = []
    for t in tuples:
        channel_values = t.checkpoint.get("channel_values", {})
        _key, msgs = _find_messages_channel(channel_values)
        if _key is None:
            msgs = channel_values.get("messages", [])
        merged.extend(msgs)
    return merged


async def get_main_thread_messages(agent_id: str, thread_id: str) -> list:
    saver = await _ensure_saver(agent_id)
    t = await saver.aget_tuple({"configurable": {"thread_id": thread_id}})
    if not t:
        return []
    return t.checkpoint.get("channel_values", {}).get("messages", [])


async def get_subgraph_messages_exact(
    agent_id: str, thread_id: str, node_name: str
) -> list:
    saver = await _ensure_saver(agent_id)
    t = await saver.aget_tuple(
        {"configurable": {"thread_id": thread_id, "checkpoint_ns": node_name}}
    )
    if not t:
        return []
    channel_values = t.checkpoint.get("channel_values", {})
    key, msgs = _find_messages_channel(channel_values)
    if key:
        return msgs
    return channel_values.get("messages", [])


def _last_ai_usage_tokens(messages: list) -> int:
    """取最后一条 AIMessage 的 usage_metadata.total_tokens（与图内口径一致）。"""
    from langchain_core.messages import AIMessage

    for msg in reversed(messages or []):
        if isinstance(msg, AIMessage):
            meta = msg.usage_metadata or {}
            return int(meta.get("total_tokens", 0) or 0)
    return 0


async def get_history_token_usage(
    agent_id: str, thread_id: str, node_name: str | None = None
) -> int:
    if node_name:
        messages = await get_subgraph_messages_exact(agent_id, thread_id, node_name)
    else:
        messages = await get_main_thread_messages(agent_id, thread_id)
    return _last_ai_usage_tokens(messages)


async def get_thread_history_markdown(
    agent_id: str, thread_id: str, **settings
) -> str | None:
    saver = await _ensure_saver(agent_id)
    t = await saver.aget_tuple({"configurable": {"thread_id": thread_id}})
    if not t:
        return None
    return render_checkpoint_to_markdown_string(t, **settings)


async def get_subgraph_history_by_node(
    agent_id: str, thread_id: str, node_name: str, **settings
) -> str | None:
    merged = await get_subgraph_messages(agent_id, thread_id, node_name)
    if not merged:
        return None
    return render_messages_to_markdown_string(
        merged, title=f"子图 [{node_name}] 历史", **settings
    )


# ── 快照（Lite 保留快照功能；数据源换成 SQLite）─────────────────────


async def _collect_snapshot_data(agent_id: str, thread_id: str) -> dict | None:
    settings = snapshot_service._history_settings()

    main_md = None
    main_count = 0
    saver = await _ensure_saver(agent_id)
    main_tuple = await saver.aget_tuple({"configurable": {"thread_id": thread_id}})
    if main_tuple:
        main_md = render_checkpoint_to_markdown_string(main_tuple, **settings)
        main_count = snapshot_service._count_messages(
            main_tuple.checkpoint.get("channel_values", {})
        )

    subgraphs = []
    total = main_count
    nodes = await list_subgraph_nodes(agent_id, thread_id)
    for node in nodes:
        name = node["node_name"]
        merged = await get_subgraph_messages(agent_id, thread_id, name)
        if not merged:
            continue
        md = render_messages_to_markdown_string(
            merged, title=f"子图 [{name}] 历史", **settings
        )
        subgraphs.append({"name": name, "markdown": md})
        total += len(merged)

    if main_md is None and not subgraphs:
        return None

    return {"main": main_md or "", "subgraphs": subgraphs, "total_messages": total}


async def capture_snapshot(
    conn_string: str, thread_id: str, agent_id: str, raise_on_error: bool = False
) -> dict | None:
    """采集并落盘一次快照（签名与 ``app.services.snapshot.capture_snapshot`` 兼容）。

    ``conn_string`` 在 SQLite 模式下没有意义（由 agent_id 定位文件），仅用于保持
    与普通版调用方（graph_builder / session_hub）的签名一致。
    """
    try:
        data = await _collect_snapshot_data(agent_id, thread_id)
    except Exception:  # noqa: BLE001 — 快照失败绝不影响总结主流程
        logger.exception("采集 SQLite 快照数据失败 (thread_id=%s)", thread_id)
        if raise_on_error:
            raise
        return None
    if data is None:
        return None

    now = datetime.now()
    created_at = now.strftime("%Y-%m-%d %H:%M:%S")
    stamp = now.strftime("%Y%m%d_%H%M%S_%f")
    total = data["total_messages"]
    filename = f"{snapshot_service._safe_filename(thread_id)}_{stamp}_{total}.json"

    record = {
        "agent_id": agent_id,
        "thread_id": thread_id,
        "created_at": created_at,
        "total_messages": total,
        "main": data["main"],
        "subgraphs": data["subgraphs"],
    }

    try:
        out_dir = snapshot_service._agent_snapshots_dir(agent_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / filename
        out_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001
        logger.exception("写 SQLite 快照文件失败 (agent_id=%s)", agent_id)
        if raise_on_error:
            raise
        return None

    return {
        "id": filename,
        "thread_id": thread_id,
        "created_at": created_at,
        "total_messages": total,
    }
