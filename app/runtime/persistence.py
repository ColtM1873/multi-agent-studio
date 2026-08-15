"""从配置构建 embedding / store / checkpointer（Postgres 持久化）。"""

from __future__ import annotations

import asyncio
import os

from huggingface_hub import snapshot_download
from langchain_huggingface import HuggingFaceEmbeddings
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.base import IndexConfig
from langgraph.store.postgres import AsyncPostgresStore

from app.config.models import EmbeddingConfig, MultiAgentConfig
from app.services.downloads import download_manager, make_progress_tqdm


def _set_offline(offline: bool) -> None:
    if offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ.pop("HF_ENDPOINT", None)
    else:
        os.environ.pop("HF_HUB_OFFLINE", None)
        os.environ.pop("TRANSFORMERS_OFFLINE", None)


def _apply_hf_endpoint(endpoint: str) -> None:
    """运行时修改 huggingface_hub 的 endpoint 常量。

    huggingface_hub 的 ENDPOINT 是 import 时读环境变量的模块级常量，
    运行时改 os.environ 不生效，必须直接改常量。
    """
    if not endpoint:
        return
    import huggingface_hub.constants as _c

    ep = endpoint.rstrip("/")
    _c.ENDPOINT = ep
    _c.HUGGINGFACE_CO_URL_TEMPLATE = ep + "/{repo_id}/resolve/{revision}/{filename}"


def _load_embeddings(cfg: EmbeddingConfig) -> HuggingFaceEmbeddings:
    return HuggingFaceEmbeddings(
        model_name=cfg.model_name,
        model_kwargs={
            "device": cfg.device,
            "local_files_only": True,
        },
        encode_kwargs={"normalize_embeddings": cfg.encode_normalize},
        cache_folder=cfg.cache_folder or None,
    )


IGNORE_PATTERNS = [
    "*.DS_Store",
    "imgs/*",
    "images/*",
    "*.md",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.webp",
    "*.svg",
]


async def _ensure_model_downloaded(cfg: EmbeddingConfig) -> None:
    """确保 embedding 模型已缓存；无缓存则下载并上报进度。"""
    model = cfg.model_name
    cache = cfg.cache_folder or None
    endpoint = cfg.hf_endpoint or None

    # 先离线快查缓存
    try:
        await asyncio.to_thread(
            snapshot_download, repo_id=model, cache_dir=cache, local_files_only=True
        )
        return
    except Exception:
        pass

    # 下载（走镜像，带进度，跳过垃圾文件）
    download_manager.start(model)
    try:
        await asyncio.to_thread(
            snapshot_download,
            repo_id=model,
            cache_dir=cache,
            local_files_only=False,
            endpoint=endpoint,
            ignore_patterns=IGNORE_PATTERNS,
            tqdm_class=make_progress_tqdm(model),
            max_workers=4,
        )
        download_manager.done(model)
    except Exception as e:
        download_manager.error(model, str(e))
        raise


async def build_embeddings(cfg: EmbeddingConfig) -> HuggingFaceEmbeddings:
    if cfg.local_files_only:
        _set_offline(True)
    else:
        _set_offline(False)
        _apply_hf_endpoint(cfg.hf_endpoint)
        await _ensure_model_downloaded(cfg)
    return await asyncio.to_thread(_load_embeddings, cfg)


async def build_persistence(config: MultiAgentConfig) -> tuple[AsyncPostgresSaver, AsyncPostgresStore]:
    """返回 (checkpointer, store)，需用 `async with` 包裹后调用 setup()。"""
    embeddings = await build_embeddings(config.main_agent.embedding)
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
