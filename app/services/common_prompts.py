"""会话常用 prompt（每个 thread 一条）：落盘到 configs/common_prompts.json。

设计要点：
- 「每个会话维持一条」= 每个 (agent_id, thread_id) 一条。**thread_id 由用户命名、
  并非全局唯一**（不同 multi-agent 可以起同名会话），所以键必须是 agent_id + thread_id 两级，
  不能只用 thread_id。
- 全局常用 prompt 库不放这里：它是全局设置，存在 `configs/settings.json`
  的 `Settings.common_prompts`（见 `app/config/settings.py`）。
- 写穿（write-through）：每次 set 立即落盘，退出托盘程序也不会丢。
- 「开启常用prompt注入功能」总开关只控制前端按钮显示，**不删除任何数据**：
  关掉再打开，全局库与各会话的选择都还在。
- 删除会话时不清理这里的记录（需求：后端数据一律保留）；同名会话重建时会沿用旧选择。
- configs/*.json 已在 .gitignore 中忽略。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

COMMON_PROMPTS_FILE_NAME = "common_prompts.json"


class CommonPromptStore:
    """按 (agent_id, thread_id) 保存「本会话选定的常用 prompt」的落盘存储。"""

    def __init__(self, config_dir: Path | str):
        self._dir = Path(config_dir)
        self._path = self._dir / COMMON_PROMPTS_FILE_NAME
        self._lock = threading.RLock()
        self._data: dict[str, dict[str, str]] = self._load()

    # ── 内部 ──────────────────────────────────────────────
    def _load(self) -> dict[str, dict[str, str]]:
        try:
            if self._path.exists():
                obj = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(obj, dict):
                    out: dict[str, dict[str, str]] = {}
                    for agent_id, threads in obj.items():
                        if not isinstance(threads, dict):
                            continue
                        clean = {
                            str(tid): str(text)
                            for tid, text in threads.items()
                            if isinstance(text, str) and text
                        }
                        if clean:
                            out[str(agent_id)] = clean
                    return out
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
    def get(self, agent_id: str, thread_id: str) -> str:
        with self._lock:
            return self._data.get(str(agent_id), {}).get(str(thread_id), "")

    def set(self, agent_id: str, thread_id: str, text: str) -> None:
        """写入该会话的常用 prompt；空字符串即「清空（不设置）」。"""
        agent_id, thread_id = str(agent_id), str(thread_id)
        text = text or ""
        with self._lock:
            threads = self._data.get(agent_id)
            if text:
                if threads is None:
                    threads = {}
                    self._data[agent_id] = threads
                threads[thread_id] = text
            elif threads is not None:
                threads.pop(thread_id, None)
                if not threads:
                    self._data.pop(agent_id, None)
            self._persist_locked()

    def flush(self) -> None:
        """把当前内存态再落盘一次（退出托盘程序时的兜底）。"""
        with self._lock:
            self._persist_locked()
