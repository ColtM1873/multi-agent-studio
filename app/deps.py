"""应用单例依赖。"""

from __future__ import annotations

from pathlib import Path

from app.config.store import ConfigStore, config_dir
from app.services.chat import ChatManager
from app.services.common_prompts import CommonPromptStore
from app.services.drafts import DraftStore
from app.services.ui_prefs import UiPrefsStore

BASE_DIR = Path(__file__).resolve().parent.parent

config_store = ConfigStore(BASE_DIR)
config_store.ensure_dir()

chat_manager = ChatManager(config_store)

draft_store = DraftStore(config_dir(BASE_DIR))

common_prompt_store = CommonPromptStore(config_dir(BASE_DIR))

ui_prefs_store = UiPrefsStore(config_dir(BASE_DIR))
