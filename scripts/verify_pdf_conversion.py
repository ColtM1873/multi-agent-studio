"""PDF 读取修复的离线验证（不调 LLM、不连数据库）。

验证内容：
1. `_pdf_table_to_markdown` 的转义 / 列补齐 / 跳空行；
2. 对给定（或默认扫描到的）PDF 跑 `_convert_to_markdown`，断言**无 `[A-Za-z]{20,}` 长串**
   （修复前 MarkItDown/pdfplumber 会把正文粘连成长串），并统计表格数量；
3. 关闭 `pdf_table_extraction` 后表格数为 0，且仍无长串；
4. `read_file` 读取 PDF 时会在相同目录生成同名 `.md` 副本、返回 `<md_copy>`/`<note>` 提示、
   二次读取不覆盖已有副本，且 `edit_file` 传原 `.pdf` 能重定向改这个 `.md` 的几行。

运行:
    venv\\Scripts\\python.exe scripts/verify_pdf_conversion.py
    venv\\Scripts\\python.exe scripts/verify_pdf_conversion.py "path\\to\\a.pdf" ...
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.runtime.file_tools import (  # noqa: E402
    _convert_pdf_to_markdown,
    _convert_to_markdown,
    _dehyphenate,
    _escape_md_cell,
    _is_page_furniture,
    _is_two_column_page,
    _is_useful_pdf_table,
    _looks_like_garbage_table,
    _map_pdf_text_segment,
    _order_page_items,
    _pdf_table_to_markdown,
    _readable_ratio,
    build_file_tools,
)

MAX_ALPHA_RUN = 30
PUA_RE = re.compile(r"[\ue000-\uf8ff]")


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


def _check_quality_helpers() -> None:
    """P0/P1/P2 的新判据与映射（纯函数，离线）。"""
    # 1. 可读率：图内乱码 vs 正文
    assert _readable_ratio("!\"#$%&'()*+*+,") < 0.1
    assert _readable_ratio("The quick brown fox 123") > 0.9
    assert _readable_ratio("") == 0.0

    # 2. 表格过滤：正常表格通过；乱码图注被拒并判为 garbage
    good_rows = [["Method", "Accuracy"], ["EEGNet", "90.1%"], ["DeepCNN", "88.7%"]]
    bad_rows = [["!\"#$%&'", "()*+,--\"$#"], [".,%/0),'", "1$#\"$,,)\"$#"]]
    assert _is_useful_pdf_table(good_rows, 612 * 792, (100, 100, 400, 300)) is True
    assert _is_useful_pdf_table(bad_rows, 612 * 792, (100, 100, 400, 300)) is False
    assert _looks_like_garbage_table(bad_rows, (100, 100, 400, 300), 612 * 792) is True
    assert _looks_like_garbage_table(good_rows, (100, 100, 400, 300), 612 * 792) is False

    # 3. 数学字体映射（来自 PDF 的 /Differences）
    assert _map_pdf_text_segment("X", "JTOLEZ+txex") == "\u2211"      # summationdisplay
    assert _map_pdf_text_segment(",", "CEYAOO+txsyc") == "\u2260"     # nequal
    assert _map_pdf_text_segment("\uf8ee", "JTOLEZ+txex") == "\u23a1"  # bracketlefttp
    assert _map_pdf_text_segment("\uf8fa", "JTOLEZ+txex") == "\u23a5"  # bracketrightex
    assert _map_pdf_text_segment("(cid:2)", "JTOLEZ+txex") == "["
    assert _map_pdf_text_segment("(cid:3)", "JTOLEZ+txex") == "]"
    assert _map_pdf_text_segment("X", "QKFYCU+NimbusRomNo9L-Regu") == "X"  # 正文字体不动

    # 4. 连字符合并：正常软连字符合并，跨块的散词不合并
    assert _dehyphenate("Evasion at-\ntacks have") == "Evasion attacks have"
    assert _dehyphenate("ad-\nversarial filter") == "adversarial filter"
    assert _dehyphenate("set-\nat-") == "set-\nat-"

    # 5. 页脚/页码判定（792 高页面）
    assert _is_page_furniture((72, 87.7, 167.6, 95.7), 792, "Preprint submitted to Elsevier") is True
    assert _is_page_furniture((303.1, 87.2, 308.1, 97.2), 792, "2") is True
    assert _is_page_furniture((72, 159, 115, 169), 792, "were used:") is False

    # 6. 表格单元格 Markdown 转义（$ 是前端 KaTeX 报错的直接触发点）
    escaped = _escape_md_cell("a$b_c*d`e[f]~g#h & <x> | y")
    assert "\\$" in escaped and "\\_" in escaped and "\\*" in escaped, escaped
    assert "&amp;" in escaped and "&lt;x&gt;" in escaped, escaped

    # 7. 版面：单栏不误判成双栏；双栏能识别；单栏表格按 y 插入
    single = [
        {"kind": "text", "bbox": (72, 600, 540, 650), "text": "a"},
        {"kind": "text", "bbox": (72, 500, 540, 590), "text": "b"},
        {"kind": "text", "bbox": (72, 400, 540, 490), "text": "c"},
        {"kind": "text", "bbox": (72, 300, 540, 390), "text": "d"},
    ]
    assert _is_two_column_page(single, 612) is False
    two = [
        {"kind": "text", "bbox": (72, 600, 300, 650), "text": "a"},
        {"kind": "text", "bbox": (72, 400, 300, 450), "text": "b"},
        {"kind": "text", "bbox": (320, 600, 540, 650), "text": "c"},
        {"kind": "text", "bbox": (320, 400, 540, 450), "text": "d"},
    ]
    assert _is_two_column_page(two, 612) is True
    table_item = {"kind": "table", "bbox": (72, 495, 540, 520), "text": "| a |"}
    ordered_single = _order_page_items(single + [table_item], 612)
    assert [it["kind"] for it in ordered_single] == ["text", "text", "table", "text", "text"], [
        it["kind"] for it in ordered_single
    ]
    # 双栏：稳定分组（左栏读到底再读右栏），栏内顺序不变
    ordered_two = _order_page_items(two, 612)
    assert [it["text"] for it in ordered_two] == ["a", "b", "c", "d"], ordered_two
    print("[OK] 可读率 / 表格过滤 / 数学字体映射 / 连字符合并 / 页脚 / 单元格转义 / 双栏判定")



def _default_pdfs() -> list[Path]:
    found: list[Path] = []
    paper = ROOT / "inner_docs" / "HIGH-DIMENSIONAL CONTINUOUS CONTROL USING.pdf"
    if paper.exists():
        found.append(paper)
    ftws = Path(tempfile.gettempdir()) / "opencode" / "ftws"
    if ftws.is_dir():
        found.extend(sorted(ftws.glob("*.pdf")))
    return found


def _check_md_copy(pdf: Path) -> bool:
    """读取 PDF 应生成同名 .md 副本，且 edit_file 传原 .pdf 能改这个副本。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        shutil.copy2(pdf, root / pdf.name)
        tools = {t.name: t for t in build_file_tools(str(root))}

        out = tools["read_file"].invoke({"file_path": pdf.name})
        md_path = root / (pdf.stem + ".md")
        if "<md_copy>" not in out or "<note>" not in out:
            print(f"[FAIL] read_file 返回缺少 <md_copy>/<note> 提示")
            return False
        if not md_path.exists() or not md_path.read_text(encoding="utf-8").strip():
            print(f"[FAIL] 读取未生成非空同名 .md 副本：{md_path.name}")
            return False

        # 二次读取不得覆盖已存在的副本（保留模型/用户的编辑）。
        md_path.write_text("EDITED\n", encoding="utf-8")
        out2 = tools["read_file"].invoke({"file_path": pdf.name})
        if md_path.read_text(encoding="utf-8") != "EDITED\n":
            print("[FAIL] 二次读取覆盖了已存在的 .md 副本")
            return False
        if "already exists" not in out2:
            print("[FAIL] 二次读取未提示副本已存在")
            return False

        # edit_file 传原 .pdf：应重定向到同名 .md 并改掉那几行。
        res = tools["edit_file"].invoke(
            {"file_path": pdf.name, "old_string": "EDITED", "new_string": "EDITED-LINE"}
        )
        if "successfully" not in res.lower():
            print(f"[FAIL] edit_file(.pdf) 重定向编辑失败：{res}")
            return False
        if md_path.read_text(encoding="utf-8") != "EDITED-LINE\n":
            print("[FAIL] edit_file(.pdf) 未正确写入 .md 副本")
            return False

        print("[OK] 读取生成同名 .md 副本 / 不覆盖已有副本 / edit_file(.pdf) 重定向改 .md")
    return True


def _check_pdf(path: Path) -> bool:
    ok, text = _convert_to_markdown(str(path), True)
    if not ok:
        print(f"[FAIL] {path.name}: 转换失败：{text}")
        return False
    run = _max_alpha_run(text)
    tables = _table_count(text)
    pua = len(PUA_RE.findall(text))
    cids = text.count("(cid:")
    status = "OK" if run < MAX_ALPHA_RUN and pua == 0 and cids == 0 else "FAIL"
    print(f"[{status}] {path.name}: 长度={len(text)} 最长字母串={run} 表格数={tables} PUA={pua} (cid={cids}")
    if run >= MAX_ALPHA_RUN:
        sample = max(re.findall(r"[A-Za-z]{20,}", text), key=len)
        print(f"        粘连样例: {sample}")
        return False
    if pua or cids:
        print(f"        未映射字符：PUA={pua} (cid)={cids}（数学字体映射未生效？）")
        return False

    if not _check_sample_quality(path, text):
        return False

    ok_off, text_off = _convert_to_markdown(str(path), False)
    if not ok_off:
        print(f"[FAIL] {path.name}: 关闭表格提取后转换失败：{text_off}")
        return False
    tables_off = _table_count(text_off)
    if tables_off != 0:
        print(f"[FAIL] {path.name}: 关闭表格提取后仍有 {tables_off} 张表")
        return False
    if PUA_RE.search(text_off):
        print(f"[FAIL] {path.name}: 关闭表格提取后仍有私有区字符")
        return False
    print(f"        [OK] 关闭表格提取：表格数=0 最长字母串={_max_alpha_run(text_off)} 无 PUA")
    return True


def _check_sample_quality(pdf: Path, text: str) -> bool:
    """针对本轮样张（arXiv 2412.07231v1）的专项断言。"""
    if not pdf.name.startswith("2412.07231"):
        return True
    checks = [
        ("页脚已剔除", "Preprint submitted to Elsevier" not in text),
        ("无裸 $（表格单元格已转义）", text.count("$") == 0),
        ("∑ 已还原（txex summationdisplay）", text.count("\u2211") > 0),
        ("≠ 已还原（txsyc nequal）", text.count("\u2260") > 0),
        ("矩阵括号已还原", "\u23a1" in text and "\u23a3" in text),
        ("图内文字已用占位替代", "图内文字无法可靠提取" in text),
        ("Figure 1 不再输出假表格", "| ---" not in text),
        ("Figure 2 乱码不再出现在正文", "!\"#$%&'()*+*+," not in text),
        ("双栏顺序修复（4.1 在 4.2 之前）",
         text.find("4.1. Datasets") != -1
         and text.find("4.2. Deep Learning Models") != -1
         and text.find("4.1. Datasets") < text.find("4.2. Deep Learning Models")),
        ("软连字符合并", "Evasion attacks have also been explored" in text),
    ]
    ok = True
    for label, passed in checks:
        if not passed:
            print(f"[FAIL] 样张断言：{label}")
            ok = False
    if ok:
        print("        [OK] 样张专项：页脚/∑/≠/括号/占位/表格/双栏/连字符")
    return ok



def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    _check_table_to_markdown()
    _check_quality_helpers()

    pdfs = [Path(p) for p in sys.argv[1:]] or _default_pdfs()
    if not pdfs:
        print("⚠️  未找到 PDF（可传入路径，或把测试 PDF 放到 %TEMP%/opencode/ftws/）")
        return

    all_ok = True
    if pdfs[0].exists():
        all_ok = _check_md_copy(pdfs[0]) and all_ok
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
