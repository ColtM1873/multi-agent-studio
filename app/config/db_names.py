"""multi-agent 名称、基础数据库名与连接串编码的唯一事实来源。

新版本里用户只填「名称 name」，另外两个身份字段由它直接派生：

- ``agent_id``：配置文件名 ``configs/<agent_id>.json`` 与接口路径的一部分；
- ``checkpoint_database``：会话历史所在的 PostgreSQL 数据库名。

因此名称必须同时满足 **Windows 文件名** 与 **PostgreSQL 数据库名** 的硬限制。
中文已验证可用（Python 文件名、FastAPI 路径参数、PostgreSQL 数据库名都支持）；
连接串里对数据库名做百分号编码（含空格时尤其必要）。
"""

from __future__ import annotations

import unicodedata
from urllib.parse import quote

# PostgreSQL 标识符上限（字节）。数据库名超过它会被截断或报错。
MAX_IDENTIFIER_BYTES = 63

# 系统库：不能作为 store 库名，也不能改名 / 删除。
SYSTEM_DATABASES = frozenset({"postgres", "template0", "template1"})

# 会破坏连接串结构（URI 分隔符）的字符：数据库名里一律不允许。
_FORBIDDEN_DB_CHARS = ("/", "?", "#")

# 这些名字已经被 configs/ 下的内部 JSON 占用，不能作为 multi-agent 名称
# （否则会覆盖 settings.json / drafts.json 等文件）。
RESERVED_CONFIG_STEMS = frozenset(
    {
        "default",
        "settings",
        "ui_prefs",
        "drafts",
        "common_prompts",
        "store_migrations",
        "identity_migration",
        "floating_stop_pos",
    }
)

# Windows 文件名非法字符。
_WINDOWS_FORBIDDEN = frozenset('\\/:*?"<>|')


class DatabaseError(Exception):
    """数据库名 / 名称不合法或数据库操作失败；message 直接面向用户展示。"""


def normalize_name(raw: str) -> str:
    """名称归一化：去首尾空白 + Unicode NFC（避免「看起来一样」的两个名字）。"""
    return unicodedata.normalize("NFC", str(raw or "").strip())


def validate_agent_name(raw: str) -> str:
    """校验并返回可同时作为 agent_id 与数据库名的名称。"""
    name = normalize_name(raw)
    if not name:
        raise DatabaseError("名称不能为空")
    if name in (".", ".."):
        raise DatabaseError("名称不能是「.」或「..」")
    if name.startswith("."):
        # 以点开头的文件名在 Windows 上是隐藏文件，配置列表会看不到它。
        raise DatabaseError("名称不能以点开头（会变成隐藏文件）")
    if name[-1] in (".", " "):
        raise DatabaseError("名称不能以点或空格结尾（Windows 文件名限制）")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        raise DatabaseError("名称不能包含换行、制表符等控制字符")

    bad = sorted({ch for ch in name if ch in _WINDOWS_FORBIDDEN})
    if bad:
        raise DatabaseError(
            "名称不能包含这些字符：" + " ".join(bad) + "（会同时用作文件名与数据库名）"
        )
    if name.lower() in SYSTEM_DATABASES:
        raise DatabaseError(f"「{name}」是 PostgreSQL 系统库名，不能作为 multi-agent 名称")
    if name.casefold() in RESERVED_CONFIG_STEMS:
        raise DatabaseError(f"「{name}」是程序内部使用的配置文件名，不能作为 multi-agent 名称")

    size = len(name.encode("utf-8"))
    if size > MAX_IDENTIFIER_BYTES:
        raise DatabaseError(
            f"名称过长：最多 {MAX_IDENTIFIER_BYTES} 字节（约 21 个汉字或 63 个英文字符），"
            f"当前 {size} 字节。因为名称会直接作为 checkpoint 数据库名。"
        )
    return name


def same_identity(a: str, b: str) -> bool:
    """名称是否相同（按 Windows 文件名的规则：不区分大小写）。"""
    return normalize_name(a).casefold() == normalize_name(b).casefold()


def validate_database_name(name: str, *, label: str = "数据库名称") -> str:
    """校验一个普通数据库名（checkpoint / 旧 store 库）。

    - 允许中文、空格等 PostgreSQL 支持的字符；
    - 拒绝会破坏连接串的 ``/ ? #``、控制字符、首尾空白、超过 63 字节的名字；
    - 不做 ASCII 限制（全局 store 库名有更严的 ASCII 白名单，见 pg_databases）。
    """
    n = str(name or "").strip()
    if not n:
        raise DatabaseError(f"{label}不能为空")
    if any(ch in n for ch in _FORBIDDEN_DB_CHARS):
        raise DatabaseError(f"{label}「{n}」包含不支持的字符（不能含 /、?、#）")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in n):
        raise DatabaseError(f"{label}「{n}」包含控制字符")
    if n != str(name):
        # 首尾空白会被 strip 掉：明确拒绝，避免"看起来同名、实际不同库"。
        raise DatabaseError(f"{label}「{n}」不能以空白开头或结尾")
    if len(n.encode("utf-8")) > MAX_IDENTIFIER_BYTES:
        raise DatabaseError(f"{label}「{n}」过长（PostgreSQL 上限 {MAX_IDENTIFIER_BYTES} 字节）")
    return n


def normalize_prefix(prefix: str) -> str:
    p = str(prefix or "").strip()
    if p and not p.endswith("/"):
        p += "/"
    return p


def quote_database(database: str) -> str:
    """把数据库名编码成可以安全拼进 URI 的一段。

    PostgreSQL URI 里的数据库名是一个路径段：空格、中文、``#`` 等必须百分号编码，
    否则 psycopg / libpq 会报 "unexpected spaces" 或把 ``#`` 当成 fragment。
    """
    return quote(str(database or ""), safe="")


def join_conn_string(prefix: str, database: str, suffix: str = "") -> str:
    """拼出一条完整连接串：前缀 + 编码后的数据库名 + 后缀。"""
    return normalize_prefix(prefix) + quote_database(database) + (suffix or "")
