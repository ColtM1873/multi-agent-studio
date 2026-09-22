"""Serialize an enhanced DOM tree into an ARIA-landmark + indentation tree.

Interactive nodes are wrapped in semantic tags, e.g.::

    姓名：<可输入元素 e4></可输入元素 e4>

Images become ``<图片>alt</图片>`` (no src, token-cheap, non-multimodal friendly).
Scrollable containers are rendered as clipped groups so that scrolling produces
a meaningful diff (only the newly revealed children appear).
"""

from __future__ import annotations

from typing import Optional

from .build import EnhancedNode, EnhancedTree
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

CATEGORY_TAGS = {
    "click": ("可点击元素", "点击"),
    "input": ("可输入元素", "输入"),
    "drag": ("可拖动元素", "拖动"),
    "scroll": ("可滚动元素", "滚动"),
}

MAX_LINE_LENGTH = 160
MAX_TEXT_LENGTH = 200

BBox = tuple[float, float, float, float]


class DOMSerializer:
    def __init__(self, registry: NameRegistry, max_chars: int = 40000) -> None:
        self.registry = registry
        self.max_chars = max_chars

    def serialize(self, tree: EnhancedTree) -> str:
        lines: list[str] = []
        header = f"[document] url={tree.url or 'about:blank'} title={tree.title or ''}".rstrip()
        lines.append(header)

        self._render_children(tree.root, 0, lines, None)

        body = "\n".join(line for line in lines if line.strip())
        text = body + "\n" + self._page_info(tree)
        if len(text) > self.max_chars:
            text = text[: self.max_chars] + "\n…（内容已截断，可用 tool-3 获取全量）"
        return text

    # ------------------------------------------------------------------ #
    # rendering
    # ------------------------------------------------------------------ #
    def _render_children(
        self, node: EnhancedNode, depth: int, lines: list[str], clip: Optional[BBox]
    ) -> None:
        buffer = ""
        for child in node.children:
            if child.is_text:
                buffer = self._append_text(buffer, child.text)
                continue

            if not child.is_element:
                buffer = self._render_transparent(child, depth, lines, buffer, clip)
                continue

            if child.tag in SKIP_TAGS or not child.visible or not child.in_viewport:
                continue
            if not self._in_clip(child, clip):
                continue

            if self._is_image(child):
                buffer = self._append_inline(buffer, self._image_text(child))
                continue

            category = classify(child)
            landmark = self._landmark_for(child)

            if landmark:
                self._flush(buffer, depth, lines)
                buffer = ""
                lines.append(f"{'  ' * depth}[{landmark}]")
                self._render_children(child, depth + 1, lines, clip)
                continue

            if category == "scroll":
                self._flush(buffer, depth, lines)
                buffer = ""
                name = self.registry.get_or_create(child)
                lines.append(f"{'  ' * depth}[可滚动元素 {name}]")
                self._render_children(child, depth + 1, lines, child.bbox)
                continue

            if category:
                if self._has_interactive_descendant(child):
                    self._flush(buffer, depth, lines)
                    buffer = ""
                    name = self.registry.get_or_create(child)
                    lines.append(f"{'  ' * depth}[{CATEGORY_TAGS[category][0]} {name}]")
                    self._render_children(child, depth + 1, lines, clip)
                else:
                    buffer = self._append_inline(buffer, self._interactive_text(child, category))
                continue

            if child.tag in BLOCK_TAGS:
                self._flush(buffer, depth, lines)
                buffer = ""
                self._render_children(child, depth, lines, clip)
                continue

            buffer = self._render_transparent(child, depth, lines, buffer, clip)

        self._flush(buffer, depth, lines)

    def _render_transparent(
        self,
        node: EnhancedNode,
        depth: int,
        lines: list[str],
        buffer: str,
        clip: Optional[BBox],
    ) -> str:
        for child in node.children:
            if child.is_text:
                buffer = self._append_text(buffer, child.text)
                continue
            if not child.is_element:
                buffer = self._render_transparent(child, depth, lines, buffer, clip)
                continue
            if child.tag in SKIP_TAGS or not child.visible or not child.in_viewport:
                continue
            if not self._in_clip(child, clip):
                continue
            if self._is_image(child):
                buffer = self._append_inline(buffer, self._image_text(child))
                continue

            category = classify(child)
            landmark = self._landmark_for(child)
            if (
                landmark
                or category == "scroll"
                or (category and self._has_interactive_descendant(child))
                or child.tag in BLOCK_TAGS
            ):
                self._flush(buffer, depth, lines)
                buffer = ""
                if landmark:
                    lines.append(f"{'  ' * depth}[{landmark}]")
                    self._render_children(child, depth + 1, lines, clip)
                elif category == "scroll":
                    name = self.registry.get_or_create(child)
                    lines.append(f"{'  ' * depth}[可滚动元素 {name}]")
                    self._render_children(child, depth + 1, lines, child.bbox)
                elif category and self._has_interactive_descendant(child):
                    name = self.registry.get_or_create(child)
                    lines.append(f"{'  ' * depth}[{CATEGORY_TAGS[category][0]} {name}]")
                    self._render_children(child, depth + 1, lines, clip)
                else:
                    self._render_children(child, depth, lines, clip)
            elif category:
                buffer = self._append_inline(buffer, self._interactive_text(child, category))
            else:
                buffer = self._render_transparent(child, depth, lines, buffer, clip)
        return buffer

    def _flush(self, buffer: str, depth: int, lines: list[str]) -> None:
        text = buffer.strip()
        if not text:
            return
        indent = "  " * depth
        while len(text) > MAX_LINE_LENGTH:
            lines.append(indent + text[:MAX_LINE_LENGTH])
            text = text[MAX_LINE_LENGTH:]
        lines.append(indent + text)

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    def _in_clip(self, node: EnhancedNode, clip: Optional[BBox]) -> bool:
        if clip is None or not node.bbox:
            return True
        x, y, w, h = node.bbox
        cx, cy, cw, ch = clip
        return not (x + w < cx - 2 or x > cx + cw + 2 or y + h < cy - 2 or y > cy + ch + 2)

    def _append_text(self, buffer: str, text: str) -> str:
        normalized = " ".join(text.split())
        if not normalized:
            return buffer
        if len(normalized) > MAX_TEXT_LENGTH:
            normalized = normalized[:MAX_TEXT_LENGTH]
        if buffer and not buffer.endswith((" ", ">")):
            buffer += " "
        return buffer + normalized

    def _append_inline(self, buffer: str, piece: str) -> str:
        if not piece:
            return buffer
        if buffer and not buffer.endswith(" "):
            return buffer + piece
        return buffer + piece

    def _interactive_text(self, node: EnhancedNode, category: str) -> str:
        tag, _ = CATEGORY_TAGS[category]
        name = self.registry.get_or_create(node)
        if category == "input":
            value = self._input_value(node)
            label = value if value else node.attributes.get("placeholder", "")
        else:
            label = self._label(node)
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

    def _image_text(self, node: EnhancedNode) -> str:
        alt = node.attributes.get("alt") or node.ax_name or "图片"
        return f"<图片>{self._truncate(alt, 80)}</图片>"

    def _landmark_for(self, node: EnhancedNode) -> str:
        if node.role in ROLE_LANDMARKS:
            return node.role
        return TAG_LANDMARKS.get(node.tag, "")

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
