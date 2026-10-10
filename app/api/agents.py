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
from app.config.settings import (
    seed_global_embedding,
    seed_global_postgres,
    seed_global_sub_summary,
    seed_global_summary,
)
from app.config.store import slugify
from app.deps import chat_manager, config_store
from app.runtime.graph_builder import describe_mcp_error
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


@router.post("/agents")
async def create_agent(cfg: MultiAgentConfig):
    checkpoint_db = cfg.postgres.checkpoint_database
    if not checkpoint_db.strip():
        raise HTTPException(status_code=400, detail="必须填写 checkpoint_database（会话历史归属的数据库名）")

    if not cfg.agent_id.strip():
        raise HTTPException(status_code=400, detail="必须填写 agent_id（或名称）")

    # 子 agent 名称不能为空（空 name 会让工具名回退为函数名，导致图构建报错）
    for sub in cfg.sub_agents:
        if not sub.name or not sub.name.strip():
            raise HTTPException(status_code=400, detail="子 agent 名称不能为空")

    # 清理 agent_id 中的特殊字符，保证文件名与 URL 安全
    cfg.agent_id = slugify(cfg.agent_id)
    if not cfg.agent_id:
        raise HTTPException(status_code=400, detail="agent_id 清理后为空，请使用字母/数字/中文/下划线/连字符")

    existing_ids = {c.agent_id for c in config_store.list()}
    if cfg.agent_id in existing_ids:
        raise HTTPException(status_code=400, detail=f"agent_id 已存在: {cfg.agent_id}")

    # 库名不能与现有配置冲突（否则会话串台）
    for c in config_store.list():
        if c.postgres.checkpoint_database == checkpoint_db and c.agent_id != cfg.agent_id:
            raise HTTPException(status_code=400, detail=f"checkpoint_database [{checkpoint_db}] 已被 [{c.name}] 占用")

    # 建库提醒：连接测试
    ok, err = await threads_service.test_connection(cfg.checkpoint_conn_string)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail=f"无法连接数据库 [{checkpoint_db}]，请先在 pgAdmin 或命令行创建该数据库。原始错误: {err}",
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

    if cfg.agent_id != agent_id:
        raise HTTPException(status_code=400, detail="agent_id 不可修改")

    try:
        merged = apply_edits(existing, cfg)
    except EditRuleViolation as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 子 agent 名称不能为空
    for sub in merged.sub_agents:
        if not sub.name or not sub.name.strip():
            raise HTTPException(status_code=400, detail="子 agent 名称不能为空")

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
