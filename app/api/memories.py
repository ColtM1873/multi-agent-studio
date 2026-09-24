"""长期记忆库（store）REST API：按 multi-agent 命名空间列出 / 删除 / 编辑。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.deps import config_store
from app.services import memories as memories_service

router = APIRouter(prefix="/api/agents/{agent_id}/memories", tags=["memories"])


class UpdateMemoryBody(BaseModel):
    value: dict[str, str] = Field(default_factory=dict)


def _load(agent_id: str):
    try:
        return config_store.load(agent_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="配置不存在")


@router.get("")
async def list_memories(agent_id: str, limit: int = 1000, offset: int = 0):
    config = _load(agent_id)
    try:
        return await memories_service.list_memories(config, limit=limit, offset=offset)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"读取记忆库失败: {e}")


@router.put("/{key}")
async def update_memory(agent_id: str, key: str, body: UpdateMemoryBody):
    config = _load(agent_id)
    value = {str(k): str(v) for k, v in body.value.items()}
    if not value:
        raise HTTPException(status_code=400, detail="记忆内容不能为空")
    try:
        await memories_service.update_memory(config, key, value)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"编辑记忆失败: {e}")
    return {"ok": True}


@router.delete("/{key}")
async def delete_memory(agent_id: str, key: str):
    config = _load(agent_id)
    try:
        await memories_service.delete_memory(config, key)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"删除记忆失败: {e}")
    return {"ok": True}
