"""旧版「每个 multi-agent 一个 store 数据库」的迁移。

背景：旧版本让用户为每个 multi-agent 单独填写 store 数据库名，长期记忆因此分散在
多个库里。新版本统一使用全局 store 数据库（默认 Long_Term_Memory），多 agent 之间
是否共用记忆只由 namespace 决定。

本模块负责：
1. 启动时检测 configs/*.json 里 store 数据库名与全局值不一致的配置，记录为「迁移源」，
   并把配置切换到全局 store 数据库（原库保持不动，等待迁移）。
2. 列出迁移源（原数据库名、原 namespace），供前端弹窗/设置页展示。
3. 把迁移源里的记忆复制到当前 store 数据库（可逐个编辑目标 namespace），
   复制并校验成功后删除原数据库（系统库 postgres/template0/template1 永不删除）。

安全：所有写操作都是「复制到新库」，删库只发生在复制校验成功之后；任何一步失败都
保留迁移源记录，便于重试。参见 inner_docs/ID46（误删数据库事故）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from psycopg import AsyncConnection, errors

from app.config.models import EmbeddingConfig
from app.config.settings import Settings
from app.config.store import ConfigStore
from app.services import pg_databases

MIGRATIONS_FILE_NAME = "store_migrations.json"
_MIGRATION_LOCK = asyncio.Lock()


def _path(config_dir: Path | str) -> Path:
    return Path(config_dir) / MIGRATIONS_FILE_NAME


def _empty_state() -> dict:
    return {"prompted": False, "sources": []}


def load_state(config_dir: Path | str) -> dict:
    path = _path(config_dir)
    if not path.exists():
        return _empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return _empty_state()
    if not isinstance(data, dict):
        return _empty_state()
    sources = data.get("sources")
    if not isinstance(sources, list):
        sources = []
    # 只保留结构完整的条目，避免坏数据把迁移界面拖垮
    clean = []
    for src in sources:
        if not isinstance(src, dict):
            continue
        db = str(src.get("database") or "").strip()
        if not db:
            continue
        clean.append(
            {
                "id": str(src.get("id") or _source_id(src.get("prefix", ""), src.get("suffix", ""), db)),
                "database": db,
                "prefix": str(src.get("prefix") or ""),
                "suffix": str(src.get("suffix") or ""),
                "agents": [str(a) for a in (src.get("agents") or [])],
            }
        )
    return {"prompted": bool(data.get("prompted")), "sources": clean}


def save_state(config_dir: Path | str, state: dict) -> None:
    d = Path(config_dir)
    d.mkdir(parents=True, exist_ok=True)
    _path(d).write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _source_id(prefix: str, suffix: str, database: str) -> str:
    raw = f"{prefix}|{suffix}|{database}".encode("utf-8", "replace")
    return hashlib.sha1(raw).hexdigest()[:12]


def _find_source(state: dict, source_id: str) -> dict | None:
    for src in state.get("sources", []):
        if src.get("id") == source_id:
            return src
    return None


def _remove_source(state: dict, source_id: str) -> None:
    state["sources"] = [s for s in state.get("sources", []) if s.get("id") != source_id]


def detect_and_record(config_store: ConfigStore, settings: Settings) -> dict:
    """检测旧 store 库并记录迁移源，然后把配置切到当前全局 store 数据库。

    返回最新的迁移状态。该函数是幂等的：迁移源已存在时只补 agent 列表。
    """
    current = settings.effective_store_database()
    state = load_state(config_store._dir)
    changed_state = False

    # 已经与全局库一致、或被改名成全局库的迁移源，直接清掉记录
    before = len(state["sources"])
    state["sources"] = [s for s in state["sources"] if s["database"] != current]
    if len(state["sources"]) != before:
        changed_state = True

    for cfg in config_store.list():
        db = (cfg.postgres.store_database or "").strip()
        if not db:
            # 空值（损坏/极旧配置）直接补全局库名，不产生迁移源
            cfg.postgres.store_database = current
            try:
                config_store.save(cfg)
            except Exception:  # noqa: BLE001 - 单个坏配置不阻断其它迁移源
                pass
            continue
        if db == current:
            continue
        sid = _source_id(cfg.postgres.prefix, cfg.postgres.suffix, db)
        src = _find_source(state, sid)
        if src is None:
            state["sources"].append(
                {
                    "id": sid,
                    "database": db,
                    "prefix": cfg.postgres.prefix,
                    "suffix": cfg.postgres.suffix,
                    "agents": [cfg.agent_id],
                }
            )
            changed_state = True
        elif cfg.agent_id not in src["agents"]:
            src["agents"].append(cfg.agent_id)
            changed_state = True

        # 立即切换到统一 store 库；旧库数据保留，等待用户迁移。
        cfg.postgres.store_database = current
        try:
            config_store.save(cfg)
        except Exception:  # noqa: BLE001 - 单个坏配置不阻断其它迁移源
            pass

    if changed_state:
        save_state(config_store._dir, state)
    return state


async def _probe_source(source: dict) -> dict:
    """只读探测源库：能否连接、有哪些 namespace。"""
    conn_string = pg_databases.join_conn_string(
        source.get("prefix", ""), source["database"], source.get("suffix", "")
    )
    info = {"exists": True, "namespaces": [], "error": ""}
    try:
        async with await AsyncConnection.connect(conn_string, connect_timeout=3, autocommit=True) as conn:
            cur = await conn.execute("SELECT to_regclass('store')")
            row = await cur.fetchone()
            if not row or not row[0]:
                info["error"] = "源数据库中还没有长期记忆表（没有可迁移的数据）"
                return info
            cur = await conn.execute(
                "SELECT DISTINCT prefix FROM store WHERE prefix <> '' ORDER BY prefix"
            )
            info["namespaces"] = [r[0] for r in await cur.fetchall()]
    except errors.InvalidCatalogName:
        info["exists"] = False
        info["error"] = "源数据库不存在（没有可迁移的数据）"
    except Exception:  # noqa: BLE001
        info["exists"] = False
        info["error"] = "无法连接源数据库（服务未启动或连接信息已变化）"
    return info


async def list_sources(config_dir: Path | str, settings: Settings, *, probe: bool = True) -> dict:
    """返回迁移状态：当前库名、是否已弹过窗、每个迁移源的详情。"""
    state = load_state(config_dir)
    current = settings.effective_store_database()
    sources = []
    for src in state["sources"]:
        probe_result = await _probe_source(src) if probe else {"exists": True, "namespaces": [], "error": ""}
        sources.append(
            {
                "id": src["id"],
                "database": src["database"],
                "masked_prefix": pg_databases.masked_prefix(src.get("prefix", "")),
                "suffix": src.get("suffix", ""),
                "agents": src.get("agents", []),
                "exists": probe_result["exists"],
                "error": probe_result["error"],
                "namespaces": [
                    {"source": ns, "target": ns}
                    for ns in probe_result["namespaces"]
                ],
                "is_system": src["database"] in pg_databases.SYSTEM_DATABASES,
            }
        )
    return {
        "current_database": current,
        "prompted": state["prompted"],
        "sources": sources,
    }


def mark_prompted(config_dir: Path | str) -> dict:
    state = load_state(config_dir)
    if not state["prompted"]:
        state["prompted"] = True
        save_state(config_dir, state)
    return state


def remove_source(config_dir: Path | str, source_id: str) -> None:
    state = load_state(config_dir)
    _remove_source(state, source_id)
    save_state(config_dir, state)


def _normalize_namespace(value: str) -> tuple[str, ...]:
    text = str(value or "").strip()
    if not text:
        raise pg_databases.DatabaseError("新的命名空间不能为空")
    parts = [p.strip() for p in text.split(".")]
    if any(not p for p in parts):
        raise pg_databases.DatabaseError("命名空间格式不正确：不能有空层级（例如 a..b）")
    if any(len(p.encode("utf-8")) > 63 for p in parts):
        raise pg_databases.DatabaseError("命名空间的单个层级过长（上限 63 字节）")
    return tuple(parts)


def _embedding_config_for(config_store: ConfigStore, source: dict) -> EmbeddingConfig:
    for agent_id in source.get("agents", []):
        try:
            return config_store.load(agent_id).main_agent.embedding
        except Exception:  # noqa: BLE001
            continue
    try:
        configs = config_store.list()
        if configs:
            return configs[0].main_agent.embedding
    except Exception:  # noqa: BLE001
        pass
    return EmbeddingConfig()


async def _read_source_rows(conn_string: str) -> dict[str, list[dict]] | None:
    """读取源库全部记忆；没有 store 表时返回 None。"""
    try:
        async with await AsyncConnection.connect(conn_string, connect_timeout=8, autocommit=True) as conn:
            cur = await conn.execute("SELECT to_regclass('store')")
            row = await cur.fetchone()
            if not row or not row[0]:
                return None
            cur = await conn.execute("SELECT prefix, key, value FROM store ORDER BY prefix, key")
            rows: dict[str, list[dict]] = {}
            for prefix, key, value in await cur.fetchall():
                rows.setdefault(prefix, []).append({"key": key, "value": value})
            return rows
    except errors.InvalidCatalogName:
        return None


async def _verify_target(conn_string: str, target_ns: str, keys: list[str]) -> None:
    async with await AsyncConnection.connect(conn_string, connect_timeout=8, autocommit=True) as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM store WHERE prefix = %s AND key = ANY(%s)",
            (target_ns, keys),
        )
        got = (await cur.fetchone())[0]
        if got != len(keys):
            raise pg_databases.DatabaseError(
                f"迁移校验失败：命名空间「{target_ns}」应有 {len(keys)} 条，实际写入 {got} 条"
            )
        cur = await conn.execute(
            "SELECT count(*) FROM store_vectors WHERE prefix = %s AND key = ANY(%s)",
            (target_ns, keys),
        )
        vectors = (await cur.fetchone())[0]
        if vectors != len(keys):
            raise pg_databases.DatabaseError(
                f"迁移校验失败：命名空间「{target_ns}」的向量应有 {len(keys)} 条，实际 {vectors} 条"
            )


async def run_migrations(
    config_store: ConfigStore,
    settings: Settings,
    selections: list[dict],
) -> dict:
    """执行迁移。selections: [{id, namespaces:[{source, target}]}]。"""
    async with _MIGRATION_LOCK:
        current = settings.effective_store_database()
        state = load_state(config_store._dir)
        results: list[dict] = []

        for sel in selections or []:
            source = _find_source(state, str(sel.get("id") or ""))
            if source is None:
                continue
            result = {
                "id": source["id"],
                "database": source["database"],
                "status": "failed",
                "copied": 0,
                "message": "",
            }
            try:
                # 源库不存在 / 没有记忆表 → 没有数据可迁，直接移除记录
                source_conn = pg_databases.join_conn_string(
                    source.get("prefix", ""), source["database"], source.get("suffix", "")
                )
                rows_by_ns = await _read_source_rows(source_conn)
                if rows_by_ns is None:
                    _remove_source(state, source["id"])
                    result.update(status="skipped", message="源数据库不存在或没有长期记忆表，已移除迁移记录")
                    results.append(result)
                    continue
                if not rows_by_ns:
                    _remove_source(state, source["id"])
                    result.update(status="skipped", message="源数据库里没有记忆条目，已移除迁移记录")
                    results.append(result)
                    continue

                mappings = sel.get("namespaces") or []
                if not mappings:
                    mappings = [{"source": ns, "target": ns} for ns in rows_by_ns]
                normalized: list[tuple[str, tuple[str, ...]]] = []
                for m in mappings:
                    src_ns = str((m or {}).get("source") or "").strip()
                    if not src_ns:
                        continue
                    normalized.append((src_ns, _normalize_namespace((m or {}).get("target"))))
                if not normalized:
                    raise pg_databases.DatabaseError("请至少填写一个目标命名空间")

                # 安全兜底：源库里还有未选择迁移的命名空间时，绝不删旧库
                selected_sources = {src_ns for src_ns, _ in normalized}
                unmapped = [
                    ns for ns, items in rows_by_ns.items() if items and ns not in selected_sources
                ]
                if unmapped:
                    raise pg_databases.DatabaseError(
                        "源库里还有未选择迁移的命名空间："
                        + "、".join(unmapped)
                        + "。请刷新迁移列表后重试。"
                    )

                # 目标库与源库在同一台服务器上；不存在就先建库
                await pg_databases.ensure_database(
                    source.get("prefix", ""), current, source.get("suffix", "")
                )

                target_conn = pg_databases.join_conn_string(
                    source.get("prefix", ""), current, source.get("suffix", "")
                )
                total = sum(len(rows_by_ns.get(src_ns, [])) for src_ns, _ in normalized)
                if total:
                    from app.runtime.persistence import build_embeddings, make_store_index_config

                    emb_cfg = _embedding_config_for(config_store, source)
                    embeddings = await build_embeddings(emb_cfg)
                    index = make_store_index_config(emb_cfg, embeddings)
                    from langgraph.store.postgres import AsyncPostgresStore

                    async with AsyncPostgresStore.from_conn_string(
                        target_conn, index=index
                    ) as store:
                        await store.setup()
                        for src_ns, target_ns in normalized:
                            for item in rows_by_ns.get(src_ns, []):
                                await store.aput(target_ns, item["key"], item["value"])

                for src_ns, target_ns in normalized:
                    items = rows_by_ns.get(src_ns, [])
                    if not items:
                        continue
                    await _verify_target(
                        target_conn, ".".join(target_ns), [i["key"] for i in items]
                    )

                # 复制并校验成功后删除旧库（系统库永不删除）
                drop_note = ""
                if source["database"] in pg_databases.SYSTEM_DATABASES:
                    drop_note = f"系统库「{source['database']}」按安全规则不删除。"
                elif source["database"] == current:
                    drop_note = "源库与当前 store 库同名，跳过删除。"
                else:
                    try:
                        drop_state = await pg_databases.drop_database(
                            source.get("prefix", ""), source.get("suffix", ""), source["database"]
                        )
                        if drop_state == "dropped":
                            drop_note = f"旧数据库「{source['database']}」已删除。"
                        elif drop_state == "system":
                            drop_note = f"系统库「{source['database']}」按安全规则不删除。"
                        else:
                            drop_note = f"旧数据库「{source['database']}」不存在，无需删除。"
                    except pg_databases.DatabaseError as exc:
                        # 数据已复制并校验，但旧库没删掉：保留迁移记录，允许重试或手动移除记录
                        raise pg_databases.DatabaseError(
                            f"数据已迁移，但旧库删除失败：{exc}"
                            "。可重试，或手动删除旧库后点「移除这条迁移记录」。"
                        ) from exc

                _remove_source(state, source["id"])
                result.update(
                    status="ok",
                    copied=total,
                    message=drop_note or "迁移完成",
                )
            except Exception as exc:  # noqa: BLE001
                result.update(
                    status="failed",
                    message=str(exc) or "迁移失败（未知错误）",
                )
            results.append(result)

        save_state(config_store._dir, state)
        return {
            "current_database": current,
            "remaining": len(state["sources"]),
            "results": results,
        }
