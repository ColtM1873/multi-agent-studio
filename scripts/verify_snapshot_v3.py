"""离线验证快照格式 v3（只存消息 + 浏览时按当前设置实时渲染）。

验证内容：
1. v3 快照（只存 main_messages / subgraphs[].messages）打开时用**当前设置**渲染，
   聊天图标/称谓改动后立即生效（这就是「阅览快照时 labels 同步为当前设置」的实现方式）；
2. 打开快照不写盘（JSON 保持原样）；
3. 快照内编辑只改消息 JSON，不重渲染、不留 main/markdown/render_settings；
4. v1 旧快照（只有渲染后的 Markdown）原样返回、不可编辑；
5. v2 旧快照（Markdown + 消息 + render_settings）同样按消息实时渲染，返回里不带 render_settings；
6. capture_snapshot 落盘格式为 v3（无 main / 无 render_settings）。

运行：
    venv\\Scripts\\python.exe scripts\\verify_snapshot_v3.py
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402

from app.config.settings import Settings, save_settings  # noqa: E402
from app.services import snapshot  # noqa: E402
from app.services.messages_json import message_to_dict  # noqa: E402

WORK = ROOT / "tmp_snapshot_v3_test"
if WORK.exists():
    shutil.rmtree(WORK)
WORK.mkdir(parents=True)
SNAPS = WORK / "snapshots"
CFG = WORK / "configs"
SNAPS.mkdir()
CFG.mkdir()

_orig_snaps = snapshot.SNAPSHOTS_DIR
_orig_cfg = snapshot.CONFIG_DIR
_orig_collect = snapshot._collect_snapshot_data
snapshot.SNAPSHOTS_DIR = SNAPS
snapshot.CONFIG_DIR = CFG

AGENT = "TestAgent"
AGENT_DIR = snapshot._agent_snapshots_dir(AGENT)
AGENT_DIR.mkdir(parents=True, exist_ok=True)


def write_record(sid: str, record: dict) -> Path:
    p = AGENT_DIR / sid
    p.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return p


def base_messages() -> list[dict]:
    return [
        message_to_dict(HumanMessage(content="你好，世界", id="h1")),
        message_to_dict(AIMessage(content="## 回复\n\n这是**答复**。", id="a1")),
    ]


def v3_record() -> dict:
    return {
        "agent_id": AGENT,
        "thread_id": "thread-1",
        "created_at": "2026-01-01 00:00:00",
        "total_messages": 2,
        "format_version": 3,
        "main_messages": base_messages(),
        "main_meta": {
            "ts": "2026-01-01T00:00:00+00:00",
            "source": "loop",
            "step": 3,
            "thread_id": "thread-1",
            "checkpoint_id": "cp-1",
        },
        "subgraphs": [
            {"name": "SubA", "messages": [message_to_dict(AIMessage(content="子 agent 输出", id="s1"))]}
        ],
    }


try:
    # --- 1. 默认设置：用默认图标/称谓实时渲染 ---
    save_settings(CFG, Settings())
    sid = "thread-1_20260101_000000_000000_2.json"
    p = write_record(sid, v3_record())
    before = p.read_text(encoding="utf-8")
    rec = snapshot.read_snapshot(AGENT, sid)
    assert rec is not None
    assert rec["editable"] is True
    assert "main_messages" not in rec, "默认不应返回消息数组"
    assert "render_settings" not in rec
    assert "你好，世界" in rec["main"]
    assert "🧑" in rec["main"] and "🤖" in rec["main"], rec["main"][:400]
    assert rec["subgraphs"][0]["markdown"].startswith("## 子图 [SubA] 历史"), rec["subgraphs"][0]["markdown"][:80]
    assert "子 agent 输出" in rec["subgraphs"][0]["markdown"]
    assert p.read_text(encoding="utf-8") == before, "打开快照不应写盘"
    print("[1] v3 按当前设置实时渲染 OK")

    # --- 2. 改「聊天图标及称谓」后再打开：外观立即跟随当前设置 ---
    s = Settings()
    s.chat_user_icon = "🦊"
    s.chat_user_name = "老板"
    s.chat_assistant_icon = "🐼"
    s.chat_assistant_name = "小助手"
    s.chat_reasoning_icon = "💭"
    save_settings(CFG, s)
    rec2 = snapshot.read_snapshot(AGENT, sid)
    assert "🦊" in rec2["main"] and "老板" in rec2["main"], rec2["main"][:400]
    assert "🐼" in rec2["main"] and "小助手" in rec2["main"], rec2["main"][:400]
    assert "🧑" not in rec2["main"] and "🤖" not in rec2["main"]
    assert p.read_text(encoding="utf-8") == before, "打开快照不应写盘（labels 已由实时渲染解决）"
    print("[2] 图标/称谓跟随当前设置 OK")

    # --- 3. 快照内编辑：只改消息 JSON，不重渲染、不留缓存字段 ---
    new_human = {"type": "HumanMessage", "content": "改过的内容", "id": "h1"}
    meta = snapshot.edit_snapshot_message(AGENT, sid, None, 0, new_human)
    assert meta["editable"] is True
    saved = json.loads(p.read_text(encoding="utf-8"))
    assert "main" not in saved and "render_settings" not in saved, saved.keys()
    assert "markdown" not in saved["subgraphs"][0], saved["subgraphs"][0].keys()
    assert saved["format_version"] == snapshot.SNAPSHOT_FORMAT_VERSION == 3
    assert saved["main_messages"][0]["content"] == "改过的内容"
    rec3 = snapshot.read_snapshot(AGENT, sid)
    assert "改过的内容" in rec3["main"] and "你好，世界" not in rec3["main"]
    print("[3] 编辑只改消息 JSON + 读回实时渲染 OK")

    # --- 4. v1 旧快照：原样返回、不可编辑 ---
    v1 = {
        "agent_id": AGENT,
        "thread_id": "old-thread",
        "created_at": "2025-01-01 00:00:00",
        "total_messages": 2,
        "main": "## 📋 旧存档\n\n旧内容（旧图标 🧑）",
        "subgraphs": [{"name": "SubOld", "markdown": "## 旧子图\n\n旧内容"}],
    }
    sid1 = "old-thread_20250101_000000_000000_2.json"
    write_record(sid1, v1)
    recv1 = snapshot.read_snapshot(AGENT, sid1)
    assert recv1["editable"] is False
    assert recv1["main"] == "## 📋 旧存档\n\n旧内容（旧图标 🧑）"
    assert recv1["subgraphs"][0]["markdown"] == "## 旧子图\n\n旧内容"
    print("[4] v1 旧快照原样返回 OK")

    # --- 5. v2 旧快照：有消息 → 同样实时渲染，不再返回 render_settings ---
    v2 = v3_record()
    v2["format_version"] = 2
    v2["main"] = "## 过时的缓存 Markdown"
    v2["render_settings"] = {"labels": {"user_icon": "🦊"}}
    v2["subgraphs"][0]["markdown"] = "## 过时的子图缓存"
    sid2 = "thread-2_20260101_000000_000000_2.json"
    write_record(sid2, v2)
    recv2 = snapshot.read_snapshot(AGENT, sid2)
    assert "render_settings" not in recv2
    assert "过时的缓存 Markdown" not in recv2["main"]
    assert "🐼" in recv2["main"], recv2["main"][:400]
    assert "过时的子图缓存" not in recv2["subgraphs"][0]["markdown"]
    print("[5] v2 旧快照按消息实时渲染 OK")

    # --- 5b. 混合格式（主图无消息但有旧 markdown，子图有消息）：主图保留旧缓存 ---
    mixed = v3_record()
    mixed["main_messages"] = []
    mixed["main"] = "## 旧主图缓存\n\n保留我"
    mix_sid = "thread-3_20260101_000000_000000_2.json"
    write_record(mix_sid, mixed)
    recmix = snapshot.read_snapshot(AGENT, mix_sid)
    assert recmix["main"] == "## 旧主图缓存\n\n保留我", recmix["main"]
    assert "子 agent 输出" in recmix["subgraphs"][0]["markdown"]
    print("[5b] 混合格式：无消息通道回退旧缓存 OK")

    # --- 6. capture_snapshot 落盘格式为 v3 ---
    async def fake_collect(conn_string, thread_id):
        return {
            "subgraphs": [{"name": "SubA", "messages": [message_to_dict(AIMessage(content="子输出"))]}],
            "total_messages": 3,
            "main_messages": base_messages(),
            "main_meta": v3_record()["main_meta"],
        }

    snapshot._collect_snapshot_data = fake_collect
    out = asyncio.run(snapshot.capture_snapshot("conn", "new-thread", AGENT))
    assert out is not None, out
    saved2 = json.loads((AGENT_DIR / out["id"]).read_text(encoding="utf-8"))
    assert saved2["format_version"] == 3
    assert "main" not in saved2 and "render_settings" not in saved2
    assert saved2["main_messages"] and saved2["main_meta"]
    assert set(saved2["subgraphs"][0].keys()) == {"name", "messages"}, saved2["subgraphs"][0].keys()
    assert saved2["total_messages"] == 3
    print("[6] capture_snapshot 落盘 v3 格式 OK ->", out["id"])

    # --- 7. 打开刚 capture 的快照：实时渲染正常 ---
    rec4 = snapshot.read_snapshot(AGENT, out["id"])
    assert "你好，世界" in rec4["main"] and "子输出" in rec4["subgraphs"][0]["markdown"]
    print("[7] capture 后可正常浏览 OK")
finally:
    snapshot.SNAPSHOTS_DIR = _orig_snaps
    snapshot.CONFIG_DIR = _orig_cfg
    snapshot._collect_snapshot_data = _orig_collect
    shutil.rmtree(WORK, ignore_errors=True)

print("ALL OK")
