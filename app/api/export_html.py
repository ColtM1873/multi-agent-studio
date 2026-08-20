"""HTML 导出（md2print）REST API。

- GET  /api/export-html-config                    返回有效配置表（默认值合并用户设置）
- POST /api/agents/{agent_id}/threads/{thread_id}/export-html
      把一段 Markdown 正文转换为独立 HTML 文件，写入系统设置里的「HTML 输出路径」。
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config.settings import load_settings
from app.deps import config_store
from md2print import DEFAULT_CONFIG, _deep_merge, markdown_to_html

router = APIRouter(tags=["export-html"])


class ExportHtmlRequest(BaseModel):
    markdown: str


@router.get("/api/export-html-config")
def get_export_html_config():
    """返回 md2print 的有效配置表（默认值深合并用户已保存的覆盖项）与原始默认值。"""
    settings = load_settings(config_store._dir)
    return {
        "config": _deep_merge(DEFAULT_CONFIG, settings.export_html_config or {}),
        "defaults": DEFAULT_CONFIG,
    }


@router.post("/api/agents/{agent_id}/threads/{thread_id}/export-html")
def export_html(agent_id: str, thread_id: str, body: ExportHtmlRequest):
    settings = load_settings(config_store._dir)
    if not settings.export_html:
        raise HTTPException(status_code=400, detail="HTML 转换未开启，请先在系统设置里开启")
    if not settings.export_html_path:
        raise HTTPException(status_code=400, detail="请先在系统设置里填写 HTML 输出路径")

    markdown = (body.markdown or "").strip()
    if not markdown:
        raise HTTPException(status_code=400, detail="消息正文为空，无法转换")

    out_dir = Path(settings.export_html_path)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"无法创建输出目录：{e}")

    safe_tid = re.sub(r"[^\w\-]+", "_", thread_id).strip("_") or "thread"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    out_path = out_dir / f"{safe_tid}_{ts}.html"

    html_str = markdown_to_html(markdown, title=thread_id, config=settings.export_html_config or {})
    out_path.write_text(html_str, encoding="utf-8")
    return {"path": str(out_path)}
