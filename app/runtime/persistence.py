"""从配置构建 embedding / store / checkpointer（Postgres 持久化）。

内存约定（2026-11-24 修复「多开几个 agent 就涨 2GB+」）：
- embedding 模型（BGE-M3，fp32 约 2.3~2.5GB）在**进程内只有一份**（模块级单例，
  按模型/device/缓存目录等配置做 key；配置变了才重载）。
- 运行时的 store 只在「第一次真正算向量」时才加载模型（``LazyEmbeddings``）。
  仅仅是浏览会话列表 / 快照 / 切换 multi-agent 时不会加载它。
- 之前的行为是每个 agent 的 runtime 各 ``build_embeddings`` 一次，且 ``_runtimes``
  永不释放，于是每看一个新 agent 就多 2.4GB（实测 warmup 一次 +2.4GB 私有内存）。
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import os
import threading

from langchain_core.embeddings import Embeddings
from huggingface_hub import HfApi, snapshot_download
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.base import IndexConfig
from langgraph.store.postgres import AsyncPostgresStore

from app.config.models import EmbeddingConfig, MultiAgentConfig
from app.services.downloads import download_manager, make_progress_tqdm

logger = logging.getLogger(__name__)


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


def _disable_xet() -> None:
    """禁用 huggingface_hub 的 Xet 分块下载（Xet CAS 国内被墙/401），改走常规 HTTP。"""
    import huggingface_hub.constants as _c

    _c.HF_HUB_DISABLE_XET = True


def _load_embeddings(cfg: EmbeddingConfig):
    """真正把模型权重读进内存（阻塞；调用方必须在工作线程里跑）。

    ``langchain_huggingface`` / torch 的导入也放在这里：应用启动时不再为了建图
    提前把 torch 拉起来，等真的要用向量时再加载。
    """
    from langchain_huggingface import HuggingFaceEmbeddings

    return HuggingFaceEmbeddings(
        model_name=cfg.model_name,
        model_kwargs={
            "device": cfg.device,
            "local_files_only": True,
        },
        encode_kwargs={"normalize_embeddings": cfg.encode_normalize},
        cache_folder=cfg.cache_folder or None,
    )


# ── 进程级 embedding 单例 ────────────────────────────────────────────────
_EMBEDDINGS_LOCK = threading.RLock()
_EMBEDDINGS_INSTANCE: Embeddings | None = None
_EMBEDDINGS_KEY: tuple | None = None


def _embedding_cache_key(cfg: EmbeddingConfig) -> tuple:
    """同一份模型配置 → 同一个 key（不同 key 才需要重新加载）。"""
    return (
        str(cfg.model_name or "").strip(),
        str(cfg.device or "").strip(),
        bool(cfg.encode_normalize),
        str(cfg.cache_folder or "").strip(),
        bool(cfg.local_files_only),
        str(cfg.hf_endpoint or "").strip(),
    )


def _load_shared_embeddings(cfg: EmbeddingConfig):
    """返回进程级共享的 embedding 实例（必要时先下载模型），阻塞。

    这是**唯一**会把 BGE-M3 读进内存的入口。加锁做 single-flight：多个 agent
    同时首次使用时只加载一份，其余线程等待。
    """
    global _EMBEDDINGS_INSTANCE, _EMBEDDINGS_KEY
    key = _embedding_cache_key(cfg)
    with _EMBEDDINGS_LOCK:
        if _EMBEDDINGS_INSTANCE is not None and _EMBEDDINGS_KEY == key:
            return _EMBEDDINGS_INSTANCE
        # 配置变了（换模型 / 换 device / 换缓存目录）：先放掉旧单例。
        # 旧 runtime 可能还引用着它，等它们被 invalidate 关闭后自然回收。
        _EMBEDDINGS_INSTANCE = None
        _EMBEDDINGS_KEY = None
        if cfg.local_files_only:
            _set_offline(True)
        else:
            _set_offline(False)
            _disable_xet()
            _apply_hf_endpoint(cfg.hf_endpoint)
            _ensure_model_downloaded_sync(cfg)
        instance = _load_embeddings(cfg)
        _EMBEDDINGS_INSTANCE = instance
        _EMBEDDINGS_KEY = key
        return instance


def reset_embeddings_cache() -> None:
    """释放进程级 embedding 单例（下次真正需要时重新加载）。"""
    global _EMBEDDINGS_INSTANCE, _EMBEDDINGS_KEY
    with _EMBEDDINGS_LOCK:
        _EMBEDDINGS_INSTANCE = None
        _EMBEDDINGS_KEY = None


class LazyEmbeddings(Embeddings):
    """延迟加载的 embedding 句柄：第一次 embed 时才加载（共享的）模型。

    运行时的 store 在「建图」阶段只需要一个符合 ``Embeddings`` 接口的对象；
    真正算向量要等用户发消息（记忆吸附 / 记忆工具）或编辑记忆库。用这个轻量代理
    顶上，浏览类操作就不会为每个 agent 各加载一份 BGE-M3。

    模型本身通过 ``_load_shared_embeddings`` 在进程内共享，因此多个 agent、
    多个 runtime 加起来也只有一份权重。
    """

    def __init__(self, cfg: EmbeddingConfig) -> None:
        self._cfg = cfg.model_copy(deep=True)
        self._loaded: Embeddings | None = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._loaded is not None

    def _ensure(self) -> Embeddings:
        instance = self._loaded
        if instance is not None:
            return instance
        with self._lock:
            if self._loaded is None:
                self._loaded = _load_shared_embeddings(self._cfg)
            return self._loaded

    def embed_documents(self, texts):
        return self._ensure().embed_documents(texts)

    def embed_query(self, text):
        return self._ensure().embed_query(text)

    def __repr__(self) -> str:  # 便于日志/调试
        return (
            f"LazyEmbeddings(model_name={self._cfg.model_name!r}, "
            f"loaded={self.loaded})"
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


def _get_total_size(model: str, endpoint: str | None) -> int:
    """获取模型需要下载的文件总大小（用于进度条预显示分母）。"""
    try:
        api = HfApi(endpoint=endpoint)
        info = api.model_info(model)
        total = 0
        for s in info.siblings or []:
            name = s.rfilename or ""
            if not name:
                continue
            if any(fnmatch.fnmatch(name, p) for p in IGNORE_PATTERNS):
                continue
            total += s.size or 0
        return total
    except Exception:
        return 0


def _ensure_model_downloaded_sync(cfg: EmbeddingConfig) -> None:
    """确保 embedding 模型已缓存；无缓存则下载并上报进度（阻塞，工作线程调用）。"""
    model = cfg.model_name
    cache = cfg.cache_folder or None
    endpoint = cfg.hf_endpoint or None

    # 先离线快查缓存
    try:
        snapshot_download(repo_id=model, cache_dir=cache, local_files_only=True)
        return
    except Exception:
        pass

    # 预取总大小，进入"准备中"状态
    total = _get_total_size(model, endpoint)
    download_manager.start(model, total)
    try:
        snapshot_download(
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


async def build_embeddings(cfg: EmbeddingConfig) -> Embeddings:
    """立即加载（或复用）共享 embedding 模型。

    只用于「确实马上要算向量」的场景（记忆库编辑、旧库迁移）。运行时的 store
    请用 ``LazyEmbeddings``，避免浏览界面就把 2.4GB 拉进内存。
    """
    return await asyncio.to_thread(_load_shared_embeddings, cfg)


# store 的向量索引字段：必须与「运行时写入记忆」完全一致，否则检索/编辑会算错向量。
# `$` 表示整个 value 的 JSON（不含 store 的 key）；langgraph 的默认值也是 `$`。
STORE_INDEX_FIELDS: list[str] = ["$"]


def make_store_index_config(cfg: EmbeddingConfig, embeddings) -> IndexConfig:
    """构造 store 的 IndexConfig（运行时与记忆库编辑共用，保证字段/dims 不会分叉）。"""
    return IndexConfig(
        embed=embeddings,
        dims=cfg.dims,
        fields=list(STORE_INDEX_FIELDS),
    )


async def build_persistence(config: MultiAgentConfig) -> tuple[AsyncPostgresSaver, AsyncPostgresStore]:
    """返回 (checkpointer, store)，需用 `async with` 包裹后调用 setup()。

    store 的 embedding 用 ``LazyEmbeddings``：这里**不加载** BGE-M3，第一次真正
    写/查记忆时才加载（进程内共享一份）。
    """
    # 旧配置 / 全局 store 库改名后，目标库可能还不存在：运行时兜底自动建库。
    from app.services import pg_databases

    await pg_databases.ensure_databases(
        config.postgres.prefix,
        [config.postgres.checkpoint_database, config.postgres.store_database],
        config.postgres.suffix,
    )
    embeddings = LazyEmbeddings(config.main_agent.embedding)
    saver = AsyncPostgresSaver.from_conn_string(config.checkpoint_conn_string)
    store = AsyncPostgresStore.from_conn_string(
        config.store_conn_string,
        index=make_store_index_config(config.main_agent.embedding, embeddings),
    )
    return saver, store
