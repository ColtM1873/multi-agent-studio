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
