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


def package_name(line: str) -> str:
    match = re.match(r"\s*([A-Za-z0-9_.-]+)", line)
    return match.group(1).lower() if match else ""


def main() -> None:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    if not output.is_absolute():
        output = ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = ["# 由 lite/make_requirements.py 从 requirements.txt 自动生成"]
    for raw in SOURCE.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped:
            lines.append("")
            continue
        if stripped.startswith("#"):
            lines.append(raw)
            continue
        if package_name(stripped) in EXCLUDE:
            continue
        lines.append(raw)

    output.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"已生成 Lite 依赖清单：{output}")


if __name__ == "__main__":
    main()
