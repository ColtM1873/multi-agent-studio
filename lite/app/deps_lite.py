"""Lite 版应用单例依赖。

注意：普通路由（``app.api.chat_ws`` / ``app.api.sessions``）在模块顶层
``from app.deps import chat_manager, session_hub, browser_session_manager``，
因此这里必须提供与 ``app/deps.py`` 同名同类型的单例，否则 Lite 导入即失败。
"""

from __future__ import annotations

from pathlib import Path

from lite.app.bootstrap import install_persistence_stub

install_persistence_stub()

from app.config.store import ConfigStore, config_dir
from app.runtime.browser_sessions import BrowserSessionManager
from app.services.common_prompts import CommonPromptStore
from app.services.drafts import DraftStore
from app.services.session_hub import SessionHub
from app.services.ui_prefs import UiPrefsStore
from lite.app.chat_lite import LiteChatManager

BASE_DIR = Path(__file__).resolve().parents[2]

config_store = ConfigStore(BASE_DIR)
config_store.ensure_dir()

chat_manager = LiteChatManager(config_store)

# ID91：运行所有权在会话中枢；浏览器按会话隔离（Lite 保留浏览器接管与多实例会话）。
session_hub = SessionHub(chat_manager)
browser_session_manager = BrowserSessionManager(config_store)

draft_store = DraftStore(config_dir(BASE_DIR))

common_prompt_store = CommonPromptStore(config_dir(BASE_DIR))

ui_prefs_store = UiPrefsStore(config_dir(BASE_DIR))
