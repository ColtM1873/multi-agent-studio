"""文件操作工具集（主 agent 专属）。

灵感与设计借鉴自 opencode 的文件工具（read / edit / write）：
- read 支持 `offset`（1 起始行号）+ `limit`（最大行数），可「从中间按行读取」；
- edit 用 `old_string` / `new_string` / `replace_all` 做「精确到字符串的中段修改」，
  并用多个 fuzzy replacer 容错（行尾空白、缩进、换行符等差异）；
- write 是整文件覆盖写，不再提供易出错、易超上下文上限的 append。

来源：https://github.com/anomalyco/opencode （MIT License）。
本项目为 Python / langchain 重实现，遵循原库语义，非逐行翻译。
"""

from __future__ import annotations

import difflib
import fnmatch
import os
import re
import shutil
from typing import Annotated, Callable, Iterator, Optional

from langchain_core.tools import tool as langchain_tool
from langchain_core.tools.base import BaseTool

# 与 opencode 一致的读取上限
DEFAULT_READ_LIMIT = 2000
MAX_LINE_LENGTH = 2000
MAX_LINE_SUFFIX = f"... (line truncated to {MAX_LINE_LENGTH} chars)"
MAX_BYTES = 50 * 1024
MAX_BYTES_LABEL = f"{MAX_BYTES // 1024} KB"

# 二进制文件扩展名黑名单（避免把二进制内容喂给 LLM）
_BINARY_EXTENSIONS = {
    ".zip", ".tar", ".gz", ".exe", ".dll", ".so", ".class", ".jar", ".war", ".7z",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp",
    ".bin", ".dat", ".obj", ".o", ".a", ".lib", ".wasm", ".pyc", ".pyo",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".pdf",
}


# ---------------------------------------------------------------------------
# 路径解析（安全：强制落在 root_dir 内）
# ---------------------------------------------------------------------------
def _resolve_path(root_dir: str, file_path: str) -> str:
    """把 `file_path` 解析到 root_dir 内的绝对路径，越界则抛错。"""
    root = os.path.abspath(root_dir or os.getcwd())
    candidate = os.path.abspath(file_path) if os.path.isabs(file_path) else os.path.abspath(os.path.join(root, file_path))
    root_norm = os.path.normcase(root)
    cand_norm = os.path.normcase(candidate)
    try:
        common = os.path.commonpath([root_norm, cand_norm])
    except ValueError:  # 跨盘符（Windows 不同盘）
        common = ""
    if common != root_norm:
        raise ValueError(f"file_path 超出允许的 root_dir：{file_path}")
    return candidate


def _is_binary(path: str) -> bool:
    if os.path.splitext(path)[1].lower() in _BINARY_EXTENSIONS:
        return True
    try:
        with open(path, "rb") as f:
            sample = f.read(4096)
    except OSError:
        return False
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    nonprintable = sum(1 for b in sample if b < 9 or (13 < b < 32))
    return nonprintable / len(sample) > 0.3


# ---------------------------------------------------------------------------
# edit_file 的 fuzzy replacer
# ---------------------------------------------------------------------------
Replacer = Callable[[str, str], Iterator[str]]


def _simple(content: str, find: str) -> Iterator[str]:
    if find:
        yield find


def _line_trimmed(content: str, find: str) -> Iterator[str]:
    original_lines = content.split("\n")
    search_lines = find.split("\n")
    if search_lines and search_lines[-1] == "":
        search_lines.pop()
    if not search_lines:
        return
    for i in range(0, len(original_lines) - len(search_lines) + 1):
        if all(
            original_lines[i + j].strip() == search_lines[j].strip()
            for j in range(len(search_lines))
        ):
            start_idx = sum(len(original_lines[k]) + 1 for k in range(i))
            end_idx = start_idx + sum(
                len(original_lines[i + k]) + (1 if k < len(search_lines) - 1 else 0)
                for k in range(len(search_lines))
            )
            yield content[start_idx:end_idx]


def _whitespace_normalized(content: str, find: str) -> Iterator[str]:
    def norm(t: str) -> str:
        return re.sub(r"\s+", " ", t).strip()

    nfind = norm(find)
    if not nfind:
        return
    lines = content.split("\n")
    for line in lines:
        lnorm = norm(line)
        if lnorm == nfind:
            yield line
        elif lnorm.find(nfind) != -1:
            words = [re.escape(w) for w in find.strip().split() if w]
            if words:
                m = re.search("\\s+".join(words), line)
                if m:
                    yield m.group(0)
    flines = find.split("\n")
    if len(flines) > 1:
        for i in range(0, len(lines) - len(flines) + 1):
            block = "\n".join(lines[i : i + len(flines)])
            if norm(block) == nfind:
                yield block


def _indentation_flexible(content: str, find: str) -> Iterator[str]:
    def strip_indent(text: str) -> str:
        ls = text.split("\n")
        non_empty = [l for l in ls if l.strip()]
        if not non_empty:
            return text
        min_indent = min(len(l) - len(l.lstrip()) for l in non_empty)
        return "\n".join(l[min_indent:] if l.strip() else l for l in ls)

    nfind = strip_indent(find)
    clines = content.split("\n")
    flines = find.split("\n")
    for i in range(0, len(clines) - len(flines) + 1):
        block = "\n".join(clines[i : i + len(flines)])
        if strip_indent(block) == nfind:
            yield block


def _escape_normalized(content: str, find: str) -> Iterator[str]:
    def unescape(s: str) -> str:
        return re.sub(
            r"\\(n|t|r|'|\"|`|\$)",
            lambda m: {"n": "\n", "t": "\t", "r": "\r", "$": "$"}.get(m.group(1), m.group(1)),
            s,
        )

    unescaped = unescape(find)
    if unescaped and content.find(unescaped) != -1:
        yield unescaped
    lines = content.split("\n")
    ufind_lines = unescaped.split("\n")
    if len(ufind_lines) > 1:
        for i in range(0, len(lines) - len(ufind_lines) + 1):
            block = "\n".join(lines[i : i + len(ufind_lines)])
            if unescape(block) == unescaped:
                yield block


def _trimmed_boundary(content: str, find: str) -> Iterator[str]:
    trimmed = find.strip()
    if not trimmed or trimmed == find:
        return
    if content.find(trimmed) != -1:
        yield trimmed
    lines = content.split("\n")
    flines = find.split("\n")
    for i in range(0, len(lines) - len(flines) + 1):
        block = "\n".join(lines[i : i + len(flines)])
        if block.strip() == trimmed:
            yield block


def _multi_occurrence(content: str, find: str) -> Iterator[str]:
    if not find:
        return
    idx = 0
    while True:
        idx = content.find(find, idx)
        if idx == -1:
            break
        yield find
        idx += len(find)


_REPLACERS: list[Replacer] = [
    _simple,
    _line_trimmed,
    _whitespace_normalized,
    _indentation_flexible,
    _escape_normalized,
    _trimmed_boundary,
    _multi_occurrence,
]


def _normalize_ending(text: str) -> str:
    return text.replace("\r\n", "\n")


def _to_ending(text: str, ending: str) -> str:
    return text if ending == "\n" else text.replace("\n", "\r\n")


def _replace(content: str, old: str, new: str, replace_all: bool = False) -> str:
    if old == new:
        raise ValueError("没有改动可执行：old_string 与 new_string 相同。")
    if old == "":
        raise ValueError("old_string 不能为空（对已存在文件）。")

    found_any = False
    for replacer in _REPLACERS:
        for search in replacer(content, old):
            index = content.find(search)
            if index == -1:
                continue
            found_any = True
            if replace_all:
                return content.replace(search, new)
            last = content.rfind(search)
            if index != last:
                continue
            return content[:index] + new + content[index + len(search) :]

    if not found_any:
        raise ValueError(
            "Could not find old_string in the file. 它必须精确匹配，包括空白、缩进与换行（如有差异请从 read_file 输出重新复制）。"
        )
    raise ValueError("找到多处匹配的 old_string。请提供更多上下文以唯一确定（或用 replace_all=true 逐处替换）。")


# ---------------------------------------------------------------------------
# build_file_tools：用闭包把 root_dir 绑定到每个工具（每个 agent 一份）
# ---------------------------------------------------------------------------
def build_file_tools(root_dir: str) -> list[BaseTool]:
    """构建绑定到指定 root_dir 的文件工具列表。"""
    root_dir = root_dir or os.getcwd()

    @langchain_tool
    def read_file(
        file_path: Annotated[str, "The path to the file or directory to read (relative to root_dir or absolute)"],
        offset: Annotated[Optional[int], "The line number to start reading from (1-indexed). Defaults to 1."] = None,
        limit: Annotated[
            int, "The maximum number of lines to read (defaults to 2000). Use a smaller value to read mid-sections."
        ] = DEFAULT_READ_LIMIT,
    ) -> str:
        """读取文件或目录。文件按行读取并带行号，支持 offset/limit 只读中段；目录列出条目。"""
        try:
            path = _resolve_path(root_dir, file_path)
        except Exception as e:
            return f"Error: {e}"

        if os.path.isdir(path):
            try:
                items = sorted(
                    name + "/" if os.path.isdir(os.path.join(path, name)) else name
                    for name in os.listdir(path)
                )
            except OSError as e:
                return f"Error: {e}"
            limit = max(1, limit)
            start = (offset or 1) - 1
            sliced = items[start : start + limit]
            total = len(items)
            truncated = start + len(sliced) < total
            note = (
                f"\n(Showing {len(sliced)} of {total} entries. Use 'offset' to read beyond entry {start + len(sliced)})"
                if truncated
                else f"\n({total} entries)"
            )
            return f"<path>{path}</path>\n<type>directory</type>\n<entries>\n" + "\n".join(sliced) + note + "\n</entries>"

        if not os.path.exists(path):
            return f"Error: no such file or directory: {file_path}"
        if not os.path.isfile(path):
            return f"Error: not a regular file: {file_path}"
        if _is_binary(path):
            return f"Cannot read binary file: {file_path}"

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                raw_lines = f.readlines()
        except OSError as e:
            return f"Error: {e}"

        total = len(raw_lines)
        limit = max(1, limit)
        start = max(0, (offset or 1) - 1)
        if start >= total and not (start == 0 and total == 0):
            return f"Offset {offset} is out of range for this file ({total} lines)."

        selected = raw_lines[start : start + limit]
        out_lines: list[str] = []
        byte_count = 0
        cut = False
        for i, line in enumerate(selected):
            line_no = start + i + 1
            text = line.rstrip("\r\n")
            if len(text) > MAX_LINE_LENGTH:
                text = text[:MAX_LINE_LENGTH] + MAX_LINE_SUFFIX
            size = len(text.encode("utf-8")) + (1 if out_lines else 0)
            if byte_count + size > MAX_BYTES:
                cut = True
                break
            out_lines.append(f"{line_no}: {text}")
            byte_count += size

        last_line = start + len(out_lines)
        next_offset = last_line + 1
        more = start + len(selected) > last_line
        if cut:
            note = f"\n\n(Output capped at {MAX_BYTES_LABEL}. Showing lines {start + 1}-{last_line}. Use offset={next_offset} to continue.)"
        elif more:
            note = f"\n\n(Showing lines {start + 1}-{last_line} of {total}. Use offset={next_offset} to continue.)"
        else:
            note = f"\n\n(End of file - total {total} lines)"
        return (
            f"<path>{path}</path>\n<type>file</type>\n<content>\n"
            + "\n".join(out_lines)
            + note
            + "\n</content>"
        )

    @langchain_tool
    def write_file(
        file_path: Annotated[str, "The path to the file to write (relative to root_dir or absolute)"],
        content: Annotated[str, "The full content to write to the file"],
    ) -> str:
        """覆写（或新建）整个文件。若只想改某一段，请改用 edit_file。"""
        try:
            path = _resolve_path(root_dir, file_path)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(content)
        except Exception as e:
            return f"Error: {e}"
        return f"File written successfully to {file_path}."

    @langchain_tool
    def edit_file(
        file_path: Annotated[str, "The path to the file to modify (relative to root_dir or absolute)"],
        old_string: Annotated[str, "The text to replace"],
        new_string: Annotated[str, "The text to replace it with (must be different from old_string)"],
        replace_all: Annotated[
            bool, "Replace all occurrences of old_string (default false). Use for renaming across the file."
        ] = False,
    ) -> str:
        """对文件做精确的字符串替换，用于修改文件中段内容。

        old_string / new_string 必须是文件中的真实内容（含正确的缩进与换行），
        可容忍少量空白/缩进/换行符差异。新建文件时 old_string 传空字符串。
        """
        try:
            path = _resolve_path(root_dir, file_path)
        except Exception as e:
            return f"Error: {e}"

        if old_string == new_string:
            return "Error: 没有改动可执行：old_string 与 new_string 相同。"

        if not os.path.exists(path):
            if old_string != "":
                return f"Error: File {path} not found. 若想新建文件，请 old_string 传空字符串或使用 write_file。"
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.write(new_string)
            except Exception as e:
                return f"Error: {e}"
            return "Edit applied successfully (file created)."

        if os.path.isdir(path):
            return f"Error: Path is a directory, not a file: {file_path}"
        if old_string == "":
            return "Error: old_string 不能为空（对已存在文件）。要整文件覆写请用 write_file。"

        try:
            with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
                content_old = f.read()
        except OSError as e:
            return f"Error: {e}"

        ending = "\r\n" if "\r\n" in content_old else "\n"
        old = _to_ending(_normalize_ending(old_string), ending)
        new = _to_ending(_normalize_ending(new_string), ending)
        try:
            content_new = _replace(content_old, old, new, replace_all)
        except Exception as e:
            return f"Error: {e}"

        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(content_new)
        except OSError as e:
            return f"Error: {e}"

        diff = "".join(
            difflib.unified_diff(
                content_old.splitlines(keepends=True),
                content_new.splitlines(keepends=True),
                fromfile=path,
                tofile=path,
            )
        ).strip()
        return "Edit applied successfully.\n\n" + (diff or "(no textual change)")

    @langchain_tool
    def list_directory(
        path: Annotated[str, "The directory path to list (relative to root_dir or absolute)"],
    ) -> str:
        """列出目录下的条目。"""
        try:
            resolved = _resolve_path(root_dir, path)
        except Exception as e:
            return f"Error: {e}"
        if not os.path.isdir(resolved):
            return f"Error: not a directory: {path}"
        try:
            items = sorted(
                name + "/" if os.path.isdir(os.path.join(resolved, name)) else name
                for name in os.listdir(resolved)
            )
        except OSError as e:
            return f"Error: {e}"
        return "\n".join(items) if items else "(empty directory)"

    @langchain_tool
    def copy_file(
        source_path: Annotated[str, "The source file path"],
        destination_path: Annotated[str, "The destination file path"],
    ) -> str:
        """复制一个文件。"""
        try:
            src = _resolve_path(root_dir, source_path)
            dst = _resolve_path(root_dir, destination_path)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        except Exception as e:
            return f"Error: {e}"
        return f"File copied from {source_path} to {destination_path}."

    @langchain_tool
    def move_file(
        source_path: Annotated[str, "The source file path"],
        destination_path: Annotated[str, "The destination file path"],
    ) -> str:
        """移动一个文件或目录。"""
        try:
            src = _resolve_path(root_dir, source_path)
            dst = _resolve_path(root_dir, destination_path)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)
        except Exception as e:
            return f"Error: {e}"
        return f"File moved from {source_path} to {destination_path}."

    @langchain_tool
    def file_delete(
        file_path: Annotated[str, "The path to the file or directory to delete"],
    ) -> str:
        """删除一个文件或目录。"""
        try:
            resolved = _resolve_path(root_dir, file_path)
            if os.path.isdir(resolved):
                shutil.rmtree(resolved)
            else:
                os.remove(resolved)
        except Exception as e:
            return f"Error: {e}"
        return f"File deleted: {file_path}."

    @langchain_tool
    def file_search(
        pattern: Annotated[str, "The regex pattern to search for"],
        path: Annotated[str, "The file or directory to search in (relative to root_dir or absolute)"],
        glob: Annotated[Optional[str], "Optional file-name glob filter (e.g. '*.py')"] = None,
    ) -> str:
        """在文件或目录中按正则查找文本，返回匹配的 `文件:行号: 内容`。"""
        try:
            resolved = _resolve_path(root_dir, path)
        except Exception as e:
            return f"Error: {e}"
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return f"Error: invalid regex: {e}"

        targets: list[str]
        if os.path.isfile(resolved):
            targets = [resolved]
        elif os.path.isdir(resolved):
            targets = [
                os.path.join(dirpath, fn)
                for dirpath, _dirs, filenames in os.walk(resolved)
                for fn in filenames
                if not glob or fnmatch.fnmatch(fn, glob)
            ]
        else:
            return f"Error: no such file or directory: {path}"

        results: list[str] = []
        for target in targets:
            if _is_binary(target):
                continue
            try:
                with open(target, "r", encoding="utf-8", errors="replace") as f:
                    for no, line in enumerate(f, 1):
                        if rx.search(line):
                            results.append(f"{os.path.basename(target)}:{no}: {line.rstrip()}")
            except OSError:
                continue

        if not results:
            return "No matches found."
        return "\n".join(results[:500])

    return [
        read_file,
        edit_file,
        write_file,
        list_directory,
        copy_file,
        move_file,
        file_delete,
        file_search,
    ]
