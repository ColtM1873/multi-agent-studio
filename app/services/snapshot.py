"""快照服务：在全量总结 / 手动归档前，把当前线程的完整历史（主 agent + 所有子 agent）
以**原始消息**的形式落盘到 <项目根>/snapshots/<agent_id>/ 下的 JSON 文件。

原则：渲染历史会话时用什么数据，快照就存什么数据。快照只保存与「浏览历史」完全同源的
消息列表与渲染头信息；**浏览时再用当前设置实时渲染**——这样聊天图标/称谓、思考/工具
展开等外观始终跟随当前设置，也省掉了在快照里缓存/查找替换 Markdown 的麻烦。
（旧版快照仍保留当时的 Markdown，读取时原样返回。）
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path

from langgraph.checkpoint.base import CheckpointTuple
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.config.settings import load_settings
from app.services.history_render import (
    _find_messages_channel,
    render_checkpoint_to_markdown_string,
    render_messages_to_markdown_string,
)
from app.services.messages_json import dict_to_message, message_to_dict
from app.services import threads as threads_service

logger = logging.getLogger(__name__)

# app/services/snapshot.py → 上溯到项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent.parent
SNAPSHOTS_DIR = BASE_DIR / "snapshots"
CONFIG_DIR = BASE_DIR / "configs"

# agent 元数据文件名（存在快照目录里，记录 agent 显示名）。
# 以 "_" 开头，与真正的快照文件（<thread>_<stamp>_<count>.json）区分开。
AGENT_META_FILENAME = "_meta.json"

# 快照文件格式版本：
#   1（历史遗留）= 只存渲染后的 Markdown，不可编辑；
#   2 = Markdown + 原始消息 + 固化渲染设置（历史版本；读取时同样按消息实时渲染）；
#   3 = **只存原始消息**（+ 主图渲染头信息），不再存渲染后的 Markdown 与
#       render_settings。浏览快照时用当前设置按「普通历史消息」的方式实时渲染，
#       编辑时只替换消息 JSON，无需重渲染或写回 Markdown。
SNAPSHOT_FORMAT_VERSION = 3



class SnapshotNotEditable(Exception):
    """快照生成于旧版本（只存渲染后的 Markdown），没有可编辑的原始消息。"""



def _agent_snapshots_dir(agent_id: str) -> Path:
    """agent_id → 快照目录名。

    新的 agent_id 就是名称，可能带空格（如「SI Agent」）。这里除了把非法字符换成
    下划线，还在「清洗后与原值不同」时追加 8 位哈希，保证两个不同名称不会映射到
    同一个目录（例如「A B」与「A_B」）。中文属于 \\w，保持原样可读。
    """
    raw = str(agent_id)
    safe = re.sub(r"[^\w\-]+", "_", raw).strip("_") or "agent"
    if safe != raw:
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
        safe = f"{safe}_{digest}"
    return SNAPSHOTS_DIR / safe


def _safe_filename(name: str) -> str:
    return re.sub(r"[^\w\-]+", "_", str(name)).strip("_") or "thread"


# 快照文件名：<会话名>_<YYYYmmdd>_<HHMMSS>_<微秒>_<消息条数>.json
# 例如 GO_20261031_112957_390810_36.json。
# 重命名只改最前面的「会话名」，日期 / 时间 / 消息条数这一段序列号必须原样保留。
SNAPSHOT_SUFFIX_RE = re.compile(r"^(?P<name>.*)_(?P<suffix>\d{8}_\d{6}_\d{6}_\d+)\.json$")


def split_snapshot_id(snapshot_id: str) -> tuple[str, str] | None:
    """把快照文件名拆成 (会话名, 序列号)。识别不了返回 None。"""
    m = SNAPSHOT_SUFFIX_RE.match(str(snapshot_id))
    if not m:
        return None
    return m.group("name"), m.group("suffix")


def snapshot_display_name(snapshot_id: str, record: dict | None = None) -> str:
    """快照的「会话名」显示值。

    优先用重命名时写入的 ``snapshot_name``（保留用户原样输入，含空格等字符）；
    其次用记录里的 ``thread_id``（老快照没写过 snapshot_name）；
    最后退回文件名里序列号之前的部分。
    """
    rec = record or {}
    saved = str(rec.get("snapshot_name") or "").strip()
    if saved:
        return saved
    tid = str(rec.get("thread_id") or "").strip()
    if tid:
        return tid
    parts = split_snapshot_id(snapshot_id)
    return parts[0] if parts else str(snapshot_id)


def _safe_snapshot_name(name: str) -> str:
    """把用户输入的新名字清洗成文件名安全的形式（保留中文）。

    与 `_safe_filename` 的区别：清洗后为空时抛错，而不是回退成 "thread"。
    """
    safe = re.sub(r"[^\w\-]+", "_", str(name)).strip("_")
    if not safe:
        raise ValueError("快照名称必须包含文字或数字")
    return safe


def _sanitize_id(snapshot_id: str) -> str:
    sid = Path(str(snapshot_id)).name
    if sid in ("", ".", ".."):
        raise ValueError("invalid snapshot id")
    return sid


def _history_settings() -> dict:
    """与历史浏览保持一致的渲染开关（思考/工具调用/工具结果是否默认展开、HTML 导出）。"""
    settings = load_settings(CONFIG_DIR)
    return {
        "reasoning_expanded": settings.reasoning_expanded,
        "tool_call_expanded": settings.tool_call_expanded,
        "tool_result_expanded": settings.tool_result_expanded,
        "tool_result_full": settings.tool_result_history_full,
        "tool_result_max_lines": settings.tool_result_history_max_lines,
        "tool_result_max_chars": settings.tool_result_history_max_chars,
        # export_html 参数控制「是否在 .ai-msg-block 埋 data-md-b64 原文」，
        # 供前端「转换 HTML / 导出 md 文件」两个按钮共用，任一开启即埋。
        "export_html": settings.export_html or settings.export_md,
        # 聊天界面「图标及称谓自定义」（快照与历史浏览严格同源）
        "labels": settings.chat_identity(),
    }


def _count_messages(channel_values: dict) -> int:
    key, msgs = _find_messages_channel(channel_values)
    if not key:
        msgs = channel_values.get("messages", [])
    return len(msgs) if isinstance(msgs, list) else 0


def _main_messages_of(checkpoint: dict) -> list:
    """取出「历史渲染实际会渲染」的那一份主图消息列表（与 history_render 同源）。"""
    channel_values = (checkpoint or {}).get("channel_values", {}) or {}
    messages = channel_values.get("messages", [])
    if not messages:
        _, messages = _find_messages_channel(channel_values)
    return messages if isinstance(messages, list) else []


def _message_dicts(messages) -> list[dict]:
    """消息对象 → 可落盘的 JSON dict（只保留渲染与编辑替换需要的字段）。

    刻意丢掉 response_metadata / additional_kwargs（可能含整段 reasoning 原文），
    避免快照文件无谓膨胀；渲染与编辑替换只依赖 content / tool_calls /
    usage_metadata / id / name / tool_call_id。
    """
    out: list[dict] = []
    for m in messages or []:
        try:
            d = message_to_dict(m)
        except Exception:  # noqa: BLE001 — 自定义消息类型也要能落盘
            d = {"type": type(m).__name__, "content": getattr(m, "content", "")}
        d.pop("response_metadata", None)
        d.pop("additional_kwargs", None)
        out.append(d)
    return out


def _main_checkpoint_meta(cp_tuple: CheckpointTuple) -> dict:
    """主图 checkpoint 的「渲染标题信息」，编辑后重渲染时原样复用（快照不因编辑而改头）。"""
    cp = cp_tuple.checkpoint or {}
    cfg = (cp_tuple.config or {}).get("configurable", {}) or {}
    meta = cp_tuple.metadata or {}
    return {
        "ts": cp.get("ts", ""),
        "source": meta.get("source", "?"),
        "step": meta.get("step", "?"),
        "thread_id": cfg.get("thread_id", ""),
        "checkpoint_id": cfg.get("checkpoint_id", ""),
    }


async def _collect_snapshot_data(conn_string: str, thread_id: str) -> dict | None:
    """采集快照数据：只取消息与渲染头信息，不在这里渲染 Markdown。

    渲染推迟到「浏览快照」时进行（用当时的设置），保证快照外观与当前设置一致，
    也避免快照文件里重复存一份可能很大的 Markdown。
    """
    main_messages: list = []
    main_meta: dict = {}
    async with AsyncPostgresSaver.from_conn_string(conn_string) as cp:
        await cp.setup()
        main_tuple = await cp.aget_tuple({"configurable": {"thread_id": thread_id}})
    if main_tuple:
        main_messages = _main_messages_of(main_tuple.checkpoint)
        main_meta = _main_checkpoint_meta(main_tuple)

    subgraphs = []
    total = len(main_messages)
    nodes = await threads_service.list_subgraph_nodes(conn_string, thread_id)
    for node in nodes:
        name = node["node_name"]
        merged = await threads_service.get_subgraph_messages(conn_string, thread_id, name)
        if not merged:
            continue
        subgraphs.append({"name": name, "messages": _message_dicts(merged)})
        total += len(merged)

    if not main_messages and not subgraphs:
        return None

    return {
        "subgraphs": subgraphs,
        "total_messages": total,
        # ── 以下是「浏览 / 快照内编辑」所需的原始数据 ──
        # 原始消息数组（与 /messages 同构）：浏览时用它按当前设置实时渲染；
        # 编辑时按 msg_indice 替换某一条后写回 JSON，不重渲染、不跑图。
        "main_messages": _message_dicts(main_messages),
        "main_meta": main_meta,
    }



async def capture_snapshot(
    conn_string: str, thread_id: str, agent_id: str, raise_on_error: bool = False
) -> dict | None:
    """采集并落盘一次快照。返回元数据（id/thread_id/created_at/total_messages），失败返回 None。

    Args:
        raise_on_error: 默认 False —— 快照失败绝不影响总结主流程（「锦上添花」）。
            手动「归档为快照后删除」场景传 True：失败时向上抛异常，让调用方中止删除，
            避免「以为归档了其实没归档」的静默数据丢失。此时返回 None 只表示
            「该会话没有可归档的历史内容」，不再是失败。
    """
    try:
        data = await _collect_snapshot_data(conn_string, thread_id)
    except Exception:  # noqa: BLE001 — 快照失败绝不影响总结主流程
        logger.exception("采集快照数据失败 (thread_id=%s)", thread_id)
        if raise_on_error:
            raise
        return None
    if data is None:
        return None

    now = datetime.now()
    created_at = now.strftime("%Y-%m-%d %H:%M:%S")
    stamp = now.strftime("%Y%m%d_%H%M%S_%f")
    total = data["total_messages"]
    filename = f"{_safe_filename(thread_id)}_{stamp}_{total}.json"

    record = {
        "agent_id": agent_id,
        "thread_id": thread_id,
        "created_at": created_at,
        "total_messages": total,
        # 快照只存原始消息 + 主图渲染头信息（v3）。
        # 浏览时按当前设置实时渲染 Markdown，因此这里不再缓存 main / subgraphs[].markdown，
        # 也不固化 render_settings —— 外观始终跟随当前设置。
        "format_version": SNAPSHOT_FORMAT_VERSION,
        "main_messages": data["main_messages"],
        "main_meta": data["main_meta"],
        "subgraphs": data["subgraphs"],
    }

    try:
        out_dir = _agent_snapshots_dir(agent_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / filename
        out_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001
        logger.exception("写快照文件失败 (agent_id=%s)", agent_id)
        if raise_on_error:
            raise
        return None

    return {
        "id": filename,
        "thread_id": thread_id,
        "created_at": created_at,
        "total_messages": total,
    }


def list_snapshots(agent_id: str) -> list[dict]:
    d = _agent_snapshots_dir(agent_id)
    if not d.exists():
        return []
    result = []
    for p in d.glob("*.json"):
        if p.name.startswith("_"):
            # 跳过 agent 元数据文件（_meta.json），它不是快照
            continue
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        result.append(
            {
                "id": p.name,
                # 「会话名」= 可重命名的那一段（序列号之前的名字）
                "name": snapshot_display_name(p.name, rec),
                "thread_id": rec.get("thread_id", ""),
                "created_at": rec.get("created_at", ""),
                "total_messages": rec.get("total_messages", 0),
            }
        )
    result.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return result


def snapshot_is_editable(record: dict | None) -> bool:
    """该快照是否支持「快照内编辑模式」。

    判据是「有没有保存原始消息数组」——升级前的旧快照只存了渲染后的 Markdown，
    无法安全地反解回消息，因此不支持编辑（前端据此隐藏编辑按钮）。
    主图没有消息、但子 agent 有消息的快照同样算可编辑（切换到子 agent 后可编辑）。
    """
    rec = record or {}
    main_msgs = rec.get("main_messages")
    if isinstance(main_msgs, list) and main_msgs:
        return True
    for sub in rec.get("subgraphs") or []:
        if isinstance(sub, dict):
            sub_msgs = sub.get("messages")
            if isinstance(sub_msgs, list) and sub_msgs:
                return True
    return False


def _find_subgraph(record: dict, name: str) -> dict | None:
    for sub in record.get("subgraphs") or []:
        if isinstance(sub, dict) and str(sub.get("name") or "") == name:
            return sub
    return None


def read_snapshot(agent_id: str, snapshot_id: str, include_messages: bool = False) -> dict | None:
    snapshot_id = _sanitize_id(snapshot_id)
    p = _agent_snapshots_dir(agent_id) / snapshot_id
    if not p.exists():
        return None
    rec = json.loads(p.read_text(encoding="utf-8"))
    # v2 / v3 快照里保存了原始消息 → 用**当前设置**按普通历史消息的方式实时渲染。
    # 因此聊天图标/称谓、思考/工具展开等外观永远跟随当前设置，JSON 里也无需再存
    # 一份（可能过时的）Markdown 或 render_settings。
    # v1 历史遗留快照只有渲染后的 Markdown，原样返回（不重渲染、保持当时外观）。
    if snapshot_is_editable(rec):
        _render_saved_channels(rec)
    # v2 及更早版本留下的固化渲染设置不再对外返回（新版已不再使用）。
    rec.pop("render_settings", None)
    # 附带「会话名」，供前端标题显示（与重命名后的名字保持一致）
    rec["name"] = snapshot_display_name(snapshot_id, rec)
    # 是否支持编辑（旧快照没有原始消息 → 前端隐藏「进入编辑模式」）
    rec["editable"] = snapshot_is_editable(rec)
    if not include_messages:
        # 原始消息数组可能很大：默认不随「打开快照」返回，
        # 由进入编辑模式时的 /messages 端点按需取（与普通会话的取数时机一致）。
        rec.pop("main_messages", None)
        for sub in rec.get("subgraphs") or []:
            if isinstance(sub, dict):
                sub.pop("messages", None)
    return rec


def read_snapshot_messages(
    agent_id: str | None, snapshot_id: str, subagent_name: str | None = None
) -> list[dict]:
    """读取快照里保存的原始消息（供快照内编辑模式使用）。

    - ``subagent_name`` 为空 → 主图消息；否则取对应子 agent 的消息；
    - 返回的每条消息带 ``msg_indice``，与渲染时 ``data-msg-indice`` 一一对应；
    - 旧快照（没有原始消息）抛 ``SnapshotNotEditable``。
    """
    sid = _sanitize_id(snapshot_id)
    p = _agent_snapshots_dir(agent_id) / sid
    if not p.exists():
        raise FileNotFoundError(sid)
    rec = json.loads(p.read_text(encoding="utf-8"))
    name = str(subagent_name or "").strip()
    if name:
        sub = _find_subgraph(rec, name)
        if sub is None:
            raise ValueError("该快照里没有这个子 agent")
        messages = sub.get("messages")
    else:
        messages = rec.get("main_messages")
    if not isinstance(messages, list):
        raise SnapshotNotEditable("该快照生成于旧版本，未保存可编辑的原始消息，无法编辑。")
    return [
        {**d, "msg_indice": i} for i, d in enumerate(messages) if isinstance(d, dict)
    ]


def _render_saved_channels(record: dict) -> None:
    """用当前设置把快照里的原始消息实时渲染成 Markdown（只改返回值，不落盘）。

    - 主图：用快照记录的渲染头（时间 / Source / Step / Thread / Checkpoint ID）
      伪造 `CheckpointTuple`，再走与普通历史浏览完全相同的渲染函数；
    - 子图：标题与 capture 时一致，同样复用 `render_messages_to_markdown_string`。

    渲染失败只影响对应通道（回退到旧版缓存的 Markdown；没有缓存则置空），
    绝不让整份快照打不开。
    """
    settings = _history_settings()

    old_main = record.get("main")
    main_msgs = record.get("main_messages")
    try:
        if isinstance(main_msgs, list) and main_msgs:
            objs = [dict_to_message(m) for m in main_msgs if isinstance(m, dict)]
            record["main"] = render_checkpoint_to_markdown_string(
                _make_main_checkpoint_tuple(record, objs), **settings
            )
        else:
            # 该通道没有原始消息（旧格式）：保留记录里已渲染的 Markdown
            record["main"] = old_main if isinstance(old_main, str) else ""
    except Exception:  # noqa: BLE001 — 单条坏消息不应让整份快照打不开
        logger.exception("实时渲染主图快照失败 (thread_id=%s)", record.get("thread_id"))
        record["main"] = old_main if isinstance(old_main, str) else ""

    for sub in record.get("subgraphs") or []:
        if not isinstance(sub, dict):
            continue
        name = str(sub.get("name") or "")
        msgs = sub.get("messages")
        old_md = sub.get("markdown")
        try:
            if isinstance(msgs, list) and msgs:
                objs = [dict_to_message(m) for m in msgs if isinstance(m, dict)]
                sub["markdown"] = render_messages_to_markdown_string(
                    objs, title=f"子图 [{name}] 历史", **settings
                )
            else:
                sub["markdown"] = old_md if isinstance(old_md, str) else ""
        except Exception:  # noqa: BLE001
            logger.exception(
                "实时渲染子图快照失败 (thread_id=%s, sub=%s)",
                record.get("thread_id"),
                name,
            )
            sub["markdown"] = old_md if isinstance(old_md, str) else ""


def _drop_cached_markdown(record: dict) -> None:
    """删除 v2 及更早版本缓存的渲染产物，把记录统一到 v3（只存原始消息）。"""
    record.pop("main", None)
    record.pop("render_settings", None)
    for sub in record.get("subgraphs") or []:
        if isinstance(sub, dict):
            sub.pop("markdown", None)


def _make_main_checkpoint_tuple(record: dict, messages: list) -> CheckpointTuple:
    """用快照里保存的 checkpoint 头信息 + 消息，伪造一个 tuple 供渲染函数使用。

    这样实时渲染出来的 Markdown 与快照生成时严格同源（时间 / Source / Step /
    Thread / Checkpoint ID 都不变），只是按当前设置决定展开/折叠与图标称谓。
    """
    meta = record.get("main_meta") if isinstance(record.get("main_meta"), dict) else {}
    configurable: dict = {}
    tid = meta.get("thread_id") or record.get("thread_id") or ""
    if tid:
        configurable["thread_id"] = tid
    cid = meta.get("checkpoint_id") or ""
    if cid:
        configurable["checkpoint_id"] = cid
    return CheckpointTuple(
        config={"configurable": configurable},
        checkpoint={"ts": meta.get("ts", ""), "channel_values": {"messages": list(messages)}},
        metadata={"source": meta.get("source", "?"), "step": meta.get("step", "?")},
        parent_config=None,
    )


def edit_snapshot_message(
    agent_id: str | None,
    snapshot_id: str,
    subagent_name: str | None,
    msg_indice: int,
    substitute_msg: dict,
) -> dict:
    """在快照（JSON 文件）内替换一条消息。

    与普通会话的编辑模式语义一致：前端持有完整消息、只改目标字段，其余原样回传；
    这里只做「替换那一条 + 写回 JSON」——**不重渲染 Markdown（浏览时按当前设置实时
    渲染）、不跑 LangGraph 图，也不碰会话数据库**。

    返回新的快照元数据；快照不存在抛 FileNotFoundError，旧快照抛 SnapshotNotEditable，
    其余非法输入抛 ValueError。
    """
    sid = _sanitize_id(snapshot_id)
    d = _agent_snapshots_dir(agent_id)
    p = d / sid
    if not p.exists():
        raise FileNotFoundError(sid)
    rec = json.loads(p.read_text(encoding="utf-8"))

    name = str(subagent_name or "").strip()
    sub = None
    if name:
        sub = _find_subgraph(rec, name)
        if sub is None:
            raise ValueError("该快照里没有这个子 agent")
        messages = sub.get("messages")
    else:
        messages = rec.get("main_messages")
    if not isinstance(messages, list):
        raise SnapshotNotEditable("该快照生成于旧版本，未保存可编辑的原始消息，无法编辑。")

    if not isinstance(substitute_msg, dict):
        raise ValueError("消息内容格式不正确")
    try:
        idx = int(msg_indice)
    except (TypeError, ValueError):
        raise ValueError("消息序号不正确")
    if idx < 0 or idx >= len(messages):
        raise ValueError("消息序号越界，请退出编辑模式后重新进入再试")

    original = messages[idx] if isinstance(messages[idx], dict) else {}
    if str(substitute_msg.get("type") or "") != str(original.get("type") or ""):
        raise ValueError("消息类型不匹配，无法替换该消息")

    cleaned = {k: v for k, v in substitute_msg.items() if k != "msg_indice"}
    messages[idx] = cleaned

    # 只改消息 JSON：Markdown 在浏览时用当前设置实时渲染，这里无需（也不应该）重渲染。
    # 顺手清掉旧版缓存的 Markdown / render_settings，把记录升级到 v3。
    _drop_cached_markdown(rec)
    rec["format_version"] = SNAPSHOT_FORMAT_VERSION
    p.write_text(json.dumps(rec, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    return {
        "id": sid,
        "name": snapshot_display_name(sid, rec),
        "thread_id": rec.get("thread_id", ""),
        "created_at": rec.get("created_at", ""),
        "total_messages": rec.get("total_messages", 0),
        "editable": True,
    }



def rename_snapshot(agent_id: str, snapshot_id: str, new_name: str) -> dict:
    """重命名快照的「会话名」，日期/时间/消息条数序列号原样保留。

    文件名会被重写成 ``<新名字>_<原序列号>.json``，同时把用户输入的原名（不做
    文件名清洗）写进记录的 ``snapshot_name``，因此含空格 / 符号的名称也能原样显示。

    返回新的快照元数据；快照不存在抛 FileNotFoundError，非法名字抛 ValueError，
    目标文件名已存在抛 FileExistsError。
    """
    snapshot_id = _sanitize_id(snapshot_id)
    d = _agent_snapshots_dir(agent_id)
    src = d / snapshot_id
    if not src.exists():
        raise FileNotFoundError(snapshot_id)

    parts = split_snapshot_id(snapshot_id)
    if not parts:
        raise ValueError("无法识别的快照文件名，无法重命名")

    name = str(new_name or "").strip()
    if not name:
        raise ValueError("快照名称不能为空")
    safe = _safe_snapshot_name(name)

    rec = json.loads(src.read_text(encoding="utf-8"))

    new_id = f"{safe}_{parts[1]}.json"
    dst = d / new_id
    if dst != src:
        if dst.exists():
            raise FileExistsError(new_id)
        src.rename(dst)

    rec["snapshot_name"] = name
    dst.write_text(
        json.dumps(rec, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    return {
        "id": new_id,
        "name": name,
        "thread_id": rec.get("thread_id", ""),
        "created_at": rec.get("created_at", ""),
        "total_messages": rec.get("total_messages", 0),
    }


def delete_snapshot(agent_id: str, snapshot_id: str) -> bool:
    snapshot_id = _sanitize_id(snapshot_id)
    p = _agent_snapshots_dir(agent_id) / snapshot_id
    if p.exists():
        p.unlink()
        return True
    return False


# ── 已删除 multi-agent：元数据 + 清理 ───────────────────────────────

def save_agent_meta(agent_id: str, name: str) -> None:
    """在 agent 的快照目录里记录显示名（删除 agent 前调用）。

    仅当快照目录已存在时才写，避免给「从没有过快照」的 agent 凭空造出一个目录、
    让它出现在「已删除 multi-agent」列表里。
    """
    d = _agent_snapshots_dir(agent_id)
    if not d.exists():
        return
    try:
        (d / AGENT_META_FILENAME).write_text(
            json.dumps({"agent_id": agent_id, "name": name}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001 — 元数据是锦上添花，失败不影响删除
        logger.exception("写 agent 元数据失败 (agent_id=%s)", agent_id)


def load_agent_meta(agent_id_or_dir: str) -> dict:
    """读取 agent 元数据（存在返回 dict，否则返回空 dict）。"""
    p = _agent_snapshots_dir(agent_id_or_dir) / AGENT_META_FILENAME
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def list_deleted_agents(existing_agent_ids) -> list[dict]:
    """扫描 snapshots/ 下「不属于任何现存 agent」的目录，视为已删除的 multi-agent。

    - 只返回**仍有快照**的目录；没有快照的残留目录会被顺手清理掉（不复存在）。
    - 显示名优先取删除时记录的 name，缺失则退回文件夹名（= 清洗后的 agent_id）。
    """
    if not SNAPSHOTS_DIR.exists():
        return []
    live_dirs = {_agent_snapshots_dir(a).name for a in existing_agent_ids}
    result = []
    for d in sorted(SNAPSHOTS_DIR.iterdir()):
        if not d.is_dir() or d.name in live_dirs:
            continue
        snaps = list_snapshots(d.name)
        if not snaps:
            # 快照已删完（只剩 _meta.json 或空目录）→ 整个目录清掉，agent 不复存在
            shutil.rmtree(d, ignore_errors=True)
            continue
        meta = load_agent_meta(d.name)
        result.append(
            {
                "agent_id": meta.get("agent_id") or d.name,
                "dir": d.name,
                "name": meta.get("name") or d.name,
                "count": len(snaps),
            }
        )
    result.sort(key=lambda x: x["name"])
    return result


def delete_all_snapshots(agent_id: str) -> int:
    """删除某 agent（含已删除的）名下所有快照：整个目录删掉。返回删除的快照数。"""
    d = _agent_snapshots_dir(agent_id)
    if not d.exists():
        return 0
    n = len(list_snapshots(agent_id))
    shutil.rmtree(d, ignore_errors=True)
    return n
