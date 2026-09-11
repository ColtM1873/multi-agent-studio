"""DeepSeek reasoning_content 保留修复的离线验证（不调 LLM）。

运行: python scripts/verify_deepseek_reasoning_fix.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.config.models import ModelConfig
from app.runtime.deepseek_reasoning_fix import is_deepseek_config
from app.runtime.graph_builder import _init_model


def main() -> None:
    msgs = [
        HumanMessage(content="hi"),
        AIMessage(content="hello", additional_kwargs={"reasoning_content": "think!"}),
    ]

    off = _init_model("deepseek:deepseek-v4-pro", "fake", ModelConfig())
    p_off = off._get_request_payload(msgs)
    assert "reasoning_content" not in p_off["messages"][1], p_off["messages"][1]
    print("[OK] 未开启：reasoning_content 未被回填（复现官方行为）")

    on = _init_model(
        "deepseek:deepseek-v4-pro", "fake",
        ModelConfig(preserve_reasoning_content=True),
    )
    p_on = on._get_request_payload(msgs)
    assert p_on["messages"][1].get("reasoning_content") == "think!", p_on["messages"][1]
    print("[OK] 开启：reasoning_content 被回填")

    tool_msgs = [
        HumanMessage(content="weather?"),
        AIMessage(
            content="",
            tool_calls=[{"name": "get_weather", "args": {"location": "bj"}, "id": "c1"}],
            additional_kwargs={"reasoning_content": "need tool"},
        ),
        ToolMessage(content="sunny", tool_call_id="c1"),
    ]
    bound = on.bind_tools([])
    assert getattr(bound, "bound", None) is on, "补丁应穿透 bind_tools"
    p_tool = bound.bound._get_request_payload(tool_msgs)
    assert p_tool["messages"][1].get("reasoning_content") == "need tool", p_tool["messages"][1]
    assert "reasoning_content" not in p_tool["messages"][2], p_tool["messages"][2]
    print("[OK] 开启 + bind_tools：工具轮 reasoning_content 被回填，tool 消息不受影响")

    assert is_deepseek_config("deepseek:deepseek-v4-pro", ModelConfig())
    assert is_deepseek_config(
        "deepseek-v4-pro",
        ModelConfig(provider_mode="openai_compatible", base_url="https://api.deepseek.com/v1"),
    )
    assert not is_deepseek_config("openai:gpt-5", ModelConfig())
    print("[OK] DeepSeek 配置识别（官方前缀 / 兼容 base_url）")


if __name__ == "__main__":
    main()
