"""JSON schemas / descriptions for the LLM tool surface."""

from __future__ import annotations

TOOLS = [
    {
        "name": "tool_1_open_browser",
        "description": (
            "打开（或复用）用户的 Chrome/Edge 浏览器，返回当前聚焦标签页的完整 DOM 基线。"
            "这是会话的第一个调用。返回 dict 字段：tabs、focused_tab、mode(全量模式)、"
            "action_ok(未进行互动元素调用)、error(无)、content(序列化 DOM)。"
            "tabs 为标签页名称列表，每个名称形如『标题001』（标题+三位全局递增序号）；"
            "focused_tab 为当前聚焦标签页名称。名称到 url 的映射用 tool_10 获取。"
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "tool_2_interact",
        "description": (
            "对 DOM 中标记出的单个互动元素执行互动，并返回相对上一步的增量 DOM(diff)。"
            "元素类别由序列化标签给出：<可点击元素 eN> / <可输入元素 eN> / "
            "<可拖动元素 eN> / <可滚动元素 eN>。"
            "若某个 [可滚动元素 eN] 下标注『整页滚动条』，表示它是文档级整页滚动，"
            "调用 tool_2 传入该名称即可把整个页面向上/向下滚动。"
            "成功时 mode=增量模式，content 只包含新增/变化的内容（每行带其祖先分组头，"
            "不含 +/- 前缀）；若互动后有可互动元素失效，结尾附『[丢失的可互动元素列表]』，"
            "列出不再可用的元素名。若焦点标签页改变则 mode=全量模式并附带 tabs；"
            "元素已失效时 action_ok=失败、error='该互动元素已经不存在于viewport中了'、content='无'。"
            "若该互动改变了当前标签页的标题，content 首行会以『[标签页标题变更] 旧名 → 新名』提示。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "互动元素名称，如 'e4'（来自序列化标签）。",
                },
                "fill": {
                    "type": "string",
                    "description": "仅用于『可输入』元素的填充内容；其他情况传空串 ''。",
                },
                "drag_pct": {
                    "type": "integer",
                    "description": "仅用于『可拖动』元素的目标位置百分比(0-100)；其他情况传 0。",
                    "minimum": 0,
                    "maximum": 100,
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "tool_3_get_viewport_dom",
        "description": (
            "重新返回当前 viewport 的完整 DOM（当多次互动元素引用失效时使用）。"
            "返回 dict 与 tool_1 一致，mode=全量模式。"
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "tool_4_list_tabs",
        "description": (
            "返回当前所有已打开标签页的名称列表。返回 dict：{tabs: ['标题001', '标题002', ...]}。"
            "名称 = 标题 + 三位全局递增序号；序号永不复用。名称对应的 url 用 tool_10 查询。"
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "tool_5_switch_tab",
        "description": (
            "切换到指定标签页，返回该标签页的完整 DOM。tab_id 传 tool_4/tool_10 返回的标签页名称"
            "（如 '百度001'）。返回 dict 与 tool_3 一致，mode=全量模式。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tab_id": {
                    "type": "string",
                    "description": "目标标签页名称，如 '百度001'（来自 tool_4/tool_10）。",
                }
            },
            "required": ["tab_id"],
        },
    },
    {
        "name": "tool_6_go_back",
        "description": (
            "浏览器内置『返回』：回到当前标签页的上一个历史记录，返回完整 DOM。"
            "无历史可回退时 action_ok=失败。返回 dict 与 tool_3 一致，mode=全量模式。"
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "tool_7_refresh",
        "description": (
            "刷新当前标签页，返回相对刷新前的增量 DOM(diff)。"
            "成功时 mode=增量模式，content 只包含新增/变化的内容（带祖先层级，无 +/- 前缀），"
            "若有可互动元素失效则结尾附『[丢失的可互动元素列表]』。"
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "tool_8_close_tab",
        "description": (
            "关闭指定标签页，返回剩余标签页名称列表。返回 dict：{tabs: ['标题001', ...]}。"
            "tab_id 传标签页名称（如 '百度001'）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tab_id": {
                    "type": "string",
                    "description": "要关闭的标签页名称，如 '百度001'（来自 tool_4/tool_10）。",
                }
            },
            "required": ["tab_id"],
        },
    },
    {
        "name": "tool_9_navigate",
        "description": (
            "新开一个标签页并访问给定地址（相当于在地址栏输入并回车）。"
            "返回新标签页的完整 DOM，dict 与 tool_3 一致，mode=全量模式。"
            "未带协议时自动补 https://。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "目标地址，如 'example.com' 或完整 URL。"}
            },
            "required": ["url"],
        },
    },
    {
        "name": "tool_10_tab_url_map",
        "description": (
            "返回当前所有已打开标签页的名称到实际 url 的映射。返回 dict，如 "
            "{'百度001': 'https://www.baidu.com/', '百度002': 'https://www.baidu.com/'}。"
            "当需要知道某个标签页名称对应哪个网址时调用。"
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "tool_11_interact_many",
        "description": (
            "一次调用按顺序完成『一系列填入 + 可选的最后一个点击』（拟人化串行执行，不并行）。"
            "name_list 为互动元素名称列表，fill_list 为对应的填入内容列表。"
            "两者等长时所有元素必须是『可填入元素』；name_list 比 fill_list 多一个时，"
            "最后一个必须是『可点击元素』（收尾点击）。"
            "执行前会先校验全部元素；校验失败则 action_ok=失败 且不执行任何操作。"
            "执行中途某元素失败：action_ok=部分失败（若第一个就失败则为失败），error 写明原因，"
            "并从失败点起放弃后续操作，content 为已成功部分的增量 diff。"
            "全部成功时 content 只包含新增/变化的内容（带祖先层级，无 +/- 前缀），"
            "若有可互动元素失效则结尾附『[丢失的可互动元素列表]』；若最后点击导致焦点标签页改变，"
            "则 mode=全量模式并附带 tabs。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name_list": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "互动元素名称列表，如 ['e4', 'e5', 'e6']；最后一个可选为可点击元素。",
                },
                "fill_list": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "填入内容列表；与 name_list 等长，或比 name_list 少一个"
                        "（少一个时 name_list 末位为收尾点击）。"
                    ),
                },
            },
            "required": ["name_list", "fill_list"],
        },
    },
]
