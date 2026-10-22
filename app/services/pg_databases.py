"""PostgreSQL 数据库自动建库 / 改名 / 删除。

需求背景：用户不再需要自己去 pgAdmin 建库。checkpoint 数据库名由 multi-agent
的「名称」自动派生（见 app/config/db_names.py），程序在保存时用同一套凭据自动
创建；长期记忆的 store 数据库也统一由程序创建（默认 Long_Term_Memory）。

安全边界（见 inner_docs/ID46）：
- 本模块只做「建库 / 改名 / 删库」，绝不删除表或数据行；
- 删库只用于「旧 store 库已迁移完成」这一条路径，且系统库（postgres/template0/
  template1）永不删除；
- 所有数据库名都通过 psycopg.sql.Identifier 引用，不拼接用户输入。
"""

from __future__ import annotations

import re

from psycopg import AsyncConnection, errors, sql

from app.config.db_names import (
    SYSTEM_DATABASES,
    DatabaseError,
    join_conn_string,
    normalize_prefix,
    validate_database_name,
)

# 全局 store 数据库名：只允许常见安全字符，避免连接串拼接/URL 解析出问题。
_STORE_DB_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,62}$")


def validate_store_database_name(name: str) -> str:
    n = (name or "").strip()
    if not n:
        raise DatabaseError("store 数据库名称不能为空")
    if n.lower() in SYSTEM_DATABASES:
        raise DatabaseError(
            f"「{n}」是 PostgreSQL 系统库，不能作为 store 数据库。请使用其他名称（默认 Long_Term_Memory）。"
        )
    if not _STORE_DB_RE.fullmatch(n):
        raise DatabaseError(
            "store 数据库名称只能包含字母、数字、下划线或连字符，"
            "且必须以字母、数字或下划线开头（最多 63 个字符）"
        )
    return n


# checkpoint / 旧 store 库名的校验统一走 app.config.db_names（允许中文与空格，
# 但拒绝连接串分隔符、控制字符与超过 63 字节的名字）。
_validate_database_name = validate_database_name


def masked_prefix(prefix: str) -> str:
    """脱敏连接前缀里的密码（仅展示用）。"""
    p = prefix or ""
    return re.sub(r"(://[^:/@]+:)[^@]*(@)", r"\1***\2", p, count=1)


def _humanize_connect_error(exc: Exception | None) -> str:
    if isinstance(exc, errors.InsufficientPrivilege):
        return (
            "当前数据库账号权限不足。请改用拥有建库权限的账号"
            "（通常是安装 PostgreSQL 时设置的 postgres 用户）。"
        )
    return "连不上 PostgreSQL 服务器，请检查连接前缀、端口、账号密码与 SSL 设置是否正确。"


async def _maintenance_connection(prefix: str, suffix: str = "") -> AsyncConnection:
    """连到维护库（postgres，失败再试 template1），用于 CREATE/ALTER/DROP DATABASE。"""
    last_exc: Exception | None = None
    for maint in ("postgres", "template1"):
        try:
            return await AsyncConnection.connect(
                join_conn_string(prefix, maint, suffix),
                connect_timeout=8,
                autocommit=True,
            )
        except Exception as exc:  # noqa: BLE001 - 统一翻译成用户可读错误
            last_exc = exc
    raise DatabaseError(_humanize_connect_error(last_exc)) from last_exc


async def _database_exists(conn: AsyncConnection, database: str) -> bool:
    cur = await conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,))
    return (await cur.fetchone()) is not None


async def _create_database_on(conn: AsyncConnection, database: str) -> bool:
    if await _database_exists(conn, database):
        return False
    try:
        await conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        return True
    except errors.DuplicateDatabase:
        return False
    except errors.InsufficientPrivilege as exc:
        raise DatabaseError(
            f"当前数据库账号没有创建数据库「{database}」的权限。"
            "请改用拥有建库权限的账号（通常是安装 PostgreSQL 时设置的 postgres 用户）。"
        ) from exc


async def ensure_database(prefix: str, database: str, suffix: str = "") -> bool:
    """确保数据库存在；返回是否本次新建。失败抛 DatabaseError。

    先直接连目标库：已存在时（正常用户最常见的情况）完全不碰维护库，
    因此即使用户没有 postgres/template1 的访问权限也能正常工作。
    """
    name = _validate_database_name(database)
    try:
        conn = await AsyncConnection.connect(
            join_conn_string(prefix, name, suffix), connect_timeout=8, autocommit=True
        )
        await conn.close()
        return False
    except Exception:  # noqa: BLE001 - 目标库可能不存在，转入维护库检查/创建
        pass

    conn = await _maintenance_connection(prefix, suffix)
    try:
        return await _create_database_on(conn, name)
    finally:
        await conn.close()


async def ensure_databases(prefix: str, databases, suffix: str = "") -> list[str]:
    """确保一组数据库都存在；返回本次新建的库名。"""
    created: list[str] = []
    seen: set[str] = set()
    for db in databases:
        name = (db or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        if await ensure_database(prefix, name, suffix):
            created.append(name)
    return created


async def rename_database(prefix: str, suffix: str, old: str, new: str) -> str:
    """把旧库直接改名为新名字（不会新建再迁移）。

    返回值：renamed / missing（旧库不存在） / exists（旧库不存在但新库已存在） /
    unchanged（同名）。
    """
    old_name = _validate_database_name(old, label="旧数据库名称")
    # 这里用通用数据库名校验（checkpoint 库名允许中文/空格）；全局 store 库名的
    # 更严 ASCII 白名单由 API 层 validate_store_database_name 负责。
    new_name = _validate_database_name(new, label="新数据库名称")
    if old_name == new_name:
        return "unchanged"
    if old_name in SYSTEM_DATABASES:
        raise DatabaseError(
            f"「{old_name}」是 PostgreSQL 系统库，不能改名。请改用普通数据库名。"
        )

    conn = await _maintenance_connection(prefix, suffix)
    try:
        old_exists = await _database_exists(conn, old_name)
        new_exists = await _database_exists(conn, new_name)
        if not old_exists:
            return "exists" if new_exists else "missing"
        if new_exists:
            raise DatabaseError(
                f"数据库「{new_name}」已经存在，无法把「{old_name}」改名为它。"
                "请换一个名称，或先手动处理已存在的同名数据库。"
            )
        try:
            # ALTER DATABASE ... RENAME 要求没有其它连接；先踢掉旧库上的连接。
            await conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (old_name,),
            )
            await conn.execute(
                sql.SQL("ALTER DATABASE {} RENAME TO {}").format(
                    sql.Identifier(old_name), sql.Identifier(new_name)
                )
            )
        except errors.InsufficientPrivilege as exc:
            raise DatabaseError(
                f"当前数据库账号没有把「{old_name}」改名为「{new_name}」的权限。"
            ) from exc
        return "renamed"
    finally:
        await conn.close()


async def drop_database(prefix: str, suffix: str, database: str) -> str:
    """删除一个旧 store 库；系统库永不删除。返回 dropped / system / missing。"""
    name = _validate_database_name(database)
    if name in SYSTEM_DATABASES:
        return "system"

    conn = await _maintenance_connection(prefix, suffix)
    try:
        if not await _database_exists(conn, name):
            return "missing"
        try:
            await conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (name,),
            )
            await conn.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
        except errors.InsufficientPrivilege as exc:
            raise DatabaseError(f"当前数据库账号没有删除数据库「{name}」的权限。") from exc
        return "dropped"
    finally:
        await conn.close()
