"""Lite 版导入期引导。

必须在任何普通业务模块导入之前调用 ``install_persistence_stub()``。
"""

from __future__ import annotations

import sys

_BLOCKED_TOP_LEVEL = {
    "torch",
    "transformers",
    "sentence_transformers",
    "langchain_huggingface",
}


class _LiteImportBlocker:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in _BLOCKED_TOP_LEVEL:
            raise ModuleNotFoundError(f"Lite 版不加载 {fullname}")
        return None


def install_import_blocker() -> None:
    if not any(isinstance(finder, _LiteImportBlocker) for finder in sys.meta_path):
        sys.meta_path.insert(0, _LiteImportBlocker())


def install_persistence_stub() -> None:
    install_import_blocker()
    import app.runtime  # noqa: F401
    from lite.app import persistence_stub

    sys.modules["app.runtime.persistence"] = persistence_stub


def bind_deps(deps_module) -> None:  # noqa: ANN001
    sys.modules["app.deps"] = deps_module
