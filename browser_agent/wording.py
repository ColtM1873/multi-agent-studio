"""可交互元素对 LLM 的说法（BID084）。

这里是「标签名 / 类别名 / 动作词」的唯一事实源：中文口径与英文口径都在这里
定义，调用时读取 :mod:`browser_agent.prefs`（由 app 层在每次工具调用前覆盖）。
序列化层内部仍然固定使用中文标签作为解析锚点；最终返回 LLM 前由
:mod:`browser_agent.finalize` 负责把标签换成英文口径。
"""

from __future__ import annotations

from . import prefs

# 内部类别 -> 中文 / 英文标签名。中文名必须与 ``dom.serialize.CATEGORY_TAGS``
# 保持一致（序列化层仍用中文标签作为解析锚点）。
TAG_CN = {
    "click": "可点击元素",
    "clickdropdown": "可点击下拉元素",
    "input": "可输入元素",
    "searchable": "可搜索下拉元素",
    "select": "可选择元素",
    "drag": "可拖动元素",
    "scroll": "可滚动元素",
}

TAG_EN = {
    "click": "Click elem",
    "clickdropdown": "Click-dropdown elem",
    "input": "Type-in elem",
    "searchable": "Search-dropdown elem",
    "select": "Select elem",
    "drag": "Drag elem",
    "scroll": "Scroll elem",
}

# 参与「整合」的互动标签（可滚动元素按需求排除）与全部互动标签。
INTERACTIVE_TAGS_CN = tuple(
    TAG_CN[key]
    for key in ("click", "clickdropdown", "input", "searchable", "select", "drag")
)
ALL_TAGS_CN = INTERACTIVE_TAGS_CN + (TAG_CN["scroll"],)
TAG_EN_BY_CN = {cn: TAG_EN[key] for key, cn in TAG_CN.items()}

# 类别名（错误提示里「它是「可输入」类」使用）。
_CATEGORY_CN = {
    "input": "可输入",
    "searchable": "可搜索下拉",
    "select": "可选择",
    "click": "可点击",
    "clickdropdown": "可点击下拉",
    "drag": "可拖动",
    "scroll": "可滚动",
}

_CATEGORY_EN = {
    "input": "Type-in",
    "searchable": "Search-dropdown",
    "select": "Select",
    "click": "Click",
    "clickdropdown": "Click-dropdown",
    "drag": "Drag",
    "scroll": "Scroll",
}

# 动作词（错误提示里「不能用于 XX 操作」使用）。
_ACTION_CN = {
    "click": "点击",
    "input": "输入",
    "searchable": "可搜索下拉筛选",
    "select": "下拉选择",
    "clickdropdown": "点击下拉",
    "scroll": "滚动",
    "drag": "拖动",
}

_ACTION_EN = {
    "click": "Click",
    "input": "Type-in",
    "searchable": "Search-dropdown filter",
    "select": "Select",
    "clickdropdown": "Open dropdown",
    "scroll": "Scroll",
    "drag": "Drag",
}


# 本函数于BID084引入
def tag_name(category: str) -> str:
    """内部类别对应的标签名（英文开关开启时返回英文名）。"""
    table = TAG_EN if prefs.interactive_english_enabled() else TAG_CN
    return table.get(category, table["click"])


# 本函数于BID084引入
def category_label(category: str) -> str:
    """错误提示里的类别名；未知类别回退为「不可互动」/「non-interactive」。"""
    if prefs.interactive_english_enabled():
        return _CATEGORY_EN.get(category, "non-interactive")
    return _CATEGORY_CN.get(category, "不可互动")


# 本函数于BID084引入
def action_label(category: str) -> str:
    """错误提示里的动作词（空串表示未知类别）。"""
    table = _ACTION_EN if prefs.interactive_english_enabled() else _ACTION_CN
    return table.get(category, "")
