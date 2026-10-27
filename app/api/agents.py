"""multi-agent 配置 REST API。"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config.edits import EditRuleViolation, apply_edits
from app.config.models import MultiAgentConfig
from app.config.db_names import (
    DatabaseError as DbNameError,
    normalize_name,
    same_identity,
    validate_agent_name,
)
from app.config.settings import (
    load_settings,
    seed_global_embedding,
    seed_global_postgres,
    seed_global_sub_summary,
    seed_global_summary,
)
from app.deps import chat_manager, config_store
from app.runtime.graph_builder import describe_mcp_error
from app.services import pg_databases
from app.runtime.state_factory import (
    assign_extracted_summary_ai_msg_keys,
    assign_history_token_measure_keys,
    assign_state_messages_keys,
)
from app.services import snapshot as snapshot_service
from app.services import threads as threads_service

router = APIRouter(prefix="/api", tags=["agents"])

logger = logging.getLogger(__name__)


class CheckDbBody(BaseModel):
    conn_string: str


class CheckMcpBody(BaseModel):
    transport: str = "http"
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    command: str | None = None


@router.get("/agents")
def list_agents():
    return [cfg.model_dump() for cfg in config_store.list()]


@router.get("/agents/{agent_id}")
def get_agent(agent_id: str):
    try:
        return config_store.load(agent_id).model_dump()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="配置不存在")


def _current_store_database() -> str:
    """当前全局 store 数据库名（旧配置保存时会被同步成它）。"""
    settings = load_settings(config_store._dir)
    return settings.effective_store_database()


def _is_lite() -> bool:
    """Lite 版不建 store 数据库（它不使用长期记忆）。"""
    return chat_manager.__class__.__module__.startswith("lite.")


def _is_sqlite_storage() -> bool:
    """Lite 版是否使用 SQLite 存会话历史（安装时决定；普通版恒为 False）。

    必须先判断 ``_is_lite()``：``configs/lite/storage.json`` 是安装时留下的，
    用户可能先装 Lite+SQLite、之后改装普通版；普通版仍用 PostgreSQL，不能因为
    残留文件就跳过建库/连接校验。
    """
    if not _is_lite():
        return False
    try:
        from app.config import storage_backend

        return storage_backend.is_sqlite(config_store._dir)
    except Exception:  # noqa: BLE001
        return False


def _normalize_namespace(value) -> tuple[str, ...]:
    """校验并归一化记忆命名空间（逗号分隔的多层级）。

    LangGraph 的 store 用 "." 把层级拼成一个 prefix，因此单个层级里不能再出现点号。
    """
    parts: list[str] = []
    for raw in value or []:
        level = normalize_name(raw)
        if not level:
            continue
        if "." in level:
            raise HTTPException(
                status_code=400,
                detail="记忆命名空间不能用点号：点号是层级分隔符，请改用逗号分隔多个层级（例如 Li,memories）",
            )
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in level):
            raise HTTPException(status_code=400, detail="记忆命名空间不能包含控制字符")
        if len(level.encode("utf-8")) > 63:
            raise HTTPException(
                status_code=400, detail=f"记忆命名空间「{level}」过长（单个层级上限 63 字节）"
            )
        parts.append(level)
    if not parts:
        raise HTTPException(status_code=400, detail="记忆命名空间不能为空")
    return tuple(parts)


def _ensure_name_is_free(name: str) -> None:
    """名称会同时成为 agent_id 与 checkpoint 数据库名，三者都不能和现有 agent 冲突。"""
    if same_identity(name, _current_store_database()):
        raise HTTPException(
            status_code=400,
            detail=f"名称「{name}」与全局 store 数据库同名，请换一个名称",
        )
    for cfg in config_store.list():
        if same_identity(cfg.name, name):
            raise HTTPException(
                status_code=400,
                detail=f"名称「{name}」已存在：名称同时用作 agent_id 与 checkpoint 数据库名，不能重名",
            )
        if same_identity(cfg.agent_id, name):
            raise HTTPException(
                status_code=400,
                detail=f"名称「{name}」与已有 multi-agent 的 agent_id 冲突，请换一个名称",
            )
        if same_identity(cfg.postgres.checkpoint_database, name):
            raise HTTPException(
                status_code=400,
                detail=f"名称「{name}」与已有 multi-agent 的 checkpoint 数据库同名，请换一个名称",
            )


async def _ensure_agent_databases(cfg: MultiAgentConfig) -> None:
    """按用户给的连接串自动创建 checkpoint / store 数据库（不要求用户先建库）。"""
    if _is_sqlite_storage():
        # SQLite 模式不需要创建 PostgreSQL 数据库；文件在首次使用时自动建立。
        return
    databases = [cfg.postgres.checkpoint_database]
    if not _is_lite():
        databases.append(cfg.postgres.store_database)
    try:
        await pg_databases.ensure_databases(
            cfg.postgres.prefix,
            databases,
            cfg.postgres.suffix,
        )
    except pg_databases.DatabaseError as exc:
        raise HTTPException(status_code=400, detail=f"自动创建数据库失败：{exc}")


@router.post("/agents")
async def create_agent(cfg: MultiAgentConfig):
    # 只需要用户填名称；另外两个身份字段由名称直接派生（见 app/config/db_names.py）。
    try:
        name = validate_agent_name(cfg.name)
    except DbNameError as exc:
        raise HTTPException(status_code=400, detail=f"名称不可用：{exc}")
    cfg.name = name
    cfg.agent_id = name
    cfg.postgres.checkpoint_database = name

    # 子 agent 名称不能为空（空 name 会让工具名回退为函数名，导致图构建报错）
    for sub in cfg.sub_agents:
        if not sub.name or not sub.name.strip():
            raise HTTPException(status_code=400, detail="子 agent 名称不能为空")

    _ensure_name_is_free(name)
    cfg.postgres.store_namespace = _normalize_namespace(cfg.postgres.store_namespace)

    # 长期记忆统一使用全局 store 数据库；旧配置里的 store_database 不再由用户决定
    cfg.postgres.store_database = _current_store_database()

    # 不再要求用户先建库：保存时由程序按连接串自动创建 checkpoint / store 数据库
    await _ensure_agent_databases(cfg)

    # 建库后验证连接（账号密码 / SSL / 端口是否真的可用）
    if not _is_sqlite_storage():
        ok, err = await threads_service.test_connection(cfg.checkpoint_conn_string)
        if not ok:
            raise HTTPException(
                status_code=400,
                detail=f"数据库已自动创建，但仍无法连接 [{name}]。请检查连接前缀、端口、账号密码与 SSL 设置。原始错误: {err}",
            )

    # 生成并锁定子 agent 的消息通道键（落盘后永久固定，与历史绑定）
    assign_state_messages_keys(cfg)
    assign_history_token_measure_keys(cfg)
    assign_extracted_summary_ai_msg_keys(cfg)

    await chat_manager.invalidate(cfg.agent_id)
    config_store.save(cfg)
    # 全新安装时首次创建 agent：顺手把它提取为全局连接 / 全局 embedding 设置（若尚未初始化）。
    try:
        configs = config_store.list()
        seed_global_postgres(config_store._dir, configs)
        seed_global_embedding(config_store._dir, configs)
        seed_global_summary(config_store._dir, configs)
        seed_global_sub_summary(config_store._dir, configs)
    except Exception:  # noqa: BLE001
        pass
    return cfg.model_dump()


@router.put("/agents/{agent_id}")
async def update_agent(agent_id: str, cfg: MultiAgentConfig):
    try:
        existing = config_store.load(agent_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="配置不存在")

    try:
        name = validate_agent_name(cfg.name)
    except DbNameError as exc:
        raise HTTPException(status_code=400, detail=f"名称不可用：{exc}")
    if normalize_name(existing.name) != name:
        raise HTTPException(
            status_code=400, detail="名称不可修改（name 同时绑定 agent_id 与 checkpoint 数据库）"
        )

    # 身份字段以磁盘上的既有值为准：名称不可改，另外两个字段由名称派生、创建后不可改。
    cfg.name = existing.name
    cfg.agent_id = existing.agent_id
    cfg.postgres.checkpoint_database = existing.postgres.checkpoint_database
    # namespace 现在允许在配置页编辑；旧客户端没传该字段时保留原值，避免悄悄改掉记忆归属。
    if "store_namespace" in cfg.postgres.model_fields_set:
        cfg.postgres.store_namespace = _normalize_namespace(cfg.postgres.store_namespace)
    else:
        cfg.postgres.store_namespace = existing.postgres.store_namespace

    try:
        merged = apply_edits(existing, cfg)
    except EditRuleViolation as e:
        raise HTTPException(status_code=400, detail=str(e))

    # store 库固定使用全局值
    merged.postgres.store_database = _current_store_database()

    # 子 agent 名称不能为空
    for sub in merged.sub_agents:
        if not sub.name or not sub.name.strip():
            raise HTTPException(status_code=400, detail="子 agent 名称不能为空")

    # 编辑保存时同样自动补齐 checkpoint / store 数据库
    await _ensure_agent_databases(merged)

    # 对旧配置（state_messages_key 为 null）兜底生成并锁定
    assign_state_messages_keys(merged)
    assign_history_token_measure_keys(merged)
    assign_extracted_summary_ai_msg_keys(merged)

    await chat_manager.invalidate(agent_id)
    config_store.save(merged)
    return merged.model_dump()


@router.delete("/agents/{agent_id}")
async def delete_agent(agent_id: str):
    try:
        cfg = config_store.load(agent_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="配置不存在")
    await chat_manager.invalidate(agent_id)
    # 删除前把显示名记进快照目录，供「查看已删除 multi-agent 的快照」展示
    snapshot_service.save_agent_meta(agent_id, cfg.name)
    config_store.delete(agent_id)
    return {"ok": True}


async def _warm_runtime(agent_id: str) -> None:
    """后台预热运行时（加载 embedding、建图）；失败静默，不影响前端。"""
    try:
        await chat_manager.get_runtime(agent_id)
    except Exception:  # noqa: BLE001
        logger.exception("预热运行时失败 (agent_id=%s)", agent_id)


@router.post("/agents/{agent_id}/warmup")
async def warmup_agent(agent_id: str):
    """在用户进入会话时提前构建运行时，避免发送消息时才等待 embedding 加载。

    立即返回；真正的构建在后台任务里进行，与聊天 WebSocket 的首次构建共用同一把锁。
    """
    try:
        config_store.load(agent_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="配置不存在")
    if chat_manager.has_runtime(agent_id):
        return {"ok": True, "state": "ready"}
    asyncio.create_task(_warm_runtime(agent_id))
    return {"ok": True, "state": "warming"}


@router.get("/default")
def get_default():
    return config_store.load_default().model_dump()


@router.put("/default")
def set_default(cfg: MultiAgentConfig):
    config_store.save_default(cfg)
    return {"ok": True}


@router.post("/check-db")
async def check_db(body: CheckDbBody):
    if _is_sqlite_storage():
        # SQLite 模式不使用 PostgreSQL 连接，前端也不会显示该检查入口。
        return {"ok": True, "error": ""}
    ok, err = await threads_service.test_connection(body.conn_string)
    return {"ok": ok, "error": err}


@router.post("/mcp-check")
async def mcp_check(body: CheckMcpBody):
    if body.transport == "stdio":
        import shutil

        found = bool(body.command) and shutil.which(body.command) is not None
        return {"ok": found, "error": "" if found else f"未找到命令: {body.command}"}

    if not body.url:
        return {"ok": False, "error": "缺少 URL"}

    # 真正发起一次 MCP 握手（initialize + tools/list），
    # 而不是裸 HTTP GET——后者对 401/400 也会亮绿灯，无法反映鉴权/协议问题。
    from langchain_mcp_adapters.client import MultiServerMCPClient

    conn: dict[str, Any] = {"transport": "http", "url": body.url, "timeout": 10}
    if body.headers:
        conn["headers"] = body.headers
    try:
        client = MultiServerMCPClient({"mcp_check": conn})
        tools = await client.get_tools()
        return {"ok": True, "error": "", "tools": [t.name for t in tools]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": describe_mcp_error(e)}
