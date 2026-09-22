"""Tab display-name registry.

Exposes each open tab to the LLM as a single simple string ``<title><NNN>``
(e.g. ``百度001``). The numeric suffix is a process-wide monotonically
increasing counter: every tab that is ever seen gets the next number, and a
number is never reused — not even after the tab is closed and a new tab with
the same title (or the same url) is opened.

The ``title`` prefix is refreshed live from the current target info, so a tab
that is still loading (``about:blank``) or that later navigates will show its
current title while keeping the same fixed number.
"""

from __future__ import annotations

from typing import Optional

PLACEHOLDER_TITLE = "无标题"


class TabRegistry:
    def __init__(self, placeholder: str = PLACEHOLDER_TITLE) -> None:
        self._seq: dict[str, int] = {}
        self._counter = 0
        self._placeholder = placeholder

    # ------------------------------------------------------------------ #
    # naming
    # ------------------------------------------------------------------ #
    def _ensure(self, target_id: str) -> int:
        if target_id not in self._seq:
            self._counter += 1
            self._seq[target_id] = self._counter
        return self._seq[target_id]

    def _prefix(self, target: dict) -> str:
        title = " ".join(str(target.get("title") or "").split())
        return title or self._placeholder

    def name(self, target: dict) -> str:
        seq = self._ensure(target["targetId"])
        return f"{self._prefix(target)}{seq:03d}"

    def names(self, targets: list[dict]) -> list[str]:
        return [self.name(t) for t in targets]

    def url_map(self, targets: list[dict]) -> dict[str, str]:
        return {
            self.name(t): (t.get("url") or "about:blank")
            for t in targets
        }

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #
    def reconcile(self, targets: list[dict]) -> None:
        """Assign numbers to newly seen tabs and forget closed ones.

        The counter is never decremented, so a forgotten number is never
        handed out again.
        """
        live = {t["targetId"] for t in targets}
        for target_id in [tid for tid in self._seq if tid not in live]:
            del self._seq[target_id]
        for target in targets:
            self._ensure(target["targetId"])

    def forget(self, target_id: str) -> None:
        self._seq.pop(target_id, None)

    # ------------------------------------------------------------------ #
    # reverse lookup (tool-5 / tool-8)
    # ------------------------------------------------------------------ #
    def target_for_name(self, raw: str, targets: list[dict]) -> Optional[str]:
        text = (raw or "").strip()
        if not text:
            return None
        for target in targets:
            if self.name(target) == text:
                return target["targetId"]
        return None
