"""会话导出 / 导入的离线验证（不连真实数据库、不调 LLM、不弹窗）。

覆盖三层：

1. **格式层**：`session_archive` 的 zip 组装 / 读回 / AES-256-GCM 加解密
   （含短密码自动补前缀、错误密码、篡改头部）；
2. **传输层**：`thread_transfer` 在真实 `AsyncSqliteSaver`（临时文件）上
   读 → 导出 → 写入另一个 thread → 再读，逐字段比对消息对象是否完整；
3. **兼容层**：`sub_agent_compat` 的「子 agent 缺失 / 状态键不一致 / 本机更多子 agent」。

用法（项目根目录）：

    venv\\Scripts\\python.exe scripts\\verify_session_transfer.py

全部断言通过时退出码为 0，并打印 OK。
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402
from langgraph.checkpoint.base import empty_checkpoint  # noqa: E402
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver  # noqa: E402

from app.services import session_archive, thread_transfer  # noqa: E402

SECRET_ANSWER = "这是助手回复：会话导出验证 ✅"


def _checkpoint(messages: list, extra: dict | None = None) -> dict:
    cp = empty_checkpoint()
    channel_values = {"messages": list(messages)}
    channel_versions = {"messages": "00000000000000000000000000000001.0.111"}
    for key, value in (extra or {}).items():
        channel_values[key] = value
        channel_versions[key] = "00000000000000000000000000000002.0.222"
    cp["channel_values"] = channel_values
    cp["channel_versions"] = channel_versions
    cp["versions_seen"] = {}
    return cp


def _messages() -> list:
    return [
        HumanMessage(content="请把这段文档总结一下"),
        AIMessage(
            content="",
            tool_calls=[
                {"name": "read_file", "args": {"path": "a.md"}, "id": "call_1"}
            ],
            usage_metadata={"input_tokens": 10, "output_tokens": 3, "total_tokens": 13},
        ),
        ToolMessage(content="文件内容：hello", tool_call_id="call_1"),
        AIMessage(
            content=SECRET_ANSWER,
            usage_metadata={"input_tokens": 20, "output_tokens": 22, "total_tokens": 42},
        ),
    ]


async def _make_saver(db_path: Path):
    """创建并 setup 一个临时 SQLite saver。

    注意：必须**同时保留** `from_conn_string` 返回的上下文管理器——只保留 saver 时
    那个 async generator 会被 GC 掉，底层 aiosqlite 连接随之关闭（setup 直接报
    "Cannot operate on a closed database"）。
    """
    cm = AsyncSqliteSaver.from_conn_string(str(db_path))
    saver = await cm.__aenter__()
    await saver.setup()
    return cm, saver


async def check_transfer(tmp: Path) -> dict:
    src_db = tmp / "src.sqlite3"
    cm, saver = await _make_saver(src_db)
    thread_id = "验证会话"
    try:
        await saver.aput(
            {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
            _checkpoint(_messages(), {"instructions_for_subagents": {}}),
            {"source": "loop", "step": 0},
            {"messages": "00000000000000000000000000000001.0.111"},
        )
        await saver.aput(
            {"configurable": {"thread_id": thread_id, "checkpoint_ns": "worker"}},
            _checkpoint([HumanMessage(content="子 agent 输入"), AIMessage(content="子 agent 输出")]),
            {"source": "loop", "step": 0},
            {"messages": "00000000000000000000000000000001.0.111"},
        )

        namespaces = await _namespaces(saver, thread_id)
        assert sorted(namespaces) == ["", "worker"], f"命名空间不对：{namespaces}"

        payload = await thread_transfer.read_thread(saver, thread_id, namespaces)
        assert payload["namespaces"].keys() == {"", "worker"}
        assert thread_transfer.count_messages(payload) == 4, "消息条数不对"
    finally:
        await cm.__aexit__(None, None, None)

    # 写进另一个库 / 另一个会话名
    dst_db = tmp / "dst.sqlite3"
    cm2, saver2 = await _make_saver(dst_db)
    try:
        written = await thread_transfer.write_thread(saver2, "导入后的会话", payload)
        assert sorted(written) == ["", "worker"], f"写入的命名空间不对：{written}"

        main = await saver2.aget_tuple({"configurable": {"thread_id": "导入后的会话"}})
        assert main is not None, "导入后读不到主图 checkpoint"
        messages = main.checkpoint["channel_values"]["messages"]
        assert len(messages) == 4, f"导入后消息条数不对：{len(messages)}"
        assert messages[0].content == "请把这段文档总结一下"
        assert messages[1].tool_calls and messages[1].tool_calls[0]["name"] == "read_file"
        assert messages[1].usage_metadata.get("total_tokens") == 13, "usage_metadata 丢了"
        assert messages[2].tool_call_id == "call_1", "ToolMessage 关联丢了"
        assert messages[3].content == SECRET_ANSWER

        sub = await saver2.aget_tuple(
            {"configurable": {"thread_id": "导入后的会话", "checkpoint_ns": "worker"}}
        )
        assert sub is not None, "导入后读不到子图 checkpoint"
        sub_msgs = sub.checkpoint["channel_values"]["messages"]
        assert len(sub_msgs) == 2 and sub_msgs[1].content == "子 agent 输出"
    finally:
        await cm2.__aexit__(None, None, None)
    return payload


async def _namespaces(saver, thread_id: str) -> list[str]:
    rows: list[str] = []
    async with saver.lock:
        cur = await saver.conn.execute(
            "SELECT DISTINCT checkpoint_ns FROM checkpoints WHERE thread_id = ?",
            (thread_id,),
        )
        try:
            rows = [str(r[0]) for r in await cur.fetchall()]
        finally:
            await cur.close()
    return rows


def check_archive(payload: dict, tmp: Path) -> None:
    agent_config = {
        "agent_id": "验证 Agent",
        "name": "验证 Agent",
        "main_agent": {"system_prompt": "sp", "api_key": "sk-secret-key"},
        "sub_agents": [
            {
                "name": "worker",
                "state_messages_key": "worker_messages",
                "history_token_measure_key": None,
                "extracted_summary_ai_msg_key": None,
            }
        ],
    }
    zip_bytes = session_archive.build_archive(
        agent_config=agent_config,
        manifest={"app": {"edition": "standard", "storage": "sqlite"}},
        sessions=[
            {
                "thread_id": "验证会话",
                "name": "验证会话",
                "message_count": 4,
                "last_updated": "2026-11-07 18:00",
                "payload": payload,
                "conversation": "# 主 agent 历史\n\n你好\n",
            }
        ],
    )
    assert zip_bytes.startswith(b"PK"), "未加密导出应是普通 zip"

    archive = session_archive.read_archive(zip_bytes)
    assert archive["manifest"]["format"] == session_archive.FORMAT_NAME
    assert archive["manifest"]["agent"]["name"] == "验证 Agent"
    assert archive["agent_config"]["main_agent"]["api_key"] == "sk-secret-key"
    assert len(archive["sessions"]) == 1
    assert archive["sessions"][0]["payload"]["namespaces"]["worker"], "会话数据丢失"
    assert "主 agent 历史" in archive["sessions"][0]["conversation"]

    # 加密：短密码（<10 字符）应自动补前缀，且能解开
    blob = session_archive.encrypt_archive(zip_bytes, "123456")
    assert session_archive.is_encrypted_bytes(blob), "加密文件缺少魔数"
    assert not blob.startswith(b"PK"), "加密后不应还是明文 zip"
    assert session_archive.decrypt_archive(blob, "123456") == zip_bytes, "解密结果与原文不一致"

    # 长密码同样可用（不补前缀）；两种密码不能互通
    blob2 = session_archive.encrypt_archive(zip_bytes, "正确密码abcdefgh")
    assert session_archive.decrypt_archive(blob2, "正确密码abcdefgh") == zip_bytes
    try:
        session_archive.decrypt_archive(blob2, "错误密码abcdefgh")
    except session_archive.SessionArchiveError as exc:
        assert "密码错误" in str(exc), f"错误密码提示不人话：{exc}"
    else:
        raise AssertionError("错误密码竟然解开了")

    # 篡改文件头（AAD 覆盖头部）必须解不开
    tampered = bytearray(blob)
    header_len = int.from_bytes(tampered[len(session_archive.MAGIC) :][:4], "little")
    header_start = len(session_archive.MAGIC) + 4
    tampered[header_start : header_start + header_len] = b" " * header_len
    try:
        session_archive.decrypt_archive(bytes(tampered), "123456")
    except session_archive.SessionArchiveError:
        pass
    else:
        raise AssertionError("头部被改坏的文件竟然解开了")

    # 解密后的明文必须是 zip：拿非 zip 明文去「伪装加密」应报错
    bogus = session_archive.MAGIC + (0).to_bytes(4, "little")
    try:
        session_archive.decrypt_archive(bogus, "123456")
    except session_archive.SessionArchiveError:
        pass
    else:
        raise AssertionError("空头部文件竟然解开了")

    # 落盘 → 按文件头识别 → 读回（模拟导入端点）
    path = tmp / "export.zip.enc"
    path.write_bytes(blob)
    assert session_archive.look_encrypted(path) is True
    assert session_archive.read_archive(session_archive.decrypt_archive(path.read_bytes(), "123456"))


def check_compat() -> None:
    exported = [
        {"name": "worker", "state_messages_key": "worker_messages"},
        {"name": "docs", "state_messages_key": "docs_messages"},
    ]
    local_ok = [
        {"name": "worker", "state_messages_key": "worker_messages"},
        {"name": "docs", "state_messages_key": "docs_messages"},
        {"name": "extra", "state_messages_key": "extra_messages"},
    ]
    ok, reason = session_archive.sub_agent_compat(exported, local_ok)
    assert ok, f"本机子 agent 更多时应可迁入：{reason}"

    ok, reason = session_archive.sub_agent_compat(exported, local_ok[:1])
    assert not ok and "docs" in reason, f"缺子 agent 应不可迁入：{reason}"

    bad_keys = [
        {"name": "worker", "state_messages_key": "worker_messages"},
        {"name": "docs", "state_messages_key": "docs_messages_2"},
    ]
    ok, reason = session_archive.sub_agent_compat(exported, bad_keys)
    assert not ok and "状态通道键" in reason, f"状态键不一致应不可迁入：{reason}"

    ok, _ = session_archive.sub_agent_compat([], [])
    assert ok, "没有子 agent 时应总是兼容"


def check_password_prefix() -> None:
    prefix = session_archive.PASSWORD_PREFIX
    assert session_archive.effective_password("123456789") == (prefix + "123456789").encode()
    assert session_archive.effective_password("1234567890") == b"1234567890"
    assert session_archive.effective_password("") == prefix.encode()


def main() -> None:
    check_password_prefix()
    check_compat()
    with tempfile.TemporaryDirectory(prefix="masession_") as raw_tmp:
        tmp = Path(raw_tmp)
        payload = asyncio.run(check_transfer(tmp))
        check_archive(payload, tmp)
    print("OK：会话导出/导入的格式层、传输层、兼容层全部通过")
    print(json.dumps({"ok": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
