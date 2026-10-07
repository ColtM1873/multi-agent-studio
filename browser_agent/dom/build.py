"""Build an enhanced DOM tree from the raw CDP payloads."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .capture import viewport_from_metrics

_VIEWPORT_MARGIN = 1000.0

# Synthetic tag for the document-level (whole page) scrollbar. ``html``/``body``
# are deliberately excluded from the scrollable classification (see IDBT02
# 8.3/14.6), which left the global scrollbar unreachable by the LLM; this node
# re-exposes it as a normal interactive scroll element.
PAGE_SCROLL_TAG = "#page"

# Media hosts whose user-agent shadow root is pure browser chrome (see ``_walk``).
_MEDIA_TAGS = {"video", "audio"}

# Component libraries animate an overlay *out* (Vue / Element-Plus / Ant's "leave"
# hooks) before detaching it. The leave-hook class (e.g. ``phoenix-popover-zoom-leave``)
# lives on a still-mounted node whose computed ``display``/``visibility`` remain
# visible and whose ``opacity`` may still be > 0, so ``_apply_visibility`` used to
# serialize a *dismissed* dialog for as long as the hook stayed mounted. In a real
# session a popconfirm lingered in ``zoom-leave`` for ~2 minutes and kept
# re-injecting its stale prompt ("是否继续上传？") into every snapshot. An *overlay*
# carrying a leave/closing hook class is conceptually gone, so treat it as hidden.
_OVERLAY_CLASS_HINTS = (
    "popover", "dropdown", "popup", "popper", "overlay", "tooltip",
    "modal", "dialog", "drawer", "toast", "notification", "messagebox",
    "message-box", "listbox", "autocomplete", "suggestion",
)
_OVERLAY_ROLES = {
    "dialog", "alertdialog", "tooltip", "menu", "menubar", "listbox",
    "combobox", "tree", "grid", "tablist", "radiogroup",
}
# ``...-leave`` / ``...-leaving`` / ``...-closing`` / ``...-exit`` and their
# animation-hook tails (``-leave-active``, ``-leave-to``, ``-exit-from`` …).
_LEAVE_MARKERS = ("leave", "leaving", "closing", "exit", "exiting")
_LEAVE_HOOK_TAILS = ("-active", "-to", "-from", "-start", "-end")


# 本函数于BID071引入
def _is_document_root(node: "EnhancedNode") -> bool:
    """``<html>`` / ``<body>`` 恒为文档根，永远不是浮动浮层。

    文档根的 ``class`` 是**全局状态**（滚动锁、布局态），不是「浮动浮层」的
    折叠/离场标记；而组件库恰恰把滚动锁加在 ``<body>`` 上：Element/Element-Plus
    的 ``el-popup-parent--hidden``（同时含 overlay 词 ``popup`` 与折叠后缀
    ``--hidden``）、Bootstrap 的 ``modal-open``、各种 ``body.collapsed``。若让根
    元素满足 ``_class_marks_collapsed_overlay`` / ``_class_marks_leaving``，整个
    文档都会被当成「闭合浮层挂载点」而剪光（见 ``in_collapsed_overlay_portal``）。

    2026-10-04 顺丰校招简历页实测：``<body class="el-popup-parent--hidden">`` 让
    ``in_collapsed_overlay_portal(#app)`` 返回 True，整页 358 个可见可交互元素
    被剪到只剩合成的整页滚动条（工具只报「可互动元素 1 个」）；豁免根元素后恢复
    到 122 个（视口差异），「城市选择」对话框与「出生日期」日历均正常暴露。全历史
    1696 份日志扫描还发现 ``body.collapsed … modal-open__generic-modal-block``
    同属此族。根元素永远不可能是浮动浮层，故所有「按 class 判折叠/离场浮层」的
    谓词都必须先排除它。
    """
    return node.tag in ("html", "body")


def _class_marks_leaving(node: "EnhancedNode") -> bool:
    """True if ``node`` is a floating overlay carrying a leave/closing hook class.

    Deliberately narrow, to avoid hiding unrelated pages:

    * only the *overlay* shape qualifies (an overlay role, or a known overlay class
      hint), so a random element that happens to carry a ``*-leave`` token is not
      touched;
    * the leave token must be a suffix (``foo-leave`` / ``foo-leave-active``), not a
      mere substring (``leave-request`` / ``overflow-hidden`` never match).

    Enter hooks (``*-enter`` / ``*-enter-active``) are intentionally *not* matched:
    a popup mid-appearance is real content and stays handled by the existing
    ``opacity: 0`` rescue in the serializer.
    """
    if not node.is_element:
        return False
    if _is_document_root(node):  # BID071 文档根是全局状态（滚动锁/布局态），不是浮层
        return False  # BID071
    classes = (node.attributes.get("class") or "").lower()
    role = (node.role or "").lower()
    is_overlay = role in _OVERLAY_ROLES or any(
        hint in classes for hint in _OVERLAY_CLASS_HINTS
    )
    if not is_overlay:
        return False
    for raw in classes.split():
        token = raw.rstrip(".!#*")
        base = token
        for tail in _LEAVE_HOOK_TAILS:
            if token.endswith(tail):
                base = token[: -len(tail)]
                break
        if base in _LEAVE_MARKERS or base.endswith(
            tuple("-" + marker for marker in _LEAVE_MARKERS)
        ):
            return True
    return False


@dataclass
class EnhancedNode:
    node_id: int
    backend_node_id: int
    frame_id: str
    node_type: int
    tag: str
    attributes: dict[str, str] = field(default_factory=dict)
    text: str = ""
    role: str = ""
    ax_name: str = ""
    bbox: Optional[tuple[float, float, float, float]] = None
    visible: bool = True
    hidden: bool = False
    in_viewport: bool = True
    # ``False`` when the CDP snapshot has no layout row for this node at all
    # (``display:none`` is omitted from ``DOMSnapshot``, so its computed styles
    # are unavailable and ``hidden`` cannot be derived; also covers
    # ``display:contents`` and detached subtrees). Such nodes have no box of
    # their own; the serializer recurses into their element descendants but
    # drops their own text.
    rendered: bool = True
    paint_order: int = 0
    styles: dict[str, str] = field(default_factory=dict)
    input_value: str = ""
    selected: Optional[bool] = None
    doc_token: str = ""
    is_shadow: bool = False
    is_iframe_root: bool = False
    children: list["EnhancedNode"] = field(default_factory=list)
    parent: Optional["EnhancedNode"] = None

    # 分类结果惰性缓存（见 ``classify.py``）。节点在 ``build_enhanced_tree`` 里
    # 全新构造，且构建后属性不再变化，故缓存不会跨快照失效。
    # 没有这些缓存时，``classify`` / ``_has_text`` / ``has_svg_descendant`` /
    # ``_has_interactive_descendant`` / ``_has_label_element`` /
    # ``_has_overflowing_child`` 会对**每个节点重新扫描整棵子树**，并且
    # ``classify`` 与 ``_has_interactive_descendant`` 相互递归——真实大页面上
    # 序列化会退化成超线性，卡数十秒乃至数分钟（详见 inner_docs/ID116）。
    cache_classify: Optional[str] = None
    cache_has_text: Optional[bool] = None
    cache_has_svg: Optional[bool] = None
    cache_has_interactive_desc: Optional[bool] = None
    cache_has_label_element: Optional[bool] = None
    cache_has_overflowing_child: Optional[bool] = None
    cache_has_dropdown_indicator: Optional[bool] = None
    cache_contains_control: Optional[bool] = None
    # 「这个外壳里有没有真正可用的文本输入框」与「它就是那个点击展开的选择器控件」
    # ——两者都要扫子树，故同上一组一样按节点惰性缓存（见 inner_docs/ID123）。
    cache_usable_text_input: Optional[bool] = None
    cache_picker_shell: Optional[bool] = None
    # 「自己在不在一个被折叠/隐藏的浮层挂载点里」（见 ``in_collapsed_overlay_portal``）。
    # 同样是祖先链扫描，按节点缓存。
    cache_in_collapsed_portal: Optional[bool] = None
    # 「自身或任一祖先 ``opacity≈0``（整棵子树完全透明）」。这是祖先链扫描，见
    # ``DOMSerializer._effective_opacity_zero``；序列化时对每个节点调用，故按节点缓存，
    # 避免大页面退化成 O(n×深度)。 # BID076
    cache_opacity_zero: Optional[bool] = None

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.doc_token, self.frame_id, self.backend_node_id)

    @property
    def is_text(self) -> bool:
        return self.node_type == 3

    @property
    def is_element(self) -> bool:
        return self.node_type == 1


@dataclass
class EnhancedTree:
    root: EnhancedNode
    nodes: list[EnhancedNode]
    viewport: dict[str, float]
    url: str = ""
    title: str = ""

    def by_backend_id(self) -> dict[int, EnhancedNode]:
        return {n.backend_node_id: n for n in self.nodes if n.is_element}


def _flat_attrs(flat: Optional[list[str]]) -> dict[str, str]:
    attrs: dict[str, str] = {}
    if not flat:
        return attrs
    for i in range(0, len(flat) - 1, 2):
        attrs[flat[i]] = flat[i + 1]
    return attrs


def _sparse_int(data: object) -> dict[int, int]:
    """DOMSnapshot uses sparse ``{index:[...], value:[...]}`` maps for some fields."""
    if isinstance(data, dict):
        indices = data.get("index", [])
        values = data.get("value", [])
        return {int(i): int(v) for i, v in zip(indices, values)}
    if isinstance(data, list):
        return {i: v for i, v in enumerate(data) if isinstance(v, int) and v >= 0}
    return {}


def _parse_snapshot(
    snapshot: dict, styles_order: list[str], device_scale: float = 1.0
) -> tuple[dict[int, dict], dict[int, str]]:
    backend_to_layout: dict[int, dict] = {}
    backend_to_value: dict[int, str] = {}
    strings = snapshot.get("strings", [])
    # ``DOMSnapshot`` bounds are in *device* pixels (multiplied by
    # ``window.devicePixelRatio``), while the viewport / scroll metrics are in
    # CSS pixels. Normalize here so every bbox comparison (viewport filter,
    # clip, overflow) uses one consistent unit (fixes ID65 §11.1 dpr limit).
    scale = device_scale if device_scale and device_scale > 0 else 1.0
    for doc in snapshot.get("documents", []):
        nodes = doc.get("nodes", {})
        layout = doc.get("layout", {})
        node_index = layout.get("nodeIndex", [])
        bounds = layout.get("bounds", [])
        styles = layout.get("styles", [])
        paint_orders = layout.get("paintOrders", [])
        backend_ids = nodes.get("backendNodeId", [])

        for node_idx, str_idx in _sparse_int(nodes.get("inputValue")).items():
            if node_idx < len(backend_ids) and 0 <= str_idx < len(strings):
                backend_to_value[backend_ids[node_idx]] = strings[str_idx]

        for i, node_idx in enumerate(node_index):
            if node_idx >= len(backend_ids):
                continue
            backend_id = backend_ids[node_idx]
            entry: dict = {}
            if i < len(bounds) and bounds[i]:
                entry["bbox"] = tuple(float(v) / scale for v in bounds[i][:4])
            if i < len(paint_orders):
                entry["paint_order"] = paint_orders[i]
            style_map: dict[str, str] = {}
            if i < len(styles):
                row = styles[i]
                for j, name in enumerate(styles_order):
                    if j < len(row):
                        idx = row[j]
                        if idx is not None and idx >= 0 and idx < len(strings):
                            style_map[name] = strings[idx]
            entry["styles"] = style_map
            backend_to_layout[backend_id] = entry
    return backend_to_layout, backend_to_value


def _parse_ax(ax_tree: dict) -> dict[int, dict]:
    backend_to_ax: dict[int, dict] = {}
    for node in ax_tree.get("nodes", []):
        backend_id = node.get("backendDOMNodeId")
        if not backend_id:
            continue
        role = (node.get("role") or {}).get("value", "")
        name = (node.get("name") or {}).get("value", "")
        backend_to_ax[backend_id] = {
            "role": role,
            "name": name,
            "selected": _ax_selection(node),
        }
    return backend_to_ax


def _ax_bool(value: object) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.lower() == "true":
            return True
        if value.lower() == "false":
            return False
    return None


def _ax_selection(node: dict) -> Optional[bool]:
    """Read ``checked`` / ``selected`` / ``pressed`` from an AX node's properties.

    Chrome's accessibility tree exposes the live selection state (unlike the DOM
    snapshot, whose ``attributes`` only carry the *initial* content attributes).
    Returning ``None`` means "not a choice control / unknown", ``True`` means
    "selected/checked/pressed", ``False`` means "explicitly not selected".
    """
    found: Optional[bool] = None
    for prop in node.get("properties", []) or []:
        if prop.get("name") not in ("checked", "selected", "pressed"):
            continue
        value = _ax_bool((prop.get("value") or {}).get("value"))
        if value is True:
            return True
        if value is False and found is None:
            found = False
    return found


def build_enhanced_tree(raw: dict) -> EnhancedTree:
    viewport = viewport_from_metrics(raw.get("metrics", {}))
    device_scale = float((raw.get("page") or {}).get("device_scale") or 1.0) or 1.0
    backend_to_layout, backend_to_value = _parse_snapshot(
        raw.get("snapshot", {}), raw.get("styles_order", []), device_scale
    )
    backend_to_ax = _parse_ax(raw.get("ax_tree", {}))

    root_node = (raw.get("document") or {}).get("root") or {}
    nodes: list[EnhancedNode] = []

    synthetic_root = EnhancedNode(
        node_id=0,
        backend_node_id=0,
        frame_id="",
        node_type=9,
        tag="#document",
    )
    _walk(
        root_node,
        synthetic_root,
        "",
        backend_to_layout,
        backend_to_ax,
        backend_to_value,
        nodes,
        viewport,
    )

    url = ""
    title = ""
    page = raw.get("page") or {}
    doc_token = str(page.get("time_origin", "")) if page else ""
    if page:
        url = page.get("url", "")
        title = page.get("title", "")
    if not url or not title:
        for doc in raw.get("snapshot", {}).get("documents", []):
            url = url or doc.get("documentURL", "")
            if not title and isinstance(doc.get("title"), str):
                title = doc.get("title", "")
    for node in nodes:
        node.doc_token = doc_token
    _apply_textarea_values(nodes, page.get("textarea_values"))

    if float(page.get("scroll_height") or 0) > float(page.get("client_height") or 0) + 4:
        page_node = EnhancedNode(
            node_id=-1,
            backend_node_id=-1,
            frame_id="",
            node_type=1,
            tag=PAGE_SCROLL_TAG,
            doc_token=doc_token,
        )
        page_node.parent = synthetic_root
        synthetic_root.children.insert(0, page_node)
        nodes.insert(0, page_node)

    return EnhancedTree(root=synthetic_root, nodes=nodes, viewport=viewport, url=url, title=title)


def _apply_textarea_values(nodes: list[EnhancedNode], values: object) -> None:
    """Restore live ``<textarea>`` values captured via the page ``Runtime.evaluate``.

    ``DOMSnapshot`` does not expose ``inputValue`` for ``<textarea>``, and its
    ``outerHTML`` keeps the *initial* text, so without this a filled textarea
    serialized as its placeholder ("请输入项目描述") even though it held text —
    the model read that as "my fill did not land" and re-filled endlessly. The
    capture pass returns every main-frame textarea's live value in document
    order; the tree's textareas are in the same order, so they zip 1:1. If the
    counts differ (a textarea inside an iframe / shadow root, which the
    document-level query does not see), we skip rather than mis-assign.
    """
    if not isinstance(values, list) or not values:
        return
    textareas: list[EnhancedNode] = []
    for node in nodes:
        if not (node.is_element and node.tag == "textarea"):
            continue
        # ``frame_id`` is populated for the top document too, so it cannot tell
        # main frame from iframe; the shadow / iframe *marker* ancestors can.
        ancestor = node.parent
        nested = False
        while ancestor is not None:
            if ancestor.is_shadow or ancestor.is_iframe_root:
                nested = True
                break
            ancestor = ancestor.parent
        if not nested:
            textareas.append(node)
    if len(textareas) != len(values):
        return
    for node, value in zip(textareas, values):
        if isinstance(value, str):
            node.input_value = value


def _walk(
    node: dict,
    parent: EnhancedNode,
    frame_id: str,
    backend_to_layout: dict,
    backend_to_ax: dict,
    backend_to_value: dict,
    out_nodes: list[EnhancedNode],
    viewport: dict,
) -> None:
    node_type = node.get("nodeType")
    node_frame = node.get("frameId") or frame_id

    if node_type == 1:
        backend_id = node.get("backendNodeId", 0)
        layout = backend_to_layout.get(backend_id)
        has_layout = layout is not None
        layout = layout or {}
        ax = backend_to_ax.get(backend_id, {})
        styles = layout.get("styles", {})
        bbox = layout.get("bbox")
        attributes = _flat_attrs(node.get("attributes"))
        # Prefer the accessibility tree's computed role, but fall back to the
        # explicit ``role`` attribute: ``getFullAXTree`` marks some nodes as
        # ignored (empty role) even when they carry a real ARIA role. Without the
        # fallback, ARIA-only controls (e.g. a jQuery-UI ``<li role="option">``
        # autocomplete item with no ``<a>/<button>``) were never classified as
        # interactive and degraded to plain text.
        role = ax.get("role") or (attributes.get("role") or "").strip()

        enhanced = EnhancedNode(
            node_id=node.get("nodeId", 0),
            backend_node_id=backend_id,
            frame_id=node_frame,
            node_type=1,
            tag=(node.get("localName") or node.get("nodeName") or "").lower(),
            attributes=attributes,
            role=role,
            ax_name=ax.get("name", ""),
            bbox=bbox,
            paint_order=layout.get("paint_order", 0),
            styles=styles,
            input_value=backend_to_value.get(backend_id, ""),
            selected=ax.get("selected"),
        )
        _apply_visibility(enhanced, viewport, has_layout)
        enhanced.parent = parent
        parent.children.append(enhanced)
        out_nodes.append(enhanced)

        for child in node.get("children", []) or []:
            _walk(
                child,
                enhanced,
                node_frame,
                backend_to_layout,
                backend_to_ax,
                backend_to_value,
                out_nodes,
                viewport,
            )
        for shadow in node.get("shadowRoots", []) or []:
            if enhanced.tag in _MEDIA_TAGS or enhanced.tag == "input":
                # ``<video>``/``<audio>`` only expose browser-generated player
                # chrome through their user-agent shadow root (dozens of
                # duplicated "选项/全屏/静音/画中画" controls), and ``<input>``
                # (file inputs in particular) only expose UA shadow text such as
                # "选择文件". Serializing it floods the LLM with noise and no
                # page-authored content lives there, so skip the shadow subtree.
                continue
            shadow_node = _walk_container(
                shadow,
                enhanced,
                node_frame,
                backend_to_layout,
                backend_to_ax,
                backend_to_value,
                out_nodes,
                viewport,
            )
            if shadow_node:
                shadow_node.is_shadow = True
        content_doc = node.get("contentDocument")
        if content_doc:
            doc_frame = content_doc.get("frameId") or node_frame
            iframe_node = _walk_container(
                content_doc,
                enhanced,
                doc_frame,
                backend_to_layout,
                backend_to_ax,
                backend_to_value,
                out_nodes,
                viewport,
            )
            if iframe_node:
                iframe_node.is_iframe_root = True

    elif node_type == 3:
        text = (node.get("nodeValue") or "").strip()
        if not text:
            return
        if parent.is_element and parent.tag == "input":
            # ``<input>`` cannot have authored children; any text under it is
            # browser user-agent shadow chrome (e.g. the file input's
            # "选择文件 / No file chosen") leaking into the output.
            return
        text_node = EnhancedNode(
            node_id=node.get("nodeId", 0),
            backend_node_id=node.get("backendNodeId", 0),
            frame_id=node_frame,
            node_type=3,
            tag="#text",
            text=text,
        )
        text_node.parent = parent
        parent.children.append(text_node)
        out_nodes.append(text_node)

    else:
        # document / fragment / other containers: recurse transparently
        for child in node.get("children", []) or []:
            _walk(
                child,
                parent,
                node_frame,
                backend_to_layout,
                backend_to_ax,
                backend_to_value,
                out_nodes,
                viewport,
            )


def _walk_container(
    node: dict,
    parent: EnhancedNode,
    frame_id: str,
    backend_to_layout: dict,
    backend_to_ax: dict,
    backend_to_value: dict,
    out_nodes: list[EnhancedNode],
    viewport: dict,
) -> Optional[EnhancedNode]:
    """Walk a shadow root / content document, returning a marker node."""
    marker = EnhancedNode(
        node_id=node.get("nodeId", 0),
        backend_node_id=node.get("backendNodeId", 0) or -1,
        frame_id=node.get("frameId") or frame_id,
        node_type=11,
        tag="#fragment",
    )
    marker.parent = parent
    parent.children.append(marker)
    for child in node.get("children", []) or []:
        _walk(
            child,
            marker,
            marker.frame_id,
            backend_to_layout,
            backend_to_ax,
            backend_to_value,
            out_nodes,
            viewport,
        )
    return marker


def _apply_visibility(
    node: EnhancedNode, viewport: dict, has_layout: bool = True
) -> None:
    styles = node.styles
    hidden = (
        styles.get("display") == "none"
        or styles.get("visibility") in ("hidden", "collapse")
        or _class_marks_leaving(node)
    )
    node.hidden = hidden
    if not has_layout:
        # No layout row at all: ``display:none`` (whose computed styles are
        # omitted from the snapshot, so ``hidden`` stays ``False``),
        # ``display:contents`` or a detached subtree. Mark it box-less so the
        # serializer recurses into element descendants but drops this node's own
        # text. Before this, hidden SEO/helper text and the options of closed
        # dropdowns leaked into the output.
        node.rendered = False
        node.visible = False
        node.in_viewport = False
        return
    if node.bbox:
        x, y, w, h = node.bbox
        has_area = w > 0 and h > 0
        node.visible = (not hidden) and has_area
        node.in_viewport = _intersects_viewport(node.bbox, viewport)
    else:
        node.visible = not hidden
        node.in_viewport = True


def _intersects_viewport(
    bbox: tuple[float, float, float, float], viewport: dict
) -> bool:
    x, y, w, h = bbox
    left = viewport["scroll_x"] - _VIEWPORT_MARGIN
    top = viewport["scroll_y"] - _VIEWPORT_MARGIN
    right = viewport["scroll_x"] + viewport["width"] + _VIEWPORT_MARGIN
    bottom = viewport["scroll_y"] + viewport["height"] + _VIEWPORT_MARGIN
    return not (x + w < left or x > right or y + h < top or y > bottom)


def in_collapsed_overlay_portal(node: "EnhancedNode", max_hops: int = 14) -> bool:
    """True if an **ancestor floating-overlay wrapper is collapsed / hidden**.

    Component libraries mount every dropdown / picker / popover panel of a form
    into a portal and keep it there forever, toggling a *hidden* state instead of
    detaching it. The two shapes seen in the wild:

    * the wrapper gets ``display: none`` (Feishu ``…-dropdown-hidden`` together
      with a ``display:none`` rule) — then ``_apply_visibility`` already marks the
      whole subtree ``hidden`` and nothing leaks;
    * the wrapper keeps ``display: block`` and is merely collapsed / visually
      hidden (it carries a ``*-hidden`` state class, or sits behind
      ``display:none`` the snapshot does not resolve) — here the panel **inside**
      keeps a real box and ``visible=True`` by its own styles, so it was
      serialized as if it were on screen. In the 2026-10-01 session a *closed*
      award-year picker surfaced as bare ``[文本]`` rows through the zero-area text
      rescue, and each closed 起止时间 picker contributed its 127 year candidates as
      ``<可点击元素>`` rows (``debug_folder/20261001-17-18``).

    The signal is the explicit collapse marker only, so a *painted* dropdown is
    never touched: a zero-height wrapper alone is normal (a portal wrapper around
    an absolutely positioned panel collapses to ``height: 0`` while the panel is
    visibly on screen — see the Feishu Jobs form, where both portal divs measure
    ``0 × 1905`` even with the picker open). Only ``…-hidden`` state classes count.

    Returns ``True`` ("no own content") for ``<br>``, which self-closes.
    """
    cached = node.cache_in_collapsed_portal
    if cached is not None:
        return cached
    result = False
    if node.tag == "br":
        node.cache_in_collapsed_portal = True
        return True
    current = node.parent
    hops = 0
    while current is not None and hops < max_hops:
        if current.is_element and _class_marks_collapsed_overlay(current):
            result = True
            break
        current = current.parent
        hops += 1
    node.cache_in_collapsed_portal = result
    return result


# ``…-hidden`` / ``…--hidden`` / ``is-hidden`` state tokens used by component
# libraries to collapse a mounted floating overlay (Feishu ``atsx-select-dropdown
# -hidden``, Ant ``ant-select-dropdown-hidden``, Element ``is-hidden``).
_COLLAPSED_OVERLAY_TAILS = ("-hidden", "--hidden", "_hidden", "-collapse", "-closed")
_COLLAPSED_OVERLAY_TOKENS = ("hidden", "is-hidden", "invisible", "collapsed", "closed")


def _class_marks_collapsed_overlay(node: "EnhancedNode") -> bool:
    """True if ``node``'s own class/id marks a collapsed floating overlay.

    Only **overlay-ish** containers qualify (see ``_OVERLAY_CLASS_HINTS`` /
    ``_OVERLAY_ROLES``), which keeps an unrelated ``…-hidden`` utility class on
    ordinary content from hiding a visible subtree.
    """
    if not node.is_element:
        return False
    if _is_document_root(node):  # BID071 文档根是全局状态（滚动锁/布局态），不是浮层
        return False  # BID071
    raw = f"{node.attributes.get('class', '')} {node.attributes.get('id', '')}".lower()
    if not raw.strip():
        return False
    role = (node.role or "").lower()
    is_overlay = role in _OVERLAY_ROLES or any(
        hint in raw for hint in _OVERLAY_CLASS_HINTS
    )
    if not is_overlay:
        return False
    for token in raw.split():
        if token in _COLLAPSED_OVERLAY_TOKENS or token.endswith(_COLLAPSED_OVERLAY_TAILS):
            return True
    return False
