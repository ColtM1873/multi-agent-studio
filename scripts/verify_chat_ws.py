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


class FakeConfigStore:
    """握手失败时 chat_ws 会退而读配置拿子 agent 名字（不建图），这里给个桩。"""

    def load(self, agent_id):
        return FakeConfig()


class FakeChatManager:
    def __init__(self, runtime, *, runtime_error=None, has_runtime=True):
        self.runtime = runtime
        self.runtime_error = runtime_error
        self._has_runtime = has_runtime
        self.config_store = FakeConfigStore()

    def has_runtime(self, agent_id):
        return self._has_runtime

    async def get_runtime(self, agent_id):
        if self.runtime_error is not None:
            raise self.runtime_error
        return self.runtime

    async def get_local_runtime(self, agent_id):
        # 编辑历史消息 / 删除全部历史走这条（不连接 MCP）。
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

        # MCP 连不上：握手必须照样成功（不再同步建图）；普通发送要把真正原因说清楚；
        # 编辑历史消息走 get_local_runtime，照样能跑完。
        mcp_error = RuntimeError(
            "子 agent [web_search_agent] 的 MCP 服务器连接失败：[web_search (http)]，"
            "请确认这些服务已启动且鉴权配置正确。原始错误: ConnectError: 连接失败"
        )
        runtime3 = FakeRuntime(delay=0.01)
        manager3 = FakeChatManager(runtime3, runtime_error=mcp_error, has_runtime=False)
        hub3 = SessionHub(manager3)
        chat_ws.chat_manager = manager3
        app3 = build_app(hub3)
        with TestClient(app3) as client3:
            with client3.websocket_connect("/api/agents/c/threads/t/chat") as ws:
                # 运行时未构建：握手只发 sub_agents（不阻塞、不发 error、不断开）
                assert ws.receive_json()["type"] == "sub_agents"
                ws.send_json({"type": "attach", "run_id": "", "since": 0})
                assert ws.receive_json()["type"] == "run_state"
                assert ws.receive_json()["type"] == "replay_done"
                # 普通发送：构建失败 → 收到带「真正原因」的 error，而不是笼统文案
                ws.send_json({"type": "send", "content": "hi"})
                err, _ = recv_until(ws, {"error"})
                assert "MCP 服务器连接失败" in err["message"], err
                assert "web_search" in err["message"], err
                assert "编辑模式" in err["message"], err
                # 编辑历史消息：走本地运行时，应正常 done（MCP 挂了也不受影响）
                ws.send_json(
                    {
                        "type": "edit",
                        "request": {
                            "request_for_subagent": False,
                            "msg_indice": 0,
                            "substitute_msg": {"type": "HumanMessage", "content": "改后"},
                        },
                    }
                )
                done, _ = recv_until(ws, {"done"})
                assert done.get("stopped") is False, done
            import time
            time.sleep(0.1)
            assert hub3.get_record("c", "t").end_reason == "done", hub3.get_record("c", "t")
        print("[OK] MCP 连不上：握手不阻塞、发送报真实原因、编辑照常完成")

    print("verify_chat_ws: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
