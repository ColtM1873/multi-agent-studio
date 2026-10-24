"""浏览器工具的运行时偏好（BID080）。

这些开关由 app 层在**每次浏览器工具调用前**覆盖（与 ``timing.configure`` 同构），
``browser_agent`` 不 import 任何 app 层代码；调用时现读，改完保存后下一次调用即生效。
"""

from __future__ import annotations

# 是否把网页里的图片解析成 ``[图片]`` 标签返回给 LLM。默认关闭（纯文本口径）。
_image_tags_enabled = False


# 本函数于BID080引入
def configure_image_tags(enabled: bool) -> None:
    """设置「浏览器工具解析图片标签」开关（调用时现读，默认 False）。"""
    global _image_tags_enabled
    _image_tags_enabled = bool(enabled)


# 本函数于BID080引入
def image_tags_enabled() -> bool:
    """当前是否输出 ``[图片]`` 标签。"""
    return _image_tags_enabled
