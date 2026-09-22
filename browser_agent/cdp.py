"""A deliberately small Chrome DevTools Protocol client.

Only what this project needs: JSON-RPC command/response over a single
WebSocket, flat session routing (``Target.attachToTarget(flatten=True)``),
target bookkeeping and a lightweight event dispatch.
"""

from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from typing import Any, Callable, Optional

import websocket  # websocket-client


class CDPError(RuntimeError):
    pass


class CDPClient:
    def __init__(self, ws_url: str, command_timeout: float = 30.0) -> None:
        self.ws_url = ws_url
        self.command_timeout = command_timeout

        self._ws = websocket.create_connection(
            ws_url,
            timeout=command_timeout,
            enable_multithread=True,
            suppress_origin=True,
        )

        self._send_lock = threading.Lock()
        self._id_lock = threading.Lock()
        self._next_id = 0
        self._pending: dict[int, dict] = {}
        self._pending_lock = threading.Lock()

        self._event_handlers: dict[str, list[Callable[[dict], None]]] = defaultdict(list)
        self._session_targets: dict[str, str] = {}
        self._targets: dict[str, dict] = {}

        self._closed = False
        self._reader = threading.Thread(target=self._read_loop, name="cdp-reader", daemon=True)
        self._reader.start()

        self._install_default_handlers()

    # ------------------------------------------------------------------ #
    # core command API
    # ------------------------------------------------------------------ #
    def send(
        self,
        method: str,
        params: Optional[dict] = None,
        session_id: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> dict:
        with self._id_lock:
            self._next_id += 1
            msg_id = self._next_id

        message: dict[str, Any] = {"id": msg_id, "method": method, "params": params or {}}
        if session_id:
            message["sessionId"] = session_id

        slot = {"event": threading.Event(), "result": None, "error": None}
        with self._pending_lock:
            self._pending[msg_id] = slot

        with self._send_lock:
            self._ws.send(json.dumps(message))

        wait_for = timeout if timeout is not None else self.command_timeout
        if not slot["event"].wait(wait_for):
            with self._pending_lock:
                self._pending.pop(msg_id, None)
            raise CDPError(f"CDP 命令超时: {method}")

        if slot["error"] is not None:
            raise CDPError(f"CDP 命令失败 {method}: {slot['error']}")
        return slot["result"] or {}

    def on(self, method: str, callback: Callable[[dict], None]) -> None:
        self._event_handlers[method].append(callback)

    def off(self, method: str, callback: Callable[[dict], None]) -> None:
        handlers = self._event_handlers.get(method)
        if not handlers:
            return
        try:
            handlers.remove(callback)
        except ValueError:
            pass

    def close(self) -> None:
        self._closed = True
        try:
            self._ws.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # targets / sessions
    # ------------------------------------------------------------------ #
    def refresh_targets(self) -> dict[str, dict]:
        result = self.send("Target.getTargets")
        for info in result.get("targetInfos", []):
            self._targets[info["targetId"]] = info
        return dict(self._targets)

    def page_targets(self) -> list[dict]:
        self.refresh_targets()
        return [
            t
            for t in self._targets.values()
            if t.get("type") == "page" and not t.get("url", "").startswith("devtools://")
        ]

    def attach(self, target_id: str) -> str:
        if target_id in self._session_targets.values():
            for session_id, tid in self._session_targets.items():
                if tid == target_id:
                    return session_id
        result = self.send("Target.attachToTarget", {"targetId": target_id, "flatten": True})
        session_id = result["sessionId"]
        self._session_targets[session_id] = target_id
        return session_id

    def session_for_target(self, target_id: str) -> Optional[str]:
        for session_id, tid in self._session_targets.items():
            if tid == target_id:
                return session_id
        return None

    def target_for_session(self, session_id: str) -> Optional[str]:
        return self._session_targets.get(session_id)

    def enable_page_domains(self, session_id: str) -> None:
        for domain in ("Page.enable", "Runtime.enable", "DOM.enable", "Network.enable"):
            try:
                self.send(domain, {}, session_id=session_id)
            except CDPError:
                pass

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #
    def _install_default_handlers(self) -> None:
        self.on("Target.targetCreated", self._on_target_created)
        self.on("Target.targetInfoChanged", self._on_target_info_changed)
        self.on("Target.targetDestroyed", self._on_target_destroyed)
        self.on("Target.detachedFromTarget", self._on_detached)

    def _on_target_created(self, params: dict) -> None:
        info = params.get("targetInfo") or {}
        if info.get("targetId"):
            self._targets[info["targetId"]] = info

    def _on_target_info_changed(self, params: dict) -> None:
        info = params.get("targetInfo") or {}
        if info.get("targetId"):
            self._targets[info["targetId"]] = info

    def _on_target_destroyed(self, params: dict) -> None:
        target_id = params.get("targetId")
        if target_id:
            self._targets.pop(target_id, None)
            for session_id in [s for s, t in self._session_targets.items() if t == target_id]:
                self._session_targets.pop(session_id, None)

    def _on_detached(self, params: dict) -> None:
        session_id = params.get("sessionId")
        if session_id:
            self._session_targets.pop(session_id, None)

    def _read_loop(self) -> None:
        while not self._closed:
            try:
                raw = self._ws.recv()
            except Exception:
                if not self._closed:
                    self._closed = True
                break
            if not raw:
                continue
            try:
                message = json.loads(raw)
            except (ValueError, TypeError):
                continue

            if "id" in message:
                self._resolve_pending(message)
            elif "method" in message:
                self._dispatch_event(message)

        # unblock any waiters
        with self._pending_lock:
            for slot in self._pending.values():
                slot["error"] = slot["error"] or "连接已关闭"
                slot["event"].set()
            self._pending.clear()

    def _resolve_pending(self, message: dict) -> None:
        msg_id = message.get("id")
        with self._pending_lock:
            slot = self._pending.pop(msg_id, None)
        if slot is None:
            return
        if "error" in message:
            slot["error"] = message["error"]
        else:
            slot["result"] = message.get("result", {})
        slot["event"].set()

    def _dispatch_event(self, message: dict) -> None:
        method = message.get("method", "")
        params = message.get("params", {})
        if "sessionId" in message:
            params = dict(params)
            params["__sessionId"] = message["sessionId"]
        for handler in list(self._event_handlers.get(method, [])):
            try:
                handler(params)
            except Exception:
                pass


def wait_for_event(
    client: CDPClient, method: str, timeout: float, session_id: Optional[str] = None
) -> Optional[dict]:
    """Block until ``method`` fires (optionally for a given session)."""
    box: dict[str, Any] = {"event": None}
    done = threading.Event()

    def handler(params: dict) -> None:
        if session_id and params.get("__sessionId") != session_id:
            return
        box["event"] = params
        done.set()

    client.on(method, handler)
    done.wait(timeout)
    return box["event"]


def poll_until_quiet(
    probe: Callable[[], str],
    quiet_seconds: float = 0.4,
    timeout: float = 8.0,
    interval: float = 0.15,
) -> bool:
    """Return True once ``probe`` yields the same value for ``quiet_seconds``."""
    deadline = time.time() + timeout
    last_value = None
    stable_since = None
    while time.time() < deadline:
        try:
            value = probe()
        except Exception:
            value = None
        if value is not None and value == last_value:
            if stable_since is None:
                stable_since = time.time()
            elif time.time() - stable_since >= quiet_seconds:
                return True
        else:
            last_value = value
            stable_since = None
        time.sleep(interval)
    return False
