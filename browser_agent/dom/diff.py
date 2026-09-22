"""Line-level structural diff between two serialized DOM snapshots."""

from __future__ import annotations

import difflib

MAX_DIFF_CHARS = 40000


def _doc_url(lines: list[str]) -> str:
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[document]"):
            marker = "url="
            idx = stripped.find(marker)
            if idx != -1:
                rest = stripped[idx + len(marker):]
                return rest.split(" ", 1)[0]
    return ""


def _clean(lines: list[str]) -> list[str]:
    out = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("<page_info>"):
            continue
        if stripped.startswith("[document]"):
            continue
        out.append(line)
    return out


def compute_diff(old_text: str, new_text: str, max_chars: int = MAX_DIFF_CHARS) -> str:
    old_url = _doc_url(old_text.splitlines())
    new_url = _doc_url(new_text.splitlines())

    old_lines = _clean(old_text.splitlines())
    new_lines = _clean(new_text.splitlines())

    matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
    out: list[str] = []
    if old_url != new_url:
        out.append(f"[增量] url 已变为 {new_url}")

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace" and (i2 - i1) == (j2 - j1):
            for offset in range(i2 - i1):
                out.append(f"~ {new_lines[j1 + offset].strip()}")
        else:
            for line in old_lines[i1:i2]:
                out.append(f"- {line.strip()}")
            for line in new_lines[j1:j2]:
                out.append(f"+ {line.strip()}")

    if not out:
        return "（页面无变化）"
    text = "\n".join(out)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n…（diff 已截断）"
    return text
