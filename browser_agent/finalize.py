"""返回 LLM 前的最后一道表现层（BID084）。

两层都是**纯文本后处理**，不参与采集 / 分类 / 序列化 / diff / 压缩：

1. 「可交互元素整合」：把所有非 ``<可滚动元素>`` 的 ``<tag eN>内容</tag eN>``
   压成 ``<tag eN {sep} 内容>``（内容原样保留换行），并去掉闭合标签；
   内容里出现的 ``<`` / ``>`` 分别转义为 ``[`` / ``]``，避免与外层尖括号混淆；
   若某个字符恰好就是用户设置的分隔符，则该字符不转义（用户口径）。
2. 「英文渲染」：把中文标签名 / 类别名 / 元素引用换成英文口径
   （``<Click elem 23>``）；只换标签与术语，正文说明仍保留中文。

调用时机：controller 的公开方法返回前（``_base_result`` 之后、所有提示拼接完成），
因此整合发生在 diff / 压缩全部结束之后，不会回头影响任何上游解析逻辑。
"""

from __future__ import annotations

import re

from . import prefs, wording

# 参与整合（去掉闭合标签）的互动标签：可滚动元素按需求排除。
_MERGE_TAGS = wording.INTERACTIVE_TAGS_CN

# 所有互动标签（英文渲染时都要换名，包括可滚动元素）。
_ALL_TAGS = wording.ALL_TAGS_CN

_TAG_ALTERNATION = "|".join(_MERGE_TAGS)

# 一个互动标签 token：``<可点击元素 e12>`` 或 ``</可点击元素 e12>``。
_TOKEN_RE = re.compile(
    r"<(?P<close>/?)(?P<tag>" + _TAG_ALTERNATION + r") (?P<name>e\d+)>"
)

# 英文渲染：``可点击元素 e12`` / ``</可滚动元素 e1>`` / ``[可拖动元素 e9]``
# 统一换成英文标签 + 去掉元素名的 ``e`` 前缀。
_TAG_REF_RE = re.compile(
    r"(?P<tag>" + "|".join(_ALL_TAGS) + r") e(?P<num>\d+)"
)

_TAG_EN = wording.TAG_EN_BY_CN

# 独立元素引用（提示语里的 ``e12``）在英文口径下也要去掉 ``e``。
_ELEM_REF_RE = re.compile(r"\be(?P<num>\d+)\b")


# 本函数于BID084引入
def _escape_merged_content(text: str, separator: str) -> str:
    """整合进尖括号的内容转义：``<``→``[``、``>``→``]``。

    与用户设置的分隔符相同的字符不转义（否则会影响分隔符语义）。
    """
    if separator != "<":
        text = text.replace("<", "[")
    if separator != ">":
        text = text.replace(">", "]")
    return text


# 本函数于BID084引入
def merge_interactive_tags(text: str, separator: str = ":") -> str:
    """把所有非滚动互动标签的包裹内容压进开标签，并取消闭合标签。"""
    if not text:
        return text
    sep = separator or ":"
    parts: list[str] = []
    # 栈元素：(parts 下标, 标签名, 元素名, 原始开标签文本)
    stack: list[tuple[int, str, str, str]] = []
    cursor = 0
    for match in _TOKEN_RE.finditer(text):
        parts.append(text[cursor:match.start()])
        cursor = match.end()
        tag = match.group("tag")
        name = match.group("name")
        if match.group("close"):
            if stack and stack[-1][1] == tag and stack[-1][2] == name:
                start, _tag, _name, opening = stack.pop()
                content = "".join(parts[start + 1:]).strip()
                if content:
                    merged = (
                        opening[:-1]
                        + f" {sep} "
                        + _escape_merged_content(content, sep)
                        + ">"
                    )
                else:
                    # 空内容同样取消闭合标签，只留开标签（不产出 ``<tag eN : >``）。
                    merged = opening
                del parts[start:]
                parts.append(merged)
            else:
                # 不平衡 / 非栈顶的闭合标签：原样保留。
                parts.append(match.group(0))
        else:
            stack.append((len(parts), tag, name, match.group(0)))
            parts.append(match.group(0))
    parts.append(text[cursor:])
    return "".join(parts)


# 本函数于BID084引入
def english_render(text: str) -> str:
    """把中文互动标签 / 术语 / 元素引用换成英文口径。"""
    if not text:
        return text
    # 丢失元素列表：``diff.format_lost`` 仍按中文口径产出，这里统一翻成英文，
    # 避免出现「[丢失的 Interactive elems 列表]」这种半截翻译。
    text = text.replace(
        "[丢失的可互动元素列表]", "[Lost interactive elems]"
    )
    text = text.replace(
        "互动之后，viewport内相较于互动之前，丢失的可互动元素列表：",
        "Interactive elems lost since the last action in the viewport: ",
    )
    text = re.sub(r"…等 (\d+) 个", r"…\1 total", text)
    text = _TAG_REF_RE.sub(
        lambda m: f"{_TAG_EN[m.group('tag')]} {m.group('num')}", text
    )
    # 没有带 ``eN`` 的泛称（例如提示语里的 ``<可滚动元素>``）。
    for cn, en in _TAG_EN.items():
        text = text.replace(cn, en)
    text = text.replace("可互动元素", "Interactive elems")
    text = text.replace("可交互元素", "Interactive elems")
    text = text.replace("互动元素", "Interactive elem")
    text = _ELEM_REF_RE.sub(lambda m: m.group("num"), text)
    return text


# 本函数于BID084引入
def finalize_text(text: str) -> str:
    """按当前运行时偏好执行「整合 → 英文渲染」两层后处理。"""
    if not text:
        return text
    if prefs.interactive_merge_enabled():
        text = merge_interactive_tags(text, prefs.interactive_merge_separator())
    if prefs.interactive_english_enabled():
        text = english_render(text)
    return text


# 本函数于BID084引入
def finalize_result(result):
    """对工具结果 dict 的 content / error 做最终表现层处理（其他字段不动）。"""
    if not isinstance(result, dict):
        return result
    content = result.get("content")
    error = result.get("error")
    if not isinstance(content, str) and not isinstance(error, str):
        return result
    out = dict(result)
    if isinstance(content, str):
        out["content"] = finalize_text(content)
    if isinstance(error, str):
        out["error"] = finalize_text(error)
    return out
