"""multi-agent 配置 REST API。"""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config.edits import EditRuleViolation, apply_edits
from app.config.models import MultiAgentConfig
from app.config.store import slugify
from app.deps import chat_manager, config_store
from app.runtime.state_factory import assign_state_messages_keys
from app.services import threads as threads_service

router = APIRouter(prefix="/api", tags=["agents"])


class CheckDbBody(BaseModel):
    conn_string: str


class CheckMcpBody(BaseModel):
    transport: str = "http"
    url: str | None = None
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

    await chat_manager.invalidate(cfg.agent_id)
    config_store.save(cfg)
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

    # 对旧配置（state_messages_key 为 null）兜底生成并锁定
    assign_state_messages_keys(merged)

    await chat_manager.invalidate(agent_id)
    config_store.save(merged)
    return merged.model_dump()


@router.delete("/agents/{agent_id}")
async def delete_agent(agent_id: str):
    try:
        config_store.load(agent_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="配置不存在")
    await chat_manager.invalidate(agent_id)
    config_store.delete(agent_id)
    return {"ok": True}


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

    import httpx

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.get(body.url or "", headers={"Accept": "application/json, text/event-stream"})
        return {"ok": True, "error": ""}
    except Exception as e:
        return {"ok": False, "error": str(e)}
