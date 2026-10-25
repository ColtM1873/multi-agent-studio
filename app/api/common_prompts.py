"""会话常用 prompt REST API（每个 thread 一条，存的是**指向全局库条目的指针**）。

- GET /api/agents/{agent_id}/threads/{thread_id}/common-prompt
    返回 `{ref, text}`：ref = 指向的全局库条目 id，text = 该条目**当前**的文本
    （实时解析，所以主界面改了常用 prompt，这里拿到的一定是新版本；条目被删则 text 为空）。
- PUT /api/agents/{agent_id}/threads/{thread_id}/common-prompt
    写入 `{ref}`（空串 = 「清空（不设置）」）。

全局常用 prompt 库不在这里：它是全局设置，走既有的 GET/PUT /api/settings
（`Settings.common_prompts: list[CommonPrompt]`，每条 `{id, text}`）。
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.config.settings import load_settings
from app.deps import common_prompt_store, config_store
from app.services.common_prompts import resolve_prompt_text

router = APIRouter(
    prefix="/api/agents/{agent_id}/threads/{thread_id}/common-prompt",
    tags=["common-prompts"],
)


class CommonPromptBody(BaseModel):
    """ref = 全局常用 prompt 库里某条的 id；空串表示「清空（不设置）」。"""

    ref: str = ""


@router.get("")
def get_common_prompt(agent_id: str, thread_id: str):
    ref = common_prompt_store.get(agent_id, thread_id)
    prompts = load_settings(config_store._dir).common_prompts
    return {
        "agent_id": agent_id,
        "thread_id": thread_id,
        "ref": ref,
        "text": resolve_prompt_text(prompts, ref),
    }


@router.put("")
def put_common_prompt(agent_id: str, thread_id: str, body: CommonPromptBody):
    ref = (body.ref or "").strip()
    common_prompt_store.set(agent_id, thread_id, ref)
    prompts = load_settings(config_store._dir).common_prompts
    return {
        "agent_id": agent_id,
        "thread_id": thread_id,
        "ref": ref,
        "text": resolve_prompt_text(prompts, ref),
    }
