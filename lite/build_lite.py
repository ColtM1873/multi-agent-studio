"""Lite 版打包脚本：生成 MultiAgentStudio-Lite.exe。"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import build_exe as base

NAME = "MultiAgentStudio-Lite"
BUILD_DIR = ROOT / "build_lite"
ENTRY = ROOT / "lite" / "launcher_lite.py"


def clean() -> None:
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    spec = BUILD_DIR / f"{NAME}.spec"
    if spec.exists():
        spec.unlink()


def build() -> None:
    cmd = [
        str(base.VENV_PY),
        "-m",
        "PyInstaller",
        "--onefile",
        "--noconsole",
        "--icon",
        str(base.ICON),
        "--name",
        NAME,
        "--distpath",
        str(base.DIST_DIR),
        "--workpath",
        str(BUILD_DIR),
        "--specpath",
        str(BUILD_DIR),
        "--clean",
        str(ENTRY),
    ]
    print("打包 Lite 版中…")
    subprocess.run(cmd, check=True)


def main() -> None:
    if not base.VENV_PY.exists():
        raise SystemExit(f"未找到 {base.VENV_PY}")
    base.ensure_icon()
    base.ensure_pyinstaller()
    clean()
    build()
    clean()
    exe = base.DIST_DIR / f"{NAME}.exe"
    root_exe = ROOT / f"{NAME}.exe"
    shutil.copy2(exe, root_exe)
    print(f"\n完成：{root_exe}（已复制到项目根目录）")
    print("双击项目根目录的 MultiAgentStudio-Lite.exe 即可启动。")


if __name__ == "__main__":
    main()
