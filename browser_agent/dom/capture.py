"""DOM capture via CDP (four parallel-ish commands, see ID02 主题 A)."""

from __future__ import annotations

from typing import Any

from ..cdp import CDPClient
from .. import studio_marker, timing

REQUIRED_COMPUTED_STYLES = [
    "display",
    "visibility",
    "opacity",
    "overflow-x",
    "overflow-y",
    "cursor",
    "pointer-events",
    "white-space",
    # ``position`` is needed by the serializer to tell an in-flow descendant of a
    # scroll container (which the container's ``overflow`` clips) from an
    # absolutely/fixed positioned overlay that visually *escapes* it. Without it,
    # a portalled dropdown whose box falls outside the scroller was silently
    # dropped (see ``DOMSerializer._render_child`` / ``_in_clip``).
    "position",
]


def capture_raw(client: CDPClient, session_id: str) -> dict[str, Any]:
    """Run the CDP capture commands and return the raw payloads."""
    document = client.send(
        "DOM.getDocument",
        {"depth": -1, "pierce": True},
        session_id=session_id,
    )
    snapshot = client.send(
        "DOMSnapshot.captureSnapshot",
        {
            "computedStyles": REQUIRED_COMPUTED_STYLES,
            "includePaintOrder": True,
            "includeDOMRects": True,
        },
        session_id=session_id,
    )
    try:
        ax_tree = client.send("Accessibility.getFullAXTree", {}, session_id=session_id)
    except Exception:
        ax_tree = {"nodes": []}

    metrics = client.send("Page.getLayoutMetrics", {}, session_id=session_id)

    page = {
        "url": "",
        "title": "",
        "ready_state": "",
        "time_origin": "",
        "scroll_height": 0.0,
        "client_height": 0.0,
        "scroll_top": 0.0,
        "device_scale": 1.0,
    }
    try:
        result = client.send(
            "Runtime.evaluate",
            {
                "expression": "(() => {const se = document.scrollingElement "
                "|| document.documentElement; return {url: document.URL, "
                "title: document.title, rs: document.readyState, "
                "to: performance.timeOrigin, sh: se ? se.scrollHeight : 0, "
                "ch: se ? se.clientHeight : 0, st: se ? se.scrollTop : 0, "
                "dpr: window.devicePixelRatio || 1, "
                # ``DOMSnapshot`` omits ``inputValue`` for ``<textarea>`` (and
                # ``outerHTML`` keeps the *initial* text), so a filled textarea
                # looked empty/unfilled in the serialized DOM and the model kept
                # re-filling it. Read the live values here (main frame, document
                # order) so ``build`` can restore them onto the textarea nodes.
                "tas: (() => {try {return Array.from("
                "document.querySelectorAll('textarea')).map(t => t.value);}"
                "catch (e) {return [];}})(), "
                # BID111：``contenteditable`` 的 live 文本同样不在 ``DOMSnapshot``
                # 里（它只给 input/textarea 提供 ``inputValue``），``outerHTML`` 也
                # 只保留初始标记。富文本编辑器（tiptap/ProseMirror、Quill…）填完后
                # 若读不到值，序列化标签永远显示「（空）」——模型会以为没填上而反复
                # 重填，甚至据此谎报失败。这里按**主文档顺序**取每个可编辑区域的
                # ``innerText``（用户看到的内容，段落间保留换行），由 ``build`` 回填
                # 到对应节点的 ``input_value``。过滤条件必须与 build 侧（以及
                # ``classify.is_input``）的「可编辑区域」口径逐字一致，否则数量对不
                # 齐就整体跳过（宁可不回填，也不能把值配到别的编辑器上）。
                "ces: (() => {try {return Array.from("
                "document.querySelectorAll('[contenteditable]'))"
                ".filter(el => {try {"
                # 文档根上的 contenteditable 是「整页设计模式」，不是可填写控件
                # （见 ``classify.is_input`` 的 BID111 护栏）；采集侧同样排除，
                # 保证「采集个数」与「树里的可编辑节点个数」口径一致。
                "if (el === document.body || el === document.documentElement)"
                "{ return false; }"
                "const v = el.getAttribute('contenteditable');"
                # 口径必须与 ``build.CONTENTEDITABLE_VALUES`` 逐字一致（含空属性）。
                "return v === '' || v === 'true' || v === 'plaintext-only';}"
                "catch (e) {return false;}})"
                ".map(el => {try {return el.innerText || '';} "
                "catch (e) {return '';}});} catch (e) {return [];}})()};})()",
                "returnByValue": True,
            },
            session_id=session_id,
        )
        value = (result.get("result") or {}).get("value") or {}
        page["url"] = value.get("url", "")
        page["title"] = studio_marker.strip_title_marker(value.get("title", ""))
        page["ready_state"] = value.get("rs", "")
        page["time_origin"] = str(value.get("to", ""))
        page["scroll_height"] = float(value.get("sh") or 0)
        page["client_height"] = float(value.get("ch") or 0)
        page["scroll_top"] = float(value.get("st") or 0)
        page["device_scale"] = float(value.get("dpr") or 1.0) or 1.0
        tas = value.get("tas")
        if isinstance(tas, list):
            page["textarea_values"] = [v if isinstance(v, str) else "" for v in tas]
        ces = value.get("ces")  # BID111
        if isinstance(ces, list):  # BID111
            page["editable_values"] = [v if isinstance(v, str) else "" for v in ces]  # BID111
    except Exception:
        pass

    return {
        "document": document,
        "snapshot": snapshot,
        "ax_tree": ax_tree,
        "metrics": metrics,
        "page": page,
        "styles_order": REQUIRED_COMPUTED_STYLES,
    }


def read_outer_html(client: CDPClient, session_id: str) -> str:
    """Return the page's raw HTML (``documentElement.outerHTML``).

    Used only by the debug logger (see ``browser_agent/debug.py``) to record the
    unprocessed DOM. Returns ``""`` on any failure so it never breaks a tool call.
    """
    try:
        result = client.send(
            "Runtime.evaluate",
            {
                "expression": "document.documentElement ? document.documentElement.outerHTML : ''",
                "returnByValue": True,
            },
            session_id=session_id,
            timeout=timing.get().cdp.outer_html_timeout,
        )
        return str((result.get("result") or {}).get("value") or "")
    except Exception:
        return ""


def viewport_from_metrics(metrics: dict) -> dict[str, float]:
    layout = metrics.get("cssLayoutViewport") or metrics.get("layoutViewport") or {}
    visual = metrics.get("cssVisualViewport") or metrics.get("visualViewport") or {}
    width = layout.get("clientWidth") or visual.get("clientWidth") or 1280
    height = layout.get("clientHeight") or visual.get("clientHeight") or 720
    page_x = layout.get("pageX") or visual.get("pageX") or 0
    page_y = layout.get("pageY") or visual.get("pageY") or 0
    return {
        "width": float(width),
        "height": float(height),
        "scroll_x": float(page_x),
        "scroll_y": float(page_y),
        # Carried so the serializer can say whether the *document* scroller is at
        # its top / bottom (``_scroller_summary``). ``scroll_y`` alone cannot:
        # 0 means "at the top" only if there is more document below.
        "scroll_height": float(layout.get("contentHeight") or visual.get("contentHeight") or height),
        "client_height": float(height),
    }
