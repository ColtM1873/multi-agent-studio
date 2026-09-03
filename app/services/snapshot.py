"""快照服务：在全量总结前，把当前线程的完整历史（主 agent + 所有子 agent）渲染为 Markdown，
连同元数据一起落盘到 <项目根>/snapshots/<agent_id>/ 下的 JSON 文件。

原则：渲染历史会话时用什么数据，快照就存什么数据。因此这里直接复用
`history_render` 与 `threads` 服务里与历史浏览完全相同的读取 + 渲染逻辑，
前端打开快照后即可获得与「浏览历史会话」一致的体验（含子 agent 切换）。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.config.settings import load_settings
from app.services.history_render import (
    _find_messages_channel,
    render_checkpoint_to_markdown_string,
    render_messages_to_markdown_string,
)
from app.services import threads as threads_service

logger = logging.getLogger(__name__)

# app/services/snapshot.py → 上溯到项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent.parent
SNAPSHOTS_DIR = BASE_DIR / "snapshots"
CONFIG_DIR = BASE_DIR / "configs"


def _agent_snapshots_dir(agent_id: str) -> Path:
    safe = re.sub(r"[^\w\-]+", "_", str(agent_id)).strip("_") or "agent"
    return SNAPSHOTS_DIR / safe


def _safe_filename(name: str) -> str:
    return re.sub(r"[^\w\-]+", "_", str(name)).strip("_") or "thread"


def _sanitize_id(snapshot_id: str) -> str:
    sid = Path(str(snapshot_id)).name
    if sid in ("", ".", ".."):
        raise ValueError("invalid snapshot id")
    return sid


def _history_settings() -> dict:
    """与历史浏览保持一致的渲染开关（思考/工具调用/工具结果是否默认展开、HTML 导出）。"""
    settings = load_settings(CONFIG_DIR)
    return {
        "reasoning_expanded": settings.reasoning_expanded,
        "tool_call_expanded": settings.tool_call_expanded,
        "tool_result_expanded": settings.tool_result_expanded,
        "export_html": settings.export_html,
    }


def _count_messages(channel_values: dict) -> int:
    key, msgs = _find_messages_channel(channel_values)
    if not key:
        msgs = channel_values.get("messages", [])
    return len(msgs) if isinstance(msgs, list) else 0


async def _collect_snapshot_data(conn_string: str, thread_id: str) -> dict | None:
    settings = _history_settings()

    main_md = None
    main_count = 0
    async with AsyncPostgresSaver.from_conn_string(conn_string) as cp:
        await cp.setup()
        main_tuple = await cp.aget_tuple({"configurable": {"thread_id": thread_id}})
    if main_tuple:
        main_md = render_checkpoint_to_markdown_string(main_tuple, **settings)
        main_count = _count_messages(main_tuple.checkpoint.get("channel_values", {}))

    subgraphs = []
    total = main_count
    nodes = await threads_service.list_subgraph_nodes(conn_string, thread_id)
    for node in nodes:
        name = node["node_name"]
        merged = await threads_service.get_subgraph_messages(conn_string, thread_id, name)
        if not merged:
            continue
        md = render_messages_to_markdown_string(merged, title=f"子图 [{name}] 历史", **settings)
        subgraphs.append({"name": name, "markdown": md})
        total += len(merged)

    if main_md is None and not subgraphs:
        return None

    return {"main": main_md or "", "subgraphs": subgraphs, "total_messages": total}


async def capture_snapshot(conn_string: str, thread_id: str, agent_id: str) -> dict | None:
    """采集并落盘一次快照。返回元数据（id/thread_id/created_at/total_messages），失败返回 None。"""
    try:
        data = await _collect_snapshot_data(conn_string, thread_id)
    except Exception:  # noqa: BLE001 — 快照失败绝不影响总结主流程
        logger.exception("采集快照数据失败 (thread_id=%s)", thread_id)
        return None
    if data is None:
        return None

    now = datetime.now()
    created_at = now.strftime("%Y-%m-%d %H:%M:%S")
    stamp = now.strftime("%Y%m%d_%H%M%S_%f")
    total = data["total_messages"]
    filename = f"{_safe_filename(thread_id)}_{stamp}_{total}.json"

    record = {
        "agent_id": agent_id,
        "thread_id": thread_id,
        "created_at": created_at,
        "total_messages": total,
        "main": data["main"],
        "subgraphs": data["subgraphs"],
    }

    try:
        out_dir = _agent_snapshots_dir(agent_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / filename
        out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        logger.exception("写快照文件失败 (agent_id=%s)", agent_id)
        return None

    return {
        "id": filename,
        "thread_id": thread_id,
        "created_at": created_at,
        "total_messages": total,
    }


def list_snapshots(agent_id: str) -> list[dict]:
    d = _agent_snapshots_dir(agent_id)
    if not d.exists():
        return []
    result = []
    for p in d.glob("*.json"):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        result.append(
            {
                "id": p.name,
                "thread_id": rec.get("thread_id", ""),
                "created_at": rec.get("created_at", ""),
                "total_messages": rec.get("total_messages", 0),
            }
        )
    result.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return result


def read_snapshot(agent_id: str, snapshot_id: str) -> dict | None:
    snapshot_id = _sanitize_id(snapshot_id)
    p = _agent_snapshots_dir(agent_id) / snapshot_id
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def delete_snapshot(agent_id: str, snapshot_id: str) -> bool:
    snapshot_id = _sanitize_id(snapshot_id)
    p = _agent_snapshots_dir(agent_id) / snapshot_id
    if p.exists():
        p.unlink()
        return True
    return False
