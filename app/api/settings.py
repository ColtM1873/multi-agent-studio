"""全局系统设置 REST API。"""

from __future__ import annotations

from fastapi import APIRouter

from app.config.settings import Settings, load_settings, save_settings
from app.deps import chat_manager, config_store

router = APIRouter(prefix="/api", tags=["settings"])


@router.get("/settings")
def get_settings():
    return load_settings(config_store._dir).model_dump()


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


@router.get("/browser-timing")
def get_browser_timing():
    """浏览器交互延迟的有效配置（默认值深合并用户覆盖项）与原始默认值。"""
    from browser_agent import timing as ba_timing

    settings = load_settings(config_store._dir)
    defaults = ba_timing.defaults()
    return {
        "config": _deep_merge(defaults, settings.browser_timing or {}),
        "defaults": defaults,
    }


@router.put("/settings")
async def put_settings(body: Settings):
    old = load_settings(config_store._dir)
    save_settings(config_store._dir, body)
    # 记忆吸附、默认总结百分比、记忆相似度门槛、PDF 表格提取在构建图时被闭包捕获，变更后需全量失效重建
    if (
        old.memory_attach != body.memory_attach
        or old.num_memories_attached != body.num_memories_attached
        or old.summary_token_percent != body.summary_token_percent
        or old.search_memory_threshold != body.search_memory_threshold
        or old.attach_memory_threshold != body.attach_memory_threshold
        or old.pdf_table_extraction != body.pdf_table_extraction
    ):
        await chat_manager.invalidate_all()
    return body.model_dump()
