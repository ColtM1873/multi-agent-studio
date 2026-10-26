"""离线验证 SessionHub 的并发/回放/停止/验收语义（不连库、不调模型）。

运行：venv\\Scripts\\python.exe scripts\\verify_session_hub.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from langgraph.runtime import RunControl  # noqa: E402

from app.runtime.streaming import RunResult  # noqa: E402
from app.services.session_hub import SessionHub  # noqa: E402


class FakeMainAgent:
    file_tools = type("FT", (), {"root_dir": str(ROOT)})()


class FakeConfig:
    name = "测试 Agent"
    sub_agents: list = []
    main_agent = FakeMainAgent()


class FakeRuntime:
    def __init__(self, *, delay: float = 0.05, text: str = "hello"):
        self.config = FakeConfig()
        self.delay = delay
        self.text = text

    async def run(self, thread_id, user_input, emit, on_interrupt, control=None, echo_user=True):
        await emit({"type": "text", "source": "main_user", "text": "hi"})
        if control is not None and not isinstance(control, RunControl):
            raise AssertionError("control 类型错误")
        for _ in range(5):
            await asyncio.sleep(self.delay)
            if control is not None and control.drain_requested:
                return RunResult("", drained=True)
        await emit({"type": "text", "source": "main", "text": self.text})
        return RunResult(self.text, drained=False)

    async def repair_tool_results(self, thread_id):
        return []


class FakeChatManager:
    def __init__(self, runtime):
        self.runtime = runtime

    async def get_runtime(self, agent_id):
        return self.runtime

    async def thread_history(self, agent_id, thread_id):
        return "# 历史基线"


def drain_queue(q) -> list[dict]:
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except asyncio.QueueEmpty:
            return out


async def wait_for(predicate, timeout=3.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return False


async def main() -> int:
    runtime = FakeRuntime()
    hub = SessionHub(FakeChatManager(runtime))

    sid, q = await hub.attach("a", "t", "", 0)
    initial = drain_queue(q)
    assert initial[0]["type"] == "run_state" and initial[0]["status"] == "idle"
    assert any(e["type"] == "replay_done" for e in initial)

    ok, err = await hub.start("a", "t", content="hi")
    assert ok, err
    assert await wait_for(lambda: hub.get_record("a", "t").status == "completed")
    events = drain_queue(q)
    types = [e.get("type") for e in events]
    assert "done" in types, types
    assert "text" in types, types
    assert [e.get("seq") for e in events] == list(range(1, len(events) + 1))
    assert hub.get_record("a", "t").accepted is False
    assert hub.counts() == {"running": 0, "completed": 1}

    # 断线重连：completed 只发最终历史，不重放事件（避免历史+事件重复）
    sid2, q2 = await hub.attach("a", "t", "", 0)
    replay = drain_queue(q2)
    assert replay[0]["type"] == "run_state"
    assert replay[0]["status"] == "completed"
    assert not replay[0].get("history_markdown")
    assert not any(e.get("type") == "done" for e in replay)

    assert await hub.accept("a", "t") is True
    assert hub.list_pending() == []
    assert hub.get_record("a", "t").status == "idle"

    # 停止：stopping 先出现，done 后才 completed
    runtime2 = FakeRuntime(delay=0.08)
    hub2 = SessionHub(FakeChatManager(runtime2))
    sid3, q3 = await hub2.attach("b", "t", "", 0)
    drain_queue(q3)
    ok, err = await hub2.start("b", "t", content="hi")
    assert ok, err
    await asyncio.sleep(0.01)
    assert await hub2.stop("b", "t") is True
    assert hub2.get_record("b", "t").status == "stopping"
    await wait_for(lambda: hub2.get_record("b", "t").status == "completed")
    stop_events = drain_queue(q3)
    tps = [e.get("type") for e in stop_events]
    assert "stopping" in tps, tps
    assert tps.index("stopping") < tps.index("done"), tps
    assert hub2.get_record("b", "t").end_reason == "stopped"

    # 同会话并发 start 被拒绝
    runtime3 = FakeRuntime(delay=0.2)
    hub3 = SessionHub(FakeChatManager(runtime3))
    await hub3.attach("c", "t", "", 0)
    ok, _ = await hub3.start("c", "t", content="x")
    assert ok
    ok2, err2 = await hub3.start("c", "t", content="y")
    assert not ok2 and "运行" in err2
    await hub3.shutdown()

    # 两个不同会话同时运行，互不覆盖
    runtime4 = FakeRuntime(delay=0.15, text="parallel")
    hub4 = SessionHub(FakeChatManager(runtime4))
    _, q4a = await hub4.attach("d", "a", "", 0)
    _, q4b = await hub4.attach("d", "b", "", 0)
    assert (await hub4.start("d", "a", content="a"))[0]
    assert (await hub4.start("d", "b", content="b"))[0]
    assert hub4.counts()["running"] == 2
    assert await wait_for(
        lambda: hub4.counts() == {"running": 0, "completed": 2}, timeout=5
    )
    for q in (q4a, q4b):
        events = drain_queue(q)
        texts = [e.get("text") for e in events if e.get("type") == "text" and e.get("source") == "main"]
        assert texts == ["parallel"], texts
    await hub4.shutdown()

    await hub.detach(sid)
    await hub.detach(sid2)
    await hub2.detach(sid3)
    await hub.shutdown()
    await hub2.shutdown()
    print("verify_session_hub: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
