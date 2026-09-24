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

from dataclasses import dataclass
from typing import Optional

from .build import PAGE_SCROLL_TAG, EnhancedNode, EnhancedTree
from .classify import (
    CLICKABLE_INPUT_TYPES,
    CLICKABLE_ROLES,
    classify,
    is_clickable,
    is_control_icon,
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
    "input": ("可输入元素", "输入"),
    "drag": ("可拖动元素", "拖动"),
    "scroll": ("可滚动元素", "滚动"),
}

MAX_TEXT_LENGTH = 4000

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

BBox = tuple[float, float, float, float]

_SCROLL_OPEN = "<可滚动元素 "
_SCROLL_CLOSE = "</可滚动元素 "


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
            text = text[: self.max_chars] + "\n…（内容已截断，可用 tool-3 获取全量）"
        return text

    def serialize_lines(self, tree: EnhancedTree) -> list[OutLine]:
        self._lines = []
        self._stack = []
        self._buffer = ""
        self._buffer_names = []
        self._buffer_labels = []
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
        if not self._in_clip(child, clip):
            return

        if not child.visible or not child.in_viewport:
            # The node itself has no visible box (typically zero-area), or its
            # own box is outside the viewport, but its element descendants may
            # still be laid out and visible. Two classic cases:
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
                    if not (cell.visible and cell.in_viewport and cell.tag not in SKIP_TAGS):
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
            if (child.tag == "li" or child.role == "listitem") and self._has_blockish_descendant(child):
                # A scrollable rich list item wraps the scroll element around its
                # ``[列表项]`` boundary.
                self._group_or_interact(child, depth, "[列表项]", clip)
            else:
                self._group_scroll(child, depth, clip)
            return

        if category:
            if category == "click" and self._has_separate_interactive_descendant(child):
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
                name = self.registry.get_or_create(child)
                self._append_inline(
                    self._interactive_text(child, category, name),
                    [name],
                    [self._interactive_label(child, category)],
                )
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
        self._emit_header(depth, header, interactive, closing)
        self._stack.append((depth, header, closing))
        self._render_children(node, depth + 1, clip)
        self._stack.pop()

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
        elif category == "click" and not self._has_interactive_descendant(node):
            self._group_click(node, depth, header, clip)
        else:
            self._group(node, depth, header, clip)

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
        before = len(self._lines)
        if inner_header:
            self._group(node, depth + 1, inner_header, node.bbox)
        else:
            self._render_children(node, depth + 1, node.bbox)
        if len(self._lines) == before:
            self._emit_content(depth + 1, "（可滚动区域）")
        self._stack.pop()
        self._emit_close(depth, closing)

    def _group_click(
        self, node: EnhancedNode, depth: int, header: str, clip: Optional[BBox]
    ) -> None:
        name = self.registry.get_or_create(node)
        opening = f"<可点击元素 {name}>"
        closing = f"</可点击元素 {name}>"
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
        alt = node.attributes.get("alt") or node.ax_name or "图片"
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
        return display.startswith(_BLOCK_DISPLAY_PREFIXES)

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
            if child.tag in SKIP_TAGS or not child.visible or not child.in_viewport:
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
        if category == "input":
            label = self._input_value(node) or node.attributes.get("placeholder", "")
        else:
            label = self._label(node)
            # An unlabeled control icon (radio / checkbox circle) pairs with a text
            # label; name it after that text but keep it distinguishable from the
            # label's own clickable entry (which expands / navigates): “选择：重庆市”.
            # A native checkbox / radio input is the same "control + text" shape
            # without an icon, so it gets the same treatment instead of an empty tag.
            if not label and category == "click" and (
                is_control_icon(node) or self._is_choice_input(node)
            ):
                paired = self._pointer_sibling_label(node) or self._nearby_text_label(node)
                if paired:
                    label = f"选择：{paired}"
            # A composite control (e.g. a select box) often shows only its value or
            # placeholder (“请选择”). Prefix the associated field label so the LLM
            # can tell which field it is: “政治面貌：请选择”.
            if category == "click" and self._has_field_input_descendant(node):
                prefix = self._associated_field_label(node)
                if prefix and prefix not in label:
                    label = f"{prefix}：{label}" if label else prefix
        # Never emit an empty interactive tag: an unnamed control is useless to the
        # LLM. Fall back to nearby text / a semantic attribute token / a generic word.
        if not label:
            label = self._fallback_label(node, category)
        # Mark the selected option of a radio / checkbox / tab group. A click on
        # one of these changes only the control's state (the page text is
        # unchanged), so without this marker the diff would say “（页面无变化）” and
        # the LLM could not confirm that its click was applied. Only the selected
        # item is marked; unselected items carry no marker.
        if self._selection_state(node) is True:
            label = f"{label} [已选]"
        return label

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
        if category == "click":
            if node.role == "combobox" or node.tag == "select":
                return "下拉框"
            if node.role == "option":
                return "选项"
            if node.role == "button" or node.tag in ("button", "input"):
                return "按钮"
            if node.tag == "a":
                return "链接"
            return "可点击项"
        if category == "input":
            return "输入框"
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
        tokens = [
            token
            for token in node.attributes.get("class", "").split()
            if self._is_semantic_token(token)
        ]
        if not tokens:
            return ""
        hinted = [
            token
            for token in tokens
            if any(hint in token for hint in _SEMANTIC_TOKEN_HINTS)
        ]
        best = max(hinted or tokens, key=len)
        parts = best.split("-")
        for index, part in enumerate(parts):
            if part in _ACTION_HINTS:
                if index > 0:
                    best = "-".join(parts[index:])
                break
        return self._truncate(best, 40)

    @staticmethod
    def _is_semantic_token(token: str) -> bool:
        if len(token) < 3 or not token[0].isalpha() or not token[0].islower():
            return False
        # Framework/runtime state & generated-style classes are not names.
        if token.startswith(_FRAMEWORK_CLASS_PREFIXES):
            return False
        return all(ch.islower() or ch.isdigit() or ch == "-" for ch in token)

    def _is_choice_input(self, node: EnhancedNode) -> bool:
        """True for a native ``<input type=checkbox|radio>``."""
        return (
            node.tag == "input"
            and node.attributes.get("type", "").lower() in ("checkbox", "radio")
        )

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

        Only ``True`` is ever rendered; ``None`` / ``False`` mean "no marker".
        """
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
        return None

    @staticmethod
    def _class_has_selection_token(node: EnhancedNode) -> bool:
        """True if a class token encodes a selected/checked state.

        Only ``checked`` / ``selected`` suffixes count (``phoenix-radio--checked``,
        ``is-checked``, ``Mui-checked``), never lookalikes such as ``unselected``
        (no hyphen boundary) or ``checkbox``.
        """
        classes = node.attributes.get("class", "").lower()
        if not classes:
            return False
        for token in classes.split():
            if token in ("checked", "selected"):
                return True
            if token.endswith(("-checked", "-selected")):
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

    def _associated_field_label(self, node: EnhancedNode) -> str:
        """Find the field label associated with a form control.

        Component libraries usually wrap a control as
        ``<div class="form-item"><div class="...title"><label>政治面貌</label>
        </div><div class="...control">…control…</div></div>``. Walk up a few
        ancestor levels and take the nearest sibling subtree's ``<label>`` text.
        """
        branch = node
        ancestor = node.parent
        hops = 0
        while ancestor is not None and hops < 8:
            for sibling in ancestor.children:
                if sibling is branch or not sibling.is_element:
                    continue
                text = self._find_label_text(sibling)
                if text:
                    return self._truncate(text, 40)
            branch = ancestor
            ancestor = ancestor.parent
            hops += 1
        return ""

    def _find_label_text(self, node: EnhancedNode) -> str:
        if node.tag == "label":
            return self._collect_text(node)
        for child in node.children:
            if child.is_element:
                text = self._find_label_text(child)
                if text:
                    return text
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
        return node.input_value or node.attributes.get("value", "")

    def _label(self, node: EnhancedNode) -> str:
        if node.ax_name:
            return self._truncate(node.ax_name)
        text = self._collect_text(node)
        if text:
            return text
        for attr in ("placeholder", "title", "aria-label", "alt", "name"):
            value = node.attributes.get(attr)
            if value:
                return self._truncate(value)
        return ""

    def _collect_text(self, node: EnhancedNode) -> str:
        parts: list[str] = []
        for child in node.children:
            if child.is_text:
                parts.append(child.text)
            elif child.is_element and not self._is_image(child):
                nested = self._collect_text(child)
                if nested:
                    parts.append(nested)
        # Composite controls often repeat the same text in several sibling
        # nodes (e.g. a select renders its value in a calc/placeholder/tip
        # span). Collapse adjacent duplicates so the label stays readable.
        deduped: list[str] = []
        for part in parts:
            if deduped and part == deduped[-1]:
                continue
            deduped.append(part)
        joined = " ".join(" ".join(deduped).split())
        return self._truncate(joined)

    def _truncate(self, text: str, limit: int = 100) -> str:
        text = " ".join(text.split())
        return text if len(text) <= limit else text[:limit] + "…"

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
        if not node.is_element or node.hidden or not node.visible or not node.in_viewport:
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

    def _has_clickable_ancestor(
        self, node: EnhancedNode, root: Optional[EnhancedNode] = None
    ) -> bool:
        """True if ``node`` sits inside a clickable container at or below ``root``.

        ``root`` is the composite control being tested (the caller), so the walk
        stops once it reaches ``root`` after checking it as well.
        """
        ancestor = node.parent
        while ancestor is not None:
            if ancestor.is_element and classify(ancestor) == "click":
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
        interactive = sum(
            1 for n in tree.nodes if n.is_element and n.visible and n.in_viewport and classify(n)
        )
        vp = tree.viewport
        return (
            f"<page_info>scrollY={int(vp['scroll_y'])} 视口={int(vp['width'])}x{int(vp['height'])} "
            f"可互动元素 {interactive} 个</page_info>"
        )
