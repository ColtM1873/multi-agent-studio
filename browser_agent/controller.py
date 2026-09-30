"""Process-wide browser session state (hidden state behind the tool API)."""

from __future__ import annotations

import contextlib
import re
import threading
import time
from dataclasses import dataclass
from typing import Optional

from . import debug, timing
from .actions import (
    ActionError,
    ActionExecutor,
    overlay_probe_expression,
)
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
    has_text,
    is_control_icon,
    is_cursor_pointer_only,
    is_range_picker_entry,
    may_navigate,
    read_outer_html,
)
from .launcher import BrowserLauncher, BrowserLaunchError
from .tab_registry import TabRegistry

# 所有对外返回文本 / 状态码集中在 ``return_prompt`` 中定义，这里以别名导入：
# 既保持模块内引用不变，也保持外部
# ``from browser_agent.controller import NOT_CALLED`` 可用。
from . import return_prompt as rp

FULL = rp.FULL
INCREMENTAL = rp.INCREMENTAL

OK = rp.OK
FAIL = rp.FAIL
PARTIAL_FAIL = rp.PARTIAL_FAIL
NOT_CALLED = rp.NOT_CALLED

# Element names (``e12``) inside serialized lines. Used to compare two serialized
# snapshots while ignoring pure renumbering: a click whose markup is unchanged but
# whose React re-render swapped backend node ids produces a diff made *only* of
# renamed tags, which must not be mistaken for real content.
_ELEMENT_NAME_RE = re.compile(r"\be\d+\b")


def _lines_have_new_text(old_lines: list[OutLine], new_lines: list[OutLine]) -> bool:
    """True if ``new_lines`` carries text not present in ``old_lines`` (names ignored)."""
    old_text = {
        (line.depth, _ELEMENT_NAME_RE.sub("eN", line.text))
        for line in old_lines
        if line.text
    }
    for line in new_lines:
        if not line.text:
            continue
        if (line.depth, _ELEMENT_NAME_RE.sub("eN", line.text)) not in old_text:
            return True
    return False

# 空白页/骨架提示文本见 ``return_prompt.BLANK_SHELL_NOTICE`` / ``ABOUT_BLANK_NOTICE``；
# 由 ``_blank_notice(tree)`` 依据 URL 选择。

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

    A fill counts as landed when the control now holds the requested value and
    nothing else:

    * whitespace/case are ignored, and the comparison is done in *both*
      directions so a reformatting widget still verifies — a typed date coming
      back as ``2027/06/01`` or ``2027-06-01`` for a requested ``2027-06`` is a
      landing, not a failure;
    * a mere substring on either side is **not** enough. The previous one-way
      containment accepted a *corrupted* value: when the clear was swallowed the
      field held the concatenation ``2026-052024-09`` and the requested
      ``2024-09`` was "found" inside it, so the tool reported success while the
      page showed garbage (the real 4399 session then typed on top of it and made
      it worse).

    An empty request is never a check here (``True``); callers that mean "clear
    the field" verify emptiness themselves. ``None`` means the node could not be
    read: also ``True``, to avoid a false failure.
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
    got = _norm(current)
    if wanted == got:
        return True
    # Reformatting tolerance, deliberately narrow. A control may add/remove
    # *digit-only* decoration (a day appended to a month, a separator swap), and
    # the requested text must then still appear as one **contiguous** run whose
    # digit budget is unchanged apart from that decoration. A concatenation has no
    # contiguous match at all (``2026-05`` + ``2024-09`` ⇒ ``202605202409``:
    # ``202409`` is not a substring) and a re-sliced fragment exceeds the digit
    # budget, so neither is mistaken for success.
    if len(got) > len(wanted):
        index = got.find(wanted)
        if index < 0:
            return False
        if not _same_digit_budget(wanted, got):
            return False
        extra_left = got[:index]
        extra_right = got[index + len(wanted):]
        if not (extra_left.isdigit() or extra_right.isdigit()):
            return False
        return len(extra_left) <= 2 and len(extra_right) <= 2
    # The control kept *less* than we typed: accept only when what it kept is a
    # contiguous run of the request (a masked / truncated display).
    return got in wanted and _same_digit_budget(got, wanted)


def _same_digit_budget(shorter: str, longer: str) -> bool:
    """True unless ``longer`` holds a *re-sliced* fragment of ``shorter``'s digits.

    ``2027-06`` vs ``2027-06-01`` share the digit run ``202706`` (longer simply
    appends ``01``) — a reformat. ``24-0`` vs ``2024-09`` does not: the shorter
    value's digits are a *misaligned* slice of the longer one, which is the
    signature of a concatenated / half-replaced field. Allowing at most two extra
    digits keeps the tolerance to a day / year fragment.
    """
    want_digits = "".join(ch for ch in shorter if ch.isdigit())
    got_digits = "".join(ch for ch in longer if ch.isdigit())
    if want_digits and want_digits in got_digits:
        return len(got_digits) - len(want_digits) <= 2
    return not want_digits


# Native input ``type`` values that are rendered by a date/time picker widget.
_DATE_INPUT_TYPES = {"date", "datetime-local", "month", "week", "time"}
# Placeholder words that reliably mark a date/time field (``开始日期`` /
# ``结束日期`` / ``出生日期`` …). We deliberately keep this narrow so ordinary
# free-text fields are never mistaken for pickers.
_DATE_PLACEHOLDER_HINTS = ("日期", "时间")
# Ancestor class tokens emitted by date-picker component libraries.
_DATE_CLASS_HINTS = ("picker", "calendar", "datepicker", "date-picker")


def _is_date_like_input(node: EnhancedNode) -> bool:
    """Heuristic: is ``node`` an input rendered by a date/time picker?

    Date / time widgets display typed text in the ``<input>`` but only *commit*
    a value when the text parses and the field blurs; a controlled picker then
    reverts the text. So a fill that merely appears in the DOM can still be a
    silent no-op (and ``_fills_same`` cannot tell). Recognizing these fields
    lets the caller reject nonsense text and verify the commit.
    """
    if node is None or not node.is_element:
        return False
    if node.tag == "input":
        if (node.attributes.get("type") or "").lower() in _DATE_INPUT_TYPES:
            return True
    placeholder = node.attributes.get("placeholder") or ""
    if any(hint in placeholder for hint in _DATE_PLACEHOLDER_HINTS):
        return True
    current: Optional[EnhancedNode] = node
    hops = 0
    while current is not None and hops < 5:
        classes = (current.attributes.get("class") or "").lower()
        if any(hint in classes for hint in _DATE_CLASS_HINTS):
            return True
        current = current.parent
        hops += 1
    return False


def _looks_like_date(text: str) -> bool:
    """A date/time value must contain at least one digit.

    ``至今`` / ``present`` / ``now`` are not dates and a date picker cannot store
    them. Requiring a digit is intentionally permissive (it does not validate the
    calendar) — the caller additionally blur-verifies that the picker committed.
    """
    return any(ch.isdigit() for ch in (text or ""))


# One interactive element tag: ``<可点击元素 e12>…</可点击元素 e12>``. Used by the
# scroll accumulator to drop the fragments that overlap between consecutive
# (deliberately overlapping) scroll windows.
_INTERACTIVE_FRAGMENT_RE = re.compile(
    r"<(?P<tag>可点击元素|可输入元素|可滚动元素|可拖动元素) (?P<name>e\d+)>"
    r".*?</(?P=tag) (?P=name)>"
)


def _dedupe_scroll_fragments(lines: list[OutLine]) -> list[OutLine]:
    """Drop interactive fragments already emitted in an earlier scroll step.

    A scrollable list renders *many* interactive siblings into a **single**
    ``OutLine`` (one long line). Each overlapping scroll step shifts that whole
    line, so ``changed_lines`` treats it as a replace and the per-line dedup key
    ``(depth, text)`` never matches — the shared candidates were repeated in the
    output (``上海旅游高等专科学校`` etc. shown two or three times). Dedup at the
    fragment (element) level instead, which is exactly the grain the LLM cares
    about.
    """
    seen: set[str] = set()
    out: list[OutLine] = []
    for line in lines:
        if not line.text:
            out.append(line)
            continue

        def _repl(match: "re.Match[str]") -> str:
            name = match.group("name")
            if name in seen:
                return ""
            seen.add(name)
            return match.group(0)

        text = _INTERACTIVE_FRAGMENT_RE.sub(_repl, line.text)
        if not text.strip():
            continue
        kept = tuple(name for name in line.interactive if name in text)
        out.append(
            OutLine(
                depth=line.depth,
                text=text,
                kind=line.kind,
                ancestors=line.ancestors,
                interactive=kept,
                closing=line.closing,
            )
        )
    return out


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


@dataclass
class _InteractContext:
    """Pre-action snapshot shared by the split interaction methods.

    Companion to :meth:`BrowserController.interact` (which is kept untouched):
    produced by ``_begin_interaction`` and consumed by the ``_run_*`` /
    ``_finish_interaction`` helpers so each split method shares one preamble
    and one post-processing path.
    """

    name: str
    target_id: str
    registry: NameRegistry
    node: EnhancedNode
    old_tree: EnhancedTree
    old_lines: list[OutLine]
    old_name: str
    old_target_ids: set
    session: str
    executor: ActionExecutor
    scroll_before: Optional[float] = None


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
        # 连续「死点击」计数：key=(doc_token, frame_id, backend_node_id) → 连续
        # 点击后前后 DOM 逐字节一致的次数。用于在 LLM 反复点击同一个非功能性元素
        # （如只有 ``cursor:pointer`` 的步骤指示/装饰文字）时升级提示措辞，避免它
        # 无限重试。点击成功即清零；元素被重渲染换 key 后自然从 0 重新计。
        self._noop_counts: dict[tuple[str, str, int], int] = {}
        # 串行化「抓取/互动」类浏览器操作：只有一个浏览器 / 一条 CDP 连接，多个
        # 入口并发（前端「提示接管」轮询 viewport-dom、图内浏览器工具）同时抓取会
        # 让 CDP 连接排队，单条命令拖到 30s 超时并触发断线重连。
        # RLock 允许同一线程内嵌套。
        self._op_lock = threading.RLock()
        # 只用于「建立/重建 CDP 连接」的专用锁。它**不**与 _op_lock 共享：已连接
        # 时的快速路径（refresh_targets）绝不排队在一段正在进行的 DOM 抓取/序列化
        # 之后，否则「打开浏览器」会被长时间进行的预览抓取饿死。
        self._connect_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "BrowserController":
        if cls._instance is None:
            cls._instance = BrowserController()
        return cls._instance

    # ------------------------------------------------------------------ #
    # connection
    # ------------------------------------------------------------------ #
    def ensure_connected(self) -> None:
        # 快速路径：已连接且健康。**不参与 _op_lock**——否则「打开浏览器」会排队在
        # 一段正在进行的 DOM 抓取/序列化之后被饿死（而抓取本身又是慢的根源）。
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

        # 建立/重建连接：只用专用的 _connect_lock，避免两个线程同时建连。
        with self._connect_lock:
            # 双重检查：等锁期间别的线程可能已经建好了连接。
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
        return rp.title_change_notice(old_name, self._name_for_target(target_id))

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
        so ``tool_21_switch_tab`` returned the requested DOM while ``tool_0x_interact_xxx``
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
        return rp.focus_mismatch_notice(requested, actual)

    def _new_tab_notice(self, opened_names: list) -> str:
        """Hint for "a click opened a background tab, focus did not move"."""
        opened = "、".join(opened_names)
        return rp.new_tab_notice(opened, self.focused_tab_label())

    def _probe_focused_target(self) -> Optional[str]:
        """The tab the human is looking at, for the **initial** pick only.

        Consulted only when the tool has no explicit selection yet (see
        :meth:`_resolve_focused_target`). ``document.hasFocus()`` on the human's
        active tab is preferred; ``document.visibilityState`` is a weaker
        fallback (measured on real Chrome it can lag behind
        ``Target.activateTarget``). Once the tool has selected a tab, that
        selection is authoritative and this probe is never used to override it.
        """
        assert self.client is not None
        visible: list[str] = []
        focused: list[str] = []
        for target in self._page_targets():
            target_id = target["targetId"]
            try:
                session = self.client.attach(target_id)
                self.client.enable_page_domains(session)
                result = self.client.send(
                    "Runtime.evaluate",
                    {
                        "expression": "document.visibilityState + '|' + document.hasFocus()",
                        "returnByValue": True,
                    },
                    session_id=session,
                    timeout=timing.get().cdp.probe_timeout,
                )
                value = str((result.get("result") or {}).get("value") or "")
            except Exception:
                continue
            state, _, focus = value.partition("|")
            if state == "visible":
                visible.append(target_id)
            if focus == "true":
                focused.append(target_id)
        if focused:
            return focused[0]
        if visible:
            return visible[0]
        return None

    def _await_opened_tabs(self, old_target_ids: set, timeout: Optional[float] = None) -> list:
        """Poll briefly for page tabs opened since ``old_target_ids``.

        A page's ``window.open`` (``target=_blank`` / JS popup) can create its tab
        a moment *after* the click has settled, so an immediate check misses it and
        the tool reports “（页面无变化）” while two tabs quietly appeared — the LLM
        then wastes many turns discovering them via ``tool_3_list_tabs``. Only the
        no-change path pays this bounded wait.
        """
        if timeout is None:
            timeout = timing.get().ready.new_tab_wait
        deadline = time.time() + timeout
        while True:
            opened = self._opened_tab_names(old_target_ids)
            if opened or time.time() >= deadline:
                return opened
            time.sleep(timing.get().ready.new_tab_poll_interval)

    def _resolve_focused_target(self) -> Optional[str]:
        """The tab the tool operates on: the **explicit selection is the truth**.

        The tool *owns* the browser: once ``focused_target_id`` is set (by
        ``switch_tab`` / ``navigate``, or the first resolution), it is never
        overridden by where the human happens to be looking. Human tab clicks,
        peeking at the window, or a page opening a background tab therefore can
        NOT redirect the LLM mid-task; only explicit tool actions change it.
        (See :meth:`_activate_target`, which keeps the *displayed* tab in sync
        with this selection so a peeking human sees what the tool is doing.)

        Only when there is no live explicit selection do we adopt the human's
        active tab, then fall back to the first target.
        """
        targets = self._page_targets()
        if not targets:
            return None
        if self.focused_target_id and any(
            t["targetId"] == self.focused_target_id for t in targets
        ):
            return self.focused_target_id
        probed = self._probe_focused_target()
        if probed is not None:
            self.focused_target_id = probed
            return probed
        self.focused_target_id = targets[0]["targetId"]
        return self.focused_target_id

    def _activate_target(self, target_id: str) -> bool:
        """Make ``target_id`` the browser's active (human-visible) tab.

        Best-effort and non-verifying: under the "LLM owns the tab" contract the
        explicit selection is authoritative, so there is nothing to verify — we
        only need the *displayed* tab to follow the tool so a human peeking at
        the window always sees what the LLM is operating on. Called before every
        read/interaction, not just on switch/navigate.

        ``Target.activateTarget`` alone does not always foreground a windowed
        tab, so the page-scoped ``Page.bringToFront`` is sent too. Never raises.
        """
        assert self.client is not None
        try:
            self.client.send("Target.activateTarget", {"targetId": target_id})
        except CDPError:
            pass
        try:
            self._bring_to_front(self.client.attach(target_id))
        except CDPError:
            pass
        self.focused_target_id = target_id
        return True

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
            raise RuntimeError(rp.NO_TABS)
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

    def _viewport_scroll_note(self, ctx: "_InteractContext") -> str:
        """A notice when the interaction auto-scrolled the whole document.

        A click/fill on an off-screen target calls ``DOM.scrollIntoViewIfNeeded``,
        which moves the viewport. The resulting incremental diff is then dominated
        by content that merely *scrolled into view* and has nothing to do with the
        action itself; without a heads-up the LLM misreads it as "opening this
        control revealed hidden content" and wastes reasoning. Returns "" when the
        page did not move (or the offset could not be read).
        """
        before = getattr(ctx, "scroll_before", None)
        try:
            after = ctx.executor.page_scroll_top()
        except Exception:  # noqa: BLE001
            after = None
        if before is None or after is None or abs(after - before) < 8:
            return ""
        return rp.viewport_scrolled(before, after)

    # ------------------------------------------------------------------ #
    # interaction (tool-0x)
    # ------------------------------------------------------------------ #
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

    @staticmethod
    def _date_fill_error(name: str, fill: str, current: Optional[str] = None) -> str:
        """Human, actionable error for a fill that a date picker did not commit."""
        return rp.date_fill_error(name, fill, current)

    @staticmethod
    def _select_option_texts(node: EnhancedNode, limit: int = 30) -> list:
        """Visible texts of a native ``<select>``'s selectable options.

        Used to give the LLM actionable feedback when a ``fill`` does not match
        any option (``可选值：深圳、北京、杭州``). The placeholder option (empty
        ``value``) is excluded; duplicates keep their first occurrence.
        """
        out: list[str] = []
        for child in node.children:
            if not child.is_element or child.tag != "option":
                continue
            value = (child.attributes.get("value") or "").strip()
            if not value:
                continue
            text = (child.attributes.get("title") or "").strip()
            if not text:
                text = " ".join(
                    (part.text or "") for part in child.children if part.is_text
                ).strip()
            if text and text not in out:
                out.append(text)
            if len(out) >= limit:
                break
        return out

    def _select_fill_error(
        self, name: str, fill: str, current: Optional[str], options: list
    ) -> str:
        return rp.select_fill_error(name, fill, current, options)

    def _select_click_notice(self, name: str, options: list) -> str:
        return rp.select_click_notice(name, options)

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

    def _label_descendant_target(self, node: EnhancedNode) -> Optional[int]:
        """The inner node that actually carries ``node``'s visible label.

        Some libraries render a clickable wrapper ``[icon][text span]`` where the
        wrapper is what gets *named* (its label is the span's text) but the action
        handler is bound to the span, or the wrapper's center point falls on
        padding that misses the span. After a physical click on the wrapper is
        confirmed a no-op, retrying on this label-bearing descendant recovers the
        real control.

        Only a passive, ``cursor:pointer`` label node is returned — never a real
        ``<button>``/``<a>``/input descendant, whose click would be a *different*,
        separately-named action. The caller gates this on the wrapper itself being
        cursor-only, so normal controls never enter here.
        """
        if not is_cursor_pointer_only(node):
            return None
        queue = list(node.children)
        hops = 0
        while queue and hops < 200:
            hops += 1
            child = queue.pop(0)
            if not child.is_element:
                continue
            queue.extend(child.children)
            if child.hidden or not child.visible or not child.bbox:
                continue
            if child.backend_node_id == node.backend_node_id:
                continue
            if not is_cursor_pointer_only(child):
                continue
            if not (child.ax_name or has_text(child)):
                continue
            # Prefer a node that carries its own direct text run (the label span),
            # not an intermediate wrapper that merely contains it.
            if any(c.is_text and c.text for c in child.children):
                return child.backend_node_id
        return None

    # ------------------------------------------------------------------ #
    # interaction, split by function
    #
    # original ``interact`` is removed. The methods
    # below are a functionally split surface: each begins from the same
    # ``_begin_interaction`` snapshot, runs one category-specific action
    # (``_run_*``) and shares one post-processing path
    # (``_finish_interaction``).
    # ------------------------------------------------------------------ #
    # 元素真实类别 → 建议改用的工具名（用于类别不匹配时的提示）。
    _SPLIT_CATEGORY_TOOL = {
        "click": "tool_01_click",
        "clickdropdown": "tool_08_click_dropdown",
        "input": "tool_02_type_in_content",
        "searchable": "tool_07_searchable_dropdown",
        "select": "tool_05_type_in_select",
        "scroll": "tool_03_scroll",
        "drag": "tool_04_drag",
    }

    def _begin_interaction(
        self, name: str, allowed: tuple
    ) -> tuple[Optional[_InteractContext], Optional[dict]]:
        """串行化入口：所有互动工具都经此持有全局浏览器锁。"""
        with self._op_lock:
            return self._begin_interaction_locked(name, allowed)

    def _begin_interaction_locked(
        self, name: str, allowed: tuple
    ) -> tuple[Optional[_InteractContext], Optional[dict]]:
        """Resolve ``name`` and snapshot the pre-action state.

        Returns ``(ctx, None)`` when ready, or ``(None, error_result)`` with a
        ready-to-return failure dict. ``allowed`` is the set of element
        categories the calling method accepts (single-element for every caller,
        and ``allowed[0]`` doubles as the "attempted action" label).
        """
        try:
            self.ensure_connected()
        except (BrowserLaunchError, CDPError) as exc:
            return None, self._base_result(
                INCREMENTAL, FAIL, rp.EMPTY_CONTENT, str(exc), include_tabs=False
            )

        assert self.client is not None
        target_id = self._resolve_focused_target()
        if target_id is None:
            return None, self._base_result(
                INCREMENTAL, FAIL, rp.EMPTY_CONTENT, rp.NO_TABS, include_tabs=False
            )
        self._activate_target(target_id)

        registry = self._registry_for(target_id)
        if registry.lookup(name) is None:
            return None, self._base_result(
                INCREMENTAL, FAIL, rp.EMPTY_CONTENT, self._unknown_name_message(name), include_tabs=False
            )
        if not registry.is_active(name):
            return None, self._base_result(
                INCREMENTAL, FAIL, rp.EMPTY_CONTENT, self._stale_name_message(name), include_tabs=False
            )

        old_tree = self._prev_trees.get(target_id) or self.capture_tree(target_id)
        key = registry.lookup(name)
        node = next((n for n in old_tree.nodes if n.key == key), None)
        if node is None:
            return None, self._base_result(
                INCREMENTAL, FAIL, rp.EMPTY_CONTENT, self._stale_name_message(name), include_tabs=False
            )

        category = classify(node)
        if category not in allowed:
            target_tool = self._SPLIT_CATEGORY_TOOL.get(category, "")
            return None, self._base_result(
                INCREMENTAL,
                FAIL,
                rp.EMPTY_CONTENT,
                rp.category_mismatch(name, category, allowed[0], target_tool),
                include_tabs=False,
            )

        old_lines = self.serialize_lines_tree(old_tree, target_id)
        old_name = self._name_for_target(target_id)
        old_target_ids = {t["targetId"] for t in self._live_targets()}

        try:
            session = self.client.attach(target_id)
            self.client.enable_page_domains(session)
        except CDPError as exc:
            return None, self._base_result(
                INCREMENTAL, FAIL, rp.EMPTY_CONTENT, str(exc), include_tabs=False
            )
        executor = ActionExecutor(self.client, session)
        # Snapshot the document scroll offset so ``_finish_interaction`` can tell
        # the LLM when the action auto-scrolled the page (``scrollIntoViewIfNeeded``
        # on an off-screen target). The diff alone cannot distinguish "the action
        # revealed this" from "the viewport moved and pulled this in".
        try:
            scroll_before = executor.page_scroll_top()
        except Exception:  # noqa: BLE001
            scroll_before = None

        return (
            _InteractContext(
                name=name,
                target_id=target_id,
                registry=registry,
                node=node,
                old_tree=old_tree,
                old_lines=old_lines,
                old_name=old_name,
                old_target_ids=old_target_ids,
                session=session,
                executor=executor,
                scroll_before=scroll_before,
            ),
            None,
        )

    def _run_click(self, ctx: _InteractContext) -> dict:
        node = ctx.node
        executor = ctx.executor
        session = ctx.session
        target_id = ctx.target_id
        old_tree = ctx.old_tree
        before_sig = ""
        after_sig = ""
        click_noop = False
        popup_missing = False

        # A click may trigger a navigation; wait for it to actually begin/finish
        # instead of trusting the stale DOM's quietness.
        retry_id = (
            self._alternative_click_target(node)
            if is_cursor_pointer_only(node)
            else None
        )
        # Snapshot the DOM before acting so we can tell a real change from a
        # no-op. Park the mouse *and* blur focus first: our own hover move and
        # the click's focus shift can toggle classes / trigger re-renders,
        # changing ``outerHTML`` with no functional change.
        #
        # The one thing we must NOT neutralise is an overlay the click is
        # *inside* (an open dropdown option) or a click that *opens* a
        # self-drawn popup (``aria-haspopup`` / ``role=combobox``): blurring
        # would dismiss it before the capture.
        overlay_before = self._overlay_open(session)
        inside_overlay = (
            executor.element_inside_overlay(node.backend_node_id)
            if overlay_before
            else False
        )
        popup_trigger = (
            executor.is_popup_trigger(node.backend_node_id)
            # Every ``clickdropdown`` is *by contract* a click that opens a panel
            # (tool_08's whole purpose), and a readonly select / cascader / picker
            # face often has no ``aria-haspopup`` at all. Neutralising (blur/park)
            # after such a click dismissed the panel the click had just opened, so
            # the returned diff said "no candidates" while the panel was painted.
            or classify(node) == "clickdropdown"
        )
        if not inside_overlay and not popup_trigger:
            executor.blur_active()
            executor.park_mouse()
        before_sig = executor.dom_signature()
        # A same-document link (active nav item / in-page hash) must not pay the
        # full navigation grace: it either fires a soft event or does nothing.
        click_may_nav = may_navigate(node)
        if click_may_nav and _href_targets_same_document(node, old_tree.url):
            click_may_nav = False
        with self._handle_dialogs(session) as dialog:
            with self._watch_navigation(session) as nav_state:
                executor.click(node.backend_node_id)
                self._settle_navigation(session, nav_state, click_may_nav)
            if inside_overlay:
                # Clicked inside an already-open popup: a popup interaction is
                # itself proof the click was real.
                click_noop = False
            elif popup_trigger:
                # Keep the just-opened popup alive (no blur/park) and let it
                # paint before the capture.
                opened = self._wait_overlay_open(session)
                if not opened:
                    # The contract of this category is "clicking opens a panel".
                    # When nothing opened, one bounded retry on the same target is
                    # worth far more than a silent "成功": in the Feishu Jobs
                    # session the first click on 国籍 left ``aria-expanded=false``
                    # and the caller was told nothing, so the model re-clicked on
                    # its own — after burning a full reasoning turn reading an
                    # unrelated diff. Retrying here makes the common transient
                    # case (focus/entry-animation race) disappear, and when the
                    # retry also fails the result says so explicitly.
                    with self._watch_navigation(session) as retry_nav:
                        executor.click(node.backend_node_id)
                        self._settle_navigation(session, retry_nav, click_may_nav)
                    opened = self._wait_overlay_open(session)
                if not opened:
                    popup_missing = True
                click_noop = False
            elif (not overlay_before) and self._overlay_open(session):
                # The click opened a real overlay: leave it open for capture.
                click_noop = False
            else:
                executor.blur_active()
                executor.park_mouse()
                if before_sig:
                    after_sig = executor.dom_signature()
                    if after_sig and after_sig == before_sig:
                        # The click changed nothing. Retry on the real control:
                        # a preceding sibling icon, else the actionable
                        # descendant of a wrapper.
                        if retry_id is not None:
                            with self._watch_navigation(session) as nav_state:
                                executor.click(retry_id)
                                self._settle_navigation(session, nav_state)
                        elif is_cursor_pointer_only(node) or is_control_icon(node):
                            # A cursor-only wrapper's own center may fall on
                            # padding that misses its label, or the handler may
                            # live on the label node itself (left step-lists
                            # render a named wrapper whose inner text span owns
                            # the click). Retry on the label-bearing descendant
                            # before falling back to a wrapper-level JS click.
                            child_id = self._label_descendant_target(node)
                            if child_id is not None:
                                with self._watch_navigation(session) as nav_state:
                                    executor.click(child_id)
                                    self._settle_navigation(session, nav_state)
                                executor.blur_active()
                                executor.park_mouse()
                                child_sig = executor.dom_signature()
                                if not (child_sig and child_sig != before_sig):
                                    executor.js_click(child_id)
                                    self._stabilize(target_id)
                            else:
                                executor.js_click(node.backend_node_id)
                                self._stabilize(target_id)
                        else:
                            click_noop = True
                        if not click_noop:
                            executor.blur_active()
                            executor.park_mouse()
                            after_sig = executor.dom_signature()
                            click_noop = not (after_sig and after_sig != before_sig)
        return {
            "fill_error": "",
            "fill_notice": "",
            "dialog": dialog,
            "click_noop": click_noop,
            "popup_missing": popup_missing,
            "before_sig": before_sig,
            "after_sig": after_sig,
        }

    def _run_fill(
        self, ctx: _InteractContext, fill: str, press_enter: bool = False
    ) -> dict:
        node = ctx.node
        executor = ctx.executor
        target_id = ctx.target_id
        name = ctx.name
        fill_error = ""
        fill_notice = ""
        # A ``readonly`` control (a cascader / date picker / masked field that
        # only displays a value its own widget committed) must never be written:
        # the native-setter fallback *can* write it, which leaves the visible text
        # and the widget's model disagreeing (the form then submits the old value
        # while the screen shows the typed one). Refuse up front and point at the
        # real control instead of silently corrupting the page.
        if fill and not fill_error:
            state = executor.interaction_state(node.backend_node_id)
            if state.get("readonly"):
                fill_error = rp.readonly_fill_error(name, fill)
        if not fill and not fill_error:
            # An empty fill is an explicit *clear* request: verify it really is
            # empty afterwards instead of answering "（页面无变化）" (which the model
            # read as "nothing happened" and retried; the real session burned
            # several turns on the 微信号 field this way).
            if not executor.clear_field(node.backend_node_id):
                fill_error = rp.clear_fill_error(name)
            self._stabilize(target_id)
            return {
                "fill_error": fill_error,
                "fill_notice": fill_notice,
                "dialog": {"type": "", "message": ""},
            }
        if not fill_error:
            result = executor.input_text(node.backend_node_id, fill)
            self._stabilize(target_id)
        else:
            result = {}
        # A fill can be silently swallowed (a readonly input, or a date / time
        # picker that only accepts calendar selection). Verify the value landed
        # and fail loudly if it did not.
        if fill and not fill_error:
            if not result.get("clear_ok", True):
                # The old value could not be removed, so whatever the field shows
                # now is a concatenation, not the requested value.
                current = executor.read_value(node.backend_node_id)
                fill_error = rp.fill_clobbered_error(name, fill, current)
            else:
                current = executor.read_value(node.backend_node_id)
                date_like = _is_date_like_input(node)
                if date_like and not _looks_like_date(fill):
                    fill_error = self._date_fill_error(name, fill)
                elif not _fills_same(fill, current):
                    fill_error = rp.fill_not_effective(name, fill, current)
                elif date_like:
                    # The picker may display typed text without committing it; blur
                    # to force a re-render, then re-read.
                    executor.blur(node.backend_node_id)
                    self._stabilize(target_id)
                    current = executor.read_value(node.backend_node_id)
                    if not _fills_same(fill, current):
                        fill_error = self._date_fill_error(name, fill, current)
        if press_enter and not fill_error:
            # A bare search box may have no submit button at all: only pressing
            # Enter submits it. Treat it like a click (wait for a possible
            # navigation / async result).
            with self._watch_navigation(ctx.session) as nav_state:
                executor.press_enter()
                self._settle_navigation(ctx.session, nav_state, True)
        return {
            "fill_error": fill_error,
            "fill_notice": fill_notice,
            "dialog": {"type": "", "message": ""},
        }

    def _run_select(self, ctx: _InteractContext, fill: str) -> dict:
        node = ctx.node
        executor = ctx.executor
        target_id = ctx.target_id
        name = ctx.name
        fill_error = ""
        fill_notice = ""
        # A native ``<select>``: its popup is drawn by the browser/OS and is not
        # part of the DOM, so the only reliable operation is to select the
        # option whose text (or value) matches ``fill``.
        options = self._select_option_texts(node)
        if fill:
            executor.select_option(node.backend_node_id, fill)
            self._stabilize(target_id)
            current = executor.read_select_text(node.backend_node_id)
            if not _fills_same(fill, current):
                fill_error = self._select_fill_error(name, fill, current, options)
        else:
            # Do not click: it would open an uncapturable native popup and still
            # report "（页面无变化）". Explain the operation instead.
            fill_notice = self._select_click_notice(name, options)
        return {
            "fill_error": fill_error,
            "fill_notice": fill_notice,
            "dialog": {"type": "", "message": ""},
        }

    def _run_searchable(self, ctx: _InteractContext, fill: str) -> dict:
        """Filter a searchable select's typeahead and surface the candidates.

        Typing only narrows the candidate list; the value is committed by a
        later ``tool_01_click`` on a candidate, so this never reports a fill
        success or failure. ``input_text`` clicks/focuses first, which is what
        opens the dropdown. An empty ``fill`` means "show the full list": click
        to open, then wipe any leftover filter from a previous call (``input_text``
        deliberately does not clear on an empty fill).
        """
        node = ctx.node
        executor = ctx.executor
        if fill:
            executor.input_text(node.backend_node_id, fill)
        else:
            executor.input_text(node.backend_node_id, "")
            executor.clear_field(node.backend_node_id)
        self._stabilize(ctx.target_id)
        # A dropdown mounts on focus/typing and paints a frame or two later;
        # give it a bounded moment so its candidates are captured.
        self._wait_overlay_open(ctx.session)
        # A *range* control (``从 __ 到 __``) is one widget whose value commits
        # only after TWO candidate picks inside this same overlay session; the
        # widget sorts the pair and a single pick followed by a blur rolls both
        # ends back. Say so here, at the moment the panel is opened — the model
        # otherwise treats the two ends as independent fields.
        fill_notice = rp.range_picker_notice(ctx.name) if is_range_picker_entry(node) else ""
        return {
            "fill_error": "",
            "fill_notice": fill_notice,
            "dialog": {"type": "", "message": ""},
        }

    def _run_drag(self, ctx: _InteractContext, drag_pct: int) -> dict:
        ctx.executor.drag(ctx.node.backend_node_id, drag_pct)
        self._stabilize(ctx.target_id)
        return {
            "fill_error": "",
            "fill_notice": "",
            "dialog": {"type": "", "message": ""},
        }

    def _run_scroll(self, ctx: _InteractContext, scroll_delta: int) -> dict:
        node = ctx.node
        executor = ctx.executor
        target_id = ctx.target_id
        scroller = (
            _PageScroller(executor)
            if node.tag == PAGE_SCROLL_TAG
            else _ContainerScroller(executor, node.backend_node_id)
        )
        direction, steps = self._scroll_plan(scroll_delta)
        diff = self._scroll_and_collect(
            scroller,
            target_id,
            ctx.old_lines,
            ctx.registry,
            direction=direction,
            max_steps=steps,
        )
        notice = self._title_change_notice(target_id, ctx.old_name).strip()
        content = "\n\n".join(p for p in (notice, diff) if p)
        return self._base_result(INCREMENTAL, OK, content, include_tabs=False)

    def _finish_interaction(self, ctx: _InteractContext, extras: dict) -> dict:
        target_id = ctx.target_id
        old_tree = ctx.old_tree
        old_lines = ctx.old_lines
        old_name = ctx.old_name
        old_target_ids = ctx.old_target_ids
        fill_error = extras.get("fill_error", "")
        fill_notice = extras.get("fill_notice", "")
        dialog = extras.get("dialog") or {"type": "", "message": ""}
        click_noop = bool(extras.get("click_noop", False))
        before_sig = extras.get("before_sig", "")
        after_sig = extras.get("after_sig", "")

        # 点击（非零变化）成功 → 该元素不是死链，清零连续死点击计数。亦在聚焦标签页
        # 切换（下面提前返回）之前处理。仅当 extras 带 click_noop 键（即本次是点击类
        # 互动）时才算，fill / scroll 不重置。
        if "click_noop" in extras and not click_noop:
            self._noop_counts.pop(ctx.node.key, None)

        new_focus = self._resolve_focused_target()
        if new_focus and new_focus != target_id:
            new_tree = self.capture_tree(new_focus)
            new_text = self.serialize_tree(new_tree, new_focus)
            if self._is_blank_shell(new_tree):
                new_text = self._blank_notice(new_tree) + new_text
            return self._base_result(
                FULL,
                FAIL if fill_error else OK,
                self._dialog_notice(dialog) + (fill_notice + "\n\n" if fill_notice else "") + new_text,
                error=fill_error or rp.NO_ERROR,
                include_tabs=True,
            )


        new_tree = self.capture_tree(target_id)
        notice = (
            self._dialog_notice(dialog)
            + (fill_notice + "\n\n" if fill_notice else "")
            + self._title_change_notice(target_id, old_name)
        )
        content = self._incremental_content(
            old_lines, new_tree, target_id, notice, old_url=old_tree.url
        )
        scroll_note = self._viewport_scroll_note(ctx)
        if scroll_note:
            content = scroll_note + "\n\n" + content
        if extras.get("popup_missing"):
            # Honest failure beats a truthful-looking "成功": the model must not
            # believe a dropdown was opened (and therefore that a value was
            # chosen) when no panel exists.
            content = rp.popup_not_opened_notice(ctx.name) + "\n\n" + content

        # The click may have opened a background tab without moving focus. The
        # focused page then looks unchanged, so tell the LLM explicitly and hand
        # it the full tab list instead.
        opened_names = self._opened_tab_names(old_target_ids)

        if not opened_names and click_noop:
            opened_names = self._await_opened_tabs(old_target_ids)
            if not opened_names:
                new_lines = self.serialize_lines_tree(new_tree, target_id)
                if not _lines_have_new_text(old_lines, new_lines):
                    key = ctx.node.key
                    count = self._noop_counts.get(key, 0) + 1
                    self._noop_counts[key] = count
                    content = rp.click_noop_notice(ctx.name, count) + "\n\n" + content

        # A click that closes a popup / clears a selection removes content; the
        # diff only reports additions, so say so explicitly rather than a
        # misleading "（页面无变化）".
        elif not opened_names and before_sig and rp.PAGE_NO_CHANGE in content:
            if after_sig and after_sig != before_sig:
                content += rp.STRUCTURE_CHANGED_NOTICE
            else:
                opened_names = self._await_opened_tabs(old_target_ids)
        if opened_names:
            extra = self._new_tab_notice(opened_names)
            # When the focused page did change, keep it and append the hint;
            # when it did not, the new tab *is* the outcome.
            content = extra if rp.PAGE_NO_CHANGE in content else content + "\n\n" + extra
            return self._base_result(
                INCREMENTAL, FAIL if fill_error else OK, content,
                error=fill_error or rp.NO_ERROR, include_tabs=True,
            )
        return self._base_result(
            INCREMENTAL, FAIL if fill_error else OK, content,
            error=fill_error or rp.NO_ERROR, include_tabs=False,
        )

    def interact_click(self, name: str) -> dict:
        """与单个『可点击元素』互动，返回增量 DOM。"""
        ctx, err = self._begin_interaction(name, allowed=("click",))
        if err is not None:
            return err
        assert ctx is not None
        try:
            extras = self._run_click(ctx)
        except (ActionError, CDPError) as exc:
            return self._base_result(INCREMENTAL, FAIL, rp.EMPTY_CONTENT, str(exc), include_tabs=False)
        return self._finish_interaction(ctx, extras)

    def interact_click_dropdown(self, name: str) -> dict:
        """点击单个『可点击下拉元素』，使其展开候选/菜单，返回增量 DOM。

        与 ``interact_click`` 共用同一套点击与浮层兜底逻辑（点击后保留刚打开的
        浮层以便捕获候选），区别只是类别与工具：候选项仍由 ``tool_01_click`` 点选。
        """
        ctx, err = self._begin_interaction(name, allowed=("clickdropdown",))
        if err is not None:
            return err
        assert ctx is not None
        try:
            extras = self._run_click(ctx)
        except (ActionError, CDPError) as exc:
            return self._base_result(INCREMENTAL, FAIL, rp.EMPTY_CONTENT, str(exc), include_tabs=False)
        if is_range_picker_entry(ctx.node):
            # A readonly range picker (readonly date range / non-searchable select
            # pair): same two-pick contract as tool_07 — say it when the panel opens.
            extras = dict(extras or {})
            extras["fill_notice"] = rp.range_picker_notice(name)
        return self._finish_interaction(ctx, extras)

    def interact_fill_in(
        self, name: str, fill: str = "", press_enter: bool = False
    ) -> dict:
        """对单个『可输入元素』填写内容（可选填完按回车），返回增量 DOM。"""
        ctx, err = self._begin_interaction(name, allowed=("input",))
        if err is not None:
            return err
        assert ctx is not None
        try:
            extras = self._run_fill(ctx, fill, press_enter)
        except (ActionError, CDPError) as exc:
            return self._base_result(INCREMENTAL, FAIL, rp.EMPTY_CONTENT, str(exc), include_tabs=False)
        return self._finish_interaction(ctx, extras)

    def interact_select(self, name: str, fill: str = "") -> dict:
        """对单个『可选择元素』（原生下拉）选择选项文字，返回增量 DOM。"""
        ctx, err = self._begin_interaction(name, allowed=("select",))
        if err is not None:
            return err
        assert ctx is not None
        try:
            extras = self._run_select(ctx, fill)
        except (ActionError, CDPError) as exc:
            return self._base_result(INCREMENTAL, FAIL, rp.EMPTY_CONTENT, str(exc), include_tabs=False)
        return self._finish_interaction(ctx, extras)

    def interact_searchable_fill_in(self, name: str, fill: str = "") -> dict:
        """对单个『可搜索下拉元素』输入筛选词并返回候选项，返回增量 DOM。

        输入只用于筛选（收缩）下拉候选项，输入本身不作数；必须再调用
        ``tool_01_click`` 点击候选项才算填入。``fill`` 为空字符串时清空筛选、
        展示完整候选列表。
        """
        ctx, err = self._begin_interaction(name, allowed=("searchable",))
        if err is not None:
            return err
        assert ctx is not None
        try:
            extras = self._run_searchable(ctx, fill)
        except (ActionError, CDPError) as exc:
            return self._base_result(INCREMENTAL, FAIL, rp.EMPTY_CONTENT, str(exc), include_tabs=False)
        return self._finish_interaction(ctx, extras)

    def interact_scroll(self, name: str, scroll_delta: int = 0) -> dict:
        """对单个『可滚动元素』滚动，返回滚动新增内容。"""
        ctx, err = self._begin_interaction(name, allowed=("scroll",))
        if err is not None:
            return err
        assert ctx is not None
        try:
            return self._run_scroll(ctx, scroll_delta)
        except (ActionError, CDPError) as exc:
            return self._base_result(INCREMENTAL, FAIL, rp.EMPTY_CONTENT, str(exc), include_tabs=False)

    def interact_drag(self, name: str, drag_pct: int = 0) -> dict:
        """对单个『可拖动元素』拖动到指定百分比位置，返回增量 DOM。"""
        ctx, err = self._begin_interaction(name, allowed=("drag",))
        if err is not None:
            return err
        assert ctx is not None
        try:
            extras = self._run_drag(ctx, drag_pct)
        except (ActionError, CDPError) as exc:
            return self._base_result(INCREMENTAL, FAIL, rp.EMPTY_CONTENT, str(exc), include_tabs=False)
        return self._finish_interaction(ctx, extras)

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
        moved = False
        at_boundary = False
        blocked = False

        for _ in range(max(1, max_steps)):
            top = scroller.scroll_step(signed_step)
            if top is None:
                break
            if last_top is not None and abs(top - last_top) < 1:
                # This step did not move the scroller. Confirm a *real* boundary
                # (scrollTop+clientHeight >= scrollHeight) before claiming
                # "已到底/顶": a smooth-scroll page (or a swallowed programmatic
                # scroll) can leave the offset unchanged on the first read while
                # plenty of content remains. Only a confirmed boundary — or a
                # deliberate "blocked" report — may be returned, never a lie.
                if scroller.at_bottom() if down else scroller.at_top():
                    at_boundary = True
                elif not moved:
                    blocked = True
                break
            last_top = top
            moved = True

            tree = self.capture_tree(target_id)
            lines = self.serialize_lines_tree(tree, target_id)
            for line in changed_lines(prev_lines, lines):
                key = (line.depth, line.text)
                if key not in seen:
                    seen.add(key)
                    added.append(line)
            prev_lines = lines

            if (scroller.at_bottom() if down else scroller.at_top()):
                at_boundary = True
                break

        added = _dedupe_scroll_fragments(added)
        if not added:
            if at_boundary:
                return rp.scroll_at_boundary(down)
            if blocked:
                return rp.scroll_blocked(down)
            if not moved:
                # Tell the model *why* nothing changed instead of a bare
                # "（页面无变化）" it cannot act on: it is already at the end, or
                # the element is not a scroller.
                return rp.scroll_no_new_content(down)
            return rp.PAGE_NO_CHANGE
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
        return rp.unknown_name_message(name, focus, other)

    def _stale_name_message(self, name: str) -> str:
        focus = self.focused_tab_label()
        other = self._foreign_tab_for_name(name, self.focused_target_id or "")
        return rp.stale_name_message(name, focus, other)

    # ------------------------------------------------------------------ #
    # batch interaction (tool-06)
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
                INCREMENTAL, FAIL, rp.EMPTY_CONTENT, rp.INTERACT_MANY_EMPTY, include_tabs=False
            )
        if n_fills not in (n_names, n_names - 1):
            return self._base_result(
                INCREMENTAL,
                FAIL,
                rp.EMPTY_CONTENT,
                rp.INTERACT_MANY_LENGTH,
                include_tabs=False,
            )

        has_click = n_names == n_fills + 1
        fill_names = names[:n_fills]
        click_name = names[-1] if has_click else None

        try:
            self.ensure_connected()
        except (BrowserLaunchError, CDPError) as exc:
            return self._base_result(INCREMENTAL, FAIL, rp.EMPTY_CONTENT, str(exc), include_tabs=False)

        assert self.client is not None
        target_id = self._resolve_focused_target()
        if target_id is None:
            return self._base_result(
                INCREMENTAL, FAIL, rp.EMPTY_CONTENT, rp.NO_TABS, include_tabs=False
            )
        self._activate_target(target_id)

        registry = self._registry_for(target_id)
        tree = self._prev_trees.get(target_id) or self.capture_tree(target_id)

        # ---- up-front validation (nothing runs if this fails) ----
        plan: list[tuple[str, str, int]] = []  # (name, category, backendNodeId)
        fill_nodes: dict[str, EnhancedNode] = {}
        for name in fill_names:
            node, err = self._resolve_interactive_node(registry, tree, name)
            if err:
                return self._base_result(INCREMENTAL, FAIL, rp.EMPTY_CONTENT, err, include_tabs=False)
            cat = classify(node)
            if cat not in ("input", "select"):
                # A searchable select (``searchable``) is not batch-fillable:
                # typing only filters, so it needs its own tool (``tool_07``).
                return self._base_result(
                    INCREMENTAL,
                    FAIL,
                    rp.EMPTY_CONTENT,
                    rp.not_fillable(name, cat),
                    include_tabs=False,
                )
            fill_nodes[name] = node
            plan.append((name, cat, node.backend_node_id))
        click_may_nav = False
        if has_click:
            node, err = self._resolve_interactive_node(registry, tree, click_name)
            if err:
                return self._base_result(INCREMENTAL, FAIL, rp.EMPTY_CONTENT, err, include_tabs=False)
            click_cat = classify(node)
            if click_cat not in ("click", "clickdropdown"):
                return self._base_result(
                    INCREMENTAL,
                    FAIL,
                    rp.EMPTY_CONTENT,
                    rp.trailing_click_not_clickable(click_name, click_cat),
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
            return self._base_result(INCREMENTAL, FAIL, rp.EMPTY_CONTENT, str(exc), include_tabs=False)

        executor = ActionExecutor(self.client, session)
        success_count = 0
        error_msg = ""

        def _run_plan() -> None:
            nonlocal success_count, error_msg
            for index, (_name, category, backend_node_id) in enumerate(plan):
                try:
                    if category == "input":
                        # A readonly control (cascader / picker / masked field)
                        # must not be written: the native-setter fallback *can*
                        # write it and then the visible text and the widget's own
                        # model disagree. Validate before touching the page.
                        if fills[index] and executor.interaction_state(backend_node_id).get(
                            "readonly"
                        ):
                            error_msg = rp.readonly_fill_error(_name, fills[index])
                            break
                        executor.input_text(backend_node_id, fills[index])
                    elif category == "select":
                        executor.select_option(backend_node_id, fills[index])
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
                        INCREMENTAL, FAIL, rp.EMPTY_CONTENT, error_msg, include_tabs=False
                    )
                self._settle_navigation(session, nav_state, click_may_nav)
        else:
            _run_plan()
            if error_msg and success_count == 0:
                return self._base_result(
                    INCREMENTAL, FAIL, rp.EMPTY_CONTENT, error_msg, include_tabs=False
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
        failed_selects: list[tuple[str, str, Optional[str], list]] = []
        if not error_msg and not focus_changed and not opened_names:
            # Date/time pickers display typed text without necessarily committing
            # it; blur them (forcing the component to re-render) before reading,
            # so a reverted value is detected below.
            date_like_ids = [
                fbid
                for (_n, fcat, fbid), fval in zip(plan, fills)
                if fcat == "input" and fval and _is_date_like_input(fill_nodes.get(_n))
            ]
            for fbid in date_like_ids:
                executor.blur(fbid)
            if date_like_ids:
                self._stabilize(target_id)
            for (fname, fcat, fbid), fval in zip(plan, fills):
                if fcat == "select":
                    current = executor.read_select_text(fbid)
                    if not _fills_same(fval, current):
                        node = fill_nodes.get(fname)
                        failed_selects.append(
                            (fname, fval, current, self._select_option_texts(node))
                        )
                    continue
                if fcat != "input":
                    continue
                node = fill_nodes.get(fname)
                if not fval:
                    # An empty fill is a *clear* request: it must be verified too,
                    # otherwise "clear" silently did nothing and the batch still
                    # reported 成功 (the real session cleared 微信号 by accident
                    # with a space, then could not tell whether it had worked).
                    if not executor.clear_field(fbid):
                        failed_fills.append((fname, executor.read_value(fbid)))
                    continue
                current = executor.read_value(fbid)
                if _is_date_like_input(node) and not _looks_like_date(fval):
                    # Nonsense text on a picker (``至今``): never committed.
                    failed_fills.append((fname, current))
                    continue
                if _fills_same(fval, current):
                    continue
                # A controlled field can end up holding a *different* value when
                # a re-render desynced the fill target (a long description can
                # land in the neighbouring textarea). Correct once with a direct
                # value set, then re-verify; only report failure when the field
                # genuinely resists (readonly / date picker) so the LLM is not
                # sent chasing a phantom mismatch.
                executor.set_value(fbid, fval)
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
            # A trailing click may have opened a background tab whose target is
            # registered a beat late (see ``_await_opened_tabs``). Re-check now
            # (the capture above already spent some time), then poll if the
            # focused page shows no change at all.
            if has_click:
                late = self._opened_tab_names(old_target_ids)
                if late and not opened_names:
                    opened_names = late
                    extra = self._new_tab_notice(late)
                    content = (
                        extra if rp.PAGE_NO_CHANGE in content else content + "\n\n" + extra
                    )
                if not opened_names and rp.PAGE_NO_CHANGE in content:
                    opened_names = self._await_opened_tabs(old_target_ids)
                    if opened_names:
                        content = self._new_tab_notice(opened_names)
            mode = INCREMENTAL

        include_tabs = focus_changed or bool(opened_names)
        if error_msg:
            action_ok = PARTIAL_FAIL if success_count > 0 else FAIL
            return self._base_result(
                mode, action_ok, content, error=error_msg, include_tabs=include_tabs
            )
        if failed_fills or failed_selects:
            chunks: list[str] = []
            if failed_fills:
                chunks.append(rp.failed_fills_message(failed_fills))
            if failed_selects:
                chunks.append(
                    rp.failed_selects_message(
                        [
                            rp.select_fill_error(n, fval, c, opts)
                            for n, fval, c, opts in failed_selects
                        ]
                    )
                )
            return self._base_result(
                mode, PARTIAL_FAIL, content, error=" ".join(chunks), include_tabs=include_tabs
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
        self._wait_pending_motion(session)

    # A popup (dropdown / menu / date panel) is mounted first and its open
    # animation starts on the *next* animation frame. In a backgrounded or
    # otherwise throttled tab that frame can be delayed by hundreds of
    # milliseconds, so the DOM looks quiet (its classes are stable at the
    # ``*-prepare`` first frame) while the popup is still fully transparent and
    # unlaid-out — a capture then drops its options entirely and the model is
    # told “（页面无变化）” after clicking a select. Give such a pending motion a
    # short, bounded chance to finish; if it never does, the serializer still
    # surfaces the transparent subtree (``_effective_opacity_zero``).
    _MOTION_SETTLE_SECONDS = 0.35
    _PENDING_MOTION_PROBE = (
        "(() => { try {"
        "const cls = document.querySelectorAll("
        "'[class*=\"-enter-prepare\"],[class*=\"-appear-prepare\"],"
        "[class*=\"-enter-active\"],[class*=\"-appear-active\"]').length;"
        "const anim = document.getAnimations().filter(a => {"
        "const t = a.effect && a.effect.getComputedTiming && a.effect.getComputedTiming();"
        "return a.playState === 'running' && t && isFinite(t.endTime) && t.endTime < 3000;"
        "}).length; return cls + anim; } catch (e) { return 0; } })()"
    )

    def _wait_pending_motion(self, session: str) -> None:
        assert self.client is not None
        deadline = time.time() + self._MOTION_SETTLE_SECONDS
        while time.time() < deadline:
            try:
                result = self.client.send(
                    "Runtime.evaluate",
                    {"expression": self._PENDING_MOTION_PROBE, "returnByValue": True},
                    session_id=session,
                    timeout=timing.get().cdp.probe_timeout,
                )
                pending = int((result.get("result") or {}).get("value") or 0)
            except Exception:
                return
            if pending <= 0:
                return
            time.sleep(timing.get().nav.settle_poll_interval)

    # A click may open a real, *interactive* overlay (a custom dropdown / menu /
    # calendar / popup). The click branch neutralises the page afterwards
    # (``blur_active`` + ``park_mouse``) so its own hover / focus noise does not
    # defeat the no-op signature — but that same neutralisation *dismisses* the
    # overlay (component selects close on blur), so the option list never reached
    # the capture and the model saw "（页面无变化）" while the options sat on
    # screen. This probe lets the caller keep the overlay open for the capture.
    #
    # The expression lives in ``actions`` (``overlay_probe_expression``) so the
    # controller probe and the executor's "is this click inside a popup?" check
    # share one definition: a candidate must be a *visible, sized, on-viewport*
    # popup container that is **not itself an interactive control**. That last
    # clause is essential — Ant Design gives every select trigger a class that
    # contains "dropdown" (``.ant-dropdown-trigger``, the header login control),
    # and naive ``[class*="dropdown"]`` matching made this probe permanently true,
    # which dead-locked the before/after differential and closed every dropdown.
    def _overlay_open(self, session: str) -> bool:
        """True if an interactive popup is currently visible on screen."""
        assert self.client is not None
        try:
            result = self.client.send(
                "Runtime.evaluate",
                {"expression": overlay_probe_expression(), "returnByValue": True},
                session_id=session,
                timeout=timing.get().cdp.probe_timeout,
            )
            return bool((result.get("result") or {}).get("value"))
        except Exception:
            return False

    def _wait_overlay_open(self, session: str, timeout: float = 0.6) -> bool:
        """Poll briefly for a popup to become visible after a trigger click.

        A self-drawn dropdown is mounted on the click but painted a frame or two
        later; a single instantaneous probe can miss it. Bounded so a dead
        trigger click is not delayed by more than ``timeout``.
        """
        deadline = time.time() + timeout
        while True:
            if self._overlay_open(session):
                return True
            if time.time() >= deadline:
                return False
            time.sleep(0.05)

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

    def _wait_for_content(
        self, session: str, quiet: bool = True, wait_content: bool = True
    ) -> None:
        """Wait out a not-yet-rendered SPA shell.

        After a click that triggers an async render (no document navigation), the
        DOM can be perfectly quiet yet still empty: ``readyState == "complete"``
        and a stable ``<html><head>…</head></html>`` skeleton. The normal quiet
        check then settles immediately and the tool returns a seemingly empty
        page. When ``<body>`` has no element children we keep polling up to
        ``nav.load_timeout`` for the first content to appear, then re-check
        quietness. Non-empty pages are unaffected.

        ``wait_content=False`` skips the empty-body wait entirely and only runs
        the quiet check when ``quiet=True``. ``full_dom`` (the "提示接管" preview
        and ``tool_10_get_full_viewport``) uses it: a blank page is returned as
        a blank notice immediately instead of blocking up to ``nav.load_timeout``
        (15s) on every poll — the front-end polls once a second and will pick up
        content as soon as the page renders.
        """
        assert self.client is not None
        cfg = timing.get()
        empty = self._body_child_count(session) == 0
        if empty and wait_content:
            deadline = time.time() + cfg.nav.load_timeout
            while time.time() < deadline:
                time.sleep(cfg.nav.settle_poll_interval)
                if self._body_child_count(session) > 0:
                    break
        if quiet:
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

    @staticmethod
    def _blank_notice(tree: EnhancedTree) -> str:
        """Notice for an empty tree: distinguish a reset about:blank from loading."""
        url = (getattr(tree, "url", "") or "").strip()
        if not url or url == "about:blank":
            return rp.ABOUT_BLANK_NOTICE
        return rp.BLANK_SHELL_NOTICE

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

    @contextlib.contextmanager
    def _handle_dialogs(self, session: str):
        """Auto-handle native JS dialogs so an action can never hang on one.

        A ``beforeunload`` confirmation ("重新加载此网站？系统可能不会保留您所做
        的更改") is a **native** dialog: it is invisible to the tool, blocks the
        renderer, and makes ``Page.reload`` / navigation wait forever. CDP surfaces
        it as ``Page.javascriptDialogOpening`` and only ``Page.handleJavaScriptDialog``
        dismisses it.

        ``beforeunload`` / ``alert`` are accepted (a deliberate tool navigation must
        be allowed to proceed; an alert has nothing to decide). ``confirm`` /
        ``prompt`` are also accepted — the user's click was the intent — but the
        dialog text is recorded and returned so the model can see what happened.

        The event handler runs on the CDP **reader thread**; sending from there can
        deadlock the websocket client, so it only records the dialog and wakes a
        dedicated worker thread that issues ``Page.handleJavaScriptDialog``.
        """
        assert self.client is not None
        seen = {"type": "", "message": ""}
        wake = threading.Event()
        stop = threading.Event()

        def handle(params: dict) -> None:
            if params.get("__sessionId") != session:
                return
            dtype = params.get("type", "")
            seen["type"] = dtype
            seen["message"] = params.get("message", "") or params.get("defaultPrompt", "") or ""
            wake.set()

        def worker() -> None:
            while not stop.is_set():
                if not wake.wait(0.2):
                    continue
                wake.clear()
                try:
                    self.client.send(
                        "Page.handleJavaScriptDialog",
                        {"accept": seen["type"] != "prompt", "promptText": ""},
                        session_id=session,
                        timeout=timing.get().cdp.input_timeout,
                    )
                except Exception:
                    pass

        self.client.on("Page.javascriptDialogOpening", handle)
        thread = threading.Thread(target=worker, name="cdp-dialog", daemon=True)
        thread.start()
        try:
            yield seen
        finally:
            self.client.off("Page.javascriptDialogOpening", handle)
            stop.set()
            wake.set()
            thread.join(timeout=1.0)

    @staticmethod
    def _dialog_notice(seen: dict) -> str:
        return rp.dialog_notice(seen)

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
        error: str = rp.NO_ERROR,
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
        # 串行化：前端「提示接管」每秒轮询一次，且「打开浏览器」也会走这里，
        # 并发抓取会拖垮 CDP 连接。持有全局锁，一次只跑一个。
        with self._op_lock:
            return self._full_dom_impl(action_ok)

    def _full_dom_impl(self, action_ok: str = NOT_CALLED) -> dict:
        try:
            self.ensure_connected()
            target_id = self._resolve_focused_target()
            if target_id is None:
                return self._base_result(FULL, FAIL, rp.EMPTY_CONTENT, rp.NO_TABS)
            session = self.client.attach(target_id)
            self.client.enable_page_domains(session)
            # 预览/全量快照：空白页立即返回「空白页」提示，不空等 15s（前端每秒
            # 轮询，页面渲染出来后自然会拿到内容）。判稳静默也不做（只取快照）。
            self._wait_for_content(session, quiet=False, wait_content=False)
            tree = self.capture_tree(target_id)
            content = self.serialize_tree(tree, target_id)
            if self._is_blank_shell(tree):
                content = self._blank_notice(tree) + content
            return self._base_result(FULL, action_ok, content)
        except (BrowserLaunchError, CDPError, RuntimeError) as exc:
            return self._base_result(FULL, FAIL, rp.EMPTY_CONTENT, str(exc))

    def list_tabs(self) -> dict:
        try:
            self.ensure_connected()
            return {"tabs": self.tab_labels()}
        except (BrowserLaunchError, CDPError) as exc:
            return {"tabs": [], "error": str(exc)}

    # ------------------------------------------------------------------ #
    # navigation / tab tools ( tool-3x)
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
            content = self._blank_notice(tree) + content
        return self._base_result(FULL, action_ok, content)

    def switch_tab(self, tab_id: str) -> dict:
        """tool_21_switch_tab: focus an existing tab and return its full DOM."""
        try:
            self.ensure_connected()
        except (BrowserLaunchError, CDPError) as exc:
            return self._base_result(FULL, FAIL, rp.EMPTY_CONTENT, str(exc))
        assert self.client is not None
        target_id = self._resolve_tab(tab_id)
        if target_id is None:
            return self._base_result(FULL, FAIL, rp.EMPTY_CONTENT, rp.tab_not_found(tab_id))
        try:
            self._activate_target(target_id)
            self._wait_ready(target_id)
            actual = self._resolve_focused_target()
            if actual is not None and actual != target_id:
                # The tab's document may have finished loading only now (a page
                # opened by the site's own ``window.open`` is frequently not
                # activatable until then). Give activation a second bounded
                # chance before declaring failure.
                self._activate_target(target_id)
                actual = self._resolve_focused_target()
        except (CDPError, RuntimeError) as exc:
            return self._base_result(FULL, FAIL, rp.EMPTY_CONTENT, str(exc))
        result = self._full_result_for(target_id)
        # If the browser refused to foreground the requested tab, do not pretend
        # the switch succeeded (that trapped the LLM in a switch/interact loop):
        # return the tab that is *actually* focused, with an explicit notice.
        if actual is not None and actual != target_id:
            result = self._full_result_for(actual)
            result["content"] = self._focus_mismatch_notice(target_id, actual) + result["content"]
        return result

    def go_back(self) -> dict:
        """tool_30_go_back: browser back button, return full DOM."""
        try:
            self.ensure_connected()
        except (BrowserLaunchError, CDPError) as exc:
            return self._base_result(FULL, FAIL, rp.EMPTY_CONTENT, str(exc))
        assert self.client is not None
        target_id = self._resolve_focused_target()
        if target_id is None:
            return self._base_result(FULL, FAIL, rp.EMPTY_CONTENT, rp.NO_TABS)
        self._activate_target(target_id)
        old_name = self._name_for_target(target_id)
        session = self.client.attach(target_id)
        self.client.enable_page_domains(session)
        try:
            history = self.client.send("Page.getNavigationHistory", {}, session_id=session)
        except CDPError as exc:
            return self._base_result(FULL, FAIL, rp.EMPTY_CONTENT, str(exc))
        index = history.get("currentIndex", 0)
        entries = history.get("entries", [])
        if index <= 0 or index >= len(entries):
            return self._base_result(FULL, FAIL, rp.EMPTY_CONTENT, rp.GO_BACK_NO_HISTORY)
        try:
            with self._handle_dialogs(session) as dialog:
                with self._watch_navigation(session) as nav_state:
                    self.client.send(
                        "Page.navigateToHistoryEntry",
                        {"entryId": entries[index - 1]["id"]},
                        session_id=session,
                    )
                    self._settle_navigation(session, nav_state)
        except CDPError as exc:
            return self._base_result(FULL, FAIL, rp.EMPTY_CONTENT, str(exc))
        result = self._full_result_for(target_id)
        result["content"] = self._dialog_notice(dialog) + self._title_change_notice(target_id, old_name) + result["content"]
        return result

    def refresh(self) -> dict:
        """tool_31_refresh: reload current tab, return incremental diff."""
        try:
            self.ensure_connected()
        except (BrowserLaunchError, CDPError) as exc:
            return self._base_result(INCREMENTAL, FAIL, rp.EMPTY_CONTENT, str(exc), include_tabs=False)
        assert self.client is not None
        target_id = self._resolve_focused_target()
        if target_id is None:
            return self._base_result(INCREMENTAL, FAIL, rp.EMPTY_CONTENT, rp.NO_TABS, include_tabs=False)
        self._activate_target(target_id)
        old_tree = self._prev_trees.get(target_id) or self.capture_tree(target_id)
        old_lines = self.serialize_lines_tree(old_tree, target_id)
        old_name = self._name_for_target(target_id)
        session = self.client.attach(target_id)
        self.client.enable_page_domains(session)
        try:
            with self._handle_dialogs(session) as dialog:
                with self._watch_navigation(session) as nav_state:
                    self.client.send("Page.reload", {"ignoreCache": False}, session_id=session)
                    self._settle_navigation(session, nav_state)
        except CDPError as exc:
            return self._base_result(INCREMENTAL, FAIL, rp.EMPTY_CONTENT, str(exc), include_tabs=False)
        new_tree = self.capture_tree(target_id)
        notice = self._dialog_notice(dialog) + self._title_change_notice(target_id, old_name)
        content = self._incremental_content(
            old_lines, new_tree, target_id, notice, old_url=old_tree.url
        )
        return self._base_result(INCREMENTAL, OK, content, include_tabs=False)

    def close_tab(self, tab_id: str) -> dict:
        """tool_32_close_tab: close a tab, return the remaining tab list."""
        try:
            self.ensure_connected()
        except (BrowserLaunchError, CDPError) as exc:
            return {"tabs": [], "error": str(exc)}
        assert self.client is not None
        target_id = self._resolve_tab(tab_id)
        if target_id is None:
            return {"tabs": self.tab_labels(), "error": rp.tab_not_found(tab_id)}
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
        """tool_33_navigate: open a new tab at the given URL, return its full DOM."""
        try:
            self.ensure_connected()
        except (BrowserLaunchError, CDPError) as exc:
            return self._base_result(FULL, FAIL, rp.EMPTY_CONTENT, str(exc))
        assert self.client is not None
        target_url = (url or "").strip()
        if not target_url:
            return self._base_result(FULL, FAIL, rp.EMPTY_CONTENT, rp.EMPTY_URL)
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
            return self._base_result(FULL, FAIL, rp.EMPTY_CONTENT, str(exc))
        self.focused_target_id = target_id
        try:
            self._activate_target(target_id)
            self._wait_ready_new_tab(target_id, target_url)
            result = self._full_result_for(target_id)
        except CDPError as exc:
            # A freshly created target can briefly be un-attachable. Return a
            # clear, recoverable message (with the tab list) instead of leaking a
            # raw CDP error, so the LLM can retry rather than treat the browser
            # as broken.
            return self._base_result(
                FULL, FAIL, rp.EMPTY_CONTENT, rp.new_tab_unreachable(exc), include_tabs=True
            )
        actual = self._resolve_focused_target()
        if actual is not None and actual != target_id:
            result = self._full_result_for(actual)
            result["content"] = self._focus_mismatch_notice(target_id, actual) + result["content"]
        return result
