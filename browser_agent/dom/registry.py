"""Stable, per-tab element naming.

Names are opaque short ids (``e1``, ``e2`` ...). They are stable across steps
within a tab: a node seen in step k keeps the same name in step k+1 as long as
it is still the same DOM node. Names are never reused, so a reference to a node
that disappeared after a navigation resolves to "stale" instead of silently
pointing at a different element.
"""

from __future__ import annotations

from typing import Optional

from .build import EnhancedNode, EnhancedTree


class NameRegistry:
    def __init__(self) -> None:
        self._key_to_name: dict[tuple[str, int], str] = {}
        self._name_to_key: dict[str, tuple[str, int]] = {}
        self._counter = 0
        self._active_keys: set[tuple[str, int]] = set()

    def get_or_create(self, node: EnhancedNode) -> str:
        key = node.key
        existing = self._key_to_name.get(key)
        if existing is not None:
            self._active_keys.add(key)
            return existing
        self._counter += 1
        name = f"e{self._counter}"
        self._key_to_name[key] = name
        self._name_to_key[name] = key
        self._active_keys.add(key)
        return name

    def reconcile(self, tree: EnhancedTree) -> None:
        """Refresh which keys are currently present in the tab."""
        self._active_keys = {n.key for n in tree.nodes if n.is_element}

    def lookup(self, name: str) -> Optional[tuple[str, int]]:
        return self._name_to_key.get(name)

    def is_active(self, name: str) -> bool:
        key = self._name_to_key.get(name)
        return key is not None and key in self._active_keys

    def name_for_key(self, key: tuple[str, int]) -> Optional[str]:
        return self._key_to_name.get(key)
