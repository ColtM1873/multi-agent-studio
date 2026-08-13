"""全局系统设置 REST API。"""

from __future__ import annotations

from fastapi import APIRouter

from app.config.settings import Settings, load_settings, save_settings
from app.deps import chat_manager, config_store

router = APIRouter(prefix="/api", tags=["settings"])


@router.get("/settings")
def get_settings():
    return load_settings(config_store._dir).model_dump()


@router.put("/settings")
async def put_settings(body: Settings):
    old = load_settings(config_store._dir)
    save_settings(config_store._dir, body)
    # 记忆吸附相关变更会影响图编译，需全量失效重建
    if old.memory_attach != body.memory_attach or old.num_memories_attached != body.num_memories_attached:
        await chat_manager.invalidate_all()
    return body.model_dump()
