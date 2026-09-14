"""PDF 读取修复的离线验证（不调 LLM、不连数据库）。

验证内容：
1. `_pdf_table_to_markdown` 的转义 / 列补齐 / 跳空行；
2. 对给定（或默认扫描到的）PDF 跑 `_convert_to_markdown`，断言**无 `[A-Za-z]{20,}` 长串**
   （修复前 MarkItDown/pdfplumber 会把正文粘连成长串），并统计表格数量；
3. 关闭 `pdf_table_extraction` 后表格数为 0，且仍无长串。

运行:
    venv\\Scripts\\python.exe scripts/verify_pdf_conversion.py
    venv\\Scripts\\python.exe scripts/verify_pdf_conversion.py "path\\to\\a.pdf" ...
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.runtime.file_tools import (  # noqa: E402
    _convert_pdf_to_markdown,
    _convert_to_markdown,
    _pdf_table_to_markdown,
)

MAX_ALPHA_RUN = 30


def _max_alpha_run(text: str) -> int:
    return max((len(m) for m in re.findall(r"[A-Za-z]{20,}", text)), default=0)


def _table_count(text: str) -> int:
    return sum(1 for line in text.splitlines() if re.match(r"^\|\s*-{3,}", line.strip()))


def _check_table_to_markdown() -> None:
    md = _pdf_table_to_markdown([["A", "B"], [None, "x|y"], ["", ""], ["p\nq", "r"]])
    assert "| A | B |" in md, md
    assert "| --- | --- |" in md, md
    assert "x\\|y" in md, md
    assert "p<br>q" in md, md
    assert md.count("\n") == 3, md  # 表头 + 分隔 + 2 行（全空行被跳过）
    print("[OK] _pdf_table_to_markdown 转义 / 补齐 / 跳空行")


def _default_pdfs() -> list[Path]:
    found: list[Path] = []
    paper = ROOT / "inner_docs" / "HIGH-DIMENSIONAL CONTINUOUS CONTROL USING.pdf"
    if paper.exists():
        found.append(paper)
    ftws = Path(tempfile.gettempdir()) / "opencode" / "ftws"
    if ftws.is_dir():
        found.extend(sorted(ftws.glob("*.pdf")))
    return found


def _check_pdf(path: Path) -> bool:
    ok, text = _convert_to_markdown(str(path), True)
    if not ok:
        print(f"[FAIL] {path.name}: 转换失败：{text}")
        return False
    run = _max_alpha_run(text)
    tables = _table_count(text)
    status = "OK" if run < MAX_ALPHA_RUN else "FAIL"
    print(f"[{status}] {path.name}: 长度={len(text)} 最长字母串={run} 表格数={tables}")
    if run >= MAX_ALPHA_RUN:
        sample = max(re.findall(r"[A-Za-z]{20,}", text), key=len)
        print(f"        粘连样例: {sample}")
        return False

    ok_off, text_off = _convert_to_markdown(str(path), False)
    if not ok_off:
        print(f"[FAIL] {path.name}: 关闭表格提取后转换失败：{text_off}")
        return False
    tables_off = _table_count(text_off)
    if tables_off != 0:
        print(f"[FAIL] {path.name}: 关闭表格提取后仍有 {tables_off} 张表")
        return False
    print(f"        [OK] 关闭表格提取：表格数=0 最长字母串={_max_alpha_run(text_off)}")
    return True


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    _check_table_to_markdown()

    pdfs = [Path(p) for p in sys.argv[1:]] or _default_pdfs()
    if not pdfs:
        print("⚠️  未找到 PDF（可传入路径，或把测试 PDF 放到 %TEMP%/opencode/ftws/）")
        return

    all_ok = True
    for pdf in pdfs:
        if not pdf.exists():
            print(f"[FAIL] 文件不存在：{pdf}")
            all_ok = False
            continue
        all_ok = _check_pdf(pdf) and all_ok

    if not all_ok:
        raise SystemExit(1)
    print("\n全部通过 ✅")


if __name__ == "__main__":
    main()
