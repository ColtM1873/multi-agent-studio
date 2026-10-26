"""浏览器工具的运行时偏好（BID080）。

这些开关由 app 层在**每次浏览器工具调用前**覆盖（与 ``timing.configure`` 同构），
``browser_agent`` 不 import 任何 app 层代码；调用时现读，改完保存后下一次调用即生效。
"""

from __future__ import annotations

# 是否把网页里的图片解析成 ``[图片]`` 标签返回给 LLM。默认关闭（纯文本口径）。
_image_tags_enabled = False

# BID084：可交互元素整合（把 ``<tag eN>内容</tag eN>`` 压成 ``<tag eN : 内容>``）。
# 默认开启；分隔符默认英文冒号，用户可在「主界面设置 → 进阶设置」自定义。
_merge_enabled = True
_merge_separator = ":"
# BID084：可交互元素是否使用英文标签渲染（``<Click elem 23>``）。默认关闭。
_english_enabled = False


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


# 本常量于BID086引入
# 「允许 Agent 保留的最大标签页数」的默认值与上下限。
# 下限取 3：当前聚焦的标签页永远不能被关（它就是本次返回内容的来源），上限若是
# 1~2，一旦后台又开出一个新页，「新页」和「聚焦页」只能留一个，没有正确答案，
# 3 起步才留出正常操作的余地。
DEFAULT_TAB_LIMIT = 4
MIN_TAB_LIMIT = 3
MAX_TAB_LIMIT = 50

# 本变量于BID086引入
# 当前生效的「允许 Agent 保留的最大标签页数」。它和 ``image_tags`` / 整合开关一样
# 是**进程级**的一档值——因为标签页是**浏览器进程**的资源（一个 CDP 端口一张 LRU
# 表），上限自然也按浏览器算，不按 agent、也不按会话分别记账。app 层在图编译时用
# ``configure_tab_limit()`` 写入（取自当前 multi-agent 配置），工具调用时现读。
_tab_limit: int = DEFAULT_TAB_LIMIT


# 本函数于BID086引入
def _coerce_tab_limit(value) -> int:
    """把任意输入收敛成 3~50 的整数（非法值回退默认值）。"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return DEFAULT_TAB_LIMIT
    return max(MIN_TAB_LIMIT, min(MAX_TAB_LIMIT, number))


# 本函数于BID086引入
def configure_tab_limit(limit) -> None:
    """设置「允许 Agent 保留的最大标签页数」（clamp 到 3~50），调用时现读。"""
    global _tab_limit
    _tab_limit = _coerce_tab_limit(limit)


# 本函数于BID086引入
def tab_limit() -> int:
    """当前生效的「允许 Agent 保留的最大标签页数」（3~50）。"""
    return _tab_limit
