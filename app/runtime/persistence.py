"""从配置构建 embedding / store / checkpointer（Postgres 持久化）。"""

from __future__ import annotations

import os

from langchain_huggingface import HuggingFaceEmbeddings
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.base import IndexConfig
from langgraph.store.postgres import AsyncPostgresStore

from app.config.models import EmbeddingConfig, MultiAgentConfig


def build_embeddings(cfg: EmbeddingConfig) -> HuggingFaceEmbeddings:
    # 离线与否只由 local_files_only 控制，不写死环境变量
    if cfg.local_files_only:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ.pop("HF_ENDPOINT", None)
    else:
        os.environ.pop("HF_HUB_OFFLINE", None)
        os.environ.pop("TRANSFORMERS_OFFLINE", None)
        if cfg.hf_endpoint:
            os.environ["HF_ENDPOINT"] = cfg.hf_endpoint
    return HuggingFaceEmbeddings(
        model_name=cfg.model_name,
        model_kwargs={
            "device": cfg.device,
            "local_files_only": cfg.local_files_only,
        },
        encode_kwargs={"normalize_embeddings": cfg.encode_normalize},
        cache_folder=cfg.cache_folder or None,
    )


def build_persistence(config: MultiAgentConfig) -> tuple[AsyncPostgresSaver, AsyncPostgresStore]:
    """返回 (checkpointer, store)，需用 `async with` 包裹后调用 setup()。"""
    embeddings = build_embeddings(config.main_agent.embedding)
    saver = AsyncPostgresSaver.from_conn_string(config.checkpoint_conn_string)
    store = AsyncPostgresStore.from_conn_string(
        config.store_conn_string,
        index=IndexConfig(
            embed=embeddings,
            dims=config.main_agent.embedding.dims,
            fields=["$"],
        ),
    )
    return saver, store
