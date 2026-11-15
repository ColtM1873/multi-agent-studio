"""为 openai_compatible（ChatOpenAI）补上三方端点的 reasoning_content 解析。

背景
----
``langchain_openai`` 的 ``BaseChatOpenAI`` 只面向**官方 OpenAI 规范**，模块 docstring 明确写了：
第三方端点额外返回的非标准字段（如 ``reasoning_content`` / ``reasoning_details``）
**不会被提取或保留**（本地 1.3.3 的 ``chat_models/base.py:1-12``、``:583-591``）。
对应的 ``_convert_delta_to_message_chunk``（:430）与 ``_convert_dict_to_message``（:200）
只搬 ``content`` / ``tool_calls`` 等标准字段。

本项目的 ``openai_compatible`` 模式固定用 ``model_provider="openai"`` → ``ChatOpenAI``，
于是接 DeepSeek / SiliconFlow / 智谱 / Qwen 等 OpenAI 协议的推理模型时，服务端明明在
流式里回了 ``delta.reasoning_content``，却在 LangChain 层被直接丢掉：

- ``AIMessageChunk.additional_kwargs`` 没有 ``reasoning_content``；
- v3 协议的 compat bridge（``_compat_bridge.chunks_to_events``）只按
  ``content_blocks`` 产事件 → 永远没有 ``reasoning-delta``；
- 最终 ``streaming.py`` 不 emit ``{"type": "reasoning"}``，前端「思考过程」板块为空；
- 历史渲染同样没有 reasoning block（v3 组装的消息 content 里就没有它）。

（官方内置 ``deepseek:`` 之所以正常，是因为 ``langchain_deepseek.ChatDeepSeek``
自己重写了 ``_convert_chunk_to_generation_chunk``，把 reasoning_content 塞进
``additional_kwargs``，并且它的 ``model_provider`` 是 ``deepseek``——
没有注册 translator，于是走 ``AIMessageChunk.content_blocks`` 的 best-effort
解析，``_extract_reasoning_from_additional_kwargs`` 就会补出 reasoning block。）

**只把字段塞进 additional_kwargs 还不够**：chunk 的 ``response_metadata["model_provider"]``
是 ``"openai"``，``content_blocks`` 会短路到 OpenAI translator
（``block_translators/openai.py::translate_content_chunk``），而它只看
``content`` / ``tool_call_chunks``，根本不会读 additional_kwargs。因此本补丁把打过补丁的
chunk 标成 ``model_provider="openai_compat"``，并注册一个**委托 OpenAI translator** 的
translator：先拿官方结果，再把 reasoning block 插到最前面。这样文本 / 工具调用 /
chunk_position 等行为与官方完全一致，只多了思考块。

实现原则
--------
与 ``deepseek_reasoning_fix.py`` 同一套路：**不改 site-packages**，只对单个 model
实例挂运行时补丁，其余全部交回官方实现。补丁只做两件事：

1. 从每个流式 chunk（及非流式响应）里把 reasoning 字段捞进
   ``additional_kwargs["reasoning_content"]``；
2. 仅对真的带思考内容的消息，把 provider 标记切到 ``openai_compat``，让上面的
   translator 生效（普通文本 chunk 不改标记，行为与官方完全一致）。

出站回填（DeepSeek 兼容端点「历史 reasoning_content 必须回传否则 400」）仍由
``deepseek_reasoning_fix.py`` 负责。两者互补：本补丁先让字段在消息里存在，
出站补丁才有内容可回填。

支持的响应字段
--------------
- ``reasoning_content``：DeepSeek / SiliconFlow / 智谱 等主流三方端点；
- ``reasoning``：OpenRouter 等使用的同义字符串字段。
"""

from __future__ import annotations

import types
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, content as types_content
from langchain_core.messages.block_translators import (
    get_translator,
    register_translator,
)
from langchain_openai.chat_models.base import BaseChatOpenAI

# 实例级补丁标记，保证幂等（重复应用不会把补丁再包一层）
_PATCH_MARKER = "_openai_compat_reasoning_capture_applied"

# 打过补丁的 chunk 使用的 provider 标记，用于命中本模块注册的 translator
_COMPAT_PROVIDER = "openai_compat"

# 三方端点用来承载思考内容的字段，按优先级排列（先 reasoning_content，后 OpenRouter 风格）
_REASONING_FIELDS = ("reasoning_content", "reasoning")


def _reasoning_slice(payload: Any) -> str | None:
    """从 ``choice.delta`` / ``choice.message`` 里取出本片的思考文本。

    只接受非空字符串：部分端点会把 ``reasoning_content`` 显式置 ``null``，
    ``reasoning`` 也可能是结构化对象（OpenRouter 的 reasoning_details），都不是本补丁目标。
    """
    if not isinstance(payload, dict):
        return None
    for field in _REASONING_FIELDS:
        value = payload.get(field)
        if isinstance(value, str) and value:
            return value
    return None


def _reasoning_block(message: AIMessage | AIMessageChunk) -> dict | None:
    """把消息上的 ``additional_kwargs["reasoning_content"]`` 转成 v1 reasoning block。"""
    value = message.additional_kwargs.get("reasoning_content")
    if isinstance(value, str) and value:
        return {"type": "reasoning", "reasoning": value}
    return None


def _openai_translator() -> dict[str, Any] | None:
    """取官方 OpenAI translator（langchain-core 启动时会自动注册）。"""
    return get_translator("openai")


def _translate_content(message: AIMessage) -> list[types_content.ContentBlock]:
    """委托官方 OpenAI translator，再把 reasoning block 插到最前面。"""
    translator = _openai_translator()
    blocks = translator["translate_content"](message) if translator else []
    if reasoning := _reasoning_block(message):
        return [reasoning, *blocks]  # type: ignore[list-item]
    return blocks


def _translate_content_chunk(message: AIMessageChunk) -> list[types_content.ContentBlock]:
    """委托官方 OpenAI translator，再把 reasoning block 插到最前面。"""
    translator = _openai_translator()
    blocks = translator["translate_content_chunk"](message) if translator else []
    if reasoning := _reasoning_block(message):
        return [reasoning, *blocks]  # type: ignore[list-item]
    return blocks


# 注册（覆盖同名键是幂等的）：只影响 provider 被标成 ``openai_compat`` 的消息。
register_translator(_COMPAT_PROVIDER, _translate_content, _translate_content_chunk)


def _choice_payloads(chunk: dict) -> list[Any]:
    """取出 chunk 里的 choices。

    官方 ``_convert_chunk_to_generation_chunk`` 兼容两种形状：常规 SSE
    （``chunk["choices"]``）与 ``beta.chat.completions.stream``（``chunk["chunk"]["choices"]``）。
    """
    if not isinstance(chunk, dict):
        return []
    choices = chunk.get("choices")
    if not choices:
        inner = chunk.get("chunk")
        choices = inner.get("choices") if isinstance(inner, dict) else None
    return choices or []


def _tag_compat(message: AIMessage | AIMessageChunk) -> None:
    """把 provider 标记切到 ``openai_compat``，让本模块的 translator 生效。

    只在原值为 ``openai``（流式 chunk 的官方标记）或未设置（非流式消息）时改写，
    不碰别的 provider 实现（例如官方 ChatDeepSeek 的 ``deepseek``）。
    """
    if message.response_metadata.get("model_provider") in (None, "openai"):
        message.response_metadata["model_provider"] = _COMPAT_PROVIDER


def apply_openai_compat_reasoning_capture(model: BaseChatOpenAI) -> BaseChatOpenAI:
    """给 model 实例挂上「捕获三方 reasoning 字段」的补丁。

    幂等：已打过补丁的实例直接返回。非 ``BaseChatOpenAI`` 子类原样返回。
    补丁对不返回 reasoning 字段的端点完全透明（行为与官方实现一致）。
    """
    if not isinstance(model, BaseChatOpenAI):
        return model
    if getattr(model, _PATCH_MARKER, False):
        return model

    # 捕获 MRO 上最派生的官方实现，避免递归。
    original_chunk = type(model)._convert_chunk_to_generation_chunk
    original_result = type(model)._create_chat_result

    def patched_chunk(
        self: BaseChatOpenAI,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ):
        generation_chunk = original_chunk(
            self, chunk, default_chunk_class, base_generation_info
        )
        if generation_chunk is None:
            return None
        message = generation_chunk.message
        if not isinstance(message, AIMessageChunk):
            return generation_chunk
        choices = _choice_payloads(chunk)
        if not choices:
            return generation_chunk
        delta = choices[0].get("delta") if isinstance(choices[0], dict) else None
        reasoning = _reasoning_slice(delta)
        # 官方实现（或别的补丁）已经填过就不覆盖，保持「谁解析谁负责」。
        if reasoning and not message.additional_kwargs.get("reasoning_content"):
            message.additional_kwargs["reasoning_content"] = reasoning
        # 只有真的带思考内容的 chunk 才改 provider 标记；普通文本 chunk 保持官方行为。
        if message.additional_kwargs.get("reasoning_content"):
            _tag_compat(message)
        return generation_chunk

    def patched_result(
        self: BaseChatOpenAI,
        response: dict | Any,
        generation_info: dict | None = None,
    ):
        result = original_result(self, response, generation_info)
        try:
            response_dict = (
                response
                if isinstance(response, dict)
                else response.model_dump(
                    exclude={"choices": {"__all__": {"message": {"parsed"}}}}
                )
            )
        except Exception:  # noqa: BLE001 — 拿不到原始响应就放弃补写，不影响主流程
            return result
        choices = response_dict.get("choices") or []
        for generation, choice in zip(result.generations, choices):
            message = generation.message
            if not isinstance(message, AIMessage) or not isinstance(choice, dict):
                continue
            reasoning = _reasoning_slice(choice.get("message"))
            if reasoning and not message.additional_kwargs.get("reasoning_content"):
                message.additional_kwargs["reasoning_content"] = reasoning
            if message.additional_kwargs.get("reasoning_content"):
                _tag_compat(message)
        if isinstance(result.llm_output, dict):
            if result.llm_output.get("model_provider") == "openai":
                result.llm_output["model_provider"] = _COMPAT_PROVIDER
        return result

    # pydantic v2 的 BaseModel.__setattr__ 会拒绝非字段属性，故用 object.__setattr__ 绕过。
    object.__setattr__(model, _PATCH_MARKER, True)
    object.__setattr__(
        model, "_convert_chunk_to_generation_chunk", types.MethodType(patched_chunk, model)
    )
    object.__setattr__(
        model, "_create_chat_result", types.MethodType(patched_result, model)
    )
    return model
