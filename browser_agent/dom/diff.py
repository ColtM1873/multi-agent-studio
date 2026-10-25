"""Structural diff between two serialized DOM snapshots.

Only *new or changed* content is emitted. Removed content is intentionally not
rendered as ``-`` lines: the LLM already remembers the previous state, so
re-sending deletions is noise. Each emitted line is preceded by its ancestor
group headers (indented as in the full serialization) so the model can place it
back into the tree. The only "something disappeared" signal is the
``[丢失的可互动元素列表]`` block, which lists interactive names that no longer
exist and would therefore fail if referenced.

BID083 adds a *containment* check on top of the line-by-line comparison: a line
whose content was already present in the old snapshot is treated as "old content
that only moved" (floating blocks, marquee animation, component re-mount) and is
not reported as new. See ``changed_lines`` for the exact three-way rule — the
point is to keep genuine changes (including "the same form block was added
again") while dropping the redundant diff noise.
"""

from __future__ import annotations

import difflib
import re
from collections import Counter  # BID083
from typing import Optional

from .compress import compress_lines  # BID079
from .serialize import OutLine, separate_scroll_blocks

MAX_DIFF_CHARS = 40000
MAX_LOST = 50

# BID083：行级 diff 的「包含判断」用到的归一化。
# 同一份内容在页面重建后会拿到新的元素编号（旧 ``e44`` → 新 ``e53``），比较前
# 统一抹成 ``eN``；行首的结构标签（``[列表项]``/``[文本]``…）只是分组的投影，
# 旧内容里同一行可能没有这层前缀（压缩内联也会临时造出 ``[列表项] <可点击元素…>``），
# 所以比较前剥掉；行内连续空白折叠（层级缩进已由 ``depth`` 表达，不算内容差异）。
_ELEMENT_NAME_RE = re.compile(r"(?<![0-9A-Za-z])e\d+(?![0-9A-Za-z])")  # BID083
_LEADING_STRUCT_RE = re.compile(r"^(?:\[[^\[\]\n]+\]\s*)+")  # BID083
# BID083：需要模型「填写/选择」的控件。带这类控件的行即使内容与旧内容重复，
# 也不能当冗余丢掉——「再加一份同样的表单块」是真实新增，模型必须看见它。
_STATEFUL_CONTROL_RE = re.compile(  # BID083
    r"<(?:可输入元素|可选择元素|可搜索下拉元素|可拖动元素|可点击下拉元素) "  # BID083
)  # BID083


def _key(line: OutLine) -> tuple[int, str]:
    return (line.depth, line.text)


# 本函数于BID083引入
def _content_key(text: str) -> str:
    """把一行文本归一成「内容键」，用于判断它是否已经出现在旧内容里。

    BID083 的核心判断：行级 diff 只有在「这一行的内容之前没出现过」时才算新增。
    浮动块重排、跑马灯位移、组件临时重建都会让同一份内容换个位置/层级重新出现，
    旧的「(depth, text) 逐行比对」会把它们当成新增，于是 diff 里塞满冗余信息。
    """
    text = _ELEMENT_NAME_RE.sub("eN", text or "")  # BID083
    text = _LEADING_STRUCT_RE.sub("", text)  # BID083
    return re.sub(r"[ \t]+", " ", text).strip()  # BID083


def changed_lines(old_lines: list[OutLine], new_lines: list[OutLine]) -> list[OutLine]:
    """Return the new/changed lines (in new order), dropping unchanged lines.

    Closing tags (``kind == "close"``) are structural: they are never emitted
    directly, they are flushed by the ancestor cursor in ``format_lines``.

    BID083：新增行还要过一道「包含判断」——它的内容键若在旧快照里已经出现，
    说明这行只是「旧内容换个位置/层级重现」（浮动块重排、跑马灯位移、组件临时
    重建），不算新增。判断分三档（见 ``_content_key``）：

    * 旧快照里没有 → 全新内容，保留；
    * 旧快照里有、且出现次数没变多 → 纯重排/重建，丢弃；
    * 旧快照里有、但出现次数变多了 → 真的「又加了一份」。此时只有整段新增块里
      带可填写/可选择控件（``_STATEFUL_CONTROL_RE``）才保留：Bing 那种把同一批
      推荐链接再插一份的异步渲染是噪声，而「添加家庭成员」再复制一份同样的表单
      块是真实变化，模型必须知道要填哪些新字段。
    """
    old_keys = [_key(line) for line in old_lines]
    new_keys = [_key(line) for line in new_lines]
    matcher = difflib.SequenceMatcher(None, old_keys, new_keys, autojunk=False)
    old_content = Counter(  # BID083
        _content_key(line.text)  # BID083
        for line in old_lines  # BID083
        if line.text and line.text.strip()  # BID083
    )  # BID083
    new_content = Counter(  # BID083
        _content_key(line.text)  # BID083
        for line in new_lines  # BID083
        if line.text and line.text.strip()  # BID083
    )  # BID083
    out: list[OutLine] = []
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag in ("equal", "delete"):
            continue
        chunk = [line for line in new_lines[j1:j2] if line.kind != "close"]  # BID083
        chunk_stateful = any(_STATEFUL_CONTROL_RE.search(line.text) for line in chunk)  # BID083
        for line in chunk:
            content_key = _content_key(line.text)  # BID083
            if not content_key:  # BID083
                continue  # BID083
            old_count = old_content[content_key]  # BID083
            if old_count == 0:  # BID083
                out.append(line)  # BID083
                continue  # BID083
            if new_content[content_key] > old_count and chunk_stateful:  # BID083
                out.append(line)  # BID083
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
        parts.append(f"[增量] url 已变为 {new_url}")
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
