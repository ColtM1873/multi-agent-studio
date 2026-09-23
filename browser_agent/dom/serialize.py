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
from .classify import classify
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

MAX_TEXT_LENGTH = 200

BBox = tuple[float, float, float, float]


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
            parts.append("\t" * line.depth + line.text)
        parts.append("")
        parts.append(self._page_info(tree))
        text = "\n".join(parts)
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
            self._append_text(child.text)
            return

        if not child.is_element:
            # fragment / document containers: recurse transparently
            for grand in child.children:
                self._render_child(grand, depth, clip)
            return

        if child.tag in SKIP_TAGS or not child.visible or not child.in_viewport:
            return
        if not self._in_clip(child, clip):
            return

        level = self._heading_level(child)
        if level:
            self._flush(depth)
            self._group(child, depth, f"[{level}级标题]", clip)
            return

        if self._is_image(child):
            self._flush(depth)
            self._group_image(child, depth)
            return

        landmark = self._landmark_for(child)
        if landmark:
            self._flush(depth)
            self._group(child, depth, f"[{landmark}]", clip)
            return

        if child.tag == "figure":
            self._flush(depth)
            self._group(child, depth, "[图]", clip)
            return

        # An unnamed ``<section>`` is a generic block container. Only surface it
        # as a group when it actually holds blockish structure (otherwise the
        # ``[文本]`` branch below is the better, less noisy fit).
        if child.tag == "section" and self._has_blockish_descendant(child):
            self._flush(depth)
            self._group(child, depth, "[区块]", clip)
            return

        if child.tag == "dl":
            self._flush(depth)
            self._group(child, depth, "[定义列表]", clip)
            return

        if child.tag == "dt":
            self._flush(depth)
            self._group(child, depth, "[术语]", clip)
            return

        if child.tag == "dd":
            self._flush(depth)
            self._group(child, depth, "[描述]", clip)
            return

        if child.tag in ("ul", "ol") or child.role == "list":
            self._flush(depth)
            self._group(child, depth, "[列表]", clip)
            return

        if child.tag == "table" or child.role == "table":
            self._flush(depth)
            self._group(child, depth, "[表格]", clip)
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
                    self._append_text(cell.text)
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
            self._group_scroll(child, depth, clip)
            return

        if category:
            if category == "click" and self._has_interactive_descendant(child):
                # A clickable wrapper around other interactive elements (e.g. a
                # media-control bar) is noise: the model would never call the
                # wrapper. Skip it (assign no name) and render its children.
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

    def _group_scroll(self, node: EnhancedNode, depth: int, clip: Optional[BBox]) -> None:
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
        self._render_children(node, depth + 1, node.bbox)
        if len(self._lines) == before:
            self._emit_content(depth + 1, "（可滚动区域）")
        self._stack.pop()
        self._emit_close(depth, closing)

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
            text = raw if len(raw) <= MAX_TEXT_LENGTH else raw[:MAX_TEXT_LENGTH] + "…"
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
    def _append_text(self, text: str) -> None:
        normalized = " ".join(text.split())
        if not normalized:
            return
        # Skip a text node that just repeats the label of the interactive tag
        # immediately before it (e.g. a button rendered as
        # ``<可点击元素 eN>进入全屏模式</可点击元素 eN>`` followed by its visible
        # text ``全屏``). The tag's label already carries the meaning.
        if self._buffer.endswith(">") and self._buffer_labels:
            last = self._buffer_labels[-1]
            if last and normalized in last:
                return
        if len(normalized) > MAX_TEXT_LENGTH:
            normalized = normalized[:MAX_TEXT_LENGTH]
        if self._buffer and not self._buffer.endswith((" ", ">")):
            self._buffer += " "
        self._buffer += normalized

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
            value = self._input_value(node)
            return value if value else node.attributes.get("placeholder", "")
        return self._label(node)

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
        joined = " ".join(" ".join(parts).split())
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

    def _page_info(self, tree: EnhancedTree) -> str:
        interactive = sum(
            1 for n in tree.nodes if n.is_element and n.visible and n.in_viewport and classify(n)
        )
        vp = tree.viewport
        return (
            f"<page_info>scrollY={int(vp['scroll_y'])} 视口={int(vp['width'])}x{int(vp['height'])} "
            f"可互动元素 {interactive} 个</page_info>"
        )
