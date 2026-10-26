"""离线验证聊天 WebSocket 的 attach / 回放 / 断连继续 / 验收。

使用真实 ``app/api/chat_ws.py`` + 真实 ``SessionHub``，只把 runtime/DB 换成 fake。
运行：venv\\Scripts\\python.exe scripts\\verify_chat_ws.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.api.chat_ws as chat_ws  # noqa: E402
import app.api.sessions as sessions_api  # noqa: E402
from app.runtime.streaming import RunResult  # noqa: E402
from app.services.session_hub import SessionHub  # noqa: E402


class FakeMainAgent:
    file_tools = type("FT", (), {"root_dir": str(ROOT)})()


class FakeConfig:
    name = "测试 Agent"
    sub_agents: list = []
    main_agent = FakeMainAgent()


class FakeRuntime:
    def __init__(self, delay=0.02):
        self.config = FakeConfig()
        self.delay = delay

    async def run(self, thread_id, user_input, emit, on_interrupt, control=None, echo_user=True):
        await emit({"type": "text", "source": "main_user", "text": "hi"})
        for _ in range(3):
            await asyncio.sleep(self.delay)
            if control is not None and control.drain_requested:
                return RunResult("", drained=True)
        await emit({"type": "text", "source": "main", "text": "hello"})
        return RunResult("hello", drained=False)

    async def repair_tool_results(self, thread_id):
        return []


class FakeChatManager:
    def __init__(self, runtime):
        self.runtime = runtime

    def has_runtime(self, agent_id):
        return True

    async def get_runtime(self, agent_id):
        return self.runtime

    async def thread_history(self, agent_id, thread_id):
        return "# fake history"


def build_app(hub):
    app = FastAPI()
    app.include_router(chat_ws.router)
    app.include_router(sessions_api.router)
    chat_ws.session_hub = hub
    sessions_api.session_hub = hub
    return app


def recv_until(ws, wanted, timeout=10):
    seen = []
    for _ in range(100):
        msg = ws.receive_json()
        seen.append(msg.get("type"))
        if msg.get("type") in wanted:
            return msg, seen
    raise AssertionError(f"not found {wanted}: {seen}")


def main() -> int:
    runtime = FakeRuntime()
    manager = FakeChatManager(runtime)
    hub = SessionHub(manager)
    chat_ws.chat_manager = manager
    app = build_app(hub)

    with TestClient(app) as client:
        with client.websocket_connect("/api/agents/a/threads/t/chat") as ws:
            assert ws.receive_json()["type"] == "status"
            assert ws.receive_json()["type"] == "sub_agents"
            ws.send_json({"type": "attach", "run_id": "", "since": 0})
            assert ws.receive_json()["type"] == "run_state"
            assert ws.receive_json()["type"] == "replay_done"
            ws.send_json({"type": "send", "content": "hi"})
            done, seen = recv_until(ws, {"done"})
            assert done["stopped"] is False
            state, _ = recv_until(ws, {"run_state"})
            assert state["status"] == "completed"
            assert hub.get_record("a", "t").accepted is False

        listing = client.get("/api/sessions").json()
        assert listing["counts"] == {"running": 0, "completed": 1}, listing
        assert listing["sessions"][0]["thread_id"] == "t"

        # 断线后重新 attach：completed 只发最终状态，不重放事件
        with client.websocket_connect("/api/agents/a/threads/t/chat") as ws:
            ws.receive_json()
            ws.receive_json()
            ws.send_json({"type": "attach", "run_id": "", "since": 0})
            state = ws.receive_json()
            assert state["type"] == "run_state" and state["status"] == "completed"
            replay = ws.receive_json()
            assert replay["type"] == "replay_done"

        r = client.post("/api/sessions/a/threads/t/accept")
        assert r.json()["ok"] is True
        assert hub.get_record("a", "t").status == "idle"

        # 运行中断线：hub 不取消运行
        runtime2 = FakeRuntime(delay=0.08)
        manager2 = FakeChatManager(runtime2)
        hub2 = SessionHub(manager2)
        chat_ws.chat_manager = manager2
        app2 = build_app(hub2)
        with TestClient(app2) as client2:
            with client2.websocket_connect("/api/agents/b/threads/t/chat") as ws:
                ws.receive_json(); ws.receive_json()
                ws.send_json({"type": "attach", "run_id": "", "since": 0})
                ws.receive_json(); ws.receive_json()
                ws.send_json({"type": "send", "content": "long"})
                recv_until(ws, {"run_state"})
            assert hub2.get_record("b", "t").status in ("running", "completed")
            import time
            time.sleep(0.5)
            assert hub2.get_record("b", "t").status == "completed", hub2.get_record("b", "t").status

    print("verify_chat_ws: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
