"""Classify enhanced nodes into the four interactive categories."""

from __future__ import annotations

from .build import PAGE_SCROLL_TAG, EnhancedNode

CLICKABLE_TAGS = {"a", "button", "summary", "label", "option"}
CLICKABLE_INPUT_TYPES = {
    "submit",
    "button",
    "reset",
    "image",
    "checkbox",
    "radio",
    "file",
    "color",
    "date",
    "datetime-local",
    "month",
    "week",
    "time",
}
CLICKABLE_ROLES = {
    "button",
    "link",
    "checkbox",
    "radio",
    "switch",
    "menuitem",
    "menuitemcheckbox",
    "menuitemradio",
    "tab",
    "option",
    "treeitem",
    "gridcell",
    "combobox",
}

INPUT_TAGS = {"textarea"}
INPUT_TYPES = {
    "text",
    "email",
    "password",
    "search",
    "tel",
    "url",
    "number",
    "date",
    "datetime-local",
    "month",
    "week",
    "time",
}
INPUT_ROLES = {"textbox", "searchbox", "spinbutton"}

DRAGGABLE_ROLES = {"slider"}


def _is_disabled(node: EnhancedNode) -> bool:
    return "disabled" in node.attributes or node.attributes.get("aria-disabled") == "true"


def _input_type(node: EnhancedNode) -> str:
    return node.attributes.get("type", "text").lower()


def is_input(node: EnhancedNode) -> bool:
    if not node.is_element or _is_disabled(node):
        return False
    if node.tag == "textarea":
        return True
    if node.attributes.get("contenteditable") in ("", "true", "plaintext-only"):
        return True
    if node.tag == "input" and _input_type(node) in INPUT_TYPES:
        return True
    if node.role in INPUT_ROLES:
        return True
    return False


def is_draggable(node: EnhancedNode) -> bool:
    if not node.is_element or _is_disabled(node):
        return False
    if node.attributes.get("draggable") == "true":
        return True
    if node.tag == "input" and _input_type(node) == "range":
        return True
    if node.role in DRAGGABLE_ROLES:
        return True
    return False


def is_scrollable(node: EnhancedNode) -> bool:
    if not node.is_element:
        return False
    if node.tag == PAGE_SCROLL_TAG:
        return True
    if not node.bbox:
        return False
    if node.tag in ("html", "body"):
        return False
    styles = node.styles
    vertical = styles.get("overflow-y") in ("auto", "scroll")
    horizontal = styles.get("overflow-x") in ("auto", "scroll")
    if not (vertical or horizontal):
        return False
    return _has_overflowing_child(node)


def is_clickable(node: EnhancedNode) -> bool:
    if not node.is_element or _is_disabled(node):
        return False
    if node.tag in CLICKABLE_TAGS:
        return True
    if node.tag == "input" and _input_type(node) in CLICKABLE_INPUT_TYPES:
        return True
    if node.role in CLICKABLE_ROLES:
        return True
    if node.styles.get("cursor") == "pointer":
        has_label = bool(node.ax_name or node.text)
        if has_label and node.bbox:
            return True
    tabindex = node.attributes.get("tabindex")
    if tabindex is not None and tabindex.isdigit() and int(tabindex) >= 0:
        return True
    return False


def classify(node: EnhancedNode) -> str:
    """Return the primary interactive category, or '' if none."""
    if is_input(node):
        return "input"
    if is_draggable(node):
        return "drag"
    if is_scrollable(node):
        return "scroll"
    if is_clickable(node):
        return "click"
    return ""


def _has_overflowing_child(node: EnhancedNode) -> bool:
    if not node.bbox:
        return False
    nx, ny, nw, nh = node.bbox
    for child in _iter_descendants(node):
        if not child.bbox:
            continue
        cx, cy, cw, ch = child.bbox
        if cy + ch > ny + nh + 4 or cx + cw > nx + nw + 4:
            return True
        if cy < ny - 4 or cx < nx - 4:
            return True
    return False


def _iter_descendants(node: EnhancedNode):
    for child in node.children:
        yield child
        yield from _iter_descendants(child)
