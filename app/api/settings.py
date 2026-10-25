"""全局系统设置 REST API。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.config.settings import Settings, load_settings, save_settings
from app.config.store import sync_store_database
from app.deps import chat_manager, config_store
from app.services import pg_databases

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
    old_store_db = old.effective_store_database()
    try:
        new_store_db = pg_databases.validate_store_database_name(body.global_store_database)
    except pg_databases.DatabaseError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    body.global_store_database = new_store_db

    # BID084：整合分隔符只接受单行文本，空串回退到默认英文冒号。
    sep = (body.browser_interactive_merge_separator or ":").strip()  # BID084
    if not sep or "\n" in sep or "\r" in sep:  # BID084
        sep = ":"  # BID084
    body.browser_interactive_merge_separator = sep  # BID084

    store_db_changed = old_store_db != new_store_db
    if store_db_changed:
        # 先关闭所有运行时，释放对旧 store 库的连接，才能 ALTER DATABASE ... RENAME
        await chat_manager.invalidate_all()

        # 同一套全局 store 数据库名可能出现在多台服务器上（每个 agent 可有自己的连接前缀）
        targets: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for cfg in config_store.list():
            pair = (cfg.postgres.prefix, cfg.postgres.suffix)
            if cfg.postgres.prefix and pair not in seen:
                seen.add(pair)
                targets.append(pair)
        if body.global_postgres_prefix:
            pair = (body.global_postgres_prefix, body.global_postgres_suffix)
            if pair not in seen:
                seen.add(pair)
                targets.append(pair)

        try:
            for prefix, suffix in targets:
                await pg_databases.rename_database(
                    prefix, suffix, old_store_db, new_store_db
                )
        except pg_databases.DatabaseError as exc:
            raise HTTPException(status_code=400, detail=f"重命名 store 数据库失败：{exc}")

        # 改名成功后，把所有 agent 配置里的 store 库名同步成新名字
        sync_store_database(config_store, new_store_db)

    save_settings(config_store._dir, body)
    # 记忆吸附、默认总结百分比、记忆相似度门槛、PDF 表格提取在构建图时被闭包捕获，变更后需全量失效重建。
    # store 数据库改名也需要重建（已在上面的改名分支里先失效过一次）。
    # BID084：浏览器可交互元素的整合/英文渲染会改变 bind_tools 时注入的工具描述，同样需要重建图。
    if (
        store_db_changed
        or old.memory_attach != body.memory_attach
        or old.num_memories_attached != body.num_memories_attached
        or old.summary_token_percent != body.summary_token_percent
        or old.search_memory_threshold != body.search_memory_threshold
        or old.attach_memory_threshold != body.attach_memory_threshold
        or old.pdf_table_extraction != body.pdf_table_extraction
        or old.browser_interactive_merge != body.browser_interactive_merge  # BID084
        or old.browser_interactive_merge_separator != body.browser_interactive_merge_separator  # BID084
        or old.browser_interactive_english != body.browser_interactive_english  # BID084
    ):
        await chat_manager.invalidate_all()
    return body.model_dump()
