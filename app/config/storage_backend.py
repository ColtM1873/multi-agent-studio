"""会话历史存储后端选择（仅 Lite 版安装时决定；普通版恒为 PostgreSQL）。

安装向导（``setup.ps1``）在 Lite 安装时把选择写到 ``configs/lite/storage.json``::

    {"backend": "sqlite"}    # 或 {"backend": "postgres"}

文件不存在、内容非法、或普通版安装 → 一律按 ``postgres`` 处理，保持既有行为。
SQLite 数据文件放在 ``configs/lite/sqlite/<agent>.sqlite3``（每 agent 一个），
不进入 ``ConfigStore.list()`` 的扫描范围（它只看 ``configs/*.json``）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

BACKEND_POSTGRES = "postgres"
BACKEND_SQLITE = "sqlite"

LITE_DIR_NAME = "lite"
STORAGE_FILE_NAME = "storage.json"
SQLITE_DIR_NAME = "sqlite"


def lite_data_dir(config_dir: Path | str) -> Path:
    """Lite 版数据目录（存储模式文件与 SQLite 文件都放这里）。"""
    return Path(config_dir) / LITE_DIR_NAME


def storage_path(config_dir: Path | str) -> Path:
    return lite_data_dir(config_dir) / STORAGE_FILE_NAME


def storage_backend(config_dir: Path | str) -> str:
    """当前生效的会话历史存储后端：``sqlite`` 或 ``postgres``（默认）。"""
    path = storage_path(config_dir)
    if not path.exists():
        return BACKEND_POSTGRES
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001 — 坏文件按默认处理，不影响启动
        return BACKEND_POSTGRES
    if not isinstance(data, dict):
        return BACKEND_POSTGRES
    value = str(data.get("backend") or "").strip().lower()
    return BACKEND_SQLITE if value == BACKEND_SQLITE else BACKEND_POSTGRES


def is_sqlite(config_dir: Path | str) -> bool:
    return storage_backend(config_dir) == BACKEND_SQLITE


def sqlite_dir(config_dir: Path | str) -> Path:
    return lite_data_dir(config_dir) / SQLITE_DIR_NAME


def sqlite_db_path(config_dir: Path | str, agent_id: str) -> Path:
    """agent 名 → SQLite 文件路径。

    名称在创建时已由 ``validate_agent_name`` 限制过（无 Windows 非法字符），这里再
    做一次保守清洗，避免手工改 JSON 或旧数据带出路径问题。中文保持原样（Windows
    与 SQLite 都支持）。
    """
    safe = re.sub(r"[^\w\- .]+", "_", str(agent_id), flags=re.UNICODE)
    safe = safe.strip(" .") or "agent"
    return sqlite_dir(config_dir) / f"{safe}.sqlite3"
