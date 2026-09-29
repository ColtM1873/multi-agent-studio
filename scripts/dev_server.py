"""开发用启动脚本（不自动开浏览器）。 python scripts/dev_server.py"""

from __future__ import annotations

import asyncio
import selectors
import sys
from pathlib import Path

from uvicorn import Config, Server

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _loop_factory():
    if sys.platform == "win32":
        return lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
    return None


def main() -> None:
    # 可选诊断探针（BROWSER_TOOL_TIMING=1 时启用；未设置时零开销）。
    try:
        from browser_agent import diagnostics

        diagnostics.install()
    except Exception:
        pass

    config = Config(app="app.main:app", host="127.0.0.1", port=8000, loop="none", log_level="info")
    server = Server(config)
    asyncio.run(server.serve(), loop_factory=_loop_factory())


if __name__ == "__main__":
    main()
