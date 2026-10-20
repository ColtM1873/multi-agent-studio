"""把「本地任意 Markdown 文件」转换为可打印 HTML（复用 md2print 与系统设置）。

会话页「编辑会话常用prompt」左侧的「将本地md转换为HTML」按钮走这里：

1. 后端在本机弹出 Windows 资源管理器风格的**文件选择窗口**，由用户自己浏览并选定
   一个 .md 文件（见 `app/services/win_file_dialog.py`）；
2. 读取该 md 文件原文（UTF-8 / 带 BOM / GB18030 兜底）；
3. 用 `md2print` + 系统设置里的「HTML 转换配置表」（`settings.export_html_config`，
   与「转换 HTML」按钮共用同一份配置，默认值深合并）转成可打印 HTML；
4. 写回**该 md 文件所在目录**：优先 `<同名>.html`；若同名文件已存在，则追加时间戳
   另存（绝不覆盖用户已有文件）。

要点：

- 端点用**同步 `def`**（不是 `async def`）：文件选择窗口会一直阻塞到用户选定/取消，
  放事件循环里会把整个后端卡住；FastAPI 会把同步端点丢进线程池。
- 同一时刻只允许一个文件选择窗口（`_dialog_lock`）：用户连点、或开多个页面时，
  后来的请求直接返回可读提示，而不是叠一堆模态窗口。
- 输出目录就是 md 所在目录，因此**不需要**设置里的「HTML 输出路径」。
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config.settings import load_settings
from app.deps import config_store
from app.services import win_file_dialog
from md2print import markdown_to_html

router = APIRouter(tags=["local-md"])

# 文件选择窗口同一时刻只弹一个：阻塞式对话框若被并发调用会叠出多个模态窗口。
_dialog_lock = threading.Lock()

_DEFAULT_DIALOG_TITLE = "选择一个 Markdown 文件"


class ConvertLocalMdRequest(BaseModel):
    # 对话框标题由前端按当前语言传入（后端默认中文）。
    title: str = ""


def _owner_hwnd() -> int:
    """尽量拿到桌面版（pywebview / WebView2）主窗口句柄，让对话框成为它的模态子窗口。"""
    try:
        import ctypes

        import desktop_app

        window = getattr(desktop_app, "_window", None)
        native = getattr(window, "native", None)
        if native is not None:
            hwnd = int(native.Handle.ToInt64())
            # 窗口可能已销毁/隐藏到托盘；只有仍有效的句柄才交给通用对话框
            if hwnd and ctypes.windll.user32.IsWindow(hwnd):
                return hwnd
    except Exception:  # noqa: BLE001 - 浏览器标签页模式 / 窗口尚未创建
        pass
    return 0


def _read_markdown_text(path: Path) -> str:
    """读 md 原文：先按 UTF-8（可带 BOM），失败再用 GB18030 兜底。"""
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _target_html_path(md_path: Path) -> Path:
    """输出路径：同目录同名 .html；已存在则追加时间戳另存，绝不覆盖。"""
    candidate = md_path.with_suffix(".html")
    if not candidate.exists():
        return candidate
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = md_path.with_name(f"{md_path.stem}_{stamp}.html")
    index = 2
    while candidate.exists():
        candidate = md_path.with_name(f"{md_path.stem}_{stamp}_{index}.html")
        index += 1
    return candidate


@router.post("/api/convert-local-md")
def convert_local_md(body: ConvertLocalMdRequest | None = None):
    settings = load_settings(config_store._dir)
    if not settings.show_local_md_convert_button:
        raise HTTPException(
            status_code=400,
            detail="「将本地md转换为HTML」功能未开启，请先在系统设置里开启",
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
            owner_hwnd=_owner_hwnd(),
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"无法打开系统文件选择窗口：{e}")
    finally:
        _dialog_lock.release()

    if not selected:
        return {"canceled": True, "path": "", "source": ""}

    md_path = Path(selected)
    if not md_path.is_absolute():
        md_path = md_path.resolve()
    if not md_path.is_file():
        raise HTTPException(status_code=400, detail="所选文件不存在或不是文件")

    try:
        markdown = _read_markdown_text(md_path)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"无法读取该文件：{e}")
    if not markdown.strip():
        raise HTTPException(status_code=400, detail="该文件内容为空，无法转换")

    out_path = _target_html_path(md_path)
    try:
        html_str = markdown_to_html(
            markdown,
            title=md_path.stem,
            config=settings.export_html_config or {},
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"转换为 HTML 失败：{e}")
    try:
        out_path.write_text(html_str, encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=400,
            detail=f"无法在 md 文件所在目录写入 HTML（目录可能只读或没有权限）：{e}",
        )

    return {"canceled": False, "path": str(out_path), "source": str(md_path)}
