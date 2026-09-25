"""browser_agent: text-only LLM browser takeover tools (P0/P1/P2).

Public API::

    from browser_agent import tool_0_open_browser, tool_1_interact, ...

Every tool returns a dict (see ID04/ID05 for the field contract).

``tool_0_open_browser`` is a control-plane tool for the Studio UI only and must
never be exposed to the LLM.
"""

from .schemas import TOOLS
from .tools import (
    tool_0_open_browser,
    tool_1_interact,
    tool_2_get_viewport_dom,
    tool_3_list_tabs,
    tool_4_switch_tab,
    tool_5_go_back,
    tool_6_refresh,
    tool_7_close_tab,
    tool_8_navigate,
    tool_9_tab_url_map,
    tool_10_interact_many,
)

__all__ = [
    "TOOLS",
    "tool_0_open_browser",
    "tool_1_interact",
    "tool_2_get_viewport_dom",
    "tool_3_list_tabs",
    "tool_4_switch_tab",
    "tool_5_go_back",
    "tool_6_refresh",
    "tool_7_close_tab",
    "tool_8_navigate",
    "tool_9_tab_url_map",
    "tool_10_interact_many",
]
