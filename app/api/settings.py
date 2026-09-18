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
    # 记忆吸附、默认总结百分比、记忆相似度门槛在构建图时被闭包捕获，变更后需全量失效重建
    if (
        old.memory_attach != body.memory_attach
        or old.num_memories_attached != body.num_memories_attached
        or old.summary_token_percent != body.summary_token_percent
        or old.search_memory_threshold != body.search_memory_threshold
        or old.attach_memory_threshold != body.attach_memory_threshold
    ):
        await chat_manager.invalidate_all()
    return body.model_dump()
