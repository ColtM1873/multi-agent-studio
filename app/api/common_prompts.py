"""会话常用 prompt REST API（每个 thread 一条）。

- GET /api/agents/{agent_id}/threads/{thread_id}/common-prompt   读取本会话的常用 prompt
- PUT /api/agents/{agent_id}/threads/{thread_id}/common-prompt   写入（空字符串即「清空（不设置）」）

全局常用 prompt 库不在这里：它是全局设置，走既有的 GET/PUT /api/settings
（`Settings.common_prompts`）。
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.deps import common_prompt_store

router = APIRouter(
    prefix="/api/agents/{agent_id}/threads/{thread_id}/common-prompt",
    tags=["common-prompts"],
)


class CommonPromptBody(BaseModel):
    text: str = ""


@router.get("")
def get_common_prompt(agent_id: str, thread_id: str):
    return {"agent_id": agent_id, "thread_id": thread_id, "text": common_prompt_store.get(agent_id, thread_id)}


@router.put("")
def put_common_prompt(agent_id: str, thread_id: str, body: CommonPromptBody):
    common_prompt_store.set(agent_id, thread_id, body.text)
    return {"agent_id": agent_id, "thread_id": thread_id, "text": body.text}
