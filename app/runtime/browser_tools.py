from langchain_core.tools import tool as langchain_tool
from typing import Annotated

from browser_agent.controller import BrowserController, NOT_CALLED
from app.runtime.browser_tools_wrap_up import _wrap_up
from app.runtime.browser_tools_privacy_mask import apply_sensitive_replacement


@langchain_tool
async def tool_01_click(
    elem_mark: Annotated[str, "<可点击元素 eN>的标识符eN。例如<可点击元素 e23>，这里就传入e23。"],
) -> str|dict:
    """用于点击当前页面中所有仍然生效的<可点击元素>。通常互动之后，会返回当前页面因为互动而产生的变化"""
    sync_func_here = BrowserController.instance().interact_click
    return await   _wrap_up(sync_func_here , name = elem_mark)  


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
    return await   _wrap_up(sync_func_here ,name =  elem_mark, fill =  type_in_cotent ,press_enter = press_enter)  


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
    return await   _wrap_up(sync_func_here , name = elem_mark, scroll_delta = times_to_scroll)  

@langchain_tool
async def tool_04_drag(
    elem_mark: Annotated[str, "<可拖动元素 eN>的标识符eN"],
    drag_percentage: Annotated[int, "拖动到哪一个百分比，用数字表示，范围0到100"],
) -> str|dict:
    """用于拖动页面中的<可拖动元素>，常见于网页的各种滑条（如声音调节拖动条）"""
    sync_func_here = BrowserController.instance().interact_drag
    return await   _wrap_up(sync_func_here ,name =  elem_mark, drag_pct = drag_percentage)  

@langchain_tool
async def tool_05_type_in_select(
    elem_mark: Annotated[str, "<可选择元素 eN>的标识符eN"],
    type_in_cotent_to_match: Annotated[str, "输入选项中的一个以达成匹配"],
) -> str|dict:
    """<可选择元素> 对于输入的值有匹配要求，只能输入备选项中的一个。第一次调用可以使用空字符串来得到候选项列表。"""
    sync_func_here = BrowserController.instance().interact_select
    type_in_cotent_to_match = apply_sensitive_replacement(str(type_in_cotent_to_match))
    return await   _wrap_up(sync_func_here ,name =  elem_mark, fill =  type_in_cotent_to_match)  


@langchain_tool
async def tool_06_interact_sequence(
    elem_mark_list: Annotated[list, "<可互动元素 eN>的标识符eN组成的列表（这里是互动指“输入”或者“点击”）。例如['e4', 'e5', 'e6']"],
    type_in_cotent_list: Annotated[list, "对应每个<可输入元素>的位置，相应的要输入的内容"],
) -> str|dict:
    """用于一次性在多个地方进行填入，并可选性的在末尾附加一个点击
附加点击时，type_in_cotent_list  的长度比  elem_mark_list  多一；否则，两者长度应当相等。"""
    sync_func_here = BrowserController.instance().interact_many
    type_in_cotent_list = [apply_sensitive_replacement(str(arg_here)) for arg_here in type_in_cotent_list ]
    return await   _wrap_up(sync_func_here , name_list = elem_mark_list, fill_list = type_in_cotent_list)  



@langchain_tool
async def tool_10_get_full_viewport() -> str|dict:
    """获得当前视窗的全貌，通常不需要调用。
普通交互工具通常只返回 交互前 和 交互后 的新增值（交互后新出现的内容），
但是对于 交互前 和 交互后 的减少值，只会以『本次互动后丢失的可互动元素列表』的方式提及（不过这种方式通常也足够了）
调用这个工具，返回的只是 过往工具结果 的一个汇总。想通过调用该工具获得，超出过往信息汇总这个范畴的，新的信息，是徒劳。"""
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



async def return_browser_tools():
    return [
        tool_01_click , 
        tool_02_type_in_content,
        tool_03_scroll,
        tool_04_drag,
        tool_05_type_in_select,
        tool_06_interact_sequence,
        tool_10_get_full_viewport,
        tool_20_list_tabs,
        tool_21_switch_tab,
        tool_30_go_back,
        tool_31_refresh,
        tool_32_close_tab,
        tool_33_navigate,
        tool_40_return_urls_of_opened_tags,
            ]