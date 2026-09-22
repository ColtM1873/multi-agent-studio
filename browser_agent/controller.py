"""Process-wide browser session state (hidden state behind the tool API)."""

from __future__ import annotations

import contextlib
import threading
import time
from typing import Optional

from .actions import ActionError, ActionExecutor
from .cdp import CDPClient, CDPError, poll_until_quiet
from .dom import (
    DOMSerializer,
    EnhancedTree,
    NameRegistry,
    build_enhanced_tree,
    capture_raw,
    classify,
    compute_diff,
)
from .launcher import BrowserLauncher, BrowserLaunchError
from .tab_registry import TabRegistry

FULL = "全量模式"
INCREMENTAL = "增量模式"

OK = "成功"
FAIL = "失败"
PARTIAL_FAIL = "部分失败"
NOT_CALLED = "未进行互动元素调用"

# After an action that may navigate (a click), give the browser this long to
# *start* a navigation before falling back to the DOM-quiet heuristic. This
# closes the "pre-commit gap": right after a click the old document is still
# fully loaded and looks perfectly quiet, so without this grace window the
# stabilizer returns before the navigation even begins and the caller sees a
# diff against the stale page.
NAV_GRACE_SECONDS = 4.0
# Once a hard navigation has started, wait this long for the load event.
NAV_LOAD_TIMEOUT = 15.0


class BrowserController:
    _instance: Optional["BrowserController"] = None

    def __init__(self) -> None:
        self.launcher = BrowserLauncher()
        self.client: Optional[CDPClient] = None
        self.focused_target_id: Optional[str] = None
        self._registries: dict[str, NameRegistry] = {}
        self._prev_trees: dict[str, EnhancedTree] = {}
        self._tab_registry = TabRegistry()
        self._last_error: str = ""

    @classmethod
    def instance(cls) -> "BrowserController":
        if cls._instance is None:
            cls._instance = BrowserController()
        return cls._instance

    # ------------------------------------------------------------------ #
    # connection
    # ------------------------------------------------------------------ #
    def ensure_connected(self) -> None:
        if self.client is not None:
            try:
                self.client.refresh_targets()
                return
            except Exception:
                try:
                    self.client.close()
                except Exception:
                    pass
                self.client = None

        info = self.launcher.ensure_browser()
        self.client = CDPClient(info["ws_url"])
        try:
            self.client.send("Target.setDiscoverTargets", {"discover": True})
        except CDPError:
            pass
        self.client.refresh_targets()

    # ------------------------------------------------------------------ #
    # tabs
    # ------------------------------------------------------------------ #
    def _page_targets(self) -> list[dict]:
        if self.client is None:
            return []
        return self.client.page_targets()

    def _live_targets(self) -> list[dict]:
        """Page targets, with the tab registry reconciled against them."""
        targets = self._page_targets()
        self._tab_registry.reconcile(targets)
        return targets

    def _name_for_target(self, target_id: str) -> str:
        for target in self._live_targets():
            if target["targetId"] == target_id:
                return self._tab_registry.name(target)
        return ""

    def tab_labels(self) -> list[str]:
        return self._tab_registry.names(self._live_targets())

    def tab_url_map(self) -> dict:
        try:
            self.ensure_connected()
        except (BrowserLaunchError, CDPError):
            return {}
        return self._tab_registry.url_map(self._live_targets())

    def focused_tab_label(self) -> str:
        target_id = self._resolve_focused_target()
        if not target_id:
            return ""
        return self._name_for_target(target_id)

    def _title_change_notice(self, target_id: str, old_name: str) -> str:
        if not old_name:
            return ""
        new_name = self._name_for_target(target_id)
        if new_name and new_name != old_name:
            return f"[标签页标题变更] {old_name} → {new_name}\n"
        return ""

    def _resolve_focused_target(self) -> Optional[str]:
        targets = self._page_targets()
        if not targets:
            return None
        assert self.client is not None
        for target in targets:
            target_id = target["targetId"]
            try:
                session = self.client.attach(target_id)
                self.client.enable_page_domains(session)
                result = self.client.send(
                    "Runtime.evaluate",
                    {"expression": "document.hasFocus()", "returnByValue": True},
                    session_id=session,
                    timeout=3.0,
                )
                if (result.get("result") or {}).get("value") is True:
                    self.focused_target_id = target_id
                    return target_id
            except Exception:
                continue
        if self.focused_target_id and any(
            t["targetId"] == self.focused_target_id for t in targets
        ):
            return self.focused_target_id
        self.focused_target_id = targets[0]["targetId"]
        return self.focused_target_id

    # ------------------------------------------------------------------ #
    # DOM
    # ------------------------------------------------------------------ #
    def _registry_for(self, target_id: str) -> NameRegistry:
        if target_id not in self._registries:
            self._registries[target_id] = NameRegistry()
        return self._registries[target_id]

    def capture_tree(self, target_id: Optional[str] = None) -> EnhancedTree:
        self.ensure_connected()
        assert self.client is not None
        if target_id is None:
            target_id = self._resolve_focused_target()
        if target_id is None:
            raise RuntimeError("没有可用的标签页")
        session = self.client.attach(target_id)
        self.client.enable_page_domains(session)
        raw = capture_raw(self.client, session)
        tree = build_enhanced_tree(raw)
        registry = self._registry_for(target_id)
        registry.reconcile(tree)
        self._prev_trees[target_id] = tree
        self.focused_target_id = target_id
        return tree

    def serialize_tree(self, tree: EnhancedTree, target_id: str) -> str:
        registry = self._registry_for(target_id)
        return DOMSerializer(registry).serialize(tree)

    # ------------------------------------------------------------------ #
    # interaction (tool-2)
    # ------------------------------------------------------------------ #
    def interact(self, name: str, fill: str = "", drag_pct: int = 0) -> dict:
        try:
            self.ensure_connected()
        except (BrowserLaunchError, CDPError) as exc:
            return self._base_result(INCREMENTAL, FAIL, "无", str(exc), include_tabs=False)

        assert self.client is not None
        target_id = self._resolve_focused_target()
        if target_id is None:
            return self._base_result(INCREMENTAL, FAIL, "无", "没有可用的标签页", include_tabs=False)

        registry = self._registry_for(target_id)
        if registry.lookup(name) is None:
            return self._base_result(
                INCREMENTAL, FAIL, "无", f"未知的互动元素名称 {name}", include_tabs=False
            )
        if not registry.is_active(name):
            return self._base_result(
                INCREMENTAL, FAIL, "无", "该互动元素已经不存在于viewport中了", include_tabs=False
            )

        old_tree = self._prev_trees.get(target_id) or self.capture_tree(target_id)
        key = registry.lookup(name)
        node = next((n for n in old_tree.nodes if n.key == key), None)
        if node is None:
            return self._base_result(
                INCREMENTAL, FAIL, "无", "该互动元素已经不存在于viewport中了", include_tabs=False
            )

        category = classify(node)
        old_text = self.serialize_tree(old_tree, target_id)
        old_focus = self.focused_target_id
        old_name = self._name_for_target(target_id)

        try:
            session = self.client.attach(target_id)
            self.client.enable_page_domains(session)
            executor = ActionExecutor(self.client, session)
            if category == "input":
                executor.input_text(node.backend_node_id, fill)
                self._stabilize(target_id)
            elif category == "drag":
                executor.drag(node.backend_node_id, drag_pct)
                self._stabilize(target_id)
            elif category == "scroll":
                diff = self._scroll_and_collect(
                    executor, node.backend_node_id, target_id, old_text
                )
                notice = self._title_change_notice(target_id, old_name)
                return self._base_result(INCREMENTAL, OK, notice + diff, include_tabs=False)
            else:
                # A click may trigger a navigation; wait for it to actually
                # begin/finish instead of trusting the stale DOM's quietness.
                with self._watch_navigation(session) as nav_state:
                    executor.click(node.backend_node_id)
                    self._settle_navigation(session, nav_state)
        except ActionError as exc:
            return self._base_result(INCREMENTAL, FAIL, "无", str(exc), include_tabs=False)
        except CDPError as exc:
            return self._base_result(INCREMENTAL, FAIL, "无", str(exc), include_tabs=False)

        new_focus = self._resolve_focused_target()
        if new_focus and new_focus != old_focus:
            new_tree = self.capture_tree(new_focus)
            new_text = self.serialize_tree(new_tree, new_focus)
            return self._base_result(FULL, OK, new_text, include_tabs=True)

        new_tree = self.capture_tree(target_id)
        new_text = self.serialize_tree(new_tree, target_id)
        diff = compute_diff(old_text, new_text)
        notice = self._title_change_notice(target_id, old_name)
        return self._base_result(INCREMENTAL, OK, notice + diff, include_tabs=False)

    def _scroll_and_collect(
        self,
        executor: ActionExecutor,
        backend_node_id: int,
        target_id: str,
        base_text: str,
        max_steps: int = 6,
    ) -> str:
        """Scroll a container in overlap-preserving steps, accumulating content.

        Each step is smaller than the container's visible height, so consecutive
        viewports always overlap. Newly revealed lines are accumulated across
        every step, so the returned diff is contiguous and never skips content
        between the pre-scroll and post-scroll snapshots.
        """
        client_h = executor.client_height(backend_node_id)
        if client_h and client_h > 0:
            # Always smaller than the visible height so consecutive viewports
            # overlap; this is what guarantees no content is skipped.
            step = max(1.0, float(client_h) * 0.7)
        else:
            step = 120.0

        added: list[str] = []
        seen: set[str] = set()
        prev_text = base_text
        last_top = executor.scroll_top(backend_node_id)

        for _ in range(max_steps):
            top = executor.scroll_step(backend_node_id, step)
            if top is None:
                break
            if last_top is not None and abs(top - last_top) < 1:
                break
            last_top = top

            tree = self.capture_tree(target_id)
            text = self.serialize_tree(tree, target_id)
            for line in compute_diff(prev_text, text).splitlines():
                if line.startswith(("+ ", "~ ")):
                    payload = line[2:]
                    if payload not in seen:
                        seen.add(payload)
                        added.append(line)
            prev_text = text

            if executor.at_bottom(backend_node_id):
                break

        removed = [
            line
            for line in compute_diff(base_text, prev_text).splitlines()
            if line.startswith("- ")
        ]
        out = removed + added
        if not out:
            return "（页面无变化）"
        result = "\n".join(out)
        if len(result) > 40000:
            result = result[:40000] + "\n…（diff 已截断）"
        return result

    def _resolve_interactive_node(
        self, registry: NameRegistry, tree: EnhancedTree, name: str
    ) -> tuple[Optional[object], str]:
        """Return (node, "") or (None, error_message) for an interactive name."""
        if registry.lookup(name) is None:
            return None, f"未知的互动元素名称 {name}"
        if not registry.is_active(name):
            return None, "该互动元素已经不存在于viewport中了"
        key = registry.lookup(name)
        node = next((n for n in tree.nodes if n.key == key), None)
        if node is None:
            return None, "该互动元素已经不存在于viewport中了"
        return node, ""

    # ------------------------------------------------------------------ #
    # batch interaction (tool-11)
    # ------------------------------------------------------------------ #
    def interact_many(self, name_list: list, fill_list: list) -> dict:
        """A sequence of fills plus an optional trailing click, run serially.

        ``name_list`` must be the same length as ``fill_list`` (all fills), or
        exactly one longer (the extra trailing name is a clickable element used
        as the closing action). Everything is validated up-front; if validation
        fails nothing is executed. If an action fails mid-way, the remaining
        actions are abandoned and ``action_ok`` becomes ``部分失败`` (or ``失败``
        when the very first action fails).
        """
        names = [str(n) for n in (name_list or [])]
        fills = [str(f) for f in (fill_list or [])]
        n_names = len(names)
        n_fills = len(fills)

        if n_names == 0:
            return self._base_result(
                INCREMENTAL, FAIL, "无", "name_list 不能为空", include_tabs=False
            )
        if n_fills not in (n_names, n_names - 1):
            return self._base_result(
                INCREMENTAL,
                FAIL,
                "无",
                "name_list 与 fill_list 长度不合法（应等长，或 name_list 比 fill_list 多一个）",
                include_tabs=False,
            )

        has_click = n_names == n_fills + 1
        fill_names = names[:n_fills]
        click_name = names[-1] if has_click else None

        try:
            self.ensure_connected()
        except (BrowserLaunchError, CDPError) as exc:
            return self._base_result(INCREMENTAL, FAIL, "无", str(exc), include_tabs=False)

        assert self.client is not None
        target_id = self._resolve_focused_target()
        if target_id is None:
            return self._base_result(
                INCREMENTAL, FAIL, "无", "没有可用的标签页", include_tabs=False
            )

        registry = self._registry_for(target_id)
        tree = self._prev_trees.get(target_id) or self.capture_tree(target_id)

        # ---- up-front validation (nothing runs if this fails) ----
        plan: list[tuple[str, str, int]] = []  # (name, category, backendNodeId)
        for name in fill_names:
            node, err = self._resolve_interactive_node(registry, tree, name)
            if err:
                return self._base_result(INCREMENTAL, FAIL, "无", err, include_tabs=False)
            if classify(node) != "input":
                return self._base_result(
                    INCREMENTAL,
                    FAIL,
                    "无",
                    f"互动元素 {name} 不是可填入元素",
                    include_tabs=False,
                )
            plan.append((name, "input", node.backend_node_id))
        if has_click:
            node, err = self._resolve_interactive_node(registry, tree, click_name)
            if err:
                return self._base_result(INCREMENTAL, FAIL, "无", err, include_tabs=False)
            if classify(node) != "click":
                return self._base_result(
                    INCREMENTAL,
                    FAIL,
                    "无",
                    f"互动元素 {click_name} 不是可点击元素",
                    include_tabs=False,
                )
            plan.append((click_name, "click", node.backend_node_id))

        # ---- serial execution ----
        old_text = self.serialize_tree(tree, target_id)
        old_focus = self.focused_target_id
        old_name = self._name_for_target(target_id)

        try:
            session = self.client.attach(target_id)
            self.client.enable_page_domains(session)
        except CDPError as exc:
            return self._base_result(INCREMENTAL, FAIL, "无", str(exc), include_tabs=False)

        executor = ActionExecutor(self.client, session)
        success_count = 0
        error_msg = ""

        def _run_plan() -> None:
            nonlocal success_count, error_msg
            for index, (_name, category, backend_node_id) in enumerate(plan):
                try:
                    if category == "input":
                        executor.input_text(backend_node_id, fills[index])
                    else:
                        executor.click(backend_node_id)
                except (ActionError, CDPError) as exc:
                    error_msg = str(exc)
                    break
                success_count += 1

        if has_click:
            # The trailing click may navigate; watch for it and wait properly.
            with self._watch_navigation(session) as nav_state:
                _run_plan()
                if error_msg and success_count == 0:
                    return self._base_result(
                        INCREMENTAL, FAIL, "无", error_msg, include_tabs=False
                    )
                self._settle_navigation(session, nav_state)
        else:
            _run_plan()
            if error_msg and success_count == 0:
                return self._base_result(
                    INCREMENTAL, FAIL, "无", error_msg, include_tabs=False
                )
            self._stabilize(target_id)

        new_focus = self._resolve_focused_target()
        focus_changed = bool(new_focus and new_focus != old_focus)

        if focus_changed:
            new_tree = self.capture_tree(new_focus)
            content = self.serialize_tree(new_tree, new_focus)
            mode = FULL
        else:
            new_tree = self.capture_tree(target_id)
            new_text = self.serialize_tree(new_tree, target_id)
            notice = self._title_change_notice(target_id, old_name)
            content = notice + compute_diff(old_text, new_text)
            mode = INCREMENTAL

        if error_msg:
            action_ok = PARTIAL_FAIL if success_count > 0 else FAIL
            return self._base_result(
                mode, action_ok, content, error=error_msg, include_tabs=focus_changed
            )
        return self._base_result(mode, OK, content, include_tabs=focus_changed)

    def _quiet(
        self, session: str, quiet_seconds: float = 0.7, timeout: float = 6.0
    ) -> None:
        """Block until the session's DOM fingerprint stops changing."""
        assert self.client is not None

        def probe() -> Optional[str]:
            try:
                result = self.client.send(
                    "Runtime.evaluate",
                    {
                        "expression": "document.readyState + '|' + "
                        "document.documentElement.outerHTML.length",
                        "returnByValue": True,
                    },
                    session_id=session,
                    timeout=3.0,
                )
                return str((result.get("result") or {}).get("value"))
            except Exception:
                return None

        poll_until_quiet(probe, quiet_seconds=quiet_seconds, timeout=timeout)

    def _stabilize(self, target_id: str) -> None:
        assert self.client is not None
        try:
            session = self.client.attach(target_id)
        except CDPError:
            return
        self._quiet(session)

    @contextlib.contextmanager
    def _watch_navigation(self, session: str):
        """Watch CDP navigation events for ``session`` while an action runs.

        Yields a state dict with ``hard`` (real document navigation),
        ``soft`` (same-document / SPA route change) and ``load`` events.
        """
        assert self.client is not None
        state = {
            "hard": threading.Event(),
            "soft": threading.Event(),
            "load": threading.Event(),
        }

        # Only the main frame matters: sub-frame loads (iframes/ads) must not
        # be mistaken for a real navigation.
        try:
            frame_tree = self.client.send(
                "Page.getFrameTree", {}, session_id=session, timeout=3.0
            )
            main_frame_id = (
                (frame_tree.get("frameTree") or {}).get("frame") or {}
            ).get("id")
        except Exception:
            main_frame_id = None

        def _is_main_frame(params: dict) -> bool:
            if main_frame_id is None:
                return True
            return (
                params.get("frameId") == main_frame_id
                or (params.get("frame") or {}).get("id") == main_frame_id
            )

        def _handler(event: threading.Event, require_main: bool):
            def handle(params: dict) -> None:
                if params.get("__sessionId") != session:
                    return
                if require_main and not _is_main_frame(params):
                    return
                event.set()

            return handle

        watchers = [
            ("Page.frameStartedLoading", _handler(state["hard"], True)),
            ("Page.frameNavigated", _handler(state["hard"], True)),
            ("Page.navigatedWithinDocument", _handler(state["soft"], True)),
            ("Page.loadEventFired", _handler(state["load"], False)),
        ]
        for method, handler in watchers:
            self.client.on(method, handler)
        try:
            yield state
        finally:
            for method, handler in watchers:
                self.client.off(method, handler)

    def _settle_navigation(self, session: str, state: dict) -> None:
        """Settle the page after an action, tolerating an induced navigation.

        Waits ``NAV_GRACE_SECONDS`` for a navigation to begin; if a real
        navigation starts, waits for its load event first. Then always waits
        for the DOM to go quiet (covers same-document route changes and async
        rendering as well).
        """
        deadline = time.time() + NAV_GRACE_SECONDS
        while time.time() < deadline:
            if state["hard"].is_set() or state["soft"].is_set() or state["load"].is_set():
                break
            time.sleep(0.05)

        if state["hard"].is_set():
            state["load"].wait(NAV_LOAD_TIMEOUT)
        self._quiet(session)

    # ------------------------------------------------------------------ #
    # result dicts
    # ------------------------------------------------------------------ #
    def _base_result(
        self,
        mode: str,
        action_ok: str,
        content: str,
        error: str = "无",
        include_tabs: bool = True,
    ) -> dict:
        result = {
            "focused_tab": self.focused_tab_label(),
            "mode": mode,
            "action_ok": action_ok,
            "error": error,
            "content": content,
        }
        if include_tabs:
            result = {"tabs": self.tab_labels(), **result}
        return result

    def full_dom(self, action_ok: str = NOT_CALLED) -> dict:
        try:
            self.ensure_connected()
            target_id = self._resolve_focused_target()
            if target_id is None:
                return self._base_result(FULL, FAIL, "无", "没有可用的标签页")
            tree = self.capture_tree(target_id)
            content = self.serialize_tree(tree, target_id)
            return self._base_result(FULL, action_ok, content)
        except (BrowserLaunchError, CDPError, RuntimeError) as exc:
            return self._base_result(FULL, FAIL, "无", str(exc))

    def list_tabs(self) -> dict:
        try:
            self.ensure_connected()
            return {"tabs": self.tab_labels()}
        except (BrowserLaunchError, CDPError) as exc:
            return {"tabs": [], "error": str(exc)}

    # ------------------------------------------------------------------ #
    # navigation / tab tools (P2: tool-5 .. tool-9)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _parse_tab_id(raw: str) -> str:
        text = (raw or "").strip()
        if text.startswith("Tab "):
            text = text[4:]
        if ":" in text:
            text = text.split(":", 1)[0]
        return text.strip()

    def _resolve_tab(self, raw: str) -> Optional[str]:
        """Accept a ``<title><NNN>`` tab name, a raw targetId, or the legacy
        ``Tab <id>: url - title`` row."""
        targets = self._live_targets()
        target_id = self._tab_registry.target_for_name(raw, targets)
        if target_id:
            return target_id
        legacy = self._parse_tab_id(raw)
        if legacy in {t["targetId"] for t in targets}:
            return legacy
        return None

    def _wait_ready(self, target_id: str, timeout: float = 15.0) -> None:
        assert self.client is not None
        session = self.client.attach(target_id)
        self.client.enable_page_domains(session)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                result = self.client.send(
                    "Runtime.evaluate",
                    {"expression": "document.readyState", "returnByValue": True},
                    session_id=session,
                    timeout=3.0,
                )
                if (result.get("result") or {}).get("value") == "complete":
                    break
            except Exception:
                pass
            time.sleep(0.15)
        self._stabilize(target_id)

    def _wait_ready_new_tab(
        self, target_id: str, expected_url: str = "", timeout: float = NAV_LOAD_TIMEOUT
    ) -> None:
        """Wait for a freshly created tab to finish its *initial* navigation.

        A brand-new target starts on ``about:blank`` which already reports
        ``readyState == "complete"``; ``_wait_ready`` would therefore return
        immediately and capture an empty page. Here we refuse to accept the
        page until the real document has committed (``location.href`` has left
        ``about:blank``) and is complete, then wait for the DOM to go quiet.
        """
        assert self.client is not None
        session = self.client.attach(target_id)
        self.client.enable_page_domains(session)
        allow_blank = bool(expected_url) and expected_url.startswith("about:")
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                result = self.client.send(
                    "Runtime.evaluate",
                    {
                        "expression": "document.readyState + '|' + location.href",
                        "returnByValue": True,
                    },
                    session_id=session,
                    timeout=3.0,
                )
                value = str((result.get("result") or {}).get("value") or "")
            except Exception:
                value = ""
            ready_state, _, href = value.partition("|")
            committed = allow_blank or (bool(href) and href != "about:blank")
            if committed and ready_state == "complete":
                break
            time.sleep(0.1)
        self._quiet(session)

    def _full_result_for(self, target_id: str, action_ok: str = NOT_CALLED) -> dict:
        tree = self.capture_tree(target_id)
        content = self.serialize_tree(tree, target_id)
        return self._base_result(FULL, action_ok, content)

    def switch_tab(self, tab_id: str) -> dict:
        """tool-5: focus an existing tab and return its full DOM."""
        try:
            self.ensure_connected()
        except (BrowserLaunchError, CDPError) as exc:
            return self._base_result(FULL, FAIL, "无", str(exc))
        assert self.client is not None
        target_id = self._resolve_tab(tab_id)
        if target_id is None:
            return self._base_result(FULL, FAIL, "无", f"未找到标签页 {tab_id}")
        try:
            self.client.send("Target.activateTarget", {"targetId": target_id})
            self.focused_target_id = target_id
            self._wait_ready(target_id)
        except (CDPError, RuntimeError) as exc:
            return self._base_result(FULL, FAIL, "无", str(exc))
        return self._full_result_for(target_id)

    def go_back(self) -> dict:
        """tool-6: browser back button, return full DOM."""
        try:
            self.ensure_connected()
        except (BrowserLaunchError, CDPError) as exc:
            return self._base_result(FULL, FAIL, "无", str(exc))
        assert self.client is not None
        target_id = self._resolve_focused_target()
        if target_id is None:
            return self._base_result(FULL, FAIL, "无", "没有可用的标签页")
        old_name = self._name_for_target(target_id)
        session = self.client.attach(target_id)
        self.client.enable_page_domains(session)
        try:
            history = self.client.send("Page.getNavigationHistory", {}, session_id=session)
        except CDPError as exc:
            return self._base_result(FULL, FAIL, "无", str(exc))
        index = history.get("currentIndex", 0)
        entries = history.get("entries", [])
        if index <= 0 or index >= len(entries):
            return self._base_result(FULL, FAIL, "无", "无法返回：没有可回退的历史记录")
        try:
            with self._watch_navigation(session) as nav_state:
                self.client.send(
                    "Page.navigateToHistoryEntry",
                    {"entryId": entries[index - 1]["id"]},
                    session_id=session,
                )
                self._settle_navigation(session, nav_state)
        except CDPError as exc:
            return self._base_result(FULL, FAIL, "无", str(exc))
        result = self._full_result_for(target_id)
        result["content"] = self._title_change_notice(target_id, old_name) + result["content"]
        return result

    def refresh(self) -> dict:
        """tool-7: reload current tab, return incremental diff."""
        try:
            self.ensure_connected()
        except (BrowserLaunchError, CDPError) as exc:
            return self._base_result(INCREMENTAL, FAIL, "无", str(exc), include_tabs=False)
        assert self.client is not None
        target_id = self._resolve_focused_target()
        if target_id is None:
            return self._base_result(INCREMENTAL, FAIL, "无", "没有可用的标签页", include_tabs=False)
        old_tree = self._prev_trees.get(target_id) or self.capture_tree(target_id)
        old_text = self.serialize_tree(old_tree, target_id)
        old_name = self._name_for_target(target_id)
        session = self.client.attach(target_id)
        self.client.enable_page_domains(session)
        try:
            with self._watch_navigation(session) as nav_state:
                self.client.send("Page.reload", {"ignoreCache": False}, session_id=session)
                self._settle_navigation(session, nav_state)
        except CDPError as exc:
            return self._base_result(INCREMENTAL, FAIL, "无", str(exc), include_tabs=False)
        new_tree = self.capture_tree(target_id)
        new_text = self.serialize_tree(new_tree, target_id)
        diff = compute_diff(old_text, new_text)
        notice = self._title_change_notice(target_id, old_name)
        return self._base_result(INCREMENTAL, OK, notice + diff, include_tabs=False)

    def close_tab(self, tab_id: str) -> dict:
        """tool-8: close a tab, return the remaining tab list."""
        try:
            self.ensure_connected()
        except (BrowserLaunchError, CDPError) as exc:
            return {"tabs": [], "error": str(exc)}
        assert self.client is not None
        target_id = self._resolve_tab(tab_id)
        if target_id is None:
            return {"tabs": self.tab_labels(), "error": f"未找到标签页 {tab_id}"}
        try:
            self.client.send("Target.closeTarget", {"targetId": target_id})
        except CDPError as exc:
            return {"tabs": self.tab_labels(), "error": str(exc)}
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if target_id not in {t["targetId"] for t in self._page_targets()}:
                break
            time.sleep(0.1)
        self._registries.pop(target_id, None)
        self._prev_trees.pop(target_id, None)
        self._tab_registry.forget(target_id)
        if self.focused_target_id == target_id:
            self.focused_target_id = None
        return {"tabs": self.tab_labels()}

    def navigate(self, url: str) -> dict:
        """tool-9: open a new tab at the given URL, return its full DOM."""
        try:
            self.ensure_connected()
        except (BrowserLaunchError, CDPError) as exc:
            return self._base_result(FULL, FAIL, "无", str(exc))
        assert self.client is not None
        target_url = (url or "").strip()
        if not target_url:
            return self._base_result(FULL, FAIL, "无", "地址为空")
        if not target_url.startswith(
            ("http://", "https://", "file://", "about:", "data:", "chrome://", "view-source:")
        ):
            target_url = "https://" + target_url
        try:
            result = self.client.send(
                "Target.createTarget", {"url": target_url, "background": False}
            )
            target_id = result["targetId"]
        except CDPError as exc:
            return self._base_result(FULL, FAIL, "无", str(exc))
        self.focused_target_id = target_id
        try:
            self.client.send("Target.activateTarget", {"targetId": target_id})
        except CDPError:
            pass
        self._wait_ready_new_tab(target_id, target_url)
        return self._full_result_for(target_id)
