"""The tool surface exposed to the LLM.

The numbering is 0-based (``tool_0`` .. ``tool_10``). ``tool_0_open_browser`` is a
**control-plane** tool used only by the Studio UI (the 「打开浏览器」 button),
and is deliberately **not** exposed to the LLM (see ``app/runtime/browser_takeover.py``).
Every tool returns a plain dict so it can be wired to any agent framework.
"""

from __future__ import annotations

from .controller import NOT_CALLED, BrowserController


def tool_0_open_browser() -> dict:
    """打开浏览器并返回当前聚焦标签页的完整 DOM（仅供 Studio UI 调用，不暴露给 LLM）。"""
    return BrowserController.instance().full_dom(action_ok=NOT_CALLED)


def tool_1_interact(
    name: str, fill: str = "", drag_pct: int = 0, scroll_delta: int = 0
) -> dict:
    """与单个互动元素互动（点击/输入/拖动/滚动），返回增量 DOM。"""
    return BrowserController.instance().interact(name, fill, drag_pct, scroll_delta)


def tool_2_get_viewport_dom() -> dict:
    """重新返回当前 viewport 的完整 DOM。"""
    return BrowserController.instance().full_dom(action_ok=NOT_CALLED)


def tool_3_list_tabs() -> dict:
    """返回当前所有标签页列表。"""
    return BrowserController.instance().list_tabs()


def tool_4_switch_tab(tab_id: str) -> dict:
    """切换到指定标签页（tab_id 可为纯数字序号或完整名称），返回其完整 DOM。"""
    return BrowserController.instance().switch_tab(tab_id)


def tool_5_go_back() -> dict:
    """浏览器返回上一页，返回完整 DOM。"""
    return BrowserController.instance().go_back()


def tool_6_refresh() -> dict:
    """刷新当前标签页，返回增量 DOM(diff)。"""
    return BrowserController.instance().refresh()


def tool_7_close_tab(tab_id: str) -> dict:
    """关闭指定标签页，返回剩余标签页列表。"""
    return BrowserController.instance().close_tab(tab_id)


def tool_8_navigate(url: str) -> dict:
    """新开一个标签页并访问给定地址，返回完整 DOM。"""
    return BrowserController.instance().navigate(url)


def tool_9_tab_url_map() -> dict:
    """返回当前所有标签页名称到实际 url 的映射 dict。"""
    return BrowserController.instance().tab_url_map()


def tool_10_interact_many(name_list: list, fill_list: list) -> dict:
    """按顺序批量互动：一系列填入 + 可选的最后一个点击，返回增量 DOM。"""
    return BrowserController.instance().interact_many(name_list, fill_list)
