"""浏览器工具的运行时偏好（BID080）。

这些开关由 app 层在**每次浏览器工具调用前**覆盖（与 ``timing.configure`` 同构），
``browser_agent`` 不 import 任何 app 层代码；调用时现读，改完保存后下一次调用即生效。
"""

from __future__ import annotations

import contextlib
import contextvars

# 是否把网页里的图片解析成 ``[图片]`` 标签返回给 LLM。默认关闭（纯文本口径）。
_image_tags_enabled = False

# BID084：可交互元素整合（把 ``<tag eN>内容</tag eN>`` 压成 ``<tag eN : 内容>``）。
# 默认开启；分隔符默认英文冒号，用户可在「主界面设置 → 进阶设置」自定义。
_merge_enabled = True
_merge_separator = ":"
# BID084：可交互元素是否使用英文标签渲染（``<Click elem 23>``）。默认关闭。
_english_enabled = False

# 本常量于BID086引入
# 「允许 Agent 保留的最大标签页数」的默认值（未配置时用 4）与下限。
# 下限取 3：上限 1~2 时会逼出「刚开的标签页和当前聚焦页只能留一个」这类没有正确答案的
# 取舍（焦点页永远不能关，于是新页一开就得关），3 起步留出正常操作的余地。
DEFAULT_TAB_LIMIT = 4
MIN_TAB_LIMIT = 3

# 本变量于BID086引入
# 允许 Agent 保留的最大标签页数。用 ``contextvars`` 而不是进程级全局：本值**按
# multi-agent 配置**（每个 agent 可以不同），由 app 层在每次工具调用时用
# ``tab_limit_scope()`` 设置；``asyncio.to_thread`` 会把上下文复制给工作线程，
# 因此控制器（在 worker 线程里跑）读到的一定是本次调用所属 agent 的值，不会因为
# 另一个 agent 同时在被使用而互相串台。
_tab_limit: contextvars.ContextVar[int] = contextvars.ContextVar(
    "browser_tab_limit", default=DEFAULT_TAB_LIMIT
)

# 本变量于BID086引入
# 当前「对话」标识（app 层的 ``<agent_id>::<thread_id>``）。标签页 LRU 表按它分桶
# ——每个对话各算各的、各关各的；同样用 contextvars，避免不同对话并发时串台。
_conversation: contextvars.ContextVar[str] = contextvars.ContextVar(
    "browser_conversation", default=""
)


# 本函数于BID086引入
def _coerce_tab_limit(value) -> int:
    """把任意输入收敛成 3~50 的整数（非法值回退默认值）。"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return DEFAULT_TAB_LIMIT
    return max(MIN_TAB_LIMIT, min(50, number))


# 本函数于BID086引入
def tab_limit() -> int:
    """当前生效的「允许 Agent 保留的最大标签页数」（>=3）。"""
    return _tab_limit.get()


# 本函数于BID086引入
def conversation_id() -> str:
    """当前对话的 LRU 桶名（空串表示未知对话，所有未知调用共用一个桶）。"""
    return _conversation.get()


# 本函数于BID086引入
@contextlib.contextmanager
def tab_limit_scope(limit):
    """在 ``with`` 期间把「最大标签页数」设为 ``limit``，退出时复位。

    由 app 层在图节点执行工具前后使用（见 ``graph_builder.tool_node_front``）。
    """
    token = _tab_limit.set(_coerce_tab_limit(limit))
    try:
        yield
    finally:
        _tab_limit.reset(token)


# 本函数于BID086引入
@contextlib.contextmanager
def conversation_scope(conversation: str):
    """在 ``with`` 期间把「当前对话」设为 ``conversation``（标签页 LRU 的分桶键）。"""
    token = _conversation.set(str(conversation or ""))
    try:
        yield
    finally:
        _conversation.reset(token)



# 本函数于BID080引入
def configure_image_tags(enabled: bool) -> None:
    """设置「浏览器工具解析图片标签」开关（调用时现读，默认 False）。"""
    global _image_tags_enabled
    _image_tags_enabled = bool(enabled)


# 本函数于BID080引入
def image_tags_enabled() -> bool:
    """当前是否输出 ``[图片]`` 标签。"""
    return _image_tags_enabled


# 本函数于BID084引入
def configure_interactive(
    merge_enabled: bool = True,
    separator: str = ":",
    english_enabled: bool = False,
) -> None:
    """设置「可交互元素整合 / 英文渲染」开关（每次工具调用前现读）。

    分隔符做最小卫生处理：去掉首尾空白、禁止换行；空串回退到 ``:``。
    ``<`` / ``>`` 允许作为分隔符（最终表现层会按用户口径跳过对应字符的转义）。
    """
    global _merge_enabled, _merge_separator, _english_enabled
    _merge_enabled = bool(merge_enabled)
    sep = str(separator if separator is not None else ":").strip()
    if not sep or "\n" in sep or "\r" in sep:
        sep = ":"
    _merge_separator = sep
    _english_enabled = bool(english_enabled)


# 本函数于BID084引入
def interactive_merge_enabled() -> bool:
    """当前是否把可交互元素的包裹内容整合进开标签。"""
    return _merge_enabled


# 本函数于BID084引入
def interactive_merge_separator() -> str:
    """当前整合分隔符（已做卫生处理，默认 ``:``）。"""
    return _merge_separator


# 本函数于BID084引入
def interactive_english_enabled() -> bool:
    """当前可交互元素是否使用英文标签渲染。"""
    return _english_enabled
