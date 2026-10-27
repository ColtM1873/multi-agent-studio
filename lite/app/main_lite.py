"""Lite 版 FastAPI 应用入口。"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lite.app import bootstrap

bootstrap.install_persistence_stub()

from lite.app import deps_lite

bootstrap.bind_deps(deps_lite)

if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from psycopg import OperationalError as PgOperationalError
from psycopg import errors as pg_errors

from app.api import (
    agents,
    browser,
    chat_ws,
    common_prompts,
    drafts,
    export_html,
    sessions,
    settings,
    snapshots,
    threads,
    ui_prefs,
)
from app.config import storage_backend as storage
from app.config.settings import (
    seed_global_embedding,
    seed_global_postgres,
    seed_global_sub_summary,
    seed_global_summary,
)
from lite.app import sqlite_store
from lite.app.deps_lite import BASE_DIR, common_prompt_store, config_store, draft_store

logger = logging.getLogger("app.lite")

CONFIG_DIR = BASE_DIR / "configs"
# 安装时决定（configs/lite/storage.json）；缺省 postgres，保持旧行为。
SQLITE_MODE = storage.is_sqlite(CONFIG_DIR)

NORMAL_STATIC_DIR = BASE_DIR / "app" / "static"
LITE_STATIC_DIR = Path(__file__).resolve().parents[1] / "static"

# SQLite 模式下快照数据源换成 SQLite（graph_builder / session_hub 通过模块属性调用）。
if SQLITE_MODE:
    from app.services import snapshot as snapshot_service

    snapshot_service.capture_snapshot = sqlite_store.capture_snapshot


def _autostart_pg_services() -> None:
    try:
        from app.services import pg_service

        pg_service.ensure_pg_services_at_startup(BASE_DIR)
    except Exception as exc:  # noqa: BLE001
        try:
            logger.warning("自动启动 PostgreSQL 服务失败：%s", exc)
        except Exception:
            pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        configs = config_store.list()
        seed_global_postgres(config_store._dir, configs)
        seed_global_embedding(config_store._dir, configs)
        seed_global_summary(config_store._dir, configs)
        seed_global_sub_summary(config_store._dir, configs)
    except Exception:  # noqa: BLE001
        pass

    if not SQLITE_MODE:
        # SQLite 模式不依赖 PostgreSQL：不拉起本机 PG 服务。
        threading.Thread(
            target=_autostart_pg_services, name="pg-autostart", daemon=True
        ).start()
    yield
    if SQLITE_MODE:
        await sqlite_store.close_all()
    for store in (draft_store, common_prompt_store):
        try:
            store.flush()
        except Exception:
            pass


app = FastAPI(title="Multi-Agent Studio Lite", lifespan=lifespan)


def _pg_startup_in_progress() -> bool:
    try:
        from app.services import pg_service

        return pg_service.startup_in_progress()
    except Exception:
        return False


def _pg_problem(
    status_code: int, code: str, detail: str, *, retryable: bool = False
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail, "code": code, "retryable": bool(retryable)},
    )


@app.exception_handler(PgOperationalError)
async def _handle_pg_operational_error(request: Request, exc: PgOperationalError):
    logger.error("PostgreSQL 连接失败：%s", exc)
    starting = _pg_startup_in_progress()
    detail = (
        "程序暂时连不上数据库，所以还打不开会话。\n\n"
        "最常见的原因是 PostgreSQL 数据库服务没有启动，可以这样处理：\n"
        "1. 按键盘上的 Win + R，输入 services.msc 后回车；\n"
        "2. 在列表里找到名字里带 PostgreSQL 的服务，右键选择「启动」；\n"
        "3. 回到本页面，点下面的「重试」。\n\n"
        "如果那个服务本来就显示「正在运行」，请检查创建这个 multi-agent 时填写的"
        "数据库地址、端口、用户名、密码是否正确。"
    )
    return _pg_problem(503, "db_unavailable", detail, retryable=starting)


@app.exception_handler(pg_errors.InvalidCatalogName)
async def _handle_pg_missing_database(
    request: Request, exc: pg_errors.InvalidCatalogName
):
    logger.error("PostgreSQL 目标数据库不存在：%s", exc)
    detail = (
        "程序在 PostgreSQL 里找不到这个 multi-agent 绑定的数据库。\n\n"
        "程序会在保存 multi-agent 时按连接串自动建库，正常情况下不需要你手动创建。请检查：\n"
        "1. 数据库服务是否已启动（程序通常会自动拉起，稍等片刻后点「重试」）；\n"
        "2. 创建 / 编辑这个 multi-agent 时填写的「checkpoint 数据库名」是否正确；\n"
        "3. 连接账号是否有建库权限（通常使用安装 PostgreSQL 时设置的 postgres 用户）。\n\n"
        "回到主界面重新保存一次该 multi-agent（会自动补建数据库），再点下面的「重试」。"
    )
    return _pg_problem(503, "db_missing_database", detail)


@app.exception_handler(pg_errors.InsufficientPrivilege)
async def _handle_pg_insufficient_privilege(
    request: Request, exc: pg_errors.InsufficientPrivilege
):
    logger.error("PostgreSQL 权限不足：%s", exc)
    detail = (
        "当前数据库用户权限不够（例如没有创建数据表的权限）。\n\n"
        "请改用拥有该数据库权限的用户——通常就是安装 PostgreSQL 时设置的 postgres 超级用户。\n"
        "在 multi-agent 配置里更新连接的用户名 / 密码并保存后，回到本页面点「重试」。"
    )
    return _pg_problem(503, "db_permission", detail)


@app.exception_handler(pg_errors.UndefinedTable)
async def _handle_pg_undefined_table(request: Request, exc: pg_errors.UndefinedTable):
    logger.error("PostgreSQL 数据表未初始化：%s", exc)
    detail = (
        "数据库里还没有程序需要的数据表。\n\n"
        "这类表会在你第一次和这个 multi-agent 对话时自动创建。\n"
        "请先进入该会话发一条消息；如果仍然报错，请确认配置里的数据库是否正确。"
    )
    return _pg_problem(503, "db_not_initialized", detail)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def no_cache_frontend(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static") or request.url.path == "/":
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


app.include_router(agents.router)
app.include_router(threads.router)
app.include_router(snapshots.router)
app.include_router(snapshots.deleted_router)
app.include_router(sessions.router)
app.include_router(chat_ws.router)
app.include_router(settings.router)
app.include_router(export_html.router)
app.include_router(drafts.router)
app.include_router(common_prompts.router)
app.include_router(ui_prefs.router)
app.include_router(browser.router)


@app.get("/api/download-status")
def download_status():
    return {"current": None}


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/focus")
def focus(request: Request):
    shown = False
    try:
        import desktop_app

        shown = desktop_app.show_window()
    except Exception:
        shown = False
    if not shown:
        try:
            import webbrowser

            webbrowser.open(str(request.base_url))
        except Exception:
            pass
    return {"ok": shown, "app": "Multi-Agent Studio Lite"}


if NORMAL_STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(NORMAL_STATIC_DIR)), name="static")
if LITE_STATIC_DIR.exists():
    app.mount(
        "/lite/static",
        StaticFiles(directory=str(LITE_STATIC_DIR)),
        name="lite-static",
    )


@app.get("/")
def index():
    index_path = LITE_STATIC_DIR / "index.html"
    if not index_path.exists():
        return {"app": "Multi-Agent Studio Lite", "hint": "Lite 前端文件缺失"}
    html = index_path.read_text(encoding="utf-8")
    # 同步告诉前端当前存储后端（首屏即生效，避免先显示 PostgreSQL 表单再隐藏）。
    marker = (
        '<script>window.__LITE_STORAGE_BACKEND = "sqlite";</script>\n'
        if SQLITE_MODE
        else ""
    )
    return HTMLResponse(html.replace("<!--LITE_STORAGE-->", marker))
