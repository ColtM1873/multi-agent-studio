"""浏览器工具 debug 模式：按调用采集「原始 DOM / 处理后的 DOM」。

这是一个**只读观测**钩子，默认不生效（``current_capture()`` 返回 ``None``）：

- 只有在 LLM 通过 ``app.runtime.browser_takeover`` 调用工具、且全局设置开启
  debug 模式时，包装层才会 ``begin_capture()``；
- 控制器在 ``capture_tree`` 抓取 DOM 时，把页面原始 HTML 与处理后的序列化
  文本写入当前采集器；
- 包装层在调用结束后读取采集器并落盘成人类可读日志。

用 ``contextvars`` 保存「当前采集器」，因此 ``asyncio.to_thread`` 并发执行的
多次调用互不干扰。直接调用 ``browser_agent`` 工具（例如前端「提示接管」走的
``tool_3``）不会 ``begin_capture()``，因此不会产生日志。
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass


@dataclass
class DebugCapture:
    """一次工具调用内采集到的 DOM 快照（后写覆盖先写）。"""

    raw_dom: str = ""
    processed_dom: str = ""
    url: str = ""
    title: str = ""


_current: contextvars.ContextVar[DebugCapture | None] = contextvars.ContextVar(
    "browser_debug_capture", default=None
)


def begin_capture() -> DebugCapture:
    """开始一次采集，返回新建的采集器并设为当前。"""
    capture = DebugCapture()
    _current.set(capture)
    return capture


def end_capture() -> None:
    """结束当前采集（复位为 None）。"""
    _current.set(None)


def current_capture() -> DebugCapture | None:
    """返回当前线程/上下文的采集器；未开启 debug 时为 ``None``。"""
    return _current.get()


def record_dom(
    raw_dom: str = "",
    processed_dom: str = "",
    url: str = "",
    title: str = "",
) -> None:
    """把一次抓取的 DOM 写入当前采集器（未开启 debug 时静默跳过）。"""
    capture = _current.get()
    if capture is None:
        return
    if raw_dom:
        capture.raw_dom = raw_dom
    if processed_dom:
        capture.processed_dom = processed_dom
    if url:
        capture.url = url
    if title:
        capture.title = title
