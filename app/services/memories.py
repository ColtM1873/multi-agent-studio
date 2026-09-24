"""长期记忆（AsyncPostgresStore）数据访问层：列出 / 删除 / 编辑。

数据库结构（langgraph.store.postgres）：
- `store(prefix, key, value jsonb, ...)`：prefix 即命名空间 `".".join(ns)`，key 为 uuid，value 为 `{subject: content}`。
- `store_vectors(prefix, key, field_name, embedding, ...)`：外键 `ON DELETE CASCADE` 指向 store；
  编辑时 `aput` 会按 IndexConfig 重新调用一次 embedding 模型并 upsert 向量行。
"""

from __future__ import annotations

from langgraph.store.postgres import AsyncPostgresStore

from app.config.models import MultiAgentConfig
from app.runtime.persistence import build_embeddings, make_store_index_config


def _namespace(config: MultiAgentConfig) -> tuple[str, ...]:
    return tuple(config.postgres.store_namespace)


async def list_memories(
    config: MultiAgentConfig, *, limit: int = 1000, offset: int = 0
) -> list[dict]:
    """列出该 multi-agent 命名空间下的全部记忆条目（按 updated_at 倒序）。

    `asearch` 在 query 为空时退化为「按命名空间前缀取列表」，不需要 embedding 模型。
    """
    ns = _namespace(config)
    async with AsyncPostgresStore.from_conn_string(config.store_conn_string) as store:
        await store.setup()
        items = await store.asearch(ns, query=None, limit=limit, offset=offset)
    return [
        {
            "key": it.key,
            "value": it.value or {},
            "created_at": it.created_at.isoformat() if it.created_at else None,
            "updated_at": it.updated_at.isoformat() if it.updated_at else None,
        }
        for it in items
    ]


async def delete_memory(config: MultiAgentConfig, key: str) -> None:
    """删除一条记忆。

    `store.adelete` 只删 `store` 表对应行；`store_vectors` 里的 embedding 行由
    外键 `ON DELETE CASCADE` 一并删除，因此「内容表 + 向量表」两边都会清掉。
    """
    ns = _namespace(config)
    async with AsyncPostgresStore.from_conn_string(config.store_conn_string) as store:
        await store.setup()
        await store.adelete(ns, key)


async def update_memory(config: MultiAgentConfig, key: str, value: dict[str, str]) -> None:
    """编辑一条记忆：先删两边，再重新压入两边（并触发一次 embedding 模型调用）。

    - 先 `adelete`：清掉 store 行（向量行级联删除）。
    - 再 `aput`：以同一 key 重新写入 store 行，并按 IndexConfig(fields=["$"]) 重新
      计算 embedding、upsert 进 `store_vectors`。整个流程只调用一次 embedding 模型。
    """
    ns = _namespace(config)
    embeddings = await build_embeddings(config.main_agent.embedding)
    index = make_store_index_config(config.main_agent.embedding, embeddings)
    async with AsyncPostgresStore.from_conn_string(
        config.store_conn_string, index=index
    ) as store:
        await store.setup()
        await store.adelete(ns, key)
        await store.aput(ns, key, value)
