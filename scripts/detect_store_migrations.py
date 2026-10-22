"""检测旧版 store 数据库，记录迁移源（仅标准库，可在 setup 阶段直接运行）。

旧版本允许每个 multi-agent 使用不同的 store 数据库。新版本统一使用全局 store 数据库
（默认 Long_Term_Memory）。本脚本不迁移、不删库，只把「配置里出现的旧 store 库」
写入 configs/store_migrations.json，供程序启动后的迁移弹窗/设置页使用。

运行：
    python scripts/detect_store_migrations.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "configs"
SETTINGS_PATH = CONFIG_DIR / "settings.json"
STATE_PATH = CONFIG_DIR / "store_migrations.json"
DEFAULT_STORE_DATABASE = "Long_Term_Memory"
IGNORED = {
    "settings.json",
    "ui_prefs.json",
    "common_prompts.json",
    "drafts.json",
    "store_migrations.json",
    "identity_migration.json",
    "default.json",
}


def _source_id(prefix: str, suffix: str, database: str) -> str:
    raw = f"{prefix}|{suffix}|{database}".encode("utf-8", "replace")
    return hashlib.sha1(raw).hexdigest()[:12]


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def main() -> int:
    current = DEFAULT_STORE_DATABASE
    settings = _load_json(SETTINGS_PATH)
    name = str(settings.get("global_store_database") or "").strip()
    if name:
        current = name

    state = _load_json(STATE_PATH)
    sources = state.get("sources")
    if not isinstance(sources, list):
        sources = []
    state["sources"] = sources
    state.setdefault("prompted", False)

    changed = False
    added = 0
    for path in sorted(CONFIG_DIR.glob("*.json")):
        if path.name in IGNORED or path.stem == "default":
            continue
        data = _load_json(path)
        if not data.get("agent_id"):
            continue
        pg = data.get("postgres") or {}
        db = str(pg.get("store_database") or "").strip()
        if not db or db == current:
            continue
        prefix = str(pg.get("prefix") or "")
        suffix = str(pg.get("suffix") or "")
        sid = _source_id(prefix, suffix, db)
        src = next((s for s in sources if isinstance(s, dict) and s.get("id") == sid), None)
        if src is None:
            sources.append(
                {
                    "id": sid,
                    "database": db,
                    "prefix": prefix,
                    "suffix": suffix,
                    "agents": [str(data.get("agent_id"))],
                }
            )
            changed = True
            added += 1
        else:
            agents = src.get("agents")
            if not isinstance(agents, list):
                agents = []
            if data.get("agent_id") not in agents:
                agents.append(str(data.get("agent_id")))
                src["agents"] = agents
                changed = True

    if changed:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(f"store migration sources: {len(sources)} (added {added})")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"detect_store_migrations failed: {exc}")
        sys.exit(1)
