"""Lite 版应用单例依赖。"""

from __future__ import annotations

from pathlib import Path

from lite.app.bootstrap import install_persistence_stub

install_persistence_stub()

from app.config.store import ConfigStore, config_dir
from app.services.common_prompts import CommonPromptStore
from app.services.drafts import DraftStore
from app.services.ui_prefs import UiPrefsStore
from lite.app.chat_lite import LiteChatManager

BASE_DIR = Path(__file__).resolve().parents[2]

config_store = ConfigStore(BASE_DIR)
config_store.ensure_dir()

chat_manager = LiteChatManager(config_store)

draft_store = DraftStore(config_dir(BASE_DIR))

common_prompt_store = CommonPromptStore(config_dir(BASE_DIR))

ui_prefs_store = UiPrefsStore(config_dir(BASE_DIR))
