"""流式聊天 WebSocket 端点。

客户端→服务端消息：
  {"type": "send", "content": "..."}   发送一轮消息
  {"type": "proactive_summarize", "percent": 20}           触发主动全量总结（主 agent）
  {"type": "proactive_summarize_sub", "sub_agent": "...", "percent": 20}
                                                          触发子 agent 主动全量总结
                                                          （percent 缺省 / 非法时走默认或图内确认）
  {"type": "clean_all", "archive": false}
                                       删除主图当前线程全部历史消息（不可复原）；
                                       archive=true 时先按「主动全量总结」的方式落一份快照，
                                       再执行删除（归档失败则中止删除）
  {"type": "resume", "value": "yes"}   回复 interrupt 中断
  {"type": "stop"}                     软取消：请求 drain，当前节点跑完后在 superstep 边界停下
                                       （不中断正在执行的工具/LLM；连接保持，直到本轮回 done）；
                                       drain 结束后会自动跑一遍图内 tool_call 修复

服务端→客户端事件：
  {"type": "text", "source": "main"|"sub:<name>", "text": token}
  {"type": "reasoning", "source": "main"|"sub:<name>", "text": token}
  {"type": "phase", "source": "main"|"sub:<name>",
   "phase": "thinking"|"answering"|"tool_edit"|"delegate"|"tool_wait"}  状态栏阶段
  {"type": "subgraph_start"|"subgraph_end", "name": ...}
  {"type": "tool_call", "source": "main"|"sub:<name>", "name", "args"}
  {"type": "tool_result", "source": "main"|"sub:<name>", "name", "content"}
  {"type": "interrupt", "prompt"}
  {"type": "snapshot_saved", "id", "thread_id", "created_at", "total_messages"}
                                           「归档为快照后删除」已成功落盘快照
  {"type": "status", "status": "loading"}  运行时构建中（embedding 模型加载等）
  {"type": "status", "status": "ready"}    运行时已就绪，图/LLM 即将开始
  {"type": "sub_agents", "names": [...]}   配置里的子 agent 名单（前端分区渲染用）
  {"type": "stopping"}                     已收到停止请求，正在等当前节点收尾
  {"type": "backfill"}                     停止后修复削掉了末尾用户消息，提示是否回填
  {"type": "done", "final_state", "stopped"}  stopped=true 表示本轮是软取消结束
  {"type": "error", "message"}
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from langgraph.runtime import RunControl

from app.deps import chat_manager
from app.services import snapshot as snapshot_service
from app.services.chat import (
    make_clean_all_input,
    make_edit_input,
    make_proactive_summary_input,
    make_proactive_summary_input_for_sub_agent,
    make_user_input,
)

router = APIRouter()

logger = logging.getLogger(__name__)


def _to_percent(value) -> float | None:
    """解析前端传来的总结百分比；非法 / 缺省返回 None（走默认或图内确认）。"""
    if value is None:
        return None
    try:
        percent = float(value)
    except (TypeError, ValueError):
        return None
    return percent if percent > 0 else None


def _is_bad_request(e: BaseException) -> bool:
    """异常链上是否带着 provider 的 HTTP 400。

    OpenAI 兼容接口（DeepSeek 也走同一套 SDK）的 400 是 `openai.BadRequestError`，
    实例上有 `status_code = 400`；被包装过时沿 `__cause__ / __context__` 往上找。
    """
    seen: set[int] = set()
    cur: BaseException | None = e
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if getattr(cur, "status_code", None) == 400:
            return True
        if getattr(getattr(cur, "response", None), "status_code", None) == 400:
            return True
        if "error code: 400" in str(cur).lower():
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def _format_build_error(e: Exception) -> str:
    """运行时构建失败时，给出面向用户的人话提示。

    原始异常（含堆栈）由调用处 logger.exception 写入服务端日志（托盘模式为
    logs/server.log），方便后续排查。
    """
    text = str(e)
    lowered = text.lower()
    if "vector" in lowered and ("not available" in lowered or "extension" in lowered):
        return (
            "未检测到 PostgreSQL 的 pgvector 扩展，长期记忆/会话存储无法初始化。"
            "请双击运行项目根目录的 install_pgvector.bat 一键安装，"
            "完成后重启本程序并重试。"
        )
    if any(
        k in lowered
        for k in (
            "connection refused",
            "could not connect",
            "connection to server",
            "could not translate host name",
            "timeout expired",
            "password authentication failed",
            "no password supplied",
            "server closed the connection",
            "terminating connection",
            "could not receive data",
            "the database system is starting up",
            "the database system is in recovery mode",
        )
    ):
        return (
            "程序暂时连不上数据库，所以这个会话还打不开。\n"
            "常见原因是 PostgreSQL 服务没有启动：按 Win + R 输入 services.msc，"
            "在列表里找到名字含 PostgreSQL 的服务，右键选择「启动」，稍等片刻后重试。\n"
            "如果该服务已经在运行，请检查这个 multi-agent 的数据库地址、端口、用户名、密码是否正确。"
        )
    if "does not exist" in lowered and "database" in lowered:
        return (
            "在 PostgreSQL 里找不到这个 multi-agent 绑定的数据库。\n"
            "请检查创建 / 编辑该 multi-agent 时填写的「checkpoint 数据库名」是否正确，"
            "并确认该库已在 PostgreSQL 中创建；改正后重试。"
        )
    return "无法初始化该会话的运行环境，请稍后重试；若持续出现，请查看服务端日志 logs/server.log。"


def _format_edit_error(e: Exception) -> str:
    """把「编辑历史消息」过程中抛出的异常翻译成给用户看的人话。

    只面向界面展示自然语言；原始异常（含堆栈）由调用处 logger.exception 写入
    服务端日志（托盘模式为 logs/server.log），方便后续排查。
    """
    text = str(e)
    lowered = text.lower()

    if "vector" in lowered and ("not available" in lowered or "extension" in lowered):
        return (
            "未检测到 PostgreSQL 的 pgvector 扩展，会话存储无法工作。"
            "请双击运行项目根目录的 install_pgvector.bat 一键安装，"
            "完成后重启本程序再重试。"
        )
    if isinstance(e, asyncio.CancelledError) or "cancelled" in lowered:
        return "写入被取消（连接已断开或页面已离开），本次改动未完成。"
    if "out of range" in lowered:
        return "要修改的消息序号超出当前会话范围，页面历史可能已与后端不同步，请刷新页面后重试。"
    if "type mismatch" in lowered:
        return "待替换的消息类型与原文不一致，无法替换，请刷新页面后重试。"
    if "unsupported message type" in lowered:
        return "该消息类型暂不支持编辑。"
    if "recursion" in lowered:
        return "图执行步数超出上限，本次写入未完成。"
    if "does not exist" in lowered or "undefinedtable" in lowered:
        return "会话数据表尚未就绪，请先在该会话发送一条消息，再进入编辑模式。"
    if "connection" in lowered or "could not connect" in lowered or "connection refused" in lowered:
        return "无法连接数据库，会话数据暂时无法写入，请确认数据库仍在运行后重试。"
    if "timeout" in lowered or "timed out" in lowered:
        return "写入数据库超时，本次改动未完成，请稍后重试。"
    if "concurrent" in lowered or "invalidupdate" in lowered:
        return "该会话正有另一个操作在进行，请等它结束后再重试。"
    return "写入过程中发生了未预期的错误，请稍后重试；若持续出现，请查看服务端日志 logs/server.log。"


@router.websocket("/api/agents/{agent_id}/threads/{thread_id}/chat")
async def chat_ws(websocket: WebSocket, agent_id: str, thread_id: str):
    await websocket.accept()

    try:
        if not chat_manager.has_runtime(agent_id):
            await websocket.send_json({"type": "status", "status": "loading"})
        runtime = await chat_manager.get_runtime(agent_id)
        # 运行时已就绪（embedding 已加载完成）。显式通知前端脱离「加载Embedding模型中」，
        # 否则在「构建完成」到「第一条 token」之间（思考模型可能先出 reasoning、或先跑工具）
        # 状态栏会一直停在 loading，与实际不符。
        await websocket.send_json({"type": "status", "status": "ready"})
        # 下发配置里的子 agent 名单：前端据此判断是否启用「子 agent 分区 + 下拉切换」
        # （配置里子 agent ≤1 时自动关闭该功能）。
        await websocket.send_json(
            {"type": "sub_agents", "names": [s.name for s in runtime.config.sub_agents]}
        )
    except Exception as e:
        logger.exception(
            "构建运行时失败 (agent_id=%s, thread_id=%s)", agent_id, thread_id
        )
        await websocket.send_json({"type": "error", "message": _format_build_error(e)})
        await websocket.close()
        return

    send_lock = asyncio.Lock()
    resume_future: asyncio.Future | None = None
    current_run: asyncio.Task | None = None
    # 当前这一轮 run 的软取消控制器：用户点「停止」时 request_drain()，
    # 图在当前节点跑完后的 superstep 边界停下（不等价于硬 cancel）。
    current_control: RunControl | None = None
    # 本轮 run 是否已经「真正开始」（收到图的第一条事件）。
    # 用来避免 stop 抢在任务启动之前 drain —— 那样连用户消息都不会落库。
    current_started: asyncio.Event | None = None

    async def emit(event: dict):
        async with send_lock:
            await websocket.send_json(event)

    async def on_interrupt(prompt: str) -> str:
        nonlocal resume_future
        await emit({"type": "interrupt", "prompt": prompt})
        resume_future = asyncio.get_running_loop().create_future()
        return await resume_future

    async def _run(
        content: str,
        proactive: bool = False,
        sub_agent: str | None = None,
        summary_percent: float | None = None,
        clean_all: bool = False,
        clean_all_archive: bool = False,
        control: RunControl | None = None,
        started: asyncio.Event | None = None,
    ):
        nonlocal current_control, current_started
        import os

        html_files: list[str] = []

        async def emit_checked(event: dict):
            if started is not None and not started.is_set():
                started.set()
            if event.get("type") == "tool_call":
                name = event.get("name") or ""
                args = event.get("args") or {}
                fp = str(args.get("file_path") or "")
                if name == "write_file" and fp.lower().endswith(".html"):
                    html_files.append(fp)
            await emit(event)

        def open_html_files():
            # HTML 报告生成后，用系统默认程序打开
            for fp in html_files:
                root = runtime.config.main_agent.file_tools.root_dir
                full = fp if os.path.isabs(fp) else os.path.join(root, fp)
                if os.path.exists(full):
                    try:
                        os.startfile(full)  # noqa: F821 — Windows
                    except Exception:
                        pass

        async def finish(result, *, allow_backfill: bool):
            """本轮正常收尾：drained 时先跑图内修复，再回 done。

            修复期间前端保持在「停止中」（它要等 done 才恢复「发送」）。
            修复可能削掉末尾还没被消费的用户消息，此时发 backfill 让前端提示回填。
            """
            if result.drained:
                removed = await runtime.repair_tool_results(thread_id)
                if removed and allow_backfill:
                    await emit({"type": "backfill"})
            await emit(
                {
                    "type": "done",
                    "final_state": result.text,
                    "stopped": result.drained,
                }
            )
            open_html_files()

        # 400 重跑要用同一份用户输入；放在 try 外先占位，异常分支才能判断是否已经构造好。
        user_input: dict | None = None
        try:
            if clean_all and clean_all_archive:
                # 「归档为快照后删除」：快照与「主动全量总结」共用同一个 capture_snapshot，
                # 产物结构完全一致（main + 所有子图 + total_messages）。
                # 这里刻意不走图内节点：必须在真正删除前确认快照已落盘，失败就中止删除。
                try:
                    archived = await snapshot_service.capture_snapshot(
                        runtime.config.checkpoint_conn_string,
                        thread_id,
                        runtime.config.agent_id,
                        raise_on_error=True,
                    )
                except Exception:
                    logger.exception(
                        "归档快照失败，已取消删除 (agent_id=%s, thread_id=%s)",
                        agent_id,
                        thread_id,
                    )
                    await emit(
                        {
                            "type": "error",
                            "message": (
                                "生成快照失败，因此没有删除任何历史消息。"
                                "请检查数据库连接与磁盘空间后重试，或改用「直接删除」。"
                            ),
                        }
                    )
                    return
                if archived:
                    await emit({"type": "snapshot_saved", **archived})
            if clean_all:
                user_input = make_clean_all_input()
            elif sub_agent:
                user_input = make_proactive_summary_input_for_sub_agent(
                    sub_agent, summary_percent
                )
            elif proactive:
                user_input = make_proactive_summary_input(summary_percent)
            else:
                user_input = make_user_input(content)
            result = await runtime.run(
                thread_id, user_input, emit_checked, on_interrupt, control=control
            )
            await finish(result, allow_backfill=True)
        except Exception as e:
            # provider 400（典型原因：历史里有悬空 tool_call）：先用图内修复把历史补干净，
            # 再用同一份用户输入重跑一次；仍然失败才把错误交给前端（失败/回填气泡）。
            if (
                user_input is not None
                and bool(content)
                and not proactive
                and not sub_agent
                and not clean_all
                and _is_bad_request(e)
            ):
                logger.warning(
                    "provider 400，先做图内 tool_call 修复再重跑一次 (agent_id=%s, thread_id=%s): %s",
                    agent_id,
                    thread_id,
                    e,
                )
                # 修复会削掉这一轮的用户消息（它就是尾部那条 Human），下面重跑会重新写入。
                await runtime.repair_tool_results(thread_id)
                # 重跑是全新的一轮 run：换一个 RunControl，保证「停止」仍然可用。
                control = RunControl()
                started = asyncio.Event()
                current_control, current_started = control, started
                try:
                    # echo_user=False：这一轮的用户气泡上一轮已经推过，别再推一遍。
                    result = await runtime.run(
                        thread_id,
                        user_input,
                        emit_checked,
                        on_interrupt,
                        control=control,
                        echo_user=False,
                    )
                except Exception as e2:  # noqa: BLE001
                    logger.exception(
                        "400 修复后重跑仍然失败 (agent_id=%s, thread_id=%s)",
                        agent_id,
                        thread_id,
                    )
                    await emit({"type": "error", "message": str(e2)})
                else:
                    await finish(result, allow_backfill=True)
            else:
                await emit({"type": "error", "message": str(e)})

    async def _run_edit(
        request: dict,
        control: RunControl | None = None,
        started: asyncio.Event | None = None,
    ):
        async def emit_checked(event: dict):
            if started is not None and not started.is_set():
                started.set()
            await emit(event)

        try:
            user_input = make_edit_input(
                request_for_subagent=bool(request.get("request_for_subagent")),
                subagent_name=request.get("subagent_name") or None,
                msg_indice=int(request.get("msg_indice", -1)),
                substitute_msg_dict=request.get("substitute_msg") or {},
            )
            result = await runtime.run(
                thread_id, user_input, emit_checked, on_interrupt, control=control
            )
            if result.drained:
                # 编辑链路同样可能在 LLM 之后 / tool 之前被 drain：补一遍图内修复。
                await runtime.repair_tool_results(thread_id)
            await emit(
                {
                    "type": "done",
                    "final_state": result.text,
                    "stopped": result.drained,
                }
            )
        except Exception as e:
            logger.exception(
                "编辑历史消息失败 (agent_id=%s, thread_id=%s)", agent_id, thread_id
            )
            await emit({"type": "error", "message": _format_edit_error(e)})

    try:
        while True:
            data = await websocket.receive_json()
            mtype = data.get("type")

            if mtype == "send":
                if current_run and not current_run.done():
                    await emit({"type": "error", "message": "上一轮仍在运行"})
                    continue
                control = RunControl()
                started = asyncio.Event()
                current_control, current_started = control, started
                current_run = asyncio.create_task(
                    _run(data.get("content", ""), control=control, started=started)
                )
            elif mtype == "proactive_summarize":
                if current_run and not current_run.done():
                    await emit({"type": "error", "message": "上一轮仍在运行"})
                    continue
                control = RunControl()
                started = asyncio.Event()
                current_control, current_started = control, started
                current_run = asyncio.create_task(
                    _run(
                        "",
                        proactive=True,
                        summary_percent=_to_percent(data.get("percent")),
                        control=control,
                        started=started,
                    )
                )
            elif mtype == "proactive_summarize_sub":
                if current_run and not current_run.done():
                    await emit({"type": "error", "message": "上一轮仍在运行"})
                    continue
                control = RunControl()
                started = asyncio.Event()
                current_control, current_started = control, started
                current_run = asyncio.create_task(
                    _run(
                        "",
                        sub_agent=data.get("sub_agent", ""),
                        summary_percent=_to_percent(data.get("percent")),
                        control=control,
                        started=started,
                    )
                )
            elif mtype == "edit":
                if current_run and not current_run.done():
                    await emit({"type": "error", "message": "上一轮仍在运行"})
                    continue
                control = RunControl()
                started = asyncio.Event()
                current_control, current_started = control, started
                current_run = asyncio.create_task(
                    _run_edit(data.get("request", {}), control=control, started=started)
                )
            elif mtype == "clean_all":
                if current_run and not current_run.done():
                    await emit({"type": "error", "message": "上一轮仍在运行"})
                    continue
                control = RunControl()
                started = asyncio.Event()
                current_control, current_started = control, started
                current_run = asyncio.create_task(
                    _run(
                        "",
                        clean_all=True,
                        clean_all_archive=bool(data.get("archive")),
                        control=control,
                        started=started,
                    )
                )
            elif mtype == "resume":
                if resume_future and not resume_future.done():
                    resume_future.set_result(data.get("value", ""))
            elif mtype == "stop":
                # 软取消：只请求 drain，不 break、不 cancel。图会在当前节点跑完后的
                # 下一个 superstep 边界停下（工具会跑完并把 ToolMessage 落库）。
                if current_run and not current_run.done() and current_control is not None:
                    # 若 run 还没真正开始（例如刚发完消息、还在建运行时），先等它的
                    # 第一条事件。否则 drain 会抢在输入 checkpoint 之前，连用户消息都不落库。
                    started = current_started
                    if started is not None and not started.is_set():
                        try:
                            await asyncio.wait_for(started.wait(), timeout=2.0)
                        except asyncio.TimeoutError:
                            pass
                    if current_run.done():
                        continue
                    current_control.request_drain("user")
                    try:
                        await emit({"type": "stopping"})
                    except Exception:  # noqa: BLE001
                        # 连接已断时不要让「通知前端」失败反过来把 run 硬取消掉。
                        logger.exception("发送 stopping 事件失败")
    except WebSocketDisconnect:
        pass
    finally:
        if current_run and not current_run.done():
            current_run.cancel()
