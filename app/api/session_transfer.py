"""会话导出 / 导入 REST API。

导出：会话选择页底部「导出会话」→ 勾选若干会话 →「确定导出」→ 后端在本机弹出
      资源管理器风格的**保存**对话框 → 打包 zip（可选 AES-256-GCM 加密）。
导入：主界面「导入会话」→ 弹出**打开**对话框 → 自动识别是否加密 → 需要密码就输入
      → 选择「加载到本机已有配置」或「连同原配置一并导入」→ 写入会话历史。

为什么拆成几个端点：

- 原生对话框是**阻塞**的（GetOpenFileNameW / GetSaveFileNameW 会自建消息循环），
  因此统一放在 `asyncio.to_thread` 里执行，同一时刻只允许一个对话框（模块级锁）；
- 其余步骤（读 checkpoint、解密、写库）都是异步 / 可并行的，走普通端点即可；
- 「选文件 → 输密码 → 选目标 → 确认」本来就需要多步交互，拆开比塞一个长事务更稳。

导入按「不可信输入」处理：只按清单里的固定相对路径从 zip 读取（**不落盘解压**）、
限制解压后总体积、加密数据以文件头为 AAD 认证（改头即解不开）。
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config.models import MultiAgentConfig
from app.config.db_names import (
    DatabaseError as DbNameError,
    same_identity,
    validate_agent_name,
)
from app.config.settings import load_settings
from app.deps import chat_manager, config_store
from app.services import session_archive, thread_transfer, win_file_dialog
from app.services.history_render import render_messages_to_markdown_string

logger = logging.getLogger(__name__)

router = APIRouter(tags=["session-transfer"])

# 同一时刻只允许一个原生对话框：用户连点 / 开多个页面时，后来的请求直接给人话提示。
_dialog_lock = threading.Lock()

_ZIP_FILTER = "会话导出文件 (*.zip;*.zip.enc;*.enc)\0*.zip;*.zip.enc;*.enc\0所有文件 (*.*)\0*.*\0\0"
_SAVE_FILTER_PLAIN = "会话导出 zip (*.zip)\0*.zip\0所有文件 (*.*)\0*.*\0\0"
_SAVE_FILTER_ENCRYPTED = "加密的会话导出 (*.zip.enc)\0*.zip.enc\0所有文件 (*.*)\0*.*\0\0"

_DEFAULT_IMPORT_TITLE = "选择要导入的会话文件"
_DEFAULT_EXPORT_TITLE = "选择会话导出位置"


# ── 通用小工具 ─────────────────────────────────────────────────────


def _is_lite() -> bool:
    return chat_manager.__class__.__module__.startswith("lite.")


def _storage_backend() -> str:
    try:
        from app.config import storage_backend

        return storage_backend.storage_backend(config_store._dir)
    except Exception:  # noqa: BLE001
        return "postgres"


async def _run_dialog(func, *args, **kwargs):
    """在后台线程里弹原生对话框（模态循环会阻塞调用线程）。"""
    if not win_file_dialog.supported():
        raise HTTPException(
            status_code=400, detail="当前系统不是 Windows，无法弹出系统资源管理器窗口"
        )
    if not _dialog_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="已经有一个文件选择窗口处于打开状态，请先完成或取消它",
        )
    try:
        return await asyncio.to_thread(func, *args, **kwargs)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"无法打开系统文件窗口：{exc}")
    finally:
        _dialog_lock.release()


def _require_feature_enabled() -> None:
    settings = load_settings(config_store._dir)
    if not settings.export_session_enabled:
        raise HTTPException(
            status_code=400,
            detail="「导出对话功能」未开启，请先在主界面系统设置里开启",
        )


def _settings():
    return load_settings(config_store._dir)


def _safe_name(text: str, fallback: str = "session") -> str:
    safe = re.sub(r'[\\/:*?"<>|]+', "_", str(text or "")).strip(" .")
    return safe or fallback


def _with_suffix(path: str, suffix: str) -> str:
    """把用户选定的路径统一成目标扩展名（不覆盖/不丢用户输入的主体部分）。"""
    lower = path.lower()
    if lower.endswith(suffix.lower()):
        return path
    for known in (".zip.enc", ".enc", ".zip"):
        if lower.endswith(known):
            return path[: -len(known)] + suffix
    return path + suffix


def _free_thread_id(base: str, taken: set[str]) -> str:
    original = str(base or "").strip() or "导入会话"
    if original.casefold() not in taken:
        return original
    candidate = f"{original} (导入)"
    index = 2
    while candidate.casefold() in taken:
        candidate = f"{original} (导入 {index})"
        index += 1
    return candidate


def _local_identity(cfg: MultiAgentConfig) -> dict:
    return session_archive.agent_identity(cfg.model_dump())


# ── 导出 ───────────────────────────────────────────────────────────


class ExportBody(BaseModel):
    agent_id: str
    thread_ids: list[str] = Field(default_factory=list)
    title: str = ""
    default_name: str = ""


def _render_settings() -> dict:
    """生成 conversation.md 用的渲染参数：**不埋 data-md-b64**（否则体积翻倍）。"""
    settings = _settings()
    return {
        "reasoning_expanded": settings.reasoning_expanded,
        "tool_call_expanded": settings.tool_call_expanded,
        "tool_result_expanded": settings.tool_result_expanded,
        "tool_result_full": settings.tool_result_history_full,
        "tool_result_max_lines": settings.tool_result_history_max_lines,
        "tool_result_max_chars": settings.tool_result_history_max_chars,
        "export_html": False,
        "labels": settings.chat_identity(),
    }


async def _conversation_markdown(agent_id: str, thread_id: str) -> str:
    """人类可读副本：主图 + 所有子图历史（与历史浏览同源渲染）。

    副本是「附加价值」，任何一步失败都不应该让整次导出失败，因此这里全部兜底。
    """
    options = _render_settings()
    parts: list[str] = []
    try:
        main_messages = await chat_manager.main_thread_messages(agent_id, thread_id)
    except Exception:  # noqa: BLE001 — 副本渲染失败不影响可导入的主体数据
        main_messages = []
    if main_messages:
        try:
            parts.append(
                render_messages_to_markdown_string(
                    main_messages, title="主 agent 历史", **options
                )
            )
        except Exception:  # noqa: BLE001
            logger.exception("渲染主 agent 历史失败 (thread=%s)", thread_id)
    try:
        nodes = await chat_manager.thread_subgraphs(agent_id, thread_id)
    except Exception:  # noqa: BLE001
        nodes = []
    for node in nodes or []:
        name = str(node.get("node_name") or "")
        if not name:
            continue
        try:
            messages = await chat_manager.subgraph_messages_exact(agent_id, thread_id, name)
        except Exception:  # noqa: BLE001
            messages = []
        if not messages:
            continue
        try:
            parts.append(
                render_messages_to_markdown_string(
                    messages, title=f"子图 [{name}] 历史", **options
                )
            )
        except Exception:  # noqa: BLE001
            logger.exception("渲染子图历史失败 (thread=%s ns=%s)", thread_id, name)
    return "\n\n---\n\n".join(parts)


@router.post("/api/session-export")
async def export_sessions(body: ExportBody):
    _require_feature_enabled()
    settings = _settings()
    thread_ids = [str(t) for t in (body.thread_ids or []) if str(t).strip()]
    if not thread_ids:
        raise HTTPException(status_code=400, detail="请先选择要导出的会话")
    try:
        cfg = config_store.load(body.agent_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="配置不存在")

    encrypt = bool(settings.export_session_encrypt)
    password = str(settings.export_session_password or "")
    if encrypt and not password.strip():
        raise HTTPException(
            status_code=400,
            detail="已开启「导出会话加密」，但还没有配置加密密码。请先在系统设置里填写密码，"
            "或关闭加密后再导出。",
        )

    suffix = ".zip.enc" if encrypt else ".zip"
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    default_name = body.default_name or f"{_safe_name(cfg.name, 'agent')}_会话导出_{stamp}{suffix}"
    if not default_name.lower().endswith(suffix):
        default_name = _with_suffix(default_name, suffix)

    selected = await _run_dialog(
        win_file_dialog.save_file_dialog,
        title=body.title or _DEFAULT_EXPORT_TITLE,
        owner=win_file_dialog.owner_hwnd(),
        filters=_SAVE_FILTER_ENCRYPTED if encrypt else _SAVE_FILTER_PLAIN,
        default_name=default_name,
        default_ext="enc" if encrypt else "zip",
    )
    if not selected:
        return {"canceled": True, "path": "", "encrypted": encrypt, "sessions": []}
    out_path = Path(_with_suffix(str(selected), suffix))

    # 逐个会话取 checkpoint（只取每个 namespace 的最新 checkpoint）与可读副本
    sessions: list[dict] = []
    skipped: list[str] = []
    for thread_id in dict.fromkeys(thread_ids):
        try:
            payload = await chat_manager.export_thread_checkpoints(cfg.agent_id, thread_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("导出会话失败 (agent=%s thread=%s)", cfg.agent_id, thread_id)
            raise HTTPException(
                status_code=400,
                detail=f"读取会话「{thread_id}」失败，未导出任何文件：{exc}",
            )
        if not (payload.get("namespaces") or {}):
            # 只是登记过、还没发过消息的空会话：跳过并如实告诉用户，不拖累其它会话
            skipped.append(thread_id)
            continue
        conversation = await _conversation_markdown(cfg.agent_id, thread_id)
        sessions.append(
            {
                "thread_id": thread_id,
                "name": thread_id,
                "message_count": thread_transfer.count_messages(payload),
                "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "payload": payload,
                "conversation": conversation,
            }
        )

    if not sessions:
        raise HTTPException(
            status_code=400,
            detail="选中的会话都还没有任何对话内容（没有 checkpoint），无法导出",
        )

    manifest = {
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "app": {
            "edition": "lite" if _is_lite() else "standard",
            "storage": _storage_backend(),
        },
    }
    zip_bytes = session_archive.build_archive(
        agent_config=cfg.model_dump(), manifest=manifest, sessions=sessions
    )
    try:
        blob = (
            await asyncio.to_thread(
                session_archive.encrypt_archive, zip_bytes, password
            )
            if encrypt
            else zip_bytes
        )
    except session_archive.SessionArchiveError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(blob)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=400,
            detail=f"无法写入导出文件（目录可能只读或没有权限）：{exc}",
        )

    return {
        "canceled": False,
        "path": str(out_path),
        "encrypted": encrypt,
        "size": len(blob),
        "skipped": skipped,
        "sessions": [
            {"thread_id": s["thread_id"], "message_count": s["message_count"]}
            for s in sessions
        ],
    }


# ── 导入：选文件 ───────────────────────────────────────────────────


class SelectBody(BaseModel):
    title: str = ""


@router.post("/api/session-import/select")
async def import_select(body: SelectBody | None = None):
    _require_feature_enabled()
    selected = await _run_dialog(
        win_file_dialog.open_file_dialog,
        title=(body.title if body else "") or _DEFAULT_IMPORT_TITLE,
        owner=win_file_dialog.owner_hwnd(),
        filters=_ZIP_FILTER,
    )
    if not selected:
        return {"canceled": True, "path": "", "encrypted": False}
    path = Path(str(selected))
    if not path.is_file():
        raise HTTPException(status_code=400, detail="所选文件不存在或不是文件")
    return {
        "canceled": False,
        "path": str(path),
        "name": path.name,
        "encrypted": session_archive.look_encrypted(path),
        "size": path.stat().st_size,
    }


# ── 导入：生成迁移计划 ─────────────────────────────────────────────


class PlanBody(BaseModel):
    path: str
    password: str = ""


def _load_archive_sync(path: str, password: str) -> dict:
    file_path = Path(str(path or ""))
    if not file_path.is_file():
        raise session_archive.SessionArchiveError("会话文件不存在")
    try:
        raw = file_path.read_bytes()
    except OSError as exc:
        raise session_archive.SessionArchiveError(f"无法读取会话文件：{exc}")
    if session_archive.is_encrypted_bytes(raw):
        if not str(password or "").strip():
            raise session_archive.SessionArchiveError("该会话文件已加密，需要输入密码")
        raw = session_archive.decrypt_archive(raw, password)
    session_archive.read_finished_zip(raw)
    return session_archive.read_archive(raw)


async def _load_archive(path: str, password: str) -> dict:
    try:
        return await asyncio.to_thread(_load_archive_sync, path, password)
    except session_archive.SessionArchiveError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _fit_name(base: str, suffix: str = "", limit: int = 63) -> str:
    """把「原名 + 后缀」压进 63 字节（PostgreSQL 标识符上限），中文按 UTF-8 字节算。"""
    text = _safe_name(base, "导入的配置")
    while text and len((text + suffix).encode("utf-8")) > limit:
        text = text[:-1]
    return (text.strip() or "配置") + suffix


def _suggest_config_name(base: str) -> str:
    """给「要新建的配置」取一个本机可用的名字（原名被占用时自动加后缀）。"""
    try:
        clean = validate_agent_name(base)
    except DbNameError:
        clean = validate_agent_name(_fit_name(base))
    existing = set()
    for cfg in config_store.list():
        existing.add(cfg.name.casefold())
        existing.add(cfg.agent_id.casefold())
    if clean.casefold() not in existing:
        return clean
    for index in range(1, 100):
        suffix = " (导入)" if index == 1 else f" (导入 {index})"
        candidate = _fit_name(base, suffix)
        try:
            validate_agent_name(candidate)
        except DbNameError:
            continue
        if candidate.casefold() not in existing:
            return candidate
    return clean


@router.post("/api/session-import/plan")
async def import_plan(body: PlanBody):
    _require_feature_enabled()
    archive = await _load_archive(body.path, body.password)
    manifest = archive["manifest"]
    agent_config = archive["agent_config"]
    sessions = archive["sessions"]
    exported = session_archive.agent_identity(agent_config)

    local_rows: list[dict] = []
    same_name: dict | None = None
    for cfg in config_store.list():
        identity = _local_identity(cfg)
        compatible, reason = session_archive.sub_agent_compat(
            exported["sub_agents"], identity["sub_agents"]
        )
        row = {
            "agent_id": cfg.agent_id,
            "name": cfg.name,
            "sub_agent_names": [s.get("name") or "" for s in identity["sub_agents"]],
            "compatible": compatible,
            "reason": reason,
        }
        local_rows.append(row)
        if same_identity(cfg.name, exported["name"]) or same_identity(
            cfg.agent_id, exported["agent_id"]
        ):
            same_name = dict(row)

    return {
        "file": {
            "path": str(body.path),
            "name": Path(str(body.path)).name,
            "encrypted": session_archive.look_encrypted(body.path),
        },
        "format_version": manifest.get("format_version"),
        "created_at": manifest.get("created_at") or "",
        "app": manifest.get("app") or {},
        "exported_agent": {
            "name": exported["name"],
            "agent_id": exported["agent_id"],
            "sub_agents": [s.get("name") or "" for s in exported["sub_agents"]],
            "session_count": len(sessions),
        },
        "sessions": [
            {
                "thread_id": s.get("thread_id") or "",
                "name": s.get("name") or "",
                "message_count": int(s.get("message_count") or 0),
                "last_updated": s.get("last_updated") or "",
            }
            for s in sessions
        ],
        "local_configs": local_rows,
        "same_name_config": same_name,
        "suggested_name": _suggest_config_name(exported["name"] or exported["agent_id"]),
    }


# ── 导入：执行 ─────────────────────────────────────────────────────


class ApplyBody(BaseModel):
    path: str
    password: str = ""
    mode: Literal["existing", "with_config"] = "existing"
    target_agent_id: str = ""
    new_agent_name: str = ""


def _config_warnings(cfg: MultiAgentConfig) -> list[str]:
    """导入配置后提醒用户「这些路径来自来源机器」。"""
    warnings: list[str] = []
    root_dir = (cfg.main_agent.file_tools.root_dir or "").strip()
    if root_dir and not os.path.isdir(root_dir):
        warnings.append(f"文件工具工作目录在本机不存在：{root_dir}")
    out_dir = (cfg.output.stream_output_dir or "").strip()
    if out_dir and not os.path.isdir(out_dir):
        warnings.append(f"流式输出目录在本机不存在：{out_dir}")
    return warnings


async def _create_config_from_export(
    exported_config: dict, name: str
) -> MultiAgentConfig:
    """按导出的配置在本机新建一个 multi-agent（身份字段与连接按本机规则重算）。"""
    try:
        cfg = MultiAgentConfig.model_validate(exported_config)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=400, detail=f"会话文件里的 multi-agent 配置无法在本机使用：{exc}"
        )

    # 身份三件套由名称派生（与 create_agent 完全一致）
    cfg.name = name
    cfg.agent_id = name
    cfg.postgres.checkpoint_database = name

    settings = _settings()
    # 连接串用本机的全局设置（跨机器导入时，来源机器的 host/账号在本机通常不可用）
    if (settings.global_postgres_prefix or "").strip():
        cfg.postgres.prefix = settings.global_postgres_prefix
    if (settings.global_postgres_suffix or "").strip():
        cfg.postgres.suffix = settings.global_postgres_suffix
    cfg.postgres.store_database = settings.effective_store_database()

    # 保留导出的子 agent 状态通道键（会话数据就存在这些通道里），缺失的按键名补全
    from app.runtime.state_factory import (
        assign_extracted_summary_ai_msg_keys,
        assign_history_token_measure_keys,
        assign_state_messages_keys,
    )

    assign_state_messages_keys(cfg)
    assign_history_token_measure_keys(cfg)
    assign_extracted_summary_ai_msg_keys(cfg)

    # 复用 agents.py 的建库 / 名称占用校验，避免两套规则漂移
    from app.api.agents import _ensure_agent_databases, _ensure_name_is_free, _is_sqlite_storage

    _ensure_name_is_free(name)
    await _ensure_agent_databases(cfg)
    if not _is_sqlite_storage():
        from app.services import threads as threads_service

        ok, err = await threads_service.test_connection(cfg.checkpoint_conn_string)
        if not ok:
            raise HTTPException(
                status_code=400,
                detail=(
                    "本机连不上导入配置所需的数据库，所以没有创建配置。\n"
                    "请先在「系统设置 → 全局配置设置」里填好本机的 PostgreSQL 连接"
                    "（或改用 Lite 的 SQLite 存储），再重新导入。\n\n"
                    f"原始错误：{err}"
                ),
            )

    await chat_manager.invalidate(cfg.agent_id)
    config_store.save(cfg)
    return cfg


@router.post("/api/session-import/apply")
async def import_apply(body: ApplyBody):
    _require_feature_enabled()
    archive = await _load_archive(body.path, body.password)
    exported_config = archive["agent_config"]
    sessions = archive["sessions"]
    if not sessions:
        raise HTTPException(status_code=400, detail="该会话文件里没有任何会话")

    exported = session_archive.agent_identity(exported_config)
    warnings: list[str] = []
    created_config = False

    if body.mode == "existing":
        # 语义 1：直接把会话并进本机已有的配置（永不新建配置）
        target_id = str(body.target_agent_id or "").strip()
        if not target_id:
            raise HTTPException(status_code=400, detail="请选择要导入到哪个本机配置")
        try:
            target = config_store.load(target_id)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="所选的本机配置不存在")
        compatible, reason = session_archive.sub_agent_compat(
            exported["sub_agents"], _local_identity(target)["sub_agents"]
        )
        if not compatible:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"这些会话无法迁入本机配置「{target.name}」：{reason}。\n"
                    "可以选择「连同原来的 multi-agent 配置一并导入」，或取消。"
                ),
            )
    else:
        # 语义 2：**永远新建一个** multi-agent 配置（想并入已有配置请走「加载到本机已有的配置」）。
        # 名称由前端要求用户填写（默认已避开重名）；这里仍按不可信输入再校验一次。
        raw_name = str(body.new_agent_name or "").strip()
        if not raw_name:
            raise HTTPException(
                status_code=400, detail="请为要导入的 multi-agent 配置填写一个名称"
            )
        try:
            name = validate_agent_name(raw_name)
        except DbNameError as exc:
            raise HTTPException(status_code=400, detail=f"配置名称不可用：{exc}")

        existing = None
        for cfg in config_store.list():
            if same_identity(cfg.name, name) or same_identity(cfg.agent_id, name):
                existing = cfg
                break
        if existing is not None:
            compatible, reason = session_archive.sub_agent_compat(
                exported["sub_agents"], _local_identity(existing)["sub_agents"]
            )
            if compatible:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"本机已有同名配置「{existing.name}」。\n"
                        "如果想把会话直接并入它，请选择「加载到本机已有的 multi-agent 配置」；"
                        "如果要连同配置一并导入，请换一个不重名的名称。"
                    ),
                )
            raise HTTPException(
                status_code=409,
                detail=(
                    f"本机已有同名配置「{existing.name}」，但和要导入的会话不匹配：{reason}。\n"
                    "请为要迁入的 multi-agent 配置换一个不重名的名称。"
                ),
            )

        target = await _create_config_from_export(exported_config, name)
        created_config = True
        warnings.extend(_config_warnings(target))

    # 目标库里已占用的会话名（导入后重名会覆盖别人的历史，必须避开）
    taken = set()
    try:
        for item in await chat_manager.list_threads(target.agent_id):
            taken.add(str(item.get("thread_id") or "").casefold())
    except Exception:  # noqa: BLE001 — 列表读不到不阻塞导入，下面逐个兜底
        logger.exception("读取目标配置的会话列表失败 (agent=%s)", target.agent_id)

    results: list[dict] = []
    for session in sessions:
        source_thread_id = str(session.get("thread_id") or "")
        new_thread_id = _free_thread_id(source_thread_id, taken)
        taken.add(new_thread_id.casefold())
        entry = {
            "source_thread_id": source_thread_id,
            "thread_id": new_thread_id,
            "renamed": new_thread_id != source_thread_id,
            "ok": False,
            "error": "",
        }
        try:
            written = await chat_manager.import_thread_checkpoints(
                target.agent_id, new_thread_id, session.get("payload") or {}
            )
            if not written:
                entry["error"] = "没有可导入的会话数据"
            else:
                await chat_manager.register_thread(target.agent_id, new_thread_id)
                entry["ok"] = True
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "导入会话失败 (agent=%s thread=%s)", target.agent_id, new_thread_id
            )
            entry["error"] = str(exc)
        results.append(entry)

    imported = sum(1 for r in results if r["ok"])
    return {
        "ok": imported > 0,
        "agent_id": target.agent_id,
        "agent_name": target.name,
        "created_config": created_config,
        "warnings": warnings,
        "imported": imported,
        "failed": len(results) - imported,
        "results": results,
    }
