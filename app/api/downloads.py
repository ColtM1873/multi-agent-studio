"""下载状态 API（前端轮询 + 托盘读取）。"""

from __future__ import annotations

from fastapi import APIRouter

from app.services.downloads import download_manager

router = APIRouter(prefix="/api", tags=["downloads"])


@router.get("/download-status")
def download_status():
    return {"current": download_manager.current()}
