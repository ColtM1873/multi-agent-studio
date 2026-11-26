"""浏览器工具 debug 模式：按调用采集「原始 DOM / 处理后的 DOM」。

这是一个**只读观测**钩子，默认不生效（``current_capture()`` 返回 ``None``）：

- 只有在 LLM 通过 ``app.runtime.browser_takeover`` 调用工具、且全局设置开启
  debug 模式时，包装层才会 ``begin_capture()``；
- 控制器在 ``capture_tree`` 抓取 DOM 时，把页面原始 HTML 与处理后的序列化
  文本写入当前采集器；
- 包装层在调用结束后读取采集器并落盘成人类可读日志。

用 ``contextvars`` 保存「当前采集器」，因此 ``asyncio.to_thread`` 并发执行的
多次调用互不干扰。直接调用 ``browser_agent`` 工具（例如前端「提示接管」走的
``tool_2``）不会 ``begin_capture()``，因此不会产生日志。
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field


@dataclass
class DebugCapture:
    """一次工具调用内采集到的 DOM 快照（后写覆盖先写）。"""

    raw_dom: str = ""
    processed_dom: str = ""
    url: str = ""
    title: str = ""
    # BID111：解析/交互判据的「双层结构」留痕。
    # ``vendors`` 记这一页识别到的编辑器组件库（通用层之外的那一层）；
    # ``notes`` 记 vendor 层**否决**过哪些通用剪枝/降级。排障时能直接看出这条
    # 输出是被通用层判的，还是被某个组件库适配层 override 过。
    vendors: str = ""  # BID111
    notes: list[str] = field(default_factory=list)  # BID111


# BID111：备注去重与上限（一次调用里同一类否决可能命中几十个节点）。
_MAX_NOTES = 20  # BID111


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
    vendors: str = "",  # BID111
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
    if vendors:  # BID111
        capture.vendors = vendors  # BID111


# 本函数于BID111引入
def note(text: str) -> None:
    """记录一条「判据留痕」（未开启 debug 时零开销）。

    供解析/交互判据在**双层结构**里说明「这一条是通用层判的，还是 vendor 层
    override 的」。只记事实、不参与任何判定；去重并限量，避免大页面把日志撑爆。
    """
    capture = _current.get()
    if capture is None or not text:
        return
    text = " ".join(str(text).split())  # 单行：备注不能破坏日志的段落结构
    if not text or text in capture.notes:
        return
    if len(capture.notes) >= _MAX_NOTES:
        return
    capture.notes.append(text)

