"""Lite 版占位模块。

普通版 ``app.services.chat`` 在导入时会导入 ``app.runtime.persistence``，
而后者会连带导入 sentence-transformers / torch。Lite 入口把本模块注册为
``app.runtime.persistence``，从而避免这些重型依赖被导入。

真正的 Lite 运行时构建在 ``lite.app.chat_lite`` 中完成。
"""

from __future__ import annotations

LITE_PERSISTENCE_STUB = True

STORE_INDEX_FIELDS: list[str] = ["$"]


async def build_persistence(config):  # noqa: ANN001
    raise RuntimeError("Lite 版不构建 embedding / store，该函数不应被调用。")


async def build_embeddings(cfg):  # noqa: ANN001
    raise RuntimeError("Lite 版不构建 embedding，该函数不应被调用。")


def make_store_index_config(cfg, embeddings):  # noqa: ANN001
    raise RuntimeError("Lite 版不构建 store，该函数不应被调用。")
