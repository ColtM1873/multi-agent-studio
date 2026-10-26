"""按会话归属运行的后端会话中枢（ID91）。

一次对话运行不再由某个 WebSocket 连接拥有：

* ``SessionHub`` 为每个 ``(agent_id, thread_id)`` 保存运行记录、事件日志与订阅者；
* WebSocket 只负责 attach / replay / 转发命令，断开只退订；
* 页面刷新、切视图、关闭窗口到托盘都不影响后台运行；
* ``/api/sessions`` 从同一份记录派生“正在进行 / 已完工待验收”的 UI 状态。

事件统一带 ``run_id`` + ``seq``，前端据此去重与断线回放。
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from langgraph.runtime import RunControl

from app.services import snapshot as snapshot_service
from app.services.chat import (
    make_clean_all_input,
    make_edit_input,
    make_proactive_summary_input,
    make_proactive_summary_input_for_sub_agent,
    make_user_input,
)

logger = logging.getLogger(__name__)

SessionKey = tuple[str, str]

ACTIVE_STATUSES = {"starting", "running", "stopping", "waiting"}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _to_percent(value) -> float | None:
    if value is None:
        return None
    try:
        percent = float(value)
    except (TypeError, ValueError):
        return None
    return percent if percent > 0 else None


def _is_bad_request(e: BaseException) -> bool:
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


@dataclass
class RunRecord:
    key: SessionKey
    agent_name: str = ""
    run_id: str = ""
    status: str = "idle"  # idle | starting | running | stopping | waiting | completed
    end_reason: str = ""  # done | stopped | error
    accepted: bool = True
    backfill: bool = False
    started_at: str | None = None
    finished_at: str | None = None
    seq: int = 0
    events: list[dict] = field(default_factory=list)
    subscribers: dict[str, asyncio.Queue] = field(default_factory=dict)
    task: asyncio.Task | None = None
    control: RunControl | None = None
    started_event: asyncio.Event | None = None
    interrupt_future: asyncio.Future | None = None
    interrupt_prompt: str | None = None
    history_markdown: str = ""
    sub_agents: list[str] = field(default_factory=list)
    error: str = ""
    kind: str = "send"
    pending_stop: bool = False


class SessionHub:
    """运行记录 + 事件日志 + 订阅 + 验收。"""

    def __init__(self, chat_manager) -> None:
        self.chat_manager = chat_manager
        self._records: dict[SessionKey, RunRecord] = {}
        self._subs: dict[str, SessionKey] = {}
        self._lock = asyncio.Lock()
        self.active_view: SessionKey | None = None
        self.active_view_visible = True

    # ------------------------------------------------------------------ #
    # 记录 / 查询
    # ------------------------------------------------------------------ #
    def _ensure(self, agent_id: str, thread_id: str) -> RunRecord:
        key = (agent_id, thread_id)
        rec = self._records.get(key)
        if rec is None:
            rec = RunRecord(key=key)
            self._records[key] = rec
        return rec

    def get_record(self, agent_id: str, thread_id: str) -> RunRecord | None:
        return self._records.get((agent_id, thread_id))

    def is_pending(self, agent_id: str, thread_id: str) -> bool:
        rec = self._records.get((agent_id, thread_id))
        if rec is None:
            return False
        return rec.status in ACTIVE_STATUSES or (rec.status == "completed" and not rec.accepted)

    def list_pending(self) -> list[dict[str, Any]]:
        return [
            self._public(rec)
            for rec in list(self._records.values())
            if rec.status in ACTIVE_STATUSES or (rec.status == "completed" and not rec.accepted)
        ]

    async def snapshot_pending(self) -> tuple[list[dict[str, Any]], dict[str, int]]:
        """给 HTTP 轮询用的线程安全快照（与 run 的写操作同锁）。"""
        async with self._lock:
            sessions = [
                self._public(rec)
                for rec in list(self._records.values())
                if rec.status in ACTIVE_STATUSES
                or (rec.status == "completed" and not rec.accepted)
            ]
            running = sum(1 for rec in self._records.values() if rec.status in ACTIVE_STATUSES)
            completed = sum(
                1
                for rec in self._records.values()
                if rec.status == "completed" and not rec.accepted
            )
        return sessions, {"running": running, "completed": completed}

    def counts(self) -> dict[str, int]:
        running = completed = 0
        for rec in self._records.values():
            if rec.status in ACTIVE_STATUSES:
                running += 1
            elif rec.status == "completed" and not rec.accepted:
                completed += 1
        return {"running": running, "completed": completed}

    def _public(self, rec: RunRecord) -> dict[str, Any]:
        agent_id, thread_id = rec.key
        return {
            "agent_id": agent_id,
            "thread_id": thread_id,
            "agent_name": rec.agent_name,
            "run_id": rec.run_id,
            "status": rec.status,
            "end_reason": rec.end_reason,
            "accepted": rec.accepted,
            "backfill": rec.backfill,
            "started_at": rec.started_at,
            "finished_at": rec.finished_at,
            "seq": rec.seq,
            "sub_agents": list(rec.sub_agents),
            "error": rec.error,
        }

    def _run_state(self, rec: RunRecord, *, snapshot: bool = False) -> dict[str, Any]:
        state = {
            "type": "run_state",
            **self._public(rec),
        }
        if snapshot:
            state["snapshot"] = True
            if rec.status != "idle" and rec.history_markdown:
                state["history_markdown"] = rec.history_markdown
            if rec.interrupt_prompt and rec.status == "waiting":
                state["interrupt_prompt"] = rec.interrupt_prompt
        return state

    # ------------------------------------------------------------------ #
    # 事件发布 / 订阅
    # ------------------------------------------------------------------ #
    def _publish_locked(self, rec: RunRecord, event: dict) -> dict:
        rec.seq += 1
        payload = dict(event)
        payload["run_id"] = rec.run_id
        payload["seq"] = rec.seq
        rec.events.append(payload)
        dead: list[str] = []
        for sid, q in list(rec.subscribers.items()):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(sid)
        for sid in dead:
            rec.subscribers.pop(sid, None)
            self._subs.pop(sid, None)
        return payload

    async def emit(self, rec: RunRecord, event: dict) -> dict:
        async with self._lock:
            return self._publish_locked(rec, event)

    async def attach(self, agent_id: str, thread_id: str, run_id: str, since: int):
        """注册订阅者并返回 ``(sid, queue)``；队列里已放好快照+回放+replay_done。"""
        async with self._lock:
            rec = self._ensure(agent_id, thread_id)
            q: asyncio.Queue = asyncio.Queue()
            sid = uuid.uuid4().hex
            q.put_nowait(self._run_state(rec, snapshot=True))
            if rec.status in ACTIVE_STATUSES:
                if run_id and run_id == rec.run_id:
                    for event in rec.events:
                        if int(event.get("seq") or 0) > int(since or 0):
                            q.put_nowait(event)
                else:
                    # 客户端没有 run_id（页面刷新）或 run_id 已过期：全量回放当前 run。
                    for event in rec.events:
                        q.put_nowait(event)
            # completed 只发最终 run_state，不回放事件；前端用 /history 取最终内容，
            # 避免「历史 + 事件」重复。
            q.put_nowait(
                {
                    "type": "replay_done",
                    "run_id": rec.run_id,
                    "seq": rec.seq,
                }
            )
            rec.subscribers[sid] = q
            self._subs[sid] = rec.key
            return sid, q

    async def detach(self, sid: str) -> None:
        async with self._lock:
            key = self._subs.pop(sid, None)
            if key is None:
                return
            rec = self._records.get(key)
            if rec is not None:
                rec.subscribers.pop(sid, None)

    # ------------------------------------------------------------------ #
    # 启动 / 停止 / 恢复
    # ------------------------------------------------------------------ #
    async def start(
        self,
        agent_id: str,
        thread_id: str,
        *,
        content: str = "",
        proactive: bool = False,
        sub_agent: str | None = None,
        summary_percent: float | None = None,
        clean_all: bool = False,
        clean_all_archive: bool = False,
        kind: str = "send",
        edit_request: dict | None = None,
    ) -> tuple[bool, str]:
        key = (agent_id, thread_id)
        async with self._lock:
            rec = self._ensure(agent_id, thread_id)
            if rec.task is not None and not rec.task.done():
                return False, "上一轮仍在运行"
            if rec.status in ACTIVE_STATUSES:
                return False, "上一轮仍在运行"
            rec.run_id = uuid.uuid4().hex
            rec.status = "starting"
            rec.end_reason = ""
            rec.accepted = False
            rec.backfill = False
            rec.started_at = _now()
            rec.finished_at = None
            rec.seq = 0
            rec.events = []
            rec.control = None
            rec.started_event = None
            rec.interrupt_future = None
            rec.interrupt_prompt = None
            rec.history_markdown = ""
            rec.sub_agents = []
            rec.error = ""
            rec.kind = kind
            rec.pending_stop = False
            self._publish_locked(rec, {"type": "status", "status": "loading"})
            run_id = rec.run_id

        try:
            runtime = await self.chat_manager.get_runtime(agent_id)
            baseline = await self.chat_manager.thread_history(agent_id, thread_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("构建运行时失败 (agent_id=%s, thread_id=%s)", agent_id, thread_id)
            await self.emit(rec, {"type": "error", "message": _format_build_error(exc)})
            await self._complete(rec, run_id, "error", error=str(exc))
            return False, ""

        if kind == "edit":
            request = edit_request or {}
            try:
                user_input = make_edit_input(
                    request_for_subagent=bool(request.get("request_for_subagent")),
                    subagent_name=request.get("subagent_name") or None,
                    msg_indice=int(request.get("msg_indice", -1)),
                    substitute_msg_dict=request.get("substitute_msg") or {},
                )
            except Exception as exc:  # noqa: BLE001
                await self.emit(rec, {"type": "error", "message": _format_edit_error(exc)})
                await self._complete(rec, run_id, "error", error=str(exc))
                return False, ""
        elif clean_all:
            user_input = make_clean_all_input()
        elif sub_agent:
            user_input = make_proactive_summary_input_for_sub_agent(sub_agent, summary_percent)
        elif proactive:
            user_input = make_proactive_summary_input(summary_percent)
        else:
            user_input = make_user_input(content)

        async with self._lock:
            if rec.run_id != run_id:
                return False, "运行已切换，请重试"
            rec.agent_name = runtime.config.name
            rec.history_markdown = baseline or ""
            rec.sub_agents = [s.name for s in runtime.config.sub_agents]
            rec.status = "stopping" if rec.pending_stop else "running"
            control = RunControl()
            started = asyncio.Event()
            rec.control = control
            rec.started_event = started
            self._publish_locked(rec, {"type": "status", "status": "ready"})
            self._publish_locked(rec, {"type": "sub_agents", "names": list(rec.sub_agents)})
            self._publish_locked(rec, self._run_state(rec))
            rec.task = asyncio.create_task(
                self._execute(
                    rec,
                    runtime,
                    user_input,
                    control=control,
                    started=started,
                    content=content,
                    proactive=proactive,
                    sub_agent=sub_agent,
                    clean_all=clean_all,
                    clean_all_archive=clean_all_archive,
                    kind=kind,
                )
            )
            if rec.pending_stop:
                async def _stop_when_started() -> None:
                    if not started.is_set():
                        await started.wait()
                    if rec.run_id == run_id and rec.control is control and not rec.task.done():
                        control.request_drain("user")

                asyncio.create_task(_stop_when_started())
        return True, ""

    async def stop(self, agent_id: str, thread_id: str) -> bool:
        async with self._lock:
            rec = self._records.get((agent_id, thread_id))
            if rec is None:
                return False
            # 运行时还在构建（embedding 加载）时用户就点了停止：记下意图，等 run
            # 真正开始、用户消息落库后再 drain（ID90 的「等 started」语义）。
            if rec.status == "starting" and rec.task is None:
                rec.pending_stop = True
                rec.status = "stopping"
                self._publish_locked(rec, {"type": "stopping"})
                self._publish_locked(rec, self._run_state(rec))
                return True
            if rec.control is None or rec.task is None or rec.task.done():
                return False
            control = rec.control
            started = rec.started_event
            waiting_future = rec.interrupt_future
            status = rec.status
        if started is not None and not started.is_set():
            try:
                await asyncio.wait_for(started.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
        async with self._lock:
            if rec.control is not control or rec.task is None or rec.task.done():
                return False
            if status == "waiting" and waiting_future is not None and not waiting_future.done():
                waiting_future.set_result("")
            rec.status = "stopping"
            control.request_drain("user")
            self._publish_locked(rec, {"type": "stopping"})
            self._publish_locked(rec, self._run_state(rec))
            return True

    async def resume(self, agent_id: str, thread_id: str, value: str) -> bool:
        async with self._lock:
            rec = self._records.get((agent_id, thread_id))
            if rec is None or rec.interrupt_future is None or rec.interrupt_future.done():
                return False
            rec.interrupt_future.set_result(value)
            rec.interrupt_future = None
            rec.interrupt_prompt = None
            rec.status = "running"
            self._publish_locked(rec, self._run_state(rec))
            return True

    async def accept(self, agent_id: str, thread_id: str) -> bool:
        async with self._lock:
            rec = self._records.get((agent_id, thread_id))
            if rec is None or rec.status != "completed" or rec.accepted:
                return False
            rec.accepted = True
            rec.status = "idle"
            rec.end_reason = ""
            rec.backfill = False
            rec.events = []
            rec.history_markdown = ""
            rec.interrupt_prompt = None
            self._publish_locked(rec, self._run_state(rec))
            return True

    def set_active_view(
        self, agent_id: str | None, thread_id: str | None, visible: bool = True
    ) -> None:
        self.active_view = (agent_id, thread_id) if agent_id and thread_id else None
        self.active_view_visible = bool(visible)

    # ------------------------------------------------------------------ #
    # 运行体
    # ------------------------------------------------------------------ #
    async def _execute(
        self,
        rec: RunRecord,
        runtime,
        user_input: dict,
        *,
        control: RunControl,
        started: asyncio.Event,
        content: str,
        proactive: bool,
        sub_agent: str | None,
        clean_all: bool,
        clean_all_archive: bool,
        kind: str,
    ) -> None:
        from app.runtime.browser_sessions import use_browser_session

        agent_id, thread_id = rec.key
        run_id = rec.run_id
        end_reason = "done"
        html_files: list[str] = []
        session_ctx = use_browser_session(agent_id, thread_id, rec.agent_name)

        async def emit_checked(event: dict) -> None:
            if not started.is_set():
                started.set()
            if event.get("type") == "tool_call":
                name = event.get("name") or ""
                args = event.get("args") or {}
                fp = str(args.get("file_path") or "")
                if name == "write_file" and fp.lower().endswith(".html"):
                    html_files.append(fp)
            await self.emit(rec, event)

        def open_html_files() -> None:
            for fp in html_files:
                root = runtime.config.main_agent.file_tools.root_dir
                full = fp if os.path.isabs(fp) else os.path.join(root, fp)
                if os.path.exists(full):
                    try:
                        os.startfile(full)  # noqa: F821 — Windows
                    except Exception:  # noqa: BLE001
                        pass

        async def finish(result, *, allow_backfill: bool) -> None:
            if result.drained:
                removed = await runtime.repair_tool_results(thread_id)
                if removed and allow_backfill:
                    rec.backfill = True
                    await self.emit(rec, {"type": "backfill"})
            # 先把状态落成 completed，再发 done：前端收到 done 会立刻自动验收，
            # 若等 _complete 再置状态，会出现“accept 抢在 completed 之前 → 验收失败”
            # 的竞态（绿色状态要等用户再进一次才消失）。
            await self._mark_completed(
                rec, run_id, "stopped" if result.drained else "done"
            )
            await self.emit(
                rec,
                {
                    "type": "done",
                    "final_state": result.text,
                    "stopped": result.drained,
                    "backfill": rec.backfill,
                },
            )
            open_html_files()

        async def on_interrupt(prompt: str) -> str:
            future = asyncio.get_running_loop().create_future()
            async with self._lock:
                rec.interrupt_future = future
                rec.interrupt_prompt = prompt
                rec.status = "waiting"
                self._publish_locked(rec, {"type": "interrupt", "prompt": prompt})
                self._publish_locked(rec, self._run_state(rec))
            value = await future
            async with self._lock:
                if rec.interrupt_future is future:
                    rec.interrupt_future = None
                    rec.interrupt_prompt = None
                if rec.status == "waiting":
                    rec.status = "running"
                    self._publish_locked(rec, self._run_state(rec))
            return value

        try:
            with session_ctx:
                if kind == "edit":
                    end_reason = await self._execute_edit(
                        rec, runtime, user_input, emit_checked, on_interrupt, control
                    )
                    return
                try:
                    if clean_all and clean_all_archive:
                        try:
                            archived = await snapshot_service.capture_snapshot(
                                runtime.config.checkpoint_conn_string,
                                thread_id,
                                runtime.config.agent_id,
                                raise_on_error=True,
                            )
                        except Exception:  # noqa: BLE001
                            logger.exception(
                                "归档快照失败，已取消删除 (agent_id=%s, thread_id=%s)",
                                agent_id,
                                thread_id,
                            )
                            await self.emit(
                                rec,
                                {
                                    "type": "error",
                                    "message": (
                                        "生成快照失败，因此没有删除任何历史消息。"
                                        "请检查数据库连接与磁盘空间后重试，或改用「直接删除」。"
                                    ),
                                },
                            )
                            end_reason = "error"
                            return
                        if archived:
                            await self.emit(rec, {"type": "snapshot_saved", **archived})
                    result = await runtime.run(
                        thread_id,
                        user_input,
                        emit_checked,
                        on_interrupt,
                        control=control,
                    )
                    await finish(result, allow_backfill=True)
                    end_reason = "stopped" if result.drained else "done"
                except Exception as exc:  # noqa: BLE001
                    if (
                        user_input is not None
                        and bool(content)
                        and not proactive
                        and not sub_agent
                        and not clean_all
                        and _is_bad_request(exc)
                    ):
                        logger.warning(
                            "provider 400，先做图内 tool_call 修复再重跑一次 "
                            "(agent_id=%s, thread_id=%s): %s",
                            agent_id,
                            thread_id,
                            exc,
                        )
                        await runtime.repair_tool_results(thread_id)
                        new_control = RunControl()
                        new_started = asyncio.Event()
                        async with self._lock:
                            rec.control = new_control
                            rec.started_event = new_started
                        try:
                            result = await runtime.run(
                                thread_id,
                                user_input,
                                emit_checked,
                                on_interrupt,
                                control=new_control,
                                echo_user=False,
                            )
                        except Exception as exc2:  # noqa: BLE001
                            logger.exception(
                                "400 修复后重跑仍然失败 (agent_id=%s, thread_id=%s)",
                                agent_id,
                                thread_id,
                            )
                            await self.emit(rec, {"type": "error", "message": str(exc2)})
                            end_reason = "error"
                        else:
                            await finish(result, allow_backfill=True)
                            end_reason = "stopped" if result.drained else "done"
                    else:
                        await self.emit(rec, {"type": "error", "message": str(exc)})
                        end_reason = "error"
        except asyncio.CancelledError:
            end_reason = "error"
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("会话运行异常 (agent_id=%s, thread_id=%s)", agent_id, thread_id)
            await self.emit(rec, {"type": "error", "message": str(exc)})
            end_reason = "error"
        finally:
            await self._complete(rec, run_id, end_reason)

    async def _execute_edit(
        self, rec, runtime, user_input, emit_checked, on_interrupt, control
    ) -> str:
        agent_id, thread_id = rec.key
        try:
            result = await runtime.run(
                thread_id, user_input, emit_checked, on_interrupt, control=control
            )
            if result.drained:
                await runtime.repair_tool_results(thread_id)
            await self._mark_completed(
                rec, rec.run_id, "stopped" if result.drained else "done"
            )
            await self.emit(
                rec,
                {
                    "type": "done",
                    "final_state": result.text,
                    "stopped": result.drained,
                    "backfill": rec.backfill,
                },
            )
            return "stopped" if result.drained else "done"
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "编辑历史消息失败 (agent_id=%s, thread_id=%s)", agent_id, thread_id
            )
            await self.emit(rec, {"type": "error", "message": _format_edit_error(exc)})
            return "error"

    async def _mark_completed(
        self, rec: RunRecord, run_id: str, end_reason: str
    ) -> None:
        """在 done 之前先把状态置 completed（不广播），避免 accept 竞态。"""
        async with self._lock:
            if rec.run_id != run_id or rec.status == "idle":
                return
            rec.status = "completed"
            rec.end_reason = end_reason
            rec.finished_at = _now()

    async def _complete(
        self, rec: RunRecord, run_id: str, end_reason: str, *, error: str = ""
    ) -> None:
        # 不在这里重新渲染最终历史：completed 的重新进入由前端拉 /history，
        # 已在视图里的 DOM 本来就是最终内容。记录里保留的 history_markdown 是
        # 本轮开始前的基线，completed 后必须清掉，避免把旧基线当成最终历史。
        async with self._lock:
            if rec.run_id != run_id:
                return
            if rec.status == "idle":
                return
            rec.status = "completed"
            rec.end_reason = end_reason
            rec.finished_at = _now()
            rec.task = None
            rec.control = None
            rec.started_event = None
            rec.error = error
            rec.history_markdown = ""
            self._publish_locked(rec, self._run_state(rec))

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    async def shutdown(self) -> None:
        async with self._lock:
            tasks = [rec.task for rec in self._records.values() if rec.task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
