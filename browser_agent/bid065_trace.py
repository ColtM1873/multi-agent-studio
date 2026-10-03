"""BID065 临时代码：浏览器工具 debug 开关下的细粒度时间戳探针。

仅在全局设置「浏览器工具 debug」(`browser_takeover_debug`) 打开时工作：
把带毫秒时间戳的探针行**追加**写入项目根目录的 ``browser_tools_timing.log``
（与 ``browser_agent.diagnostics`` 同一个文件）。

发行版移除：删除本文件，并删除所有源码里行末带 ``# BID065`` 的调用行。
"""

from __future__ import annotations  # BID065

import threading  # BID065
import time  # BID065
from pathlib import Path  # BID065

_LOG_PATH = Path(__file__).resolve().parents[1] / "browser_tools_timing.log"  # BID065
_lock = threading.Lock()  # BID065
_provider = None  # BID065: 由 app 层注入的 () -> bool（读取 debug 设置）
_cache = {"at": 0.0, "on": False}  # BID065: debug 开关的 1s TTL 缓存


def set_provider(provider) -> None:  # BID065
    """注入「浏览器工具 debug 是否开启」的读取函数（app 层注册）。"""
    global _provider  # BID065
    _provider = provider  # BID065


def enabled() -> bool:  # BID065
    if _provider is None:  # BID065
        return False  # BID065
    now = time.monotonic()  # BID065
    if now - _cache["at"] > 1.0:  # BID065
        try:  # BID065
            _cache["on"] = bool(_provider())  # BID065
        except Exception:  # BID065
            _cache["on"] = False  # BID065
        _cache["at"] = now  # BID065
    return _cache["on"]  # BID065


def perf() -> float:  # BID065
    """单调计时起点，用于 mark_elapsed。"""
    return time.perf_counter()  # BID065


def mark(label: str) -> None:  # BID065
    """写一行带毫秒时间戳 + 线程号的探针（debug 关闭时零写入）。"""
    if not enabled():  # BID065
        return  # BID065
    ts = time.strftime("%H:%M:%S") + f".{int((time.time() % 1) * 1000):03d}"  # BID065
    line = f"[{ts}] [tid={threading.get_ident()}] BID065 {label}"  # BID065
    with _lock:  # BID065
        try:  # BID065
            with open(_LOG_PATH, "a", encoding="utf-8") as fh:  # BID065
                fh.write(line + "\n")  # BID065
        except Exception:  # BID065
            pass  # BID065


def mark_elapsed(label: str, start: float) -> None:  # BID065
    mark(f"{label} 用时 {time.perf_counter() - start:.3f}s")  # BID065
