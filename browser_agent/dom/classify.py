"""Classify enhanced nodes into the four interactive categories."""

from __future__ import annotations

from .build import PAGE_SCROLL_TAG, EnhancedNode

CLICKABLE_TAGS = {"a", "button", "summary", "option"}
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


def _has_text(node: EnhancedNode) -> bool:
    """True if ``node`` (or a descendant) carries non-empty text.

    Element nodes keep their own ``text`` empty; visible text lives in ``#text``
    child nodes. So a ``cursor:pointer`` ``<div>搜索职位</div>`` has a label only
    through its descendants, not through ``node.text``.
    """
    if node.text:
        return True
    for child in node.children:
        if child.is_text:
            if child.text:
                return True
        elif child.is_element and _has_text(child):
            return True
    return False


def has_svg_descendant(node: EnhancedNode) -> bool:
    """True if ``node`` contains an ``<svg>`` (i.e. looks like an icon)."""
    for child in node.children:
        if child.is_text:
            continue
        if child.is_element and child.tag == "svg":
            return True
        if has_svg_descendant(child):
            return True
    return False


def _has_following_pointer_labeled_sibling(node: EnhancedNode) -> bool:
    """True if a *following* sibling is clickable by ``cursor:pointer`` and labeled.

    Only following siblings count: a radio / checkbox control sits before its
    text label, whereas a trailing icon is usually a different action (expand /
    delete) that must not be relabeled as the row's control.
    """
    parent = node.parent
    if parent is None:
        return False
    seen = False
    for sibling in parent.children:
        if sibling is node:
            seen = True
            continue
        if not seen or not sibling.is_element:
            continue
        if sibling.hidden or not sibling.visible or not sibling.in_viewport:
            continue
        if sibling.styles.get("cursor") != "pointer":
            continue
        if sibling.ax_name or _has_text(sibling):
            return True
    return False


def is_control_icon(node: EnhancedNode) -> bool:
    """True if ``node`` is an unlabeled ``cursor:pointer`` icon that *precedes*
    a labeled ``cursor:pointer`` sibling.

    Component libraries render such rows as ``[icon][label]`` (a radio / checkbox
    circle before the option text). The icon is the *real, separately-callable
    control* (clicking it selects), while the label owns its own action (expand /
    navigate) or is a no-op. Naming the icon lets the LLM address the control
    directly.
    """
    if not node.is_element or _is_disabled(node):
        return False
    if node.styles.get("cursor") != "pointer":
        return False
    if node.ax_name or _has_text(node):
        return False
    if not node.bbox:
        return False
    if not has_svg_descendant(node):
        return False
    return _has_following_pointer_labeled_sibling(node)


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
        has_label = bool(node.ax_name or _has_text(node))
        if has_label and node.bbox:
            return True
    tabindex = node.attributes.get("tabindex")
    if tabindex is not None and tabindex.isdigit() and int(tabindex) >= 0:
        return True
    if is_control_icon(node):
        return True
    return False


def has_text(node: EnhancedNode) -> bool:
    """Public alias for ``_has_text``: does ``node`` (or a descendant) have text?"""
    return _has_text(node)


def is_cursor_pointer_only(node: EnhancedNode) -> bool:
    """True if ``node`` is clickable *only* because of ``cursor:pointer``.

    Such an element carries no semantic click signal (tag / role / action
    ``input`` / ``tabindex``) — typically a plain ``<span>`` label styled with
    ``cursor:pointer``. Some component libraries render a control as an
    unlabeled icon followed by exactly such a label (radio / checkbox rows),
    where the label is a no-op and the icon is the real, selectable control.
    The caller uses this to detect that brittle case and retry on the icon.
    """
    if not node.is_element or _is_disabled(node):
        return False
    if node.tag in CLICKABLE_TAGS:
        return False
    if node.tag == "input" and _input_type(node) in CLICKABLE_INPUT_TYPES:
        return False
    if node.role in CLICKABLE_ROLES:
        return False
    tabindex = node.attributes.get("tabindex")
    if tabindex is not None and tabindex.isdigit() and int(tabindex) >= 0:
        return False
    return node.styles.get("cursor") == "pointer"


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
