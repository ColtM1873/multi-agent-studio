"""返回 LLM 之前的层级压缩（``\\t`` 占比过高）。

序列化用 ``\\t`` 表示层级：表单 / 深层嵌套容器的页面上，tab 的字节数已经超过了
有意义内容的字节数。本模块**不改动采集、分类、渲染逻辑**，只对「序列化已经产出的
行」做一次重组（BID079）：

1. ``<可滚动元素 eN>`` 包裹的区域不再多缩进一级 —— 滚动块本身就是结构信号；
2. 既没有「兄弟 tab」也没有「兄弟内容」的 ``[结构标签]`` tab 归并进上一级 tab 的
   同一行（``[区块][表单]``），可以连续归并；
3. tab 下只有一行内容的，内容并入该 tab 行（``[文本] 学历类型``）；2 与 3 可以
   叠加（``[列表项][文本] 内容``）。

判定口径（用户需求，见 BID079）：

* diff 类工具（tool_01/02/04/05/06/07/08）：只按**返回的 diff 自身**判断兄弟/内容
  行数，不考虑 diff 出现的上下文；
* 全量类工具（tool_03/tool_10）：按**全量**判断 —— 调用方传入「忽略视口裁剪」的
  整页序列化结果 ``context_lines``，本模块按行文本把它对齐到可见行上
  （``_align``），用全量侧的事实判断兄弟 / 内容行数。

只有 ``[..]`` 结构 tab 参与归并。``<可点击元素 eN>`` / ``<可滚动元素 eN>`` 等互动
标签不是 tab：带独立闭合行的互动**块**永远保持自己的层级；互动**叶子**行（如
``<可点击元素 eN>进入</可点击元素 eN>`` 或 ``#page`` 的单行滚动提示）按「内容」处理，
可以被规则 3 内联进上一级结构 tab（BID080 审查澄清：互动元素算内容，标签只认
``[]`` 包裹的行）。``<可滚动元素 eN>`` 尤其特殊：开标签 + 内容 + 闭标签是成对块，
规则 1 只把它的内容 dedent，绝不把整块并进上一级 tab，也不吞掉闭标签。
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from typing import Optional, Sequence

from .prune import prune_image_lines  # BID080

# ``[文本]`` / ``[列表项]`` / ``[3级标题]`` 这类纯结构标签行（后面不能再跟文字）。
_BRACKET_TAG_RE = re.compile(r"^\[[^\[\]\n]+\]$")

# 滚动块开标签（多行块，带独立闭合行）。
_SCROLL_OPEN_TEXT = "<可滚动元素 "

# 对齐「全量」与「可见」两份序列化时忽略元素编号：两边用的是不同 NameRegistry
# （全量侧用一次性 registry，避免消耗全局编号），文本只差 ``eN``。
_ELEMENT_NAME_RE = re.compile(r"(?<![0-9A-Za-z])e\d+(?![0-9A-Za-z])")


# 本类于BID079引入
@dataclass
class _Node:
    """压缩用的行树节点：``group`` 有子行（结构 tab），``leaf`` 是内容行。"""

    text: str
    kind: str  # "group" | "leaf"
    depth: int = 0
    closing: str = ""
    is_scroll: bool = False
    children: list["_Node"] = field(default_factory=list)
    parent: Optional["_Node"] = None
    # 全量口径（``context_lines``）里与之对应的节点；没有对上的保持 None。
    ctx: Optional["_Node"] = None

    @property
    def is_blank(self) -> bool:
        """空行（段落分隔）：不参与兄弟/内容行数判定。"""
        return self.kind == "leaf" and not self.text


# 本函数于BID079引入
def _is_scroll_text(text: str) -> bool:
    return text.startswith(_SCROLL_OPEN_TEXT)


# 本函数于BID079引入
def _tab(depth: int) -> str:
    return "\t" * depth


# 本函数于BID079引入
def _build_tree(lines: Sequence) -> _Node:
    """把一串 ``OutLine`` 还原成行树。

    用 ``line.ancestors``（序列化时的分组栈）定位父节点，而不是靠缩进猜：
    diff 行只有「被选中的行 + 祖先链」，祖先节点在这里被补成 context 节点；
    ``kind == "close"`` 的闭合行丢弃（收尾时按 ``closing`` 重新生成，位置一致）。
    """
    root = _Node(text="", kind="group", depth=-1)
    path: list[_Node] = []
    for line in lines:
        if getattr(line, "kind", "") == "close":
            continue
        ancestors = list(getattr(line, "ancestors", ()) or ())
        common = 0
        while common < len(ancestors) and common < len(path):
            depth, opening, _closing = ancestors[common]
            if path[common].depth != depth or path[common].text != opening:
                break
            common += 1
        del path[common:]
        parent = path[-1] if path else root
        for depth, opening, closing in ancestors[common:]:
            node = _Node(
                text=opening,
                kind="group",
                depth=depth,
                closing=closing,
                is_scroll=_is_scroll_text(opening),
            )
            node.parent = parent
            parent.children.append(node)
            path.append(node)
            parent = node
        depth = int(getattr(line, "depth", 0) or 0)
        if getattr(line, "kind", "") == "header":
            node = _Node(
                text=line.text,
                kind="group",
                depth=depth,
                closing=getattr(line, "closing", "") or "",
                is_scroll=_is_scroll_text(line.text),
            )
            node.parent = parent
            parent.children.append(node)
            path.append(node)
        else:
            node = _Node(text=line.text, kind="leaf", depth=depth)
            node.parent = parent
            parent.children.append(node)
    return root


# 本函数于BID079引入
def _match_key(node: _Node) -> str:
    """对齐用的行文本键（抹掉元素编号）。"""
    return _ELEMENT_NAME_RE.sub("e#", node.text)


# 本函数于BID079引入
def _align(visible: _Node, context: _Node) -> None:
    """把可见行树逐层对齐到全量行树（全量是可见的超集，按顺序贪心匹配）。"""
    visible.ctx = context
    if not visible.children or not context.children:
        return
    index: dict[str, list[int]] = {}
    for position, child in enumerate(context.children):
        index.setdefault(_match_key(child), []).append(position)
    cursor = 0
    for child in visible.children:
        positions = index.get(_match_key(child))
        if not positions:
            continue
        offset = bisect.bisect_left(positions, cursor)
        if offset >= len(positions):
            continue
        position = positions[offset]
        _align(child, context.children[position])
        cursor = position + 1


# 本函数于BID079引入
def _non_blank(children: Sequence[_Node]) -> list[_Node]:
    return [child for child in children if not child.is_blank]


# 本函数于BID079引入
def _facts_children(node: _Node) -> list[_Node]:
    """判定口径下的子行：有全量上下文时用全量侧，否则用可见侧。"""
    if node.ctx is not None:
        return node.ctx.children
    return node.children


# 本函数于BID079引入
def _content_count(node: _Node) -> int:
    """tab 下（全量口径）的内容行数，忽略空行。"""
    return len(_non_blank(_facts_children(node)))


# 本函数于BID079引入
def _is_group(node: _Node) -> bool:
    """是否是有子行的结构 tab（可见侧或全量侧任一侧有子行即可）。"""
    if _non_blank(node.children):
        return True
    if node.ctx is not None and _non_blank(node.ctx.children):
        return True
    return False


# 本函数于BID079引入
def _is_tab(node: _Node) -> bool:
    """只有 ``[..]`` 结构标签、且带子行、且没有独立闭合行的节点才参与归并。"""
    if node.is_scroll:  # BID080：滚动块是成对块，绝不参与归并/内联
        return False
    if node.kind != "group" or node.closing:
        return False
    if _BRACKET_TAG_RE.match(node.text) is None:
        return False
    return _is_group(node)


# 本函数于BID079引入
def _fold_child(node: _Node, visible_children: Sequence[_Node]) -> Optional[_Node]:
    """规则 2：没有兄弟 tab / 兄弟内容的 tab 归并进上一级 tab。

    返回可归并的子节点（把它的文本接在上一级 tab 行后面），否则 None。
    """
    if not _is_tab(node):
        return None
    if _content_count(node) != 1:
        # 全量口径下它不是「独子」——还有兄弟 tab 或兄弟内容。
        return None
    children = _non_blank(visible_children)
    if len(children) != 1:
        return None
    child = children[0]
    if not _is_tab(child):
        return None
    return child


# 本函数于BID079引入
def _inline_child(node: _Node, visible_children: Sequence[_Node]) -> Optional[_Node]:
    """规则 3：tab 下只有一行内容时，把这一行并入 tab 行。

    互动叶子行也是「内容行」，因此会被内联（``[1级标题] <可点击元素 e14>…``）；
    带闭合行的互动块是 group，不会走这里（BID080 审查澄清）。
    """
    if not _is_tab(node):
        return None
    if _content_count(node) != 1:
        return None
    if any(child.is_blank for child in visible_children):
        # 空行（段落分隔）必须留在原位，不能把唯一内容行并进来。
        return None
    children = _non_blank(visible_children)
    if len(children) != 1:
        return None
    child = children[0]
    if _is_group(child):
        return None
    return child


# 本函数于BID079引入
def _emit(root: _Node, blank_mode: str) -> list[str]:
    """按压缩后的层级重新产出文本行。

    ``blank_mode``：``"header"`` = 只在顶层结构 tab 前空行（全量输出口径）；
    ``"any"`` = 顶层任意行前空行（增量 diff 口径）。
    """
    out: list[str] = []

    def emit(node: _Node, depth: int, is_top: bool) -> None:
        if node.is_blank:
            out.append("")
            return
        if is_top and (blank_mode == "any" or node.kind == "group") and out and out[-1] != "":
            out.append("")
        if node.kind == "leaf":
            out.append(_tab(depth) + node.text)
            return
        text = node.text
        current = node
        children = current.children
        while True:
            folded = _fold_child(current, children)
            if folded is None:
                break
            text += folded.text
            current = folded
            children = current.children
        inlined = _inline_child(current, children)
        if inlined is not None:
            out.append(_tab(depth) + text + " " + inlined.text)
            return
        out.append(_tab(depth) + text)
        # 规则 1：滚动块包裹的区域不再多缩进一级。
        child_depth = depth if current.is_scroll else depth + 1
        for child in children:
            emit(child, child_depth, False)
        if current.closing:
            out.append(_tab(depth) + current.closing)

    for child in root.children:
        emit(child, 0, True)
    return out


# 本函数于BID079引入
def compress_lines(
    lines: Sequence,
    context_lines: Optional[Sequence] = None,
    blank_mode: str = "header",
) -> list[str]:
    """把序列化行重组为压缩后的文本行。

    ``lines``：待返回的行（全量输出或 diff 选中的行）；
    ``context_lines``：可选的全量口径行（忽略视口裁剪的整页序列化），只在
    「全量类工具」传入；缺失时全部判定只依据 ``lines`` 自身（diff 口径）。
    """
    if not lines:
        return []
    # BID080：图片标签关闭时，先把 [图片] 及其独子父 tag 链整条删除，再做层级压缩；
    # 全量口径依据（context_lines）也要同样删干净，否则兄弟/内容行数会把图片算进去。
    lines = prune_image_lines(lines)  # BID080
    if context_lines:
        context_lines = prune_image_lines(context_lines)  # BID080
    root = _build_tree(lines)
    if context_lines:
        context_root = _build_tree(context_lines)
        _align(root, context_root)
    return _emit(root, blank_mode)
