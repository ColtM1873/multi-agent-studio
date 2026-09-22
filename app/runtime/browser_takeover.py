"""浏览器接管工具：把 ``browser_agent`` 的同步工具转换成 LangChain StructuredTool。

设计要点（见 inner_docs/ID56）：
- **惰性 import** ``browser_agent``：未安装 websocket-client 时不应导致 studio 启动失败。
- **暂停短路**：全局 ``_paused`` 为真时，所有工具**最先**返回 ``STOP_TEXT``，
  不做任何连接/校验/输入检查（满足「直接 shortcut」要求）。
- **不阻塞事件循环**：browser_agent 全同步（websocket-client + sleep），
  统一用 ``asyncio.to_thread`` 在线程池执行。
- 工具返回 dict 时序列化为 JSON 字符串（``ToolMessage.content`` 需为文本）。
"""

from __future__ import annotations

import asyncio
import json

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

STOP_TEXT = "客户已暂停你的浏览器操作，请立即停止所有浏览器动作，等待客户的后续指示。"

_paused: bool = False


def set_paused(value: bool) -> None:
    """设置全局暂停标志（客户点「停止」为 True，点「继续」为 False）。"""
    global _paused
    _paused = bool(value)


def is_paused() -> bool:
    return _paused


# ── 有参工具的 args_schema（描述照搬 browser_agent/schemas.py）─────────────
class NoArgs(BaseModel):
    """无参工具的空 schema（避免从 **kwargs 推断出错误的 kwargs 参数）。"""


class InteractArgs(BaseModel):
    name: str = Field(..., description="互动元素名称，如 'e4'（来自序列化标签）。")
    fill: str = Field("", description="仅用于『可输入』元素的填充内容；其他情况传空串 ''。")
    drag_pct: int = Field(
        0, ge=0, le=100,
        description="仅用于『可拖动』元素的目标位置百分比(0-100)；其他情况传 0。",
    )


class SwitchTabArgs(BaseModel):
    tab_id: str = Field(..., description="目标标签页名称，如 '百度001'（来自 tool_4/tool_10）。")


class CloseTabArgs(BaseModel):
    tab_id: str = Field(..., description="要关闭的标签页名称，如 '百度001'。")


class NavigateArgs(BaseModel):
    url: str = Field(..., description="目标地址，如 'example.com' 或完整 URL。")


class InteractManyArgs(BaseModel):
    name_list: list[str] = Field(
        ..., description="互动元素名称列表，如 ['e4','e5','e6']；最后一个可选为可点击元素。"
    )
    fill_list: list[str] = Field(
        ...,
        description="填入内容列表；与 name_list 等长，或比它少一个（少一个时末位为收尾点击）。",
    )


def _wrap(fn, description: str, args_schema=None) -> StructuredTool:
    """把一个 browser_agent 同步工具包成 LangChain StructuredTool。"""

    async def _call(**kwargs) -> str:
        if _paused:
            return STOP_TEXT
        try:
            result = await asyncio.to_thread(fn, **kwargs)
        except Exception as exc:  # noqa: BLE001
            return f"浏览器工具执行失败：{type(exc).__name__}: {exc}"
        if isinstance(result, str):
            return result
        try:
            return json.dumps(result, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(result)

    return StructuredTool.from_function(
        coroutine=_call,
        name=fn.__name__,
        description=description,
        args_schema=args_schema,
    )


def build_browser_tools() -> list[StructuredTool]:
    """构建 11 个浏览器接管工具（惰性 import browser_agent）。"""
    import browser_agent as ba

    descriptions = {t["name"]: t["description"] for t in ba.TOOLS}

    def desc(name: str) -> str:
        return descriptions.get(name, "")

    return [
        _wrap(ba.tool_1_open_browser, desc("tool_1_open_browser"), NoArgs),
        _wrap(ba.tool_2_interact, desc("tool_2_interact"), InteractArgs),
        _wrap(ba.tool_3_get_viewport_dom, desc("tool_3_get_viewport_dom"), NoArgs),
        _wrap(ba.tool_4_list_tabs, desc("tool_4_list_tabs"), NoArgs),
        _wrap(ba.tool_5_switch_tab, desc("tool_5_switch_tab"), SwitchTabArgs),
        _wrap(ba.tool_6_go_back, desc("tool_6_go_back"), NoArgs),
        _wrap(ba.tool_7_refresh, desc("tool_7_refresh"), NoArgs),
        _wrap(ba.tool_8_close_tab, desc("tool_8_close_tab"), CloseTabArgs),
        _wrap(ba.tool_9_navigate, desc("tool_9_navigate"), NavigateArgs),
        _wrap(ba.tool_10_tab_url_map, desc("tool_10_tab_url_map"), NoArgs),
        _wrap(ba.tool_11_interact_many, desc("tool_11_interact_many"), InteractManyArgs),
    ]


def browser_status() -> tuple[bool, int | None]:
    """返回 (是否已连接, port)。

    连接判定：``config.json`` 的 ``last_port`` 能应答 CDP 的 ``/json/version``。
    """
    from browser_agent.config import load_config
    from browser_agent.launcher import BrowserLauncher

    cfg = load_config()
    port = cfg.get("last_port")
    if isinstance(port, int) and BrowserLauncher._is_port_ready(port):
        return True, port
    return False, (port if isinstance(port, int) else None)
