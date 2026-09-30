"""浏览器接管控制器（``controller.py``）对外返回的全部文本。

本模块把 ``BrowserController`` 中**直接返回给上层调用方、并最终会进入 LLM 上下文**
的文本集中到这里，便于统一审阅 / 修改提示词，而不用在控制逻辑里四处翻找。

约定：
- 固定不变的文本 → 模块级常量（全大写）；
- 需要按运行时值拼装的文本 → 函数，入参为拼装所需的原始值，返回最终字符串。

每条文本前都用注释说明：作用、触发条件、返回时机，以及返回给谁。
"""

from __future__ import annotations


# ════════════════════════════════════════════════════════════════════════
# 一、结果字典的状态字段（这些值会作为 ``_base_result`` 的 mode / action_ok /
#    error 字段，随每次工具调用的结果原样返回给上层调用方，并进入 LLM 上下文）
# ════════════════════════════════════════════════════════════════════════

# mode：本次结果是「完整 DOM」还是「相对上一次的增量 diff」。
# 返回时机：full_dom / switch_tab / go_back / navigate / 互动导致聚焦标签页切换时用 FULL；
# 其余普通互动 / 刷新用 INCREMENTAL。
FULL = "全量模式"
INCREMENTAL = "增量模式"

# action_ok：本次操作是否成功。
# 返回时机：操作正常完成 → OK；前置校验失败或动作抛错 → FAIL；
# interact_many 中途部分动作失败 → PARTIAL_FAIL。
OK = "成功"
FAIL = "失败"
PARTIAL_FAIL = "部分失败"

# action_ok：full_dom 被「打开浏览器 / 获取视窗全貌」这类非互动调用触发时的占位值，
# 表示本次并不是一次「互动元素」操作（app 层「打开浏览器」按钮也复用它）。
NOT_CALLED = "未进行互动元素调用"

# error：没有错误时 error 字段显示的占位文本。
# 返回时机：_base_result 的 error 默认值，以及各工具在 fill 有错误时用 ``fill_error or NO_ERROR``。
NO_ERROR = "无"

# content：本次没有任何可展示内容时 content 字段显示的占位文本。
# 返回时机：各类前置校验失败（无标签页 / 未知元素 / 动作抛错等）时，content 无内容可给，
# 统一回传该占位符，真正的说明放在 error 字段里。
EMPTY_CONTENT = "无"


# ════════════════════════════════════════════════════════════════════════
# 二、元素类别 / 动作的中文标签（用于拼装各类错误提示）
# ════════════════════════════════════════════════════════════════════════

# 把内部互动类别映射为面向 LLM 的中文类别名；未知类别回退为「不可互动」。
# 使用场景：元素类别与所调用工具不匹配时的错误提示。
def category_label(category: str) -> str:
    return {
        "input": "可输入",
        "searchable": "可搜索下拉",
        "select": "可选择",
        "click": "可点击",
        "clickdropdown": "可点击下拉",
        "drag": "可拖动",
        "scroll": "可滚动",
    }.get(category, "不可互动")


# 把内部互动类别映射为其对应的操作动词（该工具正在尝试做的动作）。
# 使用场景：元素类别与所调用工具不匹配时，提示「不能用于 XX 操作」。
def action_label(category: str) -> str:
    return {
        "click": "点击",
        "input": "输入",
        "searchable": "可搜索下拉筛选",
        "select": "下拉选择",
        "clickdropdown": "点击下拉",
        "scroll": "滚动",
        "drag": "拖动",
    }.get(category, "")


# ════════════════════════════════════════════════════════════════════════
# 三、页面空 / 骨架 / 无变化的通用提示（作为 content 前缀直接返回给 LLM）
# ════════════════════════════════════════════════════════════════════════

# 作用：提示当前页面几乎空白（尚未渲染出正文），建议稍后重试获取全量 DOM。
# 触发条件：抓到的树只有 <html><head>…</head></html> 这类骨架，没有可见正文。
# 返回时机：full_dom / switch_tab / go_back / navigate / 互动后聚焦标签页切换等
#           全量返回场景，若判定为「空白骨架」，把本条前缀拼到序列化 DOM 之前。
# 注意：与 ABOUT_BLANK_NOTICE 的区分由控制器的 _blank_notice(tree) 依据 URL 选择。
BLANK_SHELL_NOTICE = (
    "[页面可能仍在加载] 当前页面几乎为空（尚未渲染出正文）。"
    "请稍等片刻后重试 tool_10_get_full_viewport。\n\n"
)

# 作用：提示聚焦标签页停在 about:blank（网址未加载 / 被反爬重置为空白页）。
# 触发条件：树的 URL 为空或等于 "about:blank"。
# 返回时机：与 BLANK_SHELL_NOTICE 相同，但用于「明确是空白页」而非「仍在加载」的情况，
#           避免误导 LLM 反复重试同一张死页。
ABOUT_BLANK_NOTICE = (
    "[页面为空] 当前聚焦标签页停在 about:blank，没有正文。"
    "常见原因：①该网址未能加载或被重定向到空白页；"
    "②网站脚本/反爬在加载后把页面重置成了空白页。"
    "请稍候重试 tool_10_get_full_viewport，或用 tool_33_navigate 重新打开目标网址。\n\n"
)

# 作用：滚动（或 diff）后确实没有任何新内容的通用占位。
# 触发条件：滚动/内容比对后没有新增行。
# 返回时机：_scroll_and_collect 无新增内容且已到达边界或无移动时；以及控制器在判断
#           「当前聚焦页无变化」时用于检测 content 里是否包含该串（所以必须保持字面一致）。
PAGE_NO_CHANGE = "（页面无变化）"


# 作用：点击后前后 DOM 签名完全一致（死点击）时的诚实说明，防止 LLM 把「无变化」当成成功。
# 触发条件：点击（含一次重试）后 hover 中性的 DOM 签名逐字节一致，且没有新文本、没有新标签页。
# 返回时机：_finish_interaction 中判定为 click_noop 时，作为 content 前缀返回给 LLM。
CLICK_NOOP_NOTICE = (
    "[提示] 本次点击没有产生任何页面变化（前后 DOM 完全一致）。"
    "常见原因：①该元素只是文字/装饰，真正的控件是它紧邻的图标（单选/复选圈），"
    "请改点相邻的图标控件；②该控件处于只读/禁用状态；③操作需要先满足某个前置条件。"
    "请勿据此认为操作已生效；可换一个元素重试，或改用其它方式。"
)


# 作用：同一元素被连续多次点击且每次都零变化时的升级提示，阻止 LLM 无限重试
#       （真实会话里 LLM 对一个只有 cursor:pointer 的步骤指示元素连点 5 次）。
# 触发条件：控制器按元素 key 统计到连续 no-op 次数 >= 2。
# 返回时机：_finish_interaction 判定为 click_noop 时，作为 content 前缀返回给 LLM。
# 入参：name 为元素名（eN）；count 为连续零变化次数。
def click_noop_notice(name: str, count: int) -> str:
    if count < 2:
        return CLICK_NOOP_NOTICE
    return (
        f"[提示] 元素 {name} 已被连续点击 {count} 次，每次前后 DOM 均完全一致（零变化）。"
        "它极可能不是真实控件（如步骤指示 / 纯装饰文字），或需要先满足某个前置条件；"
        "请不要再点击它，改用其它元素或换一种思路。"
    )

# 作用：提示页面结构确实变了、只是没有「新增」的可见文本/互动元素（diff 只报新增，会显示
#       「页面无变化」而误导 LLM）。
# 触发条件：点击后页面无可见新增，但原始 outerHTML 签名发生了变化（如关闭浮层 / 取消选中）。
# 返回时机：_finish_interaction 的点击后处理分支，直接追加到 content 末尾。
STRUCTURE_CHANGED_NOTICE = (
    "\n\n[提示] 页面结构确实发生了变化，但没有新增的可见文本/互动元素"
    "（常见于：关闭了浮层/下拉、取消选中，或仅状态/样式变化）。"
)


# ════════════════════════════════════════════════════════════════════════
# 四、标签页 / 导航相关提示
# ════════════════════════════════════════════════════════════════════════

# 作用：告知请求的标签页未能切到前台，当前实际聚焦的是另一个标签页，并给出后续可选操作。
# 触发条件：switch_tab / navigate 后，浏览器实际聚焦的标签页与请求的不一致。
# 返回时机：作为全量 content 的前缀返回给 LLM（真正聚焦页的 DOM 接在其后）。
# 入参：requested / actual 为已解析好的标签页显示名。
def focus_mismatch_notice(requested: str, actual: str) -> str:
    return (
        f"[无法切换标签页] 未能把「{requested}」切到前台，浏览器当前仍聚焦"
        f"「{actual}」。可稍后重试 tool_21_switch_tab 切换到「{requested}」，"
        f"或用 tool_32_close_tab 关闭「{actual}」，"
        f"或用 tool_33_navigate 打开目标页面后再继续操作。\n\n"
    )


# 作用：提示「本次互动在当前聚焦标签页之外新开了标签页，但聚焦页未变」，引导 LLM 切换过去。
# 触发条件：点击/批量操作后发现新增了标签页，而聚焦标签页没有改变。
# 返回时机：_finish_interaction / interact_many 中 opened_names 非空时，作为（或追加到）content。
# 入参：opened 为已用「、」连接的标签页名串；focus 为当前聚焦标签页名（空则显示「未知标签页」）。
def new_tab_notice(opened: str, focus: str) -> str:
    focus = focus or "未知标签页"
    return (
        f"[新标签页] 本次互动在当前聚焦标签页之外新打开了标签页（{opened}），"
        f"但当前聚焦标签页并未改变（仍为 {focus}），因此当前聚焦页没有内容变化或只有少量内容变化。"
        f"如需查看或操作新标签页，使用 tool_21_switch_tab 切换到目标标签页。"
    )


# 作用：告知当前标签页的标题发生了变化（常用于页面跳转后的辨识）。
# 触发条件：操作前后标签页显示名发生变化。
# 返回时机：作为全量/增量 content 的前缀（_run_scroll / _finish_interaction / interact_many /
#           go_back / refresh）。old_name 为空时返回空串表示不提示。
def title_change_notice(old_name: str, new_name: str) -> str:
    if not old_name:
        return ""
    if new_name and new_name != old_name:
        return f"[标签页标题变更] {old_name} → {new_name}\n"
    return ""


# 作用：告知指定的标签页序号不存在，无法切换 / 关闭。
# 触发条件：switch_tab / close_tab 找不到对应标签页。
# 返回时机：作为 error 字段返回给调用方。
def tab_not_found(tab_id: str) -> str:
    return f"未找到标签页 {tab_id}"


# 作用：告知当前没有可用的标签页（无法进行任何操作）。
# 触发条件：_resolve_focused_target 返回 None 时（全量获取 / 互动 / 刷新等）。
# 返回时机：作为 error 字段；也用于 capture_tree 抛出的 RuntimeError。
NO_TABS = "没有可用的标签页"

# 作用：告知浏览器没有可回退的历史记录，back 失败。
# 触发条件：go_back 时当前历史索引为 0（或越界）。
# 返回时机：作为 error 字段返回。
GO_BACK_NO_HISTORY = "无法返回：没有可回退的历史记录"

# 作用：告知 navigate 的 URL 参数为空，无法打开。
# 触发条件：navigate 收到空字符串。
# 返回时机：作为 error 字段返回。
EMPTY_URL = "地址为空"


# 作用：告知新标签页已创建但暂时无法 attach/连接，属于可恢复错误，可稍后重试。
# 触发条件：navigate 新建标签页后 attach 时抛 CDPError。
# 返回时机：作为 error 字段返回（同时附上标签页列表，便于 LLM 重新定位）。
# 入参：exc 为底层异常（或异常信息）。
def new_tab_unreachable(exc) -> str:
    return f"新标签页已创建但暂时无法连接：{exc}"


# ════════════════════════════════════════════════════════════════════════
# 五、互动元素定位 / 类别不匹配的错误提示
# ════════════════════════════════════════════════════════════════════════

# 作用：告知该元素名在当前聚焦标签页中未知（可能属于另一个标签页），并引导切换标签页。
# 触发条件：registry 中查不到该名字时。
# 返回时机：互动前置校验失败，作为 error 字段返回（在 _unknown_name_message 中调用）。
# 入参：focus 当前聚焦标签页名（空显示「未知」）；other 拥有该名字的其它标签页名（空表示没有）。
def unknown_name_message(name: str, focus: str, other: str) -> str:
    if other:
        return (
            f"互动元素 {name} 不属于当前聚焦标签页（{focus or '未知'}），"
            f"而属于「{other}」；请先用 tool_21_switch_tab 切换到该标签页再互动。"
        )
    return f"未知的互动元素名称 {name}"


# 作用：告知该元素名曾经存在、但当前已失效（不在当前 viewport 里了）。
# 触发条件：registry 里有该名字但已不 active，或树中已找不到对应节点。
# 返回时机：互动前置校验 / 批量校验失败，作为 error 字段返回。
# 入参：同 unknown_name_message。
def stale_name_message(name: str, focus: str, other: str) -> str:
    if other:
        return (
            f"互动元素 {name} 在当前聚焦标签页（{focus or '未知'}）中已不存在，"
            f"它属于「{other}」；如需继续，请先用 tool_21_switch_tab 切换。"
        )
    return "该互动元素已经不存在于viewport中了"


# 作用：告知该元素的实际类别与所调用工具不符，并（若已知）指出应改用哪个工具。
# 触发条件：_begin_interaction 中 classify(node) 不在调用工具允许的类别集合内。
# 返回时机：作为 error 字段返回给 LLM。
# 入参：category 元素真实类别；attempted_category 调用工具对应的类别；
#       target_tool 该类别应使用的工具名（空则不提示改用建议）。
def category_mismatch(
    name: str, category: str, attempted_category: str, target_tool: str
) -> str:
    hint = f"；请改用 {target_tool} 来操作它" if target_tool else ""
    return (
        f"互动元素 {name} 是「{category_label(category)}」类，"
        f"不能用于{action_label(attempted_category)}操作{hint}。"
    )


# 作用：interact_many 前置校验中，告知某个元素不是可填入元素。
# 触发条件：批量填值列表里的元素类别不是 input / select。
# 返回时机：作为 error 字段返回（批量操作在执行前整体失败）。
def not_fillable(name: str, category: str) -> str:
    hint = (
        "；可搜索下拉请改用 tool_07_searchable_dropdown 操作"
        if category == "searchable"
        else ""
    )
    return (
        f"互动元素 {name} 不是可填入元素（它是「{category_label(category)}」类）{hint}"
    )


# 作用：interact_many 中，告知被当作「收尾点击」的多余元素其实不是可点击元素，并说明如何处理。
# 触发条件：name_list 比 fill_list 多一个时，最后一个元素类别不是 click。
# 返回时机：作为 error 字段返回（批量操作整体不执行）。
def trailing_click_not_clickable(click_name: str, category: str) -> str:
    return (
        f"name_list 比 fill_list 多一个时，多余的那个（{click_name}）会被当作"
        f"收尾点击；但 {click_name} 是「{category_label(category)}」类，"
        "不是可点击元素。若它也需要填写，请为它补一个 fill_list 值；"
        "若不操作它，请把它从 name_list 去掉。"
    )


# ════════════════════════════════════════════════════════════════════════
# 六、填值 / 下拉选择相关提示
# ════════════════════════════════════════════════════════════════════════

# 作用：告知日期/时间选择控件无法用 fill 写入非日期文本，并给出正确处理方式。
# 触发条件：_run_fill / 批量校验中，元素是日期类控件且填入的文本不含数字（如「至今」）。
# 返回时机：作为 fill_error（错误的 error 字段）或批量失败汇总的一部分返回。
# 入参：current 为 None 表示不附带「当前值」；否则（含空串）会附带。
def date_fill_error(name: str, fill: str, current=None) -> str:
    got = f"，控件当前值为「{current or '空'}」" if current is not None else ""
    return (
        f"元素 {name} 是日期/时间选择控件，无法用 fill 写入「{fill}」这类非日期文本{got}。"
        f"该控件只接受具体日期，页面上通常也没有『至今』这种选项；"
        f"请改为点击它并在弹出的日历里选择具体日期，"
        f"或先与用户确认该字段如何处理（例如选当天日期、或留空）。"
    )


# 作用：告知普通可输入元素的填值未生效（只读 / 日期选择器等），并给出替代做法。
# 触发条件：_run_fill 中填完读回，发现控件当前值与填入值不一致。
# 返回时机：作为 fill_error 返回。
def fill_not_effective(name: str, fill: str, current) -> str:
    return (
        f"元素 {name} 的填充未生效：填入了「{fill}」，"
        f"但控件当前值为「{current or '空'}」。"
        f"该控件可能是只读、或日期/时间选择器，无法用 fill 直接写入；"
        f"请点击它之后在弹出的选择器里选择，或改为对其它可输入元素填值。"
    )


# 作用：拒绝对 readonly 控件写入，并说明它是「只显示值、由自己的控件提交」的字段。
# 触发条件：_run_fill 中 fill 非空且 interaction_state 报告 readonly（含 aria-readonly）。
# 返回时机：作为 fill_error 返回；本次调用不触碰页面，避免「显示值被改、真实值没改」。
def readonly_fill_error(name: str, fill: str) -> str:
    return (
        f"元素 {name} 是只读控件（readonly），它只显示由自身控件提交的值，"
        f"不能直接填入「{fill}」。直接写入会让页面显示的文字和真实提交值不一致。"
        f"请改为点击它、在弹出的候选/日历/级联面板里选择；"
        f"若该控件没有任何弹层入口，请与用户确认该字段如何填写。"
    )


# 作用：告知「清空」未能生效，字段里仍留着旧值。
# 触发条件：_run_fill 收到空 fill（清空语义）且 clear_field 复核后字段仍非空。
# 返回时机：作为 fill_error 返回。
def clear_fill_error(name: str) -> str:
    return (
        f"元素 {name} 的清空未生效：控件被页面重新填回了原值，无法用 fill 置空。"
        f"该控件可能是只读、或由组件库托管的受控字段。"
        f"请改为点击它并在弹出的选择器里操作，或与用户确认该字段是否必须留空。"
    )


# 作用：告知「先清空再输入」中的清空这一步失败，字段现在是被拼接污染的脏值。
# 触发条件：_run_fill 中 input_text 报告 clear_ok=False（清空后字段仍非空）。
# 返回时机：作为 fill_error 返回（此时报告成功会让 LLM 在错误前提上继续操作）。
def fill_clobbered_error(name: str, fill: str, current) -> str:
    return (
        f"元素 {name} 的旧值未能清除，本次填入「{fill}」会与旧值拼在一起"
        f"（控件当前值为「{current or '空'}」）。"
        f"为避免写坏字段，本次未按「覆盖」处理。"
        f"该控件由组件库托管（常见于日期/月份区间选择器）："
        f"请改为点击它并在弹出的面板里选择，不要再用 fill 覆盖。"
    )


# 作用：告知原生 <select> 的选项没有匹配上，列出可选值，并说明只能从已有项中选。
# 触发条件：_run_select / 批量校验中，select_option 后读回的值与目标不匹配。
# 返回时机：作为 fill_error / 批量失败汇总的一部分返回。
# 入参：options 为可选值文字列表（空则显示「（无可选项）」）。
def select_fill_error(name: str, fill: str, current, options: list) -> str:
    opts = "、".join(options) if options else "（无可选项）"
    return (
        f"元素 {name} 是下拉选择控件，未能选中「{fill}」"
        f"（控件当前值为「{current or '未选择'}」）。"
        f"该控件只能从已有选项中选一个：请用 fill 传入下方某一项的文字。"
        f"当前可选值：{opts}。若目标值不在其中，请先与用户确认该字段如何填写。"
    )


# 作用：当对原生下拉只点击、没给 fill 时，解释「点击会打开工具无法捕获的原生弹层」，
#       并引导用 fill 指定选项文字，同时列出可选值。
# 触发条件：_run_select 中 fill 为空。
# 返回时机：作为 fill_notice 返回。
def select_click_notice(name: str, options: list) -> str:
    opts = "、".join(options) if options else "（无可选项）"
    return (
        f"[提示] 元素 {name} 是原生下拉选择控件：点击它只会展开浏览器/系统原生下拉层，"
        f"该弹层不在 DOM 中、工具无法捕获，因此不会产生任何可见变化。"
        f"请直接用 fill 参数指定要选择的选项文字。当前可选值：{opts}。"
    )


# 作用：批量填值后汇总「哪些元素填充未生效」及各自当前值，并给出处理建议。
# 触发条件：interact_many 执行后校验发现填值未落地。
# 返回时机：作为 error 字段（action_ok=部分失败）返回。
# 入参：items 为 [(元素名, 当前值或 None)]。
def failed_fills_message(items: list) -> str:
    return (
        "以下元素的填充未生效："
        + "、".join(f"{n}（当前值：{c or '空'}）" for n, c in items)
        + "。它们可能是只读、或日期/时间选择器，无法用 fill 直接写入"
        "（日期选择器通常也不接受『至今』这类非日期文本）；"
        "请点击它之后在弹出的日历里选择具体日期，"
        "或先与用户确认该字段如何处理，或改用其它可输入元素。"
    )


# 作用：批量操作后汇总「哪些下拉选择未能选中」，内容由各条 select_fill_error 拼成。
# 触发条件：interact_many 执行后校验发现 select 未选中。
# 返回时机：作为 error 字段（action_ok=部分失败）返回。
# 入参：errors 为已生成的单条说明列表。
def failed_selects_message(errors: list) -> str:
    return "以下下拉选择未能选中：" + "；".join(errors)


# ════════════════════════════════════════════════════════════════════════
# 七、滚动相关提示
# ════════════════════════════════════════════════════════════════════════

# 作用：告知滚动没有产生新内容，并解释原因（已到边界 / 该元素不可滚动）。
# 触发条件：_scroll_and_collect 没有任何新增行，且（未移动或已到边界）。
# 返回时机：作为滚动工具的 content 直接返回。
# 入参：down=True 表示向下滚动（边界显示「底部」，否则「顶部」）。
def scroll_no_new_content(down: bool) -> str:
    where = "底部" if down else "顶部"
    return f"（已到{where}或该元素当前不可滚动：没有新的可见内容）"


# 作用：告知已经滚动到边界、但仍没有新增可见内容。
# 触发条件：_scroll_and_collect 有移动、已到边界，但没有新增行。
# 返回时机：作为滚动工具的 content 直接返回。
def scroll_at_boundary(down: bool) -> str:
    where = "底部" if down else "顶部"
    return f"（已滚动到{where}，但没有新增可见内容）"


# 作用：告知滚动未能生效（既没到边界、也没产生新内容），提示页面拦截了本次滚动。
# 触发条件：_scroll_and_collect 第一步就没有移动，且确认并未到达滚动边界。
# 返回时机：作为滚动工具的 content 直接返回。
# 入参：down=True 表示本意向下滚动。
def scroll_blocked(down: bool) -> str:
    where = "下" if down else "上"
    return (
        f"（本次向{where}滚动未生效：页面未移动也未到边界，可能是被浮层/滚动锁拦截；"
        "并非已到内容尽头。可稍后重试，或改用工具返回的其它<可滚动元素>）"
    )


# 作用：告知本次互动因「把目标滚进视口」而自动滚动了整页，提醒增量里混有滚动副作用。
# 触发条件：_finish_interaction 发现文档 scrollY 前后变化超过阈值。
# 返回时机：作为增量 content 的前缀。
def viewport_scrolled(before: float, after: float) -> str:
    return (
        f"[视口已自动滚动] 为让目标元素可见，整页由 scrollY={int(before)} 滚到 "
        f"scrollY={int(after)}。下列增量里可能同时含有『本操作直接产生的内容』与"
        "『因滚动新进入视口、与本操作无关的内容』；请勿把后者当作本操作的结果。"
    )


# ════════════════════════════════════════════════════════════════════════
# 八、原生对话框提示
# ════════════════════════════════════════════════════════════════════════

# 作用：告知页面弹出了原生 JS 对话框（alert/confirm/prompt），已自动处理，并回显对话框文字。
# 触发条件：操作期间捕获到 Page.javascriptDialogOpening 且类型不是 beforeunload。
# 返回时机：作为 content 前缀返回（beforeunload 不提示）。
# 入参：seen 为控制器记录的 {"type": ..., "message": ...}。
def dialog_notice(seen: dict) -> str:
    dtype = (seen or {}).get("type") or ""
    if not dtype:
        return ""
    if dtype == "beforeunload":
        return ""
    message = (seen.get("message") or "").strip()
    labels = {"alert": "警告", "confirm": "确认", "prompt": "输入"}
    return (
        f"[原生对话框] 页面弹出了{labels.get(dtype, dtype)}框"
        + (f"：「{message}」" if message else "")
        + "（已自动处理）。\n\n"
    )


# ════════════════════════════════════════════════════════════════════════
# 九、批量互动（tool_06）的参数校验提示
# ════════════════════════════════════════════════════════════════════════

# 作用：告知 name_list 为空，批量操作无法进行。
# 触发条件：interact_many 收到空 name_list。
# 返回时机：作为 error 字段返回。
INTERACT_MANY_EMPTY = "name_list 不能为空"

# 作用：告知 name_list 与 fill_list 长度不合法，并说明本工具不支持连续点击多个元素。
# 触发条件：length(fill_list) 既不等于 length(name_list)、也不等于 length(name_list)-1。
# 返回时机：作为 error 字段返回。
INTERACT_MANY_LENGTH = (
    "name_list 与 fill_list 长度不合法（应等长，或 name_list 比 fill_list 多一个）。"
    "本工具不支持连续点击多个元素；若要连续点击，请对每个元素分别调用 tool_01_click。"
)
