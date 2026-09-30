"""浏览器工具「计时 + 卡死看门狗」诊断探针（默认关闭，零行为影响）。

用途：定位「浏览器工具调用后长时间无响应」到底是卡在哪一段。
启用方式（环境变量，二选一即可）：

    set BROWSER_TOOL_TIMING=1
    python run.py --console

启用后会在**程序根目录**写 ``browser_tools_timing.log``，内容包含：

- 每一次浏览器工具调用（``_wrap_up``）的进入 / 返回与总耗时；
- 每一条 CDP 命令（``CDPClient.send`` / ``send_nowait``）的方法名、耗时，
  超过阈值单独标记 ``SLOW CDP``；
- 关键 ``BrowserController`` 方法（``full_dom`` / ``capture_tree`` /
  ``capture_raw`` / ``_settle_navigation`` / ``_quiet`` …）的耗时；
- **卡死看门狗**：某次工具调用耗时超过 ``BROWSER_TOOL_TIMING_SLOW``（默认 10 秒）
  时，把**进程内所有线程的调用栈**各打印一次到日志（含卡住的确切文件:行号），
  这是定位「卡了 5 分钟」这类问题的关键证据。

可选环境变量：

- ``BROWSER_TOOL_TIMING_SLOW``：工具调用超过多少秒触发线程栈转储（默认 10）。
- ``BROWSER_TOOL_TIMING_CDP_SLOW``：单条 CDP 命令超过多少秒标记为 SLOW（默认 1）。
- ``BROWSER_TOOL_TIMING_DUMP_INTERVAL``：>0 时，不管有没有工具在跑，每隔这么多秒
  无条件转储一次全部线程栈（用于「工具还没派发就卡住」的极端情况，默认 0=关闭）。

关闭时（不设 ``BROWSER_TOOL_TIMING``）本模块不安装任何探针，运行时零开销。
"""

from __future__ import annotations

import functools
import logging
import os
import sys
import threading
import time
import traceback
from pathlib import Path

logger = logging.getLogger("browser_tool_timing")

_ROOT_DIR = Path(__file__).resolve().parents[1]
_LOG_PATH = _ROOT_DIR / "browser_tools_timing.log"

_installed = False
_installed_lock = threading.Lock()
_log_lock = threading.Lock()

# token -> (label, start_ts, thread_name)
_active: dict[int, tuple[str, float, str]] = {}
_active_lock = threading.Lock()
_token_seq = 0
_dumped_tokens: set[int] = set()

_VALID_SLOW = 10.0
_CDP_SLOW = 1.0
_DUMP_INTERVAL = 0.0


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _log(line: str) -> None:
    """写一行带时间戳的诊断日志（文件 + logger + stderr）。"""
    ts = time.strftime("%H:%M:%S") + f".{int((time.time() % 1) * 1000):03d}"
    text = f"[{ts}] [tid={threading.get_ident()}] {line}"
    with _log_lock:
        try:
            with open(_LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except Exception:
            pass
        try:
            logger.info(text)
        except Exception:
            pass


def _start_call(label: str) -> int:
    global _token_seq
    with _active_lock:
        _token_seq += 1
        token = _token_seq
        _active[token] = (label, time.time(), threading.current_thread().name)
    return token


def _finish_call(token: int) -> None:
    with _active_lock:
        _active.pop(token, None)
        _dumped_tokens.discard(token)


def _dump_stacks(reason: str) -> None:
    _log(f"==== 线程栈转储：{reason} ====")
    frames = sys._current_frames()
    for tid, frame in frames.items():
        name = threading._active.get(tid, "?")  # type: ignore[attr-defined]
        _log(f"---- 线程 {name} (id={tid}) ----")
        try:
            for line in traceback.format_stack(frame):
                _log("    " + line.rstrip())
        except Exception:
            pass
    _log("==== 线程栈转储结束 ====")


def _watchdog() -> None:
    while True:
        time.sleep(1.0)

        # A) 工具调用超时 → 转储一次
        now = time.time()
        to_dump: list[tuple[int, str, float]] = []
        with _active_lock:
            for token, (label, start, _tname) in list(_active.items()):
                if now - start >= _VALID_SLOW and token not in _dumped_tokens:
                    _dumped_tokens.add(token)
                    to_dump.append((token, label, now - start))
        for _token, label, elapsed in to_dump:
            _dump_stacks(f"工具调用 {label} 已运行 {elapsed:.1f}s 仍未返回")

        # B) 无条件定时转储（默认关闭）
        if _DUMP_INTERVAL > 0:
            if not hasattr(_watchdog, "_last_dump"):
                _watchdog._last_dump = now  # type: ignore[attr-defined]
            last = getattr(_watchdog, "_last_dump", now)  # type: ignore[attr-defined]
            if now - last >= _DUMP_INTERVAL:
                _watchdog._last_dump = now  # type: ignore[attr-defined]
                _dump_stacks(f"定时转储（每 {_DUMP_INTERVAL:g}s）")


def _mark(fn, label: str, track: bool = False):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        token = _start_call(label) if track else None
        t0 = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            dt = time.perf_counter() - t0
            if token is not None:
                _finish_call(token)
            _log(f"{label} 用时 {dt:.3f}s")
    wrapper._diag_wrapped = True  # type: ignore[attr-defined]
    return wrapper


def _wrap_method(cls, name: str, label: str | None = None, track: bool = False) -> None:
    fn = getattr(cls, name, None)
    if fn is None or getattr(fn, "_diag_wrapped", False):
        return
    setattr(cls, name, _mark(fn, label or f"{cls.__name__}.{name}", track))


def _wrap_namespace_func(mod, name: str, label: str, track: bool = False) -> None:
    fn = getattr(mod, name, None)
    if fn is None or getattr(fn, "_diag_wrapped", False):
        return
    setattr(mod, name, _mark(fn, label, track))


def _wrap_cdp() -> None:
    from browser_agent.cdp import CDPClient

    for method_name in ("send", "send_nowait"):
        orig = getattr(CDPClient, method_name)
        if getattr(orig, "_diag_wrapped", False):
            continue

        def make(orig_fn, mname):
            @functools.wraps(orig_fn)
            def wrapper(self, method, params=None, session_id=None, *args, **kwargs):
                t0 = time.perf_counter()
                try:
                    return orig_fn(self, method, params, session_id, *args, **kwargs)
                finally:
                    dt = time.perf_counter() - t0
                    flag = "  <== SLOW CDP" if dt >= _CDP_SLOW else ""
                    _log(
                        f"CDP.{mname} {method} 用时 {dt:.3f}s "
                        f"session={session_id}{flag}"
                    )
            wrapper._diag_wrapped = True  # type: ignore[attr-defined]
            return wrapper

        setattr(CDPClient, method_name, make(orig, method_name))


def _wrap_wrap_up() -> None:
    import app.runtime.browser_tools as bt
    import app.runtime.browser_tools_wrap_up as wrap_mod

    orig = wrap_mod._wrap_up
    if getattr(orig, "_diag_wrapped", False):
        return

    @functools.wraps(orig)
    async def wrapper(sync_func, **kwargs):
        label = getattr(sync_func, "__name__", str(sync_func))
        token = _start_call(f"tool:{label}")
        t0 = time.perf_counter()
        try:
            result = await orig(sync_func, **kwargs)
            return result
        finally:
            elapsed = time.perf_counter() - t0
            _finish_call(token)
            _log(f"==== 工具 {label} 调用结束，总用时 {elapsed:.3f}s ====")

    wrapper._diag_wrapped = True  # type: ignore[attr-defined]
    wrap_mod._wrap_up = wrapper
    # 工具函数在模块导入时已绑定旧名，这里同步替换实际被调用的引用。
    bt._wrap_up = wrapper


def install() -> bool:
    """安装诊断探针。未设 ``BROWSER_TOOL_TIMING`` 时直接返回 False（不安装）。"""
    global _installed, _VALID_SLOW, _CDP_SLOW, _DUMP_INTERVAL
    if _installed:
        return True
    if not _truthy(os.environ.get("BROWSER_TOOL_TIMING")):
        return False
    with _installed_lock:
        if _installed:
            return True
        _VALID_SLOW = _float_env("BROWSER_TOOL_TIMING_SLOW", 10.0)
        _CDP_SLOW = _float_env("BROWSER_TOOL_TIMING_CDP_SLOW", 1.0)
        _DUMP_INTERVAL = _float_env("BROWSER_TOOL_TIMING_DUMP_INTERVAL", 0.0)
        try:
            _wrap_cdp()

            from browser_agent import controller as C

            for name in (
                "ensure_connected",
                "_resolve_focused_target",
                "_probe_focused_target",
                "_activate_target",
                "capture_tree",
                "serialize_tree",
                "serialize_lines_tree",
                "full_dom",
                "_begin_interaction",
                "_finish_interaction",
                "interact_click",
                "interact_click_dropdown",
                "interact_fill_in",
                "interact_select",
                "interact_searchable_fill_in",
                "interact_scroll",
                "interact_drag",
                "interact_many",
                "_settle_navigation",
                "_wait_for_content",
                "_stabilize",
                "_quiet",
                "_wait_overlay_open",
                "switch_tab",
                "navigate",
                "go_back",
                "refresh",
            ):
                # track=True：超过 slow 阈值时，看门狗会把该调用所在线程的栈转储，
                # 这样「直接走 REST（viewport-dom/open）而非图内工具」的慢调用也能定位。
                _wrap_method(C.BrowserController, name, track=True)

            _wrap_namespace_func(C, "capture_raw", "capture_raw", track=True)
            _wrap_namespace_func(C, "build_enhanced_tree", "build_enhanced_tree", track=True)

            _wrap_wrap_up()

            threading.Thread(target=_watchdog, name="browser-timing-watchdog", daemon=True).start()
        except Exception:
            logger.exception("安装浏览器工具诊断探针失败")
            return False

        _installed = True
        try:
            _LOG_PATH.write_text("", encoding="utf-8")
        except Exception:
            pass
        _log(
            f"诊断探针已启用（slow看门狗阈值={_VALID_SLOW:g}s, CDP慢阈值={_CDP_SLOW:g}s, "
            f"定时间隔={_DUMP_INTERVAL:g}s），日志文件：{_LOG_PATH}"
        )
        print(f"[browser diagnostics] enabled -> {_LOG_PATH}")
        return True
