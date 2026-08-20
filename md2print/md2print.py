# -*- coding: utf-8 -*-
"""
md2print.py —— 把 Markdown 转换为适合 A4 纸打印的独立 HTML 文件。

特性：
  * 纯 Python 标准库实现，无需 pip 安装任何第三方 Python 包；
  * 数学公式用 KaTeX 渲染，KaTeX 的 CSS / JS / 字体全部本地打包并「内联」进
    输出的 HTML，单文件即可离线打开，不依赖网络；
  * 代码高亮为内置的轻量实现，颜色硬编码，鲁棒、可读；
  * 输出排版针对 A4 纸打印做了优化（@page、分页控制、打印时保留颜色）。

用法（命令行）：
  python md2print.py 文档.md                # 文件模式，生成 文档.html
  python md2print.py 文档.md -o 输出.html   # 指定输出路径
  python md2print.py "一些 **markdown** 文本"   # 字符串模式
  python md2print.py -                     # 从标准输入读取

用法（作为库）：
  from md2print import markdown_to_html, convert
  html_str = markdown_to_html(md_text, title="标题")
  convert(md_text_or_path, output="out.html")
"""

import argparse
import base64
import html
import json
import os
import re
import sys

# ---------------------------------------------------------------------------
# 路径与静态资源
# ---------------------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))
KATEX_DIR = os.path.join(HERE, "static", "katex")


def _read_katex(rel_path):
    with open(os.path.join(KATEX_DIR, rel_path), "rb") as f:
        return f.read()


# ---------------------------------------------------------------------------
# 配置（布局 + 配色），可被用户传入的配置表覆盖
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "page": {
        "size": "A4",
        # 左侧留出 25mm 装订边，右侧 16mm
        "margin": {"top": "18mm", "right": "16mm", "bottom": "18mm", "left": "25mm"},
    },
    "preview": {
        # 屏幕预览：内容居中，左右留白
        "max_width": "860px",
        "canvas": "#f3f4f6",
        "page_padding": "40px 48px",
    },
    "colors": {
        "text": "#1f2328",
        "background": "#ffffff",
        "link": "#2c5f8a",
        "h1": "#1e3a5f",
        "h2": "#24496e",
        "h3": "#2c547d",
        "h4": "#35608c",
        "h5": "#3f6d9c",
        "h6": "#4a7aab",
        "strong": "#b03a2e",
        "em": "#57606a",
        "blockquote_text": "#4b5563",
        "blockquote_border": "#9aa4b2",
        "hr": "#d4dbe2",
        "list_marker": "#2c5f8a",
        "inline_code_bg": "#eef0f2",
        "code_bg": "#f6f8fa",
        "code_border": "#d0d7de",
        "table_border": "#c8cdd3",
        "table_header_bg": "#eef1f5",
        "code_keyword": "#0000ff",
        "code_string": "#a31515",
        "code_comment": "#008000",
        "code_number": "#098658",
        "code_builtin": "#267f99",
        "code_function": "#795e26",
        "code_constant": "#0000ff",
        "code_variable": "#a626a4",
    },
}


def _deep_merge(base, override):
    """深度合并配置，用户可只覆盖部分字段。"""
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


_CSS_RULES = r"""
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC",
               -apple-system, "Helvetica Neue", Arial, sans-serif;
  font-size: 11pt;
  line-height: 1.65;
  color: var(--text);
  background: var(--canvas);
  margin: 0;
  padding: 0;
  -webkit-print-color-adjust: exact;
  print-color-adjust: exact;
}
/* 屏幕预览：灰底 + 居中白卡片，符合常规网页阅读习惯 */
.page {
  max-width: var(--preview_max_width);
  margin: 0 auto;
  padding: var(--preview_padding);
  background: var(--background);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.14);
}
p { margin: 0.6em 0; }
h1, h2, h3, h4, h5, h6 {
  font-weight: 600;
  line-height: 1.3;
  margin: 1.1em 0 0.5em 0;
  break-after: avoid;
  page-break-after: avoid;
}
h1 { font-size: 20pt; color: var(--h1); border-bottom: 1px solid #e2e6ea; padding-bottom: 6px; }
h2 { font-size: 15.5pt; color: var(--h2); border-bottom: 1px solid #eef0f3; padding-bottom: 4px; }
h3 { font-size: 13pt; color: var(--h3); }
h4 { font-size: 12pt; color: var(--h4); }
h5 { font-size: 11pt; color: var(--h5); }
h6 { font-size: 10.5pt; color: var(--h6); }
strong { color: var(--strong); }
em { color: var(--em); }
a { color: var(--link); text-decoration: none; }
a:hover { text-decoration: underline; }
hr {
  border: none;
  border-top: 1px solid var(--hr);
  margin: 1.4em 0;
}
blockquote {
  margin: 0.8em 0;
  padding: 0.1em 0 0.1em 14px;
  border-left: 4px solid var(--blockquote_border);
  color: var(--blockquote_text);
}
code {
  font-family: "JetBrains Mono", Consolas, "Courier New", monospace;
  font-size: 9.5pt;
  background: var(--inline_code_bg);
  border-radius: 3px;
  padding: 1px 5px;
}
pre {
  background: var(--code_bg);
  border: 1px solid var(--code_border);
  border-radius: 5px;
  padding: 10px 12px;
  overflow-x: auto;
  margin: 0.9em 0;
  line-height: 1.5;
  break-inside: avoid;
  page-break-inside: avoid;
}
pre code {
  background: transparent;
  padding: 0;
  font-size: 9.5pt;
}
img { max-width: 100%; height: auto; }
table {
  border-collapse: collapse;
  width: 100%;
  margin: 0.9em 0;
  break-inside: avoid;
  page-break-inside: avoid;
}
th, td {
  border: 1px solid var(--table_border);
  padding: 5px 9px;
  text-align: left;
  vertical-align: top;
  font-size: 10.5pt;
}
th { background: var(--table_header_bg); font-weight: 600; }
ul, ol { margin: 0.5em 0 0.5em 1.6em; padding: 0; }
li { margin: 0.25em 0; }
li::marker { color: var(--list_marker); font-weight: 600; }

/* 数学公式 */
.math-display {
  display: block;
  text-align: center;
  margin: 1em 0;
  overflow-x: auto;
  break-inside: avoid;
  page-break-inside: avoid;
}
.math-inline { display: inline; }

/* 代码高亮颜色（可通过配置覆盖） */
.tok-kw      { color: var(--code_keyword); font-weight: 600; }
.tok-str     { color: var(--code_string); }
.tok-com     { color: var(--code_comment); font-style: italic; }
.tok-num     { color: var(--code_number); }
.tok-builtin { color: var(--code_builtin); }
.tok-fn      { color: var(--code_function); }
.tok-const   { color: var(--code_constant); }
.tok-var     { color: var(--code_variable); }

@media print {
  html, body { background: var(--background); }
  .page {
    max-width: none;
    margin: 0;
    padding: 0;
    box-shadow: none;
  }
  a { color: inherit; }
  pre, blockquote, table, img, .math-display {
    break-inside: avoid;
    page-break-inside: avoid;
  }
  h1, h2, h3, h4, h5, h6 {
    break-after: avoid;
    page-break-after: avoid;
  }
}
"""


def _build_css(config):
    """根据配置生成完整 CSS（:root 变量 + @page + 规则）。"""
    c = config["colors"]
    pv = config["preview"]
    pg = config["page"]
    m = pg["margin"]

    lines = [":root {"]
    for k, v in c.items():
        lines.append("  --%s: %s;" % (k, v))
    lines.append("  --canvas: %s;" % pv["canvas"])
    lines.append("  --preview_max_width: %s;" % pv["max_width"])
    lines.append("  --preview_padding: %s;" % pv["page_padding"])
    lines.append("}")

    vars_css = "\n".join(lines)
    page_css = "@page { size: %s; margin: %s %s %s %s; }" % (
        pg["size"], m["top"], m["right"], m["bottom"], m["left"])
    return vars_css + "\n" + page_css + _CSS_RULES

# ---------------------------------------------------------------------------
# 代码高亮
# ---------------------------------------------------------------------------

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER_RE = re.compile(
    r"0[xX][0-9a-fA-F]+|0[bB][01]+|0[oO][0-7]+"
    r"|\b\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d+)?"
)

_PY_KEYWORDS = {
    "False", "None", "True", "and", "as", "assert", "async", "await", "break",
    "class", "continue", "def", "del", "elif", "else", "except", "finally",
    "for", "from", "global", "if", "import", "in", "is", "lambda", "nonlocal",
    "not", "or", "pass", "raise", "return", "try", "while", "with", "yield",
}
_PY_BUILTINS = {
    "print", "len", "range", "int", "float", "str", "bool", "list", "dict",
    "set", "tuple", "type", "object", "isinstance", "super", "open", "input",
    "enumerate", "zip", "map", "filter", "sorted", "reversed", "sum", "min",
    "max", "abs", "round", "divmod", "pow", "all", "any", "next", "iter",
    "format", "repr", "hasattr", "getattr", "setattr", "delattr", "property",
    "staticmethod", "classmethod", "vars", "dir", "id", "hash", "bytes",
    "bytearray", "frozenset", "complex", "Exception", "ValueError", "TypeError",
    "KeyError", "IndexError", "self", "cls",
}

_JS_KEYWORDS = {
    "break", "case", "catch", "class", "const", "continue", "debugger",
    "default", "delete", "do", "else", "export", "extends", "finally", "for",
    "function", "if", "import", "in", "instanceof", "let", "new", "return",
    "super", "switch", "this", "throw", "try", "typeof", "var", "void", "while",
    "with", "yield", "async", "await", "static", "get", "set", "of",
}
_TS_TYPES = {
    "string", "number", "boolean", "any", "void", "never", "unknown", "symbol",
    "bigint", "object", "enum", "interface", "type", "namespace", "declare",
    "readonly", "abstract", "implements", "keyof", "infer", "as", "is",
}

_JAVA_KEYWORDS = {
    "abstract", "assert", "boolean", "break", "byte", "case", "catch", "char",
    "class", "const", "continue", "default", "do", "double", "else", "enum",
    "extends", "final", "finally", "float", "for", "goto", "if", "implements",
    "import", "instanceof", "int", "interface", "long", "native", "new",
    "package", "private", "protected", "public", "return", "short", "static",
    "strictfp", "super", "switch", "synchronized", "this", "throw", "throws",
    "transient", "try", "void", "volatile", "while",
}

_C_KEYWORDS = {
    "auto", "break", "case", "char", "const", "continue", "default", "do",
    "double", "else", "enum", "extern", "float", "for", "goto", "if", "inline",
    "int", "long", "register", "restrict", "return", "short", "signed",
    "sizeof", "static", "struct", "switch", "typedef", "union", "unsigned",
    "void", "volatile", "while",
}
_CPP_KEYWORDS = _C_KEYWORDS | {
    "class", "namespace", "template", "typename", "public", "private",
    "protected", "virtual", "override", "new", "delete", "this", "friend",
    "operator", "try", "catch", "throw", "using", "constexpr", "nullptr",
    "bool", "true", "false", "static_cast", "dynamic_cast", "const_cast",
    "reinterpret_cast",
}

_CS_KEYWORDS = {
    "abstract", "as", "base", "bool", "break", "byte", "case", "catch", "char",
    "checked", "class", "const", "continue", "decimal", "default", "delegate",
    "do", "double", "else", "enum", "event", "explicit", "extern", "false",
    "finally", "fixed", "float", "for", "foreach", "goto", "if", "implicit",
    "in", "int", "interface", "internal", "is", "lock", "long", "namespace",
    "new", "null", "object", "operator", "out", "override", "params", "private",
    "protected", "public", "readonly", "ref", "return", "sbyte", "sealed",
    "short", "sizeof", "stackalloc", "static", "string", "struct", "switch",
    "this", "throw", "true", "try", "typeof", "uint", "ulong", "unchecked",
    "unsafe", "ushort", "using", "virtual", "void", "volatile", "while", "var",
    "dynamic", "async", "await",
}

_GO_KEYWORDS = {
    "break", "case", "chan", "const", "continue", "default", "defer", "else",
    "fallthrough", "for", "func", "go", "goto", "if", "import", "interface",
    "map", "package", "range", "return", "select", "struct", "switch", "type",
    "var",
}
_GO_BUILTINS = {
    "append", "cap", "close", "complex", "copy", "delete", "imag", "len", "make",
    "new", "panic", "print", "println", "real", "recover", "string", "int",
    "int8", "int16", "int32", "int64", "uint", "uint8", "uint16", "uint32",
    "uint64", "float32", "float64", "bool", "byte", "rune", "error",
}

_RUST_KEYWORDS = {
    "as", "async", "await", "break", "const", "continue", "crate", "dyn",
    "else", "enum", "extern", "false", "fn", "for", "if", "impl", "in", "let",
    "loop", "match", "mod", "move", "mut", "pub", "ref", "return", "self",
    "Self", "static", "struct", "super", "trait", "true", "type", "unsafe",
    "use", "where", "while",
}

_RUBY_KEYWORDS = {
    "BEGIN", "END", "alias", "and", "begin", "break", "case", "class", "def",
    "defined?", "do", "else", "elsif", "end", "ensure", "false", "for", "if",
    "in", "module", "next", "nil", "not", "or", "redo", "rescue", "retry",
    "return", "self", "super", "then", "true", "undef", "unless", "until",
    "when", "while", "yield", "require", "require_relative",
}

_PHP_KEYWORDS = {
    "abstract", "and", "array", "as", "break", "callable", "case", "catch",
    "class", "clone", "const", "continue", "declare", "default", "do", "echo",
    "else", "elseif", "empty", "enddeclare", "endfor", "endforeach", "endif",
    "endswitch", "endwhile", "extends", "final", "finally", "fn", "for",
    "foreach", "function", "global", "goto", "if", "implements", "include",
    "include_once", "instanceof", "insteadof", "interface", "isset", "list",
    "namespace", "new", "or", "print", "private", "protected", "public",
    "require", "require_once", "return", "static", "switch", "throw", "trait",
    "try", "unset", "use", "var", "while", "xor", "yield",
}

_SQL_KEYWORDS = {
    "select", "from", "where", "insert", "into", "values", "update", "set",
    "delete", "create", "table", "alter", "drop", "index", "view", "join",
    "left", "right", "inner", "outer", "full", "on", "group", "by", "order",
    "having", "limit", "offset", "union", "all", "as", "and", "or", "not",
    "null", "is", "in", "like", "between", "case", "when", "then", "else",
    "end", "distinct", "count", "sum", "avg", "min", "max", "primary", "key",
    "foreign", "references", "exists", "asc", "desc",
}

_BASH_KEYWORDS = {
    "if", "then", "else", "elif", "fi", "for", "while", "until", "do", "done",
    "case", "esac", "in", "function", "select", "time", "coproc", "local",
    "export", "readonly", "return", "exit", "break", "continue",
}
_BASH_BUILTINS = {
    "echo", "printf", "cd", "ls", "pwd", "mkdir", "rm", "cp", "mv", "cat",
    "grep", "sed", "awk", "source", "alias", "unset", "set", "shift", "test",
    "read", "env", "chmod", "chown",
}

_GENERIC_KEYWORDS = {
    "if", "else", "elif", "for", "while", "do", "return", "function", "class",
    "def", "struct", "enum", "var", "let", "const", "int", "float", "double",
    "char", "string", "bool", "void", "true", "false", "null", "new", "this",
    "try", "catch", "throw", "import", "include", "public", "private", "static",
}


def _profile(lang):
    """返回某个语言的语法配置。"""
    lang = (lang or "").lower().strip()
    p = {
        "keywords": set(), "builtins": set(), "line_comment": None,
        "block_comments": [], "strings": ['"', "'"],
        "php": False,
    }
    if lang in ("py", "python", "python3"):
        p["keywords"] = _PY_KEYWORDS
        p["builtins"] = _PY_BUILTINS
        p["line_comment"] = "#"
        p["strings"] = ['"""', "'''", '"', "'"]
    elif lang in ("js", "javascript", "jsx", "mjs", "cjs", "ts", "typescript", "tsx"):
        p["keywords"] = _JS_KEYWORDS
        p["builtins"] = _TS_TYPES if lang in ("ts", "typescript", "tsx") else set()
        p["line_comment"] = "//"
        p["block_comments"] = [("/*", "*/")]
        p["strings"] = ["`", '"', "'"]
    elif lang in ("java",):
        p["keywords"] = _JAVA_KEYWORDS
        p["line_comment"] = "//"
        p["block_comments"] = [("/*", "*/")]
    elif lang in ("c",):
        p["keywords"] = _C_KEYWORDS
        p["line_comment"] = "//"
        p["block_comments"] = [("/*", "*/")]
    elif lang in ("cpp", "c++", "cc", "cxx", "hpp", "h"):
        p["keywords"] = _CPP_KEYWORDS
        p["line_comment"] = "//"
        p["block_comments"] = [("/*", "*/")]
    elif lang in ("cs", "csharp", "c#"):
        p["keywords"] = _CS_KEYWORDS
        p["line_comment"] = "//"
        p["block_comments"] = [("/*", "*/")]
    elif lang in ("go", "golang"):
        p["keywords"] = _GO_KEYWORDS
        p["builtins"] = _GO_BUILTINS
        p["line_comment"] = "//"
        p["block_comments"] = [("/*", "*/")]
        p["strings"] = ["`", '"', "'"]
    elif lang in ("rs", "rust"):
        p["keywords"] = _RUST_KEYWORDS
        p["line_comment"] = "//"
        p["block_comments"] = [("/*", "*/")]
    elif lang in ("rb", "ruby"):
        p["keywords"] = _RUBY_KEYWORDS
        p["line_comment"] = "#"
        p["block_comments"] = [("=begin", "=end")]
    elif lang in ("php",):
        p["keywords"] = _PHP_KEYWORDS
        p["line_comment"] = "//"
        p["block_comments"] = [("/*", "*/")]
        p["php"] = True
    elif lang in ("sql", "mysql", "pgsql", "sqlite"):
        p["keywords"] = _SQL_KEYWORDS
        p["line_comment"] = "--"
        p["block_comments"] = [("/*", "*/")]
    elif lang in ("bash", "sh", "shell", "zsh", "ksh"):
        p["keywords"] = _BASH_KEYWORDS
        p["builtins"] = _BASH_BUILTINS
        p["line_comment"] = "#"
        p["strings"] = ['"', "'"]
    elif lang in ("json",):
        p["keywords"] = set()
        p["strings"] = ['"']
    elif lang in ("yaml", "yml"):
        p["keywords"] = set()
        p["line_comment"] = "#"
        p["strings"] = ['"', "'"]
    else:
        p["keywords"] = _GENERIC_KEYWORDS
        p["line_comment"] = "//"
        p["block_comments"] = [("/*", "*/")]
    return p


_CONSTANTS = {
    "true", "false", "null", "nullptr", "undefined", "None", "True", "False",
    "nil", "NaN", "Infinity", "NULL", "TRUE", "FALSE",
}

_TOK_CLASS = {
    "keyword": "tok-kw",
    "string": "tok-str",
    "comment": "tok-com",
    "number": "tok-num",
    "builtin": "tok-builtin",
    "function": "tok-fn",
    "constant": "tok-const",
    "variable": "tok-var",
}


def _esc(text):
    return html.escape(text, quote=False)


def _scan_string(code, i, delim):
    """从 i 处的字符串定界符开始，返回字符串结束位置（含定界符）。"""
    n = len(code)
    dlen = len(delim)
    j = i + dlen
    while j < n:
        if code.startswith(delim, j):
            # 计算前面连续反斜杠个数，偶数个才算真正的结束
            k = j - 1
            bs = 0
            while k >= i and code[k] == "\\":
                bs += 1
                k -= 1
            if bs % 2 == 0:
                return j + dlen
        j += 1
    return n


def _tokenize(code, prof):
    tokens = []
    i, n = 0, len(code)
    keywords = prof["keywords"]
    builtins = prof["builtins"]
    while i < n:
        ch = code[i]
        matched = False
        # 块注释
        for start, end in prof["block_comments"]:
            if code.startswith(start, i):
                j = code.find(end, i + len(start))
                j = n if j < 0 else j + len(end)
                tokens.append(("comment", code[i:j]))
                i = j
                matched = True
                break
        if matched:
            continue
        # 行注释
        if prof["line_comment"] and code.startswith(prof["line_comment"], i):
            j = code.find("\n", i)
            j = n if j < 0 else j
            tokens.append(("comment", code[i:j]))
            i = j
            continue
        # 字符串
        for d in prof["strings"]:
            if code.startswith(d, i):
                j = _scan_string(code, i, d)
                tokens.append(("string", code[i:j]))
                i = j
                matched = True
                break
        if matched:
            continue
        # 数字
        m = _NUMBER_RE.match(code, i)
        if m:
            tokens.append(("number", m.group()))
            i = m.end()
            continue
        # PHP 变量 $name
        if prof.get("php") and ch == "$":
            m = _IDENT_RE.match(code, i + 1)
            if m:
                tokens.append(("variable", "$" + m.group()))
                i = m.end() + 1
                continue
        # 标识符
        m = _IDENT_RE.match(code, i)
        if m:
            w = m.group()
            if w in keywords:
                typ = "keyword"
            elif w in builtins:
                typ = "builtin"
            elif w in _CONSTANTS:
                typ = "constant"
            else:
                k = m.end()
                while k < n and code[k] in " \t":
                    k += 1
                typ = "function" if k < n and code[k] == "(" else "plain"
            tokens.append((typ, w))
            i = m.end()
            continue
        tokens.append(("plain", ch))
        i += 1
    return tokens


def _highlight_generic(code, lang):
    prof = _profile(lang)
    out = []
    for typ, text in _tokenize(code, prof):
        t = _esc(text)
        cls = _TOK_CLASS.get(typ)
        out.append('<span class="%s">%s</span>' % (cls, t) if cls else t)
    return "".join(out)


def _highlight_markup(code):
    """HTML / XML 的轻量高亮。"""
    out = []
    i, n = 0, len(code)
    while i < n:
        if code.startswith("<!--", i):
            j = code.find("-->", i)
            j = n if j < 0 else j + 3
            out.append('<span class="tok-com">%s</span>' % _esc(code[i:j]))
            i = j
            continue
        if code[i] == "<":
            j = code.find(">", i)
            if j < 0:
                out.append(_esc(code[i:]))
                break
            tag = code[i + 1:j]
            out.append(_render_tag(tag))
            i = j + 1
            continue
        j = code.find("<", i)
        if j < 0:
            j = n
        out.append(_esc(code[i:j]))
        i = j
    return "".join(out)


def _render_tag(tag):
    if tag.startswith("/"):
        name = tag[1:].strip()
        return '<span class="tok-com">&lt;/</span><span class="tok-kw">%s</span>%s' % (
            _esc(name), "&gt;")
    if tag.startswith(("!", "?")):
        return '<span class="tok-com">&lt;%s&gt;</span>' % _esc(tag)
    m = re.match(r"(\S+)(.*)", tag, re.DOTALL)
    if not m:
        return "&lt;%s&gt;" % _esc(tag)
    name, rest = m.group(1), m.group(2)
    # 高亮属性名与字符串
    def attr_repl(mm):
        attr = mm.group(1)
        eq = mm.group(2)
        val = mm.group(3)
        return '<span class="tok-builtin">%s</span>%s<span class="tok-str">%s</span>' % (
            _esc(attr), eq, val)
    rest = re.sub(r'([A-Za-z_:][A-Za-z0-9_:.-]*)(\s*=\s*)(\"[^\"]*\"|\'[^\']*\')',
                  attr_repl, rest)
    rest = re.sub(r'([A-Za-z_:][A-Za-z0-9_:.-]*)(?=\s|/?$|/>)',
                  r'<span class="tok-builtin">\1</span>', rest)
    return '&lt;<span class="tok-kw">%s</span>%s&gt;' % (_esc(name), rest)


def _highlight_css(code):
    out = []
    i, n = 0, len(code)
    while i < n:
        if code.startswith("/*", i):
            j = code.find("*/", i)
            j = n if j < 0 else j + 2
            out.append('<span class="tok-com">%s</span>' % _esc(code[i:j]))
            i = j
            continue
        ch = code[i]
        if ch in ('"', "'"):
            j = _scan_string(code, i, ch)
            out.append('<span class="tok-str">%s</span>' % _esc(code[i:j]))
            i = j
            continue
        m = _NUMBER_RE.match(code, i)
        if m:
            out.append('<span class="tok-num">%s</span>' % _esc(m.group()))
            i = m.end()
            continue
        if ch == "#" and i + 1 < n and (code[i + 1].isalnum()):
            m = re.match(r"#[0-9a-fA-F]{3,8}", code[i:])
            if m:
                out.append('<span class="tok-num">%s</span>' % _esc(m.group()))
                i += m.end()
                continue
        if ch == "@":
            m = re.match(r"@[A-Za-z-]+", code[i:])
            if m:
                out.append('<span class="tok-builtin">%s</span>' % _esc(m.group()))
                i += m.end()
                continue
        m = re.match(r"[A-Za-z-]+", code[i:])
        if m:
            w = m.group()
            k = i + m.end()
            while k < n and code[k] in " \t":
                k += 1
            cls = "tok-kw" if k < n and code[k] == ":" else "plain"
            if cls == "plain":
                out.append(_esc(w))
            else:
                out.append('<span class="%s">%s</span>' % (cls, _esc(w)))
            i += m.end()
            continue
        out.append(_esc(ch))
        i += 1
    return "".join(out)


_PLAIN_LANGS = {"", "text", "plain", "plaintext", "txt", "markdown", "md"}


def highlight(code, lang=None):
    """把代码字符串高亮为 HTML（内容已转义）。"""
    lang = (lang or "").lower().strip()
    if lang in _PLAIN_LANGS:
        return _esc(code)
    if lang in ("html", "htm", "xml", "xhtml", "svg"):
        return _highlight_markup(code)
    if lang in ("css",):
        return _highlight_css(code)
    return _highlight_generic(code, lang)


# ---------------------------------------------------------------------------
# 保护：把代码块 / 行内代码 / 数学公式先替换为占位符，避免被 Markdown 破坏
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^(?P<indent>[ ]{0,3})(?P<fence>`{3,}|~{3,})(?P<info>.*)$")


def protect_code_blocks(text):
    """提取 ``` 围栏代码块，返回 (文本, {token: (lang, code)})。"""
    lines = text.split("\n")
    out = []
    store = {}
    idx = 0
    i = 0
    while i < len(lines):
        m = _FENCE_RE.match(lines[i])
        if m:
            fchar = m.group("fence")[0]
            flen = len(m.group("fence"))
            info = m.group("info").strip()
            lang = info.split()[0] if info else ""
            content = []
            i += 1
            while i < len(lines):
                cm = _FENCE_RE.match(lines[i])
                if (cm and cm.group("fence")[0] == fchar
                        and len(cm.group("fence")) >= flen
                        and cm.group("info").strip() == ""):
                    i += 1
                    break
                content.append(lines[i])
                i += 1
            token = "\x00CB%d\x00" % idx
            store[token] = (lang, "\n".join(content))
            out.append(token)
            idx += 1
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out), store


def protect_code_spans(text):
    """提取行内代码 `code`，返回 (文本, {token: content})。"""
    out = []
    store = {}
    idx = 0
    i, n = 0, len(text)
    while i < n:
        if text[i] == "`":
            j = i
            while j < n and text[j] == "`":
                j += 1
            run = j - i
            close = -1
            k = j
            while k < n:
                if text[k] == "`":
                    e = k
                    while e < n and text[e] == "`":
                        e += 1
                    if e - k == run:
                        close = k
                        break
                    k = e
                else:
                    k += 1
            if close >= 0:
                content = text[j:close]
                if content.startswith(" ") and content.endswith(" ") and content.strip(" "):
                    content = content[1:-1]
                token = "\x00CS%d\x00" % idx
                store[token] = content
                out.append(token)
                idx += 1
                i = close + run
                continue
        out.append(text[i])
        i += 1
    return "".join(out), store


_BEGIN_ENV_RE = re.compile(r"\\begin\{([A-Za-z@*]+)\}")


def protect_math(text):
    """提取数学公式，返回 (文本, 块级store, 行内store)。"""
    out = []
    block_store = {}
    inline_store = {}
    idx_b = 0
    idx_i = 0
    i, n = 0, len(text)
    line_start = True
    while i < n:
        # $$ ... $$ （块级）
        if text.startswith("$$", i):
            j = text.find("$$", i + 2)
            if j != -1 and text[i + 2:j].strip():
                body = text[i + 2:j]
                token = "\x00MB%d\x00" % idx_b
                block_store[token] = body
                out.append("\n" + token + "\n")
                idx_b += 1
                i = j + 2
                line_start = (i >= n) or text[i - 1] == "\n"
                continue
        # \[ ... \] （块级）
        if text.startswith("\\[", i):
            j = text.find("\\]", i + 2)
            if j != -1:
                body = text[i + 2:j]
                token = "\x00MB%d\x00" % idx_b
                block_store[token] = body
                out.append("\n" + token + "\n")
                idx_b += 1
                i = j + 2
                line_start = (i >= n) or text[i - 1] == "\n"
                continue
        # \( ... \) （行内）
        if text.startswith("\\(", i):
            j = text.find("\\)", i + 2)
            if j != -1 and "\n" not in text[i:j]:
                body = text[i + 2:j]
                token = "\x00MI%d\x00" % idx_i
                inline_store[token] = body
                out.append(token)
                idx_i += 1
                i = j + 2
                line_start = (i >= n) or text[i - 1] == "\n"
                continue
        # \begin{env} ... \end{env} （块级，需在行首）
        if line_start:
            m = _BEGIN_ENV_RE.match(text, i)
            if m:
                env = m.group(1)
                end_pat = "\\end{" + env + "}"
                j = text.find(end_pat, i)
                if j != -1:
                    body = text[i:j + len(end_pat)]
                    token = "\x00MB%d\x00" % idx_b
                    block_store[token] = body
                    out.append("\n" + token + "\n")
                    idx_b += 1
                    i = j + len(end_pat)
                    line_start = (i >= n) or text[i - 1] == "\n"
                    continue
        # $ ... $ （行内，边界不能是空白）
        if text[i] == "$":
            nxt = text[i + 1] if i + 1 < n else ""
            if nxt and not nxt.isspace() and nxt != "$":
                j = text.find("$", i + 1)
                if (j != -1 and j > i + 1 and "\n" not in text[i + 1:j]
                        and not text[j - 1].isspace()):
                    body = text[i + 1:j]
                    token = "\x00MI%d\x00" % idx_i
                    inline_store[token] = body
                    out.append(token)
                    idx_i += 1
                    i = j + 1
                    line_start = (i >= n) or text[i - 1] == "\n"
                    continue
        out.append(text[i])
        line_start = (text[i] == "\n")
        i += 1
    return "".join(out), block_store, inline_store


# ---------------------------------------------------------------------------
# 行内渲染
# ---------------------------------------------------------------------------

_IMG_RE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\((?P<url>[^)\s]+)(?:\s+[\"'](?P<title>[^\"']*)[\"'])?\)")
_LINK_RE = re.compile(
    r"(?<!!)\[(?P<label>[^\]]*)\]\((?P<url>[^)\s]+)(?:\s+[\"'](?P<title>[^\"']*)[\"'])?\)")
_AUTOLINK_URL_RE = re.compile(r"<(https?://[^>\s]+)>")
_AUTOLINK_MAIL_RE = re.compile(r"<([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})>")


def _protect_links(text, ctx):
    tokens = {}
    idx = [0]

    def tok():
        t = "\x00LK%d\x00" % idx[0]
        idx[0] += 1
        return t

    def img_repl(m):
        t = tok()
        tokens[t] = ("img", m.group("alt"), m.group("url"), m.group("title") or "")
        return t

    def link_repl(m):
        t = tok()
        tokens[t] = ("link", m.group("label"), m.group("url"), m.group("title") or "")
        return t

    text = _IMG_RE.sub(img_repl, text)
    text = _LINK_RE.sub(link_repl, text)

    def url_repl(m):
        t = tok()
        tokens[t] = ("autourl", m.group(1), "", "")
        return t

    def mail_repl(m):
        t = tok()
        tokens[t] = ("automail", m.group(1), "", "")
        return t

    text = _AUTOLINK_URL_RE.sub(url_repl, text)
    text = _AUTOLINK_MAIL_RE.sub(mail_repl, text)
    return text, tokens


def _restore_links(text, tokens, ctx):
    for tok, (kind, a, b, c) in tokens.items():
        if kind == "img":
            alt = html.escape(a, quote=True)
            url = html.escape(b, quote=True)
            title = html.escape(c, quote=True)
            seg = '<img src="%s" alt="%s"' % (url, alt)
            if title:
                seg += ' title="%s"' % title
            seg += ">"
        elif kind == "link":
            label = render_inline(a, ctx)
            url = html.escape(b, quote=True)
            title = html.escape(c, quote=True)
            seg = '<a href="%s"' % url
            if title:
                seg += ' title="%s"' % title
            seg += ">%s</a>" % label
        elif kind == "autourl":
            url = html.escape(a, quote=True)
            seg = '<a href="%s">%s</a>' % (url, a)
        else:  # automail
            seg = '<a href="mailto:%s">%s</a>' % (html.escape(a, quote=True), a)
        text = text.replace(tok, seg)
    return text


def _emphasis(text):
    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"<strong><em>\1</em></strong>", text)
    text = re.sub(r"___(.+?)___", r"<strong><em>\1</em></strong>", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"__(.+?)__", r"<strong>\1</strong>", text)
    text = re.sub(r"~~(.+?)~~", r"<del>\1</del>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"(?<![A-Za-z0-9])_([^_\n]+?)_(?![A-Za-z0-9])", r"<em>\1</em>", text)
    return text


def _restore_spans(text, ctx):
    for tok, content in ctx["cs"].items():
        text = text.replace(tok, "<code>%s</code>" % _esc(content))
    for tok, body in ctx["mi"].items():
        text = text.replace(
            tok, '<span class="math math-inline">\\(%s\\)</span>' % _esc(body))
    return text


def render_inline(text, ctx):
    if not text:
        return ""
    text, link_tokens = _protect_links(text, ctx)
    text = html.escape(text, quote=False)
    text = _emphasis(text)
    text = _restore_links(text, link_tokens, ctx)
    text = _restore_spans(text, ctx)
    return text


# ---------------------------------------------------------------------------
# 块级渲染
# ---------------------------------------------------------------------------

_LIST_RE = re.compile(r"^(?P<indent>[ ]*)(?P<marker>[-*+]|\d{1,9}[.)])\s+(?P<content>.*)$")


def _is_hr(line):
    s = line.strip()
    if not s:
        return False
    for ch in ("-", "*", "_"):
        if re.fullmatch(r"( ?%s ?){3,}" % re.escape(ch), s):
            return True
    return False


def _is_block_start(line):
    s = line.strip()
    if not s:
        return False
    if re.match(r"^#{1,6}\s+", line):
        return True
    if _is_hr(line):
        return True
    if line.lstrip().startswith(">"):
        return True
    if _LIST_RE.match(line):
        return True
    return False


def _parse_atx(line):
    m = re.match(r"^(#{1,6})\s+(.*)$", line)
    if not m:
        return None
    level = len(m.group(1))
    content = re.sub(r"\s+#+\s*$", "", m.group(2).strip())
    return level, content


def _split_row(line):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_table_sep(line):
    cells = _split_row(line)
    if not cells:
        return False
    return all(re.fullmatch(r":?-{1,}:?", c) for c in cells)


def _parse_aligns(line):
    out = []
    for c in _split_row(line):
        if c.startswith(":") and c.endswith(":"):
            out.append("center")
        elif c.endswith(":"):
            out.append("right")
        elif c.startswith(":"):
            out.append("left")
        else:
            out.append("left")
    return out


def _render_table(lines, i, ctx):
    if i + 1 >= len(lines):
        return None
    header = lines[i]
    sep = lines[i + 1]
    if "|" not in header or not _is_table_sep(sep):
        return None
    head_cells = _split_row(header)
    aligns = _parse_aligns(sep)
    body = []
    j = i + 2
    while j < len(lines) and "|" in lines[j] and lines[j].strip() != "":
        body.append(_split_row(lines[j]))
        j += 1

    def render_row(cells, tag):
        cells = cells[:len(head_cells)]
        cells += [""] * (len(head_cells) - len(cells))
        tds = []
        for k, cell in enumerate(cells):
            align = aligns[k] if k < len(aligns) else "left"
            style = ' style="text-align:%s"' % align if align != "left" else ""
            tds.append("<%s%s>%s</%s>" % (tag, style, render_inline(cell, ctx), tag))
        return "<tr>%s</tr>" % "".join(tds)

    html_out = ["<table><thead>", render_row(head_cells, "th"), "</thead><tbody>"]
    for row in body:
        html_out.append(render_row(row, "td"))
    html_out.append("</tbody></table>")
    return "".join(html_out), j


def _parse_list(lines, i, ctx):
    items = []
    base_indent = None
    while i < len(lines):
        m = _LIST_RE.match(lines[i])
        if not m:
            break
        indent = len(m.group("indent"))
        if base_indent is None:
            base_indent = indent
        if indent != base_indent:
            break
        ordered = m.group("marker")[0].isdigit()
        content = m.group("content").strip()
        i += 1
        extra = []
        while i < len(lines) and lines[i].strip() != "" and not _LIST_RE.match(lines[i]):
            if _is_block_start(lines[i]):
                break
            extra.append(lines[i].strip())
            i += 1
        item_html = "<li>" + render_inline(" ".join([content] + extra), ctx)
        if i < len(lines):
            nm = _LIST_RE.match(lines[i])
            if nm and len(nm.group("indent")) > base_indent:
                nested, i = _parse_list(lines, i, ctx)
                item_html += nested
        item_html += "</li>"
        items.append((ordered, item_html))
    if not items:
        return "", i
    tag = "ol" if items[0][0] else "ul"
    return "<%s>%s</%s>" % (tag, "".join(h for _, h in items), tag), i


def parse_blocks(text, ctx):
    lines = text.split("\n")
    html_out = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if stripped == "":
            i += 1
            continue
        # 代码块占位符
        if stripped in ctx["cb"]:
            lang, code = ctx["cb"][stripped]
            lang_cls = ' class="lang-%s"' % _esc(lang) if lang else ""
            html_out.append("<pre><code%s>%s</code></pre>" % (lang_cls, highlight(code, lang)))
            i += 1
            continue
        # 块级数学占位符
        if stripped in ctx["mb"]:
            body = _esc(ctx["mb"][stripped])
            html_out.append('<div class="math math-display">\\[%s\\]</div>' % body)
            i += 1
            continue
        # ATX 标题
        atx = _parse_atx(line)
        if atx:
            level, content = atx
            html_out.append("<h%d>%s</h%d>" % (level, render_inline(content, ctx), level))
            i += 1
            continue
        # Setext 标题
        if i + 1 < n:
            nxt = lines[i + 1].strip()
            if re.fullmatch(r"=+\s*", nxt):
                html_out.append("<h1>%s</h1>" % render_inline(stripped, ctx))
                i += 2
                continue
            if re.fullmatch(r"-{2,}\s*", nxt):
                html_out.append("<h2>%s</h2>" % render_inline(stripped, ctx))
                i += 2
                continue
        # 水平线
        if _is_hr(line):
            html_out.append("<hr/>")
            i += 1
            continue
        # 引用
        if line.lstrip().startswith(">"):
            bq_lines = []
            while i < n and lines[i].strip() != "" and lines[i].lstrip().startswith(">"):
                bq_lines.append(re.sub(r"^\s{0,3}> ?", "", lines[i]))
                i += 1
            html_out.append("<blockquote>%s</blockquote>" % parse_blocks("\n".join(bq_lines), ctx))
            continue
        # 列表
        if _LIST_RE.match(line):
            lst, i = _parse_list(lines, i, ctx)
            html_out.append(lst)
            continue
        # 表格
        table = _render_table(lines, i, ctx)
        if table:
            html_out.append(table[0])
            i = table[1]
            continue
        # 段落
        para = []
        while i < n and lines[i].strip() != "":
            if _is_block_start(lines[i]):
                break
            if lines[i].strip() in ctx["cb"] or lines[i].strip() in ctx["mb"]:
                break
            para.append(lines[i].strip())
            i += 1
        html_out.append("<p>%s</p>" % render_inline(" ".join(para), ctx))
    return "\n".join(html_out)


# ---------------------------------------------------------------------------
# KaTeX 资源内联
# ---------------------------------------------------------------------------

def _katex_css_inlined():
    css = _read_katex("katex.min.css").decode("utf-8")
    fonts_dir = os.path.join(KATEX_DIR, "fonts")

    def repl(m):
        name = m.group(1)
        with open(os.path.join(fonts_dir, name), "rb") as f:
            data = f.read()
        ext = name.rsplit(".", 1)[-1].lower()
        mime = {"woff2": "font/woff2", "woff": "font/woff", "ttf": "font/ttf"}.get(
            ext, "application/octet-stream")
        b64 = base64.b64encode(data).decode("ascii")
        return "url(data:%s;base64,%s)" % (mime, b64)

    return re.sub(r"url\([\"']?fonts/([^)\"']+)[\"']?\)", repl, css)


def _inline_script(js):
    # 防止 </script> 提前闭合
    return re.sub(r"</\s*script", r"<\\/script", js, flags=re.IGNORECASE)


# ---------------------------------------------------------------------------
# 文档组装
# ---------------------------------------------------------------------------

_DOC_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
__CSS__
__KATEX_CSS__
</style>
</head>
<body>
<main class="page">
__BODY__
</main>
<script>
__KATEX_JS__
</script>
<script>
__AUTORENDER_JS__
</script>
<script>
(function () {
  function run() {
    if (window.renderMathInElement) {
      renderMathInElement(document.body, {
        delimiters: [
          {left: '$$', right: '$$', display: true},
          {left: '\\\\[', right: '\\\\]', display: true},
          {left: '\\\\(', right: '\\\\)', display: false}
        ],
        throwOnError: false,
        strict: false
      });
    }
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', run);
  } else {
    run();
  }
})();
</script>
</body>
</html>
"""


def markdown_to_html(md_text, title="Markdown", config=None):
    """把 Markdown 转为 HTML。

    config：可选配置表（dict），用于覆盖 DEFAULT_CONFIG 中的布局/配色，
            支持只覆盖部分字段（如 {"colors": {"h1": "#ff0000"}}）。
    """
    cfg = _deep_merge(DEFAULT_CONFIG, config)
    md_text = _coerce_text(md_text)
    md_text = md_text.replace("\r\n", "\n").replace("\r", "\n")

    text, cb_store = protect_code_blocks(md_text)
    text, cs_store = protect_code_spans(text)
    text, mb_store, mi_store = protect_math(text)

    ctx = {"cb": cb_store, "cs": cs_store, "mb": mb_store, "mi": mi_store}
    body = parse_blocks(text, ctx)

    doc = _DOC_TEMPLATE
    doc = doc.replace("__TITLE__", html.escape(title, quote=True))
    doc = doc.replace("__CSS__", _build_css(cfg))
    doc = doc.replace("__KATEX_CSS__", _katex_css_inlined())
    doc = doc.replace("__BODY__", body)
    doc = doc.replace("__KATEX_JS__", _inline_script(_read_katex("katex.min.js").decode("utf-8")))
    doc = doc.replace("__AUTORENDER_JS__",
                      _inline_script(_read_katex(os.path.join("contrib", "auto-render.min.js")).decode("utf-8")))
    return doc


def _coerce_text(text):
    """把「字符串里用 \\n 表示换行」的形式还原成真正的换行。"""
    if "\n" in text or "\r" in text:
        return text
    if "\\n" in text:
        return text.replace("\\n", "\n").replace("\\t", "\t")
    return text


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------

def convert(md_input, output=None, title=None, config=None):
    """md_input 可以是 .md 文件路径，也可以是 Markdown 文本字符串。

    config：可选配置表（dict），覆盖默认布局/配色。
    """
    if os.path.isfile(md_input):
        with open(md_input, "r", encoding="utf-8") as f:
            md_text = f.read()
        base = os.path.splitext(md_input)[0]
        if output is None:
            output = base + ".html"
        if title is None:
            title = os.path.basename(base) or "Markdown"
    else:
        # 只有「明确的路径形态」才按缺失文件报错，避免把含 URL / 路径的
        # 字符串内容（如 https://x.com、[链接](/a/b)）误判成文件路径。
        looks_like_path = (
            md_input.rstrip().lower().endswith((".md", ".markdown", ".txt"))
            or re.match(r"^[A-Za-z]:[\\/]", md_input)   # C:\ 或 C:/
            or md_input.startswith(("\\", "/"))          # UNC / 绝对路径
        )
        if looks_like_path:
            raise FileNotFoundError("未找到 Markdown 文件：%s" % md_input)
        md_text = md_input
        if output is None:
            output = "output.html"
        if title is None:
            title = "Markdown"

    doc = markdown_to_html(md_text, title, config)
    with open(output, "w", encoding="utf-8") as f:
        f.write(doc)
    return output


def main(argv=None):
    # 尽量用 UTF-8 输出，避免路径含中文时打印报错/乱码
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    parser = argparse.ArgumentParser(
        description="把 Markdown 转换为适合 A4 打印的独立 HTML 文件。")
    parser.add_argument("input",
                        help="Markdown 文件路径，或 Markdown 文本字符串（换行用 \\n 表示）；"
                             "传入 - 表示从标准输入读取")
    parser.add_argument("-o", "--output", help="输出 HTML 文件路径（默认与输入同名 .html）")
    parser.add_argument("-t", "--title", help="HTML 文档标题")
    parser.add_argument("--config", help="JSON 配置文件路径，用于覆盖默认布局/配色")
    parser.add_argument("--print-default-config", action="store_true",
                        help="打印默认配置（JSON）后退出，便于据此编写自己的配置")
    args = parser.parse_args(argv)

    if args.print_default_config:
        print(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2))
        return

    config = None
    if args.config:
        with open(args.config, "r", encoding="utf-8") as f:
            config = json.load(f)

    if args.input == "-":
        md_text = sys.stdin.read()
        out = convert(md_text, args.output, args.title, config)
    else:
        out = convert(args.input, args.output, args.title, config)
    print("已生成 HTML：%s" % os.path.abspath(out))


if __name__ == "__main__":
    main()
