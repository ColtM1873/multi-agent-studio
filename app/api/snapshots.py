"""快照 REST API：列表 / 读取 / 删除 / 重命名，以及「已删除 multi-agent」的快照管理。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.deps import config_store
from app.services import snapshot as snapshot_service

router = APIRouter(prefix="/api/agents/{agent_id}/snapshots", tags=["snapshots"])

# 已删除 multi-agent 的快照管理（顶层，不属于某个现存 agent）
deleted_router = APIRouter(prefix="/api/deleted-agents", tags=["snapshots"])


class RenameSnapshotBody(BaseModel):
    """重命名请求体：只有「会话名」，日期/时间/条数序列号由后端保留。"""

    name: str


class EditSnapshotMessageBody(BaseModel):
    """快照内编辑：替换一条消息（与普通会话编辑模式的提交结构对齐）。

    - ``subagent_name``：None/空串 = 主图，否则为该子 agent 的消息；
    - ``msg_indice``：消息下标（= 渲染时的 ``data-msg-indice``）；
    - ``substitute_msg``：完整替换消息（前端只改目标字段，其余原样回传）。
    """

    subagent_name: str | None = None
    msg_indice: int
    substitute_msg: dict


@router.get("")
def list_snapshots(agent_id: str):
    return snapshot_service.list_snapshots(agent_id)


@router.get("/{snapshot_id}")
def get_snapshot(agent_id: str, snapshot_id: str):
    try:
        rec = snapshot_service.read_snapshot(agent_id, snapshot_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if rec is None:
        raise HTTPException(status_code=404, detail="快照不存在")
    return rec


@router.delete("/{snapshot_id}")
def delete_snapshot(agent_id: str, snapshot_id: str):
    try:
        ok = snapshot_service.delete_snapshot(agent_id, snapshot_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="快照不存在")
    return {"ok": True}


@router.post("/{snapshot_id}/rename")
def rename_snapshot(agent_id: str, snapshot_id: str, body: RenameSnapshotBody):
    """重命名快照的「会话名」，保留日期 / 时间 / 消息条数序列号。"""
    try:
        return snapshot_service.rename_snapshot(agent_id, snapshot_id, body.name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="快照不存在")
    except FileExistsError:
        raise HTTPException(status_code=409, detail="该名称下已存在同序列号的快照")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{snapshot_id}/messages")
def get_snapshot_messages(agent_id: str, snapshot_id: str, subagent_name: str | None = None):
    """读取快照里保存的原始消息（快照内编辑模式进入时取数，与 /messages 同构）。"""
    try:
        messages = snapshot_service.read_snapshot_messages(agent_id, snapshot_id, subagent_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="快照不存在")
    except snapshot_service.SnapshotNotEditable as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"messages": messages}


@router.post("/{snapshot_id}/edit")
def edit_snapshot_message(agent_id: str, snapshot_id: str, body: EditSnapshotMessageBody):
    """在快照 JSON 内替换一条消息并重渲染 Markdown（不跑图、不碰会话数据库）。"""
    try:
        return snapshot_service.edit_snapshot_message(
            agent_id,
            snapshot_id,
            body.subagent_name,
            body.msg_indice,
            body.substitute_msg,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="快照不存在")
    except snapshot_service.SnapshotNotEditable as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@deleted_router.get("")
def list_deleted_agents():
    """列出仍有快照、但配置已被删除的 multi-agent。"""
    existing = {c.agent_id for c in config_store.list()}
    return snapshot_service.list_deleted_agents(existing)


@deleted_router.delete("/{agent_id}/snapshots")
def delete_all_snapshots(agent_id: str):
    """删除某个已删除 multi-agent 名下的全部快照（整个目录）。"""
    n = snapshot_service.delete_all_snapshots(agent_id)
    if n == 0:
        raise HTTPException(status_code=404, detail="没有可删除的快照")
    return {"ok": True, "deleted": n}
