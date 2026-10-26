"""应用单例依赖。"""

from __future__ import annotations

from pathlib import Path

from app.config.store import ConfigStore, config_dir
from app.services.chat import ChatManager
from app.services.common_prompts import CommonPromptStore
from app.services.drafts import DraftStore
from app.services.session_hub import SessionHub
from app.services.ui_prefs import UiPrefsStore
from app.runtime.browser_sessions import BrowserSessionManager

BASE_DIR = Path(__file__).resolve().parent.parent

config_store = ConfigStore(BASE_DIR)
config_store.ensure_dir()

chat_manager = ChatManager(config_store)

# ID91：运行所有权从 WebSocket 连接移到会话中枢；浏览器按会话隔离。
session_hub = SessionHub(chat_manager)
browser_session_manager = BrowserSessionManager(config_store)

draft_store = DraftStore(config_dir(BASE_DIR))

common_prompt_store = CommonPromptStore(config_dir(BASE_DIR))

ui_prefs_store = UiPrefsStore(config_dir(BASE_DIR))
