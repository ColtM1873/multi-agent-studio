"""把现有 multi-agent 的身份三件套统一成「名称」。

新规则（用户只填名称）：

- ``agent_id`` = 名称
- ``checkpoint_database`` = 名称

旧配置里两者通常与名称不同（例如 si-agent / SI Agent / sia_agent_checkpoints）。
本模块在程序启动时扫描 ``configs/*.json``，**不弹窗、不询问**，直接把：

1. checkpoint 数据库原地改名为名称（``ALTER DATABASE ... RENAME``）；
2. 配置文件名 ``configs/<agent_id>.json`` 改名为 ``configs/<名称>.json``；
3. 所有以 agent_id 为键的本地数据同步改名：快照目录、drafts、common_prompts、
   ui_prefs（隐藏会话 / 隐藏 agent / 接管提示）、store 迁移记录里的 agents 列表。

安全边界（见 inner_docs/ID46、ID83）：

- 每台服务器先单独探测（host:port），连不上就整台跳过、下次启动重试；
- 新旧数据库同时存在 / 旧库是系统库 / 名称重复 / 名称非法 → 记录原因并跳过，
  绝不猜测、绝不覆盖；
- 旧库不存在但新库已存在 → 视为「上次已改名成功」，只补做文件改名；
- 全部操作幂等：中途失败后重启会从断点继续；
- 改动前先把 ``.json.migrating`` 临时文件的恢复逻辑跑一遍，避免 case-only 改名中断。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import threading
from datetime import datetime
from pathlib import Path

from psycopg import AsyncConnection

from app.config.db_names import (
    SYSTEM_DATABASES,
    DatabaseError,
    join_conn_string,
    same_identity,
    validate_agent_name,
    validate_database_name,
)
from app.config.store import ConfigStore
from app.services import pg_databases
from app.services.pg_service import parse_conn_host_port

logger = logging.getLogger(__name__)

STATE_FILE_NAME = "identity_migration.json"
STATE_VERSION = 1


class _AsyncThreadLock:
    """跨事件循环也能用的异步互斥（内部是 threading.Lock）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    async def __aenter__(self):
        while not self._lock.acquire(blocking=False):
            await asyncio.sleep(0.05)
        return self

    async def __aexit__(self, *exc_info):
        self._lock.release()
        return False


# 启动期的两次调用（lifespan 同步做一次、PG 服务拉起后后台线程再补一次）本来
# 就是顺序的；这里再用锁兜底，避免将来有人从接口里并发触发。
# 注意不能用 asyncio.Lock：两次调用可能落在不同的事件循环（主循环 / 后台线程）。
_LOCK = _AsyncThreadLock()


# --------------------------------------------------------------------------- #
# 状态文件
# --------------------------------------------------------------------------- #
def _state_path(config_dir: Path | str) -> Path:
    return Path(config_dir) / STATE_FILE_NAME


def _empty_state() -> dict:
    return {"version": STATE_VERSION, "updated_at": "", "renames": [], "pending": []}


def load_state(config_dir: Path | str) -> dict:
    path = _state_path(config_dir)
    if not path.exists():
        return _empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return _empty_state()
    if not isinstance(data, dict):
        return _empty_state()
    state = _empty_state()
    renames = data.get("renames")
    if isinstance(renames, list):
        state["renames"] = [r for r in renames if isinstance(r, dict)]
    pending = data.get("pending")
    if isinstance(pending, list):
        state["pending"] = [p for p in pending if isinstance(p, dict)]
    state["updated_at"] = str(data.get("updated_at") or "")
    return state


def _save_state(config_dir: Path | str, state: dict) -> None:
    state["version"] = STATE_VERSION
    state["updated_at"] = _now()
    _write_json(_state_path(config_dir), state)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def _load_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _write_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def server_label(prefix: str) -> str:
    """把连接前缀里的 host:port 提取成人类可读的「服务器」标识（迁移前先识别 server）。"""
    host, port = parse_conn_host_port(prefix)
    if host and port:
        return f"{host}:{port}"
    if host:
        return host
    return "（未填写连接前缀）"


def _recover_migrating_files(config_dir: Path) -> None:
    """恢复上次 case-only 改名中途留下的临时配置文件。"""
    for tmp in Path(config_dir).glob(".*.json.migrating"):
        try:
            data = json.loads(tmp.read_text(encoding="utf-8"))
            agent_id = str(data.get("agent_id") or "").strip()
            if not agent_id:
                continue
            target = Path(config_dir) / f"{agent_id}.json"
            if target.exists():
                tmp.unlink(missing_ok=True)
            else:
                os.replace(tmp, target)
                logger.info("[identity] 恢复中断的配置文件：%s", target.name)
        except Exception:  # noqa: BLE001
            continue


async def _can_connect(prefix: str, suffix: str, database: str) -> tuple[bool, str]:
    try:
        conn = await AsyncConnection.connect(
            join_conn_string(prefix, database, suffix),
            connect_timeout=3,
            autocommit=True,
        )
        await conn.close()
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


async def _rename_checkpoint_database(
    prefix: str, suffix: str, old: str, new: str
) -> str:
    """把 checkpoint 库原地改名为新名字；返回 renamed / exists / created / unchanged。"""
    if not old:
        created = await pg_databases.ensure_database(prefix, new, suffix)
        return "created" if created else "exists"
    validate_database_name(old, label="旧 checkpoint 数据库名称")
    validate_database_name(new, label="checkpoint 数据库名称")
    if old in SYSTEM_DATABASES:
        raise DatabaseError(f"旧 checkpoint 库「{old}」是 PostgreSQL 系统库，不能改名")
    result = await pg_databases.rename_database(prefix, suffix, old, new)
    if result == "missing":
        # 旧库不存在、新库也不存在：建一个空库让 agent 可用（历史数据本来也不在了）
        created = await pg_databases.ensure_database(prefix, new, suffix)
        return "created" if created else "exists"
    return result


# --------------------------------------------------------------------------- #
# 本地数据（以 agent_id 为键）改名
# --------------------------------------------------------------------------- #
def _rename_json_key(path: Path, old: str, new: str) -> bool:
    if old == new or not Path(path).exists():
        return False
    data = _load_json(path, None)
    if not isinstance(data, dict) or old not in data:
        return False
    if new in data:
        # 目标键已经存在：保守起见不再覆盖，只删旧键（值仍留在新键上）
        data.pop(old, None)
    else:
        data[new] = data.pop(old)
    _write_json(path, data)
    return True


def _migrate_ui_prefs(path: Path, old: str, new: str) -> bool:
    if old == new or not Path(path).exists():
        return False
    data = _load_json(path, None)
    if not isinstance(data, dict):
        return False
    changed = False

    for prefix in ("hidden_threads_", "show_hidden_"):
        old_key, new_key = prefix + old, prefix + new
        if old_key not in data:
            continue
        if new_key not in data:
            data[new_key] = data.pop(old_key)
        else:
            data.pop(old_key, None)
        changed = True

    # 旧版浏览器接管的「每个对话只提示一次」键：takeover-hint-injected_<agent>_<thread>
    old_prefix = f"takeover-hint-injected_{old}_"
    for key in list(data.keys()):
        if not key.startswith(old_prefix):
            continue
        new_key = f"takeover-hint-injected_{new}_" + key[len(old_prefix):]
        if new_key not in data:
            data[new_key] = data.pop(key)
        else:
            data.pop(key, None)
        changed = True

    raw = data.get("hidden_agents")
    if isinstance(raw, str):
        try:
            arr = json.loads(raw)
        except Exception:  # noqa: BLE001
            arr = None
        if isinstance(arr, list):
            out: list = []
            for item in arr:
                value = new if str(item) == old else item
                if value not in out:
                    out.append(value)
            new_raw = json.dumps(out, ensure_ascii=False)
            if new_raw != raw:
                data["hidden_agents"] = new_raw
                changed = True

    if changed:
        _write_json(path, data)
    return changed


def _migrate_snapshot_dir(old: str, new: str) -> bool:
    from app.services import snapshot as snapshot_service

    src = snapshot_service._agent_snapshots_dir(old)
    dst = snapshot_service._agent_snapshots_dir(new)
    # 同 _rewrite_config：Windows 上 Path 的 == 大小写不敏感，这里按字符串比较。
    if str(src) == str(dst) or not src.exists() or not src.is_dir():
        return False
    dst.mkdir(parents=True, exist_ok=True)
    for item in list(src.iterdir()):
        target = dst / item.name
        if target.exists():
            continue
        try:
            shutil.move(str(item), str(target))
        except Exception:  # noqa: BLE001
            logger.exception("[identity] 移动快照文件失败：%s", item)
    try:
        src.rmdir()
    except OSError:
        # 还有冲突文件没搬走：保留目录，人工处理
        pass

    meta_path = dst / snapshot_service.AGENT_META_FILENAME
    meta = _load_json(meta_path, None)
    if isinstance(meta, dict) and meta.get("agent_id") != new:
        meta["agent_id"] = new
        _write_json(meta_path, meta)
    return True


def _migrate_store_migration_agents(config_dir: Path, old: str, new: str) -> bool:
    path = Path(config_dir) / "store_migrations.json"
    if not path.exists():
        return False
    data = _load_json(path, None)
    if not isinstance(data, dict):
        return False
    changed = False
    for src in data.get("sources") or []:
        if not isinstance(src, dict):
            continue
        agents = src.get("agents")
        if not isinstance(agents, list):
            continue
        out: list = []
        for agent in agents:
            value = new if str(agent) == old else agent
            if value not in out:
                out.append(value)
        if out != agents:
            src["agents"] = out
            changed = True
    if changed:
        _write_json(path, data)
    return changed


def _migrate_local_data(config_dir: Path, old: str, new: str) -> None:
    _rename_json_key(Path(config_dir) / "drafts.json", old, new)
    _rename_json_key(Path(config_dir) / "common_prompts.json", old, new)
    _migrate_ui_prefs(Path(config_dir) / "ui_prefs.json", old, new)
    _migrate_store_migration_agents(Path(config_dir), old, new)
    _migrate_snapshot_dir(old, new)


def _rewrite_config(config_store: ConfigStore, old_cfg, new_id: str) -> None:
    """把配置写到新文件名（agent_id / checkpoint_database 都改成名称），再删旧文件。"""
    cfg = old_cfg.model_copy(deep=True)
    cfg.agent_id = new_id
    cfg.postgres.checkpoint_database = new_id
    text = json.dumps(cfg.model_dump(), ensure_ascii=False, indent=2)

    directory = config_store._dir
    directory.mkdir(parents=True, exist_ok=True)
    old_path = directory / f"{old_cfg.agent_id}.json"
    new_path = directory / f"{new_id}.json"
    # 注意：Windows 上 Path 的 == 是大小写不敏感的（Path("A") == Path("a")）。
    # 只改大小写时必须走「先删旧名再落新名」的分支，否则会原地写回旧文件名。
    if str(old_path) == str(new_path):
        old_path.write_text(text, encoding="utf-8")
        return

    tmp = directory / f".{new_id}.json.migrating"
    tmp.write_text(text, encoding="utf-8")
    if os.path.normcase(str(old_path)) == os.path.normcase(str(new_path)):
        # Windows 上只改大小写：先删旧名再落新名；中断时由 _recover_migrating_files 恢复
        old_path.unlink(missing_ok=True)
        os.replace(tmp, new_path)
    else:
        os.replace(tmp, new_path)
        old_path.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def _plan(configs: list) -> list[dict]:
    """先算好每个 agent 的目标身份，并标出无法自动迁移的冲突。"""
    plans: list[dict] = []
    for cfg in configs:
        item = {"cfg": cfg, "name": None, "error": ""}
        try:
            item["name"] = validate_agent_name(cfg.name)
        except DatabaseError as exc:
            item["error"] = str(exc)
        plans.append(item)

    # 最终 agent_id（无法迁移的保留旧 id）不能重复（Windows 文件名不区分大小写）
    final_ids: dict[str, int] = {}
    for item in plans:
        cfg = item["cfg"]
        final_id = item["name"] or cfg.agent_id
        final_ids[final_id.casefold()] = final_ids.get(final_id.casefold(), 0) + 1

    for item in plans:
        cfg = item["cfg"]
        name = item["name"]
        if not name:
            continue
        if final_ids.get(name.casefold(), 0) > 1:
            item["error"] = "名称与其他 multi-agent 重复（不区分大小写），无法自动统一"
            item["name"] = None
            continue

        old_db = str(cfg.postgres.checkpoint_database or "")
        # 多个 agent 共用同一个 checkpoint 库：改其中一个就会把另一个的库名改走，保守跳过
        users = [
            other["cfg"].agent_id
            for other in plans
            if str(other["cfg"].postgres.checkpoint_database or "").casefold()
            == old_db.casefold()
        ]
        if len(users) > 1:
            item["error"] = f"多个 multi-agent 共用同一个 checkpoint 库「{old_db}」，无法自动改名"
            item["name"] = None
            continue

        # 目标 agent_id 不能撞上任何现有 agent 的旧文件名（即使对方之后也会迁走，
        # 也不冒险：先跳过，等它迁完下次启动再继续）
        for other in plans:
            if other is item:
                continue
            other_cfg = other["cfg"]
            if same_identity(other_cfg.agent_id, name):
                item["error"] = (
                    f"目标 agent_id「{name}」与现有配置文件 {other_cfg.agent_id}.json 冲突，"
                    "下次启动会再尝试"
                )
                item["name"] = None
                break
    return plans


async def run_for_all(config_store: ConfigStore) -> dict:
    """扫描并统一所有 multi-agent 的身份（幂等；失败项记录后下次重试）。"""
    async with _LOCK:
        config_dir = config_store._dir
        _recover_migrating_files(config_dir)
        configs = config_store.list()
        if not configs:
            return {"renamed": [], "pending": [], "servers": {}}

        plans = _plan(configs)
        store_db = ""
        try:
            from app.config.settings import load_settings

            store_db = load_settings(config_dir).effective_store_database()
        except Exception:  # noqa: BLE001
            pass
        state = load_state(config_dir)
        renamed: list[dict] = []
        pending: list[dict] = []
        probe_results: dict[tuple[str, str], tuple[bool, str]] = {}
        servers: dict[str, bool] = {}

        for item in plans:
            cfg = item["cfg"]
            name: str | None = item["name"]
            old_id = cfg.agent_id
            old_db = str(cfg.postgres.checkpoint_database or "")
            server = server_label(cfg.postgres.prefix)
            item["server"] = server

            if not name:
                pending.append(
                    {
                        "agent_id": old_id,
                        "name": cfg.name,
                        "server": server,
                        "old_db": old_db,
                        "reason": item["error"] or "名称不可用",
                    }
                )
                continue
            if old_id == name and old_db == name:
                continue  # 已经统一

            if store_db and same_identity(name, store_db):
                pending.append(
                    {
                        "agent_id": old_id,
                        "name": cfg.name,
                        "server": server,
                        "old_db": old_db,
                        "reason": f"名称与全局 store 数据库「{store_db}」同名，无法作为 checkpoint 库",
                    }
                )
                continue

            prefix = cfg.postgres.prefix
            suffix = cfg.postgres.suffix

            # ── 1) 先识别 server：连不上就整台跳过，绝不在"看起来像"的服务器上动手 ──
            probe_key = (prefix, suffix)
            if probe_key not in probe_results:
                probe_ok = False
                probe_err = ""
                # 旧库可能已被改名（上次中断），所以依次尝试 旧库 / 新库 / 维护库
                for probe_db in (old_db, name, "postgres"):
                    if not probe_db:
                        continue
                    probe_ok, probe_err = await _can_connect(prefix, suffix, probe_db)
                    if probe_ok:
                        break
                probe_results[probe_key] = (probe_ok, probe_err)
            ok, err = probe_results[probe_key]
            servers[server] = ok
            if not ok:
                pending.append(
                    {
                        "agent_id": old_id,
                        "name": cfg.name,
                        "server": server,
                        "old_db": old_db,
                        "reason": f"无法连接服务器 {server}（服务未启动或连接信息已变化），下次启动重试",
                    }
                )
                continue

            # ── 2) checkpoint 库原地改名 ──
            db_action = "unchanged"
            try:
                if old_db != name:
                    db_action = await _rename_checkpoint_database(prefix, suffix, old_db, name)
                else:
                    await pg_databases.ensure_database(prefix, name, suffix)
            except DatabaseError as exc:
                pending.append(
                    {
                        "agent_id": old_id,
                        "name": cfg.name,
                        "server": server,
                        "old_db": old_db,
                        "reason": f"数据库改名失败：{exc}",
                    }
                )
                logger.warning("[identity] %s：%s", cfg.name, exc)
                continue
            except Exception as exc:  # noqa: BLE001
                pending.append(
                    {
                        "agent_id": old_id,
                        "name": cfg.name,
                        "server": server,
                        "old_db": old_db,
                        "reason": f"数据库改名失败：{exc}",
                    }
                )
                logger.exception("[identity] %s：数据库改名异常", cfg.name)
                continue

            # ── 3) 先迁移以 agent_id 为键的本地数据（可重复执行），最后再改配置文件名 ──
            if old_id != name:
                try:
                    _migrate_local_data(config_dir, old_id, name)
                except Exception:  # noqa: BLE001
                    # 关联数据（草稿/快照/隐藏列表）改名失败不阻断配置改名：
                    # 旧键仍在磁盘上，不会丢数据；下一次启动会再尝试（幂等）。
                    logger.exception("[identity] 迁移本地数据失败（agent_id=%s）", old_id)

            try:
                _rewrite_config(config_store, cfg, name)
            except Exception as exc:  # noqa: BLE001
                # 数据库已经改名成功、配置却没落盘：下次启动会走「新库已存在」的补做路径。
                pending.append(
                    {
                        "agent_id": old_id,
                        "name": cfg.name,
                        "server": server,
                        "old_db": old_db,
                        "reason": f"数据库已改名，但配置文件写入失败：{exc}（下次启动会重试）",
                    }
                )
                logger.exception("[identity] 配置文件改名失败（agent_id=%s）", old_id)
                continue

            record = {
                "at": _now(),
                "server": server,
                "old_id": old_id,
                "new_id": name,
                "old_db": old_db,
                "new_db": name,
                "db_action": db_action,
            }
            state["renames"].append(record)
            renamed.append(record)
            logger.info(
                "[identity] 服务器 %s：数据库 %s → %s（%s）；agent_id %s → %s",
                server,
                old_db,
                name,
                db_action,
                old_id,
                name,
            )

        # 去重 rename 记录（同一对 old→new 只留最后一次），并只保留最近 500 条
        dedup: dict[tuple, dict] = {}
        for rec in state["renames"]:
            key = (rec.get("server"), rec.get("old_id"), rec.get("new_id"), rec.get("old_db"))
            dedup[key] = rec
        state["renames"] = list(dedup.values())[-500:]
        state["pending"] = pending
        _save_state(config_dir, state)

        return {
            "renamed": renamed,
            "pending": pending,
            "servers": servers,
        }


def run_for_all_sync(config_store: ConfigStore) -> dict:
    """给后台线程用的同步入口（Windows 下沿用 SelectorEventLoop 策略）。"""
    return asyncio.run(run_for_all(config_store))
