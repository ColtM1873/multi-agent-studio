"""Classify enhanced nodes into the four interactive categories."""

from __future__ import annotations

import re

from .build import PAGE_SCROLL_TAG, EnhancedNode, _OVERLAY_ROLES

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


# Class/id signatures of JS scrollbar libraries. These libraries set native
# ``overflow: hidden`` and draw their own bar, so the ``overflow == hidden`` shape
# is the *only* signal that a scroll capability exists — but that shape also
# matches ordinary layout containers and clipped notices (Feishu
# ``resumeFormPage`` / ``ud__notice``) whose children merely overflow the box by
# layout, which are **not** scrollable and whose ``<可滚动元素>`` only invited a
# wasted "scrolled to top, no new content" call (session 2026-10-01, ``e57``).
# Requiring the library's own class token keeps the real candidate columns
# (perfect-scrollbar ``.scrollbar-container``, Element ``el-scrollbar``, Ant
# ``rc-virtual-list``) while dropping the layout-container false positives.
_SCROLL_LIBRARY_TOKENS = (
    "scrollbar",
    "scroll-view",
    "scrollview",
    "scroll-container",
    "overflow-scroll",
    "virtual-list",
    "virtual-scroll",
    "rc-virtual",
    "el-scrollbar",
    "perfect-scrollbar",
    "ps-container",
    "nicescroll",
    "nice-scroll",
    "mcustomscrollbar",
    "simplebar",
    "i-scroll",
)


def _has_scroll_library_signature(node: EnhancedNode) -> bool:
    raw = _raw_class_id(node)
    return bool(raw.strip()) and any(tok in raw for tok in _SCROLL_LIBRARY_TOKENS)


def is_scrollable(node: EnhancedNode) -> bool:
    if not node.is_element:
        return False
    if node.tag == PAGE_SCROLL_TAG:
        return True
    if not node.bbox:
        return False
    if node.bbox[2] <= 1 or node.bbox[3] <= 1:
        # A zero / one-pixel box can never be scrolled by a user; DOMSnapshot also
        # reports 0×0 for ``<svg>``/``<symbol>`` which carry ``overflow:hidden``
        # and previously passed as bogus ``<可滚动元素>``.
        return False
    if node.tag in ("html", "body"):
        return False
    styles = node.styles
    vertical = styles.get("overflow-y") in ("auto", "scroll")
    horizontal = styles.get("overflow-x") in ("auto", "scroll")
    if not (vertical or horizontal):
        # ``overflow: hidden`` **with content that overflows** is a scroll container
        # too — it is what every JS scrollbar library uses (perfect-scrollbar
        # ``.scrollbar-container``, Element's ``el-scrollbar``, Ant's
        # ``rc-virtual-list``): native ``overflow`` is hidden because the library
        # draws its own bar, while the content inside is really scrollable (the
        # wheel handler moves it). Without this, a long candidate column of a
        # dropdown (Feishu 起止时间: 127 year options, content 4842px in a 333px
        # box) was invisible to the LLM as a scrollable, so it could not reach an
        # option below the fold — only the page scrollbar was offered. This branch
        # is gated on the library class token (see ``_SCROLL_LIBRARY_TOKENS``)
        # because bbox overflow alone also matches non-scrollable layout boxes.
        # ``clip`` is deliberately excluded: it forbids programmatic scrolling, so
        # naming it would only invite an honest "cannot scroll".
        # ``_has_overflowing_child`` remains the real "is there anything to
        # scroll" gate.
        if _has_scroll_library_signature(node):
            vertical = styles.get("overflow-y") == "hidden"
            horizontal = styles.get("overflow-x") == "hidden"
    if not (vertical or horizontal):
        return False
    return _has_overflowing_child(node)


def _has_text(node: EnhancedNode) -> bool:
    """True if ``node`` (or a descendant) carries non-empty text.

    Element nodes keep their own ``text`` empty; visible text lives in ``#text``
    child nodes. So a ``cursor:pointer`` ``<div>搜索职位</div>`` has a label only
    through its descendants, not through ``node.text``.

    结果按节点缓存：这是一个子树级扫描，而 ``is_clickable`` 等会对每个节点调用它，
    不缓存则序列化退化成 O(n×子树)。
    """
    cached = node.cache_has_text
    if cached is not None:
        return cached
    result = False
    if node.text:
        result = True
    else:
        for child in node.children:
            if child.is_text:
                if child.text:
                    result = True
                    break
            elif child.is_element and _has_text(child):
                result = True
                break
    node.cache_has_text = result
    return result


def has_svg_descendant(node: EnhancedNode) -> bool:
    """True if ``node`` contains an ``<svg>`` (i.e. looks like an icon)."""
    cached = node.cache_has_svg
    if cached is not None:
        return cached
    result = False
    for child in node.children:
        if child.is_text:
            continue
        if child.is_element and child.tag == "svg":
            result = True
            break
        if has_svg_descendant(child):
            result = True
            break
    node.cache_has_svg = result
    return result


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
    if is_picker_panel_cell(node):
        # A date / calendar picker's year / month / day cell: a real option that
        # only takes ``cursor:pointer`` on ``:hover`` (see the predicate).
        return True
    if is_hover_action_control(node):
        return True
    if is_picker_panel_nav(node):
        # The prev / next chevron of an open picker panel: a cursor:pointer,
        # label-less icon that no other branch recognises.
        return True
    if is_control_icon(node):
        return True
    return False


def _has_label_element(node: EnhancedNode) -> bool:
    """True if ``node`` is, or contains, a ``<label>`` element."""
    cached = node.cache_has_label_element
    if cached is not None:
        return cached
    result = False
    if node.is_element and node.tag == "label":
        result = True
    else:
        for child in node.children:
            if child.is_element and _has_label_element(child):
                result = True
                break
    node.cache_has_label_element = result
    return result


def _has_interactive_descendant(node: EnhancedNode) -> bool:
    """True if any visible descendant of ``node`` is itself interactive.

    按节点缓存（``classify`` ↔ 本函数的相互递归正是卡死的主因）。缓存后每个
    节点的 ``classify`` 与 ``_has_interactive_descendant`` 各只计算一次，整体
    摊还为 O(节点数)。
    """
    cached = node.cache_has_interactive_desc
    if cached is not None:
        return cached
    result = False
    for child in node.children:
        if child.is_text or not child.is_element:
            continue
        if child.hidden or not child.visible:
            continue
        if classify(child) or _has_interactive_descendant(child):
            result = True
            break
    node.cache_has_interactive_desc = result
    return result


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


# ---------------------------------------------------------------------------
# 自绘「下拉」控件的识别（可搜索下拉 / 可点击下拉）
#
# 组件库的自绘下拉（Moka ``sd-Select``、Ant ``.ant-select``、Beisen
# ``.phoenix-select``、Feishu ``.atsx-select``、Element ``.el-select`` …）把「下拉」
# 语义写在 class 名里，并在触发器里放一个朝下的箭头图标；它们没有统一的 ARIA
# （实测这些自绘控件 ``role`` / ``aria-*`` 常为 0）。因此用「class/id 令牌 +
# 下拉箭头」这条跨库信号来识别：
#   * 可编辑输入框充当控件的「筛选框」 → ``searchable``（输入只筛选，须点候选）
#   * 非输入的可点击触发器              → ``clickdropdown``（点击展开候选/菜单）
# ---------------------------------------------------------------------------

_SELECT_TOKENS = ("select", "combobox", "cascader")
_MENU_TOKENS = ("dropdown", "menu")
# A dropdown-*menu* trigger is a small control (a caret button), never a container
# that merely wraps the menu's items (a sidebar of links). Requiring an explicit
# trigger token keeps containers out.
_MENU_TRIGGER_TOKENS = (
    "toggle",
    "trigger",
    "menu-button",
    "menu-btn",
    "dropdown-btn",
    "dropdown-button",
    "dropdown-toggle",
    "dropdown-trigger",
    "selector",
    "switch",
    "button",
    "btn",
)
_INDICATOR_TOKENS = (
    "caret",
    "chevron",
    "arrow",
    "angle-down",
    "angle_down",
    "icon-down",
    "down-icon",
)


def _class_id_text(node: EnhancedNode) -> str:
    """Lower-cased ``class`` + ``id`` of ``node`` (for token / substring probes)."""
    if not node.is_element:
        return ""
    return (
        f"{node.attributes.get('class', '')} {node.attributes.get('id', '')}"
    ).lower()


def _has_token(text: str, tokens) -> bool:
    return any(tok in text for tok in tokens)


def _marks_select_control(node: EnhancedNode) -> bool:
    """True if ``node``'s class/id marks it as a select / combobox container."""
    return node.is_element and _has_token(_class_id_text(node), _SELECT_TOKENS)


def _contains_control(node: EnhancedNode) -> bool:
    """True if ``node``'s subtree holds a native control / link / editable region.

    A dropdown *trigger* wraps only an arrow / icon. A sibling that is itself a
    whole composite control (Ant Design's phone-country-code selector next to the
    phone ``<input>``) wraps an ``<input>``; without excluding it the plain text
    field beside it would be mistaken for a search box.
    """
    cached = node.cache_contains_control
    if cached is not None:
        return cached
    result = False
    for child in node.children:
        if child.is_text or not child.is_element:
            continue
        if child.tag in ("input", "select", "textarea", "button"):
            result = True
        elif (child.role or "").lower() in CLICKABLE_ROLES:
            result = True
        elif child.tag == "a" and child.attributes.get("href") is not None:
            result = True
        elif child.attributes.get("contenteditable") in ("", "true", "plaintext-only"):
            result = True
        elif _contains_control(child):
            result = True
        if result:
            break
    node.cache_contains_control = result
    return result


def _has_dropdown_indicator(node: EnhancedNode) -> bool:
    """True if ``node`` or a descendant carries a down-arrow / caret icon."""
    cached = node.cache_has_dropdown_indicator
    if cached is not None:
        return cached
    result = False
    if node.is_element and _has_token(_class_id_text(node), _INDICATOR_TOKENS):
        result = True
    else:
        for child in node.children:
            if child.is_element and _has_dropdown_indicator(child):
                result = True
                break
    node.cache_has_dropdown_indicator = result
    return result


def _descendant_has_dropdown_indicator(node: EnhancedNode) -> bool:
    """True if a *strict descendant* of ``node`` is a down-arrow indicator.

    Requiring a descendant (not the node itself) keeps the arrow / addon *part*
    (``.ant-select-arrow``, ``.sd-Select-addon`` whose own signal is the element
    itself) from being mistaken for the control root.
    """
    for child in node.children:
        if child.is_element and _has_dropdown_indicator(child):
            return True
    return False


# ---------------------------------------------------------------------------
# 日期 / 时间选择器（date|month|time picker）的文本输入框
#
# 日历/年月面板与自绘下拉是同一类交互语义：**输入只用于导航或筛选，必须点面板里的
# 单元格才真正作数**（Element Plus 的 ``.el-date-editor``、Ant 的 ``.ant-picker``、
# Moka / Beisen / 飞书的 ``*-date-picker`` 都是这个形状）。所以它的文本输入框与
# 「可搜索下拉」共用 ``searchable`` 类别；只读的那种（不能打字，只能点开面板）与
# 「可点击下拉」共用 ``clickdropdown`` 类别。
#
# 识别信号是**外壳的 class/id**（组件库把这些控件命名得很明确），而不是输入框自身：
#   * 明确写了组件名 → 直接认定（``el-date-editor`` / ``ant-picker`` / ``date-picker`` …）
#   * 只是「日期词 + 容器词」的普通组合 → 还要有弹层契约（``aria-haspopup`` 等）
#     ＋ 同壳里的指示图标，避免把「名字里带 date 的普通文本框」当成选择器而
#     封掉它唯一正确的操作（直接键入日期）。
# ---------------------------------------------------------------------------
_PICKER_COMPONENT_MARKERS = (
    "date-editor",
    "dateeditor",
    "date-picker",
    "datepicker",
    "date_picker",
    "datetime-picker",
    "datetimepicker",
    "time-picker",
    "timepicker",
    "time-select",
    "month-picker",
    "year-picker",
    "calendar-picker",
    "calendar-panel",
    "ant-picker",
    "ant-calendar",
    "rc-picker",
    "el-date",
    "el-range-editor",
    "el-time",
    "sd-date",
    "phoenix-date",
    "atsx-date",
    "moka-date",
    "flatpickr",
    "react-datepicker",
    "v-calendar",
    "pikaday",
    "litepicker",
    "duet-date",
)
# 「日期感」的词。``month`` / ``year`` / ``day`` 单独出现太泛（``day_info`` 只是「这块
# 是某字段」的布局类名，实测把 Moka 表单的 ``apply-field-… day_info-…`` 误判成日期外壳），
# 所以只保留日期语义明确的写法；连写形式（``daterange`` / ``monthrange``）直接放进来。
_DATE_WIDGET_TOKENS = (
    "date",
    "datetime",
    "calendar",
    "time",
    "daterange",
    "datetimerange",
    "monthrange",
    "yearrange",
)
# 容器词同样只收「真选择器外壳」的写法；``box`` / ``field`` 这类布局词会把
# 「日期字段的整块容器」也算进来，故剔除。
_DATE_CONTAINER_TOKENS = (
    "picker",
    "editor",
    "selector",
    "range",
    "select",
    "panel",
)
# 指示图标（同壳里的兄弟元素）上的词：下拉箭头 / 日历 / 时钟。刻意不收 ``picker`` /
# ``date`` / ``time`` 这类「名字里带日期」的普通类名（``sd-picker-addon`` 只是加号槽），
# 只有真正的指示图形才算。
_PICKER_INDICATOR_TOKENS = (
    "calendar",
    "clock",
    "caret",
    "chevron",
    "arrow",
    "icon-down",
    "down-icon",
)


def _raw_class_id(node: EnhancedNode) -> str:
    """Lower-cased ``class`` + ``id`` of ``node`` (empty for non-elements)."""
    if not node.is_element:
        return ""
    return f"{node.attributes.get('class', '')} {node.attributes.get('id', '')}".lower()


def _is_named_picker_shell(node: EnhancedNode) -> bool:
    """True if ``node``'s class/id spells out a known date/time picker component."""
    raw = _raw_class_id(node)
    if not raw.strip():
        return False
    return any(marker in raw for marker in _PICKER_COMPONENT_MARKERS)


def _is_generic_picker_shell(node: EnhancedNode) -> bool:
    """True if ``node``'s class/id is a ``date|calendar|time`` + container combo.

    Deliberately loose (it catches pickers of libraries we have never seen), so
    callers must additionally require a popup signal before trusting it.
    """
    raw = _raw_class_id(node)
    if not raw.strip():
        return False
    tokens = set(re.split(r"[^a-z0-9]+", raw))
    return bool(tokens & set(_DATE_WIDGET_TOKENS)) and bool(
        tokens & set(_DATE_CONTAINER_TOKENS)
    )


def _has_popup_contract(node: EnhancedNode) -> bool:
    """True if ``node`` declares the ARIA popup contract (opens a panel)."""
    if not node.is_element:
        return False
    if node.attributes.get("aria-haspopup"):
        return True
    if node.attributes.get("aria-expanded") is not None:
        return True
    return (node.role or "").lower() in ("combobox", "button")


def _subtree_has_picker_indicator(node: EnhancedNode, depth: int = 2) -> bool:
    """True if ``node`` (or a shallow descendant) is an indicator icon.

    The marker is the class/id token *or* an ``aria-label`` / ``title`` (Ant
    Design's ``<span role="img" aria-label="calendar">`` carries the calendar only
    in the attribute).
    """
    if not node.is_element or depth < 0:
        return False
    if _has_token(_raw_class_id(node), _PICKER_INDICATOR_TOKENS):
        return True
    hint = " ".join(
        (node.attributes.get(key) or "")
        for key in ("aria-label", "title", "data-icon", "data-name")
    ).lower()
    if hint.strip() and _has_token(hint, _PICKER_INDICATOR_TOKENS):
        return True
    for child in node.children:
        if child.is_element and _subtree_has_picker_indicator(child, depth - 1):
            return True
    return False


def _has_picker_indicator_in_shell(node: EnhancedNode, max_hops: int = 2) -> bool:
    """True if an indicator icon sits beside ``node`` (or its wrapper's) siblings.

    Looks for a sibling that is not the branch itself and that carries (or shallowly
    contains) a dropdown arrow / calendar / clock icon — ``<span class="el-input__suffix">
    <i class="icon-arrow-down">`` and ``<i class="el-range__icon">`` are both this
    shape.
    """
    branch = node
    ancestor = node.parent
    hops = 0
    while ancestor is not None and hops < max_hops:
        for sibling in ancestor.children:
            if sibling is branch or sibling.is_text or not sibling.is_element:
                continue
            if _subtree_has_picker_indicator(sibling, depth=2):
                return True
        branch = ancestor
        ancestor = ancestor.parent
        hops += 1
    return False


def _iter_picker_shells(node: EnhancedNode, max_hops: int = 4):
    """Yield ``(ancestor, branch)`` pairs up the tree, nearest first."""
    branch = node
    ancestor = node.parent
    hops = 0
    while ancestor is not None and hops < max_hops:
        yield ancestor, branch
        branch = ancestor
        ancestor = ancestor.parent
        hops += 1


def _picker_shell_node(node: EnhancedNode, max_hops: int = 4):
    """The nearest date/time picker *shell* wrapping ``node``, else ``None``."""
    if _is_named_picker_shell(node):
        return node
    for ancestor, branch in _iter_picker_shells(node, max_hops):
        if not ancestor.is_element:
            continue
        if _is_named_picker_shell(ancestor):
            return ancestor
        if _is_generic_picker_shell(ancestor) and (
            _has_popup_contract(ancestor)
            or _has_picker_indicator_in_shell(branch)
        ):
            return ancestor
    return None


def _picker_shell(node: EnhancedNode, max_hops: int = 4) -> bool:
    """True if a date/time picker shell wraps ``node`` (≤ ``max_hops`` up)."""
    return _picker_shell_node(node, max_hops) is not None


def _shell_text_inputs(shell: EnhancedNode, limit: int = 8) -> list:
    """Text-like ``<input>``s of a picker shell, in document order.

    A *range* picker (``日期从 ___ 到 ___``) is one control holding two of these.
    ``limit`` bounds the scan: only the *count* matters, so a huge shell is not
    walked to the end.
    """
    found: list = []

    def walk(node: EnhancedNode) -> None:
        if len(found) >= limit:
            return
        for child in node.children:
            if child.is_text or not child.is_element:
                continue
            if child.tag == "input":
                itype = (child.attributes.get("type") or "text").lower()
                if itype in ("", "text", "search", "email"):
                    found.append(child)
            walk(child)

    walk(shell)
    return found


def picker_range_position(node: EnhancedNode):
    """``(index, total)`` of ``node`` among its picker range shell's text inputs.

    Returns ``None`` unless a picker shell around ``node`` holds **two or more**
    text inputs — i.e. a *range* control (``日期从 __ 到 __``；Element Plus
    ``monthrange`` / Ant ``RangePicker``). Such a control is **one** widget driven
    by **one** panel and it commits only after **two** candidates were picked
    **inside the same overlay session**; the widget sorts the pair (earlier =
    start), and a single pick followed by a blur rolls *both* ends back. Miss this
    fact and the two inputs look like two independent fields — the model then
    expects one pick to commit (it does not) or leaves the overlay after the first
    pick (discarding it), which is exactly how the 4399 简历 session burned
    thousands of tokens.

    The climb matters: Ant Design nests ``.ant-picker > .ant-picker-input`` and the
    *inner* div is marker-matched too, so the nearest shell alone would report
    ``total == 1`` and lose the range fact. Walk outwards while the ancestors are
    still picker shells and take the first shell that really holds two ends.
    """
    shell = _picker_shell_node(node)
    if shell is None:
        return None
    current = shell
    hops = 0
    while current is not None and hops < 4:
        inputs = _shell_text_inputs(current)
        if len(inputs) >= 2:
            for index, candidate in enumerate(inputs):
                if candidate is node:
                    return (index, len(inputs))
        parent = current.parent
        if parent is None or not parent.is_element:
            break
        if not (_is_named_picker_shell(parent) or _is_generic_picker_shell(parent)):
            break
        current = parent
        hops += 1
    return None


def is_range_picker_entry(node: EnhancedNode) -> bool:
    """True if ``node`` is the entry of a two-end picker *range* control.

    Either one text input of an input-based range (Element Plus ``monthrange`` /
    Ant ``RangePicker``), or the whole div-based picker shell (Feishu Jobs
    「起止时间」). Both are **one** widget with **one** panel that only commits
    after two candidate picks inside the same overlay session.
    """
    return picker_range_position(node) is not None or is_range_picker_shell(node)


# ---------------------------------------------------------------------------
# 「div 型」弹层选择控件：整个外壳才是控件，隐藏 input 只是内部件
#
# 飞书招聘把「起止时间」渲染成
#   ``<div class="atsx-date-picker atsx-date-picker-period-month">
#        <div class="…-period-month-label">2017-09</div>
#        <div class="…-period-line"></div>
#        <div class="…-period-month-label">2021-06</div>
#        <input class="…-period-hidden-input" style="width:0;height:0"></div>``
# 两个可见的年月文本才是「当前值」，真正能打字的输入框**没有**。以前只有
# ``<input>`` 才被当成选择器入口，于是这个 0×0 的隐藏 input 被命名为
# ``<可搜索下拉元素>``、标签取自它**左边那个值部件**（``2017-09：（空）``），
# 而右端的 ``2021-06`` 又作为一条游离的裸文本/可点击元素出现；``tool_07`` 往里
# 打字没有任何候选、``tool_01``/``tool_02`` 又都拒绝并把它推回 ``tool_07``，模型
# 就此在「到底填上了没有」上烧掉近万推理 token（详见 inner_docs/ID123）。
#
# 正解：外壳**就是**那个控件，而且是「点击展开面板」型 —— 人类操作它时也没有
# 输入的机会，只有点开面板再点格子，所以类别与「可点击下拉」共用（``clickdropdown``），
# 标签与当前值都由序列化层从外壳的可见文本里取。
# ---------------------------------------------------------------------------
# 弹层本体（面板/候选列表）自带这些词：它虽然也叫 ``*date-picker*``，但它是**被展开的
# 东西**，不是触发器，必须排除，否则面板会被命名成一个控件、把格子全吞掉。
_PICKER_PANEL_TOKENS = (
    "panel",
    "dropdown",
    "popup",
    "popper",
    "overlay",
    "menu",
    "list",
    "table",
)
# 「两端」语义：区间控件的外壳名字里通常明说（``range`` / ``period``）。
_RANGE_SHELL_TOKENS = ("range", "period", "interval", "between")


def has_usable_text_input(node: EnhancedNode) -> bool:
    """True if ``node``'s subtree holds a text input with a real (non-zero) box.

    ``DOMSnapshot`` omits ``display:none`` nodes and reports a 0×0 box for an
    element that is laid out but has no area, so "usable" means *laid out with an
    area*. Element Plus / Ant / Moka / Beisen date pickers all render a real
    ``<input>`` here and must keep their existing per-input handling; only a shell
    whose inputs are all zero-area (Feishu's ``…-period-hidden-input``) falls into
    the "the shell *is* the control" branch.
    """
    cached = node.cache_usable_text_input
    if cached is not None:
        return cached
    result = False
    stack = list(node.children)
    seen = 0
    while stack and seen < 300:
        cur = stack.pop()
        seen += 1
        if not cur.is_element:
            continue
        if cur.tag == "input" and _input_type(cur) in ("", "text", "search", "email"):
            box = cur.bbox
            if box and box[2] > 1 and box[3] > 1 and not cur.hidden:
                result = True
                break
            continue
        stack.extend(cur.children)
    node.cache_usable_text_input = result
    return result


def is_picker_shell_trigger(node: EnhancedNode) -> bool:
    """True for a click-to-open picker shell that has **no usable text entry**.

    Narrow by construction: the node's own class/id must spell a known picker
    component, it must not be the popped-up panel, and none of its inputs may have
    a real box. Any picker with a typeable input (Element/Ant/Moka/Beisen) is
    therefore untouched.
    """
    cached = node.cache_picker_shell
    if cached is not None:
        return cached

    def compute() -> bool:
        if not node.is_element or node.tag in ("input", "select", "textarea"):
            return False
        if node.hidden or not node.visible:
            return False
        box = node.bbox
        if not box or box[2] <= 1 or box[3] <= 1:
            return False
        if node.styles.get("position") in ("absolute", "fixed"):
            return False
        if (node.role or "").lower() in _OVERLAY_ROLES:
            return False
        raw = _class_id_text(node)
        if any(tok in raw for tok in _PICKER_PANEL_TOKENS):
            return False
        if not _is_named_picker_shell(node) and not (
            _is_generic_picker_shell(node) and _has_picker_indicator_in_shell(node)
        ):
            return False
        # Only the **outermost** element that spells a picker component is the
        # widget: Ant Design names every part ``ant-picker-…``, so ``-input`` /
        # ``-suffix`` / ``-separator`` / ``-header`` / ``-body`` / ``-panel`` all
        # matched the marker, and claiming them turned a calendar panel's header
        # and day grid into two bogus "clickable dropdowns" (caught by replaying
        # the 快手 campus-application capture from 20260924).
        ancestor = node.parent
        hops = 0
        while ancestor is not None and hops < 4:
            if _is_named_picker_shell(ancestor):
                return False
            ancestor = ancestor.parent
            hops += 1
        return not has_usable_text_input(node)

    result = compute()
    node.cache_picker_shell = result
    return result


def is_range_picker_shell(node: EnhancedNode) -> bool:
    """True if a click-to-open picker shell is a two-end *range* control."""
    if not is_picker_shell_trigger(node):
        return False
    return any(tok in _class_id_text(node) for tok in _RANGE_SHELL_TOKENS)


# Value-part markers that name an *end* of a div-based range shell. ``date`` is
# deliberately excluded even though ``serialize._value_part_tokens`` keeps it:
# every part of ``atsx-date-picker-period-…`` carries ``date`` in its component
# name (including the ``-line`` separator and the hidden input), so using it
# would count non-end children as ends.
_RANGE_END_TOKENS = ("year", "month", "day", "today", "totoday")


def _raw_attr_text(node: EnhancedNode) -> str:
    return (
        f"{node.attributes.get('class', '')} "
        f"{node.attributes.get('id', '')} "
        f"{node.attributes.get('data-cy', '')}"
    ).lower()


def _has_range_end_token(node: EnhancedNode) -> bool:
    tokens = set(re.split(r"[^a-z0-9]+", _raw_attr_text(node)))
    return bool(tokens & set(_RANGE_END_TOKENS))


def range_picker_ends(shell: EnhancedNode) -> list:
    """The two clickable value wrappers of a div-based range picker shell.

    Feishu ``atsx-date-picker-period-month`` renders each end as one
    ``…-period-month-label`` wrapper (holding that end's year + month parts),
    while the whole shell is a single click-to-open control. Each wrapper is an
    **independent click target**: clicking the left one opens the shared year /
    month panel with its focus on the start end, the right one on the end end
    (verified on the live form 2026-10-01). Collapsing the shell into one entry
    hid that choice, so the model could not move the focus off whichever end the
    shell-centre click happened to land on and burned ~10k reasoning tokens
    before the user aborted. Exposing the two wrappers restores the page's real
    structure — exactly as the two inputs of an Element Plus / Ant range already
    are two addressable entries.
    """
    if not is_range_picker_shell(shell):
        return []
    ends = []
    for child in shell.children:
        if (
            child.is_element
            and not child.hidden
            and child.visible
            and _has_range_end_token(child)
        ):
            ends.append(child)
    return ends


def is_range_picker_end(node: EnhancedNode) -> bool:
    """True for one clickable end wrapper of a div-based range picker shell."""
    if not node.is_element or node.tag in ("input", "select", "textarea"):
        return False
    if node.hidden or not node.visible or not node.rendered:
        return False
    if not _has_range_end_token(node):
        # Cheap gate: only a handful of nodes carry year/month/day tokens, so the
        # ancestor walk below never runs across the whole tree.
        return False
    current = node.parent
    hops = 0
    while current is not None and hops < 4:
        if is_range_picker_shell(current):
            return node in range_picker_ends(current)
        current = current.parent
        hops += 1
    return False


def is_date_picker_input(node: EnhancedNode) -> bool:
    """True for the *editable* text entry of a date / time picker widget.

    Its value can be typed but only *commits* when a cell in the popped-up
    panel is clicked, which is exactly the ``searchable`` contract (typing only
    filters/navigates). Native ``<input type=date|month|time>`` is deliberately
    excluded: its panel is drawn by the browser, so no candidate can ever be
    clicked and plain typing stays the correct operation.
    """
    if node.tag != "input" or "readonly" in node.attributes:
        return False
    if _input_type(node) not in ("", "text", "search", "email"):
        return False
    return _picker_shell(node)


def _in_picker_panel(node: EnhancedNode, max_hops: int = 8) -> bool:
    """True if an ancestor within ``max_hops`` is a picker's popped-up *panel*.

    A picker panel is the **content** that floats open under a date / calendar
    widget: it is a known picker component *and* a panel-ish part (``…-panel`` /
    ``…-dropdown`` / ``…-table`` …). The trigger shell never carries a panel
    token, so this deliberately excludes the control itself.
    """
    for ancestor, _branch in _iter_picker_shells(node, max_hops):
        if not ancestor.is_element:
            continue
        raw = _class_id_text(ancestor)
        # Check the cheap "panel" token first so the (larger) component-marker
        # scan only runs on candidates that can possibly be a panel.
        if _has_token(raw, _PICKER_PANEL_TOKENS) and any(
            marker in raw for marker in _PICKER_COMPONENT_MARKERS
        ):
            return True
    return False


def _subtree_marks_disabled(node: EnhancedNode, max_depth: int = 2) -> bool:
    """True if ``node`` or a shallow descendant carries a disabled marker.

    Component libraries put the disabled state on the *cell content* child
    (``…-panel-body-cell-content-disabled``) while the ``<td>`` itself looks
    normal, so a plain ancestor walk (``_is_disabled``) misses it. Bounded to a
    couple of hops so a disabled sub-widget far below does not disable a whole
    table cell.
    """
    if max_depth < 0 or not node.is_element:
        return False
    if _node_marks_disabled(node):
        return True
    for child in node.children:
        if child.is_element and _subtree_marks_disabled(child, max_depth - 1):
            return True
    return False


# A picker cell's own value: a number ("2018", "15", "1 月"), a Chinese
# month/year/day ("一月" / "2026 年"), or a relative sentinel ("今天" / "至今").
# Used to tell a real option cell apart from a non-interactive weekday header
# ("日 一 二 …") or a decorative blank cell.
_PICKER_CELL_RELATIVE = {"今天", "今日", "此刻", "现在", "至今", "不限", "不限制", "本月", "今年"}
_OPTION_VALUE_RE = re.compile(r"\d|[年月日]")


def _node_value_text(node: EnhancedNode, max_nodes: int = 60) -> str:
    """A bounded text sample of ``node``: ax name / title / own text, then leaves."""
    pieces: list[str] = []
    for attr in ("title", "aria-label"):
        value = node.attributes.get(attr)
        if value:
            pieces.append(value)
    if node.ax_name:
        pieces.append(node.ax_name)
    if node.text:
        pieces.append(node.text)
    seen = 0
    stack = list(node.children)
    while stack and seen < max_nodes:
        cur = stack.pop(0)
        seen += 1
        if cur.is_text:
            if cur.text:
                pieces.append(cur.text)
            continue
        if cur.is_element:
            stack.extend(cur.children)
    return " ".join(pieces)


def _looks_like_option_value(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return False
    if stripped in _PICKER_CELL_RELATIVE:
        return True
    return bool(_OPTION_VALUE_RE.search(stripped))


def is_picker_panel_cell(node: EnhancedNode) -> bool:
    """True for a selectable cell (year / month / day) of a picker panel.

    Date / calendar panels render their options as **table cells** whose
    ``cursor:pointer`` is set only on ``:hover`` and whose handler is bound by
    framework delegation (no inline ``on*``, no ARIA role). The static computed
    cursor is therefore ``auto`` and every other clickability branch misses them,
    so the model saw a year grid as plain ``[文本]`` and could not pick a year
    (Feishu ATSX ``.atsx-date-picker-panel-body-cell``, AntD ``.ant-picker-cell``,
    Element ``.el-date-table-cell`` are all this shape).

    Guard rails: only ``<td>`` (never ``<th>``, which holds the non-interactive
    weekday header row), only inside a picker *panel*, must not be disabled (own
    or shallow content), must not already contain an interactive descendant (so a
    cell wrapping a real control is not double-named), and its label must look
    like an option value (a digit / 年月日 / a relative sentinel) so decorative or
    header cells are not named.
    """
    if not node.is_element or node.tag != "td":
        return False
    if node.hidden or not node.visible or not node.bbox:
        return False
    if _is_disabled(node) or _subtree_marks_disabled(node):
        return False
    if _has_interactive_descendant(node):
        return False
    if not _in_picker_panel(node):
        return False
    return _looks_like_option_value(_node_value_text(node))


def is_picker_panel_nav(node: EnhancedNode) -> bool:
    """True for an icon-only navigation control inside a picker panel.

    A picker header's prev / next chevron is an ``<i>`` wrapping an ``<svg>`` with
    a real ``cursor:pointer`` but **no** text and **no** accessible name, so the
    ``cursor:pointer`` branch of ``is_clickable`` (which needs a label) and
    ``is_control_icon`` (which needs a following labeled pointer sibling) both
    skip it. Naming it is what lets the model page the year / month grid.
    """
    if not node.is_element or _is_disabled(node):
        return False
    if node.hidden or not node.visible or not node.bbox:
        return False
    if node.styles.get("cursor") != "pointer":
        return False
    if node.ax_name or _has_text(node):
        return False
    if not has_svg_descendant(node):
        return False
    if _has_interactive_descendant(node):
        return False
    return _in_picker_panel(node)


def is_widget_trigger_input(node: EnhancedNode) -> bool:
    """True for a ``readonly`` text input that is a click-to-open widget's face.

    A non-searchable select / cascader / readonly date picker only *shows* the
    value: the widget commits it from a popped-up panel, so the input cannot be
    filled at all. Clicking it is the one and only way in — which the model must
    be able to do, so the node is given the ``clickdropdown`` category instead of
    ``input`` (the ``input`` category refuses clicks as well as readonly fills,
    which used to leave such fields completely un-operable).

    Requires a marker that the widget really is one: a select / cascader / picker
    shell (by class token) or a date-picker shell, plus a dropdown / calendar
    indicator in the same shell. Without them a plain readonly field keeps its
    ``input`` category (``fill`` then reports the honest readonly error).
    """
    if node.tag != "input" or "readonly" not in node.attributes:
        return False
    if _input_type(node) not in ("", "text", "search"):
        return False
    for ancestor, branch in _iter_picker_shells(node, 4):
        if not ancestor.is_element:
            continue
        if _is_named_picker_shell(ancestor) or _marks_select_control(ancestor):
            return True
        if _is_generic_picker_shell(ancestor) and _has_picker_indicator_in_shell(branch):
            return True
    return False


def _has_searchable_input(node: EnhancedNode) -> bool:
    """True if ``node``'s subtree contains a searchable-select filter input."""
    for child in node.children:
        if child.is_text or not child.is_element:
            continue
        if child.tag == "input" and is_searchable_typeahead(child):
            return True
        if _has_searchable_input(child):
            return True
    return False


def is_searchable_typeahead(node: EnhancedNode) -> bool:
    """True for an *editable* combobox / autocomplete input.

    This is the typeahead of a searchable select: the user types here and the
    candidate list filters live. Typing only *filters* — the value is committed
    only by clicking a candidate — so it is given its own category
    (``searchable``) and driven by ``tool_07_searchable_dropdown`` instead of
    being treated as an ordinary ``<可输入元素>``.

    Three families of signal are recognised:

    1. the input itself is an ARIA combobox (``role=combobox``);
    2. an ``aria-autocomplete`` lives on the input or an ancestor (Ant Design's
       ``show-search`` select puts it on the wrapping ``.atsx-select-selection``);
    3. a *custom select control* (class/id token ``select`` / ``combobox`` /
       ``cascader``) wraps the input together with a down-arrow addon, and the
       input is editable. Moka's ``sd-Select`` is exactly this shape and carries
       no ARIA at all. The arrow must be a *sibling* of the input (inside the same
       control box) and must not itself be a composite control, which is what
       separates a search box (Moka) from a value-bearing custom select whose
       arrow lives outside the input wrapper (Beisen ``.phoenix-select`` — that
       one is a ``clickdropdown``, see ``is_click_dropdown``).

    A fourth family is a *date / time picker's* text entry (``is_date_picker_input``):
    same contract (typing only navigates the panel, the value commits on a click
    on a cell), so it shares this category instead of being offered to the plain
    fill tool — which silently loses the typed text on blur (see ``ID122``).
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
    # Family 3: a custom select control wrapping this input.
    if is_custom_select_input(node):
        return True
    # Family 4: a date / time picker's editable text entry.
    return is_date_picker_input(node)


def is_custom_select_input(node: EnhancedNode) -> bool:
    """True if ``node`` is the *filter input* of a custom (non-ARIA) select.

    The label / value logic for these controls differs from a plain searchable
    input (their field name lives in a sibling ``…title…`` element and their
    validation message is a sibling of the input), so callers gate the enhanced
    handling on this predicate to avoid touching any other site's searchable
    inputs.

    Shape: an editable ``<input>`` whose immediate parent is marked as a select /
    combobox control and which has a down-arrow addon *sibling* inside that control
    (Moka ``sd-Select``). A sibling that is itself a composite control (an
    Ant-Design phone-country selector) does not count.
    """
    if node.tag != "input":
        return False
    if "readonly" in node.attributes:
        return False
    if node.attributes.get("type", "text").lower() == "hidden":
        return False
    parent = node.parent
    if parent is None or not _marks_select_control(parent):
        return False
    for sibling in parent.children:
        if sibling is node or sibling.is_text or not sibling.is_element:
            continue
        if _contains_control(sibling):
            continue
        if _has_dropdown_indicator(sibling):
            return True
    return False


def is_click_dropdown(node: EnhancedNode) -> bool:
    """True for a clickable control that opens a dropdown list of candidates.

    Two semantic families (both are *click-to-open*, no editable filter):

    * 表单类下拉选框：class/id 含 ``select`` / ``combobox`` 令牌，且子树里带一个
      朝下箭头（Beisen ``.phoenix-select``、Ant ``.ant-select``、Feishu
      ``.atsx-select``、Element ``.el-select`` …）；
    * 明确的下拉菜单触发器：class/id 含 ``dropdown`` / ``menu`` 令牌，且子树里带
      下拉箭头。

    Excluded: the arrow / addon *part* itself (its own signal is the element, so it
    has no indicator *descendant*), the popup body (overlay role or absolutely /
    fixed positioned), and a wrapper that merely contains a searchable filter input
    (that belongs to ``searchable``).
    """
    if not node.is_element or _is_disabled(node):
        return False
    if node.tag in ("input", "select", "textarea"):
        return False
    raw = _class_id_text(node)
    if not raw.strip():
        return False
    form_select = _has_token(raw, _SELECT_TOKENS)
    menu = (not form_select) and _has_token(raw, _MENU_TOKENS)
    if not (form_select or menu):
        return False
    if menu:
        # Only an explicit *trigger*, and never a container that wraps the menu's
        # own items (a sidebar/nav list of links).
        if not _has_token(raw, _MENU_TRIGGER_TOKENS):
            return False
        if _has_interactive_descendant(node):
            return False
    if not _descendant_has_dropdown_indicator(node):
        return False
    if (node.role or "").lower() in _OVERLAY_ROLES:
        return False
    if node.styles.get("position") in ("absolute", "fixed"):
        return False
    if _has_searchable_input(node):
        return False
    return is_clickable(node)


def classify(node: EnhancedNode) -> str:
    """Return the primary interactive category, or '' if none.

    结果按节点缓存（``cache_classify``，``None`` 为「未计算」哨兵）。本函数会被
    ``DOMSerializer`` 对每个节点调用，且 ``_has_interactive_descendant`` 也会对每个
    后代调用它——不缓存则两者相互递归、反复重扫子树，大页面序列化会卡死。
    """
    cached = node.cache_classify
    if cached is not None:
        return cached
    if is_searchable_typeahead(node) and not _is_disabled(node):
        # A searchable select's filter box outranks ``input``: typing only
        # narrows the candidate list, so it must not be routed to the plain
        # fill-in tool (``tool_02``).
        result = "searchable"
    elif is_widget_trigger_input(node) and not _is_disabled(node):
        # A readonly select / cascader / picker face: it cannot be filled at all
        # and clicking it is the only way to open its panel. Giving it its own
        # click-open category (instead of ``input``) is what makes it operable;
        # ``input`` refuses both the fill (readonly) and the click (wrong class).
        result = "clickdropdown"
    elif is_input(node):
        result = "input"
    elif is_picker_shell_trigger(node):
        # A div-based date / month picker shell: the whole widget is
        # click-to-open and there is nothing to type into (see
        # ``is_picker_shell_trigger``). Checked before ``is_clickable`` so the
        # shell itself — not one of its value parts — carries the identity, and
        # routed to ``clickdropdown`` so the tool prompt says "click to open the
        # panel, then click a cell" instead of "type to filter" (which sent the
        # model into an unwinnable ``tool_07`` loop in the Feishu Jobs session).
        result = "clickdropdown"
    elif is_range_picker_end(node):
        # One of the two ends of a div-based range shell (see
        # ``range_picker_ends``). It is a real click target that opens the shared
        # panel focused on that end, so it shares ``clickdropdown`` and must be
        # checked before ``is_clickable`` (a bare ``cursor:pointer`` would
        # otherwise mark it as a plain click).
        result = "clickdropdown"
    elif is_select(node):
        result = "select"
    elif is_draggable(node):
        result = "drag"
    elif is_scrollable(node):
        result = "scroll"
    elif is_clickable(node):
        # A clickable that semantically opens a dropdown of candidates (a custom
        # select box / dropdown-menu trigger) is separated from plain clicks and
        # driven by ``tool_08_click_dropdown``.
        result = "clickdropdown" if is_click_dropdown(node) else "click"
    else:
        result = ""
    node.cache_classify = result
    return result


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
    cached = node.cache_has_overflowing_child
    if cached is not None:
        return cached
    result = False
    if node.bbox:
        nx, ny, nw, nh = node.bbox
        for child in _iter_unclipped_descendants(node):
            if not child.bbox:
                continue
            cx, cy, cw, ch = child.bbox
            if cy + ch > ny + nh + 4 or cx + cw > nx + nw + 4:
                result = True
                break
            if cy < ny - 4 or cx < nx - 4:
                result = True
                break
    node.cache_has_overflowing_child = result
    return result


def _iter_unclipped_descendants(node: EnhancedNode, positioned: bool = False):
    """Descendants whose layout box can actually overflow ``node``.

    Yields every child (its own bbox is compared), but stops at two shapes that
    cannot make ``node`` scrollable:

    * a child that clips its own overflow (see ``_clips_overflow``) — its inner
      content is contained by the child, not by ``node``;
    * an **out-of-flow** subtree (``position: absolute`` / ``fixed``, or anything
      inside one). An absolutely positioned descendant is placed against its own
      containing block, so it can hang far outside ``node`` while ``node`` has
      nothing to scroll. Without this, every portal wrapper (``height: 0``)
      around a floating panel made its ancestors look scrollable: on the Feishu
      Jobs apply form the whole apply section was then wrapped in one giant
      ``<可滚动元素>`` (and the picker's ``起止时间`` label stopped resolving).
    """
    if positioned:
        return
    for child in node.children:
        if not child.is_element:
            continue
        if child.styles.get("position") in ("absolute", "fixed"):
            continue
        yield child
        if not _clips_overflow(child):
            yield from _iter_unclipped_descendants(child)
