"""快照 REST API：列表 / 读取 / 删除，以及「已删除 multi-agent」的快照管理。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.deps import config_store
from app.services import snapshot as snapshot_service

router = APIRouter(prefix="/api/agents/{agent_id}/snapshots", tags=["snapshots"])

# 已删除 multi-agent 的快照管理（顶层，不属于某个现存 agent）
deleted_router = APIRouter(prefix="/api/deleted-agents", tags=["snapshots"])


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
