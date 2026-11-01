"""Browser session state behind the tool API.

ID91：默认仍提供进程内单例 ``BrowserController.instance()``；app 层也可以为每个
会话显式创建独立实例（独立 profile / CDP 端口），互不共享标签页 LRU 或暂停状态。
"""

from __future__ import annotations

import contextlib
import functools  # BID084
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Optional

from . import debug, studio_marker, timing
from . import bid065_trace  # BID065
from . import finalize as finalize_output  # BID084
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
    full_view_lines,  # BID079
    has_text,
    is_control_icon,
    is_cursor_pointer_only,
    is_custom_select_input,  # BID085
    is_range_picker_end,
    is_range_picker_entry,
    may_navigate,
    picker_range_position,
    prune_image_lines,  # BID080
    read_outer_html,
)
from .launcher import BrowserLauncher, BrowserLaunchError
from .tab_registry import TabRegistry
from . import prefs as runtime_prefs  # BID086

logger = logging.getLogger(__name__)

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
_STUDIO_MARKER_SKIP_PREFIXES = (
    "devtools://",
    "chrome://",
    "edge://",
    "chrome-extension://",
    "extension://",
)


def _range_entry_notice(node, name: str) -> str:
    """The correct "how this date/range widget commits" hint for one entry.

    Two **structurally different** shapes both render the field name「起止时间」and
    both used to get the very same notice — which is factually wrong for the
    second (verified on the live Feishu Jobs form 2026-10-01):

    * **one shell, two typeable text inputs** (Element Plus ``monthrange`` / Ant
      ``RangePicker``): genuinely one widget driven by one panel, committing only
      after two candidate picks in the *same* overlay session
      (``picker_range_position`` returns a position);
    * **one shell whose two ends have no usable input** (Feishu
      ``atsx-date-picker-period-month``: two value labels + a 0×0 hidden input,
      one shared panel with a year column and a month column): each end is filled
      on its own — one year + one month *for that end* — and the other end is not
      involved at all. Telling the model the two ends were coupled made it re-open
      the same panel over and over and burn ~10k reasoning tokens before the user
      aborted (debug session 2026-10-01, ``debug_folder/20261001-17-18``).
    """
    if is_range_picker_entry(node):
        if picker_range_position(node) is not None:
            return rp.range_picker_notice(name)
        return rp.independent_ends_picker_notice(name)
    if is_range_picker_end(node):
        # A single end of a div-based range shell that the serializer exposed as
        # its own entry (``range_picker_ends``). Clicking it opens the shared
        # panel focused on that end; the two ends are independent.
        return rp.range_end_entry_notice(name)
    return ""


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


# 本函数于BID078引入
def _strip_interactive_tags(text: str) -> str:
    """剥掉序列化行里的 ``<可…元素 eN>`` 包装，保留缩进与内部文本。

    同标签页硬跳转后，旧文档里的元素名已经全部失效（实测回退后重新编号）；
    旧页面的增量仍要完整返回，但不能留下会误导点击的旧 ``eN``。
    """
    return re.sub(r"</?[^<>]*元素[^<>]*?>", "", text)

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


# 本函数于BID084引入
def _finalize_llm_output(func):
    """公开工具方法返回前的统一收口：整合 + 英文渲染（只在最终文本上做）。

    必须包在**公开方法**上而不是 ``_base_result`` 上：``switch_tab`` / ``go_back``
    等方法会在 ``_base_result`` 之后继续往 ``content`` 前面拼提示语，若在
    ``_base_result`` 里做后处理，那些提示会绕过这一层。
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return finalize_output.finalize_result(func(*args, **kwargs))

    return wrapper


# 本函数于BID086引入
def _trim_tabs_after_reply(func):
    """工具返回后按 LRU 收敛标签页（只挂在可能开新页的 click / navigate 上）。

    必须写在 ``@_finalize_llm_output`` **上面**：装饰器自下而上应用，写在上面的那个
    最后执行，于是「已经自动关闭标签页：…」追加在整合 / 英文渲染之后——和
    ``new_tab_notice`` 同口径（那条提示也是中文，英文模式下同样保持中文）。
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return args[0]._trim_tabs_and_notice(func(*args, **kwargs))

    return wrapper


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
    # 动作前是否为了「让目标可操作」而滚动过视口；滚动后已重建 diff 基线，
    # 用于在返回里明确告知「增量只含操作本身的变化」（BID078）。
    viewport_moved: bool = False  # BID078


class BrowserController:
    _instance: Optional["BrowserController"] = None

    def __init__(self, launcher: Optional[BrowserLauncher] = None) -> None:
        self.launcher = launcher or BrowserLauncher()
        # ID91：按会话隔离时，本 controller 所属 multi-agent 的标签页上限。
        # 标签页 LRU 仍是「一个浏览器进程一张表」；这里只覆盖上限这个标量。
        self.tab_limit_override: Optional[int] = None
        self.client: Optional[CDPClient] = None
        self.focused_target_id: Optional[str] = None
        self._registries: dict[str, NameRegistry] = {}
        self._studio_marked_targets: set[str] = set()
        self._studio_marker_lock = threading.Lock()
        # One counter for every tab: element numbers are globally unique, so the
        # LLM can never confuse ``e17`` on one tab with a different element on
        # another (each tab still owns its name maps / active set).
        self._name_counter = NameCounter()
        self._prev_trees: dict[str, EnhancedTree] = {}
        self._tab_registry = TabRegistry()
        # 本属性于BID086引入
        # 当前连接的浏览器进程标识（CDP 地址）与该进程的标签页 LRU 表
        # （``[targetId, ...]``，最久未使用的在前）。标签页是**浏览器进程**的资源：
        # 一个 CDP 端口一张表，既不分 agent 也不分会话；换了进程（端口变了）就整表
        # 作废——新进程里不可能有旧进程的标签页。
        self._browser_cdp_url: str = ""
        self._tab_lru: list[str] = []
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

    def browser_port(self) -> Optional[int]:
        """该 controller 对应浏览器进程的 CDP 端口（不启动浏览器）。"""
        try:
            return self.launcher.current_port()
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------ #
    # connection
    # ------------------------------------------------------------------ #
    def ensure_connected(self) -> None:
        _bid065_t0 = bid065_trace.perf()  # BID065
        bid065_trace.mark("controller.ensure_connected START")  # BID065
        # 快速路径：已连接且健康。**不参与 _op_lock**——否则「打开浏览器」会排队在
        # 一段正在进行的 DOM 抓取/序列化之后被饿死（而抓取本身又是慢的根源）。
        if self.client is not None:
            try:
                targets = self.client.refresh_targets(timeout=timing.get().cdp.probe_timeout)  # BID066
                # 上面用「短超时」探测连接健康：陈旧/半开连接最多卡 probe_timeout 就重连，  # BID066
                # 而不是沿用 command_timeout（30s）导致空闲后首个浏览器工具卡满 30s。  # BID066
                self._sync_studio_marker(targets)
                bid065_trace.mark_elapsed("controller.ensure_connected 快速路径", _bid065_t0)  # BID065
                return
            except Exception:
                try:
                    self.client.close()
                except Exception:
                    pass
                self.client = None
                with self._studio_marker_lock:
                    self._studio_marked_targets.clear()

        # 建立/重建连接：只用专用的 _connect_lock，避免两个线程同时建连。
        with self._connect_lock:
            # 双重检查：等锁期间别的线程可能已经建好了连接。
            if self.client is not None:
                try:
                    targets = self.client.refresh_targets(timeout=timing.get().cdp.probe_timeout)  # BID066
                    self._sync_studio_marker(targets)
                    bid065_trace.mark_elapsed("controller.ensure_connected 锁内快速路径", _bid065_t0)  # BID065
                    return
                except Exception:
                    try:
                        self.client.close()
                    except Exception:
                        pass
                    self.client = None
                    with self._studio_marker_lock:
                        self._studio_marked_targets.clear()

            _bid065_ensure_t0 = bid065_trace.perf()  # BID066: ensure_browser 单独计时，避免把前置健康探测耗时刻进来造成误判
            info = self.launcher.ensure_browser()
            bid065_trace.mark_elapsed("controller.ensure_connected ensure_browser", _bid065_ensure_t0)  # BID066
            self._adopt_browser(info)  # BID086: 换浏览器进程就重置标签页 LRU 表
            self.client = CDPClient(
                info["ws_url"], command_timeout=timing.get().cdp.command_timeout
            )
            with self._studio_marker_lock:
                self._studio_marked_targets.clear()
            try:
                self.client.send("Target.setDiscoverTargets", {"discover": True})
            except CDPError:
                pass
            targets = self.client.refresh_targets()
            self._seed_tab_lru(targets)  # BID086
            self._sync_studio_marker(targets)
            bid065_trace.mark_elapsed("controller.ensure_connected TOTAL(重建)", _bid065_t0)  # BID065

    def _mark_studio_target(self, target_id: str) -> None:
        if self.client is None or not studio_marker.marker_enabled():
            return
        if not target_id:
            return
        with self._studio_marker_lock:
            if target_id in self._studio_marked_targets:
                return
            try:
                session_id = self.client.session_for_target(target_id)
                studio_marker.apply_title_marker(
                    self.client, target_id, session_id=session_id
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("Studio 标题标记注入失败 (%s): %s", target_id, exc)
                return
            self._studio_marked_targets.add(target_id)

    def _sync_studio_marker(self, targets=None) -> None:
        if self.client is None or not studio_marker.marker_enabled():
            return
        if targets is None:
            try:
                targets = self.client.page_targets()
            except Exception:
                return
        iterable = targets.values() if isinstance(targets, dict) else targets
        for target in list(iterable):
            if target.get("type") != "page":
                continue
            url = target.get("url") or ""
            if url.startswith(_STUDIO_MARKER_SKIP_PREFIXES):
                continue
            target_id = target.get("targetId")
            if target_id:
                self._mark_studio_target(target_id)

    # ------------------------------------------------------------------ #
    # tabs
    # ------------------------------------------------------------------ #
    def _page_targets(self) -> list[dict]:
        if self.client is None:
            return []
        return self.client.page_targets()

    def _live_targets(self) -> list[dict]:
        """Page targets, with the tab registry reconciled against them."""
        _bid065_t0 = bid065_trace.perf()  # BID065
        targets = self._page_targets()
        self._tab_registry.reconcile(targets)
        bid065_trace.mark_elapsed("controller._live_targets", _bid065_t0)  # BID065
        return targets

    def _name_for_target(self, target_id: str) -> str:
        for target in self._live_targets():
            if target["targetId"] == target_id:
                return self._tab_registry.name(target)
        return ""

    def tab_labels(self) -> list[str]:
        return self._tab_registry.names(self._live_targets())

    # ------------------------------------------------------------------ #
    # 标签页 LRU（BID086：按「浏览器进程」记账）
    # ------------------------------------------------------------------ #
    # 本函数于BID086引入
    def _adopt_browser(self, info: dict) -> None:
        """记录当前浏览器进程；换进程（CDP 端口变了）就丢掉上一轮的 LRU 表。

        同一个进程断线重连（端口没变）时表要留着：那些标签页还在，只是连接抖了一下。
        """
        cdp_url = str((info or {}).get("cdp_url") or "")
        if not cdp_url or cdp_url == self._browser_cdp_url:
            return
        self._browser_cdp_url = cdp_url
        self._tab_lru = []

    # 本函数于BID086引入
    def _seed_tab_lru(self, targets) -> None:
        """连接建立后，把浏览器里已有的标签页按 ``/json/list`` 的顺序填进 LRU 表。

        ``/json/list`` 是创建顺序（旧→新）。这些页是「连接前就开着的」（初始空白页、
        用户自己开的页），理应排在最旧的一侧——真要收敛时先关它们，而不是先关本次
        刚开出来的页。表非空时不动，避免断线重连把已经排好的顺序冲掉。
        """
        if self._tab_lru:
            return
        iterable = targets.values() if isinstance(targets, dict) else (targets or [])
        self._tab_lru = [
            target["targetId"]
            for target in iterable
            if target.get("type") == "page"
            and target.get("targetId")
            and not str(target.get("url") or "").startswith("devtools://")
        ]

    # 本函数于BID086引入
    def _tab_order(self, live: list[dict]) -> list[str]:
        """把 LRU 表对齐到当前活动标签页，返回「最久未使用的在前」。

        * 表里已经关掉的标签页惰性剔除；
        * 没记过的标签页（用户自己开的、或连接前就有的）补到表尾，按「最新」算
          ——宁可晚一点关，也不能把刚开出来、还没用过的页当成最旧的先关掉。
        """
        live_ids = [target["targetId"] for target in live]
        live_set = set(live_ids)
        order = [tid for tid in self._tab_lru if tid in live_set]
        known = set(order)
        for target_id in live_ids:
            if target_id not in known:
                order.append(target_id)
                known.add(target_id)
        self._tab_lru = order
        return order

    # 本函数于BID086引入
    def _touch_tab_lru(self, target_id: str) -> None:
        """把标签页记成「浏览器最近使用」（已在表里则移到表尾）。"""
        if not target_id:
            return
        if target_id in self._tab_lru:
            self._tab_lru.remove(target_id)
        self._tab_lru.append(target_id)

    # 本函数于BID086引入
    def _forget_target(self, target_id: str) -> None:
        """清除标签页在控制器里的全部登记（标签页关闭后调用）。"""
        self._registries.pop(target_id, None)
        self._prev_trees.pop(target_id, None)
        self._studio_marked_targets.discard(target_id)
        self._tab_registry.forget(target_id)
        if target_id in self._tab_lru:
            self._tab_lru.remove(target_id)
        if self.focused_target_id == target_id:
            self.focused_target_id = None

    # 本函数于BID086引入
    def _wait_targets_closed(self, target_ids, timeout: Optional[float] = None) -> None:
        """有界等待给定标签页从活动列表里消失（不做无限轮询）。"""
        pending = {tid for tid in target_ids if tid}
        if not pending:
            return
        cfg = timing.get().ready
        if timeout is None:
            timeout = cfg.close_timeout
        deadline = time.time() + timeout
        while time.time() < deadline:
            live = {t["targetId"] for t in self._page_targets()}
            if not (pending & live):
                return
            time.sleep(cfg.close_poll_interval)

    # 本函数于BID086引入
    def _enforce_tab_limit(self) -> list[str]:
        """整个浏览器超过上限时，按 LRU 关掉最旧的标签页，返回它们的显示名。

        计数口径：**一个浏览器进程一张表**——所有 page 标签页都算（含用户自己开的、
        别的对话留下的）。标签页是这个浏览器窗口的资源，跟它是谁开的无关；只有按
        窗口收敛，「界面简洁」才成立。当前聚焦标签页永不关闭：它正是本次返回内容的
        来源。触发点只有 ``tool_01_click`` / ``tool_33_navigate``（见
        ``_trim_tabs_after_reply``），只读工具不会顺手关掉用户正在看的页。
        """
        if self.client is None:
            return []
        limit = self.tab_limit_override or runtime_prefs.tab_limit()
        live = self._live_targets()
        order = self._tab_order(live)
        excess = len(order) - limit
        if excess <= 0:
            return []
        # 先取好名字：边关边查会让注册表重新 reconcile，名字可能已经变了。
        names = {target["targetId"]: self._tab_registry.name(target) for target in live}
        focused = self.focused_target_id
        closed: list[str] = []
        closed_ids: list[str] = []
        for target_id in list(order):
            if excess <= 0:
                break
            if target_id == focused:
                continue
            try:
                self.client.send("Target.closeTarget", {"targetId": target_id})
            except CDPError as exc:
                logger.debug("自动关闭标签页失败 (%s): %s", target_id, exc)
                if target_id in self._tab_lru:
                    self._tab_lru.remove(target_id)
                continue
            closed.append(names.get(target_id) or target_id)
            closed_ids.append(target_id)
            self._forget_target(target_id)
            excess -= 1
        if closed_ids:
            self._wait_targets_closed(closed_ids)
        return closed

    # 本函数于BID086引入
    def _trim_tabs_and_notice(self, result):
        """按 LRU 收敛标签页，并把「已经自动关闭标签页：…」追加到返回内容。

        由 ``_trim_tabs_after_reply`` 在最终表现层之后调用。收敛失败绝不影响本次
        工具结果（它只是省上下文的优化）。
        """
        if not isinstance(result, dict):
            return result
        try:
            with self._op_lock:  # 与其它浏览器操作串行，避免并发关掉同一批标签页
                closed = self._enforce_tab_limit()
        except Exception as exc:  # noqa: BLE001
            logger.debug("标签页数量收敛失败: %s", exc)
            return result
        notice = rp.auto_closed_tabs_notice(closed)
        if not notice:
            return result
        out = dict(result)
        content = out.get("content")
        if isinstance(content, str) and content and content != rp.EMPTY_CONTENT:
            out["content"] = content + "\n\n" + notice
        else:
            out["content"] = notice
        if "tabs" in out:
            # 被关掉的可能正是本结果里列出的标签页：重列一次，别给 LLM 过期名单。
            out["tabs"] = self.tab_labels()
        return out

    @_finalize_llm_output  # BID084
    def tab_url_map(self) -> dict:
        _bid065_t0 = bid065_trace.perf()  # BID065
        bid065_trace.mark("controller.tab_url_map START")  # BID065
        try:
            self.ensure_connected()
            bid065_trace.mark_elapsed("controller.tab_url_map ensure_connected", _bid065_t0)  # BID065
            result = self._tab_registry.url_map(self._live_targets())
        except (BrowserLaunchError, CDPError):
            result = {}
        bid065_trace.mark_elapsed("controller.tab_url_map TOTAL", _bid065_t0)  # BID065
        return result

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
        _bid065_t0 = bid065_trace.perf()  # BID065
        bid065_trace.mark("controller._probe_focused_target START")  # BID065
        visible: list[str] = []
        focused: list[str] = []
        for target in self._page_targets():
            target_id = target["targetId"]
            _bid065_pt = bid065_trace.perf()  # BID065
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
                bid065_trace.mark_elapsed(f"controller._probe_focused_target probe异常 {target_id}", _bid065_pt)  # BID065
                continue
            bid065_trace.mark_elapsed(f"controller._probe_focused_target probe {target_id}", _bid065_pt)  # BID065
            state, _, focus = value.partition("|")
            if state == "visible":
                visible.append(target_id)
            if focus == "true":
                focused.append(target_id)
        if focused:
            result = focused[0]
        elif visible:
            result = visible[0]
        else:
            result = None
        bid065_trace.mark_elapsed("controller._probe_focused_target TOTAL", _bid065_t0)  # BID065
        return result

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
        _bid065_t0 = bid065_trace.perf()  # BID065
        bid065_trace.mark("controller._resolve_focused_target START")  # BID065
        targets = self._page_targets()
        if not targets:
            bid065_trace.mark_elapsed("controller._resolve_focused_target 无标签页", _bid065_t0)  # BID065
            return None
        if self.focused_target_id and any(
            t["targetId"] == self.focused_target_id for t in targets
        ):
            bid065_trace.mark_elapsed("controller._resolve_focused_target 命中已选", _bid065_t0)  # BID065
            return self.focused_target_id
        probed = self._probe_focused_target()
        if probed is not None:
            self.focused_target_id = probed
            bid065_trace.mark_elapsed("controller._resolve_focused_target 探测", _bid065_t0)  # BID065
            return probed
        self.focused_target_id = targets[0]["targetId"]
        bid065_trace.mark_elapsed("controller._resolve_focused_target 回退首个", _bid065_t0)  # BID065
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
        self._touch_tab_lru(target_id)  # BID086: 每次激活都算「浏览器最近使用」
        return True

    # ------------------------------------------------------------------ #
    # DOM
    # ------------------------------------------------------------------ #
    def _registry_for(self, target_id: str) -> NameRegistry:
        if target_id not in self._registries:
            self._registries[target_id] = NameRegistry(self._name_counter)
        return self._registries[target_id]

    def capture_tree(
        self, target_id: Optional[str] = None, record_debug: bool = True  # BID078
    ) -> EnhancedTree:
        _bid065_t0 = bid065_trace.perf()  # BID065
        bid065_trace.mark("controller.capture_tree START")  # BID065
        self.ensure_connected()
        assert self.client is not None
        if target_id is None:
            target_id = self._resolve_focused_target()
        if target_id is None:
            raise RuntimeError(rp.NO_TABS)
        session = self.client.attach(target_id)
        self.client.enable_page_domains(session)
        _bid065_cap = bid065_trace.perf()  # BID065
        raw = capture_raw(self.client, session)
        bid065_trace.mark_elapsed("controller.capture_tree capture_raw", _bid065_cap)  # BID065
        tree = build_enhanced_tree(raw)
        registry = self._registry_for(target_id)
        registry.reconcile(tree)
        self._prev_trees[target_id] = tree
        self.focused_target_id = target_id
        # Debug 模式：记录原始 HTML 与处理后的序列化 DOM（未开启时静默跳过）。
        # 动作前为重建 diff 基线而做的额外抓取不落盘（record_debug=False），
        # 否则会把「动作前」的快照覆盖到日志里，看不到动作后的真实页面（BID078）。
        if record_debug and debug.current_capture() is not None:  # BID078
            raw_html = read_outer_html(self.client, session)
            processed = self.serialize_tree(tree, target_id)
            debug.record_dom(raw_html, processed, tree.url, tree.title)
        bid065_trace.mark_elapsed("controller.capture_tree TOTAL", _bid065_t0)  # BID065
        return tree

    def serialize_tree(self, tree: EnhancedTree, target_id: str) -> str:
        _bid065_t0 = bid065_trace.perf()  # BID065
        registry = self._registry_for(target_id)
        # BID079：全量输出（tool_10 / 切标签页 / 导航 / 刷新）的缩进压缩按「全量
        # 口径」判断 —— 传入忽略视口裁剪的整页行，避免把「视口边界下方还有兄弟 /
        # 还有第二行内容」的结构误压成单行。
        context = full_view_lines(tree)  # BID079
        result = DOMSerializer(registry).serialize(tree, context_lines=context)  # BID079
        bid065_trace.mark_elapsed("controller.serialize_tree", _bid065_t0)  # BID065
        return result

    def serialize_lines_tree(self, tree: EnhancedTree, target_id: str) -> list[OutLine]:
        registry = self._registry_for(target_id)
        lines = DOMSerializer(registry).serialize_lines(tree)  # BID080
        # BID080：图片标签关闭时，diff 基线/新快照都要先删 [图片] 及其独子父链，
        # 否则「只是页面多了一张图」也会被算成一次内容变化。
        return prune_image_lines(lines)  # BID080

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
        which moves the viewport. The incremental diff is then dominated by
        content that merely *scrolled into view* and has nothing to do with the
        action itself; without a heads-up the LLM misreads it as "opening this
        control revealed hidden content" and wastes reasoning. Returns "" when the
        page did not move (or the offset could not be read).

        ``ctx.viewport_moved`` 是 BID078 起更可靠的信号：``page_scroll_top`` 只
        描述文档滚动条，SPA 的内层滚动容器（如飞书表单）滚动时它恒为 0；而
        BID078 已在动作前把目标滚入视口并重建 diff 基线，所以这里只需要告诉
        LLM「视口被工具移动过、增量不含滚动副作用」。
        """
        if getattr(ctx, "viewport_moved", False):  # BID078
            return rp.viewport_scrolled(None, None)  # BID078
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
        _bid065_t0 = bid065_trace.perf()  # BID065
        bid065_trace.mark(f"controller._begin_interaction 等待锁 {name}")  # BID065
        with self._op_lock:
            bid065_trace.mark_elapsed(f"controller._begin_interaction 获取锁 {name}", _bid065_t0)  # BID065
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
        if category not in allowed:  # BID085
            category = self._compatible_category(node, category, allowed)  # BID085
        if category not in allowed:
            target_tool = self._SPLIT_CATEGORY_TOOL.get(category, "")
            return None, self._base_result(
                INCREMENTAL,
                FAIL,
                rp.EMPTY_CONTENT,
                rp.category_mismatch(name, category, allowed[0], target_tool),
                include_tabs=False,
            )

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

        # 动作前先把目标滚入视口，并据此重建 diff 基线（BID078）。
        # 原因：``ActionExecutor._node_box`` 在每次点击/输入前都会
        # ``DOM.scrollIntoViewIfNeeded``；目标不在当前视口时，整个视口会被滚动。
        # 动作后的视口里，所有「滚动后才出现」的行相对旧基线都是新增行，于是增量
        # diff 退化成整屏输出（debug_folder/20261023-21-22 的 e164 / e175 两例）。
        # 这里先把滚动做掉，再抓一次「动作前、已滚动到位」的快照作为基线，diff
        # 才只包含动作本身产生的变化。滚动工具自己不改基线：它就是靠移动视口取新内容。
        viewport_moved = False  # BID078
        if category != "scroll":  # BID078
            try:  # BID078
                viewport_moved = executor.scroll_into_view(node.backend_node_id)  # BID078
            except Exception:  # BID078
                viewport_moved = False  # BID078
            if viewport_moved:  # BID078
                refreshed_tree = self.capture_tree(target_id, record_debug=False)  # BID078
                refreshed_node = next(  # BID078
                    (n for n in refreshed_tree.nodes if n.key == key), None  # BID078
                )  # BID078
                if refreshed_node is not None:  # BID078
                    old_tree = refreshed_tree  # BID078
                    node = refreshed_node  # BID078
                else:  # BID078
                    # 节点在滚动后的新树里找不到（页面刚好重渲染）：没有可信的新基线，
                    # 不能对 LLM 声称「增量已按新视口重建」。
                    viewport_moved = False  # BID078

        # 基线序列化放在「预滚动 + 必要时重建基线」之后：这样一次互动只序列化一次，
        # 且序列化的一定是动作即将发生时的那个视口（BID078）。
        old_lines = self.serialize_lines_tree(old_tree, target_id)  # BID078

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
                viewport_moved=viewport_moved,  # BID078
            ),
            None,
        )

    # 本函数于BID085引入
    def _compatible_category(self, node: EnhancedNode, category: str, allowed: tuple) -> str:
        """把「下拉筛选框被判成 input」的类别映射到本次调用可接受的同族类别。

        同一个筛选框可能因为瞬时状态（悬停/聚焦换图标、禁用态、组件换装）在不同
        快照里被分成 ``searchable`` 或 ``input``。若严格按类别拒绝，tool_07 会说
        「它是可输入元素，请用 tool_02」，tool_02 又说「它是可搜索下拉，请用
        tool_07」，模型永远操作不了该字段（2026-10-26 校招表单实测死循环）。

        映射只走**结构证据**（``is_custom_select_input``：可编辑 input + 直接父容器
        带 select 令牌 + 下拉装配部件/aria 弹层契约），不猜、不放宽语法不同的控件：

        * ``input`` → ``searchable``：仅当结构上确实是下拉筛选框，且本次工具接受
          ``searchable``（tool_07 / tool_08）；
        * 反向（tool_02 接受 ``searchable``）由调用方的 ``allowed`` 显式声明，因为
          「往筛选框里打字」本身就是该工具能做的动作。
        """
        if category == "input" and "searchable" in allowed:  # BID085
            if is_custom_select_input(node):  # BID085
                return "searchable"  # BID085
        return category  # BID085

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
        panel_fixed = False  # BID095

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
        # 结构性判定（不依赖 class 清单）必须**无条件**计算：class 探针不认识组件库
        # 自定义 class 的面板（Beisen ``common-unmodeled-layer``），若仍沿用
        # 「仅当 ``overlay_before`` 才判定」，则面板内候选项被当成「不在浮层内」，
        # 点前先 blur 会把面板关掉、候选项点空（BID067）。
        inside_overlay = executor.element_inside_overlay(node.backend_node_id)  # BID067
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
        # 点击**前**的可见浮层结构指纹：用于把「真的打开了（但 class 探针不认识）的
        # 面板」与「真死点击」区分开，避免对 toggle 型触发器做有界重试时把刚打开的
        # 面板又点一次关掉（BID067）。
        #
        # 指纹是视口坐标拼的，而 ``click`` 内部可能先 scrollIntoView；先显式把节点滚入
        # 视口，消除「点击自身滚动」造成的 rect 位移假差异，指纹才只反映浮层的真实出现
        # （BID068）。
        if popup_trigger:  # BID068
            try:  # BID068
                executor.scroll_into_view(node.backend_node_id)  # BID068
            except Exception:  # BID068
                pass  # BID068
        panels_before = (
            executor.floating_panel_fingerprint() if popup_trigger else ""
        )  # BID067
        # BID091：兜底判据要的是「相对点击前**新出现**的面板」，所以必须留一份点击前
        # 的同口径基线。只问「此刻 anchored 是否为真」会把页面上常驻的、长得像面板的
        # 浮层（紧贴控件、水平覆盖 ≥60%、含 ≥2 行候选）当成「面板已打开」：首点把
        # toggle 面板关掉后，BID089 靠「浮层盒子数变少」正确判出的「没打开」会被翻回
        # 「已打开」，补点被跳过 —— 面板关着、没有候选，工具却报告成功（真机复现
        # 见 BID091 文档 §1）。基线与 ``panels_before`` 同点取（滚动之后，BID068）。
        panel_before = (
            executor.panel_state(node.backend_node_id) if popup_trigger else {}
        )  # BID091
        # A same-document link (active nav item / in-page hash) must not pay the
        # full navigation grace: it either fires a soft event or does nothing.
        click_may_nav = may_navigate(node)
        if click_may_nav and _href_targets_same_document(node, old_tree.url):
            click_may_nav = False
        click_closed = False  # BID099
        with self._handle_dialogs(session) as dialog:
            with self._watch_navigation(session) as nav_state:
                executor.click(node.backend_node_id)
                # BID099：在 settle 之前记下「这一击确实让面板变少」的证据。
                # ``_settle_navigation`` 会等 DOM 静默，收起→挂回可能整个落在它里面；
                # 等到观察窗口开始时差分已看不到任何变化，BID098 的乐观判据就失去
                # 了它唯一的前提（见 `_panel_closed_by_click`）。
                if popup_trigger:  # BID099
                    click_closed = self._panel_closed_by_click(  # BID099
                        executor, node.backend_node_id, panel_before  # BID099
                    )  # BID099
                self._settle_navigation(session, nav_state, click_may_nav)
            # BID094：``inside_overlay`` 只该回答「要不要 blur/park」（BID067 的原始
            # 用途），不能顺带否决整条面板证据链。模态框（role=dialog 满足
            # ``_FLOATING_PANEL_JS``）里的下拉控件同样被算作「在浮层内」，旧写法让它
            # 直接落到这一支：不轮询、不补点、也不披露，首点没打开就静默返回成功
            # （BID094 §1 CASE-I 真机复现）。目标本身是触发器（aria-haspopup/combobox
            # 或 clickdropdown 类别）时，点击语义就是「打开它的候选面板」，与它外层
            # 是否还套着一个模态框无关，因此仍走下面的面板校验。
            if inside_overlay and not popup_trigger:  # BID094
                # Clicked inside an already-open popup: a popup interaction is
                # itself proof the click was real.
                click_noop = False
            elif popup_trigger:
                # Keep the just-opened popup alive (no blur/park) and let it
                # paint before the capture. ``_wait_popup_opened`` accepts either
                # evidence: the class-name overlay probe, or a structural
                # fingerprint change that catches a custom-class panel our probe
                # does not know (BID067).
                opened = self._wait_popup_opened(  # BID067
                    executor, session, panels_before, overlay_before,  # BID091
                    panel_before=panel_before,  # BID092
                    backend_node_id=node.backend_node_id,  # BID092
                    saw_click_close=click_closed,  # BID099
                )  # BID067
                if not opened:  # BID089
                    # 「没打开」这个否定结论只来自间接的浮层盒子差分。控件旁边若**新出现**
                    # 一个贴着的面板盒子，那是最直接的反证：宁可认为已打开，也不要因为
                    # 一次计数误差再点一次——第二次点击会把 panel 关掉（BID089）。
                    # BID091：必须是「相对点击前新出现」。常驻的像面板的浮层会让
                    # ``anchored`` 恒真，那样这道兜底会把上面已经判对的「被自己关掉」
                    # 再翻回「已打开」，补点于是被跳过（BID089 的核心修复被抵消）。
                    if self._panel_opened_since(  # BID091
                        panel_before,  # BID091
                        executor.panel_state(node.backend_node_id),  # BID091
                    ):  # BID091
                        opened = True  # BID089
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
                    #
                    # 注意：只有「首点后连结构指纹都没变」才允许重试——否则 toggle
                    # 型触发器会被第二次点击关掉刚打开的面板（BID067）。
                    #
                    # BID089：补点的比对基线要**重新取**。若首点其实是把那面板关掉了
                    # （探测盲区里的反向操作），面板重新打开后指纹会回到「点前」的样子，
                    # 仍拿旧的 ``panels_before`` 比就永远「没变化」，于是面板明明开着却
                    # 报「没有检测到面板打开」。基线取首点之后的状态，才认得出「重新开了」。
                    panels_closed = executor.floating_panel_fingerprint()  # BID089
                    overlay_closed = self._overlay_open(session)  # BID091
                    panel_closed = executor.panel_state(node.backend_node_id)  # BID091
                    retry_closed = False  # BID099
                    with self._watch_navigation(session) as retry_nav:
                        executor.click(node.backend_node_id)
                        retry_closed = self._panel_closed_by_click(  # BID099
                            executor, node.backend_node_id, panel_closed  # BID099
                        )  # BID099
                        self._settle_navigation(session, retry_nav, click_may_nav)
                    opened = self._wait_popup_opened(  # BID067
                        executor, session, panels_closed, overlay_closed,  # BID089 / BID091
                        panel_before=panel_closed,  # BID092
                        backend_node_id=node.backend_node_id,  # BID092
                        saw_click_close=retry_closed,  # BID099
                    )  # BID067
                    if not opened:  # BID090
                        # BID090：补点后与首点后要**对称**地做同一道兜底。补点的
                        # 「成功」同样只来自间接的浮层差分：差分探针不可观测时它
                        # 必然返回 False，于是面板明明被补点打开了也照样报「没有
                        # 检测到面板打开」（实测可复现）。控件旁若贴着面板，那就是
                        # 最直接的反证。
                        # BID091：基线与首点兜底同理，取**补点前**那一刻；仍然只认
                        # 「新出现/计数变多」，否则常驻假阳性会让失败也被说成成功。
                        if self._panel_opened_since(  # BID091
                            panel_closed,  # BID091
                            executor.panel_state(node.backend_node_id),  # BID091
                        ):  # BID091
                            opened = True  # BID090
                    # BID095：补点分支的复验若通过，说明这次**多点了界面一下**——
                    # 必须落在 `if not opened:`（首点失败）之内。放到外层会连
                    # 「首点就打开」的正常路径也标成补点，误报「已额外点击一次」
                    # （BID095 真机回归：CASE-M 首点即打开却被标注）。
                    if opened:  # BID095
                        panel_fixed = True  # BID095
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
            "panel_fixed": panel_fixed,  # BID095
            "before_sig": before_sig,
            "after_sig": after_sig,
        }

    # 本函数于BID094引入
    def _click_with_popup_evidence(
        self, executor, session: str, node: EnhancedNode
    ) -> dict:
        """点击「按契约会展开面板」的元素，并用 ``_run_click`` 同一套证据收尾。

        为什么需要它：``interact_many``（tool_06）的收尾点击一直是裸
        ``executor.click``，完全没有接入 BID067 之后的任何面板证据——不轮询
        ``_wait_popup_opened``、不做 ``_panel_opened_since`` 兜底、不补点，也不给
        ``popup_missing`` 的诚实披露。于是整条 BID089–BID093 修好的东西在批量入口上
        一个都不生效：收尾点的是已展开的 toggle 下拉时把它关掉仍报「成功」，首点因
        入场竞态没打开同样报「成功」（BID094 §1 CASE-H 真机复现）。

        返回 ``{"popup_missing": bool, "panel_fixed": bool}``，语义与 ``_run_click``
        的同名键一致：前者表示「契约上应有的面板没出现」，后者表示「首点没打开/把它
        关掉了，补点一次之后确认打开」。判据全部复用既有函数，这里不另立一套。
        """
        backend_node_id = node.backend_node_id  # BID094
        try:  # BID094
            # 与 _run_click 同点取基线：指纹是视口坐标拼的，先滚到位再取（BID068）。
            executor.scroll_into_view(backend_node_id)  # BID094
        except Exception:  # noqa: BLE001  # BID094
            pass  # BID094
        panels_before = executor.floating_panel_fingerprint()  # BID094
        panel_before = executor.panel_state(backend_node_id)  # BID094
        overlay_before = self._overlay_open(session)  # BID094
        executor.click(backend_node_id)  # BID094
        click_closed = self._panel_closed_by_click(  # BID099
            executor, backend_node_id, panel_before  # BID099
        )  # BID099
        opened = self._wait_popup_opened(  # BID094
            executor, session, panels_before, overlay_before,  # BID094
            panel_before=panel_before,  # BID094
            backend_node_id=backend_node_id,  # BID094
            saw_click_close=click_closed,  # BID099
        )  # BID094
        if not opened and self._panel_opened_since(  # BID094
            panel_before, executor.panel_state(backend_node_id)  # BID094
        ):  # BID094
            opened = True  # BID094
        if opened:  # BID094
            return {"popup_missing": False, "panel_fixed": False}  # BID094
        # 与 _run_click 对称：补点的基线**重新取**（首点可能反向操作过），补点后仍要
        # 有方向的证据才认成功——BID089/BID090/BID091 的结论原样复用，不另立判据。
        panels_closed = executor.floating_panel_fingerprint()  # BID094
        overlay_closed = self._overlay_open(session)  # BID094
        panel_closed = executor.panel_state(backend_node_id)  # BID094
        executor.click(backend_node_id)  # BID094
        retry_closed = self._panel_closed_by_click(  # BID099
            executor, backend_node_id, panel_closed  # BID099
        )  # BID099
        opened = self._wait_popup_opened(  # BID094
            executor, session, panels_closed, overlay_closed,  # BID094
            panel_before=panel_closed,  # BID094
            backend_node_id=backend_node_id,  # BID094
            saw_click_close=retry_closed,  # BID099
        )  # BID094
        if not opened and self._panel_opened_since(  # BID094
            panel_closed, executor.panel_state(backend_node_id)  # BID094
        ):  # BID094
            opened = True  # BID094
        return {"popup_missing": not opened, "panel_fixed": opened}  # BID094

    # BID096：「疑似被自己关掉」的确认窗口。输入会触发筛选/远端取数，组件库常在
    # 「先卸载候选面板 → 取结果 → 再挂回来」之间空出一段（React 列表重排、loading
    # 占位、transition 首帧），真机实测补点的「判定 → 派发」本身就要 ~0.6s，中间态
    # 常在这个量级。窗口必须覆盖它，否则确认形同虚设、仍会补点误关。
    # 只在「疑似关闭」时才付这个代价（正常输入零额外开销）。
    # BID097：「清空筛选词」同样会触发重渲染，`_run_searchable` 空串路径的补点
    # 闸门也先过这个窗口——不能只凭动作后一帧就下「被关掉」的结论。
    _PANEL_CLOSE_CONFIRM_SECONDS = 1.0  # BID096
    _PANEL_CLOSE_CONFIRM_INTERVAL = 0.05  # BID096

    # 本函数于BID096引入
    def _confirm_panel_closed(
        self, executor, backend_node_id: int, before: dict, after: dict
    ) -> dict:
        """给「面板疑似被自己关掉」一个短的有界确认窗口，再下结论。

        为什么不能只看动作后的单帧快照：输入会触发筛选 / 远端取数，组件库常在
        「先卸载候选面板 → 拿结果 → 再挂回来」之间空出一段（React 列表重排、
        loading 占位、transition 首帧）。单帧快照这时看到的「面板不在」只是**中间
        态**；若据此补点，那一击会按真实 toggle 语义把它**关掉**（BID096 §1
        CASE-N 真机复现：输入后面板 0.8s 隐藏、随后自行恢复，旧实现却多点一次、
        把面板永久关闭，还报「没有检测到面板打开」）。

        BID089 对「变少」已经立过同样的规矩：变少只是候选，不能抢跑，先观察再下
        结论。这里把同一条规矩用到「关闭」上：窗口内只要面板重新出现（锚定，或
        贴着本控件的候选面板数不再比基线少），立刻返回当前状态，让调用方放弃补点。

        返回窗口结束时（或提前恢复时）的 ``panel_state``。

        BID097：``_run_searchable`` 空串路径（清空筛选词 / ``open_only``）的补点
        闸门也复用它——那条路径同样只凭「动作后一帧」就判「被关掉」。
        """
        current = after  # BID096
        deadline = time.time() + self._PANEL_CLOSE_CONFIRM_SECONDS  # BID096
        while time.time() < deadline:  # BID096
            # BID099：``current.anchored`` 只有「相对基线**新出现**」才算面板回来。
            # 页面上常驻一个「面板形态」盒子（BID091 的几何假阳性）时 ``anchored``
            # 恒真，单点判据会让本窗口一开始就返回「恢复」，后面的补点被整个跳过
            # （BID099 §1 CASE-AA 真机复现）。
            if current.get("anchored") and not before.get("anchored"):  # BID099
                return current  # BID096
            if not self._panel_closed_since(before, current):  # BID096
                # 面板回来了：这不是我们关的，别补点。
                return current  # BID096
            time.sleep(self._PANEL_CLOSE_CONFIRM_INTERVAL)  # BID096
            current = executor.panel_state(backend_node_id)  # BID096
        return current  # BID096

    # 本函数于BID095引入
    def _input_with_panel_evidence(
        self, executor, session: str, node: EnhancedNode, input_fn
    ) -> tuple[dict, dict]:
        """输入前先留面板基线；控件旁本来就有候选面板时，验证输入没把它关掉。

        为什么需要它：``ActionExecutor.input_text`` 的**第一步就是点击**目标
        （``self.click()``，为了让控件聚焦、让受控组件进入编辑态）。而组件库的
        select 是 toggle 语义：对**已展开**的控件点这一下会把它关掉，接着才打字。
        旧实现只在「空串筛出完整候选列表」分支躲开了这一击（BID085），非空筛选词
        / 往筛选框写值这两条路径完全没验面板：面板被自己的隐式点击关掉后，工具仍然
        只报告「已写入」，既不补点也不披露（BID095 §1 CASE-J/CASE-K 真机复现）。

        只在「动作前控件旁确实贴着一个候选面板」时才做动作后复验：普通文本框没有
        候选面板，不会平白多出一次确认窗口；这条判据与 BID094 的 ``tail_popup``
        同构——只在按契约会有面板的地方付代价。

        BID096：动作后**不能只凭一帧**就判定「面板被自己关掉了」。输入触发的筛选 /
        重新取数会把面板短暂卸载再挂回（React 列表重排、loading 占位、transition
        首帧），单帧快照看到的「不在」常常只是中间态；据此补点，那一击会按真实
        toggle 语义把马上要恢复的面板**真正关掉**。BID089 对「变少」已经立过
        「不抢跑」，这里对「关闭」走同一个有界确认窗口
        （:meth:`_confirm_panel_closed`）。

        返回 ``(input_result, {"panel_missing": bool, "panel_fixed": bool})``：
        前者是 ``input_fn`` 的返回值（调用方仍要读它判断清空是否成功），后者语义与
        ``_run_click`` / ``_click_with_popup_evidence`` 的同名键一致。判据全部复用
        BID089/BID090/BID091/BID093 的既有函数，这里不另立一套。
        """
        backend_node_id = node.backend_node_id  # BID095
        before = executor.panel_state(backend_node_id)  # BID095
        evidence = {"panel_missing": False, "panel_fixed": False}  # BID095
        if int(before.get("count", -1)) < 0:  # BID095
            # 探针不可观测：按 BID072 不做事后否定/补点，直接输入。
            return input_fn(), evidence  # BID095
        if not before.get("anchored") and int(before.get("near", -1)) <= 0:  # BID095
            # 动作前控件旁没有候选面板：这是普通文本输入，不额外探测或补点。
            return input_fn(), evidence  # BID095
        result = input_fn()  # BID095
        after = executor.panel_state(backend_node_id)  # BID095
        if (  # BID095
            (after.get("anchored") and not before.get("anchored"))  # BID099
            or not self._panel_closed_since(before, after)  # BID095
        ):  # BID099
            return result, evidence  # BID095
        # BID096：这一帧只是「疑似关闭」。输入触发的重渲染/取数会把面板短暂卸载，
        # 单帧快照分不清「被我们关掉」和「正在挂回来」；据此补点会把中间的动画面板
        # 真正 toggle 关掉。先给一个有界确认窗口，再决定要不要补点。
        after = self._confirm_panel_closed(  # BID096
            executor, backend_node_id, before, after  # BID096
        )  # BID096
        if (  # BID096
            (after.get("anchored") and not before.get("anchored"))  # BID099
            or not self._panel_closed_since(before, after)  # BID096
        ):  # BID099
            # 面板自己回来了：中间态而已，不是本次输入关的，不补点也不披露。
            return result, evidence  # BID096
        if self._page_hidden(session):  # BID095
            # 页面不可见时几何/盒子数都不可信；BID072 的原则是「不可观测 ⇒ 不做可能
            # 把面板点关的破坏性重试」。补点本身就是一次点击，这里一律放弃。
            return result, evidence  # BID095
        executor.click(backend_node_id)  # BID095
        self._wait_overlay_open(session)  # BID095
        confirmed = self._panel_opened_since(  # BID095
            after, executor.panel_state(backend_node_id)  # BID095
        )  # BID095
        evidence = {"panel_missing": not confirmed, "panel_fixed": confirmed}  # BID095
        return result, evidence  # BID095

    # 本函数于BID095引入
    @staticmethod
    def _panel_evidence_notice(evidence: dict, name: str) -> str:
        """把 :meth:`_input_with_panel_evidence` 的结论翻成一句披露。

        复用 ``_run_searchable`` / ``_click_with_popup_evidence`` 的同一组文案：
        「没有检测到面板打开」与「已额外点击一次」，不另立说法（BID094 的口径）。
        """
        if evidence.get("panel_missing"):  # BID095
            return rp.popup_not_opened_notice(name)  # BID095
        if evidence.get("panel_fixed"):  # BID095
            return rp.select_panel_reopened_notice(name)  # BID095
        return ""  # BID095

    # 本函数于BID095引入
    @staticmethod
    def _panel_verdict_notice(ctx: "_InteractContext", extras: dict) -> str:
        """一次点击的面板结论（``popup_missing`` / ``panel_fixed``，互斥）前缀。

        ``_run_click`` 的键名沿用 BID089 的 ``popup_missing``；补点成功原本没有键，
        BID095 补成 ``panel_fixed``。集中到一处，任何出口（含 ``new_focus`` 的提前
        返回）都拼同一个前缀，就不会再各自漏拼。返回带结尾空行，便于直接前置。
        """
        if extras.get("popup_missing"):  # BID095
            return rp.popup_not_opened_notice(ctx.name) + "\n\n"  # BID095
        if extras.get("panel_fixed"):  # BID095
            return rp.select_panel_reopened_notice(ctx.name) + "\n\n"  # BID095
        return ""  # BID095

    def _run_fill(
        self, ctx: _InteractContext, fill: str, press_enter: bool = False
    ) -> dict:
        node = ctx.node
        executor = ctx.executor
        target_id = ctx.target_id
        name = ctx.name
        fill_error = ""
        fill_notice = ""
        # BID085：下拉控件的筛选框（Moka ``sd-Select`` 的 input）也能被这个工具写到
        # （它的类别会随悬停/聚焦抖动成 ``input``）。写进去的只是**筛选词**，不会
        # 提交取值，所以下面会补一条提示，绝不把它当成「已填好」。
        is_select_filter = is_custom_select_input(node)  # BID085
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
            had_text = bool(executor.read_value(node.backend_node_id))  # BID085
            if not executor.clear_field(node.backend_node_id):
                fill_error = rp.clear_fill_error(name)
            elif is_select_filter:  # BID085
                # 清空的只是筛选词；下拉的已选值是控件自己的状态，不随筛选框清空
                # （2026-10-26：用户手动选中的「合肥市」被模型当成了自己填的/清掉的）。
                fill_notice = rp.select_filter_clear_notice(name, had_text)  # BID085
            self._stabilize(target_id)
            return {
                "fill_error": fill_error,
                "fill_notice": fill_notice,
                "dialog": {"type": "", "message": ""},
            }
        panel_evidence: dict = {}  # BID095
        if not fill_error:
            result, panel_evidence = self._input_with_panel_evidence(  # BID095
                executor, ctx.session, node,  # BID095
                lambda: executor.input_text(node.backend_node_id, fill),  # BID095
            )  # BID095
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
        panel_notice = self._panel_evidence_notice(panel_evidence, name)  # BID095
        if fill and not fill_error and is_select_filter:  # BID085
            # 值确实进了输入框，但那只是筛选词：下拉的取值只能靠点候选提交。
            # BID095：这句与面板披露是**两条独立信息**，用追加而不是覆盖——旧写法
            # 会把「面板被这次输入关掉/已补点恢复」的结论整句抹掉。
            fill_notice = "\n".join(  # BID095
                p for p in (fill_notice, rp.select_filter_typing_notice(name, fill)) if p  # BID095
            )  # BID095
        fill_notice = "\n".join(  # BID095
            p for p in (panel_notice, fill_notice) if p  # BID095
        )  # BID095
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

    # 本函数于BID089引入
    @staticmethod
    def _panel_box_count(fingerprint: str) -> int:
        """从浮层指纹（``"<数量>|class@rect;…"``）里取出可见浮层盒子数。

        取不到指纹（空串）时返回 ``-1``：那是「无法观测」，绝不能当成「一个浮层都没有」，
        否则「探测失败」会被误读成「面板确实关着/关了」而多补一次点击（BID089）。
        """
        if not fingerprint:  # BID089
            return -1  # BID089
        head = fingerprint.split("|", 1)[0]  # BID089
        try:  # BID089
            return int(head)  # BID089
        except (TypeError, ValueError):  # BID089
            return -1  # BID089

    # 本函数于BID091引入
    @staticmethod
    def _panel_opened_since(before: dict, after: dict) -> bool:
        """相对**动作前**的基线，判断「这个控件旁刚刚出现了面板」。

        为什么不能只看 ``after["anchored"]``：BID089 §5.2 的措辞是「控件旁**出现了**
        贴着它的面板」，而 ``anchored`` 只回答「此刻有没有」。页面上本来就有一个
        「长得像面板」的常驻浮层（紧贴控件、水平覆盖 ≥60%、含 ≥2 行候选）时，
        ``anchored`` 恒真；首点把 toggle 面板关掉后，BID089 靠「浮层盒子数变少」
        正确判出的「没打开」会被这道兜底翻回「已打开」，补点被跳过 —— 面板关着、
        没有候选，工具却报告成功（BID091 §1 真机复现）。

        只认三条有方向的证据：
        * 锚定面板**从无到有**（``before.anchored`` 假、``after.anchored`` 真）；
        * 同口径浮层盒子数**变多**（``count`` 增加）——锚定基线本就为真的常驻场景下，
          新面板会让计数比基线多；
        * 贴着本控件的候选面板个数**变多**（``near`` 增加，BID092）——``count`` 会被
          同一击插入的无关浮层（保存提示/loading 遮罩）带偏（面板少一个、提示多一个，
          总数不变或变多），``near`` 只数本控件旁边的候选面板，不受其影响。

        取不到数（``count == -1``）、锚定基线本来就真且计数没增加时一律返回 False：
        宁可少一次「已打开」的断言，也不要把反向操作说成成功（BID060/BID090）。
        """
        if not isinstance(after, dict):  # BID091
            return False  # BID091
        before = before if isinstance(before, dict) else {}  # BID091
        before_n = int(before.get("count", -1))  # BID091
        after_n = int(after.get("count", -1))  # BID091
        # BID092：``count`` 会被无关浮层抵消，``near`` 不会（见 ``_wait_popup_opened``）。
        try:  # BID092
            before_near = int(before.get("near", -1))  # BID092
            after_near = int(after.get("near", -1))  # BID092
        except (TypeError, ValueError):  # BID092
            before_near = after_near = -1  # BID092
        # BID093：先算「贴着本控件的候选面板变少」，再用它否决下面「总数变多」那条兜底。
        # 同一击关掉面板（-1）同时插入两个无关浮层（+2）时总数是**变多**的，这条兜底
        # 会把 BID092 刚判对的「被关掉」翻回「已打开」、跳过补点 —— 后一轮修复否决
        # 前一轮（BID093 §1 CASE-G 真机复现）。``near`` 变少说明面板已被关掉，此时
        # 总数变多只能来自无关浮层，不能算「面板新出现」。
        near_decreased = (  # BID093
            before_near >= 0 and after_near >= 0 and after_near < before_near  # BID093
        )  # BID093
        if (  # BID091
            before_n >= 0  # BID091
            and after_n >= 0  # BID091
            and after_n > before_n  # BID091
            and not near_decreased  # BID093
        ):  # BID091
            return True  # BID091
        if before_near >= 0 and after_near >= 0 and after_near > before_near:  # BID092
            return True  # BID092
        return bool(after.get("anchored")) and not bool(before.get("anchored"))  # BID091

    # 本函数于BID101引入
    @staticmethod
    def _panel_still_near(before_near: int, after_near: int) -> bool:
        """贴着本控件的候选面板一个都没少 —— 面板仍在的直接证据（BID101）。

        为什么需要这条**否决**：``count`` 是**全页所有浮层盒子**（``_FLOATING_BOX_JS``
        只要求出文档流 + 可见 + 有尺寸，不要求候选语义），而组件库会在交互时清掉上一次
        的校验提示、「草稿已保存」toast、loading 提示。那一个盒子消失就让 ``count``
        变少，``_panel_closed_since`` 于是判「面板被自己关掉」并补点一次 —— 可 ``near``
        （贴着本控件、且有候选语义的面板数）一个没少，面板明明还开着。对已展开的面板
        补点，要么白点（受控展开的下拉），要么按 toggle 语义把它真正关掉，最后再报
        「没有检测到面板打开」（BID101 §1 CASE-AK / CASE-AM 真机复现）。

        只在 ``before_near > 0`` 时否决：``near == 0`` 说明它本来就看不见这个面板
        （面板太窄 / 太远 / 属探测盲区），那时 ``count`` 是唯一证据，不能否决。
        """
        return before_near > 0 and after_near >= before_near  # BID101

    # 本函数于BID093引入
    @staticmethod
    def _panel_closed_since(before: dict, after: dict) -> bool:
        """相对**动作前**的基线，判断「这一击把面板关掉了」（补点闸门）。

        ``_run_searchable`` 的两处补点闸门（BID089/BID090）原先只认「全页浮层盒子数
        变少」。BID092 已在 ``_wait_popup_opened`` 里证明这个总数会被同一击插入的
        无关浮层（保存提示/loading 遮罩）抵消，但这两处闸门没有跟着换口径：面板被
        自己点关、总数“先减一再加一”不变 ⇒ 闸门不触发 ⇒ 不补点 ⇒ 面板关着、没有
        候选，工具静默返回（BID093 §1 CASE-E 真机复现）。

        与 :meth:`_panel_opened_since` 对称，两条独立证据取**并集**：
        * 全页浮层盒子数**变少**——不受常驻同款盒子影响（BID089 的原始证据）；
        * 贴着本控件的候选面板数（``near``）**变少**——不受同一击插入的无关浮层影响
          （BID092 引入的量，这里才真正用起来）。

        两边都必须可观测（``>=0``）；不可观测按 BID072「不下否定结论」返回 False。

        BID101：``count`` 变少只是**候选**，必须由「``near`` 也变少」背书。``count``
        计的是全页浮层（含校验提示 / toast / loading），它们被交互清掉时会凭空造出
        「面板变少」；``near`` 不变就说明那个候选面板还在，少的是无关浮层。
        """
        before = before if isinstance(before, dict) else {}  # BID093
        after = after if isinstance(after, dict) else {}  # BID093
        before_n = int(before.get("count", -1))  # BID093
        after_n = int(after.get("count", -1))  # BID093
        try:  # BID093
            before_near = int(before.get("near", -1))  # BID093
            after_near = int(after.get("near", -1))  # BID093
        except (TypeError, ValueError):  # BID093
            before_near = after_near = -1  # BID093
        if BrowserController._panel_still_near(before_near, after_near):  # BID101
            # 候选面板一个没少：``count`` 的下降来自无关浮层，不是面板被关。
            return False  # BID101
        if before_n >= 0 and after_n >= 0 and after_n < before_n:  # BID093
            return True  # BID093
        return before_near >= 0 and after_near >= 0 and after_near < before_near  # BID093

    # 本函数于BID085引入
    def _select_panel_open(self, ctx: _InteractContext, state: Optional[dict] = None) -> bool:  # BID089
        """该下拉控件此刻是否已经展开（用于躲开 toggle 语义的二次点击）。

        组件库的 select 是 toggle：对**已展开**的控件再点一次会把它关掉。
        「用空串筛出完整候选列表」「打开下拉」这两个动作如果盲点一次，就会把
        刚展开的面板关掉、返回里一个候选都没有（Moka 实测）。

        BID089 起判据换成**结构 + 几何**证据（``ActionExecutor.panel_state``）：
        控件有焦点，且**紧贴它**有一个可见**候选面板**（水平覆盖控件 ≥60%，紧贴其上方/
        下方或与它相交）。原先只用 ``_overlay_open``（class 清单）有两个方向的误差：
        自定义 class 面板块完全不在清单里（假阴性 → 补点把面板关掉），而页面里任何
        常驻弹窗/提示层又可能让清单恒真（假阳性 → 跳过点击，面板永远打不开）。
        ``_run_searchable`` 在动作后还会用实测的浮层盒子数再校正一次，这里只是「先验」。

        BID092：「像面板」进一步收窄到「**候选**面板」（``_CANDIDATE_PANEL_JS``：只认
        listbox/menu/tree/grid role 或 ≥2 行候选，不认 tooltip/dialog/alertdialog）。
        字段下方一条常驻的 ``role=tooltip`` 校验提示紧贴、水平覆盖，同样满足几何判据，
        沿用旧的「overlay role」判据会让这里恒真 → 一次都不点、面板永远打不开
        （BID092 §1 CASE-D 真机复现）。这不是放宽/推翻 BID089 的意图：BID089 要的
        本来就是「候选面板」，只是当时把 tooltip 也算进了「面板形态」。
        """
        try:  # BID085
            if not ctx.executor.is_focused(ctx.node.backend_node_id):  # BID085
                return False  # BID085
            state = state or ctx.executor.panel_state(ctx.node.backend_node_id)  # BID089
            if state.get("anchored"):  # BID089
                return True  # BID089
            # 页面不可见时（标签页被切走/窗口最小化）rAF 冻结，面板的几何/可见性判据
            # 恒假，「没看到面板」不代表没打开。按 BID072 的既定原则：不可观测就**不下
            # 否定结论**，宁可不点，也不能冒「再点一次把已开面板关掉」的风险（BID089）。
            return self._page_hidden(ctx.session) and state.get("count", 0) > 0  # BID089
        except Exception:  # noqa: BLE001  # BID085
            return False  # BID085

    def _run_searchable(
        self, ctx: _InteractContext, fill: str, open_only: bool = False  # BID085
    ) -> dict:
        """Filter a searchable select's typeahead and surface the candidates.

        Typing only narrows the candidate list; the value is committed by a
        later ``tool_01_click`` on a candidate, so this never reports a fill
        success or failure. ``input_text`` clicks/focuses first, which is what
        opens the dropdown. An empty ``fill`` means "show the full list": click
        to open, then wipe any leftover filter from a previous call (``input_text``
        deliberately does not clear on an empty fill).

        BID085：``open_only=True``（tool_08 落到可搜索下拉时）只点击展开、**不动**
        已有的筛选词——tool_08 的语义是「打开候选面板」，顺手清掉用户/模型刚输入的
        筛选词是计划外的破坏性副作用。

        BID089：面板开没开**不能只信动作前的那一帧快照**。旧实现（BID085）判「已展开」
        就整轮不点，于是任何假阳性（页面另有常驻弹窗、控件恰好带着焦点）都会让面板
        永远打不开；反过来假阴性（自定义 class 面板）时那一击会把面板 toggle 关掉。
        这两条都是「解析/探测与真实交互不一致」的回归，离线重放解析结果逐字相同、
        根本测不出来。现在动作后再收一次**实测证据**（浮层盒子数 + 是否紧贴控件），
        只在这两种可证伪的情形下补点一次：把面板真正打开，或把被自己关掉的面板恢复。

        BID097：补点闸门的证据同样是「动作后一帧」。清空筛选词（空 ``fill``）会让
        组件重新过滤 / 取数（先卸载候选面板、再挂回），固定等待之后采样仍可能落在
        中间态；据此补点，那一击会按真实 toggle 语义把马上要恢复的面板真正关掉。
        与 BID096 同一原则：先过 :meth:`_confirm_panel_closed` 的有界确认窗口，
        确认「关掉」了再补点。
        """
        node = ctx.node
        executor = ctx.executor
        already_open = False  # BID085
        clicked = False  # BID089
        before = {"count": -1, "anchored": False, "fingerprint": "", "near": -1}  # BID089 / BID092
        probe_open = False  # BID085
        input_evidence: dict = {}  # BID095
        if fill:
            # BID095：``input_text`` 内部第一步就是点击目标。控件若已展开，这一击是
            # toggle 的反向操作——先把面板关掉，再往里打字。BID085 只在「空串筛出
            # 完整候选」分支躲开了它，非空筛选词路径完全没有面板证据：面板被自己
            # 关掉后，返回只说「已输入」，没有候选、没有披露（CASE-J）。
            # 这里只在动作前控件旁确实有候选面板时复验/补点，普通输入零额外开销。
            _input_result, input_evidence = self._input_with_panel_evidence(  # BID095
                executor, ctx.session, node,  # BID095
                lambda: executor.input_text(node.backend_node_id, fill),  # BID095
            )  # BID095
        else:
            # BID068 的既定前提：浮层指纹是**视口坐标**拼的，而 ``click`` 内部可能先
            # ``scrollIntoView``。若不先把目标滚进视口再取基线，「点击自身滚动」会让
            # 常驻盒子整体进出视口、凭空造出差异（BID068 实测过假差异）。基线只在
            # 滚动稳定之后取，才是「点击造成的真实变化」（BID089）。
            try:  # BID089
                executor.scroll_into_view(node.backend_node_id)  # BID089
            except Exception:  # noqa: BLE001  # BID089
                pass  # BID089
            before = executor.panel_state(node.backend_node_id)  # BID089
            probe_open = self._select_panel_open(ctx, before)  # BID085 / BID089
            if open_only:  # BID085
                if probe_open:  # BID085 / BID089
                    already_open = True  # BID085
                else:  # BID085
                    executor.click(node.backend_node_id)  # BID085
                    clicked = True  # BID089
            else:
                # BID085：已在焦点上的下拉控件**不要**再点一次——组件库的 select 是
                # toggle 语义：点已展开的控件会把它关掉。「用空串筛出完整候选列表」于是
                # 变成「先关掉面板再清筛选词」，返回里一个候选都没有（Moka 实测）。
                # 焦点在别处时才用点击打开面板，然后只清筛选词。
                if probe_open:  # BID085 / BID089
                    executor.clear_field(node.backend_node_id)  # BID085
                else:  # BID085
                    executor.input_text(node.backend_node_id, "")  # BID085
                    executor.clear_field(node.backend_node_id)  # BID085
                    clicked = True  # BID089
        self._stabilize(ctx.target_id)
        # A dropdown mounts on focus/typing and paints a frame or two later;
        # give it a bounded moment so its candidates are captured.
        self._wait_overlay_open(ctx.session)
        panel_fixed = False  # BID089
        panel_unconfirmed = False  # BID089
        if not fill:  # BID089
            after = executor.panel_state(node.backend_node_id)  # BID089
            need_click = False  # BID089
            if probe_open and not clicked:  # BID089
                # 假阳性校正：动作前判「已展开」所以没点，但动作后控件旁边并没有任何
                # 贴着的浮层（原来的 overlay 是别的常驻弹窗，或页面自己把面板收了）。
                # 这时**必须**补点一次——这就是旧实现「无条件点一次」在兜底的那一步。
                #
                # BID090：本轮**没有点击过任何东西**（只清了筛选词/聚焦），面板不会
                # 因为这两个动作自己消失。此时若浮层盒子数一点没少，``anchored=False``
                # 更可能是探测误差（BID089 §5.10：static 布局、宽度 <40 的面板都测不出
                # ``anchored``），补点反而会把仍开着的面板 toggle 关掉——实测这正是
                # 「面板 closed、返回却说已调整为展开」的来源。只有「浮层真的少了」
                # 才说明面板被关掉，才值得补点；数不到（-1）时按 BID072 不破坏。
                #
                # BID093：闸门收口到 `_panel_closed_since`，与 `_wait_popup_opened`
                # 同口径（全页总数 ∨ 贴本控件的候选面板数变少）。只认总数时，同一击
                # 插入的无关浮层会把「变少」抵消，面板被清空动作关掉也不补点。
                if (  # BID089 / BID090
                    not (after.get("anchored") and not before.get("anchored"))  # BID099
                    and self._panel_closed_since(before, after)  # BID090 / BID093
                ):  # BID090
                    need_click = True  # BID089
            elif clicked:  # BID089
                # 假阴性校正：点前有浮层、点后反而变少 ⇒ 这一击把原本已展开的面板
                # toggle 关掉了（探测盲区）。再补点一次恢复展开；点击次数有上界，
                # 且只在「确实关掉了一个浮层」时发生，不是盲重试。
                # 再来一次「面板其实开着」的直接证据闸门：盒子数可能被无关浮层的
                # 出现/消失带偏，若控件旁仍贴着面板就绝不补点（补点会把它关掉）。
                #
                # BID093：同上，收口到 `_panel_closed_since`。这里正是「点击关掉面板 +
                # 同一击插入无关浮层」的路径：总数不变会让旧闸门整个失效（CASE-E）。
                if (  # BID089
                    self._panel_closed_since(before, after)  # BID089 / BID093
                    and not (after.get("anchored") and not before.get("anchored"))  # BID099
                ):  # BID089
                    need_click = True  # BID089
            if need_click:  # BID097
                # BID096 的同一原则：「动作后这一帧不在」只是候选，不是结论。
                # 清空筛选词同样会让组件重新过滤/取数（先卸载面板、再挂回），
                # 而这里的采样只经过固定的 _stabilize + _wait_overlay_open，
                # 重排偏慢时仍落在中间态；据此补点，那一击会按真实 toggle 语义
                # 把马上要恢复的面板真正关掉（§1 CASE-Q 真机复现）。
                after = self._confirm_panel_closed(  # BID097
                    executor, node.backend_node_id, before, after  # BID097
                )  # BID097
                if (  # BID097
                    (after.get("anchored") and not before.get("anchored"))  # BID099
                    or not self._panel_closed_since(before, after)  # BID097
                ):  # BID099
                    # 面板自己回来了：刚才那一帧只是重渲染中间态，不补点。
                    need_click = False  # BID097
            if need_click and self._page_hidden(ctx.session):  # BID072 / BID089
                # 页面不可见时 rAF 冻结、几何与盒子数都不可信；BID072 的原则是
                # 「不可观测 ⇒ 不下否定结论 ⇒ 不做可能把面板点关的动作」。补点本身
                # 就是一次点击，因此这里一律放弃补点（宁少一次修正，不误关面板）。
                need_click = False  # BID089
            if need_click:  # BID089
                executor.click(node.backend_node_id)  # BID089
                self._stabilize(ctx.target_id)  # BID089
                self._wait_overlay_open(ctx.session)  # BID089
                # 补点后必须**复验**：只有确实出现了「贴着控件的浮层」或「浮层盒子
                # 变多」才敢说「已调整为展开」。否则补点本身失败（该元素不是
                # toggle/点击无效）时会谎报成功，模型拿着「已展开」去点候选、却点空
                # （BID060 的诚实披露原则：没验证到就不能断言）。
                #
                # BID090：不能用「指纹变化」判成功——补点把面板**关掉**时指纹同样
                # 变化，那会把一次反向操作当成「已调整为展开」（实测：返回说已展开、
                # 面板实为 closed）。只认有方向的证据：锚定面板出现，或盒子数变多。
                fixed_after = executor.panel_state(node.backend_node_id)  # BID089
                # BID091：和 ``_run_click`` 的两处兜底共用同一个方向性判据。此处基线
                # 是 ``after``（补点前的实测状态），补点后必须看到「near/计数变多」或
                # 「anchored 从无到有」才认成功。BID099：闸门不再要求
                # ``after.anchored`` 为假——常驻假面板会让它为真；差分基线仍然成立，
                # 收口到一处函数是为了让后来者不会再写回「只看此刻」的版本。
                confirmed = self._panel_opened_since(  # BID091
                    after, fixed_after  # BID091
                )  # BID091
                if confirmed:  # BID089
                    panel_fixed = True  # BID089
                    already_open = False  # BID089
                else:  # BID089
                    panel_unconfirmed = True  # BID089
        # A date/range widget needs its own "how this commits" contract stated at
        # the moment the panel opens — see ``_range_entry_notice`` for why the two
        # shapes must not share one wording.
        fill_notice = _range_entry_notice(node, ctx.name)
        if already_open and not panel_unconfirmed:  # BID085 / BID090
            # 面板本来就是展开的：本轮没有可观察变化，必须说明，否则模型会把
            # 「（页面无变化）」读成「没打开」而重复调用。
            # BID090：补点后仍未确认时**不能**再说「已经是展开状态」——两句话
            # 互相否定（实测：「没有检测到面板打开」+「候选面板已经是展开状态」）。
            # BID095：与「范围控件怎么提交」的说明是两条独立信息，用追加而不是覆盖
            # ——旧写法会把 `_range_entry_notice` 整句抹掉。
            fill_notice = "\n".join(  # BID095
                p  # BID095
                for p in (rp.dropdown_already_open_notice(ctx.name), fill_notice)  # BID095
                if p  # BID095
            )  # BID095
        if panel_unconfirmed:  # BID089
            # 补点后仍没有任何「面板已展开」的证据：不能沿用「已调整为展开」的
            # 说法，按 BID060 的口径如实说「没有检测到面板打开」。
            fill_notice = "\n".join(  # BID089
                p  # BID089
                for p in (rp.popup_not_opened_notice(ctx.name), fill_notice)  # BID089
                if p  # BID089
            )  # BID089
        elif panel_fixed:  # BID089
            fill_notice = "\n".join(  # BID089
                p  # BID089
                for p in (rp.select_panel_reopened_notice(ctx.name), fill_notice)  # BID089
                if p  # BID089
            )  # BID089
        input_notice = self._panel_evidence_notice(input_evidence, ctx.name)  # BID095
        if input_notice:  # BID095
            # BID095：非空筛选词路径的复验结论（面板被隐式点击关掉 / 已补点恢复）
            # 与上面两条互斥（`input_evidence` 只在 fill 非空时非空），但都必须到达
            # 模型；用同一个前缀口径，不另立说法。
            fill_notice = "\n".join(  # BID095
                p for p in (input_notice, fill_notice) if p  # BID095
            )  # BID095
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
            # BID095：这条提前返回走的是**另一个标签页**，但它不能因此丢掉本次
            # 动作的面板结论。旧写法把 `popup_missing` / `panel_fixed` 整段跳过：
            # 点击既打开新标签页、又没有打开面板时，模型看不到任何失败说明。
            content = self._panel_verdict_notice(ctx, extras) + (  # BID095
                self._dialog_notice(dialog)  # BID095
                + (fill_notice + "\n\n" if fill_notice else "")  # BID095
                + new_text  # BID095
            )  # BID095
            return self._base_result(
                FULL,
                FAIL if fill_error else OK,
                content,
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
        # BID095：`popup_missing`（首点+补点都没打开）与 `panel_fixed`（补点才打开）
        # 是本条动作的面板结论，统一走 `_panel_verdict_notice`；`new_focus` 的提前
        # 返回也用同一个前缀，任何出口都不再漏拼。
        content = self._panel_verdict_notice(ctx, extras) + content  # BID095

        # The click may have opened a background tab without moving focus. The
        # focused page then looks unchanged, so tell the LLM explicitly and hand
        # it the full tab list instead.
        # BID083：BID081 曾在这里「只要有新标签页且当前页 URL 未变就直接丢弃原页
        # diff」，那是对功能的阉割（新标签页场景也可能同时带来真实的当前页变化）。
        # 现已回滚：噪声改由 dom/diff.py 的「行内容是否已被旧内容包含」判断处理，
        # diff 一律照常计算，只有真正重复的行才会被收敛掉。
        opened_names = self._opened_tab_names(old_target_ids)  # BID083

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

    @_trim_tabs_after_reply  # BID086
    @_finalize_llm_output  # BID084
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

    @_finalize_llm_output  # BID084
    def interact_click_dropdown(self, name: str) -> dict:
        """点击单个『可点击下拉元素』，使其展开候选/菜单，返回增量 DOM。

        与 ``interact_click`` 共用同一套点击与浮层兜底逻辑（点击后保留刚打开的
        浮层以便捕获候选），区别只是类别与工具：候选项仍由 ``tool_01_click`` 点选。

        BID085：可搜索下拉（``searchable``/其筛选框被瞬时状态判成 ``input``）也接受本
        工具——「打开候选面板」这个语义对两者完全一致，且面板已展开时**不再重复点击**
        （toggle 会把它关掉），只回提示与候选。
        """
        ctx, err = self._begin_interaction(name, allowed=("clickdropdown", "searchable"))  # BID085
        if err is not None:
            return err
        assert ctx is not None
        try:
            # BID085：可搜索下拉（或其被瞬时状态判成 input 的筛选框）用「聚焦 + 展开
            # 候选」实现同一个语义：tool_08 的契约就是「打开面板，让候选出现在返回里」。
            # 拒绝它只会把模型推向一次注定失败的 tool_07（类别抖动时）。
            if (  # BID085
                classify(ctx.node) == "searchable"  # BID085
                or is_custom_select_input(ctx.node)  # BID085
            ):  # BID085
                extras = self._run_searchable(ctx, "", open_only=True)  # BID085
            else:  # BID085
                extras = self._run_click(ctx)  # BID085
        except (ActionError, CDPError) as exc:
            return self._base_result(INCREMENTAL, FAIL, rp.EMPTY_CONTENT, str(exc), include_tabs=False)
        # Same "how this commits" contract as tool_07 — worded per shape:
        # input-based ranges, one-end-per-entry div shells, and an unidentified
        # div shell each get their own wording (empty for a plain dropdown).
        notice = _range_entry_notice(ctx.node, name)
        if notice:
            extras = dict(extras or {})
            # BID095：与 `_run_searchable` 已经给出的面板结论（「没有检测到面板打开」
            # /「已额外点击一次」）是两条独立信息，追加而不是覆盖——旧写法会把一整条
            # 失败披露替换成「这个范围控件怎么提交」的说明。
            extras["fill_notice"] = "\n".join(  # BID095
                p  # BID095
                for p in (str(extras.get("fill_notice") or ""), notice)  # BID095
                if p  # BID095
            )  # BID095
        return self._finish_interaction(ctx, extras)

    @_finalize_llm_output  # BID084
    def interact_fill_in(
        self, name: str, fill: str = "", press_enter: bool = False
    ) -> dict:
        """对单个『可输入元素』填写内容（可选填完按回车），返回增量 DOM。"""
        ctx, err = self._begin_interaction(name, allowed=("input", "searchable"))  # BID085
        if err is not None:
            return err
        assert ctx is not None
        try:
            extras = self._run_fill(ctx, fill, press_enter)
        except (ActionError, CDPError) as exc:
            return self._base_result(INCREMENTAL, FAIL, rp.EMPTY_CONTENT, str(exc), include_tabs=False)
        return self._finish_interaction(ctx, extras)

    @_finalize_llm_output  # BID084
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

    @_finalize_llm_output  # BID084
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

    @_finalize_llm_output  # BID084
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

    @_finalize_llm_output  # BID084
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
        last_tree: Optional[EnhancedTree] = None  # BID079: 全量口径判断的取样树
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
            last_tree = tree  # BID079
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
        # BID079：滚动增量同样按「全量口径」压缩 —— 滚动出来的那一屏里，
        # 「某 tab 是否还有兄弟 / 是否只有一行内容」要按整页判断，不能只按这一屏。
        context = full_view_lines(last_tree) if last_tree is not None else None  # BID079
        content = format_lines(added, context_lines=context)  # BID079
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
    @_finalize_llm_output  # BID084
    def interact_many(self, name_list: list, fill_list: list) -> dict:
        """A sequence of fills plus an optional trailing click, run serially.

        ``name_list`` must be the same length as ``fill_list`` (all fills), or
        exactly one longer (the extra trailing name is a clickable element used
        as the closing action). Everything is validated up-front; if validation
        fails nothing is executed. If an action fails mid-way, the remaining
        actions are abandoned and ``action_ok`` becomes ``部分失败`` (or ``失败``
        when the very first action fails).

        BID100：中间 fill 与单个 ``tool_02`` 不再分叉——两者都经
        :meth:`_input_with_panel_evidence`。``input_text`` 的第一步是点击控件，组件
        库的 select 是 toggle：对已展开的控件点这一下会把候选面板关掉，裸调用时
        既不补点也不披露（§1 CASE-AD 真机：clicks=1、面板 closed、action_ok=成功）。
        普通文本框的探测与文案开销不变（无候选面板时直接返回）。
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
        tail_node: Optional[EnhancedNode] = None  # BID094
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
            tail_node = node  # BID094

        # ---- serial execution ----
        old_focus = self.focused_target_id
        old_name = self._name_for_target(target_id)
        old_target_ids = {t["targetId"] for t in self._live_targets()}

        try:
            session = self.client.attach(target_id)
            self.client.enable_page_domains(session)
        except CDPError as exc:
            return self._base_result(INCREMENTAL, FAIL, rp.EMPTY_CONTENT, str(exc), include_tabs=False)

        executor = ActionExecutor(self.client, session)
        # BID094：收尾点击若是「按契约打开面板」的元素（clickdropdown / aria-haspopup /
        # role=combobox），就走与 `_run_click` 相同的证据链；普通按钮点击保持原样，
        # 不给它加一次 0.6s 轮询与补点。
        tail_popup = bool(  # BID094
            has_click  # BID094
            and tail_node is not None  # BID094
            and (  # BID094
                classify(tail_node) == "clickdropdown"  # BID094
                or executor.is_popup_trigger(tail_node.backend_node_id)  # BID094
            )  # BID094
        )  # BID094
        tail_popup_missing = False  # BID094
        tail_panel_fixed = False  # BID094
        batch_panels: list[tuple[str, dict]] = []  # BID100
        base_tree = tree  # BID078
        base_lines = self.serialize_lines_tree(tree, target_id)  # BID078
        initial_lines = base_lines  # BID078
        diff_parts: list[str] = []  # BID078
        diff_urls: list[str] = []  # BID078
        success_count = 0
        error_msg = ""

        # 本函数于BID078引入
        def _rebuild_baseline(backend_node_id: int) -> None:
            """动作前滚到目标；视口真移动时，用滚动后的视口重建这一步的基线。"""
            nonlocal base_tree, base_lines
            try:
                moved = executor.scroll_into_view(backend_node_id)
            except Exception:
                moved = False
            if moved:
                base_tree = self.capture_tree(target_id, record_debug=False)
                base_lines = self.serialize_lines_tree(base_tree, target_id)

        # 本函数于BID078引入
        def _capture_step() -> None:
            """抓取一步动作后的状态，把「相对上一步」的增量并入最终结果。

            批量字段可能分布在不同的滚动位置；只在全部动作结束后抓一次，
            前面字段的变化会随视口滚走而丢失（只剩最后一屏的 diff）。
            """
            nonlocal base_tree, base_lines
            try:
                new_tree = self.capture_tree(target_id, record_debug=False)
            except Exception:
                return
            new_lines = self.serialize_lines_tree(new_tree, target_id)
            diff = compute_diff(base_lines, new_lines, base_tree.url, new_tree.url)
            if diff and diff != rp.PAGE_NO_CHANGE:
                diff_parts.append(diff)
                diff_urls.append(new_tree.url)  # BID078
            base_tree, base_lines = new_tree, new_lines

        def _run_plan() -> None:
            nonlocal success_count, error_msg
            nonlocal tail_popup_missing, tail_panel_fixed  # BID094
            nonlocal batch_panels  # BID100
            for index, (_name, category, backend_node_id) in enumerate(plan):
                try:
                    _rebuild_baseline(backend_node_id)  # BID078
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
                        # BID100：批量中间 fill 与 `_run_fill` 走**同一条**面板证据链。
                        # `input_text` 的第一步是点击控件，组件库的 select 是 toggle
                        # 语义：对已展开的控件点这一下会把候选面板关掉，然后才打字。
                        # 裸调用时既不补点也不披露——BID095 修好的东西在批量入口上
                        # 一个都不生效（BID100 §1 CASE-AD 真机：clicks=1、面板 closed、
                        # action_ok=成功、返回里零披露）。
                        _fill_result, _fill_evidence = self._input_with_panel_evidence(  # BID100
                            executor,  # BID100
                            session,  # BID100
                            fill_nodes[_name],  # BID100
                            lambda: executor.input_text(backend_node_id, fills[index]),  # BID100
                        )  # BID100
                        if _fill_evidence.get("panel_missing") or _fill_evidence.get(  # BID100
                            "panel_fixed"  # BID100
                        ):  # BID100
                            # BID100：只在真的有面板结论时收集——普通文本框不会产生键，
                            # 批次里的常规填写零额外开销、零额外文案（CASE-AE）。
                            batch_panels.append((_name, dict(_fill_evidence)))  # BID100
                    elif category == "select":
                        executor.select_option(backend_node_id, fills[index])
                    elif index == len(plan) - 1 and tail_popup:  # BID094
                        # 收尾点击：与 `_run_click` 同一套面板证据 + 一次有界补点。
                        # 裸点击会让整条 BID067/BID089–BID093 的结论在批量入口失效
                        # （BID094 §1 CASE-H：点关已展开的 toggle 面板仍报成功）。
                        evidence = self._click_with_popup_evidence(  # BID094
                            executor, session, tail_node  # BID094
                        )  # BID094
                        tail_popup_missing = bool(evidence.get("popup_missing"))  # BID094
                        tail_panel_fixed = bool(evidence.get("panel_fixed"))  # BID094
                    else:
                        executor.click(backend_node_id)
                    if category in ("input", "select"):  # BID078
                        _capture_step()  # BID078
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
            new_text = self.serialize_tree(new_tree, new_focus)  # BID078
            old_text = "\n\n".join(diff_parts)  # BID078
            content = (  # BID078
                rp.OLD_TAB_DIFF_NOTICE + "\n" + old_text + "\n\n" + new_text  # BID078
                if old_text  # BID078
                else new_text  # BID078
            )  # BID078
            mode = FULL
        elif opened_names:
            # Trailing click opened a background tab without moving focus:
            # keep the fills done on the still-open original tab, then hint the tab.  # BID078
            content = "\n\n".join(  # BID078
                p for p in ["\n\n".join(diff_parts), self._new_tab_notice(opened_names)] if p  # BID078
            )  # BID078
            mode = INCREMENTAL
        else:
            new_tree = self.capture_tree(target_id)
            new_lines = self.serialize_lines_tree(new_tree, target_id)  # BID078
            if new_tree.url != tree.url:  # BID078
                # 旧页面被覆盖：旧增量一行不删，只剥掉已经失效的 <…元素 eN> 包装。
                diff_parts = [  # BID078
                    _strip_interactive_tags(p) if u == tree.url else p  # BID078
                    for p, u in zip(diff_parts, diff_urls)  # BID078
                ]  # BID078
                base_lines = initial_lines  # BID078
            final_diff = compute_diff(  # BID078
                base_lines, new_lines, base_tree.url, new_tree.url  # BID078
            )  # BID078
            if final_diff and final_diff != rp.PAGE_NO_CHANGE:  # BID078
                diff_parts.append(final_diff)  # BID078
                diff_urls.append(new_tree.url)  # BID078
            notice = self._title_change_notice(target_id, old_name)
            lost_text = format_lost(compute_lost(initial_lines, new_lines, registry))  # BID078
            content = "\n\n".join(  # BID078
                p for p in [notice.strip()] + diff_parts + [lost_text] if p  # BID078
            ) or rp.PAGE_NO_CHANGE  # BID078
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
        # BID094：收尾点击的面板复验结果必须披露——否则模型会把「动作执行了」当成
        # 「面板打开了」。与 `_run_click` 的 `popup_missing`、`_run_searchable` 的
        # `panel_unconfirmed`/`panel_fixed` 用同一组文案，不另立说法。
        tail_notice = ""  # BID094
        if tail_popup_missing:  # BID094
            tail_notice = rp.popup_not_opened_notice(click_name or "")  # BID094
        elif tail_panel_fixed:  # BID094
            tail_notice = rp.select_panel_reopened_notice(click_name or "")  # BID094
        if tail_notice:  # BID094
            content = tail_notice + "\n\n" + content  # BID094
        # BID100：批量**中间** fill 的面板结论，与收尾点击的 ``tail_notice`` 同一组
        # 文案（``_panel_evidence_notice`` → ``popup_not_opened_notice`` /
        # ``select_panel_reopened_notice``），不另立说法。每条都带字段名：批次里有
        # 多个字段时，模型得知道该复查哪一个，而不是拿到一句无主语的提示。
        batch_notice = ""  # BID100
        for _fname, _evidence in batch_panels:  # BID100
            _notice = self._panel_evidence_notice(_evidence, _fname)  # BID100
            if _notice:  # BID100
                batch_notice = "\n".join(  # BID100
                    p for p in (batch_notice, _notice) if p  # BID100
                )  # BID100
        if batch_notice:  # BID100
            content = batch_notice + "\n\n" + content  # BID100
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

    def _wait_popup_opened(  # BID067
        self,
        executor,
        session: str,
        panels_before: str,
        overlay_before: Optional[bool] = None,  # BID091
        panel_before: Optional[dict] = None,  # BID092
        backend_node_id: Optional[int] = None,  # BID092
        timeout: float = 0.6,
        saw_click_close: bool = False,  # BID099
    ) -> bool:  # BID067
        """触发器点击后，有界轮询「弹层是否打开」——双证据，覆盖自定义 class。

        证据一：:meth:`_overlay_open`（class 清单，快且保守）；
        证据二：可见浮层**结构指纹变化**（:data:`_FLOATING_BOX_JS`，不看 class 名、
        也不看候选行 tag，能认出 ``common-unmodeled-layer`` 这类清单外、候选又非
        li/td 的自绘面板）。任一为真即视为已打开。
        指纹每 50ms 重取一次，兼顾入场动画首帧。这样「点后确实画出了面板」就不会被
        误判成死点击、进而触发会把 panel 再关掉的补偿点击（BID067）。

        页面不可见时的兜底（BID072）：标签页被切走/窗口被遮挡/最小化时 rAF 冻结，
        组件库基于 ``<transition>`` 的入场动画永远停在首帧（``opacity:0``、``bbox`` 塌成
        0×0），任何「可见性」判据都必然为假。此时**无法观测**，绝不能做破坏性重试——
        第二次点击会把刚打开的面板关掉（实测顺丰证件类型/民族：首点已打开、被补偿重试
        关闭后报「没有检测到面板打开」，LLM 永远点不动）。故超时后只要是「页面不可见」
        就按「已打开（不可验证）」返回，交给序列化（BID030 透明子树兜底）呈现候选，
        由返回的 diff 说明真相（BID072）。

        BID089：浮层盒子**变少**必须判成「没打开」。旧实现把「指纹变了」一律当「已打开」，
        于是 toggle 型触发器被自己的点击**关掉**时（指纹从「有面板」变成「没面板」同样是
        「变了」）也返回 True，跳过补偿重试，留下一个关着的面板、返回里一个候选都没有。
        现在先比盒子数量：变少 ⇒ 这一击是反向操作 ⇒ 返回 False，让调用方补点一次把它
        重新打开（这正是「针对探测/解析误差保留的第二次点击」）。

        变少**不立刻**返回：点击可能同时关掉一个无关浮层、而自己的面板还在入场动画里。
        若立即返回 False，调用方会在面板画出来之前补点，那一击又把它 toggle 关掉。
        因此先记下「见过变少」并继续轮询；期间盒子数回升或形状变化照样判「已打开」，
        只有等满 ``timeout`` 仍是变少才判「没打开」（既有界、又不抢跑）。

        BID091：证据一的 ``_overlay_open`` 是 class 清单，页面若常驻一个可见的无关
        下拉/菜单（``.dropdown-menu[role=menu]`` 一类），它会**恒真**——没有基线时，
        首个轮询就 ``return True``，连「盒子数变少」都轮不到看，于是 toggle 型触发器
        被自己的点击关掉也报「已打开」。因此证据一改为「相对点击前**新出现**」：
        ``overlay_before=True`` 时跳过它，只用差分/几何证据。若此时差分本身也不可
        观测（``panels_before`` 为空），超时后按 BID072「不可观测 ⇒ 不下否定结论」
        保守返回 True，不做可能把面板点关的补点。

        BID092：把「这一击关掉了面板」的判据从「全页浮层盒子总数变少」换成
        **「贴着该控件的候选面板个数（``panel_state.near``）变少」**。总数会被同一击
        插入的无关浮层（保存提示/loading 遮罩）抵消：面板少了、提示层多了，「先减一
        再加一」看不出变少，而指纹确实变了 ⇒ 直接 return True ⇒ 报「已打开」、
        不补点 ⇒ 面板关着、没有候选（BID092 §1 CASE-C 真机复现）。``near`` 只数
        贴着本控件、且有候选语义的盒子，无关浮层进不来；它同时要求 ``panels_before``
        那一路的差分照旧，两者任一发现「变少」都判「没打开」。

        BID098：``timeout``（0.6s）是「死点击」的判定窗口，不是「面板不会更晚挂回」的
        证明。点击同样会触发重渲染（重新取数、旧面板卸载再挂回），而实测「观察窗口
        超时 → 判定 → 补点派发」本身要 2–3s；等补点真正发出时，面板往往已经自行挂回，
        那一击反而把它**永久关掉**（§1 CASE-T 真机：clicks=2、restored=1、最终
        panel=closed、返回「没有检测到面板打开」）。因此一旦观察到「变少」——
        不论来自全页总数还是 ``near``——就把观察窗口延长到与 BID096 的确认窗口等长；
        期间盒子数回到基线即判「已打开」（这就是上面「变少不抢跑」缺失的那一半：
        旧实现只把 ``decreased`` 复位，超时后照样返回 False）。死点击（从未变少）
        不受影响，仍是 0.6s 有界重试；真关闭（变少后不恢复）照旧等满窗口返回 False，
        让调用方补点并披露。

        BID099：BID098 的超时乐观判据（「``near`` 没变少且大于 0 ⇒ 面板在」）有一个
        未言明的前提——**这一击确实作用于那个面板**。它同样适用于另一种情况：页面上
        常驻一个「面板形态」盒子（BID091 的几何假阳性：紧贴控件、≥2 行候选、
        ``anchored`` 恒真、``near`` 恒为 1），而这一击是死点击 / 首点竞态失败——
        真面板从未打开。此时单帧状态与「点击把面板关掉、组件随后自己挂回」完全等价，
        乐观判据会把失败说成成功：补点被吞掉（面板永远打不开）、返回连「没有检测到
        面板打开」都不披露（BID099 §1 CASE-X/CASE-Y 真机复现）。因此这条乐观判据必须
        由**方向性证据**背书：只有 ``saw_click_close``（调用方在点击后、``_settle_
        navigation`` 之前观测到「这一击确实让 ``near``/浮层计数变少」）为真时才允许
        返回「已打开」。点击没让面板变化时，照旧等满窗口返回 False，让 BID067 的有界
        补点与「没有检测到面板打开」的披露生效。
        """
        deadline = time.time() + timeout  # BID067
        before_boxes = self._panel_box_count(panels_before)  # BID089
        decreased = False  # BID089
        seen_decreased = False  # BID098
        overlay_sticky = bool(overlay_before)  # BID091
        before_near = -1  # BID092
        if isinstance(panel_before, dict):  # BID092
            try:  # BID092
                before_near = int(panel_before.get("near", -1))  # BID092
            except (TypeError, ValueError):  # BID092
                before_near = -1  # BID092
        near_decreased = False  # BID092
        near_opened = False  # BID092
        while True:  # BID067
            if not overlay_sticky and self._overlay_open(session):  # BID067 / BID091
                return True  # BID067
            fingerprint = executor.floating_panel_fingerprint()  # BID089
            after_boxes = self._panel_box_count(fingerprint)  # BID089
            after_near = -1  # BID098
            if before_near >= 0 and backend_node_id is not None:  # BID092
                current = executor.panel_state(backend_node_id)  # BID092
                try:  # BID092
                    after_near = int(current.get("near", -1))  # BID092
                except (TypeError, ValueError):  # BID092
                    after_near = -1  # BID092
                if after_near >= 0:  # BID092
                    near_decreased = after_near < before_near  # BID092
                    near_opened = after_near > before_near  # BID092
                    if near_decreased and not seen_decreased:  # BID098
                        # 第一次看到「贴着本控件的候选面板变少」：面板可能只是短暂
                        # 卸载、随后自己挂回，把观察窗口延长到确认窗口等长再下结论。
                        seen_decreased = True  # BID098
                        deadline = max(  # BID098
                            deadline, time.time() + self._PANEL_CLOSE_CONFIRM_SECONDS  # BID098
                        )  # BID098
                else:  # BID092
                    near_decreased = False  # BID092
                    near_opened = False  # BID092
            # BID101：``count`` 变少必须由「``near`` 也变少」背书。``count`` 是全页
            # 浮层盒子（含校验提示 / toast / loading），交互把它们清掉同样会「变少」，
            # 而贴着本控件的候选面板一个没少 —— 那不是这一击关掉了面板，是无关浮层被
            # 清掉。旧写法据此判「没打开」、补点把仍开着的面板 toggle 关掉，再报
            # 「没有检测到面板打开」（BID101 §1 CASE-AM 真机复现）。
            if (  # BID101
                before_boxes >= 0  # BID089
                and after_boxes >= 0  # BID089
                and after_boxes < before_boxes  # BID089
                and not self._panel_still_near(before_near, after_near)  # BID101
            ):  # BID101
                decreased = True  # BID089
                if not seen_decreased:  # BID098
                    seen_decreased = True  # BID098
                    deadline = max(  # BID098
                        deadline, time.time() + self._PANEL_CLOSE_CONFIRM_SECONDS  # BID098
                    )  # BID098
            else:  # BID089
                # 盒子数已回到基线（或无法比较）：不再算「变少」，否则「面板短暂消失
                # 又完全恢复原样」会在超时被判成没打开，随后补点把它关掉（BID089）。
                if (  # BID098
                    seen_decreased  # BID098
                    and before_boxes >= 0  # BID098
                    and after_boxes >= before_boxes  # BID098
                    and not near_decreased  # BID098
                ):  # BID098
                    # BID098：见过「变少」之后盒子数回到基线 ⇒ 面板自己挂回来了。
                    # 这是「恢复」的直接证据，必须判「已打开」——旧实现只把 decreased
                    # 复位，超时后照样按「没打开」补点，而那一击正好把刚恢复的面板
                    # toggle 关掉（§1 CASE-T 真机：clicks=2、最终 closed）。
                    return True  # BID098
                decreased = False  # BID089
                if (  # BID067 / BID089
                    panels_before  # BID067
                    and fingerprint != panels_before  # BID067
                    and not near_decreased  # BID092
                ):  # BID092
                    return True  # BID067
            if time.time() >= deadline:  # BID067
                # 见过「变少」⇒ 这一击关掉了面板（toggle 反向）；否则交给 BID072 的
                # 「不可观测 ⇒ 不下否定结论」兜底（BID089）。
                if decreased or near_decreased:  # BID089 / BID092
                    return False  # BID089
                if near_opened:  # BID092
                    # 贴着本控件的候选面板比点击前多了：这是「新出现」的直接证据，
                    # 指纹不可观测（``panels_before`` 为空）时也能用。
                    return True  # BID092
                if saw_click_close and after_near > 0 and after_near >= before_near:  # BID099
                    # BID098：点击前控件旁就有候选面板、现在也没变少 ⇒ 面板在。
                    # 「点击 → 组件收起面板 → 重新取数 → 挂回」可能整个发生在
                    # `_settle_navigation` 的等待里，等本函数开始观察时面板已经恢复：
                    # 差分看不到任何「变少」，旧实现于是在超时判「没打开」，调用方补点，
                    # 而那一击正好把已恢复的面板 toggle 关掉（§1 CASE-T 真机：
                    # clicks=2、restored=1、最终 closed、返回「没有检测到面板打开」）。
                    #
                    # BID099：这条乐观判据必须由 ``saw_click_close`` 背书——那是调用方
                    # 在点击后、``_settle_navigation`` 之前观测到的「这一击确实让面板
                    # 变少」的直接证据。没有它时，同样的单帧状态也可以来自「页面上
                    # 常驻一个面板形态盒子（BID091 的几何假阳性）+ 死点击 / 首点竞态
                    # 失败」：真面板从未打开，乐观判据会吞掉补点并把失败说成成功
                    # （§1 CASE-X/CASE-Y 真机复现）。点击没让面板变化时，照旧等满窗口
                    # 返回 False，让 BID067 的有界补点与诚实披露生效。
                    return True  # BID098
                if overlay_sticky and not panels_before:  # BID091
                    # 页面本来就有可见浮层、差分又探测不到：既没有「新出现」的证据，
                    # 也无法证明「没打开」。保守按已打开处理，避免补点误关（BID072）。
                    return True  # BID091
                return self._page_hidden(session)  # BID072
            time.sleep(0.05)  # BID067

    # 本函数于BID099引入
    def _panel_closed_by_click(
        self, executor, backend_node_id: int, before: dict, window: float = 0.15
    ) -> bool:
        """点击后立即（有界）确认**这一击**是否真的把面板关掉了。

        BID098 的超时乐观判据「``near`` 没变少且大于 0 ⇒ 面板在」无法区分两件事：

        * 点击确实把面板关掉、组件随后自己挂回（观察窗口开始时已恢复）；
        * 点击根本没让面板变化——死点击 / 首点竞态失败，而页面上常驻一个「面板
          形态」盒子（BID091 的几何假阳性：``anchored`` 恒真、``near`` 恒为 1）。

        两者在单帧状态上完全等价，只有「点击后**期间**观察到变少」能证明这一击
        作用于那个面板（BID099 §1 CASE-X/CASE-Y 真机复现）。``_settle_navigation``
        会等 DOM 静默，而这个「收起 → 重新取数 → 挂回」可能整个落在它里面；所以
        必须在 ``executor.click`` 之后、settle 之前做一次很短的轮询，把这份方向性
        证据记下来，交给 ``_wait_popup_opened`` 的乐观判据（``saw_click_close``）。

        ``before`` 没有候选面板时直接返回 ``False``：常规「点击打开面板」不付这份
        延迟；只有「点击前控件旁已贴着候选面板」（toggle 关闭 / 常驻假面板）
        才需要区分。
        """
        try:  # BID099
            if not before.get("anchored") and int(before.get("near", -1)) <= 0:  # BID099
                return False  # BID099
        except (TypeError, ValueError):  # BID099
            return False  # BID099
        deadline = time.time() + window  # BID099
        while True:  # BID099
            current = executor.panel_state(backend_node_id)  # BID099
            if self._panel_closed_since(before, current):  # BID099
                return True  # BID099
            if time.time() >= deadline:  # BID099
                return False  # BID099
            time.sleep(0.04)  # BID099

    # 本函数于BID072引入
    def _page_hidden(self, session: str) -> bool:  # BID072
        """当前标签页是否不可见（``document.hidden``）。

        不可见时浏览器冻结 ``requestAnimationFrame``，页面自身的入场动画/CSS 过渡不会
        推进；依赖「渲染可见」的探测（浮层盒子、几何尺寸）全部失真。调用方据此切换到
        「不做破坏性重试 / 放宽浮层成员判据」的兜底路径（BID072）。"""
        assert self.client is not None  # BID072
        try:  # BID072
            result = self.client.send(  # BID072
                "Runtime.evaluate",  # BID072
                {  # BID072
                    "expression": "document.hidden === true",  # BID072
                    "returnByValue": True,  # BID072
                },  # BID072
                session_id=session,  # BID072
                timeout=timing.get().cdp.probe_timeout,  # BID072
            )  # BID072
            return bool((result.get("result") or {}).get("value"))  # BID072
        except Exception:  # BID072
            return False  # BID072

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

    @_finalize_llm_output  # BID084
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

    @_finalize_llm_output  # BID084
    def list_tabs(self) -> dict:
        _bid065_t0 = bid065_trace.perf()  # BID065
        bid065_trace.mark("controller.list_tabs START")  # BID065
        try:
            self.ensure_connected()
            bid065_trace.mark_elapsed("controller.list_tabs ensure_connected", _bid065_t0)  # BID065
            result = {"tabs": self.tab_labels()}
        except (BrowserLaunchError, CDPError) as exc:
            result = {"tabs": [], "error": str(exc)}
        bid065_trace.mark_elapsed("controller.list_tabs TOTAL", _bid065_t0)  # BID065
        return result

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

    @_finalize_llm_output  # BID084
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

    @_finalize_llm_output  # BID084
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

    @_finalize_llm_output  # BID084
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

    @_finalize_llm_output  # BID084
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
        self._wait_targets_closed([target_id])  # BID086
        self._forget_target(target_id)  # BID086: 含从标签页 LRU 表里摘掉
        return {"tabs": self.tab_labels()}

    @_trim_tabs_after_reply  # BID086
    @_finalize_llm_output  # BID084
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
        self._mark_studio_target(target_id)
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

    def open_url(self, url: str) -> dict:
        """Open a URL in a new tab of the tool-controlled browser, without
        capturing/serializing the page (cheap, for UI-driven navigation).

        Used when the user clicks a link inside the app: the page must open in
        the browser this tool drives (whose CDP endpoint is known), not in the
        app's own WebView — the app has no browser chrome (back button etc.), so
        rendering a site there would trap the user. Returns ``{"ok", "error"}``.
        """
        try:
            self.ensure_connected()
        except (BrowserLaunchError, CDPError) as exc:
            return {"ok": False, "error": str(exc)}
        assert self.client is not None
        target_url = (url or "").strip()
        if not target_url:
            return {"ok": False, "error": rp.EMPTY_URL}
        if not target_url.startswith(
            (
                "http://", "https://", "file://", "about:", "data:",
                "chrome://", "view-source:", "mailto:", "tel:",
            )
        ):
            target_url = "https://" + target_url
        try:
            result = self.client.send(
                "Target.createTarget", {"url": target_url, "background": False}
            )
            target_id = result["targetId"]
        except CDPError as exc:
            return {"ok": False, "error": str(exc)}
        self.focused_target_id = target_id
        self._mark_studio_target(target_id)
        try:
            self._activate_target(target_id)
        except (CDPError, RuntimeError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "error": ""}
