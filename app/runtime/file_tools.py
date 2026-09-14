"""文件操作工具集（主 agent 专属）。

一、设计来源与致谢
- **opencode**（https://github.com/anomalyco/opencode，**MIT License**）：
  read / edit / write 三件套——`read` 支持 `offset`+`limit` 按行读中段、`edit` 用
  `old_string`/`new_string`/`replace_all` 做精确字符串中段修改（含多个 fuzzy replacer
  容错行尾空白/缩进/换行符差异）、`write` 为整文件覆写（去掉了易出错的 append）。
  本项目为 **Python / langchain 重实现**，遵循原库语义，非逐行翻译。
- **Microsoft MarkItDown**（https://github.com/microsoft/markitdown，**MIT License**）：
  作为「富格式/文档文件 → Markdown」的转换引擎，用于**扩宽本 file tools 的格式适用范围**。
  见下方「三、富格式中间件」。它是**内嵌为本模块的函数**，不单独以 MCP 形式暴露给模型。
- 本项目根目录 LICENSE 为 **GNU GPL v3**。MIT 与 GPL-3.0 兼容：并入 GPL-3.0 项目后整体按
  GPL-3.0 分发，但**仍须保留各家原作者的版权声明与许可文本**（本 docstring 即满足保留声明）。

二、核心能力
- `read_file`：`offset`+`limit` 按行读中段，带行号 + 截断提示；目录列出条目。
- `edit_file`：精确字符串中段修改（旧文件 `old_string=""` 会被拒绝；新建文件 `old_string=""`）。
- `write_file`：整文件覆写。
- 路径安全：全部落点强制在 `root_dir` 内；二进制内容绝不喂给模型。

三、富格式中间件（内嵌为函数，不作为 MCP 暴露给模型）
  对 `.pdf .ppt .pptx .doc .docx .xls .xlsx .odt .ods .odp .epub .html .htm` 等富/专有格式：
  - **读取**：`read_file` 时先转成 Markdown，再按行分页返回，内容是 Markdown 文本。
    - **PDF 专用**：用 `pdfminer` 抽正文（空格与双栏阅读顺序可靠），再用 `pdfplumber` 的
      `find_tables()`（**基于框线**）抽表格，把表格按位置嵌回正文。这样既避免 MarkItDown 的
      pdfplumber 启发式（`x_tolerance=3`）把字距小的正文粘连成 `Publishedasaconferencepaper...`，
      又能拿到真正的线框表格。可用 `FileToolsConfig.pdf_table_extraction` 关闭表格提取。
    - **其余格式**：用 MarkItDown 转 Markdown。
  - **转换副本落盘**：读取**专有格式**（`_PROPRIETARY_DOC_EXTENSIONS`）时，会把完整转换结果
    **落成相同目录下的同名 `.md` 副本**（如 `the-new-SOTA-paper.pdf` → `the-new-SOTA-paper.md`；
    已存在则不覆盖，以免抹掉模型此前的编辑），并在返回里带 `<md_copy>` 路径与 `<note>` 提示。
    这样模型之后能**直接用 `edit_file` 改这个 `.md` 的几行**，而不必整文件 `write_file` 重写。
    注意 `.html/.htm` 只参与读取转换、不参与副本落盘与写入重定向。
  - **写入/编辑**：属于「只读支持」——**不能把这些格式写成二进制文档**。调用方若以这类扩展名写内容，
    会被**重定向为相同文件名的 `.md`**（如 `the-new-SOTA-paper.pdf` → `the-new-SOTA-paper.md`），
    从而避免模型写出的 Markdown 内容找不到、丢失。
  - 可选依赖：pdfminer/pdfplumber 随 `markitdown[pdf]` 安装；任一缺失时 PDF 会回退到 MarkItDown。
    MarkItDown 未安装时，`read_file` 对这些扩展名会返回明确的「转换不可用」错误；
    `write_file`/`edit_file` 的「重定向为 `.md`」不依赖转换库，仍照常生效；其余功能不受影响。
"""

from __future__ import annotations

import difflib
import fnmatch
import os
import re
import shutil
import threading
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

# 富/专有格式扩展名（不能被当作普通文本直接读，需用 MarkItDown 转成 Markdown 再读物）
_PROPRIETARY_DOC_EXTENSIONS = {
    ".pdf", ".ppt", ".pptx", ".doc", ".docx", ".xls", ".xlsx",
    ".odt", ".ods", ".odp", ".epub",
}

# MarkItDown 可转换的扩展名（在专有格式之上再宽一些，如 html/htm 也能转成更整洁的 Markdown）。
# 注意：html 只参与「读取转换」，不参与「写入重定向」，以免破坏 chat_ws 的 html 报告自动打开。
_MARKITDOWN_EXTENSIONS = _PROPRIETARY_DOC_EXTENSIONS | {".html", ".htm"}


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
# MarkItDown 富格式中间件（借用 microsoft/markitdown，内嵌为函数，不作为 MCP 暴露给模型）
# ---------------------------------------------------------------------------
_MARKITDOWN_INSTANCE = None
_MARKITDOWN_INIT_FAILED = False
_MARKITDOWN_LOCK = threading.Lock()


def _get_markitdown():
    """懒加载 MarkItDown 实例（进程级单例，与 root_dir 无关，可跨 agent 共享）。

    可选依赖：markitdown 未安装时返回 None，调用方回退到「按二进制拒绝」。
    """
    global _MARKITDOWN_INSTANCE, _MARKITDOWN_INIT_FAILED
    if _MARKITDOWN_INSTANCE is not None:
        return _MARKITDOWN_INSTANCE
    if _MARKITDOWN_INIT_FAILED:
        return None
    with _MARKITDOWN_LOCK:
        if _MARKITDOWN_INSTANCE is not None:
            return _MARKITDOWN_INSTANCE
        if _MARKITDOWN_INIT_FAILED:
            return None
        try:
            from markitdown import MarkItDown
            _MARKITDOWN_INSTANCE = MarkItDown(enable_plugins=False)
            return _MARKITDOWN_INSTANCE
        except Exception:
            _MARKITDOWN_INIT_FAILED = True
            return None


def _convert_to_markdown(path: str, pdf_table_extraction: bool = True) -> tuple[bool, str]:
    """把富格式文件转成 Markdown 文本。失败返回 (False, 原因)。

    - PDF：优先走「pdfminer 正文 + pdfplumber 基于框线表格」的自研路径；
      失败或结果为空时回退 MarkItDown。
    - 其余格式：走 MarkItDown。
    """
    if os.path.splitext(path)[1].lower() == ".pdf":
        ok, text_or_err = _convert_pdf_to_markdown(path, pdf_table_extraction)
        if ok:
            return True, text_or_err
        # PDF 路径失败：回退 MarkItDown；若 MarkItDown 也不可用，回传 PDF 侧的具体原因。
        md_ok, md_text = _convert_with_markitdown(path)
        if md_ok:
            return True, md_text
        return False, text_or_err or md_text

    return _convert_with_markitdown(path)


def _convert_with_markitdown(path: str) -> tuple[bool, str]:
    """用 MarkItDown 转换（可选依赖）。失败返回 (False, 原因)。"""
    md = _get_markitdown()
    if md is None:
        return False, "markitdown 未安装，无法转换该格式。"
    try:
        result = md.convert_local(path)
        text = result.markdown or ""
        if not text.strip():
            return False, "转换结果为空。"
        return True, text
    except Exception as e:
        return False, f"MarkItDown 转换失败：{e}"


def _is_markitdown_ext(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in _MARKITDOWN_EXTENSIONS


def _is_proprietary_doc_ext(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in _PROPRIETARY_DOC_EXTENSIONS


def _md_redirect_path(path: str) -> str:
    """把专有格式路径改为同名 .md 路径（如 x.pdf → x.md）。"""
    root, _ = os.path.splitext(path)
    return root + ".md"


def _materialize_md_copy(source_path: str, markdown: str) -> tuple[str, str]:
    """把富格式转换出的 Markdown 落成同目录同名 .md 副本。

    返回 (md_path, status)，status ∈ {"created", "exists", "failed"}。
    目标已存在时**不覆盖**（保留模型此前对 .md 的编辑）。
    """
    md_path = _md_redirect_path(source_path)
    if os.path.exists(md_path):
        return md_path, "exists"
    try:
        os.makedirs(os.path.dirname(md_path) or ".", exist_ok=True)
        with open(md_path, "w", encoding="utf-8", newline="") as f:
            f.write(markdown)
    except Exception:
        return md_path, "failed"
    return md_path, "created"


def _md_copy_note(source_path: str, md_path: str, status: str) -> str:
    """生成「这是转换副本，请编辑同名 .md」的提示，随 read_file 结果一起返回给模型。"""
    src_name = os.path.basename(source_path)
    md_name = os.path.basename(md_path)
    if status == "failed":
        return (
            f"这是由 {src_name} 转换得到的 Markdown 副本（保存为同目录 {md_name} 失败）。"
            f"如需修改，请用 write_file 把内容写为同名 {md_name}。"
            f" (This is a Markdown copy converted from {src_name}; saving it as {md_name} failed.)"
        )
    if status == "exists":
        return (
            f"这是由 {src_name} 转换得到的 Markdown 副本；同目录已存在 {md_name}（未覆盖，可能是你之前的编辑）。"
            f"如需修改（例如改几行），请直接用 edit_file 编辑 {md_name}，不要编辑原 {src_name}。"
            f" (Markdown copy of {src_name}; {md_name} already exists and was left untouched. Edit {md_name} with edit_file.)"
        )
    return (
        f"这是由 {src_name} 转换得到的 Markdown 副本，已保存为同目录的 {md_name}。"
        f"如需修改（例如改几行），请直接用 edit_file 编辑 {md_name}，不要编辑原 {src_name}"
        f"（PDF 等专有格式无法直接编辑，写入会自动改为同名 .md）。"
        f" (Markdown copy of {src_name}, saved as {md_name}. Use edit_file on {md_name} to change a few lines.)"
    )


# ---------------------------------------------------------------------------
# PDF 专用：pdfminer 抽正文 + pdfplumber 基于框线抽表格
# ---------------------------------------------------------------------------
# 为什么不用 MarkItDown 的 PDF 转换器：它用 pdfplumber 的默认 x_tolerance=3，
# 对字距很小的 PDF（LaTeX/ICLR 论文正文）不会补空格，导致整行单词粘连；且它的
# 「词坐标对齐」表格启发式不看框线，常把双栏正文/图注误判成表格。
# 这里改为：正文交给 pdfminer（空格 + 双栏阅读顺序可靠），表格交给 pdfplumber
# 的 find_tables()（默认 lines 策略，真正的框线检测）。


def _pdf_table_to_markdown(rows: list[list]) -> str:
    """把 pdfplumber 的二维表格转成管道 Markdown。"""
    norm_rows: list[list[str]] = []
    for row in rows:
        cells: list[str] = []
        for cell in row:
            text = "" if cell is None else str(cell)
            text = text.replace("\r", " ").replace("\n", "<br>").strip().replace("|", "\\|")
            cells.append(text)
        if any(cells):
            norm_rows.append(cells)
    if not norm_rows:
        return ""
    ncol = max(len(r) for r in norm_rows)
    norm_rows = [r + [""] * (ncol - len(r)) for r in norm_rows]
    # 去掉所有行都为空的多余列（pdfplumber 会按竖线给出整格宽度，常留空列）。
    keep = [c for c in range(ncol) if any(row[c] for row in norm_rows)]
    if not keep:
        return ""
    norm_rows = [[row[c] for c in keep] for row in norm_rows]
    ncol = len(keep)
    lines = [
        "| " + " | ".join(norm_rows[0]) + " |",
        "| " + " | ".join(["---"] * ncol) + " |",
    ]
    for row in norm_rows[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _is_useful_pdf_table(rows: list[list], page_area: float, bbox: tuple) -> bool:
    """过滤 pdfplumber 的误判（矢量图、整页外框等）。"""
    nrows = len(rows)
    ncols = max((len(r) for r in rows), default=0)
    if nrows < 2 or ncols < 2:
        return False
    filled = sum(1 for r in rows for c in r if c and str(c).strip())
    if filled < max(2, ncols):
        return False
    wordy = sum(1 for r in rows for c in r if c and re.search(r"[A-Za-z0-9]", str(c)))
    if wordy < 2:
        return False
    try:
        area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
    except Exception:
        area = 0
    # 几乎覆盖整页、且格子很少的，基本是页面外框，不是表格。
    if page_area > 0 and area / page_area > 0.9 and nrows * ncols <= 4:
        return False
    return True


def _extract_pdf_tables(path: str, enabled: bool) -> dict[int, list[tuple[tuple, str]]]:
    """逐页用 pdfplumber 基于框线检测表格，返回 {页索引: [(bbox, markdown)]}。"""
    if not enabled:
        return {}
    try:
        import pdfplumber
    except Exception:
        return {}

    tables: dict[int, list[tuple[tuple, str]]] = {}
    try:
        with pdfplumber.open(path) as pdf:
            for idx, page in enumerate(pdf.pages):
                page_area = float(page.width) * float(page.height)
                found: list[tuple[tuple, str]] = []
                try:
                    raw_tables = page.find_tables()
                except Exception:
                    raw_tables = []
                for table in raw_tables:
                    try:
                        rows = table.extract(x_tolerance=1.5)
                    except Exception:
                        continue
                    if not _is_useful_pdf_table(rows, page_area, table.bbox):
                        continue
                    md = _pdf_table_to_markdown(rows)
                    if md:
                        found.append((tuple(float(v) for v in table.bbox), md))
                tables[idx] = found
    except Exception:
        return {}
    return tables


def _is_vertical_text_noise(text: str, bbox: tuple, page_width: float) -> bool:
    """判断是否是竖排噪声（如 arXiv 侧边条）：窄、且几乎每行只有一个字符。"""
    if not (page_width > 0 and (bbox[2] - bbox[0]) < page_width * 0.1):
        return False
    stripped = text.strip()
    # 窄条里的纯符号（如竖排侧边条残留的 "]"）也是噪声。
    if len(stripped) <= 2 and not re.search(r"[A-Za-z0-9\u4e00-\u9fff]", stripped):
        return True
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 3:
        return False
    single = sum(1 for ln in lines if len(ln.strip()) == 1)
    return single / len(lines) > 0.6


def _extract_pdf_text_pages(path: str) -> list[tuple[float, list[tuple[tuple, str]]]]:
    """用 pdfminer 逐页抽取正文块，返回 [(页高, [(bbox, text)]), ...]，保持阅读顺序。"""
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LTTextBox

    pages: list[tuple[float, list[tuple[tuple, str]]]] = []
    for layout in extract_pages(path):
        height = float(layout.bbox[3])
        width = float(layout.bbox[2])
        blocks: list[tuple[tuple, str]] = []
        for element in layout:
            if isinstance(element, LTTextBox):
                text = element.get_text()
                if not (text and text.strip()):
                    continue
                bbox = tuple(float(v) for v in element.bbox)
                if _is_vertical_text_noise(text, bbox, width):
                    continue
                blocks.append((bbox, text))
        pages.append((height, blocks))
    return pages


def _merge_pdf_page(
    text_blocks: list[tuple[tuple, str]],
    tables: list[tuple[tuple, str]],
    page_height: float,
) -> str:
    """把某页的正文块与表格按纵向位置合并。"""
    # 表格 bbox 来自 pdfplumber（左上原点），转为 pdfminer（左下原点）。
    pm_tables: list[tuple[tuple, str]] = [
        ((x0, page_height - bottom, x1, page_height - top), md)
        for (x0, top, x1, bottom), md in tables
    ]
    pm_tables.sort(key=lambda item: item[0][3], reverse=True)  # 从页顶往下

    def _inside_table(bbox: tuple) -> bool:
        x0, y0, x1, y1 = bbox
        for (tx0, ty0, tx1, ty1), _ in pm_tables:
            if not (x1 < tx0 or x0 > tx1 or y1 < ty0 or y0 > ty1):
                return True
        return False

    seq: list[dict] = []
    for bbox, text in text_blocks:
        if _inside_table(bbox):
            continue
        seq.append({"kind": "text", "bbox": bbox, "text": text.rstrip()})

    for table_bbox, md in pm_tables:
        insert_at = len(seq)
        for i, item in enumerate(seq):
            if item["kind"] == "text" and item["bbox"][3] < table_bbox[1]:
                insert_at = i
                break
        seq.insert(insert_at, {"kind": "table", "bbox": table_bbox, "text": md})

    return "\n".join(item["text"] for item in seq if item["text"].strip())


def _convert_pdf_to_markdown(path: str, enable_tables: bool) -> tuple[bool, str]:
    """PDF → Markdown：pdfminer 抽正文 + pdfplumber 基于框线抽表格。"""
    try:
        text_pages = _extract_pdf_text_pages(path)
    except Exception as e:
        return False, f"pdfminer 解析失败：{e}"
    if not text_pages:
        return False, "PDF 未解析出任何页面。"

    tables_by_page = _extract_pdf_tables(path, enable_tables)

    page_mds: list[str] = []
    for idx, (height, blocks) in enumerate(text_pages):
        page_md = _merge_pdf_page(blocks, tables_by_page.get(idx, []), height)
        if page_md.strip():
            page_mds.append(page_md)

    text = "\n\n".join(page_mds).strip()
    if not text:
        return False, "转换结果为空。"
    return True, text


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
# 文本行分页（read_file 的文本 / MarkItDown 转出的 Markdown 共用）
# ---------------------------------------------------------------------------
def _paginate_lines(raw_lines: list[str], offset: Optional[int], limit: int) -> tuple[Optional[list[str]], str]:
    """按 offset/limit 对行列表分页。

    返回 (带行号的行列表, 尾注)；越界返回 (None, 错误信息)。
    """
    total = len(raw_lines)
    limit = max(1, limit)
    start = max(0, (offset or 1) - 1)
    if start >= total and total > 0:
        return None, f"Offset {offset} is out of range for this file ({total} lines)."

    selected = raw_lines[start : start + limit]
    body: list[str] = []
    byte_count = 0
    cut = False
    for i, line in enumerate(selected):
        line_no = start + i + 1
        text = line.rstrip("\r\n")
        if len(text) > MAX_LINE_LENGTH:
            text = text[:MAX_LINE_LENGTH] + MAX_LINE_SUFFIX
        size = len(text.encode("utf-8")) + (1 if body else 0)
        if byte_count + size > MAX_BYTES:
            cut = True
            break
        body.append(f"{line_no}: {text}")
        byte_count += size

    last_line = start + len(body)
    next_offset = last_line + 1
    more = start + len(selected) > last_line
    if cut:
        note = f"\n\n(Output capped at {MAX_BYTES_LABEL}. Showing lines {start + 1}-{last_line}. Use offset={next_offset} to continue.)"
    elif more:
        note = f"\n\n(Showing lines {start + 1}-{last_line} of {total}. Use offset={next_offset} to continue.)"
    else:
        note = f"\n\n(End of file - total {total} lines)"
    return body, note


# ---------------------------------------------------------------------------
# build_file_tools：用闭包把 root_dir 绑定到每个工具（每个 agent 一份）
# ---------------------------------------------------------------------------
def build_file_tools(root_dir: str, pdf_table_extraction: bool = True) -> list[BaseTool]:
    """构建绑定到指定 root_dir 的文件工具列表。

    pdf_table_extraction：读取 PDF 时是否用「基于框线」的表格提取并把表格嵌回正文。
    """
    root_dir = root_dir or os.getcwd()

    @langchain_tool
    def read_file(
        file_path: Annotated[
            str,
            "The path to the file or directory to read (relative to root_dir or absolute). "
            "富/专有格式（.pdf .ppt .pptx .doc .docx .xls .xlsx .odt .ods .odp .epub .html .htm）会自动转成 Markdown 返回，"
            "其中 PDF 的表格会以 Markdown 表格保留。"
            "仅支持读取。这些格式不支持写入为原格式——写内容请用 write_file 写为同名 .md 文件。"
            "读取专有格式（如 PDF）时，会在相同目录自动生成同名 .md 副本（返回里带 <md_copy> 路径），"
            "之后要修改请直接用 edit_file 编辑那个 .md（可只改几行），不要编辑原文件。",
        ],
        offset: Annotated[Optional[int], "The line number to start reading from (1-indexed). Defaults to 1."] = None,
        limit: Annotated[
            int, "The maximum number of lines to read (defaults to 2000). Use a smaller value to read mid-sections."
        ] = DEFAULT_READ_LIMIT,
    ) -> str:
        """读取文件或目录。

        对以下富/专有格式：pdf、ppt、pptx、doc、docx、xls、xlsx、odt、ods、odp、epub、html、htm，
        会自动转成 Markdown 后返回（内容是 Markdown 文本，并带行号分页）。其中 PDF 用 pdfminer 抽正文、
        用 pdfplumber 基于框线抽取表格；其余格式用 MarkItDown 转换。
        注意：这类格式**仅支持读取**（自动转换为 Markdown）——**不支持写入为原格式**；写内容时请改用同名 .md 文件
        （如把内容写到 the-new-SOTA-paper.pdf，实际会保存为 the-new-SOTA-paper.md）。
        读取专有格式时，会把完整转换结果**落成相同目录下的同名 .md 副本**（已存在则不覆盖），
        并在返回里带上 `<md_copy>` 路径与提示——模型随后可直接用 edit_file 编辑该 .md（改几行），
        不必整文件 write_file 重写；编辑原扩展名（如 .pdf）会自动重定向到同名 .md。
        普通文本文件仍按行带行号读取，支持 offset/limit 只读中段；目录则列出条目。
        """
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

        # 富格式：先转成 Markdown，再按行分页返回
        if _is_markitdown_ext(path):
            ok, text_or_err = _convert_to_markdown(path, pdf_table_extraction)
            if not ok:
                return f"Error: {text_or_err}"
            markdown = text_or_err
            ext = os.path.splitext(path)[1].lower()

            header = (
                f"<path>{path}</path>\n<type>file</type>\n<source_format>{ext}</source_format>\n<converted>true</converted>"
            )
            # 专有格式（写入会被重定向为同名 .md 的那批）：把完整转换结果落成同目录同名 .md 副本，
            # 并明确告诉模型「你读的是副本，之后用 edit_file 直接改这个 .md 即可（改几行），不用整文件重写」。
            if _is_proprietary_doc_ext(path):
                md_path, status = _materialize_md_copy(path, markdown)
                header += (
                    f"\n<md_copy>{md_path}</md_copy>"
                    f"\n<note>{_md_copy_note(path, md_path, status)}</note>"
                )

            raw_lines = markdown.splitlines(keepends=True)
            body, note = _paginate_lines(raw_lines, offset, limit)
            if body is None:
                return note
            return header + "\n<content>\n" + "\n".join(body) + note + "\n</content>"

        if _is_binary(path):
            return f"Cannot read binary file: {file_path}"

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                raw_lines = f.readlines()
        except OSError as e:
            return f"Error: {e}"

        body, note = _paginate_lines(raw_lines, offset, limit)
        if body is None:
            return note
        return (
            f"<path>{path}</path>\n<type>file</type>\n<content>\n"
            + "\n".join(body) + note + "\n</content>"
        )

    @langchain_tool
    def write_file(
        file_path: Annotated[
            str,
            "The path to the file to write (relative to root_dir or absolute). "
            "注意：不支持直接写入 .pdf .pptx .docx .xlsx 等专有格式文件；若 file_path 以此类扩展名结尾，"
            "内容会保存为相同文件名的 .md 文件（如 the-new-SOTA-paper.pdf 会写成 the-new-SOTA-paper.md）。",
        ],
        content: Annotated[str, "The full content to write to the file"],
    ) -> str:
        """覆写（或新建）整个文件。若只想改某一段，请改用 edit_file。"""
        try:
            path = _resolve_path(root_dir, file_path)
        except Exception as e:
            return f"Error: {e}"

        redirected = False
        target = path
        if _is_proprietary_doc_ext(path):
            target = _md_redirect_path(path)
            redirected = True

        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8", newline="") as f:
                f.write(content)
        except Exception as e:
            return f"Error: {e}"

        if redirected:
            ext = os.path.splitext(file_path)[1]
            return (
                f"Note: 不支持直接写入 {ext} 等专有格式文件；内容已保存为同名 Markdown 文件："
                f"{os.path.basename(target)}（原文件名：{os.path.basename(file_path)}）。"
                f"如需再读取该内容，请用 read_file 打开 {os.path.basename(target)}。"
            )
        return f"File written successfully to {file_path}."

    @langchain_tool
    def edit_file(
        file_path: Annotated[
            str,
            "The path to the file to modify (relative to root_dir or absolute). "
            "注意：不支持编辑 .pdf .pptx .docx .xlsx 等专有格式文件；若 file_path 以此类扩展名结尾，"
            "会改为编辑相同文件名的 .md 文件（如 the-new-SOTA-paper.pdf → the-new-SOTA-paper.md）。",
        ],
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

        redirected = False
        if _is_proprietary_doc_ext(path):
            path = _md_redirect_path(path)
            redirected = True

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
            msg = "Edit applied successfully (file created)."
            if redirected:
                msg += f" Note: 已保存为同名 .md 文件（专有格式不支持二进制编辑）。"
            return msg

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
        msg = "Edit applied successfully.\n\n" + (diff or "(no textual change)")
        if redirected:
            msg += f"\n\nNote: 编辑的是同名 .md 文件（{os.path.splitext(file_path)[1]} 专有格式不支持二进制编辑）。"
        return msg

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
