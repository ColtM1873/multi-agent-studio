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
    memory_attach: bool = True
    num_memories_attached: int = 5
    warn_unsaved_changes: bool = True
    notification_sound: str = "ber"
    send_key: str = "enter"
    newline_key: str = "shift_enter"
    show_placeholders: bool = True
    # 裸公式识别：无分隔符公式的启发式渲染，默认关闭以避开日常场景误判
    bare_math_detect: bool = False
    # 历史浏览时各板块默认展开/折叠
    reasoning_expanded: bool = True
    tool_call_expanded: bool = False
    tool_result_expanded: bool = False
    # HTML 导出（md2print）：默认开启；输出路径为空时需先在设置里填写
    export_html: bool = True
    export_html_path: str = ""
    export_html_config: dict = {}
    # MD 导出：默认开启；输出路径为空时需先在设置里填写
    export_md: bool = True
    export_md_path: str = ""
    # 历史消息编辑：总开关（默认开启）
    edit_mode_enabled: bool = True
    # 历史消息编辑：是否允许编辑任意久远的历史消息（默认只编辑最新一轮对话）
    edit_any_history: bool = False
    # 历史消息编辑：是否允许编辑所有消息类型（默认只编辑 AI 回复正文 text）
    edit_all_message_types: bool = False


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
