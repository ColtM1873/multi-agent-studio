"""DeepSeek reasoning_content 保留修复（社区 PR #40254 的运行时实现）。

背景
----
``langchain_deepseek==1.1.0`` 的 ``ChatDeepSeek._get_request_payload`` 在构造出站请求时，
**不会回填**历史 assistant 消息的 ``additional_kwargs["reasoning_content"]``。而 DeepSeek
官方文档要求「thinking 模式 + 请求带 tools」时，历史轮次的 ``reasoning_content`` 必须完整
回传，否则服务端可能返回间歇性 400。

上游 issue：langchain-ai/langchain#40219（bug，仍 open）
社区修复 PR：#40254（OPEN，未合并）

实现原则
--------
不改动 ``venv/Lib/site-packages`` 下的官方库文件；只对启用该选项的 model **实例**做
monkeypatch（opt-in、只影响该实例）。原始官方文件备份见 ``vendor_backup/langchain_deepseek/``。
"""

from __future__ import annotations

import types
from typing import Any

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage
from langchain_openai.chat_models.base import BaseChatOpenAI

from app.config.models import ModelConfig

# 实例级补丁标记，保证幂等（重复应用不会把补丁再包一层）
_PATCH_MARKER = "_deepseek_reasoning_fix_applied"


def is_deepseek_config(llm_provider_name: str, model_cfg: ModelConfig) -> bool:
    """判断该模型配置是否指向 DeepSeek。

    - 官方内置：``deepseek:<model>``
    - 第三方 OpenAI 兼容：``base_url`` 含 ``deepseek``（如 ``https://api.deepseek.com/v1``）
    """
    name = (llm_provider_name or "").strip().lower()
    if name.startswith("deepseek:"):
        return True
    if model_cfg.provider_mode == "openai_compatible":
        return "deepseek" in (model_cfg.base_url or "").lower()
    return False


def apply_deepseek_reasoning_fix(model: BaseChatOpenAI) -> BaseChatOpenAI:
    """给 model 实例挂上「回填 reasoning_content」的 ``_get_request_payload`` 补丁。

    幂等：已打过补丁的实例直接返回。非 ``BaseChatOpenAI`` 子类原样返回。
    """
    if not isinstance(model, BaseChatOpenAI):
        return model
    if getattr(model, _PATCH_MARKER, False):
        return model

    # 捕获 MRO 上最派生的官方实现（ChatDeepSeek 或 BaseChatOpenAI），避免递归。
    original = type(model)._get_request_payload

    def patched(
        self: BaseChatOpenAI,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        payload = original(self, input_, stop=stop, **kwargs)
        messages = self._convert_input(input_).to_messages()
        payload_messages = payload.get("messages") or []
        for i, message in enumerate(payload_messages):
            if message.get("role") != "assistant":
                continue
            if i >= len(messages) or not isinstance(messages[i], AIMessage):
                continue
            reasoning = messages[i].additional_kwargs.get("reasoning_content")
            if reasoning is not None:
                message["reasoning_content"] = reasoning
        return payload

    # pydantic v2 的 BaseModel.__setattr__ 会拒绝非字段属性，故用 object.__setattr__ 绕过。
    object.__setattr__(model, _PATCH_MARKER, True)
    object.__setattr__(model, "_get_request_payload", types.MethodType(patched, model))
    return model
