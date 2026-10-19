"""Lite 版薄启动器：定位项目根目录后拉起 venv 的 pythonw 运行 lite/run_lite.py。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _locate_root() -> Path:
    here = Path(sys.argv[0]).resolve().parent
    if (here / "lite" / "run_lite.py").exists() and (here / "venv").exists():
        return here
    parent = here.parent
    if (parent / "lite" / "run_lite.py").exists() and (parent / "venv").exists():
        return parent
    raise SystemExit(
        "未找到 lite/run_lite.py 与 venv。请将本程序放在项目根目录"
        "（与 lite/、venv 同级）。"
    )


def main() -> None:
    root = _locate_root()
    sys.path.insert(0, str(root))

    import launcher as full_launcher

    pythonw = full_launcher._ensure_venv(root)
    if pythonw is None:
        full_launcher._show_error(
            "运行环境不可用：本机未找到与 venv 兼容的 Python，自动修复失败。\n\n"
            "请安装 Python 3.13 后运行 setup.bat 选择 Lite 版重建环境。"
        )
        return

    subprocess.Popen(
        [str(pythonw), str(root / "lite" / "run_lite.py")],
        cwd=str(root),
        creationflags=full_launcher.CREATE_NO_WINDOW,
    )


if __name__ == "__main__":
    main()
