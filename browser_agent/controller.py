"""Process-wide browser session state (hidden state behind the tool API)."""

from __future__ import annotations

import contextlib
import threading
import time
from typing import Optional

from . import debug, timing
from .actions import ActionError, ActionExecutor
from .cdp import CDPClient, CDPError, poll_until_quiet
from .dom import (
    PAGE_SCROLL_TAG,
    DOMSerializer,
    EnhancedNode,
    EnhancedTree,
    NameCounter,
    NameRegistry,
    OutLine,
    build_enhanced_tree,
    capture_raw,
    changed_lines,
    classify,
    compute_diff,
    compute_lost,
    format_lines,
    format_lost,
    is_control_icon,
    is_cursor_pointer_only,
    may_navigate,
    read_outer_html,
)
from .launcher import BrowserLauncher, BrowserLaunchError
from .tab_registry import TabRegistry

FULL = "全量模式"
INCREMENTAL = "增量模式"

OK = "成功"
FAIL = "失败"
PARTIAL_FAIL = "部分失败"
NOT_CALLED = "未进行互动元素调用"

# Prepended to a full-DOM result when the document is still a bare skeleton
# (``<html><head>…</head></html>``, no rendered body). Without it the LLM sees a
# normal-looking page with "可互动元素 0 个" and wastes calls guessing.
_BLANK_SHELL_NOTICE = (
    "[页面可能仍在加载] 当前页面几乎为空（尚未渲染出正文）。"
    "请稍等片刻后重试 tool_3_get_viewport_dom，或使用 tool_7_refresh 刷新。\n\n"
)

# Structural/document nodes that never count as "rendered body content".
_SHELL_TAGS = {
    "html",
    "body",
    "head",
    "#document",
    "#fragment",
    "meta",
    "link",
    "title",
    "script",
    "style",
    "base",
    "noscript",
    "template",
}

# 所有时长（导航宽限、加载超时、判稳静默窗、轮询间隔、命令超时、拟人停顿…）
# 统一由 ``browser_agent.timing`` 提供，app 层可在每次调用前按设置覆盖。
# 见 ``timing.NavTiming`` 等；此处不再保留硬编码常量。


def _href_targets_same_document(node: EnhancedNode, page_url: str) -> bool:
    """True if ``node`` is a link whose target is the current document.

    A same-path/query link (only the fragment may differ) is a *same-document*
    navigation: clicking it either fires ``navigatedWithinDocument`` (caught by
    the soft event) or does nothing at all (clicking the currently-active nav
    item). Either way it must NOT pay the full navigation grace window. Without
    this, an active nav link cost the whole ``grace_seconds`` on every click.
    """
    href = ((node.attributes or {}).get("href") or "").strip()
    if not href or href.lower().startswith(
        ("javascript:", "mailto:", "tel:", "sms:", "blob:")
    ):
        return False
    try:
        from urllib.parse import urljoin, urlsplit

        base = urlsplit(page_url or "")
        target = urlsplit(urljoin(page_url or "", href))
    except Exception:  # noqa: BLE001
        return False
    if not base.scheme or not target.scheme:
        return False
    return (
        target.scheme == base.scheme
        and target.netloc == base.netloc
        and target.path == base.path
        and target.query == base.query
    )


def _fills_same(requested: str, current: Optional[str]) -> bool:
    """Heuristic: did a ``fill`` actually land in the field's current value?

    Exact comparison is too strict — browsers / component libraries reformat a
    typed date (``2027-06-01`` ⇄ ``2027/06/01``) or phone number — so compare the
    alphanumeric skeleton. An empty request is never a check (``True``). A
    ``None`` current value means the node could not be read: also ``True``, to
    avoid a false failure.
    """
    if not requested:
        return True
    if current is None:
        return True

    def _norm(value: str) -> str:
        return "".join(ch for ch in (value or "") if ch.isalnum())

    wanted = _norm(requested)
    if not wanted:
        return True
    return wanted in _norm(current)


class _ContainerScroller:
    """Scrolls an element-level scroll container (``[可滚动元素 eN]``)."""

    def __init__(self, executor: ActionExecutor, backend_node_id: int) -> None:
        self._executor = executor
        self._id = backend_node_id

    def client_height(self) -> Optional[float]:
        return self._executor.client_height(self._id)

    def scroll_top(self) -> Optional[float]:
        return self._executor.scroll_top(self._id)

    def scroll_step(self, step: float) -> Optional[float]:
        return self._executor.scroll_step(self._id, step)

    def at_bottom(self) -> bool:
        return self._executor.at_bottom(self._id)

    def at_top(self) -> bool:
        return self._executor.at_top(self._id)


class _PageScroller:
    """Scrolls the document-level (whole page) scrollbar (``#page``)."""

    def __init__(self, executor: ActionExecutor) -> None:
        self._executor = executor

    def client_height(self) -> Optional[float]:
        return self._executor.page_client_height()

    def scroll_top(self) -> Optional[float]:
        return self._executor.page_scroll_top()

    def scroll_step(self, step: float) -> Optional[float]:
        return self._executor.page_scroll_step(step)

    def at_bottom(self) -> bool:
        return self._executor.page_at_bottom()

    def at_top(self) -> bool:
        return self._executor.page_at_top()


class BrowserController:
    _instance: Optional["BrowserController"] = None

    def __init__(self) -> None:
        self.launcher = BrowserLauncher()
        self.client: Optional[CDPClient] = None
        self.focused_target_id: Optional[str] = None
        self._registries: dict[str, NameRegistry] = {}
        # One counter for every tab: element numbers are globally unique, so the
        # LLM can never confuse ``e17`` on one tab with a different element on
        # another (each tab still owns its name maps / active set).
        self._name_counter = NameCounter()
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
        self.client = CDPClient(
            info["ws_url"], command_timeout=timing.get().cdp.command_timeout
        )
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

    def _opened_tab_names(self, old_target_ids: set) -> list:
        """Names of page tabs that appeared since ``old_target_ids``.

        Used to tell the LLM that a click opened a background tab even though
        the focused tab (and therefore the visible page) did not change.
        """
        opened: list = []
        for target in self._live_targets():
            if target["targetId"] not in old_target_ids:
                opened.append(self._tab_registry.name(target))
        return opened

    def _bring_to_front(self, session: str) -> None:
        """Best-effort activation of a tab's page.

        ``Target.activateTarget`` alone does not always bring a windowed tab to
        the foreground: ``document.hasFocus()`` can keep reporting the old tab,
        so ``tool_5_switch_tab`` returned the requested DOM while ``tool_2``
        then rejected its elements as "belonging to another tab". The
        page-scoped ``Page.bringToFront`` performs the actual activation.
        Never raises (the caller already has a working session).
        """
        assert self.client is not None
        self.client.enable_page_domains(session)
        try:
            self.client.send("Page.bringToFront", {}, session_id=session)
        except CDPError:
            pass

    def _focus_mismatch_notice(self, requested_id: str, actual_id: str) -> str:
        """Explain that a requested tab could not be brought to the foreground."""
        requested = self._name_for_target(requested_id) or requested_id
        actual = self._name_for_target(actual_id) or actual_id
        return (
            f"[无法切换标签页] 未能把「{requested}」切到前台，浏览器当前仍聚焦"
            f"「{actual}」。请尝试用 tool_8_close_tab 关闭「{actual}」，"
            f"或用 tool_9_navigate 打开目标页面后再继续操作。\n\n"
        )

    def _new_tab_notice(self, opened_names: list) -> str:
        """Hint for "a click opened a background tab, focus did not move"."""
        opened = "、".join(opened_names)
        focus = self.focused_tab_label() or "未知标签页"
        return (
            f"[新标签页] 本次互动在当前聚焦标签页之外新打开了标签页（{opened}），"
            f"但当前聚焦标签页并未改变（仍为 {focus}），因此当前聚焦页没有内容变化或只有少量内容变化。"
            f"如需查看或操作新标签页，使用 tool_5_switch_tab 切换到目标标签页。"
        )

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
                    timeout=timing.get().cdp.probe_timeout,
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
            self._registries[target_id] = NameRegistry(self._name_counter)
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
        # Debug 模式：记录原始 HTML 与处理后的序列化 DOM（未开启时静默跳过）。
        if debug.current_capture() is not None:
            raw_html = read_outer_html(self.client, session)
            processed = self.serialize_tree(tree, target_id)
            debug.record_dom(raw_html, processed, tree.url, tree.title)
        return tree

    def serialize_tree(self, tree: EnhancedTree, target_id: str) -> str:
        registry = self._registry_for(target_id)
        return DOMSerializer(registry).serialize(tree)

    def serialize_lines_tree(self, tree: EnhancedTree, target_id: str) -> list[OutLine]:
        registry = self._registry_for(target_id)
        return DOMSerializer(registry).serialize_lines(tree)

    def _incremental_content(
        self,
        old_lines: list[OutLine],
        new_tree: EnhancedTree,
        target_id: str,
        notice: str = "",
        old_url: str = "",
    ) -> str:
        """Build an incremental result: new/changed lines + lost interactive names.

        ``old_lines`` must be captured *before* the interaction (while the
        registry's active set still reflects the old tree): serializing the old
        tree after ``reconcile`` would re-activate removed keys and break the
        lost-element detection.
        """
        registry = self._registry_for(target_id)
        new_lines = self.serialize_lines_tree(new_tree, target_id)
        diff = compute_diff(old_lines, new_lines, old_url, new_tree.url)
        lost = compute_lost(old_lines, new_lines, registry)
        parts = [notice.strip(), diff]
        lost_text = format_lost(lost)
        if lost_text:
            parts.append(lost_text)
        return "\n\n".join(p for p in parts if p)

    # ------------------------------------------------------------------ #
    # interaction (tool-2)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _category_label(category: str) -> str:
        return {
            "input": "可输入",
            "click": "可点击",
            "drag": "可拖动",
            "scroll": "可滚动",
        }.get(category, "不可互动")

    @staticmethod
    def _scroll_plan(scroll_delta: int) -> tuple[int, int]:
        """Map ``scroll_delta`` to ``(direction, steps)``.

        Positive = scroll down, negative = scroll up; the magnitude is the
        number of steps (each step is ``0.7 * visible height``). ``0`` keeps the
        legacy default: a full downward sweep (6 steps). Capped at 6 steps.
        """
        try:
            delta = int(scroll_delta or 0)
        except (TypeError, ValueError):
            delta = 0
        if delta == 0:
            return 1, 6
        return (1 if delta > 0 else -1), max(1, min(abs(delta), 6))

    def _alternative_click_target(self, node: EnhancedNode) -> Optional[int]:
        """The unlabeled control icon that may be the real action behind a label.

        Some component libraries render a control as ``[icon][text label]``
        (radio / checkbox / expand rows) where the *icon* carries the action and
        the label is a no-op or only expands. The icon is now named as its own
        entry (``选择：…``, see ``classify.is_control_icon``); this is the fallback
        for when the LLM still clicks the plain label: if that changed nothing,
        retry on the icon.

        Only a *preceding* sibling qualifies: radio / checkbox / expand icons sit
        before their label, whereas a trailing icon (delete / close) is a
        different action and must not be substituted.
        """
        parent = node.parent
        if parent is None:
            return None
        for sibling in parent.children:
            if sibling is node:
                break
            if is_control_icon(sibling):
                return sibling.backend_node_id
        return None

    def interact(
        self,
        name: str,
        fill: str = "",
        drag_pct: int = 0,
        scroll_delta: int = 0,
    ) -> dict:
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
                INCREMENTAL, FAIL, "无", self._unknown_name_message(name), include_tabs=False
            )
        if not registry.is_active(name):
            return self._base_result(
                INCREMENTAL, FAIL, "无", self._stale_name_message(name), include_tabs=False
            )

        old_tree = self._prev_trees.get(target_id) or self.capture_tree(target_id)
        key = registry.lookup(name)
        node = next((n for n in old_tree.nodes if n.key == key), None)
        if node is None:
            return self._base_result(
                INCREMENTAL, FAIL, "无", self._stale_name_message(name), include_tabs=False
            )

        category = classify(node)
        if fill and category != "input":
            # ``fill`` only means anything on an input. On a click-only element
            # the old code silently dropped the text and still reported success,
            # so the model believed it had filled a field that never changed.
            # Fail loudly with actionable guidance instead.
            return self._base_result(
                INCREMENTAL,
                FAIL,
                "无",
                f"互动元素 {name} 不是可填入元素（它是「{self._category_label(category)}」类），"
                f"fill 参数未执行；请改为对可输入元素填值，或先点击该元素。",
                include_tabs=False,
            )
        old_lines = self.serialize_lines_tree(old_tree, target_id)
        old_focus = self.focused_target_id
        old_name = self._name_for_target(target_id)
        old_target_ids = {t["targetId"] for t in self._live_targets()}
        fill_error = ""

        try:
            session = self.client.attach(target_id)
            self.client.enable_page_domains(session)
            executor = ActionExecutor(self.client, session)
            if category == "input":
                executor.input_text(node.backend_node_id, fill)
                self._stabilize(target_id)
                # A fill can be silently swallowed (a readonly input, or a date /
                # time picker that only accepts calendar selection). Reporting
                # success on an unchanged field sent the LLM into long, confused
                # retries, so verify the value actually landed and fail loudly if
                # it did not.
                if fill:
                    current = executor.read_value(node.backend_node_id)
                    if not _fills_same(fill, current):
                        fill_error = (
                            f"元素 {name} 的填充未生效：填入了「{fill}」，"
                            f"但控件当前值为「{current or '空'}」。"
                            f"该控件可能是只读、或日期/时间选择器，无法用 fill 直接写入；"
                            f"请点击它之后在弹出的选择器里选择，或改为对其它可输入元素填值。"
                        )
            elif category == "drag":
                executor.drag(node.backend_node_id, drag_pct)
                self._stabilize(target_id)
            elif category == "scroll":
                scroller = (
                    _PageScroller(executor)
                    if node.tag == PAGE_SCROLL_TAG
                    else _ContainerScroller(executor, node.backend_node_id)
                )
                direction, steps = self._scroll_plan(scroll_delta)
                diff = self._scroll_and_collect(
                    scroller,
                    target_id,
                    old_lines,
                    registry,
                    direction=direction,
                    max_steps=steps,
                )
                notice = self._title_change_notice(target_id, old_name).strip()
                content = "\n\n".join(p for p in (notice, diff) if p)
                return self._base_result(INCREMENTAL, OK, content, include_tabs=False)
            else:
                # A click may trigger a navigation; wait for it to actually
                # begin/finish instead of trusting the stale DOM's quietness.
                retry_id = (
                    self._alternative_click_target(node)
                    if is_cursor_pointer_only(node)
                    else None
                )
                # Snapshot the DOM before acting so we can tell a real change
                # from a no-op (a serialized diff would be polluted by the
                # ``:hover`` the move induces, the raw markup is not).
                before_sig = executor.dom_signature() if retry_id is not None else ""
                # A same-document link (active nav item / in-page hash) must not
                # pay the full navigation grace: it either fires a soft event or
                # does nothing.
                click_may_nav = may_navigate(node)
                if click_may_nav and _href_targets_same_document(node, old_tree.url):
                    click_may_nav = False
                with self._watch_navigation(session) as nav_state:
                    executor.click(node.backend_node_id)
                    self._settle_navigation(session, nav_state, click_may_nav)
                if retry_id is not None and before_sig:
                    if executor.dom_signature() == before_sig:
                        # The (named) label did nothing; the real control is the
                        # unlabeled icon sibling (radio/checkbox rows).
                        with self._watch_navigation(session) as nav_state:
                            executor.click(retry_id)
                            self._settle_navigation(session, nav_state)
        except ActionError as exc:
            return self._base_result(INCREMENTAL, FAIL, "无", str(exc), include_tabs=False)
        except CDPError as exc:
            return self._base_result(INCREMENTAL, FAIL, "无", str(exc), include_tabs=False)

        new_focus = self._resolve_focused_target()
        if new_focus and new_focus != old_focus:
            new_tree = self.capture_tree(new_focus)
            new_text = self.serialize_tree(new_tree, new_focus)
            if self._is_blank_shell(new_tree):
                new_text = _BLANK_SHELL_NOTICE + new_text
            return self._base_result(
                FULL, FAIL if fill_error else OK, new_text,
                error=fill_error or "无", include_tabs=True,
            )

        # The click may have opened a background tab without moving focus. The
        # focused page then looks unchanged and an incremental diff would read
        # "（页面无变化）", which falsely implies nothing happened; tell the LLM
        # explicitly and hand it the full tab list instead.
        opened_names = self._opened_tab_names(old_target_ids)
        if opened_names:
            content = self._new_tab_notice(opened_names)
            return self._base_result(
                INCREMENTAL, FAIL if fill_error else OK, content,
                error=fill_error or "无", include_tabs=True,
            )

        new_tree = self.capture_tree(target_id)
        notice = self._title_change_notice(target_id, old_name)
        content = self._incremental_content(
            old_lines, new_tree, target_id, notice, old_url=old_tree.url
        )
        return self._base_result(
            INCREMENTAL, FAIL if fill_error else OK, content,
            error=fill_error or "无", include_tabs=False,
        )

    def _scroll_and_collect(
        self,
        scroller: object,
        target_id: str,
        base_lines: list[OutLine],
        registry: NameRegistry,
        direction: int = 1,
        max_steps: int = 6,
    ) -> str:
        """Scroll a scroller in overlap-preserving steps, accumulating content.

        ``scroller`` is either a ``_ContainerScroller`` or a ``_PageScroller``.
        ``direction`` is ``+1`` (down) or ``-1`` (up). Each step is smaller than
        the visible height, so consecutive viewports always overlap. Newly
        revealed lines are accumulated across every step, so the returned diff
        is contiguous and never skips content between the pre-scroll and
        post-scroll snapshots. Only new/changed lines are emitted (no
        ``-``/``~`` prefixes).
        """
        client_h = scroller.client_height()
        if client_h and client_h > 0:
            # Always smaller than the visible height so consecutive viewports
            # overlap; this is what guarantees no content is skipped.
            step = max(1.0, float(client_h) * 0.7)
        else:
            step = 120.0
        down = direction >= 0
        signed_step = step if down else -step

        added: list[OutLine] = []
        seen: set[tuple[int, str]] = set()
        prev_lines = base_lines
        last_top = scroller.scroll_top()

        for _ in range(max(1, max_steps)):
            top = scroller.scroll_step(signed_step)
            if top is None:
                break
            if last_top is not None and abs(top - last_top) < 1:
                break
            last_top = top

            tree = self.capture_tree(target_id)
            lines = self.serialize_lines_tree(tree, target_id)
            for line in changed_lines(prev_lines, lines):
                key = (line.depth, line.text)
                if key not in seen:
                    seen.add(key)
                    added.append(line)
            prev_lines = lines

            if (scroller.at_bottom() if down else scroller.at_top()):
                break

        if not added:
            return "（页面无变化）"
        content = format_lines(added)
        lost_text = format_lost(compute_lost(base_lines, prev_lines, registry))
        if lost_text:
            content = content + "\n\n" + lost_text
        return content

    def _resolve_interactive_node(
        self, registry: NameRegistry, tree: EnhancedTree, name: str
    ) -> tuple[Optional[object], str]:
        """Return (node, "") or (None, error_message) for an interactive name."""
        if registry.lookup(name) is None:
            return None, self._unknown_name_message(name)
        if not registry.is_active(name):
            return None, self._stale_name_message(name)
        key = registry.lookup(name)
        node = next((n for n in tree.nodes if n.key == key), None)
        if node is None:
            return None, self._stale_name_message(name)
        return node, ""

    def _foreign_tab_for_name(self, name: str, current_target_id: str) -> str:
        """Tab label of another tab whose (live) registry knows ``name``, else "".

        Element names are scoped per tab, but the LLM only ever sees the current
        page. After a focus change a name the LLM learned on one tab can silently
        resolve to a *different* element on another tab. When the name is unknown
        on the focused tab we point the LLM at the tab that actually owns it.
        """
        for target_id, registry in self._registries.items():
            if target_id == current_target_id:
                continue
            if registry.lookup(name) is not None and registry.is_active(name):
                label = self._name_for_target(target_id)
                if label:
                    return label
        return ""

    def _unknown_name_message(self, name: str) -> str:
        focus = self.focused_tab_label()
        other = self._foreign_tab_for_name(name, self.focused_target_id or "")
        if other:
            return (
                f"互动元素 {name} 不属于当前聚焦标签页（{focus or '未知'}），"
                f"而属于「{other}」；请先用 tool_5_switch_tab 切换到该标签页再互动。"
            )
        return f"未知的互动元素名称 {name}"

    def _stale_name_message(self, name: str) -> str:
        focus = self.focused_tab_label()
        other = self._foreign_tab_for_name(name, self.focused_target_id or "")
        if other:
            return (
                f"互动元素 {name} 在当前聚焦标签页（{focus or '未知'}）中已不存在，"
                f"它属于「{other}」；如需继续，请先用 tool_5_switch_tab 切换。"
            )
        return "该互动元素已经不存在于viewport中了"

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
        click_may_nav = False
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
            click_may_nav = may_navigate(node)
            if click_may_nav and _href_targets_same_document(node, tree.url):
                click_may_nav = False
            plan.append((click_name, "click", node.backend_node_id))

        # ---- serial execution ----
        old_lines = self.serialize_lines_tree(tree, target_id)
        old_focus = self.focused_target_id
        old_name = self._name_for_target(target_id)
        old_target_ids = {t["targetId"] for t in self._live_targets()}

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
                self._settle_navigation(session, nav_state, click_may_nav)
        else:
            _run_plan()
            if error_msg and success_count == 0:
                return self._base_result(
                    INCREMENTAL, FAIL, "无", error_msg, include_tabs=False
                )
            self._stabilize(target_id)

        new_focus = self._resolve_focused_target()
        focus_changed = bool(new_focus and new_focus != old_focus)
        opened_names = (
            []
            if (focus_changed or error_msg)
            else self._opened_tab_names(old_target_ids)
        )

        # Verify the fills actually landed. Component-library pickers / readonly
        # fields silently ignore a text fill; without this check the batch would
        # be reported as fully successful while the fields stayed unchanged (the
        # model then chases the mismatch for many turns).
        failed_fills: list[tuple[str, Optional[str]]] = []
        if not error_msg and not focus_changed and not opened_names:
            for (fname, fcat, fbid), fval in zip(plan, fills):
                if fcat != "input" or not fval:
                    continue
                current = executor.read_value(fbid)
                if not _fills_same(fval, current):
                    failed_fills.append((fname, current))

        if focus_changed:
            new_tree = self.capture_tree(new_focus)
            content = self.serialize_tree(new_tree, new_focus)
            mode = FULL
        elif opened_names:
            # Trailing click opened a background tab without moving focus:
            # replace the (empty) incremental diff with an explicit hint.
            content = self._new_tab_notice(opened_names)
            mode = INCREMENTAL
        else:
            new_tree = self.capture_tree(target_id)
            notice = self._title_change_notice(target_id, old_name)
            content = self._incremental_content(
                old_lines, new_tree, target_id, notice, old_url=tree.url
            )
            mode = INCREMENTAL

        include_tabs = focus_changed or bool(opened_names)
        if error_msg:
            action_ok = PARTIAL_FAIL if success_count > 0 else FAIL
            return self._base_result(
                mode, action_ok, content, error=error_msg, include_tabs=include_tabs
            )
        if failed_fills:
            msg = (
                "以下元素的填充未生效："
                + "、".join(f"{n}（当前值：{c or '空'}）" for n, c in failed_fills)
                + "。它们可能是只读、或日期/时间选择器，无法用 fill 直接写入；"
                "请点击它之后在弹出的选择器里选择，或改用其它可输入元素。"
            )
            return self._base_result(
                mode, PARTIAL_FAIL, content, error=msg, include_tabs=include_tabs
            )
        return self._base_result(mode, OK, content, include_tabs=include_tabs)

    def _quiet(
        self,
        session: str,
        quiet_seconds: Optional[float] = None,
        timeout: Optional[float] = None,
    ) -> None:
        """Block until the session's DOM fingerprint stops changing."""
        assert self.client is not None
        cfg = timing.get()
        if quiet_seconds is None:
            quiet_seconds = cfg.nav.quiet_seconds
        if timeout is None:
            timeout = cfg.nav.quiet_timeout

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
                    timeout=cfg.cdp.probe_timeout,
                )
                return str((result.get("result") or {}).get("value"))
            except Exception:
                return None

        poll_until_quiet(
            probe,
            quiet_seconds=quiet_seconds,
            timeout=timeout,
            interval=cfg.nav.probe_interval,
        )

    def _stabilize(self, target_id: str) -> None:
        assert self.client is not None
        try:
            session = self.client.attach(target_id)
        except CDPError:
            return
        self._quiet(session)

    def _body_child_count(self, session: str) -> int:
        """Number of element children of ``<body>`` (``-1`` on any error)."""
        assert self.client is not None
        try:
            result = self.client.send(
                "Runtime.evaluate",
                {
                    "expression": "document.body ? document.body.childElementCount : 0",
                    "returnByValue": True,
                },
                session_id=session,
                timeout=timing.get().cdp.probe_timeout,
            )
            return int((result.get("result") or {}).get("value") or 0)
        except Exception:
            return -1

    def _wait_for_content(self, session: str, quiet: bool = True) -> None:
        """Wait out a not-yet-rendered SPA shell.

        After a click that triggers an async render (no document navigation), the
        DOM can be perfectly quiet yet still empty: ``readyState == "complete"``
        and a stable ``<html><head>…</head></html>`` skeleton. The normal quiet
        check then settles immediately and the tool returns a seemingly empty
        page. When ``<body>`` has no element children we keep polling up to
        ``nav.load_timeout`` for the first content to appear, then re-check
        quietness. Non-empty pages are unaffected.
        """
        assert self.client is not None
        cfg = timing.get()
        empty = self._body_child_count(session) == 0
        if empty:
            deadline = time.time() + cfg.nav.load_timeout
            while time.time() < deadline:
                time.sleep(cfg.nav.settle_poll_interval)
                if self._body_child_count(session) > 0:
                    break
        if quiet or empty:
            self._quiet(session)

    @staticmethod
    def _is_blank_shell(tree: EnhancedTree) -> bool:
        """True if the tree has no rendered body content (a bare SPA skeleton)."""
        for node in tree.nodes:
            if not node.is_element or not node.visible or not node.in_viewport:
                continue
            if node.tag in _SHELL_TAGS:
                continue
            return False
        return True

    @contextlib.contextmanager
    def _watch_navigation(self, session: str):
        """Watch CDP navigation events for ``session`` while an action runs.

        Yields a state dict with ``start`` (a real document navigation began),
        ``commit`` (a real document navigation committed), ``soft``
        (same-document / SPA route change) and ``load`` events.
        """
        assert self.client is not None
        state = {
            "start": threading.Event(),
            "commit": threading.Event(),
            "soft": threading.Event(),
            "load": threading.Event(),
        }

        # Only the main frame matters: sub-frame loads (iframes/ads) must not
        # be mistaken for a real navigation.
        try:
            frame_tree = self.client.send(
                "Page.getFrameTree", {}, session_id=session, timeout=timing.get().cdp.probe_timeout
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
            ("Page.frameStartedLoading", _handler(state["start"], True)),
            ("Page.frameNavigated", _handler(state["commit"], True)),
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

    def _settle_navigation(
        self, session: str, state: dict, may_navigate: bool = True
    ) -> None:
        """Settle the page after an action, tolerating an induced navigation.

        Waits up to ``grace_seconds`` (or the shorter ``short_grace_seconds``
        when the acted element cannot navigate) for a navigation to *begin*.
        If a real navigation begins, waits for it to *commit*
        (``Page.frameNavigated``) and then — only if ``load`` has not already
        fired — a short additional ``load_grace_seconds``. It deliberately does
        NOT wait the old 15 s ``load_timeout`` here: clicks on SPAs that never
        fire ``load`` used to block for the whole 15 s. Finally it always waits
        for the DOM to go quiet (covers same-document route changes and async
        rendering as well).
        """
        cfg = timing.get().nav
        grace = cfg.grace_seconds if may_navigate else cfg.short_grace_seconds
        deadline = time.time() + grace
        while time.time() < deadline:
            if (
                state["start"].is_set()
                or state["commit"].is_set()
                or state["soft"].is_set()
                or state["load"].is_set()
            ):
                break
            time.sleep(cfg.settle_poll_interval)

        saw_nav = state["start"].is_set() or state["commit"].is_set()
        # Only a *document* navigation (hard) needs a commit / load wait. A
        # same-document route change ("soft") also emits ``frameStartedLoading``
        # on some sites, yet has no commit or load to wait for — treating it as
        # hard used to cost seconds on every SPA click.
        hard = saw_nav and not state["soft"].is_set()
        if hard:
            if not state["commit"].is_set():
                state["commit"].wait(cfg.commit_timeout)
            if not state["load"].is_set():
                state["load"].wait(cfg.load_grace_seconds)
        self._wait_for_content(session)

        # Safety net for the short-grace path: if a *document* navigation only
        # began during the quiet window (so the grace loop never saw it), wait
        # for its commit and settle again. Costs nothing on the common path.
        if (
            not saw_nav
            and (state["start"].is_set() or state["commit"].is_set())
            and not state["commit"].is_set()
            and not state["soft"].is_set()
        ):
            state["commit"].wait(cfg.commit_timeout)
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
            session = self.client.attach(target_id)
            self.client.enable_page_domains(session)
            self._wait_for_content(session, quiet=False)
            tree = self.capture_tree(target_id)
            content = self.serialize_tree(tree, target_id)
            if self._is_blank_shell(tree):
                content = _BLANK_SHELL_NOTICE + content
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

    def _wait_ready(self, target_id: str, timeout: Optional[float] = None) -> None:
        assert self.client is not None
        cfg = timing.get()
        if timeout is None:
            timeout = cfg.ready.timeout
        session = self.client.attach(target_id)
        self.client.enable_page_domains(session)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                result = self.client.send(
                    "Runtime.evaluate",
                    {"expression": "document.readyState", "returnByValue": True},
                    session_id=session,
                    timeout=cfg.cdp.probe_timeout,
                )
                if (result.get("result") or {}).get("value") == "complete":
                    break
            except Exception:
                pass
            time.sleep(cfg.ready.poll_interval)
        self._wait_for_content(session)

    def _wait_ready_new_tab(
        self, target_id: str, expected_url: str = "", timeout: Optional[float] = None
    ) -> None:
        """Wait for a freshly created tab to finish its *initial* navigation.

        A brand-new target starts on ``about:blank`` which already reports
        ``readyState == "complete"``; ``_wait_ready`` would therefore return
        immediately and capture an empty page. Here we refuse to accept the
        page until the real document has committed (``location.href`` has left
        ``about:blank``) and is complete, then wait for the DOM to go quiet.
        """
        assert self.client is not None
        cfg = timing.get()
        if timeout is None:
            timeout = cfg.nav.load_timeout
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
                    timeout=cfg.cdp.probe_timeout,
                )
                value = str((result.get("result") or {}).get("value") or "")
            except Exception:
                value = ""
            ready_state, _, href = value.partition("|")
            committed = allow_blank or (bool(href) and href != "about:blank")
            if committed and ready_state == "complete":
                break
            time.sleep(cfg.ready.new_tab_poll_interval)
        self._wait_for_content(session)

    def _full_result_for(self, target_id: str, action_ok: str = NOT_CALLED) -> dict:
        tree = self.capture_tree(target_id)
        content = self.serialize_tree(tree, target_id)
        if self._is_blank_shell(tree):
            content = _BLANK_SHELL_NOTICE + content
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
            self._bring_to_front(self.client.attach(target_id))
            self.focused_target_id = target_id
            self._wait_ready(target_id)
        except (CDPError, RuntimeError) as exc:
            return self._base_result(FULL, FAIL, "无", str(exc))
        result = self._full_result_for(target_id)
        # If the browser refused to foreground the requested tab, do not pretend
        # the switch succeeded (that trapped the LLM in a switch/interact loop):
        # return the tab that is *actually* focused, with an explicit notice.
        actual = self._resolve_focused_target()
        if actual is not None and actual != target_id:
            result = self._full_result_for(actual)
            result["content"] = self._focus_mismatch_notice(target_id, actual) + result["content"]
        return result

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
        old_lines = self.serialize_lines_tree(old_tree, target_id)
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
        notice = self._title_change_notice(target_id, old_name)
        content = self._incremental_content(
            old_lines, new_tree, target_id, notice, old_url=old_tree.url
        )
        return self._base_result(INCREMENTAL, OK, content, include_tabs=False)

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
        cfg = timing.get().ready
        deadline = time.time() + cfg.close_timeout
        while time.time() < deadline:
            if target_id not in {t["targetId"] for t in self._page_targets()}:
                break
            time.sleep(cfg.close_poll_interval)
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
        try:
            self._bring_to_front(self.client.attach(target_id))
        except CDPError:
            pass
        self._wait_ready_new_tab(target_id, target_url)
        result = self._full_result_for(target_id)
        actual = self._resolve_focused_target()
        if actual is not None and actual != target_id:
            result = self._full_result_for(actual)
            result["content"] = self._focus_mismatch_notice(target_id, actual) + result["content"]
        return result
