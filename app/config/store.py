"""配置持久化：configs/<agent_id>.json 读写 + 默认配置模板。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.config.models import (
    EmbeddingConfig,
    FileToolsConfig,
    MainAgentConfig,
    MultiAgentConfig,
    PostgresConfig,
    SubAgentConfig,
    SubSummaryConfig,
    SummaryConfig,
)

CONFIG_DIR_NAME = "configs"
DEFAULT_CONFIG_NAME = "default.json"

# configs/ 下非 multi-agent 配置的内部文件（名称已禁止用作 agent 名称）。
# 列在这里可以避免每次 list() 都去尝试解析它们。
_NON_AGENT_STEMS = frozenset(
    {
        "default",
        "settings",
        "ui_prefs",
        "drafts",
        "common_prompts",
        "store_migrations",
        "identity_migration",
        "floating_stop_pos",
    }
)


def config_dir(base_dir: Path | str) -> Path:
    return Path(base_dir) / CONFIG_DIR_NAME


def slugify(name: str) -> str:
    """把子 agent 名转成合法的消息通道键名片段。"""
    s = re.sub(r"[^0-9a-zA-Z_]+", "_", name.strip()).strip("_").lower()
    return s or "agent"


def build_default_config() -> MultiAgentConfig:
    """系统内置的合理默认配置，供"新建向导"预填。"""
    return MultiAgentConfig(
        agent_id="",
        name="",
        postgres=PostgresConfig(
            prefix="",
            suffix="?sslmode=disable",
            store_database="Long_Term_Memory",
            checkpoint_database="",  # 必须由用户填写（绑定历史）
            store_namespace=("Li", "memories"),
        ),
        main_agent=MainAgentConfig(
            system_prompt="",
            api_key="",
            llm_provider_name="deepseek:deepseek-v4-pro",
            file_tools=FileToolsConfig(),
            embedding=EmbeddingConfig(),
            summary=SummaryConfig(),
        ),
        sub_agents=[],
    )


class ConfigStore:
    """多 agent 配置仓库。线程安全不做要求（单进程 GUI 后端）。"""

    def __init__(self, base_dir: Path | str):
        self.base_dir = Path(base_dir)
        self._dir = config_dir(self.base_dir)

    def ensure_dir(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, agent_id: str) -> Path:
        return self._dir / f"{agent_id}.json"

    def save(self, config: MultiAgentConfig) -> None:
        self.ensure_dir()
        self._path(config.agent_id).write_text(
            json.dumps(config.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self, agent_id: str) -> MultiAgentConfig:
        path = self._path(agent_id)
        if not path.exists():
            raise FileNotFoundError(f"配置不存在: {agent_id}")
        return MultiAgentConfig.model_validate_json(path.read_text(encoding="utf-8"))

    def delete(self, agent_id: str) -> None:
        path = self._path(agent_id)
        if path.exists():
            path.unlink()

    def list(self) -> list[MultiAgentConfig]:
        self.ensure_dir()
        configs = []
        for p in sorted(self._dir.glob("*.json")):
            if p.stem in _NON_AGENT_STEMS:
                continue
            try:
                configs.append(MultiAgentConfig.model_validate_json(p.read_text(encoding="utf-8")))
            except Exception:
                # 跳过坏配置，避免一个坏文件拖垮整个列表
                continue
        return configs

    # ── 默认配置 ────────────────────────────────────────────────
    def default_path(self) -> Path:
        return self._dir / DEFAULT_CONFIG_NAME

    def save_default(self, config: MultiAgentConfig) -> None:
        self.ensure_dir()
        self.default_path().write_text(
            json.dumps(config.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load_default(self) -> MultiAgentConfig:
        path = self.default_path()
        if not path.exists():
            return build_default_config()
        return MultiAgentConfig.model_validate_json(path.read_text(encoding="utf-8"))


def sync_store_database(store: ConfigStore, database: str) -> int:
    """把所有 multi-agent 配置里的 store 数据库名同步为全局值。

    返回被改写的配置数量。旧版配置里的 store_database 是「每个 agent 各自一个库」，
    现在统一使用 Settings.global_store_database；只要发现不一致就写回，
    保证落盘配置与运行时实际连接的库一致。
    """
    changed = 0
    for cfg in store.list():
        if cfg.postgres.store_database == database:
            continue
        cfg.postgres.store_database = database
        try:
            store.save(cfg)
            changed += 1
        except Exception:  # noqa: BLE001 - 单个坏配置不应阻断其它配置的同步
            continue
    return changed
