"""薄启动器：双击后静默启动托盘（调同目录 venv 的 pythonw 运行 run.py）。

不写死任何路径：以本程序（exe 或脚本）所在目录为基准，定位 run.py 与 venv。
打包成 exe 用：python build_exe.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _locate_root() -> Path:
    """定位项目根目录：本程序所在目录（要求与 run.py、venv 同级）。"""
    here = Path(sys.argv[0]).resolve().parent
    pythonw = here / "venv" / "Scripts" / "pythonw.exe"
    if (here / "run.py").exists() and pythonw.exists():
        return here
    raise SystemExit(
        "未找到 run.py 与 venv。请将本程序放在项目根目录（与 run.py、venv 同级）。"
    )


def main() -> None:
    root = _locate_root()
    pythonw = root / "venv" / "Scripts" / "pythonw.exe"
    run_py = root / "run.py"
    subprocess.Popen(
        [str(pythonw), str(run_py)],
        cwd=str(root),
        creationflags=CREATE_NO_WINDOW,
    )


if __name__ == "__main__":
    main()
