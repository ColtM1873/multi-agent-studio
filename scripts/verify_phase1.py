"""Phase 1 验证：加载 JSON 配置 → 动态生成 state → 编译主图/子图（MemorySaver 干跑，不调 LLM）。

运行: python scripts/verify_phase1.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore

from app.config.store import ConfigStore
from app.runtime.graph_builder import build_world
from app.runtime.state_factory import (
    assign_extracted_summery_ai_msg_keys,
    assign_history_token_measure_keys,
    assign_state_messages_keys,
    make_main_state,
    make_sub_agent_state,
)


class _FakeMCPClient:
    """离线验证用：不连接真实 MCP 服务器。"""

    async def get_tools(self):
        return []


def _fake_factory(_servers):
    return _FakeMCPClient()


async def main() -> None:
    store = ConfigStore(ROOT)
    configs = store.list()
    if not configs:
        print("⚠️  configs/ 下没有任何配置，请先运行 python scripts/seed_configs.py")
        return

    for cfg in configs:
        assign_state_messages_keys(cfg)
        assign_history_token_measure_keys(cfg)
        assign_extracted_summery_ai_msg_keys(cfg)
        make_main_state()
        keys = []
        token_keys = []
        summery_keys = []
        for s in cfg.sub_agents:
            make_sub_agent_state(s.state_messages_key, s.history_token_measure_key, s.extracted_summery_ai_msg_key)
            keys.append(s.state_messages_key)
            token_keys.append(s.history_token_measure_key)
            summery_keys.append(s.extracted_summery_ai_msg_key)

        assert "messages" not in keys, "子图消息键不能与主图 `messages` 冲突"
        assert len(keys) == len(set(keys)), "子图消息键彼此冲突"
        assert "current_history_token_volume" not in token_keys, "子图 token 计数键不能与主图 `current_history_token_volume` 冲突"
        assert len(token_keys) == len(set(token_keys)), "子图 token 计数键彼此冲突"
        assert "extracted_summery_ai_msg_as_str" not in summery_keys, "子图总结 AI 消息键不能与主图 `extracted_summery_ai_msg_as_str` 冲突"
        assert len(summery_keys) == len(set(summery_keys)), "子图总结 AI 消息键彼此冲突"

        graph = await build_world(
            cfg,
            store=InMemoryStore(),
            checkpointer=MemorySaver(),
            mcp_client_factory=_fake_factory,
        )
        node_count = len(graph.get_graph().nodes)
        print(
            f"[OK] {cfg.agent_id:<12} checkpoint_db={cfg.postgres.checkpoint_database:<24} "
            f"sub_agents={[s.name for s in cfg.sub_agents]} msg_keys={keys} nodes={node_count}"
        )


if __name__ == "__main__":
    asyncio.run(main())
