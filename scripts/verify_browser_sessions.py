"""离线验证按会话隔离的浏览器 profile / 端口 / 暂停 / 标签页上限。

不启动真实浏览器：只验证 manager 的实例划分与状态逻辑。
运行：venv\\Scripts\\python.exe scripts\\verify_browser_sessions.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from browser_agent.launcher import BrowserLauncher  # noqa: E402
from app.runtime import browser_sessions as bs  # noqa: E402


class Cfg:
    def __init__(self, name, tab_max):
        self.name = name
        self.main_agent = type(
            "Main", (), {"browser_takeover": True, "browser_tab_max_number": tab_max}
        )()


class Store:
    def __init__(self, base: Path):
        self._dir = base / "configs"
        self._dir.mkdir(parents=True, exist_ok=True)

    def load(self, agent_id):
        if agent_id == "A":
            return Cfg("写作助手", 3)
        if agent_id == "B":
            return Cfg("研究助手", 10)
        raise FileNotFoundError(agent_id)


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        old_root = bs.ROOT
        bs.ROOT = base
        try:
            with patch.object(
                BrowserLauncher,
                "_is_port_ready",
                staticmethod(lambda p: p in {12341, 12342, 12343}),
            ):
                manager = bs.BrowserSessionManager(Store(base))
                a1 = manager.get("A", "会话一")
                a2 = manager.get("A", "会话二")
                b1 = manager.get("B", "会话一")
                assert len({a1.profile_dir, a2.profile_dir, b1.profile_dir}) == 3
                assert Path(a1.profile_dir).is_dir()
                assert a1.controller is not a2.controller
                assert a1.controller.tab_limit_override == 3
                assert b1.controller.tab_limit_override == 10

                a1.controller.launcher.port = 12341
                a2.controller.launcher.port = 12342
                b1.controller.launcher.port = 12343
                a1._probe_at = a2._probe_at = b1._probe_at = 0.0
                s1 = manager.status("A", "会话一")
                s2 = manager.status("A", "会话二")
                assert s1["connected"] and s2["connected"]
                assert s1["port"] != s2["port"]

                assert manager.toggle_pause("A", "会话一") is True
                assert manager.status("A", "会话一")["paused"] is True
                assert manager.status("A", "会话二")["paused"] is False
                assert manager.toggle_pause("A", "会话二") is True
                assert manager.status("A", "会话一")["paused"] is True
                assert manager.status("A", "会话二")["paused"] is True

                # 浏览器关闭：连接状态为假，且该实例暂停复位。
                a1.controller.launcher.port = 19999
                a1._probe_at = 0.0
                closed = manager.status("A", "会话一")
                assert not closed["connected"] and closed["paused"] is False
                manager.shutdown()
        finally:
            bs.ROOT = old_root
    print("verify_browser_sessions: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
