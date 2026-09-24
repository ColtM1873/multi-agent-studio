"""Human-like interaction primitives over real CDP Input events.

Anti-bot principle: never call an element's JS API to "activate" it. Move a
real mouse along a jittered path, press/release real buttons, type real key
events. JS fallbacks exist only as a last resort and are flagged ``degraded``.
"""

from __future__ import annotations

import random
import time
from typing import Optional

from .cdp import CDPClient, CDPError
from . import timing


class ActionError(RuntimeError):
    pass


def _sleep(a: float, b: float) -> None:
    time.sleep(random.uniform(a, b))


class ActionExecutor:
    def __init__(self, client: CDPClient, session_id: str) -> None:
        self.client = client
        self.session = session_id
        self._mouse: tuple[float, float] = (random.uniform(100, 400), random.uniform(100, 400))

    # ------------------------------------------------------------------ #
    # low level
    # ------------------------------------------------------------------ #
    def _send(self, method: str, params: dict) -> dict:
        return self.client.send(method, params, session_id=self.session)

    def _send_input(self, method: str, params: dict) -> None:
        """Send an input event best-effort with a short timeout.

        Input events are "fire and forget": once handed to Chrome the effect
        happens regardless of when/if the ACK arrives. On some machines the ACK
        for a single ``Input.dispatchMouseEvent`` can be delayed by seconds;
        waiting on it would turn a ~20-step human-like mouse move into tens of
        seconds. We bound the wait (``timing.cdp.input_timeout``) and ignore a
        timeout — the next step proceeds and a late ACK is matched to a slot
        that no longer exists (harmlessly ignored).
        """
        try:
            self.client.send(
                method, params, session_id=self.session,
                timeout=timing.get().cdp.input_timeout,
            )
        except CDPError:
            pass

    def _send_input_nowait(self, method: str, params: dict) -> None:
        """Send an input event without waiting for its ACK at all.

        Used for intermediate mouse moves: they are pure cosmetics, so a slow
        ACK must never stall the action. Ordering is preserved by the shared
        WebSocket, and the click itself is detected by the DOM diff afterwards.
        """
        try:
            self.client.send_nowait(method, params, session_id=self.session)
        except Exception:  # noqa: BLE001 - best effort
            pass

    def _dispatch_mouse(
        self,
        event_type: str,
        x: float,
        y: float,
        button: str = "none",
        buttons: int = 0,
        click_count: int = 0,
        delta_x: float = 0,
        delta_y: float = 0,
    ) -> None:
        params = {
            "type": event_type,
            "x": x,
            "y": y,
            "button": button,
            "buttons": buttons,
            "clickCount": click_count,
        }
        if event_type == "mouseWheel":
            params["deltaX"] = delta_x
            params["deltaY"] = delta_y
            self._send_input("Input.dispatchMouseEvent", params)
            return
        if event_type == "mouseMoved":
            # Intermediate move: fire-and-forget so a slow ACK cannot stall a
            # multi-step human-like move (observed up to seconds per event).
            self._send_input_nowait("Input.dispatchMouseEvent", params)
            return
        self._send_input("Input.dispatchMouseEvent", params)

    def _resolve(self, backend_node_id: int) -> Optional[str]:
        try:
            res = self._send("DOM.resolveNode", {"backendNodeId": backend_node_id})
            return (res.get("object") or {}).get("objectId")
        except Exception:
            return None

    def _call_on_node(self, object_id: str, function: str, args: Optional[list] = None) -> object:
        res = self._send(
            "Runtime.callFunctionOn",
            {
                "objectId": object_id,
                "functionDeclaration": function,
                "arguments": args or [],
                "returnByValue": True,
            },
        )
        return (res.get("result") or {}).get("value")

    # ------------------------------------------------------------------ #
    # geometry
    # ------------------------------------------------------------------ #
    def _node_box(
        self, backend_node_id: int
    ) -> Optional[tuple[float, float, float, float]]:
        """Return (x, y, w, h) in viewport coordinates, scrolling into view first."""
        try:
            self._send("DOM.scrollIntoViewIfNeeded", {"backendNodeId": backend_node_id})
        except Exception:
            pass
        _sleep(*timing.get().actions.node_box)

        try:
            res = self._send("DOM.getContentQuads", {"backendNodeId": backend_node_id})
            quads = res.get("quads") or []
        except Exception:
            quads = []
        best = None
        best_area = 0.0
        for quad in quads:
            if len(quad) >= 8:
                xs = quad[0::2]
                ys = quad[1::2]
                w = max(xs) - min(xs)
                h = max(ys) - min(ys)
                if w * h > best_area:
                    best_area = w * h
                    best = (min(xs), min(ys), w, h)
        if best:
            return best

        try:
            res = self._send("DOM.getBoxModel", {"backendNodeId": backend_node_id})
            content = (res.get("model") or {}).get("content") or []
            if len(content) >= 8:
                xs = content[0::2]
                ys = content[1::2]
                return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
        except Exception:
            pass

        object_id = self._resolve(backend_node_id)
        if object_id:
            try:
                rect = self._call_on_node(
                    object_id,
                    "function(){const r=this.getBoundingClientRect();"
                    "return {x:r.x,y:r.y,w:r.width,h:r.height};}",
                )
                if rect:
                    return (rect["x"], rect["y"], rect["w"], rect["h"])
            except Exception:
                pass
        return None

    def _node_center(self, backend_node_id: int) -> Optional[tuple[float, float]]:
        box = self._node_box(backend_node_id)
        if not box:
            return None
        x, y, w, h = box
        return (x + w / 2.0, y + h / 2.0)

    # ------------------------------------------------------------------ #
    # human-like mouse
    # ------------------------------------------------------------------ #
    def _human_move(self, target: tuple[float, float]) -> None:
        x0, y0 = self._mouse
        x1, y1 = target
        distance = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        steps = max(8, min(20, int(distance / 40) + 8))
        for i in range(1, steps + 1):
            t = i / steps
            eased = t * t * (3 - 2 * t)
            x = x0 + (x1 - x0) * eased + random.uniform(-1.5, 1.5)
            y = y0 + (y1 - y0) * eased + random.uniform(-1.5, 1.5)
            self._dispatch_mouse("mouseMoved", x, y)
            _sleep(*timing.get().actions.move_step)
        self._dispatch_mouse("mouseMoved", x1, y1)
        self._mouse = (x1, y1)

    def _is_occluded(self, backend_node_id: int, x: float, y: float) -> bool:
        object_id = self._resolve(backend_node_id)
        if not object_id:
            return False
        try:
            result = self._call_on_node(
                object_id,
                "function(x,y){const el=document.elementFromPoint(x,y);"
                "return !(el && (el===this || this.contains(el)));}",
                [{"value": x}, {"value": y}],
            )
            return bool(result)
        except Exception:
            return False

    def _js_click(self, backend_node_id: int) -> None:
        object_id = self._resolve(backend_node_id)
        if object_id:
            try:
                self._call_on_node(object_id, "function(){this.click();}")
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # actions
    # ------------------------------------------------------------------ #
    def click(self, backend_node_id: int) -> dict:
        center = self._node_center(backend_node_id)
        if not center:
            raise ActionError("无法定位元素几何信息")
        x = center[0] + random.uniform(-1.0, 1.0)
        y = center[1] + random.uniform(-1.0, 1.0)
        self._human_move((x, y))
        _sleep(*timing.get().actions.click_hover)

        if self._is_occluded(backend_node_id, x, y):
            self._js_click(backend_node_id)
            return {"degraded": True, "reason": "occluded"}

        self._dispatch_mouse("mousePressed", x, y, button="left", buttons=1, click_count=1)
        _sleep(*timing.get().actions.click_press)
        self._dispatch_mouse("mouseReleased", x, y, button="left", buttons=0, click_count=1)
        return {"degraded": False}

    def input_text(self, backend_node_id: int, text: str) -> dict:
        click_result = self.click(backend_node_id)
        _sleep(*timing.get().actions.input_focus)
        self._clear_field()
        _sleep(*timing.get().actions.input_clear)

        for char in text:
            self._type_char(char)
            _sleep(*timing.get().actions.type_char)

        current = self._read_value(backend_node_id)
        if current is not None and text not in (current or ""):
            self._native_set(backend_node_id, text)
            return {"degraded": True, "reason": "native setter fallback"}
        return {"degraded": click_result.get("degraded", False)}

    def drag(self, backend_node_id: int, pct: int) -> dict:
        pct = max(0, min(100, int(pct)))
        box = self._node_box(backend_node_id)
        if not box:
            raise ActionError("无法定位元素几何信息")
        x, y, w, h = box
        if h >= w:
            start = (x + w / 2.0, y + h / 2.0)
            target = (x + w / 2.0, y + h * (pct / 100.0))
        else:
            start = (x + w / 2.0, y + h / 2.0)
            target = (x + w * (pct / 100.0), y + h / 2.0)

        self._human_move(start)
        _sleep(*timing.get().actions.drag_press)
        self._dispatch_mouse("mousePressed", start[0], start[1], button="left", buttons=1, click_count=1)
        _sleep(*timing.get().actions.drag_press)

        x0, y0 = start
        x1, y1 = target
        steps = max(8, min(20, int(abs(x1 - x0) + abs(y1 - y0)) // 20 + 8))
        for i in range(1, steps + 1):
            t = i / steps
            eased = t * t * (3 - 2 * t)
            mx = x0 + (x1 - x0) * eased
            my = y0 + (y1 - y0) * eased
            self._dispatch_mouse("mouseMoved", mx, my, button="left", buttons=1)
            _sleep(*timing.get().actions.drag_move)
        self._mouse = (x1, y1)
        self._dispatch_mouse("mouseReleased", x1, y1, button="left", buttons=0, click_count=1)
        return {"degraded": False}

    def scroll_step(self, backend_node_id: int, delta_y: float) -> Optional[float]:
        """Dispatch one wheel event over the container; return its new scrollTop."""
        center = self._node_center(backend_node_id)
        if not center:
            raise ActionError("无法定位元素几何信息")
        cx, cy = center
        if self._mouse != center:
            self._human_move((cx, cy))
        self._dispatch_mouse("mouseWheel", cx, cy, delta_x=0, delta_y=delta_y)
        _sleep(*timing.get().actions.scroll_settle)
        return self.scroll_top(backend_node_id)

    def scroll_top(self, backend_node_id: int) -> Optional[float]:
        object_id = self._resolve(backend_node_id)
        if not object_id:
            return None
        try:
            return self._call_on_node(
                object_id,
                "function(){return this.scrollTop + this.scrollLeft;}",
            )
        except Exception:
            return None

    def client_height(self, backend_node_id: int) -> Optional[float]:
        """Visible inner height of a scroll container (CSS px)."""
        object_id = self._resolve(backend_node_id)
        if not object_id:
            return None
        try:
            return self._call_on_node(object_id, "function(){return this.clientHeight;}")
        except Exception:
            return None

    def at_bottom(self, backend_node_id: int) -> bool:
        object_id = self._resolve(backend_node_id)
        if not object_id:
            return True
        try:
            result = self._call_on_node(
                object_id,
                "function(){return this.scrollTop + this.clientHeight "
                ">= this.scrollHeight - 1;}",
            )
            return bool(result)
        except Exception:
            return True

    def at_top(self, backend_node_id: int) -> bool:
        object_id = self._resolve(backend_node_id)
        if not object_id:
            return True
        try:
            return bool(
                self._call_on_node(
                    object_id, "function(){return this.scrollTop <= 1;}"
                )
            )
        except Exception:
            return True

    # ------------------------------------------------------------------ #
    # document-level (whole page) scrolling
    # ------------------------------------------------------------------ #
    def _eval(self, expression: str) -> object:
        result = self._send(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True},
        )
        return (result.get("result") or {}).get("value")

    def dom_signature(self) -> str:
        """A cheap fingerprint of the live DOM, used to detect no-op actions.

        Computed in-page as a rolling hash over ``documentElement.outerHTML`` so
        only a short digest crosses the wire. Unlike a serialized snapshot it is
        immune to the ``:hover`` state our own mouse move induces (hover can
        change *computed* styles such as ``cursor`` without touching the markup),
        so ``signature_before == signature_after`` reliably means "the page was
        not changed".
        """
        try:
            value = self._eval(
                "(() => {"
                "const s = document.documentElement.outerHTML;"
                "let h = 0;"
                "for (let i = 0; i < s.length; i++) { h = (h * 31 + s.charCodeAt(i)) | 0; }"
                "return s.length + ':' + h;"
                "})()"
            )
        except Exception:
            return ""
        return str(value) if value is not None else ""

    def page_scroll_step(self, delta_y: float) -> Optional[float]:
        """Scroll the document by ``delta_y`` CSS px; return the new scrollTop.

        The whole-page scrollbar belongs to the viewport, not an element, so a
        wheel event would be captured by whatever inner scroller sits under the
        cursor. ``window.scrollBy`` deterministically drives the document.
        """
        try:
            self._eval(f"window.scrollBy(0, {float(delta_y)});")
        except Exception:
            pass
        _sleep(*timing.get().actions.scroll_settle)
        return self.page_scroll_top()

    def page_scroll_top(self) -> Optional[float]:
        try:
            return float(
                self._eval(
                    "(document.scrollingElement || document.documentElement).scrollTop"
                )
            )
        except Exception:
            return None

    def page_client_height(self) -> Optional[float]:
        try:
            return float(
                self._eval(
                    "(document.scrollingElement || document.documentElement).clientHeight"
                )
            )
        except Exception:
            return None

    def page_at_bottom(self) -> bool:
        try:
            result = self._eval(
                "(() => {const se = document.scrollingElement "
                "|| document.documentElement; return se.scrollTop + "
                "se.clientHeight >= se.scrollHeight - 1;})()"
            )
            return bool(result)
        except Exception:
            return True

    def page_at_top(self) -> bool:
        try:
            result = self._eval(
                "(() => {const se = document.scrollingElement "
                "|| document.documentElement; return se.scrollTop <= 1;})()"
            )
            return bool(result)
        except Exception:
            return True

    # ------------------------------------------------------------------ #
    # keyboard helpers
    # ------------------------------------------------------------------ #
    def _type_char(self, char: str) -> None:
        self._send_input(
            "Input.dispatchKeyEvent",
            {"type": "keyDown", "text": char, "unmodifiedText": char, "key": char},
        )
        self._send_input("Input.dispatchKeyEvent", {"type": "keyUp", "key": char})

    def _clear_field(self) -> None:
        for key, code, vk in (("a", "KeyA", 65), ("Delete", "Delete", 46)):
            self._send_input(
                "Input.dispatchKeyEvent",
                {
                    "type": "keyDown",
                    "key": key,
                    "code": code,
                    "windowsVirtualKeyCode": vk,
                    "modifiers": 2 if key == "a" else 0,
                },
            )
            self._send_input(
                "Input.dispatchKeyEvent",
                {
                    "type": "keyUp",
                    "key": key,
                    "code": code,
                    "windowsVirtualKeyCode": vk,
                    "modifiers": 2 if key == "a" else 0,
                },
            )

    def _read_value(self, backend_node_id: int) -> Optional[str]:
        object_id = self._resolve(backend_node_id)
        if not object_id:
            return None
        try:
            return self._call_on_node(
                object_id,
                "function(){if('value' in this) return this.value;"
                "return this.textContent;}",
            )
        except Exception:
            return None

    def _native_set(self, backend_node_id: int, text: str) -> None:
        object_id = self._resolve(backend_node_id)
        if not object_id:
            return
        try:
            self._call_on_node(
                object_id,
                "function(value){"
                "const proto = this instanceof HTMLTextAreaElement"
                " ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;"
                "const setter = Object.getOwnPropertyDescriptor(proto,'value')"
                " && Object.getOwnPropertyDescriptor(proto,'value').set;"
                "if (setter) { setter.call(this, value); } else { this.value = value; }"
                "this.dispatchEvent(new Event('input',{bubbles:true}));"
                "this.dispatchEvent(new Event('change',{bubbles:true}));"
                "}",
                [{"value": text}],
            )
        except Exception:
            pass
