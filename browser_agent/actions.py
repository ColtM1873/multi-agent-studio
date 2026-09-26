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
                # Focus first: ``HTMLElement.click()`` does *not* move focus, so a
                # following ``Input.insertText`` would land in whatever element is
                # still focused (see ``input_text``). For non-focusable elements
                # ``focus()`` is a harmless no-op.
                #
                # Then fire the click on the element that actually owns the handler.
                # A JS ``this.click()`` on a *wrapper* does not reach a handler
                # bound to an inner icon (radio / checkbox rows name the wrapper's
                # icon as the control): the handler sits on the descendant, and
                # ``click()`` never walks down. Dispatch on the deepest
                # ``cursor:pointer`` descendant instead, which hits the real control
                # and bubbles exactly like a real mouse click.
                self._call_on_node(
                    object_id,
                    "function(){try{this.focus({preventScroll:true});}catch(e){}"
                    "var tag=this.tagName;"
                    "var semantic=(tag==='A'||tag==='BUTTON'||tag==='INPUT'||"
                    "tag==='SELECT'||tag==='TEXTAREA'||tag==='OPTION'||tag==='SUMMARY');"
                    "var t=this;"
                    "if(!semantic){try{"
                    "var q=this.querySelectorAll('*');"
                    "var cap=q.length<500?q.length:500;"
                    "for(var i=0;i<cap;i++){var cs;"
                    "try{cs=getComputedStyle(q[i]);}catch(e){continue;}"
                    "if(cs&&cs.cursor==='pointer'){t=q[i];break;}}}catch(e){}}"
                    "if(t&&t!==this&&t.dispatchEvent){"
                    "t.dispatchEvent(new MouseEvent('click',"
                    "{bubbles:true,cancelable:true,view:window}));}"
                    "else{try{this.click();}catch(e){}}"
                    "}",
                )
            except Exception:
                pass

    def js_click(self, backend_node_id: int) -> None:
        """Public JS-click (used as a no-op fallback by the controller)."""
        self._js_click(backend_node_id)

    def _quiet_point(self) -> Optional[tuple[float, float]]:
        """Find a point over a non-interactive area, to "park" the mouse.

        The tool's own mouse move induces ``:hover``; component libraries respond
        to hover by toggling classes (e.g. a search box's clear button) or even
        re-rendering, which changes ``outerHTML`` without any functional change.
        Parking the mouse over a non-interactive element before/after an action
        removes that noise so ``dom_signature`` can tell a real change from a no-op.

        A real app page is covered edge-to-edge by app ``<div>``s, so the old
        "only ``<html>``/``<body>`` qualifies" scan almost always found nothing
        and ``park_mouse`` became a silent no-op — the no-op detection then
        stayed fooled by hover, exactly the bug this hook exists to fix. We now
        accept any point whose top element is *not interactive* (no
        ``cursor:pointer``, no control tag, no ``role``/``tabindex``), preferring
        ``<html>``/``<body>`` when present. Parking at a *consistent* point is
        what matters: before/after both sit on the same element, so even a hover
        effect of that element cancels out.
        """
        try:
            value = self._eval(
                "(function(){var w=window.innerWidth,h=window.innerHeight;"
                "function inert(el){if(!el)return false;var t=el.tagName;"
                "if(t==='HTML'||t==='BODY')return true;"
                "if(t==='A'||t==='BUTTON'||t==='INPUT'||t==='SELECT'||"
                "t==='TEXTAREA'||t==='OPTION'||t==='SUMMARY')return false;"
                "var cs;try{cs=getComputedStyle(el);}catch(e){return false;}"
                "if(cs.cursor==='pointer')return false;"
                "if(el.getAttribute&&(el.getAttribute('role')||"
                "el.getAttribute('tabindex')!=null))return false;"
                "return true;}"
                "var best='';"
                "for(var y=1;y<h;y+=29){for(var x=1;x<w;x+=29){"
                "var el=document.elementFromPoint(x,y);"
                "if(!inert(el))continue;"
                "if(el.tagName==='HTML'||el.tagName==='BODY'){return x+':'+y;}"
                "if(!best){best=x+':'+y;}}}"
                "return best;})()"
            )
        except Exception:
            return None
        if not value:
            # Last resort: a fixed, harmless top-left pixel. Parking consistently
            # is enough to cancel hover noise even when the scan finds nothing.
            return (2.0, 2.0)
        try:
            xs, ys = str(value).split(":", 1)
            return float(xs), float(ys)
        except (ValueError, TypeError):
            return (2.0, 2.0)

    def park_mouse(self) -> None:
        """Move the mouse to an inert point without clicking (see ``_quiet_point``)."""
        point = self._quiet_point()
        if point is None or self._mouse == point:
            return
        self._dispatch_mouse("mouseMoved", point[0], point[1])
        self._mouse = point
        _sleep(*timing.get().actions.move_step)

    def blur_active(self) -> None:
        """Blur whatever currently holds focus (best effort).

        A click that moves focus can make a component re-render (e.g. a search
        box toggles its clear-button class when it loses focus). That markup
        change is *not* a functional effect, but it changes ``outerHTML`` and so
        defeats the no-op signature: a dead click then looks like a real change.
        Blurring before each signature capture makes the focus state identical
        on both sides, removing that noise. Blurring is idempotent and does not
        alter values.
        """
        try:
            self._eval(
                "(()=>{const a=document.activeElement;"
                "if(a&&a.blur){a.blur();}return true;})()"
            )
        except Exception:
            pass

    def focus(self, backend_node_id: int) -> None:
        """Move keyboard focus to ``node`` (best effort).

        Used before clearing / typing: the target can be visually occluded by an
        open portal (dropdown / calendar) that overlaps it, in which case the
        real click falls back to a JS ``click()`` and focus stays on the
        previously edited field. Any subsequent ``Input.insertText`` would then
        be typed into *that* field, duplicating the value across two inputs.
        Focusing the intended node first makes typing deterministic.
        """
        object_id = self._resolve(backend_node_id)
        if not object_id:
            return
        try:
            self._call_on_node(
                object_id,
                "function(){try{this.focus({preventScroll:true});"
                "if(typeof this.setSelectionRange==='function'){"
                "try{this.setSelectionRange(this.value.length,this.value.length);}catch(e){}}"
                "}catch(e){}}",
            )
        except Exception:
            pass

    def blur(self, backend_node_id: int) -> None:
        """Remove focus from ``node`` (best effort), firing the blur handlers.

        A controlled date/time picker commonly keeps typed text in the input
        without committing it; blurring makes the component re-render, so a
        re-read afterwards tells the caller whether the value really stuck.
        """
        object_id = self._resolve(backend_node_id)
        if not object_id:
            return
        try:
            self._call_on_node(
                object_id,
                "function(){try{if(this.blur){this.blur();}}catch(e){}}",
            )
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

    # A single ``Input.insertText`` call is the CDP primitive behind IME /
    # clipboard insertion: the page receives one ``beforeinput``/``input`` event
    # carrying the whole string, exactly as if the user pasted it. Typing a long
    # string char-by-char, by contrast, takes tens of seconds (each character
    # waits ``type_char``) and — worse — a controlled framework (React/Ant
    # Design) re-renders on every keystroke, so state can be reverted or written
    # to the wrong field mid-fill. Above this length we paste in one shot.
    PASTE_THRESHOLD = 10

    def input_text(self, backend_node_id: int, text: str) -> dict:
        click_result = self.click(backend_node_id)
        # A click on an element covered by an open portal (a dropdown / calendar
        # overlapping the field) falls back to ``click()`` and does not focus it,
        # so force focus before touching the value. Without this the Ctrl+A /
        # insertText below would operate on the *previously* focused field and
        # write the text into both elements.
        self.focus(backend_node_id)
        _sleep(*timing.get().actions.input_focus)
        if text:
            # Only clear when there is something to replace: an empty ``fill``
            # means "just click / focus this element", so a bare interaction must
            # not wipe a field the caller never meant to touch.
            self._clear_field()
            _sleep(*timing.get().actions.input_clear)

        if len(text) > self.PASTE_THRESHOLD:
            self._insert_text(text)
        else:
            for char in text:
                self._type_char(char)
                _sleep(*timing.get().actions.type_char)

        current = self._read_value(backend_node_id)
        if current is not None and text not in (current or ""):
            self._native_set(backend_node_id, text)
            return {"degraded": True, "reason": "native setter fallback"}
        return {"degraded": click_result.get("degraded", False)}

    def press_enter(self) -> None:
        """Dispatch a real Enter key press (search / form submit).

        Some search boxes have no visible submit control — the only way to submit
        is the Enter key. Sent as a real key event pair (with ``text="\\r"`` on
        keyDown) so page ``keydown``/``keypress`` and form-submit handlers fire,
        exactly as if the user pressed it.
        """
        self._send_input(
            "Input.dispatchKeyEvent",
            {
                "type": "keyDown",
                "key": "Enter",
                "code": "Enter",
                "text": "\r",
                "windowsVirtualKeyCode": 13,
                "nativeVirtualKeyCode": 13,
            },
        )
        self._send_input(
            "Input.dispatchKeyEvent",
            {
                "type": "keyUp",
                "key": "Enter",
                "code": "Enter",
                "windowsVirtualKeyCode": 13,
                "nativeVirtualKeyCode": 13,
            },
        )
        _sleep(*timing.get().actions.type_char)

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

    def _insert_text(self, text: str) -> None:
        """Insert ``text`` in one shot, like a paste (``Input.insertText``).

        This is the browser's own "text arrived from outside a key press"
        channel (IME / clipboard). It fires a real ``beforeinput`` + ``input``
        event, which frameworks with controlled inputs (React / Vue / Ant
        Design) handle exactly like human input — unlike a raw ``.value``
        assignment, which their value tracker ignores. A rejected call is
        swallowed; the caller re-reads the value and falls back to the native
        setter if the text did not land.
        """
        try:
            self._send("Input.insertText", {"text": text})
        except Exception:  # noqa: BLE001 - best effort; caller re-reads the value
            pass

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

    def read_value(self, backend_node_id: int) -> Optional[str]:
        """Public read of a node's current value (used to verify a fill landed)."""
        return self._read_value(backend_node_id)

    def set_value(self, backend_node_id: int, text: str) -> None:
        """Public native value setter (used to correct a fill that did not land).

        Dispatches ``input`` + ``change`` so a framework's controlled value can
        still update; genuinely readonly / masked widgets ignore it, which the
        caller detects by re-reading.
        """
        self._native_set(backend_node_id, text)

    def select_option(self, backend_node_id: int, text: str) -> Optional[str]:
        """Select the ``<option>`` of a native ``<select>`` whose text matches.

        Returns the chosen option's visible text, or ``None`` when nothing
        matched. Matching is whitespace/case-insensitive and tries, in order:
        exact visible text, exact ``value``, then substring (both directions).
        Disabled options are skipped. The native select is set by JS and both
        ``input`` and ``change`` are dispatched so controlled frameworks update.
        """
        object_id = self._resolve(backend_node_id)
        if not object_id:
            return None
        try:
            result = self._call_on_node(
                object_id,
                "function(want){"
                "if(!this||this.tagName!=='SELECT')return null;"
                "var norm=function(s){return String(s==null?'':s).replace(/\\s+/g,' ').trim().toLowerCase();};"
                "var target=norm(want);"
                "var opts=Array.prototype.slice.call(this.options||[]).filter(function(o){return !o.disabled;});"
                "var otxt=function(o){return norm(o.textContent||o.text||'');};"
                "var match=null;"
                "if(target){match=opts.filter(function(o){return otxt(o)===target;})[0];}"
                "if(!match&&target){match=opts.filter(function(o){return norm(o.value)===target;})[0];}"
                "if(!match&&target){match=opts.filter(function(o){return otxt(o).indexOf(target)>=0;})[0];}"
                "if(!match&&target){match=opts.filter(function(o){var t=otxt(o);return t&&target.indexOf(t)>=0;})[0];}"
                "if(!match)return null;"
                "match.selected=true;"
                "try{this.value=match.value;}catch(e){}"
                "this.dispatchEvent(new Event('input',{bubbles:true}));"
                "this.dispatchEvent(new Event('change',{bubbles:true}));"
                "return (match.textContent||match.text||'').replace(/\\s+/g,' ').trim();"
                "}",
                [{"value": text}],
            )
            return result if isinstance(result, str) else None
        except Exception:
            return None

    def read_select_text(self, backend_node_id: int) -> Optional[str]:
        """Visible text of a native ``<select>``'s current selection.

        Reading ``.value`` would yield the option's internal value
        (``0/194/32520``); verification needs the *text* (``共青团员``).
        """
        object_id = self._resolve(backend_node_id)
        if not object_id:
            return None
        try:
            result = self._call_on_node(
                object_id,
                "function(){if(!this||this.tagName!=='SELECT')return null;"
                "var i=this.selectedIndex;var o=(i>=0&&this.options)?this.options[i]:null;"
                "return o?String(o.textContent||o.text||'').replace(/\\s+/g,' ').trim():'';}",
            )
            return result if isinstance(result, str) else None
        except Exception:
            return None

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
