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
    config = Config(app="app.main:app", host="127.0.0.1", port=8000, loop="none", log_level="info")
    server = Server(config)
    asyncio.run(server.serve(), loop_factory=_loop_factory())


if __name__ == "__main__":
    main()
