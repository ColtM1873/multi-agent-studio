"""Serialize an enhanced DOM tree into a hierarchy-preserving indented tree.

The output is intentionally structured: every meaningful group gets a bracketed
label header (``[主体内容]`` / ``[2级标题]`` / ``[文本]`` / ``[图片]`` …), children
are indented one level deeper, and top-level blocks are separated by a blank line.
This exposes the DOM's own tree structure instead of flattening it.

Interactive nodes are wrapped in semantic tags, e.g.::

    姓名：<可输入元素 e4></可输入元素 e4>

Images become a ``[图片]`` group (alt text only, token-cheap, non-multimodal
friendly). Scrollable containers are rendered as clipped groups so that
scrolling produces a meaningful diff (only the newly revealed children appear).

``serialize()`` returns the full text; ``serialize_lines()`` returns the
structured ``OutLine`` list used by the diff engine (it carries each line's
ancestor header path and the interactive names appearing on the line).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Optional

from .build import (
    PAGE_SCROLL_TAG,
    EnhancedNode,
    EnhancedTree,
    in_collapsed_overlay_portal,
)
from .classify import (
    CLICKABLE_INPUT_TYPES,
    CLICKABLE_ROLES,
    classify,
    has_inline_click_handler,
    has_svg_descendant,
    is_clickable,
    is_control_icon,
    is_cursor_pointer_only,
    is_custom_select_input,
    is_date_picker_input,
    is_picker_shell_trigger,
    is_range_picker_shell,
    is_searchable_typeahead,
    is_widget_trigger_input,
    picker_range_position,
    _is_generic_picker_shell,
    _is_named_picker_shell,
    _marks_select_control,
)
from .registry import NameRegistry

SKIP_TAGS = {
    "script",
    "style",
    "svg",
    "head",
    "meta",
    "link",
    "title",
    "noscript",
    "template",
    "path",
    "br",
    "wbr",
}

BLOCK_TAGS = {
    "p",
    "li",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "tr",
    "hr",
    "section",
    "article",
    "header",
    "footer",
    "nav",
    "aside",
    "main",
    "form",
    "ul",
    "ol",
    "table",
    "thead",
    "tbody",
    "tfoot",
    "dialog",
    "blockquote",
    "pre",
    "figure",
    "figcaption",
    "dt",
    "dd",
}

TAG_LANDMARKS = {
    "nav": "navigation",
    "main": "main",
    "aside": "complementary",
    "header": "banner",
    "footer": "contentinfo",
    "form": "form",
}

ROLE_LANDMARKS = {
    "banner",
    "navigation",
    "main",
    "complementary",
    "contentinfo",
    "search",
    "form",
    "region",
    "dialog",
    "article",
}

# Chinese labels keep the output consistent with the existing interactive tags
# (``<可点击元素 eN>``). The exact wording matters less than the visible level.
LANDMARK_LABELS = {
    "banner": "页眉",
    "navigation": "导航",
    "main": "主体内容",
    "complementary": "侧栏",
    "contentinfo": "页脚",
    "search": "搜索",
    "form": "表单",
    "region": "区域",
    "dialog": "对话框",
    "article": "文章",
}

HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}

# Tags that are structural list/table cells: they render as plain content lines
# (not as their own ``[文本]`` groups) to keep lists/tables compact.
TEXT_BLOCK_EXCLUDE = {"li", "tr", "td", "th", "dt", "dd", "option", "label", "summary"}

_BLOCK_DISPLAY_PREFIXES = (
    "block",
    "flex",
    "grid",
    "list-item",
    "table",
    "flow-root",
)

CATEGORY_TAGS = {
    "click": ("可点击元素", "点击"),
    "clickdropdown": ("可点击下拉元素", "点击"),
    "input": ("可输入元素", "输入"),
    "searchable": ("可搜索下拉元素", "筛选"),
    "select": ("可选择元素", "选择"),
    "drag": ("可拖动元素", "拖动"),
    "scroll": ("可滚动元素", "滚动"),
}

# Marks a one-element range control (a div-based 「起止时间」 whose two ends live
# inside the same shell). The two-input form uses ``[区间 i/n]`` per entry instead
# (see ``_picker_entry_label``); both tell the model "two picks, one overlay".
RANGE_SHELL_MARK = "[区间控件]"


# Class / id tokens that mark a *helper / error* text (not the control's value or
# label). A custom select renders its validation message as a sibling of its
# filter input (Moka ``.sd-Input-message`` → “必填项未填写”); treating it as the
# current value produced ``是否校园大使推荐：必填项未填写`` and consumed the marker.
_HELPER_TEXT_TOKENS = (
    "message",
    "error",
    "validate",
    "validation",
    "help",
    "hint",
    "describe",
    "feedback",
    "warning",
    "required",
    "asterisk",
    "tooltip",
)


def _is_click_like(category: str) -> bool:
    """True for ``click`` and its dropdown sub-family ``clickdropdown``.

    A ``clickdropdown`` behaves exactly like a ``click`` (the tool's action is a
    click that opens the candidate list); only its tag / tool differ. Share every
    behavioural branch between the two so the new category never regresses the
    click handling.
    """
    return category in ("click", "clickdropdown")


def _has_word_char(text: str) -> bool:
    """True if ``text`` holds at least one letter / digit / CJK character.

    Used to reject a range control's bare separator (``-`` / ``~``) as if it were
    a field's current value.
    """
    return any(ch.isalnum() for ch in (text or ""))

MAX_TEXT_LENGTH = 4000

# A scroll container holding at least this many option-like text leaves is treated
# as a *candidate column* of an open picker panel and rendered compactly (head
# sample + total count + selected value) instead of option by option. Kept well
# above the option count of normal selects / menus (5~30), so ordinary dropdowns
# keep listing their options.
_OPTION_COLUMN_MIN = 40
_OPTION_COLUMN_SAMPLE = 6

# Returned by ``_overlay_centerpiece_span`` for content that is a *duplicate* of an
# overlay centrepiece's describing line (its own ``2018 - 2026`` header title), so
# the renderer drops it instead of emitting the same fact twice.
_SUPPRESS_CENTERPIECE = "\x00suppress"

# A text run that is nothing but a year/month span ("2018 - 2026", "2026年").
_SPAN_ONLY_TEXT_RE = re.compile(
    r"(?:\d{4}|\d{1,2})\s*\u5e74?\s*[-\u2013\u2014~\uff5e]\s*(?:\d{4}|\d{1,2})\s*\u5e74?"
)

# Computed ``white-space`` values where a literal ``\n`` in the text really is a
# line break in the rendered page. For every other value (``normal``/``nowrap``)
# newlines collapse to a space, exactly like a browser does.
_PRESERVE_NEWLINE_WHITE_SPACE = {"pre", "pre-wrap", "pre-line", "break-spaces"}

_TRUNCATION_NOTE = (
    f"…（本行原文超过 {MAX_TEXT_LENGTH} 字符，已按上限截断，后面还有内容）"
)

# Lowercase keywords that make a ``class`` token look like a human-authored
# control name (``phoenix-calendar-prev-year-btn``) rather than an obfuscated
# style hash (``sc-gIDmLj``). Only used as a last-resort label for interactive
# elements that expose no accessible name / text / title (see ``_fallback_label``).
_SEMANTIC_TOKEN_HINTS = (
    "btn", "button", "icon", "close", "prev", "previous", "next", "back",
    "forward", "add", "delete", "remove", "search", "upload", "download",
    "edit", "menu", "tab", "arrow", "expand", "collapse", "play", "pause",
    "refresh", "reload", "home", "link", "select", "checkbox", "radio",
    "toggle", "switch", "plus", "minus", "left", "right", "up", "down",
    "first", "last", "year", "month", "day", "date", "ok", "confirm",
    "cancel", "submit", "reset", "clear", "filter", "sort", "share", "copy",
    "save",
)

# Class-token prefixes emitted by framework/runtime/CSS-in-JS tooling. These are
# never authored names: Angular state classes (``ng-untouched``/``ng-pristine``),
# Vue scoped ids (``v-…``), Svelte/JSX hashes (``svelte-…``/``jsx-…``) and
# generated style classes (``css-…``/``sc-…``). Treating them as semantic tokens
# used to leak garbage labels such as ``ng-untouched`` onto empty form fields.
_FRAMEWORK_CLASS_PREFIXES = (
    "ng-",
    "ngcontent",
    "nghost",
    "_ngcontent",
    "_nghost",
    "v-",
    "vue-",
    "svelte-",
    "ember-",
    "jsx-",
    "css-",
    "sc-",
    # Component-library / utility-CSS namespaces (Element UI ``el-checkbox``,
    # Tailwind ``tw-cursor-pointer``, Ant Design ``ant-…`` …). These are emitted
    # by tooling and never name a control; left alone they leaked as labels.
    "el-",
    "tw-",
    "ant-",
    "antd-",
    "arco-",
    "van-",
    "nut-",
    "chakra-",
)

# Action words inside a hyphenated class token. When present, the token is
# trimmed from the first action word onward (``phoenix-calendar-prev-year-btn``
# → ``prev-year-btn``), which is the part that actually names the control.
_ACTION_HINTS = {
    "prev", "previous", "next", "back", "forward", "close", "open", "add",
    "new", "create", "delete", "remove", "edit", "save", "submit", "cancel",
    "search", "upload", "download", "refresh", "reload", "play", "pause",
    "expand", "collapse", "toggle", "clear", "filter", "sort", "share",
    "copy", "home", "menu", "more", "help", "info", "settings", "select",
}

# Qualifiers that modify an action word and must be preserved when trimming a
# class token from its first action word (``super-prev-btn`` ≠ ``prev-btn``).
_QUALIFIER_HINTS = {
    "super", "double", "half", "first", "last", "sub", "mini", "multi", "step",
}

# Structural / container words name *where* something sits, not *what it does*.
# A class token composed only of these (``header-logo-link`` / ``footer-wrapper``)
# is machine markup: leaking it as a label ("header-logo-link") tells the LLM
# nothing and reads like a bug. Such tokens are rejected so the caller can fall
# back to a semantic generic word; a token that also carries an action word
# (``header-search-submit-icon``) is kept and trimmed to that action.
_GENERIC_STRUCTURE_WORDS = {
    "header", "footer", "nav", "navbar", "navigation", "logo", "brand", "banner",
    "wrapper", "wrap", "container", "layout", "section", "sect", "block", "box",
    "area", "panel", "pane", "mask", "overlay", "popup", "modal", "dialog",
    "page", "site", "web", "main", "content", "inner", "outer", "left", "right",
    "top", "bottom", "middle", "center", "mid", "item", "list", "row", "col",
    "cell", "grid", "title", "text", "txt", "label", "img", "image", "pic",
    "photo", "avatar", "thumb", "icon", "iconfont", "font", "link", "btn",
    "button", "input", "form", "field", "group", "menu", "bar", "tool", "tools",
    "ctrl", "control", "widget", "component",
    # A native ``<select class="select1">`` used to leak its own class as a label
    # (``<可点击元素 e42>select1</可点击元素 e42>``); ``select`` is a structural
    # word, never a human name.
    "select", "selection", "dropdown", "combobox", "listbox",
}

# ARIA roles that identify a *floating overlay* (a dropdown / menu / popup whose
# entries are separate, individually-callable controls).
_OVERLAY_ROLES = {
    "listbox", "menu", "menubar", "menuitem", "tree", "tablist", "grid",
    "radiogroup", "dialog", "tooltip", "popover",
}
# A floating overlay's entries (each is one selectable item, not a wrapper).
_OPTION_ROLES = {
    "option", "menuitem", "menuitemcheckbox", "menuitemradio", "treeitem",
}
# Class-name fragments that mark a floating overlay container. Used together
# with an "option" class token (or an overlay role) so a plain nav ``menu-item``
# elsewhere is not mistaken for a select's option list.
_OVERLAY_CLASS_HINTS = (
    "dropdown", "popup", "popper", "popover", "overlay",
    "listbox", "select-menu", "autocomplete", "suggestion", "option-list",
)
# Utility-CSS property prefixes (Tailwind & friends). A class token such as
# ``px-2`` / ``mt-2`` / ``w-[146px]`` is pure styling and must never become a
# control label — an icon-only delete button used to surface as
# ``<可点击元素 eN>px-2</可点击元素 eN>``.
_UTILITY_CLASS_PREFIXES = {
    "p", "px", "py", "pt", "pr", "pb", "pl",
    "m", "mx", "my", "mt", "mr", "mb", "ml", "space",
    "w", "min", "max", "h", "size",
    "text", "font", "leading", "tracking", "indent", "align", "whitespace", "break",
    "bg", "border", "divide", "ring", "rounded", "outline", "shadow",
    "flex", "grid", "gap", "basis", "grow", "shrink", "order", "col", "row",
    "items", "justify", "content", "self", "place",
    "z", "opacity", "overflow", "object", "inset", "top", "right", "bottom", "left",
    "static", "fixed", "absolute", "relative", "sticky",
    "block", "inline", "hidden", "visible", "table",
    "transition", "duration", "ease", "delay", "animate", "transform", "scale",
    "rotate", "translate", "skew", "origin", "fill", "stroke", "cursor", "select",
    "pointer-events", "resize", "list", "decoration", "filter", "backdrop", "blur",
    "brightness", "contrast", "saturate", "grayscale", "sepia",
}


def _is_likely_utility_class(token: str) -> bool:
    """True for a Tailwind-style utility class (``px-2`` / ``w-[146px]``).

    Only the shape ``<property>-<numeric|bracketed value>`` is matched, so real
    words that merely start with a shared prefix (PrimeNG's ``p-button``) are
    preserved; arbitrary colour values (``bg-white``) are intentionally left
    alone — the observed leaks (``px-2`` / ``mt-2`` / ``w-[146px]``) are numeric.
    """
    body = token[1:] if token.startswith("-") else token
    parts = body.split("-", 1)
    if len(parts) != 2:
        return False
    if parts[0].lower() not in _UTILITY_CLASS_PREFIXES:
        return False
    tail = parts[1]
    return bool(tail) and (tail[0].isdigit() or tail[0] in "[(")


# Common icon ``aria-label`` / ``alt`` names (Ant Design ``anticon``, Element UI,
# Material icons …) mapped to a short, actionable Chinese label. Component
# libraries label their icon-only controls with the *icon* name (`calendar`,
# `close-circle`), which tells the LLM nothing about what the control does.
_ICON_LABELS = {
    "calendar": "打开日历",
    "close-circle": "清除",
    "close": "关闭",
    "x": "关闭",
    "×": "关闭",
    "✕": "关闭",
    "✖": "关闭",
    "logo": "网站标志",
    "down": "展开",
    "up": "收起",
    "left": "向左",
    "right": "向右",
    "double-left": "最前",
    "double-right": "最后",
    "backward": "上一步",
    "forward": "下一步",
    "upload": "上传",
    "download": "下载",
    "search": "搜索",
    "delete": "删除",
    "plus": "新增",
    "minus": "移除",
    "check": "确认",
    "eye": "查看",
    "edit": "编辑",
    "reload": "刷新",
    "sync": "刷新",
    "ellipsis": "更多",
    "more": "更多",
    "menu": "菜单",
    "filter": "筛选",
    "sort": "排序",
    # Directional carets. A tree/list expander is often the *only* accessible
    # name its host exposes, and Feishu's ATSX localizes Ant Design's icon
    # aria-label to the literal string ``图标: caret-down`` — which then got
    # concatenated into the sibling option's AX name
    # (``图标: caret-down 中国大陆``). ``_clean_icon_accessible_name`` strips such
    # prefixes; these entries are the fallback when the icon is the whole name.
    "caret-down": "展开",
    "caret-up": "收起",
    "caret-right": "展开",
    "caret-left": "收起",
}

# A component library may bake an icon name into an accessible name as
# ``图标: caret-down`` (localized Ant Design ``anticon``). Concatenated with a
# sibling's text it produced labels like ``图标: caret-down 中国大陆`` — a
# criterion-ii mismatch (the entry is the option, not an expander). Match the
# prefix and drop it, keeping the human remainder; if the whole name *was* the
# icon, map it through ``_ICON_LABELS``.
_ICON_LABEL_PREFIX_RE = re.compile(
    r"(?:图标|icon)\s*[:：]\s*([A-Za-z0-9_\-]+)", re.IGNORECASE
)


# Placeholder labels that mean "the serializer could not find a human name".
# When a clickable ends up with one of these *and* carries no text / aria /
# title / href anywhere, it is criterion-i noise (an empty calendar cell, a
# tree row with a collapsed child, a decorative loading spinner) rather than a
# control the LLM can usefully call. Emitting it produced
# ``<可点击元素 eN>可点击项</可点击元素 eN>`` and ``<可点击元素 eN>animation</>``
# and pushed the model into repeated dead clicks.
_NON_ACTIONABLE_LABELS = {
    "可点击项",
    "可交互元素",
    "animation",
    "loading",
    "loader",
    "spinner",
    "动画",
    "加载中",
}


def _clean_icon_accessible_name(name: str) -> str:
    """Remove ``图标: <icon>`` fragments from an accessible name.

    Keeps any human text around them; when the name is *only* an icon token,
    returns its mapped Chinese label (or ``""`` so the caller falls back).
    """
    text = (name or "").strip()
    if not text or "图标" not in text and "icon" not in text.lower():
        return text
    matches = list(_ICON_LABEL_PREFIX_RE.finditer(text))
    if not matches:
        return text
    remainder = _ICON_LABEL_PREFIX_RE.sub("", text)
    remainder = " ".join(remainder.split())
    if remainder:
        return remainder
    return _ICON_LABELS.get(matches[0].group(1).lower(), "")


# Icon fonts put their glyphs in the Unicode Private Use Areas (``\ue76e`` /
# ``\ue71f`` / ``\ue72a`` …). They carry no readable text, so a control whose
# only "content" is such a glyph was rendered as a content-free label
# (``<可点击元素 e11>\ue76e</可点击元素 e11>``) and read as criterion-i noise.
# Zero-width and bidi control marks are likewise invisible. Strip both wherever
# element text becomes a label so every clickable shows something actionable.
_PRIVATE_USE_RE = re.compile("[\ue000-\uf8ff\U000F0000-\U0010FFFD]")
_INVISIBLE_MARK_RE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")


def _strip_icon_glyphs(text: str) -> str:
    """Remove private-use icon glyphs and invisible format marks from ``text``."""
    if not text:
        return text
    cleaned = _PRIVATE_USE_RE.sub("", text)
    return _INVISIBLE_MARK_RE.sub("", cleaned)

# Icon-only navigation controls (a ``‹``/``›`` chevron with no text, no
# ``aria-label``) previously leaked their raw CSS class fragment as the label:
# a real Ant Design range picker exposed ``<可点击元素 eN>prev-btn</可点击元素 eN>``
# / ``super-prev-btn`` — criterion-i "clickable with no (human) content". The
# class token itself carries the action, so translate it. Inside a date/calendar
# widget ``prev``/``next`` step a month and ``super-prev``/``super-next`` a year;
# elsewhere they are plain previous/next page controls.
_CALENDAR_NAV_LABELS = {
    "prev": "上一月",
    "previous": "上一月",
    "next": "下一月",
    "super-prev": "上一年",
    "super-next": "下一年",
}
_GENERAL_NAV_LABELS = {
    "prev": "上一页",
    "previous": "上一页",
    "next": "下一页",
}
# Class/id fragments that identify a date/calendar widget (Ant Design ``picker``,
# Element UI ``date-picker``, generic ``calendar``).
_CALENDAR_CONTEXT_HINTS = ("picker", "calendar", "datepicker", "date-picker")


# CamelCase / PascalCase control classes are common in hand-written markup
# (``delIcon``, ``closeBtn``). ``_is_semantic_token`` rejects them (it only
# accepts lowercase hyphenated words), so an icon-only control built that way was
# left with the content-less generic label ``可点击项`` (user criterion i: a
# clickable element with no content). This small allow-list maps the *action*
# word of such a class to a short Chinese label. It is intentionally narrow —
# only unambiguous action words, no directional words (``down``/``left``) that
# would misfire on unrelated class names.
_CAMEL_ACTION_LABELS = {
    "del": "删除",
    "delete": "删除",
    "remove": "移除",
    "close": "关闭",
    "add": "新增",
    "edit": "编辑",
    "search": "搜索",
    "upload": "上传",
    "download": "下载",
    "refresh": "刷新",
    "reload": "刷新",
    "save": "保存",
    "submit": "提交",
    "clear": "清除",
    "filter": "筛选",
    "sort": "排序",
    "more": "更多",
    "menu": "菜单",
    "settings": "设置",
    "help": "帮助",
    "info": "信息",
    "expand": "展开",
    "collapse": "收起",
    "play": "播放",
    "pause": "暂停",
}

BBox = tuple[float, float, float, float]

_SCROLL_OPEN = "<可滚动元素 "
_SCROLL_CLOSE = "</可滚动元素 "

# A real tag (``<p …>`` / ``</p>``), not a comparison like ``1 < 2``. Used to
# strip markup that page authors accidentally pasted into an image ``alt`` /
# ``title`` before it is shown to the LLM.
_HTML_TAG_RE = re.compile(r"<[A-Za-z/!][^>]*>")


def _clean_alt_text(raw: str) -> str:
    """Turn an ``alt``/``title`` value into plain, readable text.

    Some sites embed a whole HTML fragment (already entity-encoded, sometimes
    double-encoded) in an image's ``alt``: the home page rendered six identical
    ``[图片]`` groups whose "alt" was ``&amp;lt;p style=…&amp;gt;简历投递…`` — pure
    noise that buried the real step names. Unescape (tolerating double-encoding),
    drop any markup, and collapse whitespace.
    """
    if not raw:
        return ""
    text = raw
    for _ in range(3):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    if "<" in text:
        text = _HTML_TAG_RE.sub(" ", text)
    return " ".join(text.split())


def _is_scroll_open(text: str) -> bool:
    return text.lstrip().startswith(_SCROLL_OPEN)


def _is_scroll_boundary(text: str) -> bool:
    # A closing line, or the single-line ``#page`` tag (which both opens and
    # closes on the same line).
    stripped = text.lstrip()
    return stripped.startswith(_SCROLL_CLOSE) or (
        stripped.startswith(_SCROLL_OPEN) and _SCROLL_CLOSE in stripped
    )


def separate_scroll_blocks(lines: list[str]) -> list[str]:
    """Put a blank line before/after every scrollable-element block.

    Scroll blocks can appear at any depth, so the top-level blank-line rule in
    ``serialize`` is not enough. This gives every ``<可滚动元素 eN>`` block a
    clear boundary in both the full serialization and the incremental diff.
    """
    out: list[str] = []
    for line in lines:
        if _is_scroll_open(line) and out and out[-1] != "":
            out.append("")
        out.append(line)
        if _is_scroll_boundary(line):
            out.append("")
    cleaned: list[str] = []
    for line in out:
        if line == "" and cleaned and cleaned[-1] == "":
            continue
        cleaned.append(line)
    while cleaned and cleaned[0] == "":
        cleaned.pop(0)
    while cleaned and cleaned[-1] == "":
        cleaned.pop()
    return cleaned


@dataclass(frozen=True)
class OutLine:
    """One rendered line, with the context the diff engine needs."""

    depth: int
    text: str
    kind: str  # "header" | "content" | "close"
    ancestors: tuple[tuple[int, str, str], ...]  # (depth, opening, closing)
    interactive: tuple[str, ...]  # interactive names appearing on this line
    closing: str = ""  # closing tag emitted when this header's group ends


class DOMSerializer:
    def __init__(self, registry: NameRegistry, max_chars: int = 40000) -> None:
        self.registry = registry
        self.max_chars = max_chars
        self._lines: list[OutLine] = []
        self._stack: list[tuple[int, str, str]] = []
        self._buffer = ""
        self._buffer_names: list[str] = []
        self._buffer_labels: list[str] = []
        # Nodes whose text was already folded into an interactive control's label
        # (the placeholder of a searchable select): skip them so the text is not
        # emitted a second time as a bare line.
        self._consumed: set[int] = set()
        # Whether the scroll container that currently owns the clip is *not* a
        # containing block for absolutely-positioned descendants (i.e. it is
        # ``position: static``). When true, an ``absolute`` overlay descendant may
        # visually escape the scroller and must not be clipped away. Managed as a
        # save/restore around ``_group_scroll`` so nested scrollers compose.
        self._clip_escapes = False
        # Per-call caches (see ``_enclosing_section_title`` / ``_scroller_summary``).
        self._section_titles: dict[int, str] = {}
        self._scroll_reports: list[tuple] = []
        self._tree: Optional[EnhancedTree] = None

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #
    def serialize(self, tree: EnhancedTree) -> str:
        lines = self.serialize_lines(tree)
        parts = [f"[document] url={tree.url or 'about:blank'} title={tree.title or ''}".rstrip()]
        first_block = True
        for line in lines:
            if line.depth == 0 and line.kind == "header":
                if not first_block:
                    parts.append("")
                first_block = False
            parts.append(("\t" * line.depth + line.text) if line.text else "")
        parts.append("")
        parts.append(self._page_info(tree))
        text = "\n".join(separate_scroll_blocks(parts))
        if len(text) > self.max_chars:
            text = text[: self.max_chars] + "\n…（内容已截断，可用 tool_2 获取全量）"
        return text

    def serialize_lines(self, tree: EnhancedTree) -> list[OutLine]:
        self._lines = []
        self._stack = []
        self._buffer = ""
        self._buffer_names = []
        self._buffer_labels = []
        self._consumed = set()
        self._clip_escapes = False
        self._section_titles = {}
        self._scroll_reports = []
        self._tree = tree
        self._render_children(tree.root, 0, None)
        self._flush(0)
        return list(self._lines)

    # ------------------------------------------------------------------ #
    # rendering
    # ------------------------------------------------------------------ #
    def _render_children(
        self, node: EnhancedNode, depth: int, clip: Optional[BBox]
    ) -> None:
        for child in node.children:
            self._render_child(child, depth, clip)
        self._flush(depth)

    def _render_child(
        self, child: EnhancedNode, depth: int, clip: Optional[BBox]
    ) -> None:
        if child.is_text:
            self._append_text(child, depth)
            return

        if not child.is_element:
            # fragment / document containers: recurse transparently
            for grand in child.children:
                self._render_child(grand, depth, clip)
            return

        if in_collapsed_overlay_portal(child):
            # A closed dropdown / picker / popover panel that is still mounted
            # inside a **collapsed portal** looks visible by its own styles (its
            # box is real; only the zero-height portal ancestor clips it away).
            # Serializing it flooded the LLM with hundreds of off-screen options
            # (137 year candidates of a *closed* 起止时间 picker on the Feishu Jobs
            # apply form) and, when the panel's cells have no pointer cursor, its
            # text leaked as bare ``[文本]`` rows through the zero-area rescue.
            # See ``build.in_collapsed_overlay_portal``.
            return

        if child.is_element and self._overlay_centerpiece_span(child) == _SUPPRESS_CENTERPIECE:
            # The overlay centrepiece's own header title ("2018 - 2026"): the
            # describing line emitted for the centrepiece carries the same span, so
            # this duplicate is dropped wherever it is reached from (the title is
            # not always inside the centrepiece's scroll container).
            return

        # A ``<br>`` is a rendered line break; it is in ``SKIP_TAGS`` (it has no
        # box of its own), so handle it here before the skip check.
        if child.tag == "br":
            if self._buffer.strip():
                self._flush(depth)
            elif self._lines and self._lines[-1].text and self._lines[-1].depth == depth:
                # Two consecutive breaks (``<br><br>``) are a blank line.
                self._emit_blank(depth)
            return

        if child.tag in SKIP_TAGS or child.hidden:
            return
        if child.tag == "label" and (
            self._is_redundant_option_label(child)
            or self._is_redundant_choice_label(child)
        ):
            # The text half of a native radio/checkbox option, already carried
            # by the named ``<input>``; emitting it again produced a duplicated
            # bare text line (``<可点击元素 eN>国内本硕 [已选]</可点击元素 eN>``
            # followed by ``国内本硕``).
            return
        if self._is_widget_mirror(child):
            # A component library's text-size *mirror* helper
            # (``.atsx-select-search__field__mirror`` / AntD's
            # ``.ant-select-selection-search-mirror``) sits beside a searchable
            # select's typeahead and repeats its value verbatim so the input can
            # grow with it. It is invisible layout scaffolding, but on the Feishu
            # Jobs form it rendered as a second bare text line right under the
            # control (``<可输入元素 eN>上海外国语大学</可输入元素 eN>`` then
            # ``上海外国语大学``), which the LLM read as a separate suggestion.
            return
        if child.is_element and child.node_id in self._consumed:
            # Text already folded into a control's label (a searchable select's
            # placeholder); emit it only once.
            return
        if clip is not None:
            # An absolutely / fixed positioned overlay can escape its scroll
            # container's overflow clip and still be visible on screen (a
            # portalled dropdown / menu / tooltip whose host sits inside the
            # scroller). Clipping it away made a click on a select return
            # "（页面无变化）" while the option list was right there, sending the
            # LLM into a retry loop. Stop applying the scroller's clip once we
            # cross into such an overlay: its own ``visible`` / ``in_viewport``
            # box still filters it. ``fixed`` is never clipped by an ancestor's
            # overflow; ``absolute`` only escapes when the scroller is
            # ``position: static`` (``_clip_escapes``).
            position = child.styles.get("position")
            if position == "fixed" or (position == "absolute" and self._clip_escapes):
                clip = None
        if not self._in_clip(child, clip):
            return

        if not child.visible or not child.in_viewport:
            # A searchable select's inline filter input is a real field the model
            # must be able to type into, even when it has no width yet (the
            # component hides it until focus/typing; see ``_is_separate_control``).
            # Let it fall through and be rendered as a normal ``<可输入元素>``.
            search_box = (
                child.tag == "input"
                and child.in_viewport
                and child.rendered
                and not child.hidden
                and self._is_searchable_typeahead(child)
            )
            if not search_box:
                # The node itself has no visible box (typically zero-area), or
                # its own box is outside the viewport, but its element
                # descendants may still be laid out and visible. Two classic
                # cases:
                #   * portal/dropdown: `<div style="position:absolute;width:100%">`
                #     wraps an absolutely positioned popup, so the wrapper collapses
                #     to height 0 while the popup is clearly visible;
                #   * deep scroll: the root/ICB or a fixed shell reports a
                #     viewport-sized box (`y=0`) that no longer intersects
                #     `scroll_y ± margin`, yet its descendants use page coordinates
                #     and are on screen.
                # Recurse transparently instead of pruning the whole subtree; the
                # node's own (invisible / off-screen) text is skipped, and each
                # descendant is still filtered by its own visibility/viewport box.
                #
                # Exception — a *transparent* (``opacity: 0``) subtree that is a
                # floating overlay caught mid-enter-animation. Component libraries
                # mount a dropdown / menu / tooltip, then start its open animation
                # on the next frame, so a capture taken during that frame sees a
                # rendered-but-unpositioned subtree (Ant Design parks a not-yet-
                # positioned popup at ``left/top: -9999px``). Pure visibility /
                # viewport pruning dropped it entirely and the model saw
                # “（页面无变化）” after clicking a select even though the option
                # list was right there. Fall through and serialize it as if visible
                # so its controls get named (and stay clickable). Only a node that
                # *is* a popup entry, or that sits inside a floating overlay and
                # holds popup entries, qualifies: this keeps hover-only
                # ``opacity: 0`` affordances (an input's increase/decrease arrows)
                # and permanent transparent wrappers out of the output. A genuine
                # collapsed element keeps ``opacity: 1`` and still recurses
                # box-only in the branch above.
                opacity_zero = self._effective_opacity_zero(child)
                popup = self._is_popup_option(child) or (
                    self._inside_overlay(child)
                    and self._has_popup_option_descendant(child)
                )
                zero_size_rescue = (
                    not child.visible
                    and self._has_rescuable_interactive_descendant(child)
                )
                # A *zero-area* text run inside an overlay that is genuinely open
                # is panel context, not decoration: a month-range picker's
                # "<div>2026 年</div>" header collapses to 0x0 for a frame while
                # the popup animates/positions, and the cells around it were kept
                # (``_table_cell_renderable``) while the year label was dropped —
                # leaving two identical month grids with no year, so the model
                # could not tell which panel it was clicking and never escaped the
                # loop. Rescue the text; the overlay must itself be painted, which
                # keeps a permanently collapsed block's text out of the output.
                overlay_text_rescue = (
                    not child.visible
                    and child.in_viewport
                    and self._has_direct_text(child)
                    and self._inside_open_overlay(child)
                )
                if not (
                    (opacity_zero and (popup or zero_size_rescue)) or overlay_text_rescue
                ):
                    for grand in child.children:
                        if not grand.is_text:
                            self._render_child(grand, depth, clip)
                    return

        level = self._heading_level(child)
        if level:
            self._flush(depth)
            self._group_or_interact(child, depth, f"[{level}级标题]", clip)
            return

        if self._is_image(child):
            self._flush(depth)
            self._group_image(child, depth)
            return

        landmark = self._landmark_for(child)
        if landmark:
            self._flush(depth)
            self._group_or_interact(child, depth, f"[{landmark}]", clip)
            return

        if child.tag == "figure":
            self._flush(depth)
            self._group_or_interact(child, depth, "[图]", clip)
            return

        # An unnamed ``<section>`` is a generic block container. Only surface it
        # as a group when it actually holds blockish structure (otherwise the
        # ``[文本]`` branch below is the better, less noisy fit).
        if child.tag == "section" and self._has_blockish_descendant(child):
            self._flush(depth)
            self._group_or_interact(child, depth, "[区块]", clip)
            return

        if child.tag == "dl":
            self._flush(depth)
            self._group_or_interact(child, depth, "[定义列表]", clip)
            return

        if child.tag == "dt":
            self._flush(depth)
            self._group_or_interact(child, depth, "[术语]", clip)
            return

        if child.tag == "dd":
            self._flush(depth)
            self._group_or_interact(child, depth, "[描述]", clip)
            return

        if child.tag in ("ul", "ol") or child.role == "list":
            self._flush(depth)
            self._group_or_interact(child, depth, "[列表]", clip)
            return

        if child.tag == "table" or child.role == "table":
            self._flush(depth)
            self._group_or_interact(child, depth, "[表格]", clip)
            return

        # ``<tr>``: keep the row on a single line but separate the cells with
        # ``|`` so column boundaries survive. A fully-``<th>`` row is marked.
        if child.tag == "tr":
            self._flush(depth)
            if self._is_header_row(child):
                self._buffer = "[表头] "
            first_cell = True
            for cell in child.children:
                if cell.is_text:
                    self._append_text(cell, depth)
                    continue
                if cell.is_element and cell.tag in ("td", "th"):
                    if not self._table_cell_renderable(cell):
                        continue
                    if not first_cell:
                        self._buffer += " | "
                    first_cell = False
                self._render_child(cell, depth, clip)
            self._flush(depth)
            return

        category = classify(child)
        if category == "scroll":
            self._flush(depth)
            centerpiece = self._overlay_centerpiece_span(child)
            suppression = centerpiece == _SUPPRESS_CENTERPIECE
            option_kind = "" if centerpiece else self._option_column_kind(child)
            if suppression:
                # Duplicate fact (the panel header's own ``2018 - 2026`` title): the
                # centrepiece line already carries it, so emit nothing here.
                pass
            elif centerpiece:
                # The overlay's **non-interactive centrepiece** (a picker panel's
                # header, e.g. a year-range table reading ``2018-2026``): it is a
                # scroll container with overflowing content, but nothing inside it
                # is a control. Serializing its children produced ~20 lines of
                # ``[表格]`` / ``[文本] 2019 | 2020`` scaffolding that looked like
                # options the model could not click (2026-10-01 session: it read the
                # award picker's header years as "candidates without marks"). Say
                # what the block is instead, and keep the panel structure.
                self._group_overlay_centerpiece(child, depth, centerpiece, clip)
            elif option_kind:
                # A long *option column* of an open picker panel (year / month /
                # day lists, an Ant ``virtual-list`` of 130 years …): keep the
                # column boundary and say what it is and how many options it
                # holds, but do not dump every option — the raw list is 127 items
                # on the Feishu 起止时间 picker and would swamp the form itself.
                self._group_option_column(child, depth, option_kind, clip)
            elif (child.tag == "li" or child.role == "listitem") and self._has_blockish_descendant(child):
                # A scrollable rich list item wraps the scroll element around its
                # ``[列表项]`` boundary.
                self._group_or_interact(child, depth, "[列表项]", clip)
            else:
                self._group_scroll(child, depth, clip)
            return

        if category:
            if is_picker_shell_trigger(child):
                # A div-based picker (see ``classify.is_picker_shell_trigger``) is
                # **one** control: its two value parts and its 0×0 hidden input are
                # internals, not fields. Emit a single line whose label carries the
                # field name plus the *displayed* value, and never recurse — the
                # parts used to be emitted as a bare ``2021-06`` text line and as
                # the bogus field name of the hidden input (``2017-09：（空）``),
                # which left the model unable to tell whether the range was filled.
                self._flush(depth)
                label = self._picker_shell_label(child)
                name = self.registry.get_or_create(child)
                tag, _ = CATEGORY_TAGS[category]
                self._emit_content(depth, f"<{tag} {name}>{label}</{tag} {name}>", (name,))
                return
            if _is_click_like(category) and self._is_searchable_display_value(child):
                # The passive "display value" of a searchable select (Moka
                # ``.sd-Input-display-value``) is only clickable because it
                # inherits the control's pointer cursor; its text is folded into
                # the searchable input's label. Drop it so the value is not emitted
                # a second time as a separate ``<可点击元素>``.
                return
            if _is_click_like(category) and (
                self._has_label_element_descendant(child)
                and self._has_interactive_descendant(child)
            ):
                # A clickable *form row* must NOT be named: it wraps a field
                # ``<label>`` plus its own control(s). Naming the row swallowed
                # the editable ``<input>`` (the model saw
                # ``<可点击元素 eN>手机：姓名</可点击元素 eN>`` and no way to fill) or
                # merged the label into a composite control's text with the wrong
                # prefix. Render its children so the real field / control is
                # exposed. A native ``<label>`` that merely wraps a hidden
                # checkbox/radio is not affected: it has no *interactive*
                # descendant once the hidden input is pruned, so it keeps naming
                # the control as before.
                self._flush(depth)
                for grand in child.children:
                    self._render_child(grand, depth, clip)
            elif _is_click_like(category) and self._is_redundant_choice_label(child):
                # The text half of a native radio / checkbox option: it only looks
                # clickable because it inherits ``cursor:pointer``; the sibling
                # ``<input>`` already carries this exact label. Skip it so the
                # option is not named twice.
                return
            elif _is_click_like(category) and self._is_textual_click_only(child):
                # A ``cursor:pointer`` element with no strong control descendant,
                # no icon and only a long descriptive text run (e.g. Ant Design's
                # ``.ant-form-item-extra`` helper note) is *not* a control: the
                # page merely inherited a pointer cursor from a form row. Emit its
                # text as one plain line instead of recursing (which would expose
                # each inner ``<span>`` as a bogus clickable). Short
                # ``cursor:pointer`` labels (real text buttons) stay clickable.
                #
                # ``_is_textual_click_only`` itself refuses a node that is or holds
                # a popup option, so a dropdown wrapper is never demoted to text
                # (which would swallow its options); it falls through to the
                # prune-and-recurse branch below.
                self._flush(depth)
                text = self._collect_text(child)
                if text:
                    self._emit_content(depth, text)
            elif _is_click_like(category) and self._has_separate_interactive_descendant(child):
                # A clickable wrapper around *separate* controls (e.g. a
                # media-control bar, a clickable card holding links) is noise:
                # the model would never call the wrapper. Skip it (assign no
                # name) and render its children.
                #
                # Note: a composite form control (e.g. ``.phoenix-select``) is
                # itself the entry; its placeholder / bare typeahead input are
                # internal parts, not separate controls, so it is named instead
                # of pruned (see ``_has_separate_interactive_descendant``).
                self._flush(depth)
                for grand in child.children:
                    self._render_child(grand, depth, clip)
            else:
                label = self._interactive_label(child, category)
                if self._is_contentless_clickable(child, label):
                    # Criterion-i: a clickable whose only possible name is a
                    # generic filler word — an empty calendar/tree cell, a
                    # decorative loading spinner — is not a control. Naming it
                    # produced ``<可点击元素 eN>可点击项</>`` and sent the LLM
                    # into repeated dead clicks. Render its children instead so
                    # any real content still surfaces.
                    self._flush(depth)
                    for grand in child.children:
                        self._render_child(grand, depth, clip)
                    return
                name = self.registry.get_or_create(child)
                tag, _ = CATEGORY_TAGS[category]
                piece = f"<{tag} {name}>{label}</{tag} {name}>"
                if self._is_block_level(child):
                    # A block-level clickable (a card / list row) gets its own
                    # line. Appending it inline glued every consecutive job card
                    # of a `<div>`-based list into one unreadable line, defeating
                    # the whole point of returning a structured DOM.
                    self._flush(depth)
                    self._emit_content(depth, piece, (name,))
                else:
                    self._append_inline(piece, [name], [label])
            return

        # Preserve the raw whitespace of code blocks instead of collapsing it
        # into a single line.
        if child.tag == "pre":
            self._flush(depth)
            self._group_code(child, depth)
            return

        # A list item is a "rich" item when it carries blockish structure
        # (heading / image / nested list / block container …), e.g. a search
        # result. Group those so each item's fields stay together; keep simple
        # single-line items (e.g. nav links) inline to avoid noise.
        if child.tag == "li" or child.role == "listitem":
            self._flush(depth)
            if self._has_blockish_descendant(child):
                self._group(child, depth, "[列表项]", clip)
            else:
                self._render_children(child, depth, clip)
            return

        if self._is_text_block(child):
            self._flush(depth)
            self._group(child, depth, "[文本]", clip)
            return

        if child.tag in BLOCK_TAGS:
            self._flush(depth)
            self._render_children(child, depth, clip)
            return

        # inline / transparent container: recurse without adding a level
        for grand in child.children:
            self._render_child(grand, depth, clip)

    def _group(
        self,
        node: EnhancedNode,
        depth: int,
        header: str,
        clip: Optional[BBox],
        interactive: tuple[str, ...] = (),
        closing: str = "",
    ) -> None:
        before = len(self._lines)
        self._emit_header(depth, header, interactive, closing)
        self._stack.append((depth, header, closing))
        self._render_children(node, depth + 1, clip)
        self._stack.pop()
        # A group that rendered no real content is pure noise: an off-screen
        # ``<div class="title">基本信息</div>`` whose only text child fell outside
        # the viewport still passed ``_is_text_block`` (``_collect_text`` does not
        # filter by viewport), leaving a bare ``[文本]`` with nothing under it.
        # Drop the whole group; if it later gains visible content it returns.
        if not any(
            line.kind == "content" and line.text for line in self._lines[before + 1 :]
        ):
            del self._lines[before:]

    def _group_or_interact(
        self, node: EnhancedNode, depth: int, header: str, clip: Optional[BBox]
    ) -> None:
        """Render a structural group, wrapping it in the interactive element if needed.

        A landmark/list/table/… can itself be interactive: a scrollable
        ``<aside style="overflow-y:auto">`` job list, or a clickable
        ``<header style="cursor:pointer">``. The interactive element is the outer
        boundary the LLM interacts with; the structural header (``[侧栏]`` /
        ``[页眉]`` …) is rendered inside it. A clickable container that merely
        wraps other interactive elements is left as-is (naming it would be noise,
        see the click branch in ``_render_child``).
        """
        category = classify(node)
        if category == "scroll":
            self._group_scroll(node, depth, clip, inner_header=header)
        elif _is_click_like(category) and not self._has_interactive_descendant(node):
            self._group_click(node, depth, header, clip)
        else:
            before = len(self._lines)
            self._group(node, depth, header, clip)
            # A non-interactive group that rendered no real content (an empty
            # `[列表]` / `[文本]` / `[区块]`) carries no information and only
            # wastes attention. `[图片]`/interactive groups have their own paths.
            if not any(
                line.kind == "content" and line.text
                for line in self._lines[before + 1 :]
            ):
                del self._lines[before:]

    def _group_scroll(
        self,
        node: EnhancedNode,
        depth: int,
        clip: Optional[BBox],
        inner_header: str = "",
    ) -> None:
        name = self.registry.get_or_create(node)
        if node.tag == PAGE_SCROLL_TAG:
            # Document-level scrollbar: a single inline tag carrying the hint.
            self._emit_content(
                depth,
                f"<可滚动元素 {name}> 整页滚动条：用 scroll_delta 向下(正)/向上(负)滚动整个页面 </可滚动元素 {name}>",
                (name,),
            )
            return
        opening = f"<可滚动元素 {name}>"
        closing = f"</可滚动元素 {name}>"
        self._emit_header(depth, opening, (name,), closing)
        self._stack.append((depth, opening, closing))
        # Record where this scroller actually *is* so ``_page_info`` can report the
        # real one instead of ``document.scrollingElement`` (see ``_scroller_summary``).
        self._scroll_reports.append(
            (depth, name, node.tag == PAGE_SCROLL_TAG) + self._scroll_position(node)
        )
        before = len(self._lines)
        # Only a positioned scroller is a containing block for absolutely
        # positioned descendants, so only then does its ``overflow`` genuinely
        # clip them. A ``static`` scroller lets an abspos overlay escape (see
        # ``_render_child``); remember which case we are in for this subtree.
        prev_escapes = self._clip_escapes
        self._clip_escapes = node.styles.get("position") not in (
            "relative",
            "absolute",
            "fixed",
            "sticky",
        )
        try:
            if inner_header:
                self._group(node, depth + 1, inner_header, node.bbox)
            else:
                self._render_children(node, depth + 1, node.bbox)
        finally:
            self._clip_escapes = prev_escapes
        if len(self._lines) == before:
            self._emit_content(depth + 1, "（可滚动区域）")
        self._stack.pop()
        self._emit_close(depth, closing)

    # ------------------------------------------------------------------ #
    # long option columns (open picker panels / long select lists)
    # ------------------------------------------------------------------ #
    def _option_column_kind(self, node: EnhancedNode) -> str:
        """``"year"`` / ``"month"`` / ``""`` for a long list of option cells.

        A picker panel's candidate columns are ordinary scroll containers whose
        items are ``<可点击元素>``; dumping all of them (Feishu 起止时间 offers 127
        years + 12 months, and the year list scrolls) buries the form the model is
        filling. Classification needs no library knowledge: a column is a scroll
        container holding at least ``_OPTION_COLUMN_MIN`` option-like leaves, and
        whether it is the year or the month column is read off the values
        themselves. Anything shorter (a 5-item select, a nav list) is left alone.
        """
        if not node.is_element or node.tag == PAGE_SCROLL_TAG:
            return ""
        items = [c for c in node.children if c.is_element and classify(c)]
        if not items:
            return ""
        if not all(self._is_short_text_leaf(c) for c in items[:40]):
            return ""
        # Special entries ("至今" / "不限制") sit inside the numeric column and must
        # not defeat the year/month recognition — filter them out before matching.
        texts = []
        for c in items[:60]:
            raw = self._collect_text(c).strip()
            if self._is_special_option_text(raw):
                continue
            texts.append(raw)
        # A picker column renders its numbers as plain "01"~"12"; some libraries
        # append the unit ("01 月"), which must not defeat the recognition.
        def _monthish(value: str) -> bool:
            return bool(re.fullmatch(r"\d{1,2}\s*\u6708?", value or ""))
        # A purely numeric column IS a calendar-y option column whatever its length
        # (the month column only holds 12 entries) — grouping it is what tells the
        # model "type a year / pick a month" instead of leaving 12 bare numbers.
        numeric = bool(texts) and all(
            re.fullmatch(r"\d{4}|\d{1,2}\s*\u6708?", t or "") for t in texts
        )
        if not numeric and len(items) < _OPTION_COLUMN_MIN:
            return ""
        if texts and all(re.fullmatch(r"\d{4}", t or "") for t in texts):
            return "year"
        if texts and all(_monthish(t) for t in texts):
            return "month"
        return ""

    # A picker column's non-numeric entries: "至今" (open-ended end), "不限制".
    _SPECIAL_OPTION_TOKENS = ("至今", "至今为止", "不限制", "不限", "现在", "present", "now")

    @classmethod
    def _is_special_option_text(cls, text: str) -> bool:
        """True for a non-numeric sentinel entry inside an option column."""
        stripped = (text or "").strip()
        if not stripped:
            return True
        if stripped.lower() in cls._SPECIAL_OPTION_TOKENS:
            return True
        return not re.search(r"\d", stripped)

    @staticmethod
    def _is_short_text_leaf(node: EnhancedNode) -> bool:
        """True if ``node`` is an option cell: text only, no nested controls."""
        for child in node.children:
            if child.is_text:
                continue
            if not child.is_element:
                continue
            if classify(child):
                return False
        return True

    def _overlay_centerpiece_span(self, node: EnhancedNode) -> str:
        """The year/date span of an overlay's non-interactive **centrepiece**, else "".

        A picker panel's header is a real, scrollable block inside the open overlay
        (Feishu's year picker renders ``<table>`` of ``2018…2026`` cells under a
        ``2018 - 2026`` title) but holds **no control at all** — its cells only take
        a pointer cursor on ``:hover``, so every other rule here sees plain text and
        the model read them as un-clickable candidates. Recognising it lets the
        serializer state what the block is instead of dumping its cells.

        Deliberately narrow: the subtree must be free of controls *and* look like a
        year/date table (4-digit year cells, or a ``…-2026`` style span in the
        text), so an ordinary scrollable data table is never swallowed.
        """
        if not node.is_element or not node.bbox:
            return ""
        if self._has_rescuable_interactive_descendant(node) or self._iter_has_control(node):
            return ""
        if node.tag in ("table",):
            return ""
        # A bare year/month span ("2018 - 2026") is the panel's own header title: it
        # is real text, but the centrepiece line below already states the span, so
        # this one is a duplicate of the same fact.
        if _SPAN_ONLY_TEXT_RE.fullmatch(self._collect_text(node).strip()):
            return _SUPPRESS_CENTERPIECE
        # Collect from **leaf runs**, not the concatenated subtree text:
        # ``_collect_text`` joins adjacent cells ("201820192020…") and any
        # ``\b``-style anchor then fails to see individual years.
        years: list[str] = []
        stack = [node]
        seen = 0
        while stack and seen < 400:
            cur = stack.pop(0)
            seen += 1
            for child in cur.children:
                if child.is_text:
                    years.extend(re.findall(r"(?<!\d)(1[89]\d{2}|2[01]\d{2})(?!\d)", child.text))
                    continue
                if child.is_element:
                    stack.append(child)
        if len(years) < 3:
            return ""
        ordered = sorted(set(years))
        return f"{ordered[0]}-{ordered[-1]}"

    def _iter_has_control(self, node: EnhancedNode, depth: int = 4) -> bool:
        """True if ``node`` or a bounded descendant classifies as interactive."""
        if depth < 0:
            return False
        for child in node.children:
            if child.is_text or not child.is_element or child.hidden:
                continue
            if classify(child):
                return True
            if self._iter_has_control(child, depth - 1):
                return True
        return False

    def _group_overlay_centerpiece(
        self, node: EnhancedNode, depth: int, span: str, clip: Optional[BBox]
    ) -> None:
        """Render a panel's non-interactive centrepiece as one describing line."""
        name = self.registry.get_or_create(node)
        opening = f"<可滚动元素 {name}>"
        closing = f"</可滚动元素 {name}>"
        self._emit_header(depth, opening, (name,), closing)
        self._emit_content(
            depth + 1,
            f"\uff08\u9762\u677f\u6807\u9898\u533a\uff1a{span} \u5e74\u4efd\u8303\u56f4\uff0c"
            "\u6b64\u5904\u65e0\u53ef\u70b9\u5143\u7d20\uff1b\u9009\u9879\u5728\u5019\u9009\u5217\u91cc\uff09",
        )
        self._emit_close(depth, closing)

    def _group_option_column(
        self, node: EnhancedNode, depth: int, kind: str, clip: Optional[BBox]
    ) -> None:
        """Render a long option column compactly, keeping the column boundary.

        The output keeps (a) the scrollable boundary so the model knows it can
        scroll this specific column rather than the page, and (b) a head/tail
        sample plus the total count — enough to recognise "this is the year
        column, 2026 is at the top", without listing 127 entries.
        """
        name = self.registry.get_or_create(node)
        opening = f"<可滚动元素 {name}>"
        closing = f"</可滚动元素 {name}>"
        kind_label = {"year": "\u5e74\u4efd", "month": "\u6708\u4efd"}.get(kind, "\u5019\u9009")
        options = [
            c
            for c in node.children
            if c.is_element and classify(c) and self._is_short_text_leaf(c)
        ]
        samples = [self._collect_text(c).strip() for c in options[:_OPTION_COLUMN_SAMPLE]]
        selected = [
            self._collect_text(c).strip()
            for c in options
            if self._selection_state(c) is True
        ]
        specials = [
            self._collect_text(c).strip()
            for c in options
            if self._is_special_option_text(self._collect_text(c))
        ]
        parts = [f"{kind_label}候选 {len(options)} 项"]
        if specials:
            # "至今" is the one entry the model most needs to find and cannot reach
            # by scrolling to a number, so name it explicitly.
            parts.append(f"含特殊项：{'、'.join(dict.fromkeys(specials))[:40]}")
        if samples:
            shown = "、".join(samples)
            if len(options) > len(samples):
                shown += f"、…（还有 {len(options) - len(samples)} 项，可向下滚动该列查看）"
            parts.append(f"顶部：{shown}")
        if selected:
            parts.append(f"当前已选：{'、'.join(selected[:2])}")
        self._emit_header(depth, opening, (name,), closing)
        self._emit_content(depth + 1, "；".join(parts))
        self._scroll_reports.append(
            (depth, name, node.tag == PAGE_SCROLL_TAG) + self._scroll_position(node)
        )
        self._emit_close(depth, closing)

    def _group_click(
        self, node: EnhancedNode, depth: int, header: str, clip: Optional[BBox]
    ) -> None:
        name = self.registry.get_or_create(node)
        tag, _ = CATEGORY_TAGS[classify(node)]
        opening = f"<{tag} {name}>"
        closing = f"</{tag} {name}>"
        before = len(self._lines)
        self._emit_header(depth, opening, (name,), closing)
        self._stack.append((depth, opening, closing))
        self._group(node, depth + 1, header, clip)
        self._stack.pop()
        self._emit_close(depth, closing)
        # An interactive *structural* container that renders no content at all
        # (``<可点击元素 eN>\n[列表]\n</可点击元素 eN>``) is pure noise: the LLM
        # cannot tell what it does, and clicking it is a guess. Such wrappers come
        # from empty chrome (a not-yet-populated autocomplete listbox carrying a
        # ``tabindex``). Drop it entirely; if it later gains content it is named
        # then.
        if not any(
            line.kind == "content" and line.text for line in self._lines[before + 1 : -1]
        ):
            del self._lines[before:]

    def _group_image(self, node: EnhancedNode, depth: int) -> None:
        self._emit_header(depth, "[图片]")
        self._stack.append((depth, "[图片]", ""))
        alt = _clean_alt_text(node.attributes.get("alt", "")) or _clean_alt_text(
            node.ax_name
        )
        # An image with no alt/name (very common for logo/icon/decorative images)
        # used to emit ``[图片]\n\t图片``: the fallback word merely repeated the
        # header and added no information. Emit the header alone in that case.
        if alt:
            self._emit_content(depth + 1, self._truncate(alt, 120))
        self._stack.pop()

    def _group_code(self, node: EnhancedNode, depth: int) -> None:
        self._emit_header(depth, "[代码]")
        self._stack.append((depth, "[代码]", ""))
        lines = self._raw_text(node).split("\n")
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines:
            self._emit_content(depth + 1, "（空代码块）")
        for raw in lines:
            # Do NOT collapse whitespace here (``_truncate`` would); indentation
            # is the whole point of a code block.
            text = raw if len(raw) <= MAX_TEXT_LENGTH else raw[:MAX_TEXT_LENGTH] + _TRUNCATION_NOTE
            self._emit_content(depth + 1, text)
        self._stack.pop()

    def _raw_text(self, node: EnhancedNode) -> str:
        """Concatenate text without collapsing whitespace (for ``<pre>``)."""
        parts: list[str] = []
        for child in node.children:
            if child.is_text:
                parts.append(child.text)
            elif child.is_element:
                parts.append(self._raw_text(child))
        return "".join(parts)

    def _is_header_row(self, row: EnhancedNode) -> bool:
        cells = [
            c for c in row.children if c.is_element and c.tag in ("td", "th")
        ]
        return bool(cells) and all(c.tag == "th" for c in cells)

    def _emit_header(
        self,
        depth: int,
        text: str,
        interactive: tuple[str, ...] = (),
        closing: str = "",
    ) -> None:
        self._lines.append(
            OutLine(depth, text, "header", tuple(self._stack), tuple(interactive), closing)
        )

    def _emit_content(
        self, depth: int, text: str, interactive: tuple[str, ...] = ()
    ) -> None:
        self._lines.append(
            OutLine(depth, text, "content", tuple(self._stack), tuple(interactive))
        )

    def _emit_close(self, depth: int, text: str) -> None:
        self._lines.append(
            OutLine(depth, text, "close", tuple(self._stack), ())
        )

    def _emit_blank(self, depth: int) -> None:
        """Emit an intentionally empty line (a structural paragraph break)."""
        self._lines.append(
            OutLine(depth, "", "content", tuple(self._stack), ())
        )

    def _flush(self, depth: int) -> None:
        text = self._buffer.strip()
        names = self._buffer_names
        self._buffer = ""
        self._buffer_names = []
        self._buffer_labels = []
        if not text:
            return
        self._lines.append(
            OutLine(depth, text, "content", tuple(self._stack), tuple(names))
        )

    # ------------------------------------------------------------------ #
    # buffer helpers
    # ------------------------------------------------------------------ #
    def _append_text(self, node: EnhancedNode, depth: int) -> None:
        """Append the text of ``node`` at ``depth``.

        When the parent element renders with a whitespace-preserving
        ``white-space`` (``pre``/``pre-wrap``/``pre-line``/``break-spaces``) the
        literal newlines are meaningful and become real output lines, including
        blank lines used as paragraph separators. Every other element collapses
        newlines to a space, exactly like the browser.
        """
        text = node.text
        white_space = self._parent_white_space(node)
        if "\n" in text and white_space in _PRESERVE_NEWLINE_WHITE_SPACE:
            # ``pre-line`` collapses runs of spaces but keeps newlines; the
            # other modes keep the line's internal spacing verbatim.
            collapse_spaces = white_space == "pre-line"
            for index, line in enumerate(self._split_lines(text, collapse_spaces)):
                if index:
                    self._flush(depth)
                if line:
                    self._append_segment(line)
                else:
                    self._emit_blank(depth)
            return
        self._append_segment(" ".join(text.split()))

    def _append_segment(self, text: str) -> None:
        if not text:
            return
        # Skip a text node that just repeats the label of the interactive tag
        # immediately before it (e.g. a button rendered as
        # ``<可点击元素 eN>进入全屏模式</可点击元素 eN>`` followed by its visible
        # text ``全屏``). The tag's label already carries the meaning.
        if self._buffer.endswith(">") and self._buffer_labels:
            last = self._buffer_labels[-1]
            if last and text in last:
                return
        if len(text) > MAX_TEXT_LENGTH:
            text = text[:MAX_TEXT_LENGTH] + _TRUNCATION_NOTE
        if self._buffer and not self._buffer.endswith((" ", ">")):
            self._buffer += " "
        self._buffer += text

    def _parent_white_space(self, node: EnhancedNode) -> str:
        parent = node.parent
        if parent is None:
            return ""
        return (parent.styles or {}).get("white-space", "")

    def _split_lines(self, text: str, collapse_spaces: bool) -> list[str]:
        """Split on newlines, collapse/dedupe blank lines, trim the ends."""
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if collapse_spaces:
            parts = [" ".join(line.split()) for line in text.split("\n")]
        else:
            parts = text.split("\n")
        cleaned: list[str] = []
        for part in parts:
            if part == "" and cleaned and cleaned[-1] == "":
                continue
            cleaned.append(part)
        while cleaned and cleaned[0] == "":
            cleaned.pop(0)
        while cleaned and cleaned[-1] == "":
            cleaned.pop()
        return cleaned

    def _append_inline(
        self, piece: str, names: list[str], labels: Optional[list[str]] = None
    ) -> None:
        if not piece:
            return
        self._buffer += piece
        self._buffer_names.extend(names)
        if labels:
            self._buffer_labels.extend(labels)

    # ------------------------------------------------------------------ #
    # classification helpers
    # ------------------------------------------------------------------ #
    def _in_clip(self, node: EnhancedNode, clip: Optional[BBox]) -> bool:
        if clip is None or not node.bbox:
            return True
        x, y, w, h = node.bbox
        cx, cy, cw, ch = clip
        return not (x + w < cx - 2 or x > cx + cw + 2 or y + h < cy - 2 or y > cy + ch + 2)

    def _table_cell_renderable(self, cell: EnhancedNode) -> bool:
        """Whether a ``<td>`` / ``<th>`` should appear on its row line.

        Normally only visible, in-viewport cells are rendered (an off-screen cell
        must not inject separators or text). The one exception mirrors the
        visibility rescue in ``_render_child``: a *zero-area* cell that is fully
        transparent (its popup/table was captured on the enter-animation first
        frame, e.g. an Ant Design range-picker calendar) is real content and must
        be serialized, otherwise the whole day grid silently disappears and the
        LLM can never pick a date.
        """
        if cell.tag in SKIP_TAGS or cell.hidden:
            return False
        if in_collapsed_overlay_portal(cell):
            # A cell of a closed picker panel still mounted in a collapsed portal
            # (see ``build.in_collapsed_overlay_portal``): its own box is real, so
            # this method would happily claim it — but the model must not see the
            # options of a panel nobody has open.
            return False
        if cell.visible and cell.in_viewport:
            return True
        return (not cell.visible) and self._effective_opacity_zero(cell)

    @staticmethod
    def _effective_opacity_zero(node: EnhancedNode) -> bool:
        """True if ``node`` or any ancestor is fully transparent (``opacity: 0``).

        Used to tell a *not yet laid out* subtree from a genuinely collapsed one
        (see ``_render_child``): an element being animated in by a component
        library has its own computed ``opacity`` at 0 during the animation's
        first frame, while its descendants keep ``opacity: 1`` (opacity does not
        inherit as a computed value). A collapsed element (``height: 0`` behind
        ``overflow: hidden``) keeps ``opacity: 1`` on every ancestor, so it is
        never mistaken for transient content.
        """
        current: Optional[EnhancedNode] = node
        hops = 0
        while current is not None and hops < 12:
            opacity = current.styles.get("opacity")
            if opacity is not None:
                try:
                    if float(opacity) <= 0.01:
                        return True
                except (TypeError, ValueError):
                    pass
            current = current.parent
            hops += 1
        return False

    def _heading_level(self, node: EnhancedNode) -> int:
        if node.tag in HEADING_TAGS:
            return HEADING_TAGS[node.tag]
        if node.role == "heading":
            raw = node.attributes.get("aria-level", "")
            if raw.isdigit():
                return max(1, min(6, int(raw)))
            return 2
        return 0

    def _is_block_level(self, node: EnhancedNode) -> bool:
        if node.tag in BLOCK_TAGS:
            return True
        display = node.styles.get("display", "")
        # ``display: table`` is a block-level box, but its *internal* boxes
        # (``table-cell`` / ``table-row`` / ``table-*-group`` / ``table-column``
        # / ``table-caption``) are NOT: the ``<tr>`` branch already lays the row
        # out and inserts the ``|`` cell separators. Treating ``table-cell`` as
        # block made every clickable table cell demand its own line, collapsing
        # a calendar's 7-column day grid into a flat vertical list of numbers
        # and emitting stray ``|`` lines (a real date picker, 2026-09-27 debug).
        if display.startswith(_BLOCK_DISPLAY_PREFIXES) and not display.startswith(
            "table-"
        ):
            return True
        return False

    def _is_text_block(self, node: EnhancedNode) -> bool:
        if node.tag in TEXT_BLOCK_EXCLUDE:
            return False
        if not self._is_block_level(node):
            return False
        if self._landmark_for(node) or self._heading_level(node) or self._is_image(node):
            return False
        if node.tag in ("ul", "ol", "table") or node.role in ("list", "table"):
            return False
        if classify(node):
            return False
        if not self._collect_text(node):
            return False
        if self._has_blockish_descendant(node):
            return False
        return True

    def _has_blockish_descendant(self, node: EnhancedNode) -> bool:
        for child in node.children:
            if child.is_text:
                continue
            if not child.is_element:
                if self._has_blockish_descendant(child):
                    return True
                continue
            if child.tag in SKIP_TAGS or child.hidden:
                continue
            if not child.visible and not (
                child.rendered and self._effective_opacity_zero(child)
            ):
                # A genuinely collapsed / boxless child is not structure. But a
                # zero-area child that is fully transparent is real content caught
                # on the enter-animation first frame (ID90): ``_render_child``
                # serializes it via the opacity fallback, so the parent must see
                # it as structure too. Otherwise a visible wrapper whose only
                # "blockish" descendant is mid-animation degrades to ``[文本]``,
                # producing 6~8 nested anonymous ``[文本]`` shells around a popup.
                continue
            if not child.in_viewport:
                continue
            if self._is_block_level(child):
                return True
            if self._landmark_for(child) or self._heading_level(child) or self._is_image(child):
                return True
            if child.tag in ("ul", "ol", "table") or child.role in ("list", "table"):
                return True
            if classify(child) and self._has_interactive_descendant(child):
                return True
            if self._has_blockish_descendant(child):
                return True
        return False

    def _interactive_label(self, node: EnhancedNode, category: str) -> str:
        if category == "select":
            # A native dropdown whose value is selected rather than typed: label
            # it with the field name and the current / placeholder value so the
            # LLM knows *what* it is and *whether* a choice was made. (``fill`` on
            # this element selects the option whose text matches.)
            return self._select_label(node)
        if category == "searchable":
            # A searchable select's typeahead has neither a value nor a
            # placeholder of its own (the chosen value lives in a sibling
            # ``.ant-select-selection-item``). Name it after the field and the
            # current selection so the model knows what it is editing:
            # ``最高学历专业：金融学``. Its own tool (``tool_07``) carries the
            # "typing only filters, click a candidate" contract, so the label
            # stays a plain "field: value" without extra hints.
            label = self._searchable_label(node)
        elif category == "input":
            value = self._input_value(node)
            if value:
                # Say "value" explicitly. A privacy-masked real value reads as
                # ``<邮箱>`` — byte-identical to the placeholder the model is told
                # to type — so ``<可输入元素 e61><邮箱></>`` was understood as "empty
                # field, placeholder hint 邮箱" and the model re-filled a field that
                # already held the real address. ``值：…`` versus
                # ``（空，占位提示：…）`` removes the ambiguity, and matches the
                # picker-entry wording already introduced in ID122.
                label = f"值：{value}"
            else:
                # Never render the placeholder as if it were the current value:
                # an empty field used to read ``<可输入元素 e53>结束日期</…>`` and
                # the model believed it already held "结束日期". Mark emptiness
                # explicitly and keep the placeholder only as a hint.
                label = self._empty_input_label(node)
        else:
            label = self._label(node)
            if is_widget_trigger_input(node):
                # The text face of a *click-to-open* widget (a readonly select /
                # cascader / date picker): its own value / placeholder is all it
                # shows, and without the field name two such fields in one row are
                # indistinguishable.
                label = self._picker_entry_label(node)
            else:
                # An unlabeled control icon (radio / checkbox circle) pairs with a text
                # label; name it after that text but keep it distinguishable from the
                # label's own clickable entry (which expands / navigates): “选择：重庆市”.
                # A native checkbox / radio input is the same "control + text" shape
                # without an icon, so it gets the same treatment instead of an empty tag.
                if _is_click_like(category) and (
                    is_control_icon(node) or self._is_choice_input(node)
                ):
                    paired = (
                        self._adjacent_text_label(node)
                        or self._pointer_sibling_label(node)
                        or self._nearby_text_label(node)
                    )
                    # Prefer the visible option text over the control's ``name`` (an
                    # internal id such as ``11_20_1``): the text is what the user (and
                    # the LLM) reads. A real accessible name is left untouched.
                    if paired and (not label or self._is_machine_token(label)):
                        label = f"选择：{paired}"
                # A composite control (e.g. a select box) often shows only its value or
                # placeholder (“请选择”). Prefix the associated field label so the LLM
                # can tell which field it is: “政治面貌：请选择”.
                if _is_click_like(category) and self._has_field_input_descendant(node):
                    prefix = self._associated_field_label(node)
                    if prefix and prefix not in label:
                        label = f"{prefix}：{label}" if label else prefix
        # Drop icon-font private-use glyphs / invisible marks first: a label that
        # was *only* a glyph (``\ue76e``) must fall through to the human fallback
        # below instead of being emitted as a content-free clickable.
        label = _strip_icon_glyphs(label).strip()
        # Never emit an empty interactive tag: an unnamed control is useless to the
        # LLM. Fall back to nearby text / a semantic attribute token / a generic word.
        if not label:
            label = self._fallback_label(node, category)
        # Fold an inline validation message into the field's own label. A page
        # renders "必填项未填写" in a sibling ``.…error…`` node of the control and
        # the old serializer emitted it as a bare ``[文本]`` line — the LLM could
        # not tell which field it belonged to (and one appeared next to an already
        # filled field). Attaching it (``… [校验：必填项未填写]``) makes the state
        # unambiguous; the node is consumed so it is not emitted twice.
        if category in ("input", "select", "searchable", "clickdropdown"):
            error = self._inline_error_text(node)
            if error and error not in label:
                label = f"{label} [校验：{error}]"
        # Mark the selected option of a radio / checkbox / tab group. A click on
        # one of these changes only the control's state (the page text is
        # unchanged), so without this marker the diff would say “（页面无变化）” and
        # the LLM could not confirm that its click was applied. Only the selected
        # item is marked; unselected items carry no marker.
        if self._selection_state(node) is True:
            label = f"{label} [已选]"
        if _is_click_like(category):
            label = self._qualify_action_label(node, label)
        return label

    # A bare action verb ("添加" / "删除" / "编辑" …) says nothing about *what* it
    # acts on. A form that repeats it once per section (Feishu Jobs lists 教育经历
    # / 实习经历 / 项目经历 / 作品 / 获奖 / 语言能力, each with its own 添加) turns
    # every one of them into the same unreadable tag; in the real session the model
    # burned two long reasoning turns guessing whether the 添加 sitting right under
    # 「描述」 added a project or confirmed the description. Qualifying it with the
    # enclosing section title ("项目经历·添加") removes the guess.
    _GENERIC_ACTION_LABELS = (
        "添加",
        "新增",
        "新建",
        "删除",
        "移除",
        "编辑",
        "修改",
        "保存",
        "提交",
        "取消",
        "确定",
        "确认",
        "搜索",
        "查询",
        "上传",
        "下载",
        "更多",
        "展开",
        "收起",
        "查看",
        "复制",
        "清空",
        "重置",
        "返回",
        "上一步",
        "下一步",
        "完成",
        "上传文件",
        "加载更多",
        "展开更多",
    )
    _SECTION_TITLE_TOKENS = ("title", "heading", "headline", "section-name")

    def _qualify_action_label(self, node: EnhancedNode, label: str) -> str:
        """Prefix a generic action verb with its enclosing section's title."""
        if not label or label not in self._GENERIC_ACTION_LABELS:
            return label
        title = self._enclosing_section_title(node)
        if not title or title == label or label in title:
            return label
        return f"{title}·{label}"

    def _enclosing_section_title(self, node: EnhancedNode, max_hops: int = 6) -> str:
        """The nearest *preceding* title text of an enclosing block, else ``""``.

        Bounded to a few ancestors and to short title-like nodes so a page header
        ("投递简历 - 加入沐瞳科技") can never become a control's qualifier. Cached per
        node because the walk is only cheap relative to being done once.
        """
        cached = self._section_titles.get(node.node_id)
        if cached is not None:
            return cached
        title = ""
        branch = node
        ancestor = node.parent
        hops = 0
        while ancestor is not None and hops < max_hops:
            for child in ancestor.children:
                if child is branch:
                    break
                if not child.is_element:
                    continue
                found = self._title_text_in(child, 3)
                if found:
                    title = found
                    break
            if title:
                break
            branch = ancestor
            ancestor = ancestor.parent
            hops += 1
        self._section_titles[node.node_id] = title
        return title

    def _title_text_in(self, node: EnhancedNode, max_depth: int) -> str:
        """The first heading-like text inside ``node`` (≤ ``max_depth`` levels)."""
        if not node.is_element or self._is_helper_text(node):
            return ""
        raw = (
            f"{node.attributes.get('class', '')} {node.attributes.get('id', '')}"
        ).lower()
        if node.tag in ("h1", "h2", "h3", "h4", "h5", "h6") or any(
            tok in raw for tok in self._SECTION_TITLE_TOKENS
        ):
            text = self._collect_text(node).strip()
            # A real section heading is short and unpunctuated. Without this guard
            # a ``…__title`` wrapper around a *paragraph* became the qualifier: the
            # iTalent form's declaration "我所提交的上述材料、信息均真实有效" got
            # prefixed onto its 上一题 button.
            if text and len(text) <= 16 and not re.search(r"[，,。.；;！!？?]", text):
                return text
        if max_depth <= 0:
            return ""
        for child in node.children:
            if child.is_element:
                text = self._title_text_in(child, max_depth - 1)
                if text:
                    return text
        return ""

    @staticmethod
    def _is_error_text(node: EnhancedNode) -> bool:
        """True for an *error* / invalid / alert node (not a mere hint)."""
        if not node.is_element:
            return False
        if (node.attributes.get("role") or "").lower() == "alert":
            return True
        raw = (
            f"{node.attributes.get('class', '')} {node.attributes.get('id', '')}"
        ).lower()
        return any(tok in raw for tok in ("error", "invalid", "danger", "-err-", "err-"))

    def _inline_error_text(self, node: EnhancedNode) -> str:
        """Visible inline validation text attached to ``node``'s field, else ``""``.

        Walks a couple of ancestor levels looking for an ``error`` sibling of the
        control's wrapper (``sd-Input-message sd-Input-error`` and friends),
        consumes it and returns its text so it can be folded into the label.
        """
        branch = node
        ancestor = node.parent
        hops = 0
        while ancestor is not None and hops < 3:
            for sibling in ancestor.children:
                if sibling is branch or not sibling.is_element:
                    continue
                if not self._is_error_text(sibling):
                    continue
                if sibling.hidden or not sibling.rendered or not sibling.visible:
                    continue
                if sibling.node_id in self._consumed:
                    continue
                text = self._collect_text(sibling)
                if not text or text in ("*", "＊"):
                    continue
                self._consumed.add(sibling.node_id)
                return self._truncate(text, 40)
            branch = ancestor
            ancestor = ancestor.parent
            hops += 1
        return ""

    def _searchable_label(self, node: EnhancedNode) -> str:
        """Label a searchable select's typeahead as ``字段：当前值``.

        The typeahead input itself has neither a value nor a placeholder (the
        chosen value lives in a sibling ``.ant-select-selection-item``), so the
        field name comes from the associated ``<label>`` and the current
        selection from the nearest sibling text. When the placeholder and the
        folded value coincide, the sibling text is consumed so it is not emitted
        a second time.

        A *custom* select filter input (Moka ``sd-Select``) needs a different
        label walk (its field name is a sibling ``…title…`` element, its control
        wrapper is a ``<label>`` that must not be treated as a field title, and its
        validation message sits beside the input); that walk is gated on
        ``is_custom_select_input`` so every other site's searchable label is
        unchanged.
        """
        if is_date_picker_input(node):
            # A date / time picker's text entry holds its *own* value (unlike a
            # searchable select, whose value lives in a sibling element), so the
            # label has to be built from the input itself.
            return self._picker_entry_label(node)
        if is_custom_select_input(node):
            # The Moka-shaped walk first; fall back to the generic field-label
            # association when it finds nothing. Feishu's ``.atsx-select`` inline
            # search box hits ``is_custom_select_input`` too, and the Moka walk
            # looks for a ``…title…`` sibling that Feishu does not render — so the
            # field name was lost and the control showed only its value
            # (``<可搜索下拉元素 e64>上海外国语大学</>`` with 「学校名称」 stranded on
            # the line above).
            prefix = self._searchable_field_label(node) or self._associated_field_label(
                node
            )
            current = self._searchable_value_text(node)
        else:
            prefix = self._associated_field_label(node)
            current = self._nearby_text_label(node)
        if current and not _has_word_char(current):
            # A range control renders a bare separator ("-", "~") between its two
            # inputs; treating it as the "current value" produced the nonsense
            # label ``起止年月：-``.
            current = ""
        if prefix and current:
            if prefix in current:
                # ``current`` already contains the field name (e.g. the nearby
                # text is the whole ``性别：男`` block).
                label = current
            elif self._field_already_has_value(prefix, current):
                label = prefix
            else:
                label = f"{prefix}：{current}"
        else:
            label = prefix or current or self._empty_input_label(node)
        if current and current in label:
            self._consume_nearby_label_source(node, current)
        typed = (self._input_value(node) or "").strip()
        if typed and typed not in label:
            # The typeahead holds a *typed* run that is not part of the presented
            # value: say so explicitly. A searchable select shows filter text in the
            # same box it will later show the chosen value in, and the model read
            # ``获奖时间：2025`` as "already 2025" purely because it had typed the
            # digits itself (2026-10-01 session, where it then spent a long turn
            # asking whether the value had committed). Naming the pending filter
            # removes that ambiguity without changing the value read-out.
            label = f"{label}（筛选词：{self._truncate(typed, 20)}，尚未点选候选）"
        return label

    def _picker_shell_label(self, node: EnhancedNode) -> str:
        """Label the whole div-based picker shell: ``字段：值`` / ``字段：（空…）``.

        The shell holds no typeable input, so both halves come from the widget
        itself: the field name from the associated ``<label>`` *outside* the shell
        (``_associated_field_label`` skips the shell's own value parts) and the
        current value from the visible value parts (``2017-09`` + ``2021-06`` →
        ``2017-09 ~ 2021-06``). Report emptiness honestly — the previous output
        claimed ``（空）`` for a range that was plainly filled, which is what made
        the model re-open and re-pick a field that was already correct.

        Values are read from the widget's **own labelled parts** when it has them
        (Feishu renders each end as ``<span …-label-year>2026</span>`` +
        ``<span …-label-month>05</span>``), because a generic "first two text
        runs" harvest loses half of a half-filled endpoint: with 年=2026 月=MM the
        harvest saw ``["2026", "YYYY-MM"]``, dropped the format hint and reported
        ``起止时间：至今`` / ``（空，占位提示：YYYY-MM）`` — a *false empty* that made the
        2026-10-01 session re-open the picker four times and finally give up.
        """
        prefix = self._associated_field_label(node)
        raw_units = self._labelled_value_units(node)
        if raw_units is None:
            raw_units = self._shell_value_parts(node)
        # Values are the widget's *own* value parts; anything that is only a format
        # skeleton (``YYYY-MM``) is not a value.
        filled = [u for u in raw_units if not self._is_format_placeholder(u)]
        if filled:
            # Show every end, keeping the format skeleton for the one still empty:
            # ``2026-05 ~ YYYY-MM`` states exactly what is missing, whereas dropping
            # the empty end made a half-filled range look like a single value.
            value = " ~ ".join(raw_units)
            label = f"{prefix}：{value}" if prefix and prefix not in value else value
        else:
            hint = raw_units[0] if raw_units else ""
            empty = f"（空，占位提示：{self._truncate(hint, 40)}）" if hint else "（空）"
            label = f"{prefix}：{empty}" if prefix and prefix not in empty else empty
        if is_range_picker_shell(node):
            label += RANGE_SHELL_MARK
        # A two-end widget shows both ends in one control; marking which end the
        # widget itself currently considers active (its own focus class) tells the
        # model whether its last pick landed on 起 or 止, without inventing a
        # position the page never states.
        position = self._range_focus_position(node)
        if position:
            label += f"[当前焦点：{position}]"
        return label

    def _range_focus_position(self, node: EnhancedNode) -> str:
        """``"起"`` / ``"止"`` when the widget marks one end as focused, else ``""``.

        Feishu's period control moves a ``…-focus`` class onto the active end's
        label wrapper; reading it costs one bounded subtree scan and answers the
        question the LLM otherwise has to guess ("did my pick go to 起 or 止?").
        """
        wrappers: list[EnhancedNode] = []
        stack = [node]
        seen = 0
        while stack and seen < 200:
            cur = stack.pop(0)
            seen += 1
            for child in cur.children:
                if not child.is_element:
                    continue
                if self._value_part_tokens(child):
                    wrappers.append(child)
                    continue
                stack.append(child)
        if len(wrappers) < 2:
            return ""
        wrappers.sort(key=lambda n: self._document_order(n))
        classes = [
            (w.attributes.get("class") or "") for w in wrappers
        ]
        if not any("focus" in c for c in classes):
            return ""
        # The first value part is the range's start (the widget renders 起 then 止),
        # so the focused index tells which end the last interaction landed on.
        return "起" if "focus" in classes[0] else "止"

    def _labelled_value_units(self, node: EnhancedNode) -> Optional[list]:
        """Value of each *labelled* end of a picker shell, or ``None``.

        A picker that marks its value parts with the component's own tokens
        (``*-label-year`` / ``*-label-month`` / ``data-cy="year"``) can be read
        exactly: each *label wrapper* becomes one value ("2026" + "05" →
        ``2026-05``), and an unset half keeps its format placeholder
        (``2026`` + ``MM`` → ``2026-MM``) instead of silently disappearing.
        Returns ``None`` when the widget has no such markers, so callers fall back
        to the generic text harvest.
        """
        labels: list[EnhancedNode] = []
        stack = [node]
        seen = 0
        while stack and seen < 400:
            cur = stack.pop(0)
            seen += 1
            for child in cur.children:
                if not child.is_element:
                    continue
                tokens = self._value_part_tokens(child)
                if tokens:
                    labels.append(child)
                    seen += 1
                    continue
                stack.append(child)
        if not labels:
            return None
        # Keep document order (the BFS above is order-preserving per level).
        labels.sort(key=lambda n: self._document_order(n))
        # Merge: a widget renders one end either as **one** wrapper that owns both
        # halves (``<div …-label><span …-year>2026</span>-<span …-month>05</span>
        # </div>``) or as **consecutive** single-half siblings. Accumulate until an
        # end is complete (holds both a year and a month, or is a lone 「至今」).
        units: list[str] = []
        pending = ""
        for label in labels:
            tokens = self._value_part_tokens(label)
            pending += self._join_label_parts(label)
            # An end is complete when this part closes it: both year+month in one
            # wrapper, a month/day closing a pending year, a lone 「至今」, or any
            # unmarked value part (nothing left to pair with).
            closes = (
                ("year" in tokens and "month" in tokens)
                or "month" in tokens
                or "day" in tokens
                or "today" in tokens
                or not (tokens & {"year", "month", "day"})
            )
            if closes:
                units.append(pending)
                pending = ""
        if pending:
            units.append(pending)
        return [u for u in units if u]

    def _value_part_tokens(self, node: EnhancedNode) -> set:
        """Token set that marks ``node`` as a picker *value part* (or empty)."""
        raw = node.attributes.get("class") or ""
        cy = (node.attributes.get("data-cy") or "").lower()
        tokens = set()
        for token in raw.replace("--", "-").replace("_", "-").split("-"):
            if token in ("year", "month", "day", "date", "today", "totoday"):
                tokens.add(token)
        if cy in ("year", "month", "day"):
            tokens.add(cy)
        return tokens

    def _join_label_parts(self, label: EnhancedNode) -> str:
        """Text of one value part, with its own halves/separators preserved."""
        chunks: list[str] = []
        for child in label.children:
            if child.is_text and (child.text or "").strip():
                chunks.append(child.text.strip())
            elif child.is_element:
                text = self._collect_text(child).strip()
                if text:
                    chunks.append(text)
        joined = "".join(chunks).strip()
        return joined or self._collect_text(label).strip()

    @staticmethod
    def _document_order(node: EnhancedNode) -> int:
        return node.node_id

    def _shell_value_parts(self, node: EnhancedNode) -> list:
        """The visible value runs of a div-based picker, in document order.

        Feishu's period picker renders one ``<div>`` per end; a library that adds
        further furniture is bounded by the two-part limit.
        """
        parts: list = []
        for child in node.children:
            if not child.is_element or child.hidden or not child.rendered:
                continue
            if child.tag in ("input", "textarea", "select"):
                continue
            text = self._collect_text(child).strip()
            if text:
                parts.append(text)
            if len(parts) >= 2:
                break
        return parts

    @staticmethod
    def _is_format_placeholder(text: str) -> bool:
        """True for a date *format* hint (``YYYY-MM``) rather than a value.

        An empty picker still renders its format so the user knows what to type;
        treating it as the current value would report ``起止时间：YYYY-MM ~ YYYY-MM``
        for an untouched field.
        """
        stripped = (text or "").strip()
        if not stripped or len(stripped) > 12:
            return False
        if re.search(r"YYYY|yyyy|MM|DD|HH|mm|ss", stripped):
            return True
        # ``年-月`` / ``----`` style hints carry no digits at all; a real value
        # ("至今", "2026-05") always does or is not pure punctuation.
        return bool(re.fullmatch(r"[\s\-/.年月日：:]+", stripped))

    def _picker_entry_label(self, node: EnhancedNode) -> str:
        """Label a picker / widget text entry as ``字段：值`` or ``字段：（空…）``.

        Used for the text face of a date / time picker (``<可搜索下拉元素>``) and of
        a readonly select / cascader (``<可点击下拉元素>``). Both hold their value
        in the input itself, so the value must be read from the input — not from a
        sibling. The field name is essential: the 4399 简历 form rendered 起止年月
        as two bare ``<可输入元素 e80>（空）</>`` with the field name only in a
        separate ``[文本]`` line, so the model had to guess which field it was
        addressing (and whether the range was start or end).

        A **range** control's two inputs additionally carry ``[区间 i/n]``: they are
        *one* widget with *one* panel that only commits after two candidate picks
        inside the same overlay session (the widget sorts the pair, and a single
        pick followed by a blur rolls both ends back). Without the marker the two
        entries read as two independent fields.
        """
        prefix = self._associated_field_label(node)
        value = self._input_value(node)
        if value:
            if prefix and prefix not in value:
                label = f"{prefix}：{value}"
            else:
                label = value or prefix
        else:
            empty = self._empty_input_label(node)
            if prefix and prefix not in empty:
                label = f"{prefix}：{empty}"
            else:
                label = empty or prefix
        position = picker_range_position(node)
        if position is not None:
            index, total = position
            label = f"{label}[区间 {index + 1}/{total}]"
        return label

    @staticmethod
    def _field_already_has_value(field: str, value: str) -> bool:
        """True if ``field`` already renders ``value`` as its suffix value.

        Used to avoid duplicating the value (``性别：男：男``) when the field text
        already ends with it. A *bare* substring match is deliberately NOT enough:
        a one-character value such as ``是`` / ``否`` occurs inside almost any
        Chinese field name (``是否校园大使推荐``), so ``value in field`` wrongly
        erased the current selection and made the control look unset — after
        which selecting it produced no visible change and the LLM retried forever.
        Only the value after a separator counts as "already shown".
        """
        if not field or not value:
            return False
        if field == value:
            return True
        return any(field.endswith(sep + value) for sep in ("：", ":", " ", "、", "|", "·"))

    def _fallback_label(self, node: EnhancedNode, category: str) -> str:
        """Guarantee a non-empty label for an otherwise unnamed interactive element.

        Sources, in order: native choice/file inputs use nearby text or a generic
        word; anything else uses a semantic ``data-*`` / ``class`` token (e.g.
        ``phoenix-calendar-prev-year-btn``); failing that, a category-generic word.
        """
        if self._is_choice_input(node):
            kind = node.attributes.get("type", "").lower()
            return "单选框" if kind == "radio" else "复选框"
        if node.tag == "input" and node.attributes.get("type", "").lower() == "file":
            return "上传文件"
        token = self._attribute_token(node)
        if token:
            return token
        if category == "clickdropdown":
            # A custom dropdown opened by clicking; give a usable word if neither a
            # value nor a field label could name it.
            return "下拉框"
        if category == "click":
            if node.role == "combobox" or node.tag == "select":
                return "下拉框"
            if node.role == "option":
                return "选项"
            if node.role == "button" or node.tag in ("button", "input"):
                return "按钮"
            if node.tag == "a":
                # An animation logo / icon link often has no text. Give the LLM a
                # clue instead of the content-less word "链接": the visible image
                # alt, else the link's host / last path segment.
                image = self._image_alt_label(node)
                if image:
                    return image
                hint = self._href_hint(node)
                return f"链接（{hint}）" if hint else "链接"
            return "可点击项"
        if category == "input":
            return "输入框"
        if category == "searchable":
            return "可搜索下拉"
        if category == "drag":
            return "可拖动"
        if category == "scroll":
            return "可滚动区域"
        return "可交互元素"

    def _attribute_token(self, node: EnhancedNode) -> str:
        """A meaningful label token from ``data-*`` / ``class``, or ``""``.

        Only human-authored-looking tokens (lowercase words joined by ``-``) are
        accepted; obfuscated style hashes (``sc-gIDmLj``) and ids are ignored so
        the caller falls back to a generic label instead of noise.
        """
        for attr in (
            "data-testid",
            "data-test",
            "data-name",
            "data-action",
            "data-tooltip",
            "data-title",
            "data-label",
        ):
            value = node.attributes.get(attr)
            if value:
                return self._truncate(value, 40)
        tokens = []
        for token in node.attributes.get("class", "").split():
            if self._is_semantic_token(token):
                tokens.append(token)
                continue
            stripped = self._strip_framework_namespace(token)
            if (
                stripped != token
                and self._is_semantic_token(stripped)
                and any(hint in stripped for hint in _ACTION_HINTS)
            ):
                # A framework-namespaced but semantically named control class
                # (``ant-picker-header-prev-btn``): strip the namespace so it can
                # name the control instead of the useless generic "按钮". Pure
                # state / utility classes (``ng-untouched``) are still rejected
                # because their remainder carries no action word.
                tokens.append(stripped)
        if not tokens:
            return self._camel_case_action_label(node)
        hinted = [
            token
            for token in tokens
            if any(hint in token for hint in _SEMANTIC_TOKEN_HINTS)
        ]
        best = max(hinted or tokens, key=len)
        parts = best.split("-")
        start = 0
        for index, part in enumerate(parts):
            if part in _ACTION_HINTS:
                start = index
                if index > 0 and parts[index - 1] in _QUALIFIER_HINTS:
                    # Keep a meaningful qualifier so ``super-prev`` and ``prev``
                    # (previous year vs previous month) stay distinguishable.
                    start = index - 1
                break
        trimmed = "-".join(parts[start:])
        nav = self._navigation_label(node, trimmed)
        if nav:
            return nav
        # If one of the (remaining) words is a known icon/action name, prefer its
        # short Chinese label: ``search-submit-icon`` -> ``搜索`` instead of the
        # raw machine token. This runs before the structural rejection so a real
        # action word always wins.
        for part in parts[start:]:
            mapped = _ICON_LABELS.get(part)
            if mapped:
                return mapped
        # A token made only of structural/container words names a place on the
        # page, not a control (``header-logo-link``). Reject it so the caller
        # falls back to a semantic generic word rather than leaking markup. The
        # comparison strips a trailing digit run so framework counters like
        # ``select1`` / ``input1`` are rejected too.
        if self._is_generic_structure_token(trimmed):
            return ""
        return self._truncate(trimmed, 40)

    @staticmethod
    def _split_class_words(token: str) -> list[str]:
        """Split a class token into lowercase words (handles camelCase)."""
        words: list[str] = []
        for part in re.split(r"[-_]", token):
            words.extend(
                re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+", part)
            )
        return [word.lower() for word in words if word]

    def _camel_case_action_label(self, node: EnhancedNode) -> str:
        """A short action label from a camelCase control class, else ``""``.

        Last resort before the generic word: ``delIcon`` / ``closeBtn`` name a
        real control but ``_is_semantic_token`` cannot read them. Decompose each
        class token and map an unambiguous action word (``del`` → 删除) so the
        icon-only control is not exposed as the content-less ``可点击项``.
        """
        for token in node.attributes.get("class", "").split():
            if token.startswith(_FRAMEWORK_CLASS_PREFIXES):
                continue
            words = self._split_class_words(token)
            if not words or len(words) > 4:
                continue
            for word in words:
                mapped = _CAMEL_ACTION_LABELS.get(word)
                if mapped:
                    return mapped
        return ""

    @staticmethod
    def _is_generic_structure_token(token: str) -> bool:
        """True if ``token`` is only structural words (optionally + a counter)."""
        parts = [part for part in token.split("-") if part]
        if not parts:
            return True
        cleaned = [re.sub(r"\d+$", "", part) or part for part in parts]
        return all(part in _GENERIC_STRUCTURE_WORDS for part in cleaned)

    def _navigation_label(self, node: EnhancedNode, trimmed: str) -> str:
        """Translate an icon-only prev/next control's class token, else ``""``.

        Only reached when the control exposes no accessible name / text / title,
        so the class token (``ant-picker-header-prev-btn`` trimmed to
        ``prev-btn``) is the *only* clue. Inside a date/calendar widget the step
        size differs (month vs year); elsewhere it is a generic page step.

        A bare direction arrow (``d-arrow-left`` / ``arrow-right``, the Element Plus
        picker header buttons) is also translated *inside a picker context only*:
        ``d-``/``double`` prefixes step a year and a single arrow a month, so the
        panel's nav reads ``上一年`` / ``上一月`` instead of the bare ``向左``
        (which the model had to reverse-engineer by clicking and diffing the year).
        Outside a picker, ``向左`` / ``向右`` stays as-is.
        """
        raw_parts = [part for part in trimmed.split("-") if part]
        words = [
            part for part in raw_parts if part not in _GENERIC_STRUCTURE_WORDS
        ]
        if not raw_parts:
            return ""
        last = raw_parts[-1]
        classes = node.attributes.get("class", "").lower()
        in_calendar = any(hint in classes for hint in _CALENDAR_CONTEXT_HINTS)
        if last in ("left", "right") and in_calendar:
            # NOTE: "left"/"right" are *generic structure words* and were filtered
            # out of ``words`` — read the direction off ``raw_parts`` instead.
            double = any(part in ("d", "double", "super") for part in raw_parts[:-1])
            if last == "left":
                key = "super-prev" if double else "prev"
            else:
                key = "super-next" if double else "next"
            return _CALENDAR_NAV_LABELS.get(key, "")
        if not words:
            return ""
        last = words[-1]
        if last not in ("prev", "previous", "next"):
            return ""
        key = last
        if len(words) >= 2 and words[-2] in _QUALIFIER_HINTS:
            key = f"{words[-2]}-{last}"
        if in_calendar:
            return _CALENDAR_NAV_LABELS.get(key, "")
        return _GENERAL_NAV_LABELS.get(key, "")

    @staticmethod
    def _is_semantic_token(token: str) -> bool:
        if len(token) < 3 or not token[0].isalpha() or not token[0].islower():
            return False
        # Framework/runtime state & generated-style classes are not names.
        if token.startswith(_FRAMEWORK_CLASS_PREFIXES):
            return False
        # Utility-CSS classes (``px-2`` / ``mt-2`` / ``text-sm``) are styling, not
        # names: leaking one produced the content-less ``<可点击元素>px-2</>``.
        if _is_likely_utility_class(token):
            return False
        return all(ch.islower() or ch.isdigit() or ch == "-" for ch in token)

    @staticmethod
    def _strip_framework_namespace(token: str) -> str:
        """Return ``token`` without a leading framework/CSS-in-JS namespace."""
        for prefix in _FRAMEWORK_CLASS_PREFIXES:
            if token.startswith(prefix):
                return token[len(prefix) :]
        return token

    def _is_choice_input(self, node: EnhancedNode) -> bool:
        """True for a native ``<input type=checkbox|radio>``."""
        return (
            node.tag == "input"
            and node.attributes.get("type", "").lower() in ("checkbox", "radio")
        )

    def _is_redundant_choice_label(self, node: EnhancedNode) -> bool:
        """True when ``node`` is merely the text half of an adjacent choice control.

        Component libraries render a radio / checkbox option in one of two shapes:

        * native — ``<label class="…"><span class="…"><input type=radio></span>
          <span>男</span></label>``;
        * SVG icon — ``<div class="list-item-container"><span class="icon-container">
          <svg class="RadioUnchecked"></span><span class="item-text-label">汉族</span>
          </div>`` (a Beisen ``phoenix-select`` transfer list, observed on the PICC
          recruitment form).

        In both the trailing text span is clickable only because it inherits the
        row's ``cursor:pointer``; the real control is the ``<input>`` / the icon,
        which already drew its own label from that same text. Naming both produced
        duplicate entries (``<可点击元素 e7>男</可点击元素 e7>`` next to
        ``<可点击元素 e8>男</可点击元素 e8>``; ``选择：汉族`` next to ``汉族``),
        which made the LLM click the no-op text half and lose several turns.
        Drop the text half in that case.

        The guard ``is_cursor_pointer_only(node)`` means a real anchor / ARIA
        button / tabindex label is never dropped — only a purely decorative
        ``cursor:pointer`` text span, exactly the shape inherited from the row.
        """
        if not node.is_element or node.tag == "input":
            return False
        # A ``<label>`` that is a *sibling* of a native choice control is the
        # option's visible text; the control already absorbs that same text as
        # its label ("选择：男"), and a browser does not wire a sibling label to
        # the radio without ``for``/``id``. It is not a control itself, so it
        # may be dropped even though it lacks ``cursor:pointer`` (Beisen
        # ``radio_list`` / ``basSelect`` option rows).
        if node.tag != "label" and not is_cursor_pointer_only(node):
            return False
        text = self._collect_text(node)
        if not text:
            return False
        parent = node.parent
        if parent is None:
            return False
        for sibling in parent.children:
            if sibling is node or not sibling.is_element:
                continue
            for choice in self._iter_choice_inputs(sibling):
                # Only drop a ``<label>`` that the sibling control *already*
                # carries as (part of) its own label. Comparing against a fresh
                # nearby-text probe would wrongly drop a *field* label
                # (``性别``) sitting next to a radio whose option text (its AX
                # name) is ``男`` — the probe cannot see the AX name, so both
                # would read as ``性别`` and the label would vanish.
                control_label = self._interactive_label(choice, "click")
                if control_label and text in control_label:
                    return True
            # SVG icon half of the same option row: the icon is the real,
            # separately-callable control (``_interactive_label`` names it
            # ``选择：<text>``); drop the redundant text half too.
            if is_control_icon(sibling):
                paired = self._pointer_sibling_label(sibling) or self._nearby_text_label(sibling)
                if paired and paired == text:
                    return True
        return False

    def _is_redundant_option_label(self, node: EnhancedNode) -> bool:
        """True for a ``<label for=X>`` that merely repeats control ``X``'s label.

        A native radio / checkbox option is authored as
        ``<input type=radio id=X checked><label for=X>国内本硕</label>``. The
        ``<input>`` is named from the option text (its AX name), but the label is
        not itself clickable in that shape, so it was serialized a second time as
        a bare text line — every filter option appeared twice
        (``<可点击元素 e649>国内本硕 [已选]</可点击元素 e649>`` then ``国内本硕``).
        Drop the label; the control already carries the same text.

        Only an **explicit** ``for=<input id>`` association qualifies (a group
        field label such as ``<label for=sex>性别</label>`` is never tied to one
        option), the referenced control must be a rendered native choice control
        or control icon, and the control's own label must actually contain this
        text — so a label that is the only representation of an *unrendered*
        input (or a machine-named input that did not absorb the option text) is
        never dropped.
        """
        if not node.is_element or node.tag != "label":
            return False
        target_id = node.attributes.get("for")
        if not target_id:
            return False
        parent = node.parent
        if parent is None:
            return False
        text = self._collect_text(node)
        if not text:
            return False
        for sibling in parent.children:
            if sibling is node or not sibling.is_element:
                continue
            if sibling.attributes.get("id") != target_id:
                continue
            if not (self._is_choice_input(sibling) or is_control_icon(sibling)):
                continue
            if not sibling.rendered or not sibling.visible:
                continue
            control_label = self._interactive_label(sibling, "click")
            if control_label and text in control_label:
                return True
        return False

    def _consume_nearby_label_source(self, node: EnhancedNode, text: str) -> None:
        """Mark the nearby element showing ``text`` as already rendered.

        A searchable select renders its placeholder / current value in a sibling
        of its typeahead input; ``_nearby_text_label`` folds that text into the
        control's label, and the element would otherwise be serialized again as a
        bare text line (``<可输入元素 eN>研发类：更多</可输入元素 eN>`` followed by
        ``[文本] 更多``). Consume the most specific non-interactive element that
        shows exactly ``text`` within the same bounded ancestor walk the label
        lookup uses, so the text is emitted only once.

        A searchable select's *display value* (Moka ``.sd-Input-display-value``)
        only looks clickable because it inherits the control's ``cursor:pointer``;
        it is a passive part, not a control, so it is consumed too (otherwise the
        value would be emitted twice: once in the label and once as a separate
        ``<可点击元素>``). Genuine interactive siblings are never consumed.
        """
        if not text:
            return
        branch = node
        ancestor = node.parent
        hops = 0
        while ancestor is not None and hops < 3:
            best = self._find_consumable_text_source(ancestor, branch, text)
            if best is not None:
                self._consumed.add(best.node_id)
                return
            branch = ancestor
            ancestor = ancestor.parent
            hops += 1

    def _find_consumable_text_source(
        self, ancestor: EnhancedNode, branch: EnhancedNode, text: str
    ) -> Optional[EnhancedNode]:
        """Nearest element under ``ancestor`` that renders exactly ``text``.

        Two shapes qualify:

        * an element sibling (or the branch's own descendant, for a value nested
          in an *invisible* wrapper) whose collected text equals ``text`` and
          which is not itself a control — the classic sibling placeholder;
        * an element that only *contains* a matching text run inside a zero-area
          wrapper. Element Plus renders a select's current value inside
          ``<div class="el-select__selected-item el-select__input-wrapper is-hidden">
          <input …></div>`` plus a visible ``.el-select__placeholder``; the hidden
          wrapper is skipped by ``_collect_text``, so the placeholder was never
          matched and the value was emitted twice (once inside the control's label,
          once as a bare ``25000 - 50000`` line right after it).
        """
        candidates: list[EnhancedNode] = []
        for child in ancestor.children:
            if not child.is_element or child.hidden or not child.rendered:
                continue
            if child is branch:
                candidates.extend(g for g in child.children if g.is_element)
                continue
            candidates.append(child)
        best: Optional[EnhancedNode] = None
        for sibling in candidates:
            if sibling.node_id in self._consumed or sibling.hidden or not sibling.rendered:
                continue
            # A wrapper that genuinely holds *another* control must not be
            # consumed — except when it is the composite widget's own passive
            # value display (Element Plus ``.el-select__placeholder`` inherits the
            # select's ``cursor:pointer`` and so reports a "click" descendant of
            # itself; it is the control's value, not a second field).
            if self._has_interactive_descendant(sibling) and not (
                self._is_passive_value_part(sibling)
                and self._is_searchable_display_value(sibling)
            ):
                continue
            if classify(sibling) and not self._is_passive_value_part(sibling):
                continue
            hay = self._collect_text(sibling)
            if hay == text or self._contains_text_run(sibling, text):
                if best is None or len(sibling.children) < len(best.children):
                    best = sibling
        return best

    def _contains_text_run(self, node: EnhancedNode, text: str, depth: int = 3) -> bool:
        """True if ``node`` shows ``text`` anywhere in a *passive* subtree.

        Used to consume a value that the control's label already folded in even
        when the matching text sits inside a zero-area wrapper (see
        ``_find_consumable_text_source``). Recursion stops at any nested control
        so a genuine second field is never swallowed.
        """
        if depth <= 0:
            return False
        for child in node.children:
            if child.is_text:
                if " ".join((child.text or "").split()) == text:
                    return True
                continue
            if not child.is_element or child.hidden or not child.rendered:
                continue
            if self._collect_text(child) == text:
                return True
            if classify(child) or self._has_interactive_descendant(child):
                continue
            if self._contains_text_run(child, text, depth - 1):
                return True
        return False

    def _iter_choice_inputs(self, node: EnhancedNode):
        """Yield every native checkbox / radio input in ``node``'s subtree."""
        if self._is_choice_input(node):
            yield node
        for child in node.children:
            if child.is_element:
                yield from self._iter_choice_inputs(child)

    def _selection_state(self, node: EnhancedNode) -> Optional[bool]:
        """Whether ``node`` is a *selected* choice control, else ``None``.

        Needed because a click on a radio / checkbox / option usually changes
        only the control's state, not the page text — without a state marker the
        diff would report “（页面无变化）” and the LLM could not confirm its click.
        Sources, most reliable first:

        1. the accessibility tree's live ``checked`` / ``selected`` / ``pressed``
           property (native inputs and ARIA widgets);
        2. ``aria-checked`` / ``aria-selected`` / ``aria-pressed`` content
           attributes, or a bare native ``checked`` / ``selected`` attribute;
        3. selection state encoded as a CSS class on the control or on one of its
           ancestors (custom components: ``phoenix-radio--checked``,
           ``ant-radio-checked``, ``is-checked`` …).

        Grid *cells* (a calendar / month picker's ``<td>``) are special-cased: a
        cell's ``aria-selected`` is frequently set by the component just to mark
        the *active / on-screen* cell, so ``Element Plus`` marked 9 月 in BOTH year
        panels of an empty month-range picker as selected (two ``[已选]`` months
        while the field held nothing), which sent the model into a long wrong
        deduction about an already-set range. For a cell the claim is only
        believed when a selection *class* corroborates it.

        Only ``True`` is ever rendered; ``None`` / ``False`` mean "no marker".
        """
        if self._is_grid_cell(node):
            if "checked" in node.attributes or "selected" in node.attributes:
                return True
            branch: Optional[EnhancedNode] = node
            hops = 0
            while branch is not None and hops < 4:
                if self._cell_class_has_selection_token(branch):
                    return True
                branch = branch.parent
                hops += 1
            return None
        if node.selected is not None:
            return node.selected
        for attr in ("aria-checked", "aria-selected", "aria-pressed"):
            value = node.attributes.get(attr)
            if value is not None:
                return value.lower() == "true"
        if "checked" in node.attributes or "selected" in node.attributes:
            return True
        branch: Optional[EnhancedNode] = node
        hops = 0
        while branch is not None and hops < 4:
            if self._class_has_selection_token(branch):
                return True
            branch = branch.parent
            hops += 1
        # The control may be a *descendant* of the named element: the Beisen
        # transfer row names ``span.icon-container`` while the checked state
        # lives on the inner ``svg.RadioChecked``. Without this, a click that
        # only flips the icon (no text change) would render no ``[已选]`` marker
        # and the LLM could not confirm its click. Inspect a small, bounded
        # subtree (native ``input[checked]`` or a ``*Checked`` class).
        if self._subtree_has_selection(node, depth=3):
            return True
        return None

    def _subtree_has_selection(self, node: EnhancedNode, depth: int) -> bool:
        if depth < 0:
            return False
        for child in node.children:
            if child.is_text or not child.is_element:
                continue
            if self._is_choice_input(child):
                if "checked" in child.attributes or child.selected is True:
                    return True
            if self._class_has_selection_token(child):
                return True
            if self._subtree_has_selection(child, depth - 1):
                return True
        return False

    @staticmethod
    def _is_grid_cell(node: EnhancedNode) -> bool:
        """True for a table / grid *cell* (a calendar or month picker cell)."""
        if not node.is_element:
            return False
        if node.tag in ("td", "th"):
            return True
        return (node.role or "").lower() in ("gridcell", "cell")

    # Selection *class* tokens used by date / month pickers on their cells
    # (``el-date-table-cell``'s ``current`` / ``start-date`` / ``end-date`` /
    # ``in-range``, ``ant-picker-cell-selected``, ``is-selected`` …). Kept apart
    # from ``_class_has_selection_token`` because a bare ``current`` on a nav /
    # carousel item is not a form selection, while on a *cell* it is.
    _CELL_SELECTION_TOKENS = (
        "current",
        "start-date",
        "end-date",
        "in-range",
        "selected",
        "checked",
    )

    @classmethod
    def _cell_class_has_selection_token(cls, node: EnhancedNode) -> bool:
        """True if a *cell*'s class encodes the committed selection."""
        classes = node.attributes.get("class", "").lower()
        if not classes:
            return False
        for token in classes.split():
            if token in cls._CELL_SELECTION_TOKENS:
                return True
            if token.endswith(("-selected", "-checked", "-current")):
                return True
            if token.endswith("selected") and not token.endswith("unselected"):
                return True
        return False

    @staticmethod
    def _class_has_selection_token(node: EnhancedNode) -> bool:
        """True if a class token encodes a selected/checked state.

        Covers separated suffixes (``phoenix-radio--checked``, ``is-checked``,
        ``ant-radio-checked``) and concatenated/camelCase component classes
        (``RadioChecked``, ``CheckboxChecked``), but never the negative
        lookalikes ``unchecked`` / ``unselected`` (or ``checkbox``).
        """
        classes = node.attributes.get("class", "").lower()
        if not classes:
            return False
        for token in classes.split():
            if token in ("checked", "selected"):
                return True
            if token.endswith(("-checked", "-selected")):
                return True
            if token.endswith("checked") and not token.endswith("unchecked"):
                return True
            if token.endswith("selected") and not token.endswith("unselected"):
                return True
        return False

    def _pointer_sibling_label(self, node: EnhancedNode) -> str:
        """The text of the nearest ``cursor:pointer`` labeled sibling of ``node``."""
        parent = node.parent
        if parent is None:
            return ""
        for sibling in parent.children:
            if sibling is node or not sibling.is_element:
                continue
            if sibling.hidden or not sibling.visible or not sibling.in_viewport:
                continue
            if sibling.styles.get("cursor") != "pointer":
                continue
            text = self._collect_text(sibling)
            if text:
                return text
        return ""

    def _is_helper_text(self, node: EnhancedNode) -> bool:
        """True if ``node`` is a helper / error / hint text, not a value or label."""
        raw = (
            f"{node.attributes.get('class', '')} {node.attributes.get('id', '')}"
        ).lower()
        return any(tok in raw for tok in _HELPER_TEXT_TOKENS)

    def _is_passive_value_part(self, node: EnhancedNode) -> bool:
        """True for a non-control text part of a composite widget (its display value).

        Used to allow consuming the visible value of a searchable select
        (``.sd-Input-display-value`` / ``.ant-select-selection-item``) which is
        ``cursor:pointer`` *only because it inherits the control's pointer cursor*,
        never a control of its own. A genuine clickable (native tag / ARIA role /
        inline handler / ``tabindex`` / ``href``) is never consumed.
        """
        if node.tag in ("a", "button", "select", "textarea", "input", "summary", "option"):
            return False
        if (node.role or "").lower() in CLICKABLE_ROLES:
            return False
        if has_inline_click_handler(node):
            return False
        if node.attributes.get("href") is not None:
            return False
        tabindex = node.attributes.get("tabindex")
        if tabindex is not None and tabindex.isdigit() and int(tabindex) >= 0:
            return False
        return True

    def _is_searchable_display_value(self, node: EnhancedNode) -> bool:
        """True if ``node`` is the passive value display of a searchable select.

        Its text equals the ``_nearby_text_label`` of a searchable ``<input>``
        sibling (Moka renders the committed value in ``.sd-Input-display-value``
        next to the filter input). Such a node must not be serialized as a
        separate clickable, or the value appears twice.
        """
        if not self._is_passive_value_part(node):
            return False
        parent = node.parent
        if parent is None:
            return False
        text = self._collect_text(node)
        if not text:
            return False
        for sibling in parent.children:
            if sibling is node or not sibling.is_element:
                continue
            for candidate in self._iter_searchable_inputs(sibling, depth=2):
                if self._searchable_value_text(candidate) == text:
                    return True
        return False

    def _iter_searchable_inputs(self, node: EnhancedNode, depth: int = 2):
        """Yield editable filter inputs of a composite select inside ``node``.

        The filter input is often nested one level deeper than the value display
        (Element Plus renders ``.el-select__selection > .el-select__input-wrapper >
        input.el-select__input`` next to ``.el-select__placeholder``), so the
        display-value check must look through small wrappers instead of only at
        direct ``<input>`` siblings.
        """
        if node.tag == "input":
            if is_custom_select_input(node) or self._is_searchable_typeahead(node):
                yield node
            return
        if depth <= 0:
            return
        for child in node.children:
            if child.is_element:
                yield from self._iter_searchable_inputs(child, depth - 1)

    def _nearby_text_label(self, node: EnhancedNode, max_hops: int = 3) -> str:
        """Nearest short label text on a sibling of ``node`` or of its ancestors.

        Native controls often carry their visible label in a *wrapper's* sibling
        (e.g. ``<div class="phoenix-checkbox"><span><input type=checkbox></span>
        <span class="...text">至今</span></div>``): the text is not a sibling of
        the control itself but of the wrapper around it. Walk up a few levels and
        take the shortest non-empty sibling text found at the first level that has
        any.
        """
        branch = node
        ancestor = node.parent
        hops = 0
        while ancestor is not None and hops < max_hops:
            siblings = list(ancestor.children)
            index = siblings.index(branch) if branch in siblings else -1
            ordered = (
                siblings[index + 1 :] + siblings[:index] if index >= 0 else siblings
            )
            texts: list[str] = []
            for sibling in ordered:
                if sibling is branch or not sibling.is_element:
                    continue
                if sibling.hidden or not sibling.visible or not sibling.in_viewport:
                    continue
                text = self._collect_text(sibling)
                if text:
                    texts.append(text)
            if texts:
                return self._truncate(min(texts, key=len), 40)
            branch = ancestor
            ancestor = ancestor.parent
            hops += 1
        return ""

    def _adjacent_text_label(self, node: EnhancedNode) -> str:
        """Visible text of the text node immediately before/after ``node``.

        Native radio / checkbox rows are frequently authored as
        ``<input type=radio>男<input type=radio>女`` — the option text is a bare
        ``#text`` sibling, not an element, so ``_pointer_sibling_label`` /
        ``_nearby_text_label`` (which only look at *element* siblings) miss it and
        the control was named after its internal ``name`` id instead. Look at the
        nearest sibling text node, first after then before.
        """
        parent = node.parent
        if parent is None:
            return ""
        try:
            index = parent.children.index(node)
        except ValueError:
            return ""
        for sibling in parent.children[index + 1 :]:
            if sibling.is_text:
                text = " ".join((sibling.text or "").split())
                if text:
                    return self._truncate(text, 40)
                continue
            break
        for sibling in reversed(parent.children[:index]):
            if sibling.is_text:
                text = " ".join((sibling.text or "").split())
                if text:
                    return self._truncate(text, 40)
                continue
            break
        return ""

    def _selected_option_label(self, node: EnhancedNode) -> str:
        """Text of a native ``<select>``'s currently selected option, else ``""``.

        A closed ``<select>`` only renders the selected option; its other options
        have no box and are not serialized. Name the control after that value so
        the LLM can tell the field's current state (and avoid re-selecting it).
        A placeholder option (empty ``value``) means no real choice yet, so it is
        ignored and the caller falls back to the field label.
        """
        options = [
            child
            for child in node.children
            if child.is_element and child.tag == "option"
        ]
        if not options:
            return ""
        chosen = None
        for option in options:
            if option.selected is True or "selected" in option.attributes:
                chosen = option
                break
        if chosen is None:
            chosen = options[0]
        if not (chosen.attributes.get("value") or "").strip():
            return ""
        text = self._collect_text(chosen) or chosen.attributes.get("title", "")
        return self._truncate(text, 40)

    def _select_label(self, node: EnhancedNode) -> str:
        """A native ``<select>``'s label: ``字段名：当前值（或占位/未选择）``.

        A native select often has no AX name and no visible text of its own
        (the non-selected ``<option>``s have no box), so the old chain fell
        through to the CSS class (``select1``). Recover the *field* name from a
        nearby label / preceding sibling cell and pair it with the current
        selection (or the placeholder when nothing is chosen yet), so the model
        can both identify the field and see whether it still needs filling.
        """
        field = ""
        if node.ax_name and not self._is_machine_token(node.ax_name):
            field = self._truncate(node.ax_name)
        if not field:
            field = self._associated_field_label(node)
        if not field:
            field = self._preceding_field_label(node)
        field = self._clean_field_label(field)
        selected = self._selected_option_label(node)
        if selected:
            if field and field not in selected:
                return f"{field}：{selected}"
            return selected
        marker = self._select_placeholder(node) or "未选择"
        if field:
            return f"{field}：{marker}"
        return marker

    def _select_placeholder(self, node: EnhancedNode) -> str:
        """Text of a native ``<select>``'s empty-value placeholder option, or ``""``."""
        for child in node.children:
            if not child.is_element or child.tag != "option":
                continue
            if (child.attributes.get("value") or "").strip():
                # The first option with a value is the browser's default
                # selection, so there is no separate placeholder.
                return ""
            text = self._collect_text(child) or child.attributes.get("title", "")
            return self._truncate(text, 40) if text else ""
        return ""

    def _preceding_field_label(self, node: EnhancedNode) -> str:
        """Field name from the nearest preceding element sibling of any ancestor.

        Table / row based forms place the label in the cell *before* the
        control's cell (``<tr><td title="政治面貌">政治面貌*</td>
        <td><select>…</select></td></tr>``). The control's own siblings are the
        (empty) value cell, so the search walks up to a few ancestors and takes
        the nearest *preceding* element that is not itself a control. This is a
        last resort, only reached by native selects that have no AX name and no
        associated ``<label>``; the ancestor depth is capped so it cannot steal
        an unrelated section's text.
        """
        branch = node
        ancestor = node.parent
        hops = 0
        while ancestor is not None and ancestor.is_element and hops < 4:
            try:
                branch_index = ancestor.children.index(branch)
            except ValueError:
                return ""
            for sibling in reversed(ancestor.children[:branch_index]):
                if not sibling.is_element:
                    continue
                if sibling.hidden or not sibling.visible:
                    continue
                if classify(sibling):
                    continue
                for attr in ("title", "aria-label"):
                    value = sibling.attributes.get(attr)
                    if value and not self._is_machine_token(value):
                        return self._clean_field_label(self._truncate(value, 40))
                text = self._collect_text(sibling)
                if text:
                    return self._clean_field_label(self._truncate(text, 40))
            branch = ancestor
            ancestor = ancestor.parent
            hops += 1
        return ""

    def _is_widget_mirror(self, node: EnhancedNode) -> bool:
        """True for a component library's text-size *mirror* helper.

        Ant Design / ATSX render a width-measuring ``<span>`` beside a searchable
        select's typeahead (``.ant-select-selection-search-mirror`` /
        ``.atsx-select-search__field__mirror``) whose text duplicates the input's
        own value. It is pure layout scaffolding — never user content — but it
        leaked a second bare text line and looked like a separate suggestion.
        Matched by the ``mirror`` class token only on a non-interactive element,
        so a real control whose class happens to contain the word is untouched.
        """
        if not node.is_element:
            return False
        classes = node.attributes.get("class", "")
        if not classes or "mirror" not in classes.lower():
            return False
        for token in classes.split():
            low = token.lower()
            if low.endswith("mirror") or "-mirror" in low or "_mirror" in low:
                return not self._has_interactive_descendant(node)
        return False

    def _has_field_input_descendant(self, node: EnhancedNode) -> bool:
        """True if ``node`` wraps a form field (input / textarea / select)."""
        for child in node.children:
            if child.is_text or not child.is_element:
                continue
            if child.tag in ("input", "textarea", "select"):
                return True
            if child.attributes.get("contenteditable") in ("", "true", "plaintext-only"):
                return True
            if self._has_field_input_descendant(child):
                return True
        return False

    @staticmethod
    def _input_is_widget_helper(node: EnhancedNode) -> bool:
        """True for a text ``<input>`` that is a *widget's own* passive part.

        A ``readonly`` / ``hidden`` input is never something the model should
        address directly (a date picker's text part, a non-searchable select's
        hidden typeahead) — the surrounding control owns the interaction.

        Note the deliberate contrast with ``_is_searchable_typeahead``: an
        *editable* ``role=combobox`` / ``aria-autocomplete`` input is the
        opposite — it is a real search box the user types into to filter
        candidates (Ant Design's ``show-search`` select, a native autocomplete),
        so it must be exposed, not hidden. Only ``readonly`` distinguishes the
        two (verified on the live page: non-searchable selects carry
        ``readonly``; searchable ones do not).
        """
        itype = node.attributes.get("type", "text").lower()
        if "readonly" in node.attributes:
            return True
        if itype == "hidden":
            return True
        return False

    @staticmethod
    def _is_searchable_typeahead(node: EnhancedNode) -> bool:
        """True for an *editable* combobox / autocomplete input.

        Thin delegate to :func:`browser_agent.dom.classify.is_searchable_typeahead`
        so the predicate has a single source of truth: ``classify`` uses it to
        assign the ``searchable`` category, and the serializer uses it to decide
        which input is a searchable select's filter box (exposed as
        ``<可搜索下拉元素>`` and driven by ``tool_07``).
        """
        return is_searchable_typeahead(node)

    def _has_label_element_descendant(self, node: EnhancedNode) -> bool:
        """True if ``node`` is, or wraps, a ``<label>`` element.

        A clickable node that contains a field ``<label>`` is a form row (or a
        native ``<label>`` control wrapper), never the field control itself.
        """
        if node.is_element and node.tag == "label":
            return True
        for child in node.children:
            if child.is_element and self._has_label_element_descendant(child):
                return True
        return False

    def _is_textual_click_only(self, node: EnhancedNode) -> bool:
        """True for a pointer-cursor element that is really a descriptive text run.

        Heuristic for form-row helper notes (``.ant-form-item-extra``): the page
        set ``cursor:pointer`` on a container, every descendant inherits it, and
        a plain note ends up looking clickable. Such a node has no *strong*
        control descendant, no icon, and only a long text run — it is not a
        control. Short ``cursor:pointer`` labels (real text buttons such as
        ``搜索职位``) stay clickable.
        """
        if self._is_popup_option(node) or self._has_popup_option_descendant(node):
            # A dropdown / menu entry is a real control, however terse its text
            # ("TOP5%"); a wrapper holding such entries (the nested option list of
            # an Ant Design select) must be pruned, not demoted to text. Demoting
            # either made the option list unaddressable.
            return False
        if has_inline_click_handler(node):
            # A real inline click handler is not "descriptive copy": the page
            # author bound the action here (e.g. a My97 calendar day cell
            # ``<td onclick="day_Click(...)">``). Demoting it to plain text
            # silently deleted every day from the calendar's interactable grid.
            return False
        if self._has_strong_descendant(node):
            return False
        if has_svg_descendant(node):
            return False
        if self._has_image_descendant(node):
            # A file/attachment row (icon + name) inside a dropzone is a real
            # (preview/remove) control, not descriptive copy.
            return False
        if self._has_blockish_descendant(node):
            # A node with real block-level structure (e.g. a job card made of a
            # title row + a label row) is a *structured container*, not a plain
            # text run. The old length-only test demoted any long card whose
            # label happened to exceed 60 chars (multi-city job rows) to plain
            # text, so structurally identical rows became inconsistently
            # un-clickable. Structure always wins over the length heuristic.
            return False
        if len(self._collect_text(node)) >= 60:
            return True
        # A short ``cursor:pointer`` text node that sits inside a clickable
        # container which already owns a real control (an upload dropzone's
        # "支持…格式…" hint next to its button) is descriptive copy, not a
        # control: naming it produced a no-op ``<可点击元素>``.
        return is_cursor_pointer_only(node) and self._ancestor_owns_control(node)

    def _is_contentless_clickable(self, node: EnhancedNode, label: str) -> bool:
        """True for a clickable whose only name is a generic filler.

        Used to drop criterion-i noise (an empty calendar/tree cell or a
        decorative loading spinner) instead of emitting
        ``<可点击元素 eN>可点击项</>``. Only fires when the label is one of the
        known filler words *and* the node exposes no other human content
        (text / cleaned accessible name / title / href), so a real (if
        unnamed) control is never silently removed.
        """
        text = (label or "").strip()
        if text.endswith(" [已选]"):
            text = text[: -len(" [已选]")].strip()
        if text.lower() not in _NON_ACTIONABLE_LABELS:
            return False
        if self._collect_text(node):
            return False
        accessible = _clean_icon_accessible_name(node.ax_name or "").strip().lower()
        if accessible and accessible not in _NON_ACTIONABLE_LABELS:
            return False
        if node.attributes.get("title") or node.attributes.get("href"):
            return False
        return True

    def _has_image_descendant(self, node: EnhancedNode) -> bool:
        for child in node.children:
            if child.is_text:
                continue
            if self._is_image(child):
                return True
            if child.is_element and self._has_image_descendant(child):
                return True
        return False

    def _ancestor_owns_control(self, node: EnhancedNode, max_hops: int = 6) -> bool:
        """True if a clickable ancestor of ``node`` already contains a real control."""
        current = node.parent
        hops = 0
        while current is not None and hops < max_hops:
            if (
                current.is_element
                and _is_click_like(classify(current))
                and self._has_separate_interactive_descendant(current)
            ):
                return True
            current = current.parent
            hops += 1
        return False

    def _has_strong_descendant(self, node: EnhancedNode) -> bool:
        """True if ``node`` wraps a descendant with an intrinsic control signal.

        Unlike a mere inherited ``cursor:pointer``, these signals (tag, ARIA
        role, ``tabindex``, ``contenteditable``) make a descendant a real,
        separately-addressable control.
        """
        for child in node.children:
            if child.is_text or not child.is_element:
                continue
            if child.tag in ("a", "button", "select", "textarea", "input", "summary", "option"):
                return True
            if child.role in CLICKABLE_ROLES or child.role in ("textbox", "searchbox", "spinbutton"):
                return True
            if child.attributes.get("tabindex") is not None:
                return True
            if child.attributes.get("contenteditable") in ("", "true", "plaintext-only"):
                return True
            if self._has_strong_descendant(child):
                return True
        return False

    def _associated_field_label(self, node: EnhancedNode) -> str:
        """Find the field label associated with a form control.

        Component libraries usually wrap a control as
        ``<div class="form-item"><div class="...label">政治面貌</div>
        <div class="...control">…control…</div></div>``.

        Two passes, both bounded, ordered from the most to the least reliable:

        1. **the nearest enclosing field container** — the closest ancestor that
           holds *both* a label element and this control. Its own label is the
           field name (Element Plus ``.el-form-item``, Ant ``.ant-form-item``,
           Beisen/Moka ``.form-item`` …). This must win: while climbing further
           the walk eventually reaches a *row* container whose siblings are other
           fields, and the first label found there names the control after the
           neighbouring field (the 4399 简历 form labelled 期望薪酬 as
           ``生源地：月薪``).
        2. **the original sibling walk** — kept as a fallback for the layouts
           where the label is not inside a shared container with the control
           (label rendered in a separate column / portal). Unchanged semantics so
           no already-working site regresses.
        """
        branch = node
        ancestor = node.parent
        chain: list[tuple[EnhancedNode, EnhancedNode]] = []
        hops = 0
        # 12 hops: Feishu nests a select's search input as
        # ``wrap > search > div > rendered > selection > select > children >
        # control > col > row``, so the field's own row (which holds the label) sits
        # beyond the old 8-hop cap and the control was left without a field name.
        while ancestor is not None and hops < 12:
            chain.append((ancestor, branch))
            branch = ancestor
            ancestor = ancestor.parent
            hops += 1

        # A control's *own* widget shell is not its field name: Feishu's period
        # picker renders each end as ``<div class="…-period-month-label">2017-09
        # </div>``, which ``_is_label_like`` accepted (its class contains the token
        # "label"), so the control was named after its own value. Keep every
        # candidate inside that shell out of the search.
        skip_root = self._widget_shell_of(node)

        # Pass 1: the nearest enclosing container that owns a label element.
        for container, child in chain:
            text = self._label_beside(container, child, skip_root)
            if text:
                return self._clean_field_label(self._truncate(text, 40))

        # Pass 2: historical sibling walk (kept for layouts where the label is not
        # inside a shared container with the control).
        for container, child in chain:
            text = self._label_beside(container, child, skip_root)
            if text:
                return self._clean_field_label(self._truncate(text, 40))
        return ""

    def _label_beside(
        self,
        container: EnhancedNode,
        branch: EnhancedNode,
        skip_root: Optional[EnhancedNode],
    ) -> str:
        """``branch``'s own field label among ``container``'s children, else ``""``.

        A label **before** the control wins over one after it, and among those the
        *nearest* (last) one wins. A row that holds several fields renders
        ``label, control, label, control``; taking the first label in document
        order named a control after its *neighbour* field — the 4399 简历 form
        labelled 期望薪酬 as ``生源地：月薪``. The closest preceding label is the
        field's own.
        """
        seen_branch = False
        nearest_before = ""
        first_after = ""
        for candidate in container.children:
            if candidate is branch:
                seen_branch = True
                continue
            if not candidate.is_element:
                continue
            text = self._find_label_text(candidate, skip_root=skip_root)
            if not text:
                continue
            if not seen_branch:
                nearest_before = text
            elif not first_after:
                first_after = text
        return nearest_before or first_after

    def _widget_shell_of(self, node: EnhancedNode):
        """The *picker* shell that owns ``node`` (nearest ancestor), else ``None``.

        Deliberately strict: only an ancestor whose own class/id spells a known
        picker component counts. A loose "date + container" match would treat a
        form row such as ``date_field_component`` as the shell and then swallow
        that row's own field label.
        """
        current = node.parent
        hops = 0
        while current is not None and hops < 4:
            if current.is_element and _is_named_picker_shell(current):
                return current
            current = current.parent
            hops += 1
        return None

    @staticmethod
    def _within(node: EnhancedNode, ancestor: Optional[EnhancedNode]) -> bool:
        """True if ``node`` is ``ancestor`` or one of its descendants."""
        if ancestor is None:
            return False
        current: Optional[EnhancedNode] = node
        hops = 0
        while current is not None and hops < 64:
            if current is ancestor:
                return True
            current = current.parent
            hops += 1
        return False

    def _inner_label_text(self, node: EnhancedNode, max_depth: int = 2) -> str:
        """The text of a *real* ``<label>`` inside ``node``, else ``""``.

        A field-name column often carries extra hint copy beside the ``<label>``
        (Feishu's 「起止时间」 row appends 「无准确的毕业时间可填写预计毕业时间」);
        preferring the inner ``<label>`` keeps that hint out of the field name.
        Bounded so it can never reach a *neighbouring* field's label.
        """
        if node.tag == "label":
            return self._collect_text(node)
        if max_depth <= 0:
            return ""
        for child in node.children:
            if child.is_element:
                text = self._inner_label_text(child, max_depth - 1)
                if text:
                    return text
        return ""

    @staticmethod
    def _is_fields_container(node: EnhancedNode) -> bool:
        """True for a list-of-fields container (shared box holding many fields)."""
        raw = f"{node.attributes.get('class', '')} {node.attributes.get('id', '')}".lower()
        return any(
            tok in raw
            for tok in ("fields", "fields-col", "fields-row", "form-fields", "field-list")
        )

    def _find_title_text(self, node: EnhancedNode, max_depth: int = 3) -> str:
        """The first field-*title* element's text within ``max_depth`` levels.

        A field title is ``<div class="…title…"><span>字段名</span></div>`` (Moka
        ``.title-…``); unlike ``<label>`` it is not a form association, so it is
        used only as a fallback when no ``<label>`` names the field. Helper / error
        nodes never qualify.
        """
        if not node.is_element or self._is_helper_text(node):
            return ""
        raw = f"{node.attributes.get('class', '')} {node.attributes.get('id', '')}".lower()
        if "title" in raw:
            text = self._collect_text(node)
            if text:
                return text
        if max_depth <= 0:
            return ""
        for child in node.children:
            if child.is_element:
                text = self._find_title_text(child, max_depth - 1)
                if text:
                    return text
        return ""

    # Class / id tokens that mark a *label-like* element. Modern component
    # libraries (Element Plus ``.el-form-item__label``, Ant ``.ant-form-item-label``,
    # ``.form-item__label``, ``.field-label`` …) render the field name in a plain
    # ``<div>``, not in a ``<label>``. Without this, ``_find_label_text`` skipped
    # the field's own label element and kept climbing until it hit some *other*
    # field's ``<label>`` — which is how the 4399 简历 form labelled the 期望薪酬
    # dropdown as ``生源地：月薪`` (the neighbouring field's name). Recognising the
    # label-like element makes the search stop at the right level.
    _LABEL_CLASS_TOKENS = ("label", "title", "caption", "legend")

    def _is_label_like(self, node: EnhancedNode) -> bool:
        if not node.is_element:
            return False
        if node.tag in ("label", "legend", "caption"):
            return True
        raw = f"{node.attributes.get('class', '')} {node.attributes.get('id', '')}".lower()
        if not raw.strip():
            return False
        for token in re.split(r"[^a-z0-9]+", raw):
            if token in self._LABEL_CLASS_TOKENS:
                return True
        return False

    def _find_label_text(
        self,
        node: EnhancedNode,
        max_depth: int = 3,
        skip_root: Optional[EnhancedNode] = None,
    ) -> str:
        """The first label element's text within ``max_depth`` levels of ``node``.

        A label element is a real ``<label>``/``<legend>``/``<caption>`` or an
        element whose class/id marks it as a label (``…__label`` / ``…-label`` /
        ``…label…``) — see ``_LABEL_CLASS_TOKENS``.

        The depth cap matters: a field label always sits in a shallow "label
        column" (``<div class="...label"><label>姓名</label></div>``). Without it,
        walking up to a large shared ancestor makes the search dive into an
        unrelated sibling section and steal its label — a partially scrolled
        upload container was named ``姓名：请上传您的简历…`` because the search
        reached the deep ``<label>姓名</label>`` inside the sibling basic-info
        form.
        """
        if skip_root is not None and node is not skip_root and self._within(
            node, skip_root
        ):
            # Inside the control's own widget shell: this is value furniture, not
            # a field name (see ``_associated_field_label``).
            return ""
        if self._is_label_like(node):
            text = self._inner_label_text(node) or self._collect_text(node)
            # A control-wrapper label (one that *contains* the input) is not a
            # field name; its text is the control's own value/placeholder.
            if text and not self._has_field_input_descendant(node):
                return text
            if node.tag == "label":
                return ""
        if max_depth <= 0:
            return ""
        for child in node.children:
            if child.is_element:
                text = self._find_label_text(child, max_depth - 1, skip_root)
                if text:
                    return text
        return ""

    def _find_field_label_candidate(self, node: EnhancedNode, max_depth: int = 3) -> str:
        """Like ``_find_label_text`` but for a *custom select filter input*.

        Skips a control-wrapper label (one that wraps the actual ``<input>`` —
        returning its text names the field after a *sibling field's* value) and
        helper / error labels. Accepts the same label elements as
        ``_find_label_text`` (real ``<label>`` plus class-marked label divs).
        """
        if self._is_label_like(node):
            if self._is_helper_text(node) or self._has_field_input_descendant(node):
                return ""
            return self._collect_text(node)
        if max_depth <= 0:
            return ""
        for child in node.children:
            if child.is_element:
                text = self._find_field_label_candidate(child, max_depth - 1)
                if text:
                    return text
        return ""

    def _searchable_field_label(self, node: EnhancedNode) -> str:
        """Field name for a *custom select filter input* (Moka ``sd-Select``).

        Same walk as ``_associated_field_label`` but: stops before a shared
        *fields list* container (a sibling there is a different field), accepts a
        ``…title…`` element as a fallback for the field name, and skips the
        control's own wrapper label.
        """
        branch = node
        ancestor = node.parent
        hops = 0
        while ancestor is not None and hops < 8:
            if ancestor.is_element and self._is_fields_container(ancestor):
                break
            for sibling in ancestor.children:
                if sibling is branch or not sibling.is_element:
                    continue
                text = self._find_field_label_candidate(sibling) or self._find_title_text(
                    sibling
                )
                if text:
                    return self._clean_field_label(self._truncate(text, 40))
            branch = ancestor
            ancestor = ancestor.parent
            hops += 1
        return ""

    def _searchable_value_text(self, node: EnhancedNode, max_hops: int = 3) -> str:
        """Current-value text for a *custom select filter input*.

        Same as ``_nearby_text_label`` but skips helper / validation text
        (``必填项未填写``), which a custom select renders right beside its input.
        """
        branch = node
        ancestor = node.parent
        hops = 0
        while ancestor is not None and hops < max_hops:
            siblings = list(ancestor.children)
            index = siblings.index(branch) if branch in siblings else -1
            ordered = (
                siblings[index + 1 :] + siblings[:index] if index >= 0 else siblings
            )
            texts: list[str] = []
            for sibling in ordered:
                if sibling is branch or not sibling.is_element:
                    continue
                if sibling.hidden or not sibling.visible or not sibling.in_viewport:
                    continue
                if self._is_helper_text(sibling):
                    continue
                text = self._collect_text(sibling)
                if text:
                    texts.append(text)
            if texts:
                return self._truncate(min(texts, key=len), 40)
            branch = ancestor
            ancestor = ancestor.parent
            hops += 1
        return ""

    def _interactive_text(
        self, node: EnhancedNode, category: str, name: str
    ) -> str:
        tag, _ = CATEGORY_TAGS[category]
        label = self._interactive_label(node, category)
        return f"<{tag} {name}>{label}</{tag} {name}>"

    def _input_value(self, node: EnhancedNode) -> str:
        if node.attributes.get("type", "").lower() == "password":
            return ""
        value = node.input_value or node.attributes.get("value", "")
        # A ``<textarea>`` can hold a long document; keep the inline label short
        # while still proving the fill landed.
        return self._truncate(value, 120) if value else ""

    def _empty_input_label(self, node: EnhancedNode) -> str:
        """A label for an empty field that cannot be mistaken for a value.

        The placeholder is *hint* text the page shows while empty; rendering it
        bare (``<可输入元素 e53>结束日期</…>``) made the model think the field held
        that text. Prefix an explicit emptiness marker and keep the placeholder
        only as a clearly-labelled hint.
        """
        placeholder = " ".join((node.attributes.get("placeholder") or "").split())
        if placeholder:
            return f"（空，占位提示：{self._truncate(placeholder, 40)}）"
        return "（空）"

    def _label(self, node: EnhancedNode) -> str:
        # A native ``<select>`` shows its *selected option*, not its internal id.
        # Rendering the id (``11_150051_1``) told the LLM nothing about the current
        # value (and made it unable to tell whether a choice was already made).
        if node.tag == "select":
            selected = self._selected_option_label(node)
            if selected:
                field = self._truncate(node.ax_name) if node.ax_name else ""
                if field and field not in selected and not self._is_machine_token(field):
                    return f"{field}：{selected}"
                return selected
        if node.ax_name:
            ax_name = _clean_icon_accessible_name(node.ax_name)
            if ax_name:
                return self._icon_label(ax_name) or self._truncate(ax_name)
        # A button-like ``<input>`` shows its action text in ``value`` ("清空" /
        # "今天" / "确定"). The generic attribute loop below deliberately omits
        # ``value`` (it is an input's current text everywhere else), so these
        # controls were exposed as the content-less "按钮" even though the page
        # author named them.
        if node.tag == "input" and node.attributes.get("type", "").lower() in (
            "button",
            "submit",
            "reset",
        ):
            value = node.attributes.get("value")
            if value:
                return self._icon_label(value) or self._truncate(value)
        text = self._collect_text(node)
        if text:
            # A visible glyph-only label (``X`` / ``×`` / ``Close``) is an icon in
            # disguise: map it to an actionable word so the entry is not read as a
            # meaningless "X" button.
            return self._icon_label(text) or text
        for attr in ("placeholder", "title", "aria-label", "alt", "name"):
            value = node.attributes.get(attr)
            if value:
                if attr == "alt":
                    value = _clean_alt_text(value)
                    if not value:
                        continue
                label = self._icon_label(value) or self._truncate(value)
                if attr == "name" and (
                    self._is_machine_token(label) or self._is_choice_input(node)
                ):
                    # ``name`` is the page's internal field id (``11_150051_1``),
                    # never a human label. Leaking it produced meaningless tags.
                    # For a radio / checkbox the ``name`` is the *group* id
                    # (``cateItems``, ``RecruitmentPortalPersonProfile_gender``),
                    # identical for every option — using it as the option label
                    # is always wrong; the option text comes from the nearby
                    # ``<label>`` (see ``_interactive_label``).
                    continue
                return label
        # A label-less wrapper around a single icon (Ant Design's
        # ``.ant-picker-suffix`` holding ``<span aria-label="calendar">``): take
        # the *descendant* icon's name so the entry reads ``打开日历`` instead of
        # the empty fallback word ``可点击项``.
        icon = self._descendant_icon_label(node)
        if icon:
            return icon
        return ""

    def _descendant_icon_label(self, node: EnhancedNode, max_depth: int = 3) -> str:
        if max_depth <= 0:
            return ""
        for child in node.children:
            if child.is_text or not child.is_element:
                continue
            mapped = self._icon_label(_clean_icon_accessible_name(child.ax_name or ""))
            if mapped:
                return mapped
            for attr in ("aria-label", "title"):
                value = child.attributes.get(attr)
                if value:
                    mapped = self._icon_label(_clean_icon_accessible_name(value))
                    if mapped:
                        return mapped
            deeper = self._descendant_icon_label(child, max_depth - 1)
            if deeper:
                return deeper
        return ""

    def _image_alt_label(self, node: EnhancedNode, max_depth: int = 3) -> str:
        """A cleaned ``alt``/AX name of an image descendant, else ``""``.

        Used to name an otherwise anonymous image link (``<a><img alt="首页"></a>``)
        instead of the content-less fallback word "链接".
        """
        if max_depth <= 0:
            return ""
        for child in node.children:
            if child.is_text or not child.is_element:
                continue
            if self._is_image(child):
                alt = _clean_alt_text(child.attributes.get("alt") or child.ax_name or "")
                if alt:
                    return self._truncate(alt, 40)
            deeper = self._image_alt_label(child, max_depth - 1)
            if deeper:
                return deeper
        return ""

    @staticmethod
    def _href_hint(node: EnhancedNode) -> str:
        """A short human hint from an ``<a>``'s ``href`` (host or last path part)."""
        href = (node.attributes.get("href") or "").strip()
        if not href or href.lower().startswith(
            ("javascript:", "#", "mailto:", "tel:", "sms:", "blob:")
        ):
            return ""
        if "://" in href:
            rest = href.split("://", 1)[1]
            host = rest.split("/", 1)[0].split("?", 1)[0]
            return host[4:] if host.lower().startswith("www.") else host
        parts = [p for p in href.split("?")[0].split("/") if p]
        return parts[-1][:40] if parts else ""

    @staticmethod
    def _icon_label(value: str) -> str:
        """Map a bare icon name (``calendar`` / ``close-circle``) to Chinese.

        Icon-only controls expose their *icon* name as the accessible name; the
        mapping turns it into something the LLM can act on.
        """
        return _ICON_LABELS.get((value or "").strip().lower(), "")

    def _collect_text(self, node: EnhancedNode) -> str:
        parts: list[tuple[str, bool]] = []
        for child in node.children:
            if child.is_text:
                parts.append((child.text or "", False))
            elif child.is_element and not self._is_image(child):
                # Never aggregate text from hidden / box-less subtrees: a
                # closed dropdown's option text, hidden SEO copy, etc. otherwise
                # leaked into the label of the *visible* control that wraps them
                # (e.g. a country ``<select>`` named "中国 +86 美国 +1 …").
                if child.hidden or not child.rendered:
                    continue
                # An *open* floating overlay nested inside its trigger (Ant Design
                # renders the dropdown inside the select when the page overrides
                # ``getPopupContainer``) is not part of the trigger's own label;
                # folding its entries in produced the unreadable
                # ``成绩排名：请选择 TOP5% TOP10% TOP20% …`` blob.
                if self._is_overlay_container(child):
                    continue
                nested = self._collect_text(child)
                if nested:
                    parts.append((nested, self._is_block_level(child)))
        # Composite controls often repeat the same text in several sibling
        # nodes (e.g. a select renders its value in a calc/placeholder/tip
        # span). Collapse adjacent duplicates so the label stays readable.
        deduped: list[tuple[str, bool]] = []
        for text, is_block in parts:
            if not text:
                continue
            if deduped and text == deduped[-1][0]:
                continue
            deduped.append((text, is_block))
        # Join the way a browser lays text out, *not* with an unconditional
        # space: adjacent inline runs (``<mark>计算机</mark><span>科学与技术</span>``)
        # render as one glued word, so a space there corrupts the value
        # ("计算机 科学与技术"). Only a block-level child (``<div>``/``<p>`` …)
        # or an existing whitespace boundary introduces a separator.
        out = ""
        prev_block = False
        for text, is_block in deduped:
            if out and not out.endswith((" ", "\n")) and not text.startswith((" ", "\n")):
                if prev_block or is_block:
                    out += " "
            out += text
            prev_block = is_block
        joined = " ".join(out.split())
        joined = _strip_icon_glyphs(joined)
        return self._truncate(joined)

    @staticmethod
    def _clean_field_label(text: str) -> str:
        """Trim a field label's decorative trailing colon / required mark.

        Sites author the label as ``<label>证件类型：</label>``; composing it as
        a prefix produced doubled punctuation (``证件类型：：请选择``). The
        trailing ``：`` / ``:`` / ``*`` is decoration, never part of the name.
        """
        if not text:
            return text
        return text.strip().rstrip("：:＊*").strip()

    def _truncate(self, text: str, limit: int = 100) -> str:
        text = " ".join(text.split())
        return text if len(text) <= limit else text[:limit] + "…"

    @staticmethod
    def _is_machine_token(value: str) -> bool:
        """True for an internal identifier masquerading as a label.

        Page frameworks name/ID controls with generated tokens
        (``11_20_1`` / ``firstLevl11_245_1`` / ``14_66011_1``) that are meaningful
        to the page but tell the LLM nothing. They are **bugs** to surface as
        control labels. Only clearly machine-shaped strings are matched so human
        field names (``email`` / ``field1`` / ``q``) survive.
        """
        text = (value or "").strip()
        if not text:
            return False
        digits = sum(ch.isdigit() for ch in text)
        if digits == 0:
            return False
        if text.isdigit():
            return True
        core = text.replace("_", "").replace("-", "").replace(" ", "")
        if core.isdigit():
            return True
        # Internal ids are digit groups joined by separators
        # (``11_20_1`` / ``firstLevl11_245_1``); a digit-heavy token is also an id.
        if "_" in text and digits > 0:
            return True
        return digits >= max(2, len(text) * 0.4)

    def _is_image(self, node: EnhancedNode) -> bool:
        if node.tag == "img" or node.role == "img":
            return True
        if node.tag == "input" and node.attributes.get("type") == "image":
            return True
        return False

    def _landmark_for(self, node: EnhancedNode) -> str:
        if node.role in ROLE_LANDMARKS:
            return LANDMARK_LABELS.get(node.role, node.role)
        role = TAG_LANDMARKS.get(node.tag, "")
        if role:
            return LANDMARK_LABELS.get(role, role)
        return ""

    def _has_interactive_descendant(self, node: EnhancedNode) -> bool:
        for child in node.children:
            if child.is_text:
                continue
            if child.is_element and classify(child):
                return True
            if self._has_interactive_descendant(child):
                return True
        return False

    def _has_rescuable_interactive_descendant(self, node: EnhancedNode) -> bool:
        """Like ``_has_interactive_descendant`` but ignores zero-width a11y mirrors.

        Used by the mid-animation rescue: it must fire for a real popup option
        (even one still 0-size) or a normal control, but must *not* revive the
        zero-width ``role=listbox`` accessibility mirror whose only children are
        degenerate ``role=option`` nodes that merely repeat the real entries.
        """
        for child in node.children:
            if child.is_text or not child.is_element:
                continue
            if self._is_popup_option(child):
                return True
            if (child.role or "").lower() in _OPTION_ROLES and (
                not child.bbox or child.bbox[2] <= 4
            ):
                # A zero-width ARIA-mirror option: not a real, clickable target;
                # skip it and do not descend (its subtree holds only text).
                continue
            if classify(child):
                return True
            if self._has_rescuable_interactive_descendant(child):
                return True
        return False

    def _inside_overlay(self, node: EnhancedNode, max_hops: int = 6) -> bool:
        """True if an ancestor looks like a floating overlay (dropdown / menu)."""
        current = node.parent
        hops = 0
        while current is not None and hops < max_hops:
            if current.is_element:
                if (current.role or "").lower() in _OVERLAY_ROLES:
                    return True
                classes = (current.attributes.get("class") or "").lower()
                if any(hint in classes for hint in _OVERLAY_CLASS_HINTS):
                    return True
            current = current.parent
            hops += 1
        return False

    @staticmethod
    def _has_direct_text(node: EnhancedNode) -> bool:
        """True if ``node`` owns a non-empty text run (no element needed)."""
        for child in node.children:
            if child.is_text and (child.text or "").strip():
                return True
        return False

    def _inside_open_overlay(self, node: EnhancedNode, max_hops: int = 10) -> bool:
        """True if an ancestor is a *painted* floating overlay (see ``_rescue``).

        "Painted" means the overlay itself is not hidden, has a real box and is on
        screen (or is caught on the transparent first frame of its enter
        animation) — so a closed / detached popup never leaks its text.
        """
        current = node.parent
        hops = 0
        while current is not None and hops < max_hops:
            if current.is_element:
                role = (current.role or "").lower()
                classes = (current.attributes.get("class") or "").lower()
                looks_overlay = role in _OVERLAY_ROLES or any(
                    hint in classes for hint in _OVERLAY_CLASS_HINTS
                )
                if looks_overlay and not current.hidden:
                    if current.bbox and current.bbox[2] > 0 and current.bbox[3] > 0:
                        if current.in_viewport:
                            return True
                    elif self._effective_opacity_zero(current):
                        return True
            current = current.parent
            hops += 1
        return False

    def _is_popup_option(self, node: EnhancedNode) -> bool:
        """True if ``node`` is one selectable entry of a floating overlay.

        Covers both the ARIA mirror entries (``role=option``) and the rendered
        entries that carry only a class (``.ant-select-item-option``). A bare
        ``role=option`` additionally needs a real, non-degenerate box so the
        zero-width accessibility mirror inside a ``height:0`` listbox is not named
        a second time. Identification is shared by ``_is_separate_control`` (to
        prune the wrapper) and the render pass (to rescue the mid-animation
        popup), so the two never disagree.
        """
        if not node.is_element or not classify(node):
            return False
        role = (node.role or "").lower()
        classes = (node.attributes.get("class") or "").lower()
        tokens = classes.split()
        if any(
            tok == "option" or tok.endswith("-option") or tok.endswith("__option")
            for tok in tokens
        ):
            return self._inside_overlay(node)
        if role in _OPTION_ROLES:
            if not node.bbox or node.bbox[2] <= 4:
                # The a11y mirror listbox is ``width:0; overflow:hidden``; its
                # ``role=option`` children duplicate the real entries.
                return False
            return self._inside_overlay(node)
        return False

    def _has_popup_option_descendant(self, node: EnhancedNode) -> bool:
        """True if any descendant is a selectable entry of a floating overlay."""
        for child in node.children:
            if child.is_text or not child.is_element:
                continue
            if self._is_popup_option(child):
                return True
            if self._has_popup_option_descendant(child):
                return True
        return False

    def _is_overlay_container(self, node: EnhancedNode) -> bool:
        """True if ``node`` *is* a floating overlay (not one of its entries)."""
        if not node.is_element:
            return False
        if (node.role or "").lower() in _OVERLAY_ROLES:
            return True
        classes = (node.attributes.get("class") or "").lower()
        if not any(hint in classes for hint in _OVERLAY_CLASS_HINTS):
            return False
        # A trigger often carries a "...-dropdown" class; only treat a node as the
        # overlay itself when it is actually positioned to float.
        return node.styles.get("position") in ("absolute", "fixed")

    def _is_separate_control(self, node: EnhancedNode, root: Optional[EnhancedNode] = None) -> bool:
        """True if ``node`` is an independent control, not a mere internal part.

        Used to tell a clickable *wrapper* (whose children are the real,
        separately-callable controls — prune it) from a composite control such
        as ``.phoenix-select`` (the box itself is the entry; its placeholder and
        bare typeahead input are internal parts, so name the box).

        A descendant only counts as separate when it is interactive via a
        *semantic* signal (``a``/``button``/``select``/``textarea``/``summary``,
        an action ``input`` type, an ARIA control role, ``contenteditable``, or a
        separate scroll/drag capability). Elements that are merely
        ``cursor:pointer`` (e.g. a placeholder) do not count. A plain text input
        only counts when it is a real field of its own: a select's typeahead
        input lives *inside* the box (``root``) and carries the selected value,
        so it must be treated as a part, not a separate control.
        """
        if not node.is_element or node.hidden:
            return False
        if self._is_popup_option(node):
            # A dropdown / menu entry is a real, separately-callable control even
            # while the just-opened popup is still 0-size / transparent (its box
            # is resolved live at click time). Counting it as separate prunes the
            # composite select wrapper, so the options are exposed instead of
            # being flattened into the wrapper's label.
            return node.rendered
        if not node.visible:
            # NOTE: ``in_viewport`` is deliberately *not* consulted here. A real
            # control is still a real control when it is momentarily scrolled
            # just out of the viewport margin; judging by viewport made a
            # clickable container (whose inner button was outside the margin)
            # look like a leaf composite control, swallowing the button and
            # flattening the whole area into one named blob.
            #
            # Exception: a searchable select's inline typeahead is a real field
            # even before it has any laid-out width (``visible`` is False while
            # the box is zero-area). If the snapshot *did* lay it out
            # (``rendered``), keep it as a separate control; a closed select's
            # search input has no layout row (``rendered`` False) and stays an
            # internal part, so no phantom fields appear.
            if (
                node.tag == "input"
                and node.rendered
                and not node.hidden
                and self._is_searchable_typeahead(node)
            ):
                return True
            return False
        if node.tag in ("button", "select", "textarea", "summary"):
            return True
        if node.tag == "a":
            # A bare ``<a>`` with no link / click signal (no ``href``, inline
            # ``on*`` handler, ARIA role, ``cursor:pointer`` or ``tabindex``) is
            # a decorative wrapper, not a separate control. Counting it as one
            # used to prune the genuinely clickable wrapper around it (e.g. a
            # ``role="gridcell"`` month cell); the bare ``<a>`` then fell through
            # to plain text, leaving the whole control unaddressable. Only a
            # real anchor is a separate control.
            return is_clickable(node)
        if node.tag == "input":
            itype = node.attributes.get("type", "text").lower()
            if itype in CLICKABLE_INPUT_TYPES:
                return True
            if self._input_is_widget_helper(node):
                # A readonly / hidden input is a widget's passive part, never a
                # field the model addresses directly.
                return False
            if self._is_searchable_typeahead(node):
                # An *editable* combobox / autocomplete is a real search box even
                # when it sits inside the clickable composite select; the model
                # must be able to type into it to filter candidates.
                return True
            if self._is_labelled_field(node):
                # An editable input that is the target of a field ``<label>``
                # (or nested in one) is a *real* field, even when a clickable
                # form row / wrapper surrounds it (Ant Design turns the whole
                # row ``cursor:pointer``). It must count as a separate control so
                # the wrappers are pruned and the field is exposed.
                return True
            if self._has_clickable_ancestor(node, root):
                # A text input wrapped by a clickable composite control (the
                # select box itself) is that control's internal typeahead, not a
                # field the model should address directly.
                return False
            return bool(node.input_value or node.attributes.get("placeholder"))
        if node.role in CLICKABLE_ROLES:
            return True
        if node.attributes.get("contenteditable") in ("", "true", "plaintext-only"):
            return True
        return classify(node) in ("scroll", "drag")

    def _is_labelled_field(self, node: EnhancedNode) -> bool:
        """True if ``node`` is an input associated with a field ``<label>``.

        Association is either a wrapping ``<label>`` (``<label><input></label>``)
        or a ``<label for="<id>">`` anywhere in a nearby ancestor's subtree. This
        is the semantic signal that separates a *real* form field from a widget's
        internal typeahead (which has no label pointing at it).
        """
        ancestor = node.parent
        while ancestor is not None:
            if ancestor.is_element and ancestor.tag == "label":
                return True
            ancestor = ancestor.parent
        nid = node.attributes.get("id", "")
        if not nid:
            return False
        ancestor = node.parent
        hops = 0
        while ancestor is not None and hops < 8:
            if self._find_label_for(ancestor, nid):
                return True
            ancestor = ancestor.parent
            hops += 1
        return False

    def _find_label_for(self, node: EnhancedNode, target_id: str) -> bool:
        if node.is_element and node.tag == "label":
            return node.attributes.get("for") == target_id
        for child in node.children:
            if child.is_element and self._find_label_for(child, target_id):
                return True
        return False

    def _has_clickable_ancestor(
        self, node: EnhancedNode, root: Optional[EnhancedNode] = None
    ) -> bool:
        """True if ``node`` sits inside a clickable container at or below ``root``.

        ``root`` is the composite control being tested (the caller), so the walk
        stops once it reaches ``root`` after checking it as well.
        """
        ancestor = node.parent
        while ancestor is not None:
            if ancestor.is_element and _is_click_like(classify(ancestor)):
                return True
            if ancestor is root:
                break
            ancestor = ancestor.parent
        return False

    def _has_separate_interactive_descendant(
        self, node: EnhancedNode, root: Optional[EnhancedNode] = None
    ) -> bool:
        if root is None:
            root = node
        for child in node.children:
            if child.is_text:
                continue
            if self._is_separate_control(child, root):
                return True
            if self._has_separate_interactive_descendant(child, root):
                return True
        return False

    def _page_info(self, tree: EnhancedTree) -> str:
        # Count the interactive names actually emitted for this viewport, not
        # every classified node in the viewport *margin*. The old tree-wide count
        # routinely overstated the list (145 reported vs ~24 named on a real
        # form), which pushed the LLM to guess element ids that were never
        # rendered. ``self._lines`` is populated by ``serialize_lines`` just
        # before this call.
        interactive = len({name for line in self._lines for name in line.interactive})
        vp = tree.viewport
        return (
            f"<page_info>视口={int(vp['width'])}x{int(vp['height'])} "
            f"可互动元素 {interactive} 个 {self._scroller_summary()}</page_info>"
        )

    def _scroll_position(self, node: EnhancedNode) -> tuple:
        """``(can_move_up, can_move_down)`` for one scroll container.

        Two independent sources, because neither is universal:

        * a container: does any laid-out descendant lie above / below the box that
          clips it (the same comparison ``_in_clip`` uses)? That is exactly "is
          there anything further up / further down", i.e. "已到顶/已到底";
        * the document scroller (``#page``): use the page metrics captured in
          ``viewport`` — a synthetic node has no subtree to measure.
        """
        if node.tag == PAGE_SCROLL_TAG:
            vp = (self._tree.viewport if self._tree is not None else {}) or {}
            top = float(vp.get("scroll_y") or 0.0)
            height = float(vp.get("height") or 0.0)
            full = float(vp.get("scroll_height") or 0.0)
            client = float(vp.get("client_height") or height)
            return (top > 1.0, top + client < full - 4.0)
        box = node.bbox
        if not box or box[2] <= 0 or box[3] <= 0:
            return (False, False)
        top, bottom = box[1], box[1] + box[3]
        up = down = False
        stack = list(node.children)
        seen = 0
        while stack and seen < 4000 and not (up and down):
            current = stack.pop()
            seen += 1
            if not current.is_element:
                continue
            if current.styles.get("position") == "fixed":
                continue
            box2 = current.bbox
            if box2 and box2[2] > 0 and box2[3] > 0:
                if box2[1] + box2[3] < top - 2:
                    up = True
                elif box2[1] > bottom + 2:
                    down = True
            stack.extend(current.children)
        return (up, down)

    def _scroller_summary(self) -> str:
        """Tell the model *which* scroller matters and whether it can still move.

        ``scrollY`` describes only ``document.scrollingElement``. On an SPA layout
        (Feishu Jobs scrolls inside ``section.atsx-layout``) the page never moves,
        so ``scrollY=0`` was reported while the form's own container sat 1060px
        down — the model read "we are at the top" and concluded a field it could
        not see (姓名 / 手机号码, hidden above the viewport) did not exist. Report
        the outermost scroller instead, or the document scroller when no container
        scrolls.
        """
        report = None
        for item in self._scroll_reports:
            if report is None or item[0] < report[0]:
                report = item
        if report is None:
            return "滚动：无（内容已全部可见）"
        _depth, name, is_page, can_up, can_down = report
        target = "整页" if is_page else f"[可滚动元素 {name}]"
        return "滚动：%s %s、%s" % (
            target,
            "未到顶" if can_up else "已到顶",
            "未到底" if can_down else "已到底",
        )
