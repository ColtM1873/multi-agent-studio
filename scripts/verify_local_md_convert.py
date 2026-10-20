"""离线验证「本地 md → HTML」端点的业务逻辑（不弹真实文件选择窗口）。

做法：monkeypatch `win_file_dialog.open_file_dialog` 直接返回临时 md 路径，然后直接调用
端点函数，检查产出的 HTML、命名规则（不覆盖）与取消分支。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api import local_md  # noqa: E402
from app.api.local_md import convert_local_md  # noqa: E402
from app.services import win_file_dialog  # noqa: E402

WORK = Path("tmp_local_md_test")
if WORK.exists():
    shutil.rmtree(WORK)
WORK.mkdir(parents=True)

MD = WORK / "样例文档.md"
MD.write_text(
    "# 标题一\n\n这是一个**加粗**与 `行内代码` 的测试。\n\n"
    "$$\nabc\n$$\n\n```python\nprint('hi')\n```\n",
    encoding="utf-8",
)

# --- 1. 正常转换：同名 .html，配置来自设置里的 export_html_config ---
local_md.win_file_dialog.open_file_dialog = lambda **kw: str(MD)
r = convert_local_md(None)
assert r["canceled"] is False, r
out = Path(r["path"])
assert out.parent.resolve() == MD.parent.resolve(), out
assert out.name == "样例文档.html", out.name
assert out.exists()
html = out.read_text(encoding="utf-8")
assert "<!doctype html>" in html.lower() or "<!DOCTYPE html>" in html
assert "<title>样例文档</title>" in html
assert "标题一" in html
assert "katex" in html.lower()
print("[1] 正常转换 OK ->", out, len(html), "bytes")

# --- 2. 已存在同名 html：追加时间戳另存，绝不覆盖 ---
original = out.read_text(encoding="utf-8")
MD.write_text("# 第二版\n", encoding="utf-8")
r2 = convert_local_md(None)
out2 = Path(r2["path"])
assert out2 != out and out2.exists(), (out, out2)
assert out.read_text(encoding="utf-8") == original, "原有 HTML 被覆盖了"
assert out2.name.startswith("样例文档_") and out2.name.endswith(".html"), out2.name
assert "<title>样例文档</title>" in out2.read_text(encoding="utf-8")
print("[2] 不覆盖 + 时间戳另存 OK ->", out2.name)

# --- 3. 用户取消：canceled=True，不产出文件 ---
local_md.win_file_dialog.open_file_dialog = lambda **kw: None
before = set(WORK.iterdir())
r3 = convert_local_md(None)
assert r3["canceled"] is True and r3["path"] == ""
assert set(WORK.iterdir()) == before
print("[3] 取消分支 OK")

# --- 4. GB18030 编码的 md 也能读 ---
gb_md = WORK / "gbk文档.md"
gb_md.write_bytes("# 中文标题\n\n正文\n".encode("gb18030"))
assert local_md._read_markdown_text(gb_md).strip() == "# 中文标题\n\n正文"
print("[4] GB18030 读取 OK")

# --- 5. BOM / UTF-8 ---
bom_md = WORK / "bom文档.md"
bom_md.write_bytes(b"\xef\xbb\xbf# BOM \xe6\xa0\x87\xe9\xa2\x98\n")
assert local_md._read_markdown_text(bom_md).startswith("# BOM 标题")
print("[5] UTF-8 BOM 读取 OK")

# --- 6. 命名规则单元测试 ---
plain = WORK / "a.md"
plain.write_text("x", encoding="utf-8")
assert local_md._target_html_path(plain).name == "a.html"
(Path(WORK) / "a.html").write_text("old", encoding="utf-8")
t = local_md._target_html_path(plain)
assert t.name.startswith("a_") and t.name.endswith(".html"), t.name
print("[6] 命名规则 OK ->", t.name)

# --- 7. 设置字段向后兼容 ---
from app.config.settings import Settings  # noqa: E402

s = Settings.model_validate_json('{"export_html": false}')
assert s.show_local_md_convert_button is True
assert "show_local_md_convert_button" in s.model_dump()
print("[7] Settings 兼容 OK")

# --- 8. 未开启开关时端点报 400（用假的 load_settings，绝不碰真实 settings.json） ---
from app.config.settings import Settings  # noqa: E402


def _fake_load(_dir):
    s = Settings()
    s.show_local_md_convert_button = False
    return s


_orig_load = local_md.load_settings
local_md.load_settings = _fake_load
try:
    convert_local_md(None)
    raise AssertionError("未开启开关时不应成功")
except Exception as e:  # HTTPException
    assert "未开启" in str(getattr(e, "detail", e)), e
finally:
    local_md.load_settings = _orig_load
print("[8] 关闭开关 -> 400 OK")

shutil.rmtree(WORK, ignore_errors=True)
print("ALL OK")
