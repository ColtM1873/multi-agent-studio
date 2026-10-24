"""删除 ``[图片]`` 及其「独子父 tag 链」（BID080）。

关闭「浏览器工具解析图片标签」时（默认关闭），序列化仍然会产生 ``[图片]`` 组
（有 alt 时还带一行文本）；本模块在 BID079 的层级压缩**之前**把这类行整棵删除，
如果删完之后某个 ``[..]`` 结构标签（``[表单]`` / ``[区块]`` / ``[列表项]`` …）不再
有任何内容，就把它也删掉，并沿祖先链继续向上收 —— 即用户口径的「独子父 tag 链」。

口径（与用户确认）：

* 只认 ``[图片]`` 这一种标签；``[图]``（``<figure>``）不在其列，但如果 figure 里
  只有一张图片，删掉 ``[图片]`` 后 ``[图]`` 会因为「独子」而一并删除；
* 「父 tag」只认 ``[..]`` 结构标签（没有独立闭合行）；``<可点击元素 eN>`` /
  ``<可滚动元素 eN>`` 等互动标签**算内容/寻址锚点，不算标签**，不参与链式删除；
* ``<可滚动元素 eN>`` 是「开标签 + 可滚动内容 + 闭标签」的成对块（闭标签非空），
  即使删完图片后内容为空，也**整对保留**，绝不把它向上合并进 ``[..]`` 标签或删除
  （用户口径，BID080）；
* 空行不算内容；原本就没有子行的结构标签不删，只有「因为删图片才变空」才删，
  避免顺带改变与图片无关的既有输出。

开关读 :mod:`browser_agent.prefs`（app 层每次工具调用前覆盖），默认关闭。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Sequence

from .. import prefs

# 图片标签（``serialize._group_image`` 只输出这一种 header，没有闭合行）。
_IMAGE_TAG = "[图片]"

# 与 ``compress._BRACKET_TAG_RE`` 同口径：整行只有一个 ``[..]`` 结构标签。
_STRUCTURAL_TAG_RE = re.compile(r"^\[[^\[\]\n]+\]$")


# 本类于BID080引入
@dataclass
class _PNode:
    """裁剪用的行树节点（``group`` 有子行，``leaf`` 是内容行）。"""

    text: str
    kind: str  # "group" | "leaf"
    depth: int = 0
    closing: str = ""
    interactive: tuple[str, ...] = ()
    children: list["_PNode"] = field(default_factory=list)
    parent: Optional["_PNode"] = None


# 本函数于BID080引入
def _build_tree(lines: Sequence) -> _PNode:
    """把 ``OutLine`` 序列还原成行树（与 ``compress._build_tree`` 同构）。

    用 ``line.ancestors`` 定位父节点，不数缩进：diff 行只有「被选中的行 + 祖先
    链」，祖先节点在这里补成 group；``kind == "close"`` 的闭合行丢弃（收尾时按
    ``closing`` 重新生成）。额外保留 ``interactive`` 以便原样重建。
    """
    root = _PNode(text="", kind="group", depth=-1)
    path: list[_PNode] = []
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
            node = _PNode(text=opening, kind="group", depth=depth, closing=closing)
            node.parent = parent
            parent.children.append(node)
            path.append(node)
            parent = node
        depth = int(getattr(line, "depth", 0) or 0)
        text = getattr(line, "text", "")
        interactive = tuple(getattr(line, "interactive", ()) or ())
        if getattr(line, "kind", "") == "header":
            node = _PNode(
                text=text,
                kind="group",
                depth=depth,
                closing=getattr(line, "closing", "") or "",
                interactive=interactive,
            )
            node.parent = parent
            parent.children.append(node)
            path.append(node)
        else:
            node = _PNode(text=text, kind="leaf", depth=depth, interactive=interactive)
            node.parent = parent
            parent.children.append(node)
    return root


# 本函数于BID080引入
def _is_image(node: _PNode) -> bool:
    return node.kind == "group" and node.text == _IMAGE_TAG


# 本函数于BID080引入
def _is_structural_tag(node: _PNode) -> bool:
    """只有 ``[..]`` 结构标签（无独立闭合行）才参与「独子父链」删除。"""
    if node.kind != "group" or node.closing:
        return False
    return _STRUCTURAL_TAG_RE.match(node.text) is not None


# 本函数于BID080引入
def _has_content(node: _PNode) -> bool:
    """是否还有内容行（空行不算内容）。"""
    return any(
        not (child.kind == "leaf" and not child.text) for child in node.children
    )


# 本函数于BID080引入
def _prune(node: _PNode) -> bool:
    """自底向上删除图片行与「因删图片而变空」的结构标签。

    返回 ``node`` 是否仍保留（父节点据此判断自己是否被删空）。
    """
    kept: list[_PNode] = []
    removed_any = False
    for child in node.children:
        if _prune(child):
            kept.append(child)
        else:
            removed_any = True
    node.children = kept
    if _is_image(node):
        # 图片子树（含 alt 文本）整棵删除；父链由上面的 removed_any 继续上收。
        return False
    # 根节点（depth = -1）永远保留；结构标签只有「因删图片才变空」才删，
    # 原本就空的结构标签保持原状，避免改动与图片无关的输出。
    if (
        node.parent is not None
        and _is_structural_tag(node)
        and removed_any
        and not _has_content(node)
    ):
        return False
    return True


# 本函数于BID080引入
def _flatten(root: _PNode) -> list:
    """把裁剪后的行树重建为 ``OutLine`` 列表（保留层级/祖先链/互动名/闭合行）。"""
    from .serialize import OutLine  # 延迟导入：serialize → compress → prune 的循环

    out: list = []
    stack: list[tuple[int, str, str]] = []

    def emit(node: _PNode) -> None:
        if node.kind == "leaf":
            out.append(
                OutLine(node.depth, node.text, "content", tuple(stack), node.interactive)
            )
            return
        out.append(
            OutLine(
                node.depth,
                node.text,
                "header",
                tuple(stack),
                node.interactive,
                node.closing,
            )
        )
        stack.append((node.depth, node.text, node.closing))
        for child in node.children:
            emit(child)
        stack.pop()
        if node.closing:
            out.append(OutLine(node.depth, node.closing, "close", tuple(stack), ()))

    for child in root.children:
        emit(child)
    return out


# 本函数于BID080引入
def prune_image_lines(lines: Sequence) -> Sequence:
    """关闭图片标签时删除 ``[图片]`` 行及其独子父 tag 链；开启时原样返回。

    没有 ``[图片]`` 的页面走快速路径（只扫一遍文本），不改动任何行对象。
    """
    if not lines or prefs.image_tags_enabled():
        return lines
    if not any(getattr(line, "text", "") == _IMAGE_TAG for line in lines):
        return lines
    root = _build_tree(lines)
    _prune(root)
    return _flatten(root)
