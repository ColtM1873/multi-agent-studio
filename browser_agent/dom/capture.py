"""DOM capture via CDP (four parallel-ish commands, see ID02 主题 A)."""

from __future__ import annotations

from typing import Any

from ..cdp import CDPClient
from .. import timing

REQUIRED_COMPUTED_STYLES = [
    "display",
    "visibility",
    "opacity",
    "overflow-x",
    "overflow-y",
    "cursor",
    "pointer-events",
    "white-space",
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
                "catch (e) {return [];}})()};})()",
                "returnByValue": True,
            },
            session_id=session_id,
        )
        value = (result.get("result") or {}).get("value") or {}
        page["url"] = value.get("url", "")
        page["title"] = value.get("title", "")
        page["ready_state"] = value.get("rs", "")
        page["time_origin"] = str(value.get("to", ""))
        page["scroll_height"] = float(value.get("sh") or 0)
        page["client_height"] = float(value.get("ch") or 0)
        page["scroll_top"] = float(value.get("st") or 0)
        page["device_scale"] = float(value.get("dpr") or 1.0) or 1.0
        tas = value.get("tas")
        if isinstance(tas, list):
            page["textarea_values"] = [v if isinstance(v, str) else "" for v in tas]
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
    }
