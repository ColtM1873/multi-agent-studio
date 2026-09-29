"""Classify enhanced nodes into the four interactive categories."""

from __future__ import annotations

import re

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

# Action words inside a ``class`` / ``id`` token that, together with an icon and
# a visible text label, identify a self-drawn button. Component libraries often
# render such controls as::
#
#     <div class="createFormSection-addBtn addMore__d36c7e">
#         <i class="anticon addMore-plus"><svg…/></i>
#         <span class="addMore-add">添加</span>
#     </div>
#
# where the *only* interactivity cue is a ``:hover`` style
# (``…createFormSection-empty:hover{cursor:pointer}``): the static computed
# ``cursor`` stays ``auto`` until the pointer is over the element, so the
# ``cursor:pointer`` branch below cannot see it, and there is no ``role`` /
# ``tabindex`` / inline handler either. Requiring an action word in the class/id
# (plus an icon, a label and no already-interactive descendant) keeps purely
# decorative "icon + text" markup out while still exposing the real control.
_ACTION_CLASS_WORDS = {
    "add", "addbtn", "addmore", "new", "create",
    "remove", "delete", "del", "edit", "save", "submit", "cancel",
    "search", "upload", "download", "refresh", "reload",
    "close", "expand", "collapse", "filter", "sort", "share", "copy",
    "plus", "minus", "play", "pause", "clear", "reset",
}

# CamelCase / PascalCase aware word splitter for class/id tokens
# (``createFormSection-addBtn`` → create|form|section|add|btn).
_CLASS_WORD_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def _class_has_action_word(node: EnhancedNode) -> bool:
    """True if ``node``'s ``class``/``id`` contains a known action word."""
    if not node.is_element:
        return False
    raw = f"{node.attributes.get('class', '')} {node.attributes.get('id', '')}"
    for token in raw.split():
        for part in re.split(r"[-_]+", token):
            for word in _CLASS_WORD_RE.findall(part):
                if word.lower() in _ACTION_CLASS_WORDS:
                    return True
    return False


def is_hover_action_control(node: EnhancedNode) -> bool:
    """True for an icon + label action button whose cursor is set only on hover.

    A self-drawn ``<div class="…-addBtn"><svg/><span>添加</span></div>`` has no
    native control tag, ARIA role, ``tabindex`` or inline handler, and its
    ``cursor:pointer`` only applies while hovered — so every other branch of
    ``is_clickable`` misses it and the LLM cannot address it. Guard rails: the
    element must carry an action word in its class/id, wrap an ``<svg>`` icon and
    a text label, have a box, and contain no already-interactive descendant (so
    composite containers / search bars wrapping a real input are not named).
    """
    if not node.is_element or _is_disabled(node):
        return False
    if not node.bbox:
        return False
    if not has_svg_descendant(node):
        return False
    if _has_interactive_descendant(node):
        return False
    if not (node.ax_name or _has_text(node)):
        return False
    return _class_has_action_word(node)


# Inline event-handler attributes that unambiguously mean "this element *is* a
# control". Only click-like handlers count; hover handlers (``onmouseover`` /
# ``onmouseout``, used purely for CSS class swaps) are deliberately excluded so
# an element that merely inherits ``cursor:pointer`` is still treated as such.
_CLICK_HANDLER_ATTRS = (
    "onclick",
    "onmousedown",
    "onmouseup",
    "ontouchstart",
    "ontouchend",
    "onpointerdown",
    "onpointerup",
)


def has_inline_click_handler(node: EnhancedNode) -> bool:
    """True if ``node`` carries an inline click-like handler attribute.

    Many legacy widgets (My97 date pickers, Beisen ``basSelect`` lists, plain
    ``<td onclick=...>`` grids) bind their action through an inline handler on
    an element that has no ``href`` / ARIA role / ``tabindex``. Such an element
    is a genuine, separately-callable control even though its only *style* cue
    is an inherited ``cursor:pointer``; callers that demote "cursor-only" shapes
    must not discard it.
    """
    if not node.is_element:
        return False
    attrs = node.attributes
    return any(name in attrs for name in _CLICK_HANDLER_ATTRS)


# Component libraries mark a disabled *widget* with a class suffix rather than
# the HTML ``disabled`` attribute (Ant Design ``.ant-select-disabled`` /
# ``.ant-input-disabled`` / ``.ant-picker-disabled``). The class lives on the
# wrapper whose own ``cursor`` may still be ``pointer``; only the inner parts get
# ``cursor: not-allowed``. Without recognising this, the wrapper stayed
# "clickable" and was exposed to the LLM — which then clicked and tried to fill a
# control the page (and the mouse pointer) treats as forbidden.
_DISABLED_CLASS_SUFFIXES = ("-disabled", "--disabled", "_disabled")
_DISABLED_CLASS_TOKENS = {"disabled", "is-disabled", "true-disabled"}


def _class_marks_disabled(value: str) -> bool:
    for token in (value or "").split():
        low = token.lower()
        if low in _DISABLED_CLASS_TOKENS or low.endswith(_DISABLED_CLASS_SUFFIXES):
            return True
    return False


def _node_marks_disabled(node: EnhancedNode) -> bool:
    if "disabled" in node.attributes:
        return True
    if node.attributes.get("aria-disabled") == "true":
        return True
    if _class_marks_disabled(node.attributes.get("class", "")):
        return True
    # The ``cursor: not-allowed`` the disabled rules ultimately paint (captured by
    # the computed-style snapshot) is the most uniform cross-framework signal.
    if node.styles.get("cursor") == "not-allowed":
        return True
    return False


def _is_disabled(node: EnhancedNode) -> bool:
    """True if ``node`` or a disabled widget ancestor is non-interactive.

    Besides the node's own ``disabled`` / ``aria-disabled`` (and ``<fieldset
    disabled>`` via the ancestor walk), this recognises component-library
    ``*-disabled`` classes and the ``cursor: not-allowed`` they produce. A
    disabled wrapper's own cursor often stays ``pointer``, so only an ancestor
    walk exposes the true state.
    """
    hops = 0
    while node is not None and hops < 10:
        if node.is_element and _node_marks_disabled(node):
            return True
        node = node.parent
        hops += 1
    return False


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


def is_select(node: EnhancedNode) -> bool:
    """True for a native ``<select>`` dropdown.

    A native select is *not* a plain clickable: clicking it only opens a
    browser/OS-level popup that is not part of the DOM (so no diff is produced),
    and its real operation is "choose one of N options". Giving it its own
    category lets the serializer label it and the controller route ``fill`` to a
    real option-selection primitive instead of a dead click.
    """
    if not node.is_element or _is_disabled(node):
        return False
    return node.tag == "select"


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


def _is_anchor_clickable(node: EnhancedNode) -> bool:
    """True if an ``<a>`` is a real link / carries a click signal.

    A bare ``<a>`` with no ``href`` is frequently used as a decorative wrapper
    (e.g. the required-field ``*`` mark: ``<a class="form-item__required">``
    holding only an ``<svg>``). Treating every ``<a>`` as clickable gave those
    marks a name with no label ("``<可点击元素 eN></可点击元素 eN>``"). An
    ``<a>`` is only a link when it has an ``href`` (even empty), or when it
    carries another click signal (an inline ``on*`` handler, an ARIA role,
    ``cursor:pointer`` or ``tabindex`` — the generic checks below).
    """
    if node.attributes.get("href") is not None:
        return True
    return any(name.lower().startswith("on") for name in node.attributes)


def is_clickable(node: EnhancedNode) -> bool:
    if not node.is_element or _is_disabled(node):
        return False
    if node.tag == "a":
        if _is_anchor_clickable(node):
            return True
    elif node.tag in CLICKABLE_TAGS:
        return True
    if node.tag == "input" and _input_type(node) in CLICKABLE_INPUT_TYPES:
        return True
    if node.role in CLICKABLE_ROLES:
        return True
    if node.styles.get("cursor") == "pointer":
        has_label = bool(node.ax_name or _has_text(node))
        if has_label and node.bbox:
            # A clickable element whose only content is a field ``<label>`` (and
            # which wraps no interactive control) is a *field description*, not a
            # control: naming it produces a bogus "clickable" duplicate of the
            # field name (Ant Design marks a whole form row ``cursor:pointer``,
            # which every descendant inherits, so labels/``.ant-col`` columns
            # would otherwise all be named). A ``<label>`` that *does* wrap the
            # real control (hidden checkbox / radio) keeps its descendant, which
            # is what gets named, so excluding it here is safe.
            if _has_label_element(node) and not _has_interactive_descendant(node):
                return False
            return True
    tabindex = node.attributes.get("tabindex")
    if tabindex is not None and tabindex.isdigit() and int(tabindex) >= 0:
        return True
    if has_inline_click_handler(node):
        # A genuine inline click handler is a semantic click signal even when
        # the element is not a native control and its CSS cursor is ``auto``.
        # My97 calendar day cells are ``<td onclick="day_Click(...)">``; the
        # "today / selected" cell carries no ``cursor:pointer`` (its class
        # ``Wselday`` only sets a background), so without this it was the one
        # day a user could not click. Mirror the label guard used for the
        # ``cursor:pointer`` branch so a clickable *form row* is still not named.
        if node.bbox:
            has_label = bool(node.ax_name or _has_text(node))
            if has_label:
                if _has_label_element(node) and not _has_interactive_descendant(node):
                    return False
                return True
    if is_hover_action_control(node):
        return True
    if is_control_icon(node):
        return True
    return False


def _has_label_element(node: EnhancedNode) -> bool:
    """True if ``node`` is, or contains, a ``<label>`` element."""
    if node.is_element and node.tag == "label":
        return True
    for child in node.children:
        if child.is_element and _has_label_element(child):
            return True
    return False


def _has_interactive_descendant(node: EnhancedNode) -> bool:
    """True if any visible descendant of ``node`` is itself interactive."""
    for child in node.children:
        if child.is_text or not child.is_element:
            continue
        if child.hidden or not child.visible:
            continue
        if classify(child):
            return True
        if _has_interactive_descendant(child):
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
    if node.tag == "a":
        if _is_anchor_clickable(node):
            return False
    elif node.tag in CLICKABLE_TAGS:
        return False
    if node.tag == "input" and _input_type(node) in CLICKABLE_INPUT_TYPES:
        return False
    if node.role in CLICKABLE_ROLES:
        return False
    tabindex = node.attributes.get("tabindex")
    if tabindex is not None and tabindex.isdigit() and int(tabindex) >= 0:
        return False
    return node.styles.get("cursor") == "pointer"


def _inside_form(node: EnhancedNode) -> bool:
    """True if ``node`` has a ``<form>`` ancestor (so submit controls navigate)."""
    current = node.parent
    while current is not None:
        if current.is_element and current.tag == "form":
            return True
        current = current.parent
    return False


# ``href`` values that never cause a document navigation.
_NON_NAV_HREF_PREFIXES = ("javascript:", "#", "mailto:", "tel:", "sms:", "blob:")


def may_navigate(node: EnhancedNode) -> bool:
    """Heuristic: could clicking ``node`` trigger a *document* navigation?

    Used by the controller to decide how long to wait for a navigation to begin.
    Elements that can navigate (links, form submit controls, ``role=link``) keep
    the full grace window; everything else (plain buttons / icons / ``cursor``
    divs) uses a short grace — they almost never navigate, and waiting the full
    window is the single largest source of click latency. This is deliberately
    conservative: when unsure it returns ``False`` only for genuinely inert
    shapes, and same-document (``#``) navigations are still caught by the
    ``navigatedWithinDocument`` event regardless of this result.
    """
    if node is None or not node.is_element or _is_disabled(node):
        return False
    attrs = node.attributes or {}
    tag = (node.tag or "").lower()

    if tag in ("a", "area"):
        if attrs.get("download") is not None:
            return False
        href = (attrs.get("href") or "").strip().lower()
        if href and not href.startswith(_NON_NAV_HREF_PREFIXES):
            return True

    if tag in ("button", "input") and _inside_form(node):
        itype = (attrs.get("type") or ("submit" if tag == "button" else "")).lower()
        if itype in ("submit", "image"):
            return True

    if attrs.get("formaction"):
        return True
    if (node.role or "") == "link":
        return True
    return False


def is_searchable_typeahead(node: EnhancedNode) -> bool:
    """True for an *editable* combobox / autocomplete input.

    This is the typeahead of a searchable select (``show-search``): the user
    types here and the candidate list filters live. Typing only *filters* — the
    value is committed only by clicking a candidate — so it is given its own
    category (``searchable``) and driven by ``tool_07_searchable_dropdown``
    instead of being treated as an ordinary ``<可输入元素>``.

    The ``role=combobox`` / ``aria-autocomplete`` signal frequently lives on an
    *ancestor* rather than on the ``<input>`` itself (Ant Design's
    ``show-search`` select puts it on the wrapping ``.atsx-select-selection``).
    Keying on ``aria-autocomplete`` — not a bare ``role=combobox`` — keeps
    non-searchable composite selects (the ``.phoenix-select`` typeahead, whose
    container carries no ``aria-autocomplete``) as a single entry, while
    exposing the real filter box of a searchable one.
    """
    if node.tag != "input":
        return False
    if "readonly" in node.attributes:
        return False
    if node.attributes.get("type", "text").lower() == "hidden":
        return False
    if node.role == "combobox":
        return True
    if node.attributes.get("aria-autocomplete"):
        return True
    ancestor = node.parent
    hops = 0
    while ancestor is not None and hops < 5:
        if ancestor.is_element and ancestor.attributes.get("aria-autocomplete"):
            return True
        ancestor = ancestor.parent
        hops += 1
    return False


def classify(node: EnhancedNode) -> str:
    """Return the primary interactive category, or '' if none."""
    if is_searchable_typeahead(node) and not _is_disabled(node):
        # A searchable select's filter box outranks ``input``: typing only
        # narrows the candidate list, so it must not be routed to the plain
        # fill-in tool (``tool_02``).
        return "searchable"
    if is_input(node):
        return "input"
    if is_select(node):
        return "select"
    if is_draggable(node):
        return "drag"
    if is_scrollable(node):
        return "scroll"
    if is_clickable(node):
        return "click"
    return ""


def _clips_overflow(node: EnhancedNode) -> bool:
    """True if ``node`` establishes its own clipping/scroll context.

    A descendant with ``overflow != visible`` clips whatever is inside it, so
    content that lies *below* such a descendant can never actually overflow the
    ancestor being tested. Skipping those subtrees is what keeps a *nesting* of
    scroll containers (an Ant ``<select>`` renders ``dropdown > div[overflow:auto]
    > ul[overflow:auto]``) from marking every ancestor as scrollable: the inner
    ``<li>`` bboxes are clipped by the ``<ul>``, yet their unbounded layout boxes
    used to make the two outer wrappers look scrollable too. Only the element
    that really clips the content is then named ``<可滚动元素>``.
    """
    return (
        node.styles.get("overflow-x") in ("auto", "scroll", "hidden", "clip")
        or node.styles.get("overflow-y") in ("auto", "scroll", "hidden", "clip")
    )


def _has_overflowing_child(node: EnhancedNode) -> bool:
    if not node.bbox:
        return False
    nx, ny, nw, nh = node.bbox
    for child in _iter_unclipped_descendants(node):
        if not child.bbox:
            continue
        cx, cy, cw, ch = child.bbox
        if cy + ch > ny + nh + 4 or cx + cw > nx + nw + 4:
            return True
        if cy < ny - 4 or cx < nx - 4:
            return True
    return False


def _iter_unclipped_descendants(node: EnhancedNode):
    """Descendants whose layout box can actually overflow ``node``.

    Yields every child (its own bbox is compared), but does not descend into a
    child that clips its own overflow (see ``_clips_overflow``): that child's
    inner content is contained by the child, not by ``node``.
    """
    for child in node.children:
        yield child
        if child.is_element and not _clips_overflow(child):
            yield from _iter_unclipped_descendants(child)
