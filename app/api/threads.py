"""thread 数据访问 REST API。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.deps import chat_manager

router = APIRouter(prefix="/api/agents/{agent_id}/threads", tags=["threads"])


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


@router.delete("/{thread_id}")
async def delete_thread(agent_id: str, thread_id: str):
    _load(agent_id)
    await chat_manager.delete_thread(agent_id, thread_id)
    return {"ok": True}


@router.get("/{thread_id}/history")
async def thread_history(agent_id: str, thread_id: str):
    _load(agent_id)
    md = await chat_manager.thread_history(agent_id, thread_id)
    if md is None:
        raise HTTPException(status_code=404, detail="该线程无 checkpoint")
    return {"markdown": md}


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
