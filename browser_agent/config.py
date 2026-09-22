"""Browser discovery + config.json persistence.

Browser executable is searched only once; the resolved path is cached in
``config.json`` at the project root. Subsequent calls read the cache first.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
DEFAULT_PROFILE_DIR = ROOT / ".chrome-profile"

_CHROME_CANDIDATES = [
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles%\Google\Chrome Beta\Application\chrome.exe",
    r"%LOCALAPPDATA%\Google\Chrome SxS\Application\chrome.exe",
]

_EDGE_CANDIDATES = [
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe",
]

_DEFAULT_CONFIG = {
    "browser_path": None,
    "browser_type": None,
    "user_data_dir": str(DEFAULT_PROFILE_DIR),
    "last_port": None,
    "last_cdp_url": None,
    "search_paths": [],
}


def _expand(path: str) -> str:
    return os.path.expandvars(path)


def _first_existing(candidates: list[str]) -> Optional[str]:
    for cand in candidates:
        expanded = _expand(cand)
        if Path(expanded).is_file():
            return str(Path(expanded))
    return None


def load_config() -> dict:
    cfg = dict(_DEFAULT_CONFIG)
    if CONFIG_PATH.is_file():
        try:
            with CONFIG_PATH.open("r", encoding="utf-8") as fh:
                stored = json.load(fh)
            if isinstance(stored, dict):
                cfg.update(stored)
        except (json.JSONDecodeError, OSError):
            pass
    if not cfg.get("user_data_dir"):
        cfg["user_data_dir"] = str(DEFAULT_PROFILE_DIR)
    return cfg


def save_config(cfg: dict) -> None:
    try:
        with CONFIG_PATH.open("w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2, ensure_ascii=False)
    except OSError:
        pass


def discover_browser() -> tuple[str, str]:
    """Return ``(browser_path, browser_type)``.

    Reads the cache first; only scans known install locations when the cached
    path is missing or invalid. The result is persisted back to config.json.
    """
    cfg = load_config()

    cached_path = cfg.get("browser_path")
    if cached_path and Path(cached_path).is_file():
        return cached_path, cfg.get("browser_type") or _type_from_path(cached_path)

    for browser_type, candidates in (("chrome", _CHROME_CANDIDATES), ("edge", _EDGE_CANDIDATES)):
        found = _first_existing(candidates)
        if found:
            cfg["browser_path"] = found
            cfg["browser_type"] = browser_type
            cfg["search_paths"] = [_expand(c) for c in candidates]
            save_config(cfg)
            return found, browser_type

    raise FileNotFoundError(
        "未找到 Chrome 或 Edge。请手动在 config.json 的 browser_path 中指定浏览器路径。"
    )


def _type_from_path(path: str) -> str:
    name = Path(path).name.lower()
    if "msedge" in name:
        return "edge"
    return "chrome"


def get_user_data_dir() -> str:
    cfg = load_config()
    path = Path(cfg["user_data_dir"])
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def update_config(**kwargs) -> dict:
    cfg = load_config()
    cfg.update(kwargs)
    save_config(cfg)
    return cfg
