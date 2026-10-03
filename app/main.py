"""FastAPI 应用入口。"""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager

# huggingface_hub 在 import 时读取这些环境变量一次；先设默认国内镜像并禁用 Xet，
# 避免 huggingface_hub 被 import 时锁定为官方站 / 走被墙的 Xet CAS（per-config 镜像在 persistence 里动态覆盖）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

# psycopg 异步 / langgraph 在 Windows 上需要 SelectorEventLoop
if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import (
    agents,
    browser,
    chat_ws,
    common_prompts,
    downloads,
    drafts,
    export_html,
    memories,
    settings,
    snapshots,
    threads,
)
from app.deps import common_prompt_store, draft_store

# 可选的浏览器工具计时 / 卡死看门狗探针（仅当设置 BROWSER_TOOL_TIMING=1 时安装，
# 未设置时零开销、不改变任何行为）。见 browser_agent/diagnostics.py。
try:
    from browser_agent import diagnostics as _browser_diagnostics

    _browser_diagnostics.install()
except Exception:
    pass

# BID065: 浏览器工具 debug 开关下的细粒度时间戳探针（只在「浏览器工具debug」打开时写
# browser_tools_timing.log）。发行版删除：连同所有行末 # BID065 的行一起移除。
try:
    from browser_agent import bid065_trace as _bid065_trace
    from app.runtime.browser_tools_debug_log import _debug_enabled as _bid065_debug_enabled

    _bid065_trace.set_provider(_bid065_debug_enabled)  # BID065
except Exception:
    pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    # 退出托盘程序时兜底把未发送消息缓存 / 会话常用 prompt 再落盘一次
    for store in (draft_store, common_prompt_store):
        try:
            store.flush()
        except Exception:
            pass


app = FastAPI(title="Multi-Agent Studio", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def no_cache_frontend(request, call_next):
    """前端静态资源不缓存，避免更新后浏览器仍显示旧界面。"""
    response = await call_next(request)
    if request.url.path.startswith("/static") or request.url.path == "/":
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response

app.include_router(agents.router)
app.include_router(threads.router)
app.include_router(memories.router)
app.include_router(snapshots.router)
app.include_router(chat_ws.router)
app.include_router(settings.router)
app.include_router(downloads.router)
app.include_router(export_html.router)
app.include_router(drafts.router)
app.include_router(common_prompts.router)
app.include_router(browser.router)


@app.get("/api/health")
def health():
    return {"ok": True}


STATIC_DIR = __import__("pathlib").Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {"app": "Multi-Agent Studio", "hint": "前端尚未构建（Phase 3）"}
