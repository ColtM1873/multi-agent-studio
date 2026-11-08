"""把「本地任意 PDF 文件」转换为 Markdown（复用文件工具的 PDF→Markdown 转换）。

会话页「将本地md转换为HTML」左侧的「将本地PDF转换为md」按钮走这里：

1. 后端在本机弹出 Windows 资源管理器风格的**文件选择窗口**，由用户自己浏览并选定
   一个 .pdf 文件（见 `app/services/win_file_dialog.py`）；
2. 复用文件工具里的 PDF 转换（`app/runtime/file_tools.convert_file_to_markdown`）：
   pdfminer 抽正文 + pdfplumber 基于框线抽表格，受进阶设置里的「📄 PDF 表格提取」
   （`settings.pdf_table_extraction`）控制；失败时回退 MarkItDown；
3. 写回**该 PDF 文件所在目录**：优先 `<同名>.md`；若同名文件已存在，则追加时间戳
   另存（绝不覆盖用户已有文件）。

要点与「将本地md转换为HTML」一致：

- 端点用**同步 `def`**（不是 `async def`）：文件选择窗口会一直阻塞到用户选定/取消，
  放事件循环里会把整个后端卡住；FastAPI 会把同步端点丢进线程池。
- 同一时刻只允许一个文件选择窗口（与 local_md 共用 `win_file_dialog.DIALOG_LOCK`）。
- 输出目录就是 PDF 所在目录，因此不需要设置里的「md 输出路径」。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config.settings import load_settings
from app.deps import config_store
from app.runtime import file_tools
from app.services import win_file_dialog

router = APIRouter(tags=["local-pdf"])

# 与「将本地md转换为HTML」共用同一把锁（对话框共用同一套 Win32 实现）。
_dialog_lock = win_file_dialog.DIALOG_LOCK

_DEFAULT_DIALOG_TITLE = "选择一个 PDF 文件"


class ConvertLocalPdfRequest(BaseModel):
    # 对话框标题由前端按当前语言传入（后端默认中文）。
    title: str = ""


def _target_md_path(pdf_path: Path) -> Path:
    """输出路径：同目录同名 .md；已存在则追加时间戳另存，绝不覆盖。"""
    candidate = pdf_path.with_suffix(".md")
    if not candidate.exists():
        return candidate
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = pdf_path.with_name(f"{pdf_path.stem}_{stamp}.md")
    index = 2
    while candidate.exists():
        candidate = pdf_path.with_name(f"{pdf_path.stem}_{stamp}_{index}.md")
        index += 1
    return candidate


@router.post("/api/convert-local-pdf")
def convert_local_pdf(body: ConvertLocalPdfRequest | None = None):
    settings = load_settings(config_store._dir)
    if not settings.show_local_pdf_convert_button:
        raise HTTPException(
            status_code=400,
            detail="「将本地PDF转换为md」功能未开启，请先在系统设置里开启",
        )
    if not win_file_dialog.supported():
        raise HTTPException(
            status_code=400,
            detail="当前系统不是 Windows，无法弹出系统文件选择窗口",
        )

    if not _dialog_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="已经有一个文件选择窗口处于打开状态，请先选择或取消它",
        )
    try:
        selected = win_file_dialog.open_file_dialog(
            title=(body.title if body else "") or _DEFAULT_DIALOG_TITLE,
            owner=win_file_dialog.owner_hwnd(),
            filters=("PDF 文件 (*.pdf)\0*.pdf\0所有文件 (*.*)\0*.*\0\0"),
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"无法打开系统文件选择窗口：{e}")
    finally:
        _dialog_lock.release()

    if not selected:
        return {"canceled": True, "path": "", "source": ""}

    pdf_path = Path(selected)
    if not pdf_path.is_absolute():
        pdf_path = pdf_path.resolve()
    if not pdf_path.is_file():
        raise HTTPException(status_code=400, detail="所选文件不存在或不是文件")
    if pdf_path.suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="请选择 PDF 文件（.pdf）")

    try:
        ok, text_or_err = file_tools.convert_file_to_markdown(
            str(pdf_path), settings.pdf_table_extraction
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"转换为 Markdown 失败：{e}")
    if not ok:
        raise HTTPException(status_code=500, detail=f"转换为 Markdown 失败：{text_or_err}")
    markdown = text_or_err
    if not markdown.strip():
        raise HTTPException(status_code=400, detail="该 PDF 未解析出任何内容，无法转换")

    out_path = _target_md_path(pdf_path)
    try:
        out_path.write_text(markdown, encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=400,
            detail=f"无法在 PDF 文件所在目录写入 md（目录可能只读或没有权限）：{e}",
        )

    return {"canceled": False, "path": str(out_path), "source": str(pdf_path)}
