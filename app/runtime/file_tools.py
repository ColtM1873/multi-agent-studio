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
      又能拿到真正的线框表格。可用全局设置 `Settings.pdf_table_extraction` 关闭表格提取。
      另外：矢量图内的文字（Visio/Word 子集字体没有 Unicode 映射，任何提取器都只能得到
      `!"#$%&'` 这类乱码）会被识别并替换成一条**占位提示**，不再输出乱码；数学字体
      （LaTeX txfonts 等）的符号按 PDF 里的字形名还原（∑ / ≠ / 矩阵括号 / `(cid:N)`）；
      页脚页码会被剔除、双栏页面按「左栏读到底再读右栏」排序、行尾连字符会合并。
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


def convert_file_to_markdown(path: str, pdf_table_extraction: bool = True) -> tuple[bool, str]:
    """把富格式文件转成 Markdown 文本（供 file tools 之外的调用方复用）。

    会话页「将本地PDF转换为md」按钮走这里：与 `read_file` 完全同一套转换路径
    （PDF 用 pdfminer 正文 + pdfplumber 框线表格；其余格式用 MarkItDown），
    `pdf_table_extraction` 即进阶设置里的「📄 PDF 表格提取」。
    返回 (是否成功, Markdown 文本或失败原因)。
    """
    return _convert_to_markdown(path, pdf_table_extraction)


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
#
# 2026-11 质量修复（P0/P1/P2）：
# - **图内文字**：矢量图里的文字常来自 Visio/Word 子集字体（ArialMT /
#   TimesNewRomanPSMT / Calibri…），这些字体**没有 ToUnicode**，字符码被重排成
#   连续 ASCII，任何提取器（pdfminer / pdfplumber / pypdfium2 实测一致）都只能得到
#   `!"#$%&'` 这类乱码。判断方式：字体不在数学字体白名单、且「字母数字占比」低于
#   阈值（实测图内 0.00–0.40、正文 0.83–1.00）。判定为图内文字后**不输出乱码**，
#   只留一条占位提示。
# - **假表格**：矢量图的框线会被 find_tables()（lines 策略）当成真表格。除原有的
#   行/列/填充数过滤外，再要求「内容可读」（字母数字占比 >= 0.6）；不可读的候选
#   记为 garbage，只输出占位，不输出乱码表格。
# - **数学字体还原**：LaTeX 用 txfonts（txex/txsy…）时同样没有 ToUnicode，pdfminer
#   会把「字体编码」原样输出（如码位 0x58 的 summationdisplay 变成字母 `X`、txsyc
#   码位 0x2C 的 nequal 变成 `,`）。PDF 的 `/Differences` 里有确定字形名，按
#   「字体 + 解码字符」还原：`X`→`∑`、`,`→`≠`、PUA→⎡⎢⎣⎤⎥⎦、(cid:2/3)→[]。
# - **版面**：剔除页脚/页码；双栏页面按「左栏读到底再读右栏」重排；合并行尾连字符。
# - **Markdown 安全**：表格单元格转义 `$ _ * ` [ ] ~ & < > #`，表格块之间留空行，
#   避免前端 texmath 把乱码里的 `$...$` 当公式渲染出 KaTeX ParseError。

# 数学字体（TeX/LaTeX 与 Word 公式常用）：字符大多是可读的符号，不能按「可读率低」
# 判成图内噪声，否则公式会被整块丢弃。
_PDF_MATH_FONT_PREFIXES = (
    "tx", "rtx", "MSBM", "CM", "LatinModernMath", "STIX", "XITS", "Asana",
    "Euclid", "Symbol", "msam", "msbm", "cmmi", "cmsy", "cmex", "cmr",
)

# txfonts 无 ToUnicode 时的「字体 + 解码字符 → 正确 Unicode」映射（来自 PDF 的
# /Differences：txex 码位 88 是 summationdisplay；txsyc 码位 44 是 nequal）。
# 只收录能确证的字形，宁缺毋滥：映射错比不映射更糟。
_PDF_MATH_GLYPH_MAP: dict[str, dict[str, str]] = {
    "txex": {"X": "\u2211"},   # summationdisplay → ∑
    "txsyc": {",": "\u2260"},  # nequal → ≠
}

# txex 的括号延展构件（/Differences: bracketlefttp/ex/bt、bracketrighttp/ex/bt）。
# pdfminer 对缺失 Unicode 映射的码位会输出私有区字符（U+F8EE…），这里还原成
# Unicode 的「括号组件」，让矩阵的左右括号重新可见。
_PDF_PUA_GLYPH_MAP = {
    "\uf8ee": "\u23a1",  # bracketlefttp  → ⎡
    "\uf8ef": "\u23a2",  # bracketleftex  → ⎢
    "\uf8f0": "\u23a3",  # bracketleftbt  → ⎣
    "\uf8f9": "\u23a4",  # bracketrighttp → ⎤
    "\uf8fa": "\u23a5",  # bracketrightex → ⎥
    "\uf8fb": "\u23a6",  # bracketrightbt → ⎦
}

# pdfminer 无法解码时的 `(cid:N)` 占位（txex：2/3=bracketleftbig/rightbig，
# 16/17=parenleftBig/rightBig，50..55=括号延展件）。
_PDF_CID_GLYPH_MAP = {
    "2": "[", "3": "]",
    "16": "(", "17": ")",
    "50": "\u23a1", "51": "\u23a4", "52": "\u23a3",
    "53": "\u23a6", "54": "\u23a2", "55": "\u23a5",
}

# 图内文字被识别出来后输出的占位（保留「这里原本有图内文字」的信息，不输出乱码）。
_FIGURE_TEXT_PLACEHOLDER = "> [图内文字无法可靠提取，已省略（图形区域文字缺少 Unicode 映射）]"

# 可读率阈值：图内乱码实测 0.00–0.40，正文 0.83–1.00，取 0.6 两侧都有余量。
_READABLE_RATIO_THRESHOLD = 0.6


def _pdf_font_base(fontname: str) -> str:
    """去掉子集前缀（如 `JTOLEZ+txex` → `txex`）。"""
    return (fontname or "").split("+")[-1]


def _is_pdf_math_font(fontname: str) -> bool:
    """是否是数学/公式字体（这些字体的低可读率是正常的，不能当图内噪声丢掉）。"""
    return _pdf_font_base(fontname).startswith(_PDF_MATH_FONT_PREFIXES)


def _readable_ratio(text: str) -> float:
    """「字母数字 / 非空白字符」占比：乱码里的符号多，正文里字母多。"""
    chars = [c for c in (text or "") if not c.isspace()]
    if not chars:
        return 0.0
    return sum(1 for c in chars if c.isalnum()) / len(chars)


def _map_pdf_text_segment(text: str, fontname: str) -> str:
    """把一个 LTChar 的文字按字体映射回正确的 Unicode。"""
    if not text:
        return ""
    # pdfminer 对无 Unicode 映射的字符输出 `(cid:N)`，整段替换（不是逐字符）。
    m = re.fullmatch(r"\(cid:(\d+)\)", text)
    if m:
        if _is_pdf_math_font(fontname):
            return _PDF_CID_GLYPH_MAP.get(m.group(1), text)
        return text
    base = _pdf_font_base(fontname)
    glyph_map = _PDF_MATH_GLYPH_MAP.get(base)
    out = []
    for ch in text:
        if ch in _PDF_PUA_GLYPH_MAP:
            out.append(_PDF_PUA_GLYPH_MAP[ch])
        elif glyph_map and ch in glyph_map:
            out.append(glyph_map[ch])
        else:
            out.append(ch)
    return "".join(out)


def _iter_lt_pieces(element):
    """递归取出 pdfminer 布局元素下的字符/标注，**保留顺序与字体**。

    关键：不能只取 LTChar——PDF 里的空格往往不是字形，而是 pdfminer 生成的
    `LTAnno(" ")`。只拼 LTChar 会把整行单词粘在一起（实测 `AdversarialFiltering...`）。
    LTAnno 没有字体，返回 font=None，映射时原样保留。
    """
    from pdfminer.layout import LTAnno, LTChar

    for child in element:
        if isinstance(child, LTChar):
            yield (child.fontname or "", child.get_text())
        elif isinstance(child, LTAnno):
            yield (None, child.get_text())
        elif hasattr(child, "__iter__"):
            try:
                yield from _iter_lt_pieces(child)
            except TypeError:
                pass


def _pdf_font_family(fontname: str) -> str:
    """字体族（`NimbusRomNo9L-Medi` → `NimbusRomNo9L`），用于判断「正文主字体」。"""
    base = _pdf_font_base(fontname)
    return base.split("-")[0] if base else ""


def _is_page_furniture(bbox: tuple, page_height: float, text: str) -> bool:
    """判断是否是页脚/页码/页眉这类版面家具（不是正文）。"""
    stripped = (text or "").strip()
    if not stripped or len(stripped) > 90:
        return False
    if (bbox[3] - bbox[1]) > 22:  # 多行大块不算
        return False
    bottom_gap = bbox[1]
    top_gap = page_height - bbox[3]
    # 底部 12%（页脚常见区）或顶部 5%（页眉常见区）；顶部取更保守，避免误删正文首行。
    return bottom_gap < page_height * 0.12 or top_gap < page_height * 0.05


def _is_figure_text_block(text: str, fontname: str, main_families: set[str]) -> bool:
    """判断文本块是否来自矢量图内部（字体无 Unicode 映射 → 乱码）。

    三个条件同时满足才算：不是数学字体（公式符号多但可读）、字体族不是本页正文主字体、
    字母数字占比低于阈值。这样 `xi(1, 1)`（正文主字体、占比 0.5）不会被误杀，而
    `!"#$%&'`（图内 ArialMT、占比 0.0）会被识别。
    """
    stripped = (text or "").strip()
    if not stripped:
        return False
    if _is_pdf_math_font(fontname):
        return False
    if _pdf_font_family(fontname) in main_families:
        return False
    # 图内乱码常是「数字+符号」拼成的（如 `355',4.2'$678`，字母占比 0），
    # 单看字母数字占比会误判成可读；这里补一条「无字母 + 有一定长度」的判据。
    letters = sum(1 for c in stripped if c.isalpha())
    if len(stripped) >= 4 and letters == 0:
        return True
    return _readable_ratio(stripped) < _READABLE_RATIO_THRESHOLD


def _dehyphenate(text: str) -> str:
    """合并行尾连字符（`Evasion at-\\ntacks` → `Evasion attacks`）。

    只合并「连字符 + 换行 + 以字母结尾的小写词」：`set-\\nat-` 这类 PDF 内容流错乱
    产生的散词（两个不同词的前半）不该被粘成 `setat-`，所以后面紧跟字母/连字符时不合并。
    """
    return re.sub(
        r"([A-Za-z\u00c0-\u024f])-\n([a-z\u00e0-\u00ff]{2,})(?![A-Za-z-])",
        r"\1\2",
        text or "",
    )


def _bbox_overlaps(a: tuple, b: tuple) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def _dedupe_nested_boxes(boxes: list[tuple]) -> list[tuple]:
    """去掉被其它框完全包含的框（pdfplumber 对同一张图常给出多层候选）。"""
    kept: list[tuple] = []
    for bbox in sorted(boxes, key=lambda b: -((b[2] - b[0]) * (b[3] - b[1]))):
        contained = False
        for other in kept:
            if (
                bbox[0] >= other[0] - 1
                and bbox[1] >= other[1] - 1
                and bbox[2] <= other[2] + 1
                and bbox[3] <= other[3] + 1
            ):
                contained = True
                break
        if not contained:
            kept.append(bbox)
    return kept


def _escape_md_cell(text: str) -> str:
    """把表格单元格里的 Markdown/LaTeX 敏感字符转义，避免污染整篇渲染。

    - `\\` 先转义，避免后续替换产生新的转义序列；
    - `& < >` 走 HTML 实体（反斜杠转义在 CommonMark 里对它们无效）；
    - `$` 必须转义：否则前端 markdown-it-texmath 会把乱码里的 `$...$` 当公式，
      渲染出 KaTeX ParseError（本轮问题的直接触发点）；
    - `_ * ` [ ] ~ #` 转成字面量，避免被当成强调/代码/链接/标题。
    """
    s = str(text).replace("\\", "\\\\")
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for ch in ("$", "_", "*", "`", "[", "]", "~", "#"):
        s = s.replace(ch, "\\" + ch)
    return s


def _pdf_table_to_markdown(rows: list[list]) -> str:
    """把 pdfplumber 的二维表格转成管道 Markdown。"""
    norm_rows: list[list[str]] = []
    for row in rows:
        cells: list[str] = []
        for cell in row:
            text = "" if cell is None else str(cell)
            # 单元格内换行改 <br>：先占位再转义，最后还原，避免 < 被转成 &lt;
            text = text.replace("\r", " ").replace("\n", "\x00BR\x00").strip()
            text = _escape_md_cell(text).replace("\x00BR\x00", "<br>")
            text = text.replace("|", "\\|")
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
    """过滤 pdfplumber 的误判（矢量图、整页外框、乱码图注等）。"""
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
    # 内容必须「像人话」：矢量图里的乱码（!"#$%&' 等）可读率极低，不能当表格输出。
    flat = " ".join(str(c) for r in rows for c in r if c and str(c).strip())
    if _readable_ratio(flat) < _READABLE_RATIO_THRESHOLD:
        return False
    try:
        area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
    except Exception:
        area = 0
    # 几乎覆盖整页、且格子很少的，基本是页面外框，不是表格。
    if page_area > 0 and area / page_area > 0.9 and nrows * ncols <= 4:
        return False
    return True


def _looks_like_garbage_table(rows: list[list], bbox: tuple, page_area: float) -> bool:
    """判断被过滤的表格候选是否「本应是一张图/图注」——用于输出占位。

    只对「有基本表格形状 + 面积合理 + 内容可读率低」的候选返回 True；
    1×1 的小误判、覆盖整页的外框都不会走到占位。
    """
    nrows = len(rows)
    ncols = max((len(r) for r in rows), default=0)
    if nrows < 2 or ncols < 2:
        return False
    flat = " ".join(str(c) for r in rows for c in r if c and str(c).strip())
    if not flat.strip():
        return False
    try:
        area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
    except Exception:
        area = 0
    if page_area > 0:
        ratio = area / page_area
        if ratio < 0.005 or ratio > 0.9:
            return False
    return _readable_ratio(flat) < _READABLE_RATIO_THRESHOLD


def _extract_pdf_tables(path: str, enabled: bool) -> dict[int, dict]:
    """逐页用 pdfplumber 基于框线检测表格。

    返回 {页索引: {"useful": [(bbox, markdown)], "garbage": [bbox]}}：
    - useful：内容可读、按管道表格输出；
    - garbage：形状像表格但内容不可读（矢量图的框线/图注乱码），只输出占位。
    bbox 为 pdfplumber 坐标（左上原点）。
    """
    if not enabled:
        return {}
    try:
        import pdfplumber
    except Exception:
        return {}

    tables: dict[int, dict] = {}
    try:
        with pdfplumber.open(path) as pdf:
            for idx, page in enumerate(pdf.pages):
                page_area = float(page.width) * float(page.height)
                useful: list[tuple[tuple, str]] = []
                garbage: list[tuple] = []
                try:
                    raw_tables = page.find_tables()
                except Exception:
                    raw_tables = []
                for table in raw_tables:
                    try:
                        rows = table.extract(x_tolerance=1.5)
                    except Exception:
                        continue
                    bbox = tuple(float(v) for v in table.bbox)
                    if _is_useful_pdf_table(rows, page_area, bbox):
                        md = _pdf_table_to_markdown(rows)
                        if md:
                            useful.append((bbox, md))
                    elif _looks_like_garbage_table(rows, bbox, page_area):
                        garbage.append(bbox)
                tables[idx] = {
                    "useful": useful,
                    "garbage": _dedupe_nested_boxes(garbage),
                }
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


def _extract_pdf_text_pages(path: str) -> list[tuple[float, float, list[tuple[tuple, str, bool]]]]:
    """用 pdfminer 逐页抽取正文块，保持阅读顺序。

    返回 [(页高, 页宽, [(bbox, text, 是否图内文字)])]：
    - text 已按字体做过数学符号映射（∑ / ≠ / 括号 / (cid:N)），并保留 LTAnno 空格；
    - 「是否图内文字」按「字体族是否本页正文主字体 + 字母数字占比」判定。
    """
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LTTextBox

    pages: list[tuple[float, float, list[tuple[tuple, str, bool]]]] = []
    for layout in extract_pages(path):
        height = float(layout.bbox[3])
        width = float(layout.bbox[2])
        raw_blocks: list[tuple[tuple, str, str]] = []  # (bbox, text, dominant_family)
        family_chars: dict[str, int] = {}
        for element in layout:
            if not isinstance(element, LTTextBox):
                continue
            text_parts: list[str] = []
            char_counts: dict[str, int] = {}
            for font, raw in _iter_lt_pieces(element):
                if not raw:
                    continue
                text_parts.append(_map_pdf_text_segment(raw, font or ""))
                if font:
                    family = _pdf_font_family(font)
                    char_counts[family] = char_counts.get(family, 0) + len(raw)
            text = "".join(text_parts)
            if not (text and text.strip()):
                continue
            bbox = tuple(float(v) for v in element.bbox)
            if not char_counts:
                continue
            dominant = max(char_counts.items(), key=lambda kv: kv[1])[0]
            # 数学字体的单字符块（如矩阵大括号的上下角 ⎡⎤）是正文内容，不能当竖排噪声删掉。
            if not _is_pdf_math_font(dominant) and _is_vertical_text_noise(text, bbox, width):
                continue
            for family, count in char_counts.items():
                family_chars[family] = family_chars.get(family, 0) + count
            raw_blocks.append((bbox, text, dominant))

        total_chars = sum(family_chars.values()) or 1
        # 本页「正文主字体族」：覆盖 >= 20% 字符的族（图内 ArialMT/TimesNewRoman 通常 < 15%）。
        main_families = {f for f, n in family_chars.items() if n >= total_chars * 0.20}
        blocks: list[tuple[tuple, str, bool]] = [
            (bbox, text, _is_figure_text_block(text, dominant, main_families))
            for bbox, text, dominant in raw_blocks
        ]
        pages.append((height, width, blocks))
    return pages


def _is_two_column_page(items: list[dict], page_width: float) -> bool:
    """判断该页是否「双栏并行」版面。

    判据：以页宽中点为界，左/右两侧各至少 2 个块，且两栏的 y 区间有实质重叠
    （重叠高度 >= 较短一栏的 25%）。单栏页面绝大多数块横跨中点，左右两侧块都少，
    不会误判。
    """
    if page_width <= 0 or len(items) < 4:
        return False
    mid = page_width / 2
    left = [it for it in items if it["bbox"][0] < mid]
    right = [it for it in items if it["bbox"][0] >= mid]
    if len(left) < 2 or len(right) < 2:
        return False
    ly0 = min(it["bbox"][1] for it in left)
    ly1 = max(it["bbox"][3] for it in left)
    ry0 = min(it["bbox"][1] for it in right)
    ry1 = max(it["bbox"][3] for it in right)
    overlap = min(ly1, ry1) - max(ly0, ry0)
    if overlap <= 0:
        return False
    shorter = min(ly1 - ly0, ry1 - ry0)
    return shorter > 0 and overlap >= 0.25 * shorter


def _order_page_items(items: list[dict], page_width: float) -> list[dict]:
    """排列一页的块：先把表格/占位按 y 插入正文序列，再按栏稳定分组。

    两步顺序不能反：
    1. 表格（含图区占位）必须插到「顶边低于表底边」的第一个块之前，否则 page 1 的
       Figure 1 占位会跑到图注后面；
    2. 双栏**只在栏一级做稳定分组**，栏内保持 pdfminer 原顺序——不按 y 重排。
       原因是公式/矩阵的多个文本块在 y 方向高度重叠，按 y 排序会把
       `xi =`、`⎡`、`xi(1, 1)` 打乱成毫无意义的顺序（本轮实测的回归）。
    """
    texts = [it for it in items if it["kind"] not in ("table", "figure_area")]
    anchors = sorted(
        (it for it in items if it["kind"] in ("table", "figure_area")),
        key=lambda it: -it["bbox"][3],
    )
    seq = list(texts)
    for anchor in anchors:
        insert_at = len(seq)
        for i, item in enumerate(seq):
            if item["bbox"][3] < anchor["bbox"][1]:
                insert_at = i
                break
        seq.insert(insert_at, anchor)

    if not _is_two_column_page(seq, page_width):
        return seq

    # 双栏：左栏读到底再读右栏。块的起始 x 决定归属；栏内保持原顺序（稳定分组），
    # 这样公式块、图内占位、表格的相对位置都不会被打乱。
    mid = page_width / 2
    left = [it for it in seq if it["bbox"][0] < mid]
    right = [it for it in seq if it["bbox"][0] >= mid]
    return left + right


def _join_page_items(items: list[dict]) -> str:
    """拼接一页内容：正文之间软换行，表格/占位前后留空行，连续占位合并。"""
    parts: list[str] = []
    prev_kind = ""
    for item in items:
        kind = item["kind"]
        if kind in ("figure", "figure_area"):
            if prev_kind in ("figure", "figure_area"):
                continue
            parts.extend(["", _FIGURE_TEXT_PLACEHOLDER, ""])
            prev_kind = "figure_area"
            continue
        if kind == "table":
            md = item.get("text") or ""
            if not md:
                prev_kind = kind
                continue
            parts.extend(["", md, ""])
        else:
            text = item.get("text") or ""
            if text.strip():
                parts.append(text)
        prev_kind = kind
    merged = "\n".join(parts)
    merged = re.sub(r"\n{3,}", "\n\n", merged)
    return merged.strip()


def _merge_pdf_page(
    text_blocks: list[tuple[tuple, str, bool]],
    tables: dict,
    page_height: float,
    page_width: float,
) -> str:
    """把某页的正文块、表格、图内占位按版面顺序合并成一页 Markdown。"""
    page_tables = tables or {}
    # 表格 bbox 来自 pdfplumber（左上原点），转为 pdfminer（左下原点）。
    pm_useful: list[tuple[tuple, str]] = [
        ((x0, page_height - bottom, x1, page_height - top), md)
        for (x0, top, x1, bottom), md in page_tables.get("useful", [])
    ]
    pm_garbage: list[tuple] = [
        (x0, page_height - bottom, x1, page_height - top)
        for (x0, top, x1, bottom) in page_tables.get("garbage", [])
    ]
    blocked = [bbox for bbox, _ in pm_useful] + pm_garbage

    items: list[dict] = []
    for bbox, text, is_figure in text_blocks:
        if _is_page_furniture(bbox, page_height, text):
            continue
        if any(_bbox_overlaps(bbox, blocked_bbox) for blocked_bbox in blocked):
            continue  # 表格区域内的文字由表格 / 占位承载，不再重复输出
        if is_figure:
            items.append({"kind": "figure", "bbox": bbox, "text": ""})
            continue
        cleaned = _dehyphenate(text.rstrip())
        if cleaned.strip():
            items.append({"kind": "text", "bbox": bbox, "text": cleaned})

    for bbox, md in pm_useful:
        if md:
            items.append({"kind": "table", "bbox": bbox, "text": md})
    for bbox in pm_garbage:
        items.append({"kind": "figure_area", "bbox": bbox, "text": ""})

    # 图内文字常被拆成很多小块，中间夹着 (a)/(b)/W 这类短标签；把它们一并归入占位，
    # 避免同一张图出现好几个占位（Figure 2 实测会被切成 2 段）。
    for i, item in enumerate(items):
        if item["kind"] != "text":
            continue
        if len((item.get("text") or "").strip()) > 3:
            continue
        prev_fig = i > 0 and items[i - 1]["kind"] in ("figure", "figure_area")
        next_fig = i + 1 < len(items) and items[i + 1]["kind"] in ("figure", "figure_area")
        if prev_fig or next_fig:
            item["kind"] = "figure"
            item["text"] = ""

    return _join_page_items(_order_page_items(items, page_width))


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
    for idx, (height, width, blocks) in enumerate(text_pages):
        page_md = _merge_pdf_page(blocks, tables_by_page.get(idx, {}), height, width)
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
            "其中 PDF 的表格会以 Markdown 表格保留；矢量图内无法可靠提取的文字会以占位标记（> [图内文字无法可靠提取…]）省略。"
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
        PDF 里矢量图内的文字（字体没有 Unicode 映射）无法可靠提取，会以
        `> [图内文字无法可靠提取，已省略…]` 占位标记代替，不会输出乱码。
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
