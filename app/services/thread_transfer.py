"""会话 checkpoint 的**后端中立**导出 / 导入。

为什么不能直接读写数据库表：两种会话历史后端的表结构完全不同——

- 普通版 / Lite+PostgreSQL（`AsyncPostgresSaver`）：``checkpoints`` +
  ``checkpoint_blobs`` + ``checkpoint_writes``，checkpoint 是 JSONB，
  通道值（messages 等）单独存在 ``checkpoint_blobs`` 里；
- Lite+SQLite（`AsyncSqliteSaver`）：只有 ``checkpoints`` + ``writes`` 两张表，
  checkpoint / value 都是序列化后的 BLOB，没有 blobs 分表。

因此这里**只通过 checkpointer 的公开接口**（``aget_tuple`` / ``aput`` /
``aput_writes``）拿 / 放数据，并用 langgraph 自带的 `JsonPlusSerializer`
把整份 checkpoint（含 `channel_values` 里的 LangChain 消息对象）序列化成
``(type, bytes)``，再 base64 塞进 JSON。这样导出的会话可以：

- 在 Lite / 非 Lite、PostgreSQL / SQLite 之间互相导入（本轮需求「导出会话共通」）；
- 完整保留消息对象、工具调用、usage_metadata 等，而不只是渲染后的 Markdown。

约定：``payload`` 的结构是 ::

    {
      "thread_id": "...",
      "namespaces": {
        "": {"checkpoint_type": "msgpack", "checkpoint": "<b64>",
             "metadata": {...}, "pending_writes": [
                 {"task_id": "...", "channel": "...", "type": "...", "value": "<b64>"}
             ]},
        "子agent名": {...}
      }
    }

只导出每个 namespace 的**最新** checkpoint（图/前端取历史时都只读最新 checkpoint），
不再携带历史 checkpoint 链；因此导出体积与会话大小成正比，而不是与「跑过多少步」成正比。
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

logger = logging.getLogger(__name__)

# 与 checkpointer 默认 serde 同一实现：它能把 BaseMessage 等对象序列化成 msgpack。
serde = JsonPlusSerializer()


def _b64e(data: bytes) -> str:
    return base64.b64encode(data or b"").decode("ascii")


def _b64d(text: Any) -> bytes:
    return base64.b64decode(str(text or ""))


def _jsonable(value: Any) -> Any:
    """把 metadata 之类的东西收敛成纯 JSON（保留中文，非 JSON 类型转字符串）。"""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except Exception:  # noqa: BLE001 — 兜底：宁可丢元数据也不要整份导出失败
        return {}


async def read_thread(saver: Any, thread_id: str, namespaces: list[str]) -> dict:
    """读出一个会话每个 namespace 的最新 checkpoint（含 pending writes）。

    ``saver`` 是 AsyncPostgresSaver / AsyncSqliteSaver 之一；``namespaces`` 由调用方
    先用各自后端的方式列出（空字符串表示主图，子 agent 名表示子图）。
    """
    out: dict[str, Any] = {}
    for ns in namespaces:
        try:
            tup = await saver.aget_tuple(
                {"configurable": {"thread_id": thread_id, "checkpoint_ns": ns}}
            )
        except Exception:  # noqa: BLE001 — 某个子图读失败不应让整个导出失败
            logger.exception("读取会话 checkpoint 失败（thread=%s ns=%s）", thread_id, ns)
            continue
        if tup is None:
            continue
        try:
            ctype, cdata = serde.dumps_typed(tup.checkpoint)
        except Exception:  # noqa: BLE001
            logger.exception("序列化会话 checkpoint 失败（thread=%s ns=%s）", thread_id, ns)
            continue
        writes: list[dict] = []
        for item in tup.pending_writes or []:
            try:
                task_id, channel, value = item
                wtype, wdata = serde.dumps_typed(value)
            except Exception:  # noqa: BLE001
                continue
            writes.append(
                {
                    "task_id": str(task_id),
                    "channel": str(channel),
                    "type": wtype,
                    "value": _b64e(wdata),
                }
            )
        out[str(ns)] = {
            "checkpoint_id": str(tup.checkpoint.get("id") or ""),
            "checkpoint_type": ctype,
            "checkpoint": _b64e(cdata),
            "metadata": _jsonable(tup.metadata),
            "pending_writes": writes,
        }
    return {"thread_id": thread_id, "namespaces": out}


async def write_thread(saver: Any, thread_id: str, payload: dict) -> list[str]:
    """把 `read_thread` 的结果写进 ``thread_id`` 这个会话。

    返回成功写入的 namespace 列表（主图为 ``""``）。写入前不清理目标会话：
    调用方必须保证 ``thread_id`` 是**新会话名**（导入时会先算出不冲突的名字）。
    """
    written: list[str] = []
    for ns, item in (payload.get("namespaces") or {}).items():
        try:
            checkpoint = serde.loads_typed(
                (str(item.get("checkpoint_type") or ""), _b64d(item.get("checkpoint")))
            )
        except Exception:  # noqa: BLE001
            logger.exception("反序列化会话 checkpoint 失败（thread=%s ns=%s）", thread_id, ns)
            continue
        if not isinstance(checkpoint, dict):  # 兜底：坏数据直接跳过
            continue
        metadata = item.get("metadata") or {}
        # new_versions 决定哪些通道要落 blob（PG 版按它写 checkpoint_blobs）；
        # 用 checkpoint 自带的 channel_versions 即可完整还原。
        new_versions = dict(checkpoint.get("channel_versions") or {})
        config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ns}}
        try:
            await saver.aput(config, checkpoint, metadata, new_versions)
        except Exception:  # noqa: BLE001
            logger.exception("写入会话 checkpoint 失败（thread=%s ns=%s）", thread_id, ns)
            continue
        written.append(ns)

        # pending writes 属于「运行到一半的残留」，有就还原、失败不影响主体。
        by_task: dict[str, list[tuple[str, Any]]] = {}
        for w in item.get("pending_writes") or []:
            try:
                value = serde.loads_typed((str(w.get("type") or ""), _b64d(w.get("value"))))
            except Exception:  # noqa: BLE001
                continue
            by_task.setdefault(str(w.get("task_id") or ""), []).append(
                (str(w.get("channel") or ""), value)
            )
        if by_task:
            wconfig = {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": ns,
                    "checkpoint_id": str(checkpoint.get("id") or ""),
                }
            }
            for task_id, writes in by_task.items():
                try:
                    await saver.aput_writes(wconfig, writes, task_id)
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "写入会话 pending writes 失败（thread=%s ns=%s task=%s）",
                        thread_id,
                        ns,
                        task_id,
                    )
    return written


def count_messages(payload: dict) -> int:
    """主图 checkpoint 里的消息条数（用于导出清单 / 导入结果展示）。"""
    item = (payload.get("namespaces") or {}).get("")
    if not item:
        return 0
    try:
        checkpoint = serde.loads_typed(
            (str(item.get("checkpoint_type") or ""), _b64d(item.get("checkpoint")))
        )
    except Exception:  # noqa: BLE001
        return 0
    values = checkpoint.get("channel_values") or {} if isinstance(checkpoint, dict) else {}
    for key in ("messages",):
        msgs = values.get(key)
        if isinstance(msgs, list):
            return len(msgs)
    # 主图消息键固定是 messages；找不到时退回第一个 list 通道
    for value in values.values():
        if isinstance(value, list):
            return len(value)
    return 0
