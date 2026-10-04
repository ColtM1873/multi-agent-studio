"""本机界面偏好 REST API（替代浏览器 localStorage 的落盘版本）。

- GET  /api/ui-prefs        读取全部偏好
- GET  /api/ui-prefs.js     以 JS 形式返回（`window.__UI_PREFS = {...}`），供页面加载前同步注入
- PUT  /api/ui-prefs        增量写入 {updates:{key:value|null}} 或整体替换 {replace:true,data:{...}}
"""

from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel

from app.deps import ui_prefs_store

router = APIRouter(prefix="/api", tags=["ui-prefs"])


class UiPrefsBody(BaseModel):
    updates: dict[str, str | None] | None = None
    replace: bool = False
    data: dict[str, str] | None = None


@router.get("/ui-prefs")
def get_ui_prefs():
    return {"prefs": ui_prefs_store.all()}


@router.get("/ui-prefs.js")
def get_ui_prefs_js():
    payload = json.dumps(ui_prefs_store.all(), ensure_ascii=False)
    return Response(
        content=f"window.__UI_PREFS = {payload};",
        media_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )


@router.put("/ui-prefs")
def put_ui_prefs(body: UiPrefsBody):
    if body.replace and body.data is not None:
        ui_prefs_store.replace(body.data)
    elif body.updates:
        ui_prefs_store.apply(body.updates)
    return {"ok": True, "prefs": ui_prefs_store.all()}
