"""系统级置顶「暂停 / 继续」悬浮控件（ID91：多会话列表）。

* 单会话：保持原有样式（120×72 红/绿球 + 分离拖动把手）；
* 多会话：纵向列表，每一行是独立会话，点击只切换该会话的暂停状态；
* 数据由 ``BrowserSessionManager.floating_entries()`` 提供（Tk 线程只读快照）；
* 浏览器关闭 / 会话验收且离开会话后，对应行自动消失；全部消失则窗口隐藏。

仍用 tkinter 在守护线程跑 mainloop，外部只投队列 / 读 provider，不在 Tk 线程做
CDP 探测（provider 内部有 1 秒缓存）。
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

RED = "#dc2626"
GREEN = "#16a34a"
HANDLE_BG = "#3f3f46"
KEY = "#ff00ff"

BALL_W, BALL_H = 120, 72
BALL_R = 18
GAP = 14
HANDLE_SIZE = 36
HANDLE_R = 12

ROW_W, ROW_H, ROW_GAP = 246, 58, 8
MAX_VISIBLE_ROWS = 6


def _round_rect(canvas, x1, y1, x2, y2, r, **kwargs):
    points = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


def _clip(text: str, limit: int = 28) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


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
        self._atexit_done = False
        self._provider: Optional[Callable[[], list[dict]]] = None
        self._manual_hidden = False
        self._last_sig = None

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

    def set_provider(self, provider: Callable[[], list[dict]]) -> None:
        # 只登记数据源；真正创建 Tk 线程由浏览器会话/打开浏览器时触发，
        # 避免只用 CLI/纯后端时也起一个隐藏窗口。
        self._provider = provider

    def ensure_started(self) -> None:
        if not self.available():
            return
        self._ensure_thread()
        self._q.put(("refresh", None))

    def show(self, name: str = "agent", pos_path: Optional[Path] = None) -> None:
        if not self.available():
            logger.warning("tkinter 不可用，跳过系统级停止按钮")
            return
        if pos_path is not None:
            self._pos_path = Path(pos_path)
        self._name = (name or "agent").strip() or "agent"
        self._manual_hidden = False
        self.ensure_started()

    def hide(self) -> None:
        self._manual_hidden = True
        if self._thread is None:
            return
        self._q.put(("refresh", None))

    def shutdown(self) -> None:
        self._q.put(("quit", None))
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #
    def _ensure_thread(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._started.clear()
        if not self._atexit_done:
            self._atexit_done = True
            import atexit

            atexit.register(self.shutdown)
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

    def _entries(self) -> list[dict]:
        if self._manual_hidden:
            return []
        if self._provider is None:
            return []
        try:
            return list(self._provider() or [])
        except Exception:  # noqa: BLE001
            logger.debug("读取悬浮球会话列表失败", exc_info=True)
            return []

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

        try:
            scale = root.winfo_fpixels("1i") / 96.0
        except Exception:  # noqa: BLE001
            scale = 1.0
        if not (scale and scale > 0):
            scale = 1.0

        def _s(v: float) -> int:
            return max(1, int(round(v * scale)))

        canvas = tk.Canvas(root, bg=KEY, highlightthickness=0, bd=0)
        canvas.pack(fill="both", expand=True)

        state = {"shown": False, "layout": "", "scrollable": False}
        row_boxes: list[tuple[float, float, dict]] = []
        drag = {"on": False, "dx": 0, "dy": 0, "moved": False}
        tick = {"n": 0}

        def window_size(entries: list[dict]) -> tuple[int, int, int]:
            if len(entries) <= 1:
                width = _s(BALL_W) + _s(GAP) + _s(HANDLE_SIZE)
                height = _s(BALL_H)
                return width, height, height
            shown_rows = min(len(entries), MAX_VISIBLE_ROWS)
            height = shown_rows * _s(ROW_H) + (shown_rows - 1) * _s(ROW_GAP)
            width = _s(ROW_W) + _s(GAP) + _s(HANDLE_SIZE)
            total = len(entries) * _s(ROW_H) + (len(entries) - 1) * _s(ROW_GAP)
            return width, height, total

        def place_window(width: int, height: int) -> None:
            pos = self._load_pos()
            if pos:
                x, y = pos
            else:
                x, y = max(0, root.winfo_screenwidth() - width - _s(40)), _s(80)
            max_x = max(0, root.winfo_screenwidth() - width)
            max_y = max(0, root.winfo_screenheight() - height)
            x = max(0, min(x, max_x))
            y = max(0, min(y, max_y))
            root.geometry(f"{width}x{height}+{x}+{y}")

        def draw(entries: list[dict]) -> None:
            nonlocal row_boxes
            canvas.delete("all")
            row_boxes = []
            single = len(entries) <= 1
            width, height, total = window_size(entries)
            canvas.configure(width=width, height=height, scrollregion=(0, 0, width, total))
            place_window(width, height)
            state["scrollable"] = total > height
            if single:
                entry = entries[0]
                paused = bool(entry.get("paused"))
                color = GREEN if paused else RED
                ball_w, ball_h = _s(BALL_W), _s(BALL_H)
                _round_rect(
                    canvas, _s(1), _s(1), ball_w - _s(1), ball_h - _s(1), _s(BALL_R),
                    fill=color, outline=color,
                )
                canvas.create_text(
                    ball_w / 2, _s(23), text="@" + _clip(entry.get("agent_name") or self._name, 14) + " agent",
                    fill="white", font=("Microsoft YaHei", 9),
                )
                canvas.create_text(
                    ball_w / 2, _s(48), text="继续" if paused else "停止",
                    fill="white", font=("Microsoft YaHei", 20, "bold"),
                )
                row_boxes.append((0, ball_h, entry))
                handle_y = (ball_h - _s(HANDLE_SIZE)) // 2
                handle_x = ball_w + _s(GAP)
                _round_rect(
                    canvas, handle_x, handle_y, handle_x + _s(HANDLE_SIZE),
                    handle_y + _s(HANDLE_SIZE), _s(HANDLE_R),
                    fill=HANDLE_BG, outline=HANDLE_BG,
                )
                canvas.create_text(
                    handle_x + _s(HANDLE_SIZE) / 2, ball_h / 2, text="✥",
                    fill="white", font=("Segoe UI", 14),
                )
                state["layout"] = "single"
                return
            y = 0
            for entry in entries:
                paused = bool(entry.get("paused"))
                color = GREEN if paused else RED
                _round_rect(
                    canvas, _s(1), y + _s(1), _s(ROW_W) - _s(1), y + _s(ROW_H) - _s(1),
                    _s(12), fill=color, outline=color,
                )
                label = "@" + _clip(entry.get("agent_name") or "agent", 15)
                label += " - " + _clip(entry.get("thread_id") or "会话", 15)
                canvas.create_text(
                    _s(12), y + _s(18), text=label, anchor="w", fill="white",
                    font=("Microsoft YaHei", 9),
                )
                canvas.create_text(
                    _s(12), y + _s(41), text="继续" if paused else "暂停",
                    anchor="w", fill="white", font=("Microsoft YaHei", 15, "bold"),
                )
                row_boxes.append((y, y + _s(ROW_H), entry))
                y += _s(ROW_H) + _s(ROW_GAP)
            handle_x = _s(ROW_W) + _s(GAP)
            handle_y = (height - _s(HANDLE_SIZE)) // 2
            _round_rect(
                canvas, handle_x, handle_y, handle_x + _s(HANDLE_SIZE),
                handle_y + _s(HANDLE_SIZE), _s(HANDLE_R),
                fill=HANDLE_BG, outline=HANDLE_BG,
            )
            canvas.create_text(
                handle_x + _s(HANDLE_SIZE) / 2, height / 2, text="✥",
                fill="white", font=("Segoe UI", 14),
            )
            state["layout"] = "list"

        def on_wheel(event) -> None:
            if not state["scrollable"]:
                return
            delta = -1 if event.delta > 0 else 1
            canvas.yview_scroll(delta, "units")

        def on_press(event) -> None:
            single = state["layout"] == "single"
            handle_x = (_s(BALL_W) if single else _s(ROW_W)) + _s(GAP) / 2
            if event.x >= handle_x:
                drag["on"] = True
                drag["moved"] = False
                drag["dx"] = event.x_root - root.winfo_x()
                drag["dy"] = event.y_root - root.winfo_y()
                return
            cy = canvas.canvasy(event.y)
            for y1, y2, entry in row_boxes:
                if y1 <= cy <= y2:
                    try:
                        entry["toggle"]()
                    except Exception:  # noqa: BLE001
                        logger.debug("切换会话暂停失败", exc_info=True)
                    self._last_sig = None
                    break

        def on_move(event) -> None:
            if not drag["on"]:
                return
            drag["moved"] = True
            root.geometry(f"+{event.x_root - drag['dx']}+{event.y_root - drag['dy']}")

        def on_release(_event) -> None:
            if drag["on"]:
                drag["on"] = False
                if drag["moved"]:
                    self._save_pos(root.winfo_x(), root.winfo_y())

        canvas.bind("<Button-1>", on_press)
        canvas.bind("<B1-Motion>", on_move)
        canvas.bind("<ButtonRelease-1>", on_release)
        canvas.bind("<MouseWheel>", on_wheel)

        def drain() -> None:
            try:
                while True:
                    cmd, arg = self._q.get_nowait()
                    if cmd == "quit":
                        root.quit()
                        return
                    if cmd == "refresh":
                        self._last_sig = None
            except queue.Empty:
                pass

            tick["n"] += 1
            if tick["n"] % 2 == 0:
                entries = self._entries()
                sig = tuple(
                    (
                        str(e.get("key")),
                        bool(e.get("paused")),
                        str(e.get("agent_name")),
                        str(e.get("thread_id")),
                    )
                    for e in entries
                )
                if not entries:
                    if state["shown"]:
                        state["shown"] = False
                        root.withdraw()
                    self._last_sig = sig
                else:
                    if sig != self._last_sig:
                        self._last_sig = sig
                        draw(entries)
                    if not state["shown"]:
                        state["shown"] = True
                        root.deiconify()
                    try:
                        root.attributes("-topmost", True)
                    except Exception:  # noqa: BLE001
                        pass
            root.after(250, drain)

        self._started.set()
        root.after(50, drain)
        try:
            root.mainloop()
        finally:
            try:
                root.destroy()
            except Exception:  # noqa: BLE001
                pass
            try:
                del root, canvas
            except Exception:  # noqa: BLE001
                pass
            try:
                import gc

                gc.collect()
            except Exception:  # noqa: BLE001
                pass
