"""The tool surface exposed to the LLM.

P0/P1 implements tool_1 .. tool_4. Tools 5-9 are added in P2 (see ID04).
Every tool returns a plain dict so it can be wired to any agent framework.
"""

from __future__ import annotations

from .controller import NOT_CALLED, BrowserController


def tool_1_open_browser() -> dict:
    """打开浏览器并返回当前聚焦标签页的完整 DOM。"""
    return BrowserController.instance().full_dom(action_ok=NOT_CALLED)


def tool_2_interact(name: str, fill: str = "", drag_pct: int = 0) -> dict:
    """与单个互动元素互动（点击/输入/拖动/滚动），返回增量 DOM。"""
    return BrowserController.instance().interact(name, fill, drag_pct)


def tool_3_get_viewport_dom() -> dict:
    """重新返回当前 viewport 的完整 DOM。"""
    return BrowserController.instance().full_dom(action_ok=NOT_CALLED)


def tool_4_list_tabs() -> dict:
    """返回当前所有标签页列表。"""
    return BrowserController.instance().list_tabs()


def tool_5_switch_tab(tab_id: str) -> dict:
    """切换到指定标签页，返回其完整 DOM。"""
    return BrowserController.instance().switch_tab(tab_id)


def tool_6_go_back() -> dict:
    """浏览器返回上一页，返回完整 DOM。"""
    return BrowserController.instance().go_back()


def tool_7_refresh() -> dict:
    """刷新当前标签页，返回增量 DOM(diff)。"""
    return BrowserController.instance().refresh()


def tool_8_close_tab(tab_id: str) -> dict:
    """关闭指定标签页，返回剩余标签页列表。"""
    return BrowserController.instance().close_tab(tab_id)


def tool_9_navigate(url: str) -> dict:
    """新开一个标签页并访问给定地址，返回完整 DOM。"""
    return BrowserController.instance().navigate(url)


def tool_10_tab_url_map() -> dict:
    """返回当前所有标签页名称到实际 url 的映射 dict。"""
    return BrowserController.instance().tab_url_map()


def tool_11_interact_many(name_list: list, fill_list: list) -> dict:
    """按顺序批量互动：一系列填入 + 可选的最后一个点击，返回增量 DOM。"""
    return BrowserController.instance().interact_many(name_list, fill_list)
