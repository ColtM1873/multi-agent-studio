"""一键打包脚本：python build_exe.py → 生成 dist/MultiAgentStudio.exe

自动完成：生成图标 → 检查/安装 pyinstaller → 打包 launcher.py → 清理临时文件。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PY = ROOT / "venv" / "Scripts" / "python.exe"
ICON = ROOT / "icon.ico"
NAME = "MultiAgentStudio"
DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "build"


def ensure_icon() -> None:
    if ICON.exists():
        return
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((8, 8, 248, 248), radius=56, fill=(79, 70, 229, 255))
    d.ellipse((80, 80, 176, 176), fill=(255, 255, 255, 255))
    d.ellipse((104, 104, 152, 152), fill=(124, 58, 237, 255))
    img.save(ICON, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("已生成 icon.ico")


def ensure_pyinstaller() -> None:
    r = subprocess.run(
        [str(VENV_PY), "-c", "import PyInstaller"],
        capture_output=True,
    )
    if r.returncode == 0:
        return
    print("未检测到 pyinstaller，正在安装…")
    subprocess.run(
        [str(VENV_PY), "-m", "pip", "install", "pyinstaller"],
        check=True,
    )


def clean() -> None:
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    spec = ROOT / f"{NAME}.spec"
    if spec.exists():
        spec.unlink()


def build() -> None:
    cmd = [
        str(VENV_PY), "-m", "PyInstaller",
        "--onefile", "--noconsole",
        "--icon", str(ICON),
        "--name", NAME,
        "--distpath", str(DIST_DIR),
        "--workpath", str(BUILD_DIR),
        "--specpath", str(BUILD_DIR),
        "--clean",
        str(ROOT / "launcher.py"),
    ]
    print("打包中…")
    subprocess.run(cmd, check=True)


def main() -> None:
    if not VENV_PY.exists():
        raise SystemExit(f"未找到 {VENV_PY}")
    ensure_icon()
    ensure_pyinstaller()
    build()
    clean()
    exe = DIST_DIR / f"{NAME}.exe"
    root_exe = ROOT / f"{NAME}.exe"
    shutil.copy2(exe, root_exe)
    print(f"\n完成：{root_exe}（已复制到项目根目录）")
    print("双击项目根目录的 MultiAgentStudio.exe 即可启动托盘。")


if __name__ == "__main__":
    main()
