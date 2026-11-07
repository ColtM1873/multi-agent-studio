"""会话导出归档（zip / 加密 / 清单）——与存储后端、Lite 版完全无关。

一个导出文件里放三样东西：

- ``manifest.json``：格式版本、导出时间、来源 multi-agent 身份（名称 / 子 agent 及其
  状态通道键）、每个会话的摘要与文件位置；
- ``agent_config.json``：完整的 multi-agent 配置（**含模型 API key**，因此可加密）；
- ``sessions/<会话>/{session.json,conversation.md}``：会话的 checkpoint 数据（可再导入）
  与人类可读的 Markdown 副本。

文件有两种形态，导入时按**文件头**自动识别：

1. 未加密：普通 zip（以 ``PK`` 开头），扩展名 ``.zip``；
2. 加密：``Magic + 4 字节头长度 + JSON 头 + AES-256-GCM 密文``，扩展名 ``.zip.enc``。
   密文正是上面那个 zip 的字节，解密后与未加密版完全一致。

加密细节（全部写进文件头，导入端不需要任何本机约定，跨机器/跨版本都能解开）：

- KDF：优先 ``scrypt(n=2^16, r=8, p=1)``；当前 OpenSSL 不支持时回退
  ``PBKDF2-HMAC-SHA256(600000)``，选哪个写在头里；
- 密码：用户原始密码；**少于 10 个字符时自动在前面补固定长前缀**
  （需求：短密码加前缀增加密钥长度）；
- 加密：AES-256-GCM，12 字节随机 nonce，**文件头作为 AAD**——改头即解不开；
- 因此改密码 / 换机器 / 换 Lite 版都不会导致旧文件打不开，也不需要「先解密再加密」。

注意：本地单机应用的导出文件天然可能被离线暴力破解（用户已知），所以这里的目标是
**真正的认证加密 + 足够慢的 KDF**，而不是「不可破解」。
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import struct
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

try:  # 加密库：普通版与 Lite 版都会随 markitdown / google-auth 装上，仍显式兜底
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except Exception:  # noqa: BLE001
    AESGCM = None  # type: ignore[assignment]
    InvalidTag = Exception  # type: ignore[assignment,misc]

FORMAT_NAME = "multi-agent-studio-session-export"
FORMAT_VERSION = 1

# 加密文件头魔数：固定 10 字节，读到它即认为「这是加密的导出文件」
MAGIC = b"MASESSION\x01"
CIPHER_NAME = "aes-256-gcm"

# 短密码前缀（导出/导入两端都必须用同一个，因此写在格式层而不是设置层）
PASSWORD_PREFIX = "Multi-Agent-Studio-Session-Password"
SHORT_PASSWORD_LIMIT = 10

SCRYPT_N = 2**16
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MAXMEM = 256 * 1024 * 1024
PBKDF2_ITERATIONS = 600_000
KDF_SCRYPT = "scrypt"
KDF_PBKDF2 = "pbkdf2-sha256"

# 解压后的总体积上限：防止损坏/恶意 zip 把内存吃光（正常会话远小于此）。
MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024

_ZIP_MAGIC = b"PK"


class SessionArchiveError(Exception):
    """导出/导入过程中的可读错误（message 直接展示给用户）。"""


def _probe_scrypt() -> bool:
    try:
        hashlib.scrypt(
            b"probe", salt=b"0123456789abcdef", n=2**10, r=8, p=1, dklen=32,
            maxmem=16 * 1024 * 1024,
        )
        return True
    except Exception:  # noqa: BLE001 — 部分 OpenSSL 构建禁用了 scrypt
        return False


_SCRYPT_OK = _probe_scrypt()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(str(text or ""))


def effective_password(raw: str) -> bytes:
    """把用户输入的原始密码变成真正用于 KDF 的字节串。

    少于 10 个字符时自动补固定长前缀（导出与导入走同一个函数，因此不需要用户知道
    「到底加没加前缀」——两端输入同样的原始密码即可）。
    """
    text = "" if raw is None else str(raw)
    if len(text) < SHORT_PASSWORD_LIMIT:
        text = PASSWORD_PREFIX + text
    return text.encode("utf-8")


def _derive_key(password: bytes, salt: bytes, header: dict) -> bytes:
    kdf = str(header.get("kdf") or KDF_SCRYPT)
    if kdf == KDF_SCRYPT:
        try:
            return hashlib.scrypt(
                password,
                salt=salt,
                n=int(header.get("n") or SCRYPT_N),
                r=int(header.get("r") or SCRYPT_R),
                p=int(header.get("p") or SCRYPT_P),
                dklen=32,
                maxmem=SCRYPT_MAXMEM,
            )
        except Exception as exc:  # noqa: BLE001
            raise SessionArchiveError(
                f"当前系统无法用 scrypt 派生密钥（{exc}）。请换一台机器或换用未加密导出。"
            )
    if kdf == KDF_PBKDF2:
        return hashlib.pbkdf2_hmac(
            "sha256",
            password,
            salt,
            int(header.get("iterations") or PBKDF2_ITERATIONS),
            dklen=32,
        )
    raise SessionArchiveError(f"不认识的密钥派生方式：{kdf}（导出文件可能来自更新的版本）")


# ── zip 组装 / 读取 ─────────────────────────────────────────────────


def _unique_dir(thread_id: str, used: set[str]) -> str:
    safe = re.sub(r"[^\w\-]+", "_", str(thread_id or "")).strip("_") or "thread"
    safe = safe[:60]
    candidate = safe
    index = 2
    while candidate.casefold() in used:
        candidate = f"{safe}_{index}"
        index += 1
    used.add(candidate.casefold())
    return candidate


def agent_identity(config: dict) -> dict:
    """从配置里挑出「会话能不能迁入其它配置」所需的最小身份信息。"""
    subs: list[dict] = []
    for sub in config.get("sub_agents") or []:
        if not isinstance(sub, dict):
            continue
        subs.append(
            {
                "name": str(sub.get("name") or ""),
                "state_messages_key": sub.get("state_messages_key"),
                "history_token_measure_key": sub.get("history_token_measure_key"),
                "extracted_summary_ai_msg_key": sub.get("extracted_summary_ai_msg_key"),
            }
        )
    return {
        "agent_id": str(config.get("agent_id") or ""),
        "name": str(config.get("name") or ""),
        "sub_agents": subs,
    }


def sub_agent_compat(exported: list[dict], local: list[dict]) -> tuple[bool, str]:
    """判断「导出的会话」能否迁入某个本机配置。

    规则（宁严勿松——迁错配置会让会话看起来「历史全空」）：

    1. 导出侧用到的子 agent，本机配置里必须**同名存在**；
    2. 同名子 agent 的**状态通道键**（`state_messages_key` 等）必须一致；本机配置里
       这些键为空（没绑定过历史）时视为可兼容，导入后按导出的键锁定。

    本机有更多子 agent 不影响：多出来的子 agent 在自己会话里从空历史开始。
    """
    local_map = {str(s.get("name") or ""): s for s in (local or []) if isinstance(s, dict)}
    missing = [
        str(s.get("name") or "")
        for s in (exported or [])
        if str(s.get("name") or "") not in local_map
    ]
    if missing:
        return False, (
            "导出会话用到了本机配置里没有的子 agent："
            + "、".join(missing)
            + "（本机已有配置的子 agent 集合可以多、不能少）"
        )

    key_fields = (
        "state_messages_key",
        "history_token_measure_key",
        "extracted_summary_ai_msg_key",
    )
    for item in exported or []:
        name = str(item.get("name") or "")
        target = local_map.get(name) or {}
        for field in key_fields:
            want = str(item.get(field) or "")
            have = str(target.get(field) or "")
            if want and have and want != have:
                return False, (
                    f"子 agent「{name}」的状态通道键与本机不一致（导出为 {want}，本机为 {have}）"
                )
    return True, ""


def build_zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buffer.getvalue()


def build_archive(
    *,
    agent_config: dict,
    manifest: dict,
    sessions: list[dict],
) -> bytes:
    """组装导出 zip。

    ``sessions`` 每项：``{"thread_id", "name", "message_count", "last_updated",
    "payload": dict, "conversation": str}``。
    """
    out_manifest: dict[str, Any] = dict(manifest or {})
    out_manifest["format"] = FORMAT_NAME
    out_manifest["format_version"] = FORMAT_VERSION
    out_manifest.setdefault("created_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    out_manifest["agent"] = agent_identity(agent_config)

    files: dict[str, bytes] = {}
    entries: list[dict] = []
    used_dirs: set[str] = set()
    for session in sessions or []:
        dirname = _unique_dir(str(session.get("thread_id") or ""), used_dirs)
        base = f"sessions/{dirname}"
        payload_file = f"{base}/session.json"
        conversation_file = f"{base}/conversation.md"
        files[payload_file] = json.dumps(
            session.get("payload") or {}, ensure_ascii=False
        ).encode("utf-8")
        files[conversation_file] = str(session.get("conversation") or "").encode("utf-8")
        entries.append(
            {
                "thread_id": str(session.get("thread_id") or ""),
                "name": str(session.get("name") or session.get("thread_id") or ""),
                "message_count": int(session.get("message_count") or 0),
                "last_updated": str(session.get("last_updated") or ""),
                "payload_file": payload_file,
                "conversation_file": conversation_file,
            }
        )
    out_manifest["sessions"] = entries

    files["manifest.json"] = json.dumps(
        out_manifest, ensure_ascii=False, indent=2
    ).encode("utf-8")
    files["agent_config.json"] = json.dumps(
        agent_config or {}, ensure_ascii=False, indent=2
    ).encode("utf-8")
    return build_zip(files)


def _safe_member(name: str) -> bool:
    if not name or name.startswith(("/", "\\")):
        return False
    parts = re.split(r"[\\/]", name)
    return ".." not in parts


def read_archive(zip_bytes: bytes) -> dict:
    """读回一个导出 zip，返回 ``{"manifest", "agent_config", "sessions"}``。

    ``sessions`` 每项在清单条目上补 ``payload`` 与 ``conversation``。
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except Exception as exc:  # noqa: BLE001
        raise SessionArchiveError(f"无法读取会话文件（不是有效的 zip）：{exc}")

    with zf:
        total = sum(info.file_size for info in zf.infolist())
        if total > MAX_UNCOMPRESSED_BYTES:
            raise SessionArchiveError("会话文件解压后体积过大，已拒绝读取")
        names = set(zf.namelist())
        for name in names:
            if not _safe_member(name):
                raise SessionArchiveError(f"会话文件里含有非法路径：{name}")
        if "manifest.json" not in names:
            raise SessionArchiveError("这不是本程序导出的会话文件（缺少清单 manifest.json）")

        try:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise SessionArchiveError(f"会话清单损坏：{exc}")
        if not isinstance(manifest, dict):
            raise SessionArchiveError("会话清单格式不正确")
        if str(manifest.get("format") or "") != FORMAT_NAME:
            raise SessionArchiveError("这不是本程序导出的会话文件（格式标识不匹配）")
        version = int(manifest.get("format_version") or 0)
        if version > FORMAT_VERSION:
            raise SessionArchiveError(
                f"会话文件格式版本（{version}）比当前程序更新，请先升级程序再导入"
            )

        try:
            agent_config = json.loads(zf.read("agent_config.json").decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise SessionArchiveError(f"会话文件里的 multi-agent 配置损坏：{exc}")
        if not isinstance(agent_config, dict):
            raise SessionArchiveError("会话文件里的 multi-agent 配置格式不正确")

        sessions: list[dict] = []
        for entry in manifest.get("sessions") or []:
            if not isinstance(entry, dict):
                continue
            payload_file = str(entry.get("payload_file") or "")
            conversation_file = str(entry.get("conversation_file") or "")
            if payload_file not in names:
                raise SessionArchiveError(
                    f"会话「{entry.get('thread_id') or '?'}」的数据缺失（{payload_file}）"
                )
            try:
                payload = json.loads(zf.read(payload_file).decode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                raise SessionArchiveError(
                    f"会话「{entry.get('thread_id') or '?'}」的数据损坏：{exc}"
                )
            conversation = ""
            if conversation_file in names:
                try:
                    conversation = zf.read(conversation_file).decode("utf-8")
                except Exception:  # noqa: BLE001
                    conversation = ""
            item = dict(entry)
            item["payload"] = payload if isinstance(payload, dict) else {}
            item["conversation"] = conversation
            sessions.append(item)

    return {"manifest": manifest, "agent_config": agent_config, "sessions": sessions}


# ── 加密 / 解密 ─────────────────────────────────────────────────────


def _require_aesgcm() -> None:
    if AESGCM is None:
        raise SessionArchiveError(
            "当前环境缺少加密组件（cryptography），无法加密/解密会话文件。"
            "请重新运行安装脚本以补齐依赖。"
        )


def is_encrypted_bytes(blob: bytes) -> bool:
    return bool(blob) and blob.startswith(MAGIC)


def look_encrypted(path: str | Path) -> bool:
    """只读文件头判断是否加密（不解密、不把整份文件读进内存）。"""
    try:
        with open(path, "rb") as fp:
            return fp.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def encrypt_archive(zip_bytes: bytes, password: str) -> bytes:
    """把 zip 字节加密成 ``.zip.enc`` 的完整内容。"""
    _require_aesgcm()
    salt = os.urandom(16)
    nonce = os.urandom(12)
    header: dict[str, Any] = {
        "v": 1,
        "cipher": CIPHER_NAME,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "salt": _b64(salt),
        "nonce": _b64(nonce),
        "kdf": KDF_SCRYPT if _SCRYPT_OK else KDF_PBKDF2,
    }
    if header["kdf"] == KDF_SCRYPT:
        header.update({"n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P})
    else:
        header["iterations"] = PBKDF2_ITERATIONS

    header_bytes = json.dumps(header, ensure_ascii=False, sort_keys=True).encode("utf-8")
    key = _derive_key(effective_password(password), salt, header)
    ciphertext = AESGCM(key).encrypt(nonce, zip_bytes, MAGIC + header_bytes)
    return MAGIC + struct.pack("<I", len(header_bytes)) + header_bytes + ciphertext


def decrypt_archive(blob: bytes, password: str) -> bytes:
    """把 ``.zip.enc`` 的内容解回 zip 字节；密码错误 / 文件被动过都报同一条人话。"""
    _require_aesgcm()
    if not is_encrypted_bytes(blob):
        raise SessionArchiveError("这不是加密的会话导出文件")
    offset = len(MAGIC)
    if len(blob) < offset + 4:
        raise SessionArchiveError("会话文件已损坏（头部不完整）")
    (header_len,) = struct.unpack("<I", blob[offset : offset + 4])
    if header_len <= 0 or len(blob) < offset + 4 + header_len:
        raise SessionArchiveError("会话文件已损坏（头长度非法）")
    header_bytes = blob[offset + 4 : offset + 4 + header_len]
    try:
        header = json.loads(header_bytes.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise SessionArchiveError(f"会话文件已损坏（头不是合法 JSON）：{exc}")
    if not isinstance(header, dict):
        raise SessionArchiveError("会话文件已损坏（头格式不正确）")

    salt = _unb64(header.get("salt") or "")
    nonce = _unb64(header.get("nonce") or "")
    if not salt or not nonce:
        raise SessionArchiveError("会话文件已损坏（缺少密钥派生参数）")

    key = _derive_key(effective_password(password), salt, header)
    ciphertext = blob[offset + 4 + header_len :]
    try:
        plain = AESGCM(key).decrypt(nonce, ciphertext, MAGIC + header_bytes)
    except InvalidTag:
        raise SessionArchiveError("密码错误，或者该会话文件已被改动/损坏")
    except Exception as exc:  # noqa: BLE001
        raise SessionArchiveError(f"解密会话文件失败：{exc}")
    if not plain.startswith(_ZIP_MAGIC):
        raise SessionArchiveError("解密结果不是有效的 zip（文件可能已损坏）")
    return plain


def read_finished_zip(blob: bytes) -> bytes:
    """解密后的明文直接当 zip 用；这里只做一步「确实像 zip」的校验。"""
    if not blob.startswith(_ZIP_MAGIC):
        raise SessionArchiveError("这不是有效的会话文件（既不是 zip 也不是本程序的加密格式）")
    return blob
