"""薄启动器：双击后静默启动托盘（调同目录 venv 的 pythonw 运行 run.py）。

不写死任何路径：以本程序（exe 或脚本）所在目录为基准，定位 run.py 与 venv。
打包成 exe 用：python build_exe.py

自愈说明：venv 的 pyvenv.cfg 里 `home` 记录的是「创建该 venv 时使用的基础 Python
安装路径」。一旦把 venv 整体拷贝到「Python 装在别的目录」的机器，pythonw 会因找不到
基础解释器而静默退出（exit 103），服务就起不来。这里在启动前做一次**廉价**校验：
健康时零副作用、直接启动；只有检测到失效，才在本机寻找兼容的基础 Python 并改写
pyvenv.cfg（改前留 .bak）。找不到可用 Python 时弹出明确错误提示。
"""

from __future__ import annotations

import ctypes
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _venv_dir(root: Path) -> Path:
    return root / "venv"


def _venv_pythonw(root: Path) -> Path:
    return _venv_dir(root) / "Scripts" / "pythonw.exe"


def _locate_root() -> Path:
    """定位项目根目录：本程序所在目录（要求与 run.py、venv 同级）。"""
    here = Path(sys.argv[0]).resolve().parent
    if (here / "run.py").exists() and _venv_pythonw(here).exists():
        return here
    raise SystemExit(
        "未找到 run.py 与 venv。请将本程序放在项目根目录（与 run.py、venv 同级）。"
    )


def _show_error(message: str) -> None:
    """无控制台（pythonw / --noconsole）下用系统弹窗显示错误，并落一份日志。"""
    try:
        log_dir = Path(sys.argv[0]).resolve().parent / "logs"
        log_dir.mkdir(exist_ok=True)
        with open(log_dir / "launcher.log", "a", encoding="utf-8") as fh:
            fh.write(message + "\n")
    except Exception:
        pass
    try:
        ctypes.windll.user32.MessageBoxW(0, message, "Multi-Agent Studio", 0x10)
    except Exception:
        pass


def _read_pyvenv_cfg(cfg_path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    try:
        text = cfg_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return data
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            data[key.strip().lower()] = value.strip()
    return data


def _venv_is_usable(cfg: dict[str, str]) -> bool:
    """廉价校验：pyvenv.cfg 的 home 指向的基础解释器是否还在。

    只检查 `home\\python.exe` 是否存在——这正是 Python 自身启动时所做的检查，
    对正常安装（含本机新建 venv）零误判、零副作用。
    """
    home = cfg.get("home")
    if not home:
        return False
    try:
        return (Path(home) / "python.exe").exists()
    except OSError:
        return False


def _wanted_version(version: str | None) -> str | None:
    """'3.13.4' -> '3.13'（只保留 major.minor）。"""
    if not version:
        return None
    m = re.match(r"\s*(\d+)\.(\d+)", version)
    return f"{m.group(1)}.{m.group(2)}" if m else None


def _base_candidates(wanted: str | None) -> list[Path]:
    found: list[Path] = []

    def add(path: Path | None) -> None:
        if path is not None:
            found.append(path)

    if wanted:
        try:
            out = subprocess.run(
                ["py", f"-{wanted}", "-c", "import sys; print(sys.executable)"],
                capture_output=True,
                text=True,
                timeout=15,
                creationflags=CREATE_NO_WINDOW,
            )
            if out.returncode == 0:
                add(Path(out.stdout.strip()))
        except Exception:
            pass

    for path in _registry_candidates(wanted):
        add(path)

    if wanted:
        tag = wanted.replace(".", "")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            add(Path(local) / "Programs" / "Python" / f"Python{tag}" / "python.exe")
        for env in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(env)
            if base:
                add(Path(base) / f"Python{tag}" / "python.exe")
        add(Path(f"C:/Python{tag}") / "python.exe")

    which = shutil.which("python")
    if which:
        add(Path(which))

    unique: list[Path] = []
    seen: set[str] = set()
    for path in found:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _registry_candidates(wanted: str | None):
    try:
        import winreg
    except Exception:
        return
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                root = winreg.OpenKey(
                    hive, r"SOFTWARE\Python\PythonCore", 0, winreg.KEY_READ | view
                )
            except OSError:
                continue
            try:
                index = 0
                while True:
                    try:
                        name = winreg.EnumKey(root, index)
                    except OSError:
                        break
                    index += 1
                    if wanted and name != wanted:
                        continue
                    try:
                        key = winreg.OpenKey(
                            root, name + r"\InstallPath", 0, winreg.KEY_READ | view
                        )
                    except OSError:
                        continue
                    try:
                        value, _ = winreg.QueryValueEx(key, "")
                        if value:
                            yield Path(value) / "python.exe"
                    except OSError:
                        pass
                    finally:
                        key.Close()
            finally:
                root.Close()


def _base_ok(exe: Path, wanted: str | None) -> bool:
    try:
        if not exe.exists():
            return False
    except OSError:
        return False
    if not wanted:
        return True
    try:
        out = subprocess.run(
            [str(exe), "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=CREATE_NO_WINDOW,
        )
        return out.returncode == 0 and out.stdout.strip() == wanted
    except Exception:
        return False


def _find_base_python(version: str | None) -> Path | None:
    wanted = _wanted_version(version)
    for candidate in _base_candidates(wanted):
        if _base_ok(candidate, wanted):
            try:
                return candidate.resolve()
            except OSError:
                return candidate
    return None


def _repair_pyvenv_cfg(cfg_path: Path, base_exe: Path) -> bool:
    """把 pyvenv.cfg 的 home/executable 改写到本机基础 Python；改前留一份 .bak。"""
    try:
        text = cfg_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False

    home = str(base_exe.parent)
    out: list[str] = []
    seen_home = seen_exe = False
    for line in text.splitlines():
        key = line.partition("=")[0].strip().lower()
        if key == "home":
            out.append(f"home = {home}")
            seen_home = True
        elif key == "executable":
            out.append(f"executable = {base_exe}")
            seen_exe = True
        else:
            out.append(line)
    if not seen_home:
        out.append(f"home = {home}")
    if not seen_exe:
        out.append(f"executable = {base_exe}")

    try:
        backup = cfg_path.with_name("pyvenv.cfg.bak")
        if not backup.exists():
            shutil.copy2(cfg_path, backup)
        cfg_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    except OSError:
        return False
    return True


def _ensure_venv(root: Path) -> Path | None:
    """返回可用的 pythonw 路径；健康时零副作用，失效时尝试自愈，失败返回 None。"""
    pythonw = _venv_pythonw(root)
    cfg_path = _venv_dir(root) / "pyvenv.cfg"
    if not cfg_path.exists():
        return pythonw
    cfg = _read_pyvenv_cfg(cfg_path)
    if _venv_is_usable(cfg):
        return pythonw
    base = _find_base_python(cfg.get("version"))
    if base is None or not _repair_pyvenv_cfg(cfg_path, base):
        return None
    return pythonw


def main() -> None:
    try:
        root = _locate_root()
    except SystemExit as exc:
        _show_error(str(exc))
        return

    pythonw = _ensure_venv(root)
    if pythonw is None:
        _show_error(
            "运行环境不可用：本机未找到与 venv 兼容的 Python，自动修复失败。\n\n"
            "请安装 Python 3.13 后运行 setup.bat 重建环境，"
            "或将项目放到已正确安装 Python 3.13 的机器上再试。"
        )
        return

    run_py = root / "run.py"
    subprocess.Popen(
        [str(pythonw), str(run_py)],
        cwd=str(root),
        creationflags=CREATE_NO_WINDOW,
    )


if __name__ == "__main__":
    main()
