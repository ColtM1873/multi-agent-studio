"""未发送消息缓存 REST API。

- GET  /api/drafts/{agent_id}          读取该 multi-agent 的未发送文本
- PUT  /api/drafts/{agent_id}          写入（空字符串即清除）；sync_all=True 时同步所有 multi-agent
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.deps import config_store, draft_store

router = APIRouter(prefix="/api", tags=["drafts"])


class DraftBody(BaseModel):
    text: str = ""
    sync_all: bool = False


@router.get("/drafts/{agent_id}")
def get_draft(agent_id: str):
    return {"agent_id": agent_id, "text": draft_store.get(agent_id)}


@router.put("/drafts/{agent_id}")
def put_draft(agent_id: str, body: DraftBody):
    if body.sync_all:
        try:
            ids = [c.agent_id for c in config_store.list()]
        except Exception:
            ids = []
        if agent_id not in ids:
            ids.append(agent_id)
        draft_store.set_many(ids, body.text)
    else:
        draft_store.set(agent_id, body.text)
    return {"agent_id": agent_id, "text": body.text}
