"""browser_agent: text-only LLM browser takeover tools (P0/P1/P2).

Public API::

    from browser_agent import tool_1_open_browser, tool_2_interact, ...

Every tool returns a dict (see ID04/ID05 for the field contract).
"""

from .schemas import TOOLS
from .tools import (
    tool_1_open_browser,
    tool_2_interact,
    tool_3_get_viewport_dom,
    tool_4_list_tabs,
    tool_5_switch_tab,
    tool_6_go_back,
    tool_7_refresh,
    tool_8_close_tab,
    tool_9_navigate,
    tool_10_tab_url_map,
    tool_11_interact_many,
)

__all__ = [
    "TOOLS",
    "tool_1_open_browser",
    "tool_2_interact",
    "tool_3_get_viewport_dom",
    "tool_4_list_tabs",
    "tool_5_switch_tab",
    "tool_6_go_back",
    "tool_7_refresh",
    "tool_8_close_tab",
    "tool_9_navigate",
    "tool_10_tab_url_map",
    "tool_11_interact_many",
]
