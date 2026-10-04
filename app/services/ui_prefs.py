"""本机界面偏好（曾存浏览器 localStorage）的落盘存储：configs/ui_prefs.json。

背景：隐藏的 multi-agent / 会话、卡片外观等偏好原本存在浏览器 localStorage，
换渲染外壳（浏览器 → WebView2）会因数据目录不同而全部丢失。改为后端落盘后，
数据与外壳无关，换壳 / 清缓存都不会再丢。

键值为任意字符串，键名沿用前端既有的 localStorage 键（如 `hidden_agents`、
`hidden_threads_<agentId>`、`card-bg`、`lang`）。写穿（write-through），
configs/*.json 已在 .gitignore 中忽略。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

UI_PREFS_FILE_NAME = "ui_prefs.json"


class UiPrefsStore:
    """一个扁平的 key→string 偏好表，落盘为单个 JSON 文件。"""

    def __init__(self, config_dir: Path | str):
        self._dir = Path(config_dir)
        self._path = self._dir / UI_PREFS_FILE_NAME
        self._lock = threading.RLock()
        self._data: dict[str, str] = self._load()

    def _load(self) -> dict[str, str]:
        try:
            if self._path.exists():
                obj = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(obj, dict):
                    return {str(k): str(v) for k, v in obj.items() if v is not None}
        except Exception:
            pass
        return {}

    def _persist_locked(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self._path)

    def all(self) -> dict[str, str]:
        with self._lock:
            return dict(self._data)

    def apply(self, updates: dict) -> None:
        """增量写入：值 None 表示删除该键。"""
        with self._lock:
            changed = False
            for key, value in updates.items():
                key = str(key)
                if value is None:
                    if key in self._data:
                        self._data.pop(key, None)
                        changed = True
                else:
                    text = str(value)
                    if self._data.get(key) != text:
                        self._data[key] = text
                        changed = True
            if changed:
                self._persist_locked()

    def replace(self, data: dict) -> None:
        """整体替换（用于一次性导入旧浏览器 localStorage）。"""
        with self._lock:
            self._data = {str(k): str(v) for k, v in data.items() if v is not None}
            self._persist_locked()
