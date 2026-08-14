"""全局系统设置（区别于每个 multi-agent 的配置）。

持久化在 configs/settings.json。记忆吸附（memory_attach）属于全局设置：
它影响图编译，因此在进入某个 agent 后（图已编译）不允许改动，需退回主界面。
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

SETTINGS_FILE_NAME = "settings.json"


class Settings(BaseModel):
    memory_attach: bool = False
    num_memories_attached: int = 3
    warn_unsaved_changes: bool = True
    notification_sound: str = "ber"
    send_key: str = "enter"
    newline_key: str = "shift_enter"
    show_placeholders: bool = True


def settings_path(config_dir: Path | str) -> Path:
    return Path(config_dir) / SETTINGS_FILE_NAME


def load_settings(config_dir: Path | str) -> Settings:
    path = settings_path(config_dir)
    if not path.exists():
        return Settings()
    try:
        return Settings.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return Settings()


def save_settings(config_dir: Path | str, settings: Settings) -> None:
    d = Path(config_dir)
    d.mkdir(parents=True, exist_ok=True)
    settings_path(d).write_text(
        json.dumps(settings.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
