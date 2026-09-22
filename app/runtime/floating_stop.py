"""系统级置顶「停止 / 继续」浮动按钮。

为什么需要它：浏览器接管时用户常切到浏览器窗口观看 Agent 操作，网页内浮层会被切走。
这里用 ``tkinter`` 创建一个**无边框、始终置顶**的小窗口，跨应用可见（相当于桌面悬浮球）。

外观：用 ``-transparentcolor`` 把窗口底色抠成透明，再用 Canvas 画**圆角**的
红色/绿色「停止/继续」球体 + 一个**分离**的灰色拖动把手（两者之间留透明间隙）。

- 全局单例；在独立守护线程里跑 Tk ``mainloop``。
- 点击球体：翻转 ``browser_takeover.set_paused()``（红「停止」/ 绿「继续」）。
- 拖动把手 ``✥``：移动窗口，位置持久化到 ``configs/floating_stop_pos.json``。
- 每 250ms 轮询全局暂停标志并重新置顶，保证与后端状态一致、不被其它窗口盖住。
- 定时探测浏览器 CDP 端口：浏览器已关闭（端口无响应）时**自动隐藏**并复位暂停状态为「停止」，无需退出程序。

tkinter 缺失时静默降级（只记日志），不影响其它功能。
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

RED = "#dc2626"
GREEN = "#16a34a"
HANDLE_BG = "#3f3f46"
# 抠图色：窗口里这个颜色的像素会变透明（Windows 的 -transparentcolor）
KEY = "#ff00ff"

# 布局常量（像素）
BALL_W, BALL_H = 120, 72
BALL_R = 18
GAP = 14
HANDLE_SIZE = 36
HANDLE_R = 12
WIN_W = BALL_W + GAP + HANDLE_SIZE
WIN_H = BALL_H


def _round_rect(canvas, x1, y1, x2, y2, r, **kwargs):
    """在 Canvas 上画一个圆角矩形（用平滑多边形近似），返回 item id。"""
    points = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


class FloatingStop:
    _instance: Optional["FloatingStop"] = None

    @classmethod
    def instance(cls) -> "FloatingStop":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._q: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._started = threading.Event()
        self._name = "agent"
        self._pos_path: Optional[Path] = None
        self._available: Optional[bool] = None

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #
    def available(self) -> bool:
        if self._available is None:
            try:
                import tkinter  # noqa: F401

                self._available = True
            except Exception:  # noqa: BLE001
                self._available = False
        return self._available

    def show(self, name: str = "agent", pos_path: Optional[Path] = None) -> None:
        """显示（或更新名称后显示）系统级浮动按钮。"""
        if not self.available():
            logger.warning("tkinter 不可用，跳过系统级停止按钮")
            return
        if pos_path is not None:
            self._pos_path = Path(pos_path)
        self._name = (name or "agent").strip() or "agent"
        self._ensure_thread()
        self._q.put(("show", self._name))

    def hide(self) -> None:
        if self._thread is None:
            return
        self._q.put(("hide", None))

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #
    def _ensure_thread(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._started.clear()
        self._thread = threading.Thread(target=self._run, name="floating-stop", daemon=True)
        self._thread.start()
        self._started.wait(timeout=5.0)

    def _load_pos(self) -> Optional[tuple[int, int]]:
        try:
            if self._pos_path and self._pos_path.is_file():
                data = json.loads(self._pos_path.read_text(encoding="utf-8"))
                return int(data["x"]), int(data["y"])
        except Exception:  # noqa: BLE001
            pass
        return None

    def _save_pos(self, x: int, y: int) -> None:
        try:
            if self._pos_path:
                self._pos_path.parent.mkdir(parents=True, exist_ok=True)
                self._pos_path.write_text(
                    json.dumps({"x": int(x), "y": int(y)}), encoding="utf-8"
                )
        except Exception:  # noqa: BLE001
            pass

    def _run(self) -> None:  # noqa: C901
        try:
            import tkinter as tk
        except Exception:  # noqa: BLE001
            logger.exception("导入 tkinter 失败")
            self._started.set()
            return

        try:
            root = tk.Tk()
        except Exception:  # noqa: BLE001
            logger.exception("创建 tkinter 窗口失败")
            self._started.set()
            return

        root.withdraw()
        root.overrideredirect(True)
        try:
            root.attributes("-topmost", True)
        except Exception:  # noqa: BLE001
            pass
        try:
            root.attributes("-transparentcolor", KEY)
        except Exception:  # noqa: BLE001
            pass
        root.configure(bg=KEY)

        canvas = tk.Canvas(
            root, width=WIN_W, height=WIN_H, bg=KEY, highlightthickness=0, bd=0
        )
        canvas.pack()

        # 球体（圆角）+ 两行文字
        ball = _round_rect(
            canvas, 1, 1, BALL_W - 1, BALL_H - 1, BALL_R, fill=RED, outline=RED
        )
        name_id = canvas.create_text(
            BALL_W / 2, 23, text="@agent", fill="white",
            font=("Microsoft YaHei", 9),
        )
        action_id = canvas.create_text(
            BALL_W / 2, 48, text="停止", fill="white",
            font=("Microsoft YaHei", 20, "bold"),
        )

        # 拖动把手（圆角，和球体之间留透明间隙）
        hx = BALL_W + GAP
        hy = (WIN_H - HANDLE_SIZE) // 2
        handle = _round_rect(
            canvas, hx, hy, hx + HANDLE_SIZE, hy + HANDLE_SIZE, HANDLE_R,
            fill=HANDLE_BG, outline=HANDLE_BG,
        )
        handle_txt = canvas.create_text(
            hx + HANDLE_SIZE / 2, WIN_H / 2, text="✥", fill="white",
            font=("Segoe UI", 14),
        )

        state = {"paused": False, "shown": False}
        # 浏览器连接探测节流：每 6 个 tick（≈1.5s）探一次，连续 2 次失败才隐藏。
        conn = {"tick": 0, "miss": 0}

        def browser_connected() -> bool:
            """探测浏览器 CDP 端口是否仍可应答；探测不可用时保守返回 True（不误隐藏）。"""
            try:
                from app.runtime.browser_takeover import browser_status

                return bool(browser_status()[0])
            except Exception:  # noqa: BLE001
                return True

        def apply_state(paused: bool) -> None:
            state["paused"] = paused
            color = GREEN if paused else RED
            canvas.itemconfigure(ball, fill=color, outline=color)
            canvas.itemconfigure(action_id, text="继续" if paused else "停止")

        def on_ball_click(_evt=None) -> None:
            from app.runtime.browser_takeover import is_paused, set_paused

            set_paused(not is_paused())
            apply_state(is_paused())

        for item in (ball, name_id, action_id):
            canvas.tag_bind(item, "<Button-1>", on_ball_click)

        drag = {"on": False, "dx": 0, "dy": 0}

        def on_press(e) -> None:
            drag["on"] = True
            drag["dx"] = e.x_root - root.winfo_x()
            drag["dy"] = e.y_root - root.winfo_y()

        def on_move(e) -> None:
            if drag["on"]:
                root.geometry(f"+{e.x_root - drag['dx']}+{e.y_root - drag['dy']}")

        def on_release(_e) -> None:
            if drag["on"]:
                drag["on"] = False
                self._save_pos(root.winfo_x(), root.winfo_y())

        for item in (handle, handle_txt):
            canvas.tag_bind(item, "<Button-1>", on_press)
        canvas.bind("<B1-Motion>", on_move)
        canvas.bind("<ButtonRelease-1>", on_release)

        pos = self._load_pos()
        if pos:
            x, y = pos
        else:
            x, y = max(0, root.winfo_screenwidth() - WIN_W - 40), 80
        root.geometry(f"{WIN_W}x{WIN_H}+{x}+{y}")

        def drain() -> None:
            from app.runtime.browser_takeover import is_paused, set_paused

            try:
                while True:
                    cmd, arg = self._q.get_nowait()
                    if cmd == "show":
                        self._name = str(arg or "agent")
                        canvas.itemconfigure(name_id, text=f"@{self._name} agent")
                        apply_state(is_paused())
                        state["shown"] = True
                        conn["miss"] = 0
                        root.deiconify()
                        try:
                            root.attributes("-topmost", True)
                            root.lift()
                        except Exception:  # noqa: BLE001
                            pass
                    elif cmd == "hide":
                        state["shown"] = False
                        root.withdraw()
            except queue.Empty:
                pass

            paused = is_paused()
            if paused != state["paused"]:
                apply_state(paused)
            try:
                root.attributes("-topmost", True)
            except Exception:  # noqa: BLE001
                pass

            # 浏览器关闭（CDP 端口无响应）后自动隐藏悬浮按钮，不必退出程序。
            if state["shown"]:
                conn["tick"] += 1
                if conn["tick"] >= 6:
                    conn["tick"] = 0
                    if browser_connected():
                        conn["miss"] = 0
                    else:
                        conn["miss"] += 1
                        if conn["miss"] >= 2:
                            conn["miss"] = 0
                            state["shown"] = False
                            root.withdraw()
                            # 浏览器已关闭：暂停状态随之复位为「停止」，避免重开后仍被短路。
                            set_paused(False)
                            apply_state(False)

            root.after(250, drain)

        self._started.set()
        root.after(50, drain)
        root.mainloop()
