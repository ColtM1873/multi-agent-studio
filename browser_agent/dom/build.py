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
    paint_order: int = 0
    styles: dict[str, str] = field(default_factory=dict)
    input_value: str = ""
    selected: Optional[bool] = None
    doc_token: str = ""
    is_shadow: bool = False
    is_iframe_root: bool = False
    children: list["EnhancedNode"] = field(default_factory=list)
    parent: Optional["EnhancedNode"] = None

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
        layout = backend_to_layout.get(backend_id, {})
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
        _apply_visibility(enhanced, viewport)
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


def _apply_visibility(node: EnhancedNode, viewport: dict) -> None:
    styles = node.styles
    hidden = styles.get("display") == "none" or styles.get("visibility") in ("hidden", "collapse")
    node.hidden = hidden
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
