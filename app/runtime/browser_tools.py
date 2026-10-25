import re  # BID084

from langchain_core.tools import tool as langchain_tool
from typing import Annotated

from browser_agent.controller import BrowserController, NOT_CALLED
from app.runtime.browser_tools_wrap_up import _wrap_up
from app.runtime.browser_tools_privacy_mask import apply_sensitive_replacement


# 本函数于BID084引入
def _normalize_elem_mark(value) -> str:
    """把 LLM 传来的元素引用归一成内部名 ``eN``。

    中文口径本来就传 ``e23``；英文渲染口径下工具描述要求直接传 ``23``。
    这里同时容忍 ``23`` / ``e23`` / ``E23`` / ``<Click elem 23>`` 等写法，
    避免「英文开关生效后旧写法失效」这类无谓的死锁。
    """
    text = str(value if value is not None else "").strip()
    if not text:
        return text
    match = re.fullmatch(r"[eE](\d+)", text)
    if match:
        return "e" + match.group(1)
    match = re.fullmatch(r"(\d+)", text)
    if match:
        return "e" + match.group(1)
    # 容忍整段标签：``<Click elem 23>`` / ``<可点击元素 e23>``。
    # 只取标签里的第一个数字（元素编号）；整合后的标签内容里也可能有数字
    # （``<可点击元素 e34 : 2024>``），绝不能被内容里的数字带偏。
    match = re.search(r"<[^<>]*?\be?(\d+)\b", text)
    if match:
        return "e" + match.group(1)
    return text


@langchain_tool
async def tool_01_click(
    elem_mark: Annotated[str, "<可点击元素 eN>的标识符eN。例如<可点击元素 e23>，这里就传入e23。"],
) -> str|dict:
    """用于点击当前页面中所有仍然生效的<可点击元素>。通常互动之后，会返回当前页面因为互动而产生的变化"""
    sync_func_here = BrowserController.instance().interact_click
    return await   _wrap_up(sync_func_here , name = _normalize_elem_mark(elem_mark))  # BID084


@langchain_tool
async def tool_02_type_in_content(
    elem_mark: Annotated[str, "<可输入元素 eN>的标识符eN"],
    type_in_cotent: Annotated[str, "你打算在这里输入的内容"],
    press_enter :Annotated[bool, "是否在输入完毕后按下回车。在搜索栏情况下，有可能是应用搜索；在文本栏情况下，有可能是普通换行。网页会如何响应取决于具体情况。默认值为False（不按）"] = False,
) -> str|dict:
    """用于在当前页面中所有仍然生效的<可输入元素>的位置，输入你需要输入的内容。
通常会返回这一次输入的内容本身"""
    sync_func_here = BrowserController.instance().interact_fill_in
    type_in_cotent = apply_sensitive_replacement(str(type_in_cotent))
    return await   _wrap_up(sync_func_here ,name =  _normalize_elem_mark(elem_mark), fill =  type_in_cotent ,press_enter = press_enter)  # BID084


@langchain_tool
async def tool_03_scroll(
    elem_mark: Annotated[str, "<可滚动元素 eN>的标识符eN。例如<可滚动元素 e23>，这里就传入e23。"],
    times_to_scroll: Annotated[str, "你打算滚动多少下，用负值调用是向上滚动，用正值调用是向下滚动，单次调用使用的最大绝对值是6"],
) -> str|dict:
    """页面中使用<可滚动元素>标签包裹起来的部分，表示这一个板块是带一个滚动条的，可以进行滚动。
（如果是『整页滚动条』中的 <可滚动元素 eN>，则表示它是整个网页的滚动条）
只返回滚动后，对于当前窗口，新看见的内容。
通常，滚动本身不会导致『可互动元素』丢失，即使你滚动使得当前的视口（viewport）使得滚动之前的『可互动元素』不再可见，
在你调用时 工具会自动滚回相应可互动元素的位置。
而如果 可互动元素 出现在了各种浏览器相关工具返回的『丢失的可互动元素列表』中，那就是真的无法再互动了"""
    sync_func_here = BrowserController.instance().interact_scroll
    return await   _wrap_up(sync_func_here , name = _normalize_elem_mark(elem_mark), scroll_delta = times_to_scroll)  # BID084

@langchain_tool
async def tool_04_drag(
    elem_mark: Annotated[str, "<可拖动元素 eN>的标识符eN"],
    drag_percentage: Annotated[int, "拖动到哪一个百分比，用数字表示，范围0到100"],
) -> str|dict:
    """用于拖动页面中的<可拖动元素>，常见于网页的各种滑条（如声音调节拖动条）"""
    sync_func_here = BrowserController.instance().interact_drag
    return await   _wrap_up(sync_func_here ,name =  _normalize_elem_mark(elem_mark), drag_pct = drag_percentage)  # BID084

@langchain_tool
async def tool_05_type_in_select(
    elem_mark: Annotated[str, "<可选择元素 eN>的标识符eN"],
    type_in_cotent_to_match: Annotated[str, "输入选项中的一个以达成匹配"],
) -> str|dict:
    """<可选择元素> 对于输入的值有匹配要求，只能输入备选项中的一个。第一次调用可以使用空字符串来得到候选项列表。"""
    sync_func_here = BrowserController.instance().interact_select
    type_in_cotent_to_match = apply_sensitive_replacement(str(type_in_cotent_to_match))
    return await   _wrap_up(sync_func_here ,name =  _normalize_elem_mark(elem_mark), fill =  type_in_cotent_to_match)  # BID084


@langchain_tool
async def tool_06_interact_sequence(
    elem_mark_list: Annotated[list, "<可互动元素 eN>的标识符eN组成的列表（这里是互动指“输入”或者“点击”）。例如['e4', 'e5', 'e6']"],
    type_in_cotent_list: Annotated[list, "对应每个<可输入元素>的位置，相应的要输入的内容"],
) -> str|dict:
    """用于一次性在多个地方进行填入，并可选性的在末尾附加一个点击
附加点击时，type_in_cotent_list  的长度比  elem_mark_list  多一；否则，两者长度应当相等。"""
    sync_func_here = BrowserController.instance().interact_many
    type_in_cotent_list = [apply_sensitive_replacement(str(arg_here)) for arg_here in type_in_cotent_list ]
    name_list = [_normalize_elem_mark(value) for value in (elem_mark_list or [])]  # BID084
    return await   _wrap_up(sync_func_here , name_list = name_list, fill_list = type_in_cotent_list)  # BID084



@langchain_tool
async def tool_07_searchable_dropdown(
    elem_mark: Annotated[str, "<可搜索下拉元素 eN>的标识符eN"],
    type_in_to_filter: Annotated[str, "输入要搜索的内容，以收缩匹配结果（通常是严格的前缀匹配，输入ab只会匹配ab开头的内容）"],
) -> str|dict:
    """用于跟<可搜索下拉元素>进行交互，
本处的输入只可以用于筛选（收缩）下拉选单（候选项），输入的内容本身不能作数。
只输入内容，不对返回的候选单进行点击的话，一旦点击其他地方，输入内容就被清空，该表单项视作未填。
必须要点击下拉选单中的项（调用tool_01_click）才能真正作数。
拿不准选单内容时，可以填入空字符串，用于获得完整候选表单。
"""
    sync_func_here = BrowserController.instance().interact_searchable_fill_in
    type_in_to_filter = apply_sensitive_replacement(str(type_in_to_filter))
    return await   _wrap_up(sync_func_here , name = _normalize_elem_mark(elem_mark), fill = type_in_to_filter)  # BID084

@langchain_tool
async def tool_08_click_dropdown(
    elem_mark: Annotated[str, "<可点击下拉元素 eN>的标识符eN"],
) -> str|dict:
    """用于跟<可点击下拉元素>进行交互，交互后通常会弹出一个下拉列表（候选项），需要再调用tool_01_click进行选择，
本工具只用于点击相关元素，使得下拉框弹出"""
    sync_func_here = BrowserController.instance().interact_click_dropdown
    return await   _wrap_up(sync_func_here , name = _normalize_elem_mark(elem_mark))  # BID084


@langchain_tool
async def tool_10_get_full_viewport() -> str|dict:
    """获得当前视口的全貌，通常不需要调用。
普通交互工具通常只返回 交互后 相对于交互前 的新增值（交互后新出现的内容），
但是对于 交互后 相对于交互前 的减少值，只会以『本次互动后丢失的可互动元素列表』的方式提及
调用这个工具，返回的是 过往工具结果 的一个汇总，并不会获得新的信息。"""
    sync_func_here = BrowserController.instance().full_dom
    return await   _wrap_up(sync_func_here , action_ok=NOT_CALLED)  

@langchain_tool
async def tool_20_list_tabs() -> dict:
    """获得当前由你接管的浏览器里面，所有标签页的title，以及相应的全局唯一序列号"""
    sync_func_here = BrowserController.instance().list_tabs
    return await   _wrap_up(sync_func_here )  


@langchain_tool
async def tool_21_switch_tab(
    tab_id_to_switch_to: Annotated[str, "需要切换到哪一个tab。直接传入三位数字序列号。例如“百度百科023”，直接传入023（字符串）"],
) -> dict:
    """用于切换标签页，通常会返回全量内容。
如果忘记 打开了哪些标签页，可以先调用tool_6_list_tabs查看"""
    sync_func_here = BrowserController.instance().switch_tab
    return await   _wrap_up(sync_func_here , tab_id =  tab_id_to_switch_to)  

@langchain_tool
async def tool_30_go_back() -> dict:
    """浏览器内置『返回』功能，即回到当前标签页的上一个历史记录。
通常会返回全量内容。
无历史可回退时，action_ok=失败。"""
    sync_func_here = BrowserController.instance().go_back
    return await   _wrap_up(sync_func_here )  


@langchain_tool
async def tool_31_refresh() -> dict:
    """浏览器内置『刷新』功能。除非当前页面显式提醒刷新，否则不要调用。
必定导致已经填写内容丢失"""
    sync_func_here = BrowserController.instance().refresh
    return await   _wrap_up(sync_func_here )  

@langchain_tool
async def tool_32_close_tab(
    tab_id_to_close: Annotated[str, "需要关闭哪一个标签页。直接传入三位数字序列号。例如“百度百科023”，就直接传入023（字符串）"],
) -> dict:
    """用于关闭指定标签页"""
    sync_func_here = BrowserController.instance().close_tab
    return await   _wrap_up(sync_func_here , tab_id = tab_id_to_close)  

@langchain_tool
async def tool_33_navigate(
    url_here: Annotated[str, "需要导航到哪一个网页地址。输入不带协议时，自动在前面补充 https://"],
) -> dict:
    """新开一个标签页并访问给定地址（相当于在地址栏输入并回车）。
通常会返回全量内容。"""
    sync_func_here = BrowserController.instance().navigate
    return await   _wrap_up(sync_func_here ,url =  url_here)  

@langchain_tool
async def tool_40_return_urls_of_opened_tags() -> dict:
    """返回当前所有已打开标签页的title到实际 url 的映射。"""
    sync_func_here = BrowserController.instance().tab_url_map
    return await   _wrap_up(sync_func_here )  


# ── BID084：工具描述的四套口径（中文/英文 × 未整合/已整合） ──────────────
# 说明：中文原始描述 = 上面函数 docstring 的原文；英文描述取自
# ``browser_tools_EN/browser_tools.py`` 的原型（只换标签与术语，正文保留中文）。
# 每次图编译时由 ``return_browser_tools()`` 按当前全局设置覆盖工具的
# ``description`` 与 ``elem_mark`` / ``elem_mark_list`` 参数说明。
_ALL_TOOLS = [
    tool_01_click,
    tool_02_type_in_content,
    tool_03_scroll,
    tool_04_drag,
    tool_05_type_in_select,
    tool_06_interact_sequence,
    tool_07_searchable_dropdown,
    tool_08_click_dropdown,
    tool_10_get_full_viewport,
    tool_20_list_tabs,
    tool_21_switch_tab,
    tool_30_go_back,
    tool_31_refresh,
    tool_32_close_tab,
    tool_33_navigate,
    tool_40_return_urls_of_opened_tags,
]

# 本字典于BID084引入
_BASE_DESC = {_tool.name: _tool.description for _tool in _ALL_TOOLS}
# 本字典于BID084引入
_BASE_ARGS = {
    _tool.name: {key: dict(value) for key, value in _tool.args.items()}
    for _tool in _ALL_TOOLS
}

# 本字典于BID084引入
_TOOL_CATEGORY = {
    "tool_01_click": "click",
    "tool_02_type_in_content": "input",
    "tool_03_scroll": "scroll",
    "tool_04_drag": "drag",
    "tool_05_type_in_select": "select",
    "tool_06_interact_sequence": "interactive",
    "tool_07_searchable_dropdown": "searchable",
    "tool_08_click_dropdown": "clickdropdown",
}

# 本字典于BID084引入
_TAG_CN = {
    "click": "可点击元素",
    "clickdropdown": "可点击下拉元素",
    "input": "可输入元素",
    "searchable": "可搜索下拉元素",
    "select": "可选择元素",
    "drag": "可拖动元素",
    "scroll": "可滚动元素",
    "interactive": "可互动元素",
}

# 本字典于BID084引入
_TAG_EN = {
    "click": "Click elem",
    "clickdropdown": "Click-dropdown elem",
    "input": "Type-in elem",
    "searchable": "Search-dropdown elem",
    "select": "Select elem",
    "drag": "Drag elem",
    "scroll": "Scroll elem",
    "interactive": "Interactive elem",
}

# 本字典于BID084引入
_EN_DESC = {
    "tool_01_click": "用于点击当前页面中所有仍然生效的<Click elem>（可点击元素）。通常会返回当前页面因为 点击互动 而产生的变化",
    "tool_02_type_in_content": "用于在<Type-in elem>（可输入元素）的位置，输入你需要输入的内容。\n通常会返回这一次输入的内容本身",
    "tool_03_scroll": (
        "页面中使用<Scroll elem>标签包裹起来的部分，表示这一个板块是带一个滚动条的，可以进行滚动。\n"
        "（如果是『整页滚动条』中的 <Scroll elem N>，则表示它是整个网页的滚动条）\n"
        "只返回滚动后，对于当前窗口，新看见的内容。\n"
        "通常，滚动本身不会导致『Interactive elem』（可交互元素）丢失，即使你滚动使得当前的视口（viewport）使得滚动之前的『Interactive elem』不再可见，他们仍然是生效的。\n"
        "在你调用时，工具会自动滚回相应的 Interactive elem 的位置。\n"
        "而如果『Interactive elem』出现在了各种浏览器相关工具返回的『丢失的 Interactive elems』中，那就是真的无法再互动了"
    ),
    "tool_04_drag": "用于拖动页面中的<Drag elem>（可拖动元素），常见于网页的各种滑条（如声音调节拖动条）",
    "tool_05_type_in_select": "<Select elem>（可选择元素）对于输入的值有匹配要求，只能输入备选项中的一个。第一次调用可以使用空字符串来得到候选项列表。",
    "tool_06_interact_sequence": (
        "用于一次性在多个地方进行 Type-in（填入），并可选性的在末尾附加一个Click（点击）\n"
        "附加点击时，type_in_cotent_list  的长度应该比  elem_mark_list  多一；否则，两者长度应当相等（不附加点击，仅填入）。"
    ),
    "tool_07_searchable_dropdown": (
        "用于跟<Search-dropdown elem>（可搜索下拉元素）进行交互，\n"
        "本处的输入只可以用于筛选（收缩）下拉选单（候选项），输入的内容本身不能作数。\n"
        "只输入内容，不对返回的候选单进行点击的话，一旦点击其他地方，输入内容就被清空，该表单项视作未填。\n"
        "必须要点击下拉选单中的项（调用tool_01_click）才能真正作数。\n"
        "拿不准选单内容时，可以填入空字符串，用于获得完整候选表单。\n"
    ),
    "tool_08_click_dropdown": (
        "用于跟<Click-dropdown elem>（可点击下拉元素）进行交互，交互后通常会弹出一个下拉列表（候选项），需要再调用tool_01_click进行选择，\n"
        "本工具只用于点击相关元素，使得下拉框弹出"
    ),
    "tool_10_get_full_viewport": (
        "获得当前视口的全貌，通常不需要调用。\n"
        "普通交互工具通常只返回 交互后 相对于交互前 的新增值（交互后新出现的内容），\n"
        "但是对于 交互后 相对于交互前 的减少值，只会以『本次互动后丢失的 Interactive elems』的方式提及\n"
        "调用这个工具，返回的是 过往工具结果 的一个汇总，并不会获得新的信息。"
    ),
}

# 本字典于BID084引入
_EN_ELEM_DESC = {
    "tool_01_click": "<Click elem N>的标号N。例如<Click elem 23>，这里就传入23。",
    "tool_02_type_in_content": "<Type-in elem N>的标号N",
    "tool_03_scroll": "<Scroll elem N>的标号N。例如<Scroll elem 23>，这里就传入23。",
    "tool_04_drag": "<Drag elem N>的标号N",
    "tool_05_type_in_select": "<Select elem N>的标号N",
    "tool_06_interact_sequence": "<Interactive elem N>的标号N组成的列表（这里的 Interactive 特指 Type-in 和 Click）。例如['4', '6', '7']这样的列表形式",
    "tool_07_searchable_dropdown": "<Search-dropdown elem N>的标号N",
    "tool_08_click_dropdown": "<Click-dropdown elem N>的标号N",
}

# tool_06 的第二个参数也提到「可输入元素」，需要随口径一起改（BID084）。
_TYPE_IN_LIST_DESC_EN = "对应每个<Type-in elem>的位置，相应的要输入的内容"

# 本常量于BID084引入
_MERGE_NOTE_CN = (
    "\n本页可交互元素已整合成 `<可点击元素 eN {sep} 内容>` 的形式（内容直接位于尖括号内、"
    "不再有闭合标签）；`<可滚动元素 eN>` 是唯一例外，它仍成对出现。"
)

# 本常量于BID084引入
_MERGE_NOTE_EN = (
    "\nInteractive elems on this page are merged into the form `<Click elem N {sep} content>` "
    "(the content sits directly inside the angle brackets, with no closing tag); "
    "`<Scroll elem N>` is the only exception and stays paired."
)


# 本函数于BID084引入
def _load_wording_options() -> tuple[bool, str, bool]:
    """图编译时读取全局「整合 / 分隔符 / 英文渲染」设置（失败则用默认值）。"""
    try:
        from app.config.settings import load_settings
        from app.deps import config_store

        settings = load_settings(config_store._dir)
        sep = str(
            getattr(settings, "browser_interactive_merge_separator", ":") or ":"
        ).strip()
        if not sep or "\n" in sep or "\r" in sep:
            sep = ":"
        return (
            bool(getattr(settings, "browser_interactive_merge", True)),
            sep,
            bool(getattr(settings, "browser_interactive_english", False)),
        )
    except Exception:  # noqa: BLE001
        return True, ":", False


# 本字典于BID084引入
_MERGED_EXAMPLE = {
    "click": "提交申请",
    "clickdropdown": "请选择",
    "input": "张三",
    "searchable": "北京",
    "select": "北京",
    "drag": "50",
}


# 本函数于BID084引入
def _merged_elem_desc(category: str, sep: str, english: bool) -> str:
    """整合口径下的 ``elem_mark`` 参数说明。"""
    if category == "interactive":
        # tool_06 接收的是「输入 / 点击」两类元素的混合列表；整合后它们分别是
        # 可输入元素 / 可点击元素，这里用两个示例说明，避免给出不存在的标签名。
        if english:
            return (
                f"交互元素的标号N组成的列表（元素形如 `<Click elem N {sep} content>` "
                f"或 `<Type-in elem N {sep} content>`）。例如['4', '6', '7']这样的列表形式"
            )
        return (
            f"互动元素的标识符eN组成的列表（元素形如 `<可点击元素 eN {sep} 内容>` "
            f"或 `<可输入元素 eN {sep} 内容>`）。例如['e4', 'e5', 'e6']"
        )
    example = _MERGED_EXAMPLE.get(category, "内容")
    if english:
        tag = _TAG_EN[category]
        return (
            f"<{tag} N {sep} content>的标号N。"
            f"例如<{tag} 23 {sep} {example}>，这里就传入23。"
        )
    tag = _TAG_CN[category]
    return (
        f"<{tag} eN {sep} 内容>的标识符eN。"
        f"例如<{tag} e23 {sep} {example}>，这里就传入e23。"
    )


# 本函数于BID084引入
def _apply_tool_wording(merge: bool, sep: str, english: bool) -> None:
    """按当前口径覆盖工具的 description 与元素参数说明（调用方：return_browser_tools）。"""
    for tool in _ALL_TOOLS:
        name = tool.name
        desc = _EN_DESC.get(name, _BASE_DESC[name]) if english else _BASE_DESC[name]
        args = _BASE_ARGS[name]
        category = _TOOL_CATEGORY.get(name)
        if category is not None and merge:
            desc = desc + (
                _MERGE_NOTE_EN if english else _MERGE_NOTE_CN
            ).format(sep=sep)
        # 注意顺序：先写 description，再写参数说明。StructuredTool 的
        # ``description`` 赋值会让基于 args_schema 的 JSON schema 缓存失效；
        # 若先改参数说明，随后的 description 赋值会把参数说明刷回原文（实测）。
        tool.description = desc

        # 先把参数 schema 复位成原始文案，再按需要覆盖，避免多次编译互相污染。
        for key, value in args.items():
            if key in tool.args:
                tool.args[key].clear()
                tool.args[key].update(value)

        if category is not None:
            if english and name in _EN_ELEM_DESC:
                elem_key = "elem_mark_list" if name == "tool_06_interact_sequence" else "elem_mark"
                if elem_key in tool.args:
                    tool.args[elem_key]["description"] = _EN_ELEM_DESC[name]
                if name == "tool_06_interact_sequence" and "type_in_cotent_list" in tool.args:
                    tool.args["type_in_cotent_list"]["description"] = _TYPE_IN_LIST_DESC_EN
            if merge:
                # 可滚动元素不参与整合，参数说明保持成对写法。
                elem_key = "elem_mark_list" if name == "tool_06_interact_sequence" else "elem_mark"
                if category != "scroll" and elem_key in tool.args:
                    tool.args[elem_key]["description"] = _merged_elem_desc(
                        category, sep, english
                    )
                if name == "tool_06_interact_sequence" and "type_in_cotent_list" in tool.args:
                    tag = _TAG_EN["input"] if english else _TAG_CN["input"]
                    ref = "N" if english else "eN"
                    content = "content" if english else "内容"
                    tool.args["type_in_cotent_list"]["description"] = (
                        f"对应每个<{tag} {ref} {sep} {content}>的位置，相应的要输入的内容"
                    )


async def return_browser_tools():
    merge, sep, english = _load_wording_options()  # BID084
    _apply_tool_wording(merge, sep, english)  # BID084
    return _ALL_TOOLS  # BID084
