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
THEME_TEXT_RGB = "ffffff"
TITLE_SUFFIX = " · Studio"

# 旧代码/文档可能直接引用这个常量；默认主色保持不变。
_THEME_COLOR_JSON = int("ff" + THEME_COLOR_RGB, 16)
if _THEME_COLOR_JSON >= 1 << 31:
    _THEME_COLOR_JSON -= 1 << 32


def _coerce_rgb(value: object, fallback: str) -> str:
    """把 ``#rrggbb`` / ``rrggbb`` / ``#rgb`` 归一成 6 位小写十六进制。"""
    text = str(value or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in text):
        return fallback
    return text.lower()


def _theme_palette(theme: Optional[dict]) -> tuple[str, str]:
    data = theme if isinstance(theme, dict) else {}
    border = _coerce_rgb(data.get("border_color"), THEME_COLOR_RGB)
    text = _coerce_rgb(data.get("text_color"), THEME_TEXT_RGB)
    return border, text


def _color_json(hex_rgb: str) -> int:
    value = int("ff" + hex_rgb, 16)
    if value >= 1 << 31:
        value -= 1 << 32
    return value


def _color_tuple(hex_rgb: str) -> list[int]:
    return [int(hex_rgb[i : i + 2], 16) for i in (0, 2, 4)]

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


def _theme_values(theme: Optional[dict] = None) -> dict:
    border, text = _theme_palette(theme)
    # Chrome 的 Material You 主题用两个种子色：user_color 决定标签栏/边框主色，
    # user_color2 参与地址栏选中文字/强调色。两者写成不同颜色时，同一窗口会出现
    # 两个可区分的视觉层；沿用旧键名以兼容 Chrome 155/156。
    return {
        "user_color": _color_json(border),
        "user_color2": _color_json(text),
        "browser_color_variant": 3,
        "color_variant2": 3,
    }


def _edge_manifest(theme: Optional[dict] = None) -> dict:
    source = theme_extension_path()
    if source is None:
        return {}
    try:
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(manifest, dict):
        return {}
    if theme:
        border, text = _theme_palette(theme)
        colors = manifest.setdefault("theme", {}).setdefault("colors", {})
        if isinstance(colors, dict):
            for key in ("frame", "frame_inactive", "toolbar", "button_background"):
                colors[key] = _color_tuple(border)
            for key in ("tab_text", "bookmark_text", "toolbar_text", "tab_background_text"):
                colors[key] = _color_tuple(text)
    return manifest


def theme_extension_path() -> Optional[Path]:
    path = Path(__file__).resolve().parent / "theme_extension"
    return path if (path / "manifest.json").is_file() else None


def edge_extension_args(user_data_dir: str, theme: Optional[dict] = None) -> list[str]:
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
        # Edge 每次启动都会重新读取 unpacked 主题；把用户选的两个颜色写进
        # profile 副本，避免所有会话共用仓库里的默认 manifest。
        manifest = _edge_manifest(theme)
        if manifest:
            (target / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    except OSError:
        logger.exception("复制 Edge 主题扩展失败: %s", target)
        return []
    return [f"--load-extension={target}"]


def apply_profile_theme(
    user_data_dir: str,
    browser_type: Optional[str] = None,
    theme: Optional[dict] = None,
) -> bool:
    """Write the per-session browser colours into the automation profile.

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
    pref_theme = _ensure_dict(browser, "theme")
    if pref_theme is None:
        logger.warning("Preferences 的 browser.theme 字段不是对象，跳过主题写入: %s", prefs_path)
        return False

    changed = False
    for key, value in _theme_values(theme).items():
        if pref_theme.get(key) != value:
            pref_theme[key] = value
            changed = True

    # Chrome 156 实测：profile 里一旦存在这个「主题 pref 已完成 synced → non-syncing
    # 迁移」标记，Chrome 就会忽略 browser.theme 的 user_color*，窗口回退到默认主题
    # （表现为默认蓝色/浅紫），而会话列表用的还是我们自己的 border_color，于是出现
    # 「列表是青的、浏览器是蓝的」。删掉标记后 Chrome 每次启动都会重新迁移并应用种子色，
    # 所以这里必须把它一并清掉（不能只在首次创建 profile 时依赖迁移）。
    if prefs.pop("syncing_theme_prefs_migrated_to_non_syncing", None) is not None:
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

    border, text = _theme_palette(theme)
    logger.info(
        "已为自动化浏览器设置主题色 (%s): border=#%s text=#%s",
        browser_type or "chromium",
        border,
        text,
    )
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
