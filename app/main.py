"""FastAPI 应用入口。"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
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

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from psycopg import OperationalError as PgOperationalError
from psycopg import errors as pg_errors

from app.api import (
    agents,
    browser,
    chat_ws,
    common_prompts,
    downloads,
    drafts,
    export_html,
    local_md,
    memories,
    settings,
    snapshots,
    store_migrations,
    threads,
    ui_prefs,
)
from app.config.settings import (
    load_settings,
    seed_global_embedding,
    seed_global_postgres,
    seed_global_sub_summary,
    seed_global_summary,
)
from app.deps import BASE_DIR, common_prompt_store, config_store, draft_store, ui_prefs_store
from app.services import store_migrations as store_migration_service
from app.services import identity_migration

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


logger = logging.getLogger("app")


def _reload_identity_stores() -> None:
    """身份迁移会改写 drafts / common_prompts / ui_prefs 里的 agent_id 键；
    这些 store 在 import 时已经把旧键读进内存，必须重新加载一次。"""
    for store in (draft_store, common_prompt_store, ui_prefs_store):
        try:
            store.reload()
        except Exception:  # noqa: BLE001
            logger.exception("重新加载本机数据失败（%s）", type(store).__name__)


def _autostart_pg_services() -> None:
    """后台拉起配置所指向的本机 PostgreSQL 服务（见 app/services/pg_service.py）。"""
    try:
        from app.services import pg_service

        pg_service.ensure_pg_services_at_startup(BASE_DIR)
    except Exception as exc:  # noqa: BLE001
        try:
            logger.warning("自动启动 PostgreSQL 服务失败：%s", exc)
        except Exception:
            pass
    # 服务起来后再补一次身份统一（三字段同步）。启动时的第一次尝试可能因服务
    # 尚未就绪而跳过；失败项会记录在 configs/identity_migration.json.pending。
    try:
        result = identity_migration.run_for_all_sync(config_store)
        if result.get("renamed"):
            logger.info("[identity] 后台补做改名：%s 项", len(result["renamed"]))
            _reload_identity_stores()
    except Exception:  # noqa: BLE001
        try:
            logger.exception("身份统一（agent_id/checkpoint 数据库按名称改名）失败")
        except Exception:
            pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 统一 store 数据库：检测旧版「每个 agent 一个 store 库」的配置，记录迁移源，
    # 并把 agent 配置切到全局 store 库（默认 Long_Term_Memory）。旧库数据保留待迁移。
    try:
        settings = load_settings(config_store._dir)
        store_migration_service.detect_and_record(config_store, settings)
    except Exception:  # noqa: BLE001
        pass
    # 统一身份三件套：agent_id / checkpoint 数据库都改成与名称相同。
    # 不弹窗、不询问；连不上数据库的 agent 会记录到 configs/identity_migration.json，
    # 由后台线程在 PostgreSQL 服务拉起后再补一次（见 _autostart_pg_services）。
    try:
        await identity_migration.run_for_all(config_store)
    except Exception:  # noqa: BLE001
        logger.exception("身份统一（agent_id/checkpoint 数据库按名称改名）失败")
    finally:
        _reload_identity_stores()
    # 首次启动：若有已存在的 multi-agent，把第一个带连接前缀的配置提取为全局连接设置，
    # 供各 agent 配置界面的「使用全局连接」开关使用（见 app/config/settings.py）。
    try:
        agents = config_store.list()
        seed_global_postgres(config_store._dir, agents)
        seed_global_embedding(config_store._dir, agents)
        seed_global_summary(config_store._dir, agents)
        seed_global_sub_summary(config_store._dir, agents)
    except Exception:  # noqa: BLE001
        pass
    # 部分用户的 PostgreSQL 服务没随开机启动，进入会话界面会连不上库。这里在后台
    # 线程里把「配置所指向的本机 PostgreSQL 服务」拉起（静默失败再弹一次 UAC），
    # 不阻塞服务启动、也不影响 /api/health 就绪。
    threading.Thread(
        target=_autostart_pg_services, name="pg-autostart", daemon=True
    ).start()
    yield
    # 退出托盘程序时兜底把未发送消息缓存 / 会话常用 prompt 再落盘一次
    for store in (draft_store, common_prompt_store):
        try:
            store.flush()
        except Exception:
            pass


app = FastAPI(title="Multi-Agent Studio", lifespan=lifespan)


def _pg_startup_in_progress() -> bool:
    """本机 PostgreSQL 服务是否正在（或刚被）拉起——用于把「仍在加载」与「确实失败」区分开。"""
    try:
        from app.services import pg_service

        return pg_service.startup_in_progress()
    except Exception:
        return False


def _pg_problem(status_code: int, code: str, detail: str, *, retryable: bool = False) -> JSONResponse:
    """统一的「数据库问题」响应：带机器可读的 code / retryable，供前端决定「等待」还是「报错」。"""
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail, "code": code, "retryable": bool(retryable)},
    )


@app.exception_handler(PgOperationalError)
async def _handle_pg_operational_error(request: Request, exc: PgOperationalError):
    """数据库连不上（服务未启动 / 连接串错误 / 认证失败等）→ 503 + 人话，而非裸 500。

    以前这类异常直接冒泡成 Starlette 的纯文本 "Internal Server Error"，
    前端拿不到 detail，只能显示无信息量的报错。若服务正在被程序自动拉起
    （低配机器可能需要一两分钟），会标记 retryable，让前端显示「正在加载，请稍候」
    并自动重试，而不是把报错甩给用户。
    """
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
async def _handle_pg_missing_database(request: Request, exc: pg_errors.InvalidCatalogName):
    """目标数据库不存在 → 503 + 人话。"""
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
    """数据库用户权限不足（如无权建表）→ 503 + 人话。"""
    logger.error("PostgreSQL 权限不足：%s", exc)
    detail = (
        "当前数据库用户权限不够（例如没有创建数据表的权限）。\n\n"
        "请改用拥有该数据库权限的用户——通常就是安装 PostgreSQL 时设置的 postgres 超级用户。\n"
        "在 multi-agent 配置里更新连接的用户名 / 密码并保存后，回到本页面点「重试」。"
    )
    return _pg_problem(503, "db_permission", detail)


@app.exception_handler(pg_errors.UndefinedTable)
async def _handle_pg_undefined_table(request: Request, exc: pg_errors.UndefinedTable):
    """数据表尚未初始化（数据库结构由程序首次使用某功能时惰性创建）→ 503 + 人话。"""
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
    """前端静态资源不缓存，避免更新后浏览器仍显示旧界面。"""
    response = await call_next(request)
    if request.url.path.startswith("/static") or request.url.path == "/":
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response

app.include_router(agents.router)
app.include_router(threads.router)
app.include_router(memories.router)
app.include_router(snapshots.router)
app.include_router(snapshots.deleted_router)
app.include_router(store_migrations.router)
app.include_router(chat_ws.router)
app.include_router(settings.router)
app.include_router(downloads.router)
app.include_router(export_html.router)
app.include_router(local_md.router)
app.include_router(drafts.router)
app.include_router(common_prompts.router)
app.include_router(ui_prefs.router)
app.include_router(browser.router)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/focus")
def focus(request: Request):
    """把已运行的桌面窗口唤到前台（供重复双击 exe 的第二实例调用）。"""
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
    return {"ok": shown, "app": "Multi-Agent Studio"}


STATIC_DIR = __import__("pathlib").Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {"app": "Multi-Agent Studio", "hint": "前端尚未构建（Phase 3）"}
