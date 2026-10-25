"""会话常用 prompt（每个 thread 一条）：落盘到 configs/common_prompts.json。

设计要点：
- **指针语义**：本文件里存的是「全局常用 prompt 库中某条条目的 id」（指针），不是文本副本。
  全局库是 `configs/settings.json` 的 `Settings.common_prompts`（每条 `{id, text}`）。
  因此在主界面编辑某条常用 prompt 后，所有指向它的会话会**一起变**（读取时实时解析）；
  该条被删除后，指向它的会话自动回到「未设置」。
- 老数据兼容：旧版本这里存的是文本副本。启动时 `migrate_legacy_session_refs` 做一次性迁移：
  能在全局库里按文本匹配到条目的改成指针；匹配不到的把它补进全局库再指向它（内容不丢）。
- 「每个会话维持一条」= 每个 (agent_id, thread_id) 一条。**thread_id 由用户命名、
  并非全局唯一**（不同 multi-agent 可以起同名会话），所以键必须是 agent_id + thread_id 两级，
  不能只用 thread_id。
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

from app.config.settings import (
    CommonPrompt,
    Settings,
    load_settings,
    make_common_prompt_id,
    save_settings,
)

COMMON_PROMPTS_FILE_NAME = "common_prompts.json"


class CommonPromptStore:
    """按 (agent_id, thread_id) 保存「本会话指向的全局常用 prompt 条目 id」的落盘存储。"""

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
        """返回该会话指向的全局常用 prompt 条目 id（未设置返回空串）。"""
        with self._lock:
            return self._data.get(str(agent_id), {}).get(str(thread_id), "")

    def set(self, agent_id: str, thread_id: str, ref: str) -> None:
        """写入该会话指向的条目 id；空字符串即「清空（不设置）」。"""
        agent_id, thread_id = str(agent_id), str(thread_id)
        ref = ref or ""
        with self._lock:
            threads = self._data.get(agent_id)
            if ref:
                if threads is None:
                    threads = {}
                    self._data[agent_id] = threads
                threads[thread_id] = ref
            elif threads is not None:
                threads.pop(thread_id, None)
                if not threads:
                    self._data.pop(agent_id, None)
            self._persist_locked()

    def items(self) -> list[tuple[str, str, str]]:
        """列出全部 (agent_id, thread_id, ref)（用于启动时的老数据迁移）。"""
        with self._lock:
            return [
                (agent_id, thread_id, ref)
                for agent_id, threads in self._data.items()
                for thread_id, ref in threads.items()
            ]

    def flush(self) -> None:
        """把当前内存态再落盘一次（退出托盘程序时的兜底）。"""
        with self._lock:
            self._persist_locked()

    def reload(self) -> None:
        """从磁盘重新读取（身份迁移改了 agent_id 键之后必须调用，避免内存里还是旧键）。"""
        with self._lock:
            self._data = self._load()


# ── 指针解析 / 老数据迁移（模块级纯函数，便于离线探针） ──────────────────
def find_prompt_by_id(prompts: list[CommonPrompt], ref: str) -> CommonPrompt | None:
    """按 id 找全局库条目；找不到（例如该条已被删除）返回 None。"""
    if not ref:
        return None
    for p in prompts or []:
        if p.id == ref:
            return p
    return None


def find_prompt_by_text(prompts: list[CommonPrompt], text: str) -> CommonPrompt | None:
    """按内容（去首尾空白）找全局库条目；用于老数据迁移。"""
    key = (text or "").strip()
    if not key:
        return None
    for p in prompts or []:
        if (p.text or "").strip() == key:
            return p
    return None


def resolve_prompt_text(prompts: list[CommonPrompt], ref: str) -> str:
    """把会话保存的指针解析成当前文本；指针失效（条目被删）时返回空串 = 未设置。"""
    entry = find_prompt_by_id(prompts, ref)
    return entry.text if entry else ""


def migrate_legacy_session_refs(config_dir: Path | str, store: CommonPromptStore) -> int:
    """把旧版「存文本副本」的会话记录升级成「存全局库条目 id（指针）」。

    - 能按文本匹配到全局库条目的：直接改成指向它；
    - 匹配不到的（用户先设了会话、后来又改了全局库内容）：把旧文本补进全局库再指向它，
      保证用户已有的内容不丢（与旧版「直接输入会自动进全局库」的语义一致）。
    - 幂等：已经是合法 id 的记录不动；没有任何旧数据时不写盘。
    返回迁移的记录数。
    """
    settings: Settings = load_settings(config_dir)
    prompts: list[CommonPrompt] = list(settings.common_prompts)
    ids: set[str] = {p.id for p in prompts}
    legacy = [(a, t, v) for a, t, v in store.items() if v and v not in ids]
    if not legacy:
        return 0

    updates: list[tuple[str, str, str]] = []
    settings_changed = False
    for agent_id, thread_id, value in legacy:
        entry = find_prompt_by_text(prompts, value)
        if entry is None:
            entry = CommonPrompt(id=make_common_prompt_id(value, ids), text=value)
            prompts.append(entry)
            ids.add(entry.id)
            settings_changed = True
        updates.append((agent_id, thread_id, entry.id))

    # 先保证全局库里真有这些条目，再写指针（避免指针指向不存在的条目）
    if settings_changed:
        settings.common_prompts = prompts
        save_settings(config_dir, settings)
    for agent_id, thread_id, ref in updates:
        store.set(agent_id, thread_id, ref)
    return len(updates)
