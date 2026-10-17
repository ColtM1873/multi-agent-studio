"""Visual identity for the CDP browser owned by the project.

Two independent markers are applied to the isolated automation profile:

* a Chromium theme colour (Chrome writes profile Preferences before launch;
  Edge loads the bundled theme extension with ``--load-extension``);
* a `` · Studio`` suffix on every page title, injected through CDP.

The title suffix is a visual marker only. ``strip_title_marker`` removes it at
the DOM/registry ingestion points so it never reaches the model context.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

THEME_COLOR_RGB = "4f46e5"
TITLE_SUFFIX = " · Studio"

_THEME_COLOR_JSON = int("ff" + THEME_COLOR_RGB, 16)
if _THEME_COLOR_JSON >= 1 << 31:
    _THEME_COLOR_JSON -= 1 << 32

_TITLE_SCRIPT = """
(() => {
  const SUFFIX = __STUDIO_SUFFIX__;
  const KEY = "__studioTitleMarkerInstalledV1";
  try {
    if (window.top !== window) return;
  } catch (e) {
    return;
  }
  if (window[KEY]) return;
  window[KEY] = true;

  const apply = () => {
    try {
      const title = document.title || "";
      if (!title.endsWith(SUFFIX)) {
        document.title = title + SUFFIX;
      }
    } catch (e) {}
  };

  const install = () => {
    apply();
    try {
      const target = document.head || document.documentElement;
      if (!target) return;
      const observer = new MutationObserver(apply);
      observer.observe(target, {subtree: true, childList: true, characterData: true});
    } catch (e) {}
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", install, {once: true});
  } else {
    install();
  }
})();
""".replace("__STUDIO_SUFFIX__", json.dumps(TITLE_SUFFIX, ensure_ascii=False))


def marker_enabled() -> bool:
    value = os.environ.get("STUDIO_BROWSER_MARKER", "1").strip().lower()
    return value not in {"0", "false", "off", "no"}


def strip_title_marker(title: object) -> str:
    text = str(title or "")
    suffixes = (TITLE_SUFFIX, TITLE_SUFFIX.lstrip())
    while True:
        candidate = text.rstrip()
        for suffix in suffixes:
            if candidate.endswith(suffix):
                text = candidate[: -len(suffix)].rstrip()
                break
        else:
            return candidate


def _norm(path: Path) -> str:
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        resolved = path.expanduser().absolute()
    return os.path.normcase(str(resolved))


def _is_within(child: Path, parent: Path) -> bool:
    child_norm = _norm(child)
    parent_norm = _norm(parent)
    try:
        return os.path.commonpath([child_norm, parent_norm]) == parent_norm
    except ValueError:
        return False


def _is_default_profile_dir(user_data_dir: Path) -> bool:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return False
    roots = (
        Path(local_app_data) / "Google" / "Chrome" / "User Data",
        Path(local_app_data) / "Google" / "Chrome Beta" / "User Data",
        Path(local_app_data) / "Google" / "Chrome SxS" / "User Data",
        Path(local_app_data) / "Microsoft" / "Edge" / "User Data",
        Path(local_app_data) / "Microsoft" / "Edge Beta" / "User Data",
        Path(local_app_data) / "Microsoft" / "Edge Dev" / "User Data",
        Path(local_app_data) / "Microsoft" / "Edge SxS" / "User Data",
    )
    return any(_is_within(user_data_dir, root) for root in roots)


def _ensure_dict(parent: dict, key: str) -> Optional[dict]:
    value = parent.get(key)
    if value is None:
        value = {}
        parent[key] = value
    if not isinstance(value, dict):
        return None
    return value


def _theme_values() -> dict:
    return {
        "user_color": _THEME_COLOR_JSON,
        "user_color2": _THEME_COLOR_JSON,
        "browser_color_variant": 3,
        "color_variant2": 3,
    }


def theme_extension_path() -> Optional[Path]:
    path = Path(__file__).resolve().parent / "theme_extension"
    return path if (path / "manifest.json").is_file() else None


def edge_extension_args(user_data_dir: str) -> list[str]:
    if not marker_enabled():
        return []
    source = theme_extension_path()
    if source is None:
        return []
    target = Path(user_data_dir) / "_studio_theme"
    try:
        target.mkdir(parents=True, exist_ok=True)
        cached = target / "Cached Theme.pak"
        if cached.exists():
            cached.unlink()
        shutil.copytree(
            source,
            target,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("Cached Theme.pak"),
        )
    except OSError:
        logger.exception("复制 Edge 主题扩展失败: %s", target)
        return []
    return [f"--load-extension={target}"]


def apply_profile_theme(user_data_dir: str, browser_type: Optional[str] = None) -> bool:
    """Write the Studio accent colour into the Chrome automation profile.

    Edge applies the bundled theme extension at launch instead; Chrome ignores
    ``--load-extension`` on branded builds, so its profile Preferences are used.
    The caller invokes this immediately before starting a new browser process.
    """
    if not marker_enabled():
        return False
    if (browser_type or "").strip().lower() == "edge":
        if theme_extension_path() is None:
            logger.warning("Edge 主题扩展缺失，跳过主题标记")
        return True

    root = Path(user_data_dir)
    if _is_default_profile_dir(root):
        logger.warning("拒绝给默认浏览器 profile 写主题: %s", root)
        return False

    prefs_path = root / "Default" / "Preferences"
    prefs: dict = {}
    if prefs_path.is_file():
        try:
            loaded = json.loads(prefs_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("自动化 profile 的 Preferences 无法解析，跳过主题写入: %s", prefs_path)
            return False
        if not isinstance(loaded, dict):
            logger.warning("自动化 profile 的 Preferences 不是对象，跳过主题写入: %s", prefs_path)
            return False
        prefs = loaded

    browser = _ensure_dict(prefs, "browser")
    if browser is None:
        logger.warning("Preferences 的 browser 字段不是对象，跳过主题写入: %s", prefs_path)
        return False
    theme = _ensure_dict(browser, "theme")
    if theme is None:
        logger.warning("Preferences 的 browser.theme 字段不是对象，跳过主题写入: %s", prefs_path)
        return False

    changed = False
    for key, value in _theme_values().items():
        if theme.get(key) != value:
            theme[key] = value
            changed = True

    if not changed:
        return True

    try:
        prefs_path.parent.mkdir(parents=True, exist_ok=True)
        if prefs_path.is_file():
            backup_path = prefs_path.with_name("Preferences.studio-bak")
            if not backup_path.exists():
                shutil.copy2(prefs_path, backup_path)
        tmp_path = prefs_path.with_name("Preferences.studio-tmp")
        tmp_path.write_text(
            json.dumps(prefs, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(tmp_path, prefs_path)
    except OSError:
        logger.exception("写入自动化浏览器主题失败: %s", prefs_path)
        return False

    logger.info("已为自动化浏览器设置 Studio 主题色 (%s)", browser_type or "chromium")
    return True


def apply_title_marker(client, target_id: str, session_id: Optional[str] = None) -> None:
    """Inject the title suffix into the current document and future navigations."""
    if not marker_enabled():
        return
    session_id = session_id or client.attach(target_id)
    client.enable_page_domains(session_id)
    client.send(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": _TITLE_SCRIPT},
        session_id=session_id,
    )
    client.send(
        "Runtime.evaluate",
        {"expression": _TITLE_SCRIPT, "returnByValue": True},
        session_id=session_id,
    )
