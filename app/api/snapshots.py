"""快照 REST API：列表 / 读取 / 删除。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.services import snapshot as snapshot_service

router = APIRouter(prefix="/api/agents/{agent_id}/snapshots", tags=["snapshots"])


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
