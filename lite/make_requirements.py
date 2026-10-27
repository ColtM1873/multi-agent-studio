"""从根 requirements.txt 生成 Lite 版依赖清单。"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "requirements.txt"
DEFAULT_OUTPUT = ROOT / "build" / "requirements-lite.txt"

EXCLUDE = {
    "sentence-transformers",
    "langchain-huggingface",
    "langchain-community",
}

# Lite 专有依赖（根 requirements.txt 里没有）。
# langgraph-checkpoint-sqlite 提供 AsyncSqliteSaver：Lite 安装时若选 SQLite，
# 会话历史直接落本地文件，不需要 PostgreSQL；选 PostgreSQL 时该包仍保留（体积很小）。
EXTRA = [
    "langgraph-checkpoint-sqlite==3.1.0",
]


def package_name(line: str) -> str:
    match = re.match(r"\s*([A-Za-z0-9_.-]+)", line)
    return match.group(1).lower() if match else ""


def main() -> None:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    if not output.is_absolute():
        output = ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = ["# 由 lite/make_requirements.py 从 requirements.txt 自动生成"]
    present: set[str] = set()
    for raw in SOURCE.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped:
            lines.append("")
            continue
        if stripped.startswith("#"):
            lines.append(raw)
            continue
        name = package_name(stripped)
        if name in EXCLUDE:
            continue
        present.add(name)
        lines.append(raw)

    extra = [item for item in EXTRA if package_name(item) not in present]
    if extra:
        lines.append("")
        lines.append("# Lite 专有依赖（会话历史可选用 SQLite）")
        lines.extend(extra)

    output.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"已生成 Lite 依赖清单：{output}")


if __name__ == "__main__":
    main()
