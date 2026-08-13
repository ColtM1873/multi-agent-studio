"""FastAPI 应用入口。"""

from __future__ import annotations

import asyncio
import sys

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

from app.api import agents, chat_ws, settings, threads

app = FastAPI(title="Multi-Agent Studio")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agents.router)
app.include_router(threads.router)
app.include_router(chat_ws.router)
app.include_router(settings.router)


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
