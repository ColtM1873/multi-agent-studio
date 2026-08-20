"""md2print 集成入口：把根目录的 md2print 项目作为包暴露给 app 使用。"""

from .md2print import DEFAULT_CONFIG, _deep_merge, convert, markdown_to_html

__all__ = ["markdown_to_html", "convert", "DEFAULT_CONFIG", "_deep_merge"]
