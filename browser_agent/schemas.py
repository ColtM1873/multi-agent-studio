"""JSON schemas / descriptions for the LLM tool surface.

Numbering is 0-based: ``tool_1_interact`` .. ``tool_10_interact_many``.
``tool_0_open_browser`` is intentionally absent — it is a control-plane tool for
the Studio UI and is never exposed to the LLM.
"""

from __future__ import annotations

TOOLS = [
    {
        "name": "tool_1_interact",
        "description": (
            "对 DOM 中标记出的单个互动元素执行互动，并返回相对上一步的增量 DOM(diff)。"
            "元素类别由序列化标签给出：<可点击元素 eN> / <可输入元素 eN> / "
            "<可选择元素 eN> / <可拖动元素 eN> / <可滚动元素 eN>。"
            "『可选择元素』是原生下拉框（记住：它点击只会展开浏览器/系统原生层、"
            "不会产生可见变化）：对它传 fill = 要选择的选项文字即可选中该项；"
            "若所填文字不在候选中会判失败并在 error 里列出该下拉的可选值。"
            "若某处出现『整页滚动条』的 <可滚动元素 eN>，表示它是文档级整页滚动，"
            "调用 tool_1 传入该名称并用 scroll_delta 指定方向即可滚动整个页面。"
            "对『可滚动元素 eN』用 scroll_delta 指定滚动：正数向下、负数向上，单位为『步』"
            "（每步约 0.7 个可见高度，相邻视窗重叠、不会跳过内容），绝对值上限 6；"
            "传 0（默认）等同向下 6 步。"
            "成功时 mode=增量模式，content 只包含新增/变化的内容；"
            "若互动后有  之前出现过的  可互动元素，互动之后已经不可访问，则结尾会附带『[丢失的可互动元素列表]』，"
            "列出不再可用的元素名。若焦点标签页改变则 mode=全量模式并附带 tabs；"
            "若点击在后台新打开了标签页、但当前聚焦标签页未改变，则 content 为一条『[新标签页]』"
            "提示文本（说明新标签页名与当前聚焦页未变），并附带完整 tabs 标签页列表，"
            "此时 mode=增量模式；元素已失效时 action_ok=失败、"
            "error='该互动元素已经不存在于viewport中了'、content='无'。"
            "若对『可输入元素』传入 fill、但控件实际未改变（例如只读字段、"
            "日期/时间选择器），action_ok=失败、error 会说明该填充未生效，content 仍为 diff。"
            "若搜索框没有可点击的提交按钮、只能靠回车提交，"
            "对『可输入元素』传 fill 时把 press_enter 设为 true，工具会在填入后按一次回车并等待结果。"
            "特别注意：对日期/时间选择器填入不含数字的文本（如『至今』『present』）会直接判失败"
            "——这类控件只接受具体日期、通常没有『至今』选项，请改用点击后弹出的日历选择，"
            "或先询问用户如何处理该字段。"
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
                    "description": (
                        "仅用于『可输入』或『可选择』元素：前者是填入文本，"
                        "后者是选择该下拉中文字匹配的选项；其他情况传空串 ''。"
                    ),
                },
                "drag_pct": {
                    "type": "integer",
                    "description": "仅用于『可拖动』元素的目标位置百分比(0-100)；其他情况传 0。",
                    "minimum": 0,
                    "maximum": 100,
                },
                "scroll_delta": {
                    "type": "integer",
                    "description": (
                        "仅用于『可滚动』元素：正数向下滚、负数向上滚，单位为步"
                        "（每步约 0.7 个可见高度），绝对值上限 6；其他情况传 0"
                        "（对可滚动元素传 0 等同向下 6 步）。"
                    ),
                    "minimum": -6,
                    "maximum": 6,
                },
                "press_enter": {
                    "type": "boolean",
                    "description": (
                        "仅用于『可输入』元素：填完值后是否按一次回车以提交搜索/表单"
                        "（当搜索框没有可点击的提交按钮时设为 true）；其他情况传 false（默认）。"
                    ),
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "tool_2_get_viewport_dom",
        "description": (
            "重新返回当前 viewport 的完整 DOM（当多次互动元素引用失效时使用）。"
            "返回 dict 与打开浏览器后的全量返回一致，mode=全量模式。"
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "tool_3_list_tabs",
        "description": (
            "返回当前所有已打开标签页的名称列表。返回 dict：{tabs: ['标题001', '标题002', ...]}。"
            "名称 = 标题 + 三位全局递增序号；序号永不复用。名称对应的 url 用 tool_9 查询。"
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "tool_4_switch_tab",
        "description": (
            "切换到指定标签页，返回该标签页的完整 DOM。"
            "tab_id 只填标签页名称末尾的三位数字序号（例如标签页名为 '百度001' 时只填 '001'）。"
            "返回 dict 与 tool_2 一致，mode=全量模式。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tab_id": {
                    "type": "string",
                    "description": "标签页的数字序号，如 '001'（取自 tool_3/tool_9 返回名称末尾的数字）。",
                }
            },
            "required": ["tab_id"],
        },
    },
    {
        "name": "tool_5_go_back",
        "description": (
            "浏览器内置『返回』：回到当前标签页的上一个历史记录，返回完整 DOM。"
            "无历史可回退时 action_ok=失败。返回 dict 与 tool_2 一致，mode=全量模式。"
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "tool_6_refresh",
        "description": (
            "刷新当前标签页，返回相对刷新前的增量 DOM(diff)。"
            "成功时 mode=增量模式，content 只包含新增/变化的内容（带祖先层级，无 +/- 前缀），"
            "若有可互动元素失效则结尾附『[丢失的可互动元素列表]』。"
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "tool_7_close_tab",
        "description": (
            "关闭指定标签页，返回剩余标签页名称列表。返回 dict：{tabs: ['标题001', ...]}。"
            "tab_id 只填标签页名称末尾的三位数字序号（例如 '001'）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tab_id": {
                    "type": "string",
                    "description": "要关闭的标签页数字序号，如 '001'（取自 tool_3/tool_9 返回名称末尾的数字）。",
                }
            },
            "required": ["tab_id"],
        },
    },
    {
        "name": "tool_8_navigate",
        "description": (
            "新开一个标签页并访问给定地址（相当于在地址栏输入并回车）。"
            "返回新标签页的完整 DOM，dict 与 tool_2 一致，mode=全量模式。"
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
        "name": "tool_9_tab_url_map",
        "description": (
            "返回当前所有已打开标签页的名称到实际 url 的映射。返回 dict，如 "
            "{'百度001': 'https://www.baidu.com/', '百度002': 'https://www.baidu.com/'}。"
            "当需要知道某个标签页名称对应哪个网址时调用。"
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "tool_10_interact_many",
        "description": (
            "一次调用按顺序完成『一系列填入 + 可选的最后一个点击』（拟人化串行执行，不并行）。"
            "name_list 为互动元素名称列表，fill_list 为对应的填入内容列表。"
            "两者等长时所有元素必须是『可填入元素』（可输入元素或可选择元素——"
            "对可选择元素 fill 的是要选中的选项文字）；name_list 比 fill_list 多一个时，"
            "最后一个必须是『可点击元素』（收尾点击）。"
            "执行前会先校验全部元素；校验失败则 action_ok=失败 且不执行任何操作。"
            "执行中途某元素失败：action_ok=部分失败（若第一个就失败则为失败），error 写明原因，"
            "并从失败点起放弃后续操作，content 为已成功部分的增量 diff。"
            "执行完成后会校验每个 fill 是否真的写入；若有元素填入未生效（只读、"
            "日期/时间选择器、下拉无可匹配选项等），action_ok=部分失败、"
            "error 会逐一列出未生效的元素名与当前值（下拉还会列出其可选值）。"
            "对日期/时间选择器填入不含数字的文本（如『至今』）一律判为未生效。"
            "全部成功时 content 只包含新增/变化的内容（带祖先层级，无 +/- 前缀），"
            "若有可互动元素失效则结尾附『[丢失的可互动元素列表]』；若最后点击导致焦点标签页改变，"
            "则 mode=全量模式并附带 tabs；若最后点击在后台新打开了标签页、但当前聚焦标签页未改变，"
            "则 content 为一条『[新标签页]』提示文本，并附带完整 tabs 标签页列表，mode=增量模式。"
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
