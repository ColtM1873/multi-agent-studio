"""Structural diff between two serialized DOM snapshots.

Only *new or changed* content is emitted. Removed content is intentionally not
rendered as ``-`` lines: the LLM already remembers the previous state, so
re-sending deletions is noise. Each emitted line is preceded by its ancestor
group headers (indented as in the full serialization) so the model can place it
back into the tree. The only "something disappeared" signal is the
``[丢失的可互动元素列表]`` block, which lists interactive names that no longer
exist and would therefore fail if referenced.
"""

from __future__ import annotations

import difflib
import re
from typing import Optional

from .serialize import OutLine, separate_scroll_blocks

MAX_DIFF_CHARS = 40000
MAX_LOST = 50


def _key(line: OutLine) -> tuple[int, str]:
    return (line.depth, line.text)


def changed_lines(old_lines: list[OutLine], new_lines: list[OutLine]) -> list[OutLine]:
    """Return the new/changed lines (in new order), dropping unchanged lines.

    Closing tags (``kind == "close"``) are structural: they are never emitted
    directly, they are flushed by the ancestor cursor in ``format_lines``.
    """
    old_keys = [_key(line) for line in old_lines]
    new_keys = [_key(line) for line in new_lines]
    matcher = difflib.SequenceMatcher(None, old_keys, new_keys, autojunk=False)
    out: list[OutLine] = []
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag in ("equal", "delete"):
            continue
        out.extend(line for line in new_lines[j1:j2] if line.kind != "close")
    return out


def _emit(out: list[str], cursor: list[tuple[int, str, str]], line: OutLine) -> None:
    """Append ``line``, closing/opening ancestor groups as needed.

    ``cursor`` holds the currently open groups as ``(depth, opening, closing)``.
    """
    path = list(line.ancestors)
    common = 0
    while common < len(path) and common < len(cursor) and path[common] == cursor[common]:
        common += 1
    # Close the groups we are leaving, innermost first.
    for depth, _opening, closing in reversed(cursor[common:]):
        if closing:
            out.append("\t" * depth + closing)
    for depth, opening, _closing in path[common:]:
        if depth == 0 and out:
            out.append("")
        out.append("\t" * depth + opening)
    if line.depth == 0 and out:
        out.append("")
    out.append("\t" * line.depth + line.text)
    if line.kind == "header":
        cursor[:] = path + [(line.depth, line.text, line.closing)]
    else:
        cursor[:] = path


def format_lines(lines: list[OutLine], max_chars: int = MAX_DIFF_CHARS) -> str:
    out: list[str] = []
    cursor: list[tuple[int, str, str]] = []
    for line in lines:
        _emit(out, cursor, line)
    # Flush any groups still open at the end.
    for depth, _opening, closing in reversed(cursor):
        if closing:
            out.append("\t" * depth + closing)
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
) -> str:
    lines = changed_lines(old_lines, new_lines)
    if not lines:
        return "（页面无变化）"
    parts: list[str] = []
    if old_url != new_url and new_url:
        parts.append(f"[增量] url 已变为 {new_url}")
    parts.append(format_lines(lines, max_chars))
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
