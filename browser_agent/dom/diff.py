"""Structural diff between two serialized DOM snapshots.

Only *new or changed* content is emitted. Removed content is intentionally not
rendered as ``-`` lines: the LLM already remembers the previous state, so
re-sending deletions is noise. Each emitted line is preceded by its ancestor
group headers (indented as in the full serialization) so the model can place it
back into the tree. The only "something disappeared" signal is the
``[丢失的可互动元素列表]`` block, which lists interactive names that no longer
exist and would therefore fail if referenced.

BID083 adds a *conservative* duplicate check on top of the line-by-line
comparison: an added line is only treated as "old content that merely reappeared"
when it is literally the same line (same text, element names included), sits
under the same parent, and its immediate siblings under that parent match too.
Anything else (a hard insertion, a re-rendered element with a new name, a block
that moved to another parent) keeps its diff. The rule of thumb is explicit:
give up de-duplication rather than risk hiding a real change.
"""

from __future__ import annotations

import difflib
import re
from typing import Optional, Sequence

from .compress import compress_lines  # BID079
from .serialize import OutLine, separate_scroll_blocks
from .. import prefs  # BID088

MAX_DIFF_CHARS = 40000
MAX_LOST = 50

# BID088：「返回内容携带 url」关闭时（默认）用来替代 ``[增量] url 已变为 …`` 的那一行。
# 只保留「地址变过」这个事实，不附地址本体（地址常带一长串无语义的随机字符）。
# 注意：这里**不要**写「如需查看请调用某工具」之类的指路文案——LLM 本来就能看到
# 完整工具列表，点名某个工具会让它产生强烈的调用动机（哪怕完全没有必要），
# 反而增加上下文噪音、模糊任务目标。
_URL_CHANGED_NOTICE = "[增量] 页面地址已变化"

# BID083：去重判据用的「直接父级」= 最邻近的那一层分组开标签（祖先链最后一项）。
# 再往上/往前的祖先不参与比较——类型不同、层级更深的嵌套不该阻止去重。
# 没有父标签（顶层行）时用空串表示，两边都是空串自然满足。


def _key(line: OutLine) -> tuple[int, str]:
    return (line.depth, line.text)


# 本函数于BID083引入
def _parent_tag(line: OutLine) -> str:
    """该行最邻近的父级标签（'' 表示顶层，没有父标签）。"""
    ancestors = getattr(line, "ancestors", ()) or ()
    return ancestors[-1][1] if ancestors else ""


# 本函数于BID083引入
def _sibling_links(lines: Sequence[OutLine]) -> tuple[list[Optional[int]], list[Optional[int]]]:
    """给每一行算出「直接父级标签相同」的上一条/下一条下标（跳过 close 行）。

    BID083 的第二道防护：只有前后相邻行也一致，才允许把这一行判成重复。
    close 行是结构标记、不是渲染内容，不参与相邻关系。
    同一父级标签下的多个平行容器（两个 ``[列表]``）会被视作同一组，这只会让
    「前/后一行」要求更严（更容易保留 diff），不会多吞内容。
    """
    groups: dict[str, list[int]] = {}
    for index, line in enumerate(lines):
        if line.kind == "close":
            continue
        groups.setdefault(_parent_tag(line), []).append(index)
    prev: list[Optional[int]] = [None] * len(lines)
    nxt: list[Optional[int]] = [None] * len(lines)
    for indices in groups.values():
        for position, index in enumerate(indices):
            prev[index] = indices[position - 1] if position > 0 else None
            nxt[index] = indices[position + 1] if position + 1 < len(indices) else None
    return prev, nxt


# 本函数于BID083引入
def _text_at(lines: Sequence[OutLine], index: Optional[int]) -> Optional[str]:
    """取某行的文本；没有这一行时返回 ``None``（用于「没有前/后一行」的比较）。"""
    return None if index is None else lines[index].text


# 本函数于BID083引入
def _reappears_in_place(
    old_lines: Sequence[OutLine],
    new_lines: Sequence[OutLine],
    new_index: int,
    old_index: int,
    old_prev: Sequence[Optional[int]],
    old_next: Sequence[Optional[int]],
    new_prev: Sequence[Optional[int]],
    new_next: Sequence[Optional[int]],
) -> bool:
    """判断新行是否只是旧内容在「同一个位置」重现（可安全从 diff 中去掉）。

    三个条件同时成立才算：文本逐字相同（含元素编号）、直接父级标签相同、
    该父级下的前/后一行也相同。任何一项不满足都保留，宁可多给 diff，
    也不吞掉可能的变化。
    """
    if _parent_tag(old_lines[old_index]) != _parent_tag(new_lines[new_index]):  # BID083
        return False  # BID083
    if _text_at(old_lines, old_prev[old_index]) != _text_at(new_lines, new_prev[new_index]):  # BID083
        return False  # BID083
    if _text_at(old_lines, old_next[old_index]) != _text_at(new_lines, new_next[new_index]):  # BID083
        return False  # BID083
    return True  # BID083


def changed_lines(old_lines: list[OutLine], new_lines: list[OutLine]) -> list[OutLine]:
    """Return the new/changed lines (in new order), dropping unchanged lines.

    Closing tags (``kind == "close"``) are structural: they are never emitted
    directly, they are flushed by the ancestor cursor in ``format_lines``.

    BID083：新增行还要过一道「是否只是旧内容原地重现」的判断。判定**故意很严**，
    原则是「宁可放弃去重，也不让可能的信息丢失」：

    * 行文本必须**逐字相同**（元素编号 ``eN`` 也参与比较，不抹成 ``eN``——
      重新渲染/硬插入会分配新编号，这种一律当成新增让它进 diff）；
    * 直接父级必须相同（``_parent_tag``：只看最邻近那层分组标签，更上层不管；
      两边都没有父标签时自然相同）；
    * 同一父级下的前一行、后一行内容必须相同；若这一行在父级下是第一行
      （没有前一行）则只比后一行，最后一行只比前一行，父级下只有这一行则不比。
    """
    old_keys = [_key(line) for line in old_lines]
    new_keys = [_key(line) for line in new_lines]
    matcher = difflib.SequenceMatcher(None, old_keys, new_keys, autojunk=False)
    old_by_text: dict[str, list[int]] = {}  # BID083
    for index, line in enumerate(old_lines):  # BID083
        if line.kind == "close":  # BID083
            continue  # BID083
        old_by_text.setdefault(line.text, []).append(index)  # BID083
    old_prev, old_next = _sibling_links(old_lines)  # BID083
    new_prev, new_next = _sibling_links(new_lines)  # BID083

    out: list[OutLine] = []
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag in ("equal", "delete"):
            continue
        for new_index in range(j1, j2):
            line = new_lines[new_index]
            if line.kind == "close":
                continue
            same_spot = any(  # BID083
                _reappears_in_place(  # BID083
                    old_lines, new_lines, new_index, old_index,  # BID083
                    old_prev, old_next, new_prev, new_next,  # BID083
                )  # BID083
                for old_index in old_by_text.get(line.text, ())  # BID083
            )  # BID083
            if same_spot:  # BID083
                continue  # BID083
            out.append(line)
    return out


def format_lines(
    lines: list[OutLine],
    max_chars: int = MAX_DIFF_CHARS,
    context_lines: Optional[list[OutLine]] = None,  # BID079
) -> str:
    # BID079：先按「祖先链 + 行」还原成行树再做缩进压缩，然后整体产出文本。
    # 原来的祖先游标式拼装（``_emit``）逻辑已并入 compress.compress_lines：
    # 归并/内联会改写祖先行的文本与层级，游标式增量拼装无法表达。
    out = compress_lines(lines, context_lines=context_lines, blank_mode="any")  # BID079
    text = "\n".join(separate_scroll_blocks(out))
    if len(text) > max_chars:
        text = text[:max_chars] + "\n…（diff 已截断）"
    return text


def compute_diff(
    old_lines: list[OutLine],
    new_lines: list[OutLine],
    old_url: str = "",
    new_url: str = "",
    max_chars: int = MAX_DIFF_CHARS,
    context_lines: Optional[list[OutLine]] = None,  # BID079
) -> str:
    lines = changed_lines(old_lines, new_lines)
    if not lines:
        return "（页面无变化）"
    parts: list[str] = []
    if old_url != new_url and new_url:
        if prefs.url_in_content_enabled():  # BID088
            parts.append(f"[增量] url 已变为 {new_url}")  # BID088
        else:  # BID088
            parts.append(_URL_CHANGED_NOTICE)  # BID088
    parts.append(format_lines(lines, max_chars, context_lines=context_lines))  # BID079
    return "\n".join(parts)


def _interactive_names(lines: list[OutLine]) -> list[str]:
    seen: list[str] = []
    seen_set: set[str] = set()
    for line in lines:
        for name in line.interactive:
            if name not in seen_set:
                seen_set.add(name)
                seen.append(name)
    return seen


def _name_sort_key(name: str) -> tuple[int, object]:
    match = re.match(r"^e(\d+)$", name)
    return (0, int(match.group(1))) if match else (1, name)


def compute_lost(
    old_lines: list[OutLine],
    new_lines: list[OutLine],
    registry: Optional[object] = None,
) -> list[str]:
    """Interactive names that were rendered before and are now unavailable.

    An element that merely scrolled out of the viewport is still in the DOM and
    still interactable (``scrollIntoViewIfNeeded`` handles it), so it is *not*
    reported. Only names whose key is no longer active in the registry — i.e.
    they would fail with "该互动元素已经不存在于viewport中了" — are listed.
    """
    new_set = set(_interactive_names(new_lines))
    lost: list[str] = []
    for name in _interactive_names(old_lines):
        if name in new_set:
            continue
        if registry is not None and registry.is_active(name):
            continue
        lost.append(name)
    lost.sort(key=_name_sort_key)
    return lost


def format_lost(lost: list[str], limit: int = MAX_LOST) -> str:
    if not lost:
        return ""
    shown = lost[:limit]
    body = "[ " + ", ".join(shown) + " ]"
    if len(lost) > limit:
        body += f" …等 {len(lost)} 个"
    return (
        "[丢失的可互动元素列表]\n"
        f"\t互动之后，viewport内相较于互动之前，丢失的可互动元素列表：{body}"
    )
