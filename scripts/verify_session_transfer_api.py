"""会话导出 / 导入 **API 层**的离线验证（不连数据库、不弹窗、不读真实配置）。

用假的 `app.deps` / `app.api.agents` 和临时 `configs/` 目录，直接调
`app.api.session_transfer` 的四个端点（导出 / 选文件 / 迁移计划 / 执行），覆盖：

- 未加密导出 → 加密导出（短密码、密码为空报错）；
- 加密文件自动识别、错误密码给人话错误；
- 迁移计划：本机配置逐条给「能否迁入 + 原因」、同名配置标记、建议改名；
- 执行：新建配置、复用同名且匹配的配置、迁入已有配置、会话重名自动加后缀；
- 不匹配的配置必须被拒绝，并提示改名 / 另选方式；
- 总开关关闭时导出与导入都拒绝。

用法（项目根目录）：

    venv\\Scripts\\python.exe scripts\\verify_session_transfer_api.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config.models import MultiAgentConfig  # noqa: E402
from app.config.settings import Settings, save_settings  # noqa: E402

# ── 先放好假依赖，再导入被测模块（避免拉起 torch / 连真实数据库）──────────


class FakeConfigStore:
    """最小 ConfigStore：只用内存字典 + 一个临时目录（组件会读 settings.json）。"""

    def __init__(self, base_dir: Path):
        self._dir = base_dir / "configs"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._configs: dict[str, MultiAgentConfig] = {}

    def list(self):
        return list(self._configs.values())

    def load(self, agent_id: str):
        if agent_id not in self._configs:
            raise FileNotFoundError(agent_id)
        return self._configs[agent_id]

    def save(self, cfg: MultiAgentConfig):
        self._configs[cfg.agent_id] = cfg


class FakeChatManager:
    """只实现端点用到的几个方法；存的内存里的 dict 模拟会话库。"""

    # 让 _is_lite() 为真 → 走「不碰 PostgreSQL」的分支
    __module__ = "lite.app.chat_lite"

    def __init__(self):
        self.threads: dict[str, dict[str, dict]] = {}

    async def list_threads(self, agent_id: str):
        return [{"thread_id": tid, "checkpoints": 1, "last_updated": ""} for tid in self.threads.get(agent_id, {})]

    async def export_thread_checkpoints(self, agent_id: str, thread_id: str):
        payload = self.threads.get(agent_id, {}).get(thread_id)
        if payload is None:
            raise KeyError(thread_id)
        return payload

    async def import_thread_checkpoints(self, agent_id: str, thread_id: str, payload: dict):
        self.threads.setdefault(agent_id, {})[thread_id] = payload
        return sorted(payload.get("namespaces") or {})

    async def register_thread(self, agent_id: str, thread_id: str):
        self.threads.setdefault(agent_id, {}).setdefault(thread_id, {"thread_id": thread_id, "namespaces": {}})

    async def invalidate(self, agent_id: str):
        return None

    async def invalidate_all(self):
        return None

    async def main_thread_messages(self, agent_id: str, thread_id: str):
        return []

    async def thread_subgraphs(self, agent_id: str, thread_id: str):
        return []

    async def subgraph_messages_exact(self, agent_id: str, thread_id: str, node_name: str):
        return []


def install_fakes(tmp: Path):
    """在 sys.modules 里塞假 app.deps / app.api.agents，再导入被测路由。"""
    fake_store = FakeConfigStore(tmp)
    fake_chat = FakeChatManager()

    deps = types.ModuleType("app.deps")
    deps.config_store = fake_store
    deps.chat_manager = fake_chat
    deps.BASE_DIR = tmp
    sys.modules["app.deps"] = deps

    async def _ensure_agent_databases(cfg):  # 测试里绝不允许连数据库
        return None

    def _ensure_name_is_free(name):  # 简化版：只在本测试的假仓库里查重
        for cfg in fake_store.list():
            if cfg.name.casefold() == str(name).casefold():
                from fastapi import HTTPException

                raise HTTPException(status_code=400, detail=f"名称「{name}」已存在")

    agents = types.ModuleType("app.api.agents")
    agents._ensure_agent_databases = _ensure_agent_databases
    agents._ensure_name_is_free = _ensure_name_is_free
    agents._is_sqlite_storage = lambda: True
    sys.modules["app.api.agents"] = agents

    from app.api import session_transfer

    app = FastAPI()
    app.include_router(session_transfer.router)
    return app, fake_store, fake_chat, session_transfer


# ── 造配置 / 会话数据 ───────────────────────────────────────────────


def make_config(name: str, sub_agents: list[str]) -> MultiAgentConfig:
    return MultiAgentConfig.model_validate(
        {
            "agent_id": name,
            "name": name,
            "postgres": {
                "prefix": "postgresql://u:p@127.0.0.1:5432/",
                "suffix": "?sslmode=disable",
                "checkpoint_database": name,
                "store_namespace": ["Li", "memories"],
            },
            "main_agent": {"system_prompt": "sp", "api_key": "sk-main"},
            "sub_agents": [
                {"name": sub, "description": "d", "system_prompt": "s", "api_key": f"sk-{sub}"}
                for sub in sub_agents
            ],
        }
    )


def make_payload(thread_id: str, ns: list[str]) -> dict:
    from app.services import thread_transfer

    namespaces = {}
    for name in ns:
        checkpoint = {
            "v": 4,
            "id": "00000000-0000-6000-8000-000000000001",
            "ts": "2026-11-07T10:00:00+00:00",
            "channel_values": {"messages": []},
            "channel_versions": {"messages": "00000000000000000000000000000001.0.1"},
            "versions_seen": {},
        }
        ctype, cdata = thread_transfer.serde.dumps_typed(checkpoint)
        namespaces[name] = {
            "checkpoint_id": checkpoint["id"],
            "checkpoint_type": ctype,
            "checkpoint": thread_transfer._b64e(cdata),
            "metadata": {"source": "loop", "step": 0},
            "pending_writes": [],
        }
    return {"thread_id": thread_id, "namespaces": namespaces}


def seed_state(fake_store: FakeConfigStore, fake_chat: FakeChatManager) -> dict:
    """造一份可导出的源状态：一个带 worker 子 agent 的配置 + 一条会话。"""
    src = make_config("源配置", ["worker"])
    fake_store.save(src)
    payload = make_payload("源会话", ["", "worker"])
    fake_chat.threads[src.agent_id] = {"源会话": payload}
    return {"agent_id": src.agent_id, "payload": payload}


def build_export_file(tmp: Path, sess, password: str = "") -> Path:
    """直接用归档层造一个导出文件（端点只负责把它读回来）。"""
    from app.services import session_archive

    cfg = make_config("源配置", ["worker"])
    zip_bytes = session_archive.build_archive(
        agent_config=cfg.model_dump(),
        manifest={"app": {"edition": "lite", "storage": "sqlite"}},
        sessions=[
            {
                "thread_id": "源会话",
                "name": "源会话",
                "message_count": 2,
                "last_updated": "2026-11-07 18:00",
                "payload": sess["payload"],
                "conversation": "# 主 agent 历史\n\n你好\n",
            }
        ],
    )
    if password:
        out = tmp / "export.zip.enc"
        out.write_bytes(session_archive.encrypt_archive(zip_bytes, password))
    else:
        out = tmp / "export.zip"
        out.write_bytes(zip_bytes)
    return out


# ── 校验 ───────────────────────────────────────────────────────────


def check(client: TestClient, tmp: Path, fake_store, fake_chat, st, session_transfer) -> None:
    from app.services import session_archive, win_file_dialog

    export_target = tmp / "out.zip"
    export_target_enc = tmp / "out.zip.enc"
    # 桩掉原生对话框（真实环境会阻塞等用户点）
    win_file_dialog.save_file_dialog = lambda **kwargs: str(export_target)
    win_file_dialog.open_file_dialog = lambda **kwargs: str(build_export_file(tmp, st))

    # 1) 未加密导出
    r = client.post(
        "/api/session-export",
        json={"agent_id": st["agent_id"], "thread_ids": ["源会话"], "title": "导出"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["canceled"] is False and body["encrypted"] is False
    assert Path(body["path"]) == export_target and export_target.exists()
    assert export_target.read_bytes().startswith(b"PK"), "未加密导出应是普通 zip"
    archive = session_archive.read_archive(export_target.read_bytes())
    assert archive["agent_config"]["name"] == "源配置"
    assert archive["manifest"]["agent"]["sub_agents"][0]["name"] == "worker"
    assert "主 agent 历史" not in (archive["sessions"][0]["conversation"] or ""), "fake 渲染应为空"
    print("  1. 未加密导出：OK")

    # 2) 加密导出：没填密码要被人话拒绝
    save_settings(
        fake_store._dir,
        Settings(export_session_enabled=True, export_session_encrypt=True, export_session_password=""),
    )
    r = client.post(
        "/api/session-export",
        json={"agent_id": st["agent_id"], "thread_ids": ["源会话"]},
    )
    assert r.status_code == 400 and "密码" in r.json()["detail"], r.text
    print("  2. 加密导出缺密码：OK（人话报错）")

    # 3) 加密导出成功（短密码 → 自动补前缀）
    save_settings(
        fake_store._dir,
        Settings(export_session_enabled=True, export_session_encrypt=True, export_session_password="abc123"),
    )
    win_file_dialog.save_file_dialog = lambda **kwargs: str(export_target_enc)
    r = client.post(
        "/api/session-export",
        json={"agent_id": st["agent_id"], "thread_ids": ["源会话"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["encrypted"] is True and Path(r.json()["path"]).suffix == ".enc"
    assert session_archive.look_encrypted(export_target_enc), "导出的加密文件缺少魔数"
    assert not export_target_enc.read_bytes().startswith(b"PK")
    print("  3. 加密导出（短密码）：OK")

    # 4) 选文件端点自动识别「是否加密」
    enc_file = build_export_file(tmp, st, password="abc123")
    win_file_dialog.open_file_dialog = lambda **kwargs: str(enc_file)
    r = client.post("/api/session-import/select", json={"title": "选文件"})
    assert r.status_code == 200 and r.json()["encrypted"] is True, r.text
    print("  4. 选择文件 + 自动识别加密：OK")

    # 5) 错误密码 → 人话错误；正确密码 → 迁移计划
    r = client.post("/api/session-import/plan", json={"path": str(enc_file), "password": "wrong"})
    assert r.status_code == 400 and "密码错误" in r.json()["detail"], r.text
    r = client.post("/api/session-import/plan", json={"path": str(enc_file), "password": "abc123"})
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["exported_agent"]["name"] == "源配置"
    assert plan["exported_agent"]["sub_agents"] == ["worker"]
    assert len(plan["sessions"]) == 1 and plan["sessions"][0]["thread_id"] == "源会话"
    same = next(c for c in plan["local_configs"] if c["name"] == "源配置")
    assert same["compatible"] is True, same
    assert plan["same_name_config"] and plan["same_name_config"]["name"] == "源配置"
    print("  5. 加密文件迁移计划：OK")

    # 6) 「连同原配置一并导入」= 总是新建配置：同名（即使结构匹配）也必须换名
    r = client.post(
        "/api/session-import/apply",
        json={"path": str(enc_file), "password": "abc123", "mode": "with_config", "new_agent_name": "源配置"},
    )
    assert r.status_code == 409, r.text
    assert "加载到本机已有的 multi-agent 配置" in r.json()["detail"], r.text
    r = client.post(
        "/api/session-import/apply",
        json={"path": str(enc_file), "password": "abc123", "mode": "with_config",
              "new_agent_name": "源配置 (导入)"},
    )
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["created_config"] is True and res["agent_name"] == "源配置 (导入)", res
    assert res["imported"] == 1
    assert "源会话" in fake_chat.threads["源配置 (导入)"], "会话没有落进新建的配置"
    assert "源配置" in fake_store._configs and fake_store._configs["源配置"].name == "源配置"
    print("  6. 「连同原配置一并导入」总是新建、同名要求换名：OK")

    # 7) 迁入已有配置时重名 → 自动加后缀
    r = client.post(
        "/api/session-import/apply",
        json={"path": str(enc_file), "password": "abc123", "mode": "existing", "target_agent_id": "源配置"},
    )
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["imported"] == 1 and res["results"][0]["renamed"] is True, res
    assert "源会话 (导入)" in fake_chat.threads["源配置"]
    print("  7. 会话重名自动加后缀：OK")

    # 8) 不兼容的本机配置：必须拒绝并给原因
    broken = make_config("没有子agent的配置", [])
    fake_store.save(broken)
    r = client.post(
        "/api/session-import/plan", json={"path": str(enc_file), "password": "abc123"}
    )
    row = next(c for c in r.json()["local_configs"] if c["name"] == "没有子agent的配置")
    assert row["compatible"] is False and "worker" in row["reason"], row
    r = client.post(
        "/api/session-import/apply",
        json={"path": str(enc_file), "password": "abc123", "mode": "existing",
              "target_agent_id": "没有子agent的配置"},
    )
    assert r.status_code == 400 and "无法迁入" in r.json()["detail"], r.text
    print("  8. 不兼容配置被拒：OK")

    # 9) 同名但不匹配 + 选「连同原配置一并导入」→ 说明不匹配并要求换名（409）
    conflict_cfg = make_config("冲突配置", ["docs"])
    fake_chat.threads[conflict_cfg.agent_id] = {"冲突会话": make_payload("冲突会话", ["", "docs"])}
    fake_store.save(conflict_cfg)
    conflict_file = tmp / "conflict.zip"
    from app.services import session_archive

    conflict_file.write_bytes(
        session_archive.build_archive(
            agent_config=make_config("冲突配置", ["worker"]).model_dump(),
            manifest={},
            sessions=[
                {
                    "thread_id": "冲突会话",
                    "name": "冲突会话",
                    "message_count": 1,
                    "last_updated": "",
                    "payload": make_payload("冲突会话", ["", "worker"]),
                    "conversation": "",
                }
            ],
        )
    )
    r = client.post("/api/session-import/plan", json={"path": str(conflict_file), "password": ""})
    plan2 = r.json()
    assert plan2["same_name_config"] and plan2["same_name_config"]["compatible"] is False, plan2
    assert plan2["suggested_name"] != "冲突配置", plan2["suggested_name"]
    assert "导入" in plan2["suggested_name"]
    r = client.post(
        "/api/session-import/apply",
        json={"path": str(conflict_file), "password": "", "mode": "with_config", "new_agent_name": "冲突配置"},
    )
    assert r.status_code == 409 and "不重名的名称" in r.json()["detail"], r.text
    assert "不匹配" in r.json()["detail"], r.text
    # 改名后可以新建（本机已有配置的名称不动）
    r = client.post(
        "/api/session-import/apply",
        json={"path": str(conflict_file), "password": "", "mode": "with_config", "new_agent_name": plan2["suggested_name"]},
    )
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["created_config"] is True and res["imported"] == 1, res
    assert res["agent_name"] == plan2["suggested_name"]
    assert any("不存在" in w for w in res["warnings"]) is False  # fake 配置没有路径字段
    print("  9. 同名不匹配 → 说明不匹配并要求换名，换名后可导入：OK")

    # 10) 总开关关闭：导出与导入都拒绝
    save_settings(fake_store._dir, Settings(export_session_enabled=False))
    r = client.post(
        "/api/session-export",
        json={"agent_id": st["agent_id"], "thread_ids": ["源会话"]},
    )
    assert r.status_code == 400 and "未开启" in r.json()["detail"], r.text
    r = client.post("/api/session-import/select", json={})
    assert r.status_code == 400 and "未开启" in r.json()["detail"], r.text
    print(" 10. 总开关关闭时拒绝导出/导入：OK")

    # 11) 取消对话框：返回 canceled，不建文件
    save_settings(fake_store._dir, Settings(export_session_enabled=True))
    win_file_dialog.save_file_dialog = lambda **kwargs: None
    r = client.post(
        "/api/session-export",
        json={"agent_id": st["agent_id"], "thread_ids": ["源会话"]},
    )
    assert r.status_code == 200 and r.json()["canceled"] is True, r.text
    win_file_dialog.open_file_dialog = lambda **kwargs: None
    r = client.post("/api/session-import/select", json={})
    assert r.status_code == 200 and r.json()["canceled"] is True, r.text
    print(" 11. 取消对话框：OK")

    # 12) 空会话（没有 checkpoint）会被跳过而不是让整次导出失败
    fake_chat.threads[st["agent_id"]]["空会话"] = {"thread_id": "空会话", "namespaces": {}}
    win_file_dialog.save_file_dialog = lambda **kwargs: str(tmp / "skip.zip")
    r = client.post(
        "/api/session-export",
        json={"agent_id": st["agent_id"], "thread_ids": ["源会话", "空会话"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["skipped"] == ["空会话"], r.json()
    print(" 12. 空会话跳过：OK")


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="masession_api_"))
    try:
        app, fake_store, fake_chat, session_transfer = install_fakes(tmp)
        st = seed_state(fake_store, fake_chat)
        with TestClient(app) as client:  # 这个 app 没有 lifespan 副作用
            check(client, tmp, fake_store, fake_chat, st, session_transfer)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("OK：会话导出/导入 API 层 12 项全部通过")


if __name__ == "__main__":
    main()
