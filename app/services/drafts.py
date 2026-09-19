"""未发送消息缓存（draft）：每个 multi-agent 一条，落盘到 configs/drafts.json。

设计要点：
- 一个 multi-agent 只维护一条（不区分其内部不同会话），键为 agent_id。
- 写穿（write-through）：每次 set 都立即落盘，因此即使直接退出托盘程序也不会丢。
- 「跨 multi-agent 流转」由上层决定是否把同一个文本写到多个 agent 的缓存里，
  这里仍按 per-agent 存储，不做「单一全局缓存」的简化。
- configs/*.json 已在 .gitignore 中忽略，缓存内容不会被提交。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

DRAFTS_FILE_NAME = "drafts.json"


class DraftStore:
    """按 agent_id 保存「已输入未发送」文本的落盘缓存。"""

    def __init__(self, config_dir: Path | str):
        self._dir = Path(config_dir)
        self._path = self._dir / DRAFTS_FILE_NAME
        self._lock = threading.RLock()
        self._data: dict[str, str] = self._load()

    # ── 内部 ──────────────────────────────────────────────
    def _load(self) -> dict[str, str]:
        try:
            if self._path.exists():
                obj = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(obj, dict):
                    return {str(k): str(v) for k, v in obj.items() if isinstance(v, str)}
        except Exception:
            # 坏文件不应阻断功能，降级为空
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

    # ── 对外 ──────────────────────────────────────────────
    def get(self, agent_id: str) -> str:
        with self._lock:
            return self._data.get(agent_id, "")

    def set(self, agent_id: str, text: str) -> None:
        with self._lock:
            if text:
                self._data[agent_id] = text
            else:
                self._data.pop(agent_id, None)
            self._persist_locked()

    def set_many(self, agent_ids, text: str) -> None:
        with self._lock:
            for agent_id in agent_ids:
                if not agent_id:
                    continue
                if text:
                    self._data[agent_id] = text
                else:
                    self._data.pop(agent_id, None)
            self._persist_locked()

    def flush(self) -> None:
        """把当前内存态再落盘一次（退出托盘程序时的兜底）。"""
        with self._lock:
            self._persist_locked()
