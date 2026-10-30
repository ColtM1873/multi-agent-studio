"""离线验证按会话隔离的浏览器 profile / 端口 / 暂停 / 标签页上限 / 配色。

不启动真实浏览器：只验证 manager 的实例划分、状态逻辑、重启后发现存活
profile，以及「存活浏览器继续占用自己的边框色」。
运行：venv\\Scripts\\python.exe scripts\\verify_browser_sessions.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
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
                staticmethod(lambda p: p in {12341, 12342, 12343, 12351}),
            ):
                store = Store(base)
                (store._dir / "settings.json").write_text(
                    json.dumps(
                        {
                            "browser_color_presets": [
                                {"border_color": "#ff0000", "text_color": "#ffffff"},
                                {"border_color": "#00aa00", "text_color": "#111111"},
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
                manager = bs.BrowserSessionManager(store)
                a1 = manager.get("A", "会话一")
                a2 = manager.get("A", "会话二")
                b1 = manager.get("B", "会话一")
                assert len({a1.profile_dir, a2.profile_dir, b1.profile_dir}) == 3
                assert Path(a1.profile_dir).is_dir()
                assert a1.controller is not a2.controller
                assert a1.controller.tab_limit_override == 3
                assert b1.controller.tab_limit_override == 10

                # 按列表顺序取色，列表用完后随机且不与已占用的边框色重复。
                t1 = manager._allocate_theme(a1)
                t2 = manager._allocate_theme(a2)
                t3 = manager._allocate_theme(b1)
                assert t1 == {"border_color": "#ff0000", "text_color": "#ffffff"}
                assert t2 == {"border_color": "#00aa00", "text_color": "#111111"}
                assert t3["border_color"] not in {"#ff0000", "#00aa00"}
                assert t3["text_color"].startswith("#")

                a1.controller.launcher.port = 12341
                a2.controller.launcher.port = 12342
                b1.controller.launcher.port = 12343
                a1._probe_at = a2._probe_at = b1._probe_at = 0.0
                s1 = manager.status("A", "会话一")
                s2 = manager.status("A", "会话二")
                assert s1["connected"] and s2["connected"]
                assert s1["port"] != s2["port"]
                assert a1.theme == t1 and a2.theme == t2

                # 会话列表着色数据：只返回仍连接、已有配色的会话。
                by_thread = {
                    item["thread_id"]: item
                    for item in manager.browser_theme_entries()
                    if item["agent_id"] == "A"
                }
                assert by_thread["会话一"]["border_color"] == t1["border_color"]
                assert by_thread["会话二"]["border_color"] == t2["border_color"]

                # 程序重启边界：profile 映射里的浏览器仍在监听时，新 manager
                # 不必先进入会话，也能发现它并按原颜色给会话列表着色。
                (Path(a1.profile_dir) / "DevToolsActivePort").write_text(
                    "12341\n", encoding="utf-8"
                )
                manager2 = bs.BrowserSessionManager(store)
                manager2._discover_live_profiles()
                restarted = {
                    item["thread_id"]: item
                    for item in manager2.browser_theme_entries()
                    if item["agent_id"] == "A"
                }
                assert restarted["会话一"]["border_color"] == t1["border_color"]
                # 重启后新开的会话不能拿到存活浏览器仍占用的边框色。
                a3 = manager2.get("A", "会话三")
                assert manager2._allocate_theme(a3)["border_color"] != t1["border_color"]
                manager2.shutdown()
                (Path(a1.profile_dir) / "DevToolsActivePort").unlink()

                # 启动期保护：还没连上的会话在 30 秒内仍占色，超过才视为失败释放。
                launching = bs.BrowserSession(
                    key=("A", "启动中"),
                    agent_id="A",
                    thread_id="启动中",
                    agent_name="A",
                    profile_dir=a1.profile_dir,
                    controller=a1.controller,
                    theme={"border_color": "#123456", "text_color": "#ffffff"},
                    theme_at=time.monotonic(),
                )
                launching._disconnect_count = 5  # 启动期探测失败多次也不能提前释放
                assert "#123456" in manager._used_borders([launching])
                launching.theme_at -= 31.0
                assert "#123456" not in manager._used_borders([launching])

                # 浏览器进程关闭 → 它占的颜色立即回到程序的颜色池：
                # 下一个启动的浏览器可以按预设列表重新拿到它。
                a2.controller.launcher.port = 19999
                a2._probe_at = 0.0
                a2._disconnect_count = 1
                closed2 = manager.status("A", "会话二")
                assert not closed2["connected"] and a2.theme is None
                fresh = bs.BrowserSession(
                    key=("A", "新会话"),
                    agent_id="A",
                    thread_id="新会话",
                    agent_name="A",
                    profile_dir=a2.profile_dir,
                    controller=a2.controller,
                )
                # 此刻 a1 占 #ff0000、b1 占随机色，a2 的 #00aa00 已收回。
                assert manager._allocate_theme(fresh)["border_color"] == "#00aa00"

                assert manager.toggle_pause("A", "会话一") is True
                assert manager.status("A", "会话一")["paused"] is True
                assert manager.status("A", "会话二")["paused"] is False
                assert manager.toggle_pause("A", "会话二") is True
                assert manager.status("A", "会话一")["paused"] is True
                assert manager.status("A", "会话二")["paused"] is True

                # 再关一个浏览器：连接状态为假、暂停复位、颜色收回。
                a1.controller.launcher.port = 19999
                a1._probe_at = 0.0
                a1.theme_at -= 31.0  # 越过启动宽限期
                a1._disconnect_count = 1  # 连续两次判离线才收回
                closed = manager.status("A", "会话一")
                assert not closed["connected"] and closed["paused"] is False
                assert a1.theme is None
                assert not any(
                    item["agent_id"] == "A" and item["thread_id"] == "会话一"
                    for item in manager.browser_theme_entries()
                )
                assert manager._allocate_theme(a1)["border_color"] == "#ff0000"

                # ---- profile 池：删除会话 = 释放（不关浏览器、不删目录） ----
                # 造一个「浏览器进程还活着」的会话：删掉它之后，颜色必须仍然
                # 被程序占用（否则另一个窗口会领到同一个颜色）。
                survivor = manager.get("B", "存活会话")
                survivor_dir = survivor.profile_dir
                survivor_theme = manager._allocate_theme(survivor)
                survivor.controller.launcher.port = 12351
                survivor._probe_at = 0.0
                # 真实浏览器会把端口写进 profile；复用时靠这个文件找回进程。
                (Path(survivor_dir) / "DevToolsActivePort").write_text(
                    "12351\n", encoding="utf-8"
                )
                assert manager.status("B", "存活会话")["connected"] is True
                saved_controller = survivor.controller  # 只做「没被关闭」的事实引用

                assert manager.release("B", "存活会话") is True
                assert manager.peek("B", "存活会话") is None
                assert Path(survivor_dir).is_dir()          # 目录没删
                assert saved_controller is not None         # 浏览器没被关闭
                freed_item = manager._profiles[manager._profile_key("B", "存活会话")]
                assert freed_item.get("released") is True
                assert freed_item.get("theme") == survivor_theme

                # 进程还活着 → 颜色仍被占用；同时下一个会话会认领这条 profile，
                # 并继承它背后仍存活进程的颜色（你说的 a→b→c→d→e）。
                adopter = manager.get("A", "复用会话")
                assert adopter.profile_dir == survivor_dir
                assert adopter.theme == survivor_theme
                assert manager._profile_key("B", "存活会话") not in manager._profiles
                assert manager._profile_key("A", "复用会话") in manager._profiles

                # 池子空了以后再新建一个 profile：它不能拿走仍被占用的颜色。
                other = manager.get("B", "另一个新会话")
                assert (
                    manager._allocate_theme(other)["border_color"]
                    != survivor_theme["border_color"]
                )

                # 这个浏览器一关，颜色就收回；下一次启动重新按预设列表分配。
                adopter.controller.launcher.port = 19999
                (Path(adopter.profile_dir) / "DevToolsActivePort").unlink(
                    missing_ok=True
                )
                adopter._probe_at = 0.0
                adopter._disconnect_count = 1
                assert manager.status("A", "复用会话")["connected"] is False
                assert adopter.theme is None
                recycled = bs.BrowserSession(
                    key=("A", "回收后新开"),
                    agent_id="A",
                    thread_id="回收后新开",
                    agent_name="A",
                    profile_dir=adopter.profile_dir,
                    controller=adopter.controller,
                )
                assert (
                    manager._allocate_theme(recycled)["border_color"]
                    == survivor_theme["border_color"]
                )

                # 同名会话重建：认领自己释放的 profile（进程已关，颜色重新分配）
                manager.release("A", "复用会话")
                same_name = manager.get("A", "复用会话")
                assert same_name.profile_dir == survivor_dir
                assert same_name.theme is None

                # 删除 multi-agent：名下 profile 全部释放，内存会话一并摘掉
                released_count = manager.release_agent("B")
                assert released_count >= 1
                assert all(
                    item.get("released")
                    for pkey, item in manager._profiles.items()
                    if pkey.startswith("B" + "\u241f")
                )
                assert not [s for s in manager.sessions() if s.agent_id == "B"]

                # ---- FIFO：最早释放的 profile 先被认领 ----
                pool_store = Store(base / "pool")
                pool = bs.BrowserSessionManager(pool_store)
                q1 = pool.get("A", "池一")
                pool._allocate_theme(q1)
                q2 = pool.get("A", "池二")
                pool._allocate_theme(q2)
                dir1, dir2 = q1.profile_dir, q2.profile_dir
                pool.release("A", "池一")
                pool.release("A", "池二")
                pool._profiles[pool._profile_key("A", "池一")]["released_at"] = (
                    "2026-01-01 00:00:00"
                )
                pool._profiles[pool._profile_key("A", "池二")]["released_at"] = (
                    "2026-01-01 00:00:01"
                )
                pool._save_profiles()
                # 换一个 manager 模拟程序重启：池子从磁盘恢复，FIFO 仍然成立。
                pool2 = bs.BrowserSessionManager(pool_store)
                first = pool2.get("B", "认领一")
                assert first.profile_dir == dir1
                second = pool2.get("B", "认领二")
                assert second.profile_dir == dir2
                pool.shutdown()
                pool2.shutdown()

                manager.shutdown()
        finally:
            bs.ROOT = old_root
    print("verify_browser_sessions: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
