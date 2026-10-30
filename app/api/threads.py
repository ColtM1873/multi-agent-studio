"""thread 数据访问 REST API。"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.deps import chat_manager
from app.services.messages_json import message_to_dict

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents/{agent_id}/threads", tags=["threads"])


class CreateThreadBody(BaseModel):
    thread_id: str


def _load(agent_id: str):
    from app.deps import config_store

    try:
        return config_store.load(agent_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="配置不存在")


@router.get("")
async def list_threads(agent_id: str):
    _load(agent_id)
    return await chat_manager.list_threads(agent_id)


@router.post("")
async def create_thread(agent_id: str, body: CreateThreadBody):
    _load(agent_id)
    thread_id = body.thread_id.strip()
    if not thread_id:
        raise HTTPException(status_code=400, detail="会话名称不能为空")
    await chat_manager.register_thread(agent_id, thread_id)
    return {"ok": True, "thread_id": thread_id}


@router.delete("/{thread_id}")
async def delete_thread(agent_id: str, thread_id: str):
    _load(agent_id)
    from app.deps import session_hub

    if session_hub.is_pending(agent_id, thread_id):
        raise HTTPException(
            status_code=409,
            detail="该会话正在运行或等待验收，暂时不能删除。请等它完成并验收后再删除。",
        )
    await chat_manager.delete_thread(agent_id, thread_id)
    # 删除会话 = 把它的浏览器 profile 释放回池：
    # 不关闭浏览器、不删除 profile 目录（登录态/配色都保留），
    # 下一个需要 profile 的会话会按 FIFO 认领它。
    try:
        from app.deps import browser_session_manager

        # 释放里可能探测 CDP 端口，放线程里跑，别卡住事件循环。
        await asyncio.to_thread(
            browser_session_manager.release, agent_id, thread_id
        )
    except Exception:  # noqa: BLE001 — 释放失败不影响会话删除本身
        logger.exception(
            "释放会话浏览器 profile 失败 (agent_id=%s, thread_id=%s)", agent_id, thread_id
        )
    return {"ok": True}


@router.get("/{thread_id}/history")
async def thread_history(agent_id: str, thread_id: str):
    _load(agent_id)
    md = await chat_manager.thread_history(agent_id, thread_id)
    if md is None:
        raise HTTPException(status_code=404, detail="该线程无 checkpoint")
    return {"markdown": md}


@router.get("/{thread_id}/token-usage")
async def thread_token_usage(agent_id: str, thread_id: str, sub_agent: str | None = None):
    """当前历史消息的 token 总数（主动总结百分比设置框用）。"""
    _load(agent_id)
    total = await chat_manager.history_token_usage(agent_id, thread_id, sub_agent)
    return {"total_tokens": total}


@router.get("/{thread_id}/subgraphs")
async def list_subgraphs(agent_id: str, thread_id: str):
    _load(agent_id)
    return await chat_manager.thread_subgraphs(agent_id, thread_id)


@router.get("/{thread_id}/subgraphs/{node_name:path}/history")
async def subgraph_history(agent_id: str, thread_id: str, node_name: str):
    _load(agent_id)
    md = await chat_manager.subgraph_history(agent_id, thread_id, node_name)
    if md is None:
        raise HTTPException(status_code=404, detail="该子图无 checkpoint")
    return {"markdown": md}


@router.get("/{thread_id}/messages")
async def thread_messages(agent_id: str, thread_id: str):
    _load(agent_id)
    msgs = await chat_manager.main_thread_messages(agent_id, thread_id)
    return {
        "messages": [
            {**message_to_dict(m), "msg_indice": i} for i, m in enumerate(msgs)
        ]
    }


@router.get("/{thread_id}/subgraphs/{node_name:path}/messages")
async def subgraph_messages(agent_id: str, thread_id: str, node_name: str):
    _load(agent_id)
    msgs = await chat_manager.subgraph_messages_exact(agent_id, thread_id, node_name)
    return {
        "messages": [
            {**message_to_dict(m), "msg_indice": i} for i, m in enumerate(msgs)
        ]
    }
