"""离线验证「本地 PDF → md」端点（不弹真实文件选择窗口、不连数据库）。

做法：
- 用一个手写的最小单页 PDF 当样本，真实跑文件工具的 PDF → Markdown 转换；
- monkeypatch `win_file_dialog.open_file_dialog` 直接返回该 PDF 路径；
- 检查产出命名、不覆盖规则、取消分支、开关关闭、非 PDF 输入、锁占用等分支；
- 用 spy 包住 `file_tools.convert_file_to_markdown`，确认「PDF 表格提取」设置被透传。

运行：
    venv\\Scripts\\python.exe scripts\\verify_local_pdf_convert.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.api import local_pdf  # noqa: E402
from app.api.local_pdf import convert_local_pdf  # noqa: E402
from app.config.settings import Settings  # noqa: E402

WORK = ROOT / "tmp_local_pdf_test"
if WORK.exists():
    shutil.rmtree(WORK)
WORK.mkdir(parents=True)


def make_pdf(path: Path, text: str = "Hello PDF sample") -> None:
    """写一个 pdfminer / pdfplumber 都能解析的最小单页 PDF（无第三方依赖）。"""
    content = f"BT /F1 14 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    buf = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(buf))
        buf += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_pos = len(buf)
    buf += f"xref\n0 {len(objects) + 1}\n".encode()
    buf += b"0000000000 65535 f \n"
    for off in offsets:
        buf += f"{off:010d} 00000 n \n".encode()
    buf += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    ).encode()
    path.write_bytes(bytes(buf))


def fake_settings(**overrides) -> Settings:
    s = Settings()
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


PDF = WORK / "样例PDF.pdf"
make_pdf(PDF, "Hello PDF sample")

_orig_load = local_pdf.load_settings
_orig_dialog = local_pdf.win_file_dialog.open_file_dialog
_orig_convert = local_pdf.file_tools.convert_file_to_markdown
local_pdf.load_settings = lambda _dir: fake_settings()

try:
    # --- 1. 正常转换：同目录同名 .md，内容来自文件工具的 PDF 转换 ---
    calls: list[bool] = []
    real_convert = _orig_convert

    def spy_convert(path, flag=True):
        calls.append(bool(flag))
        return real_convert(path, flag)

    local_pdf.file_tools.convert_file_to_markdown = spy_convert
    local_pdf.win_file_dialog.open_file_dialog = lambda **kw: str(PDF)
    r = convert_local_pdf(None)
    assert r["canceled"] is False, r
    out = Path(r["path"])
    assert out.parent.resolve() == PDF.parent.resolve(), out
    assert out.name == "样例PDF.md", out.name
    text = out.read_text(encoding="utf-8")
    assert "Hello PDF" in text, text[:200]
    assert calls and calls[-1] is True, calls
    print("[1] 正常转换 OK ->", out.name, len(text), "chars")

    # --- 2. 已存在同名 md：追加时间戳另存，绝不覆盖 ---
    original = out.read_text(encoding="utf-8")
    local_pdf.win_file_dialog.open_file_dialog = lambda **kw: str(PDF)
    r2 = convert_local_pdf(None)
    out2 = Path(r2["path"])
    assert out2 != out and out2.exists(), (out, out2)
    assert out.read_text(encoding="utf-8") == original, "原有 md 被覆盖了"
    assert out2.name.startswith("样例PDF_") and out2.name.endswith(".md"), out2.name
    print("[2] 不覆盖 + 时间戳另存 OK ->", out2.name)

    # --- 3. 用户取消：canceled=True，不产出文件 ---
    local_pdf.win_file_dialog.open_file_dialog = lambda **kw: None
    before = set(WORK.iterdir())
    r3 = convert_local_pdf(None)
    assert r3["canceled"] is True and r3["path"] == "", r3
    assert set(WORK.iterdir()) == before
    print("[3] 取消分支 OK")

    # --- 4. 开关关闭 -> 400 ---
    local_pdf.load_settings = lambda _dir: fake_settings(show_local_pdf_convert_button=False)
    try:
        convert_local_pdf(None)
        raise AssertionError("未开启开关时不应成功")
    except Exception as e:  # noqa: BLE001 — HTTPException
        detail = str(getattr(e, "detail", e))
        assert "未开启" in detail, detail
    print("[4] 关闭开关 -> 400 OK")

    # --- 5. 「PDF 表格提取」设置必须透传给文件工具 ---
    local_pdf.load_settings = lambda _dir: fake_settings(pdf_table_extraction=False)
    calls.clear()
    local_pdf.win_file_dialog.open_file_dialog = lambda **kw: str(PDF)
    convert_local_pdf(None)
    assert calls and calls[-1] is False, calls
    print("[5] PDF 表格提取设置透传 OK")
    local_pdf.load_settings = lambda _dir: fake_settings()

    # --- 6. 非 PDF 文件 -> 400 ---
    other = WORK / "not_a_pdf.txt"
    other.write_text("x", encoding="utf-8")
    local_pdf.win_file_dialog.open_file_dialog = lambda **kw: str(other)
    try:
        convert_local_pdf(None)
        raise AssertionError("非 PDF 不应成功")
    except Exception as e:  # noqa: BLE001
        assert "PDF" in str(getattr(e, "detail", e)), e
    print("[6] 非 PDF -> 400 OK")

    # --- 7. 转换失败 -> 500 ---
    local_pdf.file_tools.convert_file_to_markdown = lambda path, flag=True: (False, "坏文件")
    local_pdf.win_file_dialog.open_file_dialog = lambda **kw: str(PDF)
    try:
        convert_local_pdf(None)
        raise AssertionError("转换失败不应成功")
    except Exception as e:  # noqa: BLE001
        assert "坏文件" in str(getattr(e, "detail", e)), e
    local_pdf.file_tools.convert_file_to_markdown = spy_convert
    print("[7] 转换失败 -> 500 OK")

    # --- 8. 对话框已被占用 -> 409 ---
    assert local_pdf._dialog_lock.acquire(blocking=False)
    try:
        try:
            convert_local_pdf(None)
            raise AssertionError("锁被占用时不应成功")
        except Exception as e:  # noqa: BLE001
            assert "文件选择窗口" in str(getattr(e, "detail", e)), e
    finally:
        local_pdf._dialog_lock.release()
    print("[8] 并发弹窗 -> 409 OK")

    # --- 9. 命名规则单元测试 ---
    plain = WORK / "b.pdf"
    plain.write_bytes(b"x")
    assert local_pdf._target_md_path(plain).name == "b.md"
    (WORK / "b.md").write_text("old", encoding="utf-8")
    t = local_pdf._target_md_path(plain)
    assert t.name.startswith("b_") and t.name.endswith(".md"), t.name
    print("[9] 命名规则 OK ->", t.name)

    # --- 10. Settings 向后兼容（老 settings.json 没有新字段时默认开启） ---
    old = Settings.model_validate_json('{"export_html": false}')
    assert old.show_local_pdf_convert_button is True
    assert "show_local_pdf_convert_button" in old.model_dump()
    print("[10] Settings 兼容 OK")
finally:
    local_pdf.load_settings = _orig_load
    local_pdf.win_file_dialog.open_file_dialog = _orig_dialog
    local_pdf.file_tools.convert_file_to_markdown = _orig_convert
    shutil.rmtree(WORK, ignore_errors=True)

print("ALL OK")
