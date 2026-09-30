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
    if is_hover_action_control(node):
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

_SELECT_TOKENS = ("select", "combobox")
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
    3. a *custom select control* (class/id token ``select`` / ``combobox``)
       wraps the input together with a down-arrow addon, and the input is
       editable. Moka's ``sd-Select`` is exactly this shape and carries no ARIA
       at all. The arrow must be a *sibling* of the input (inside the same
       control box) and must not itself be a composite control, which is what
       separates a search box (Moka) from a value-bearing custom select whose
       arrow lives outside the input wrapper (Beisen ``.phoenix-select`` — that
       one is a ``clickdropdown``, see ``is_click_dropdown``).
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
    return is_custom_select_input(node)


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
    elif is_input(node):
        result = "input"
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
