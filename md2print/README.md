# md2print —— Markdown 转 A4 可打印 HTML

一个**纯 Python 标准库**实现的 Markdown → HTML 转换工具，输出为**单文件、可离线打开**、
针对 **A4 纸打印** 优化的 HTML。

## 特性

- **零第三方 Python 依赖**：只使用 Python 标准库，`pip install` 都不用装，也不依赖网络。
- **数学公式稳定渲染**：使用 [KaTeX](https://katex.org/)（本地打包，MIT 协议）。
  KaTeX 的 CSS / JS / 字体**全部内联**进输出的 HTML，单文件即可离线渲染，
  客户打开时**不会因网络原因渲染失败**。
- **代码高亮鲁棒**：内置轻量高亮器，颜色**硬编码**（VS Code Light+ 配色），
  不依赖任何外部 CDN 或高亮库。
- **屏幕预览友好**：浏览器预览时呈现「灰底 + 居中白卡片」布局，内容在中间、左右留白，
  符合常规网页阅读习惯；打印时自动切换为满幅 A4。
- **A4 打印排版**：`@page { size: A4 }`、**左侧留 25mm 装订边**、代码块/表格/公式跨页保护、
  打印时保留颜色（`print-color-adjust: exact`）。
- **配色可配置**：各级标题、列表标记、加粗/斜体、链接、引用、表格、代码 token 等均可通过
  配置表指定颜色，整体色调统一、易读。

## 环境要求

- Python 3.7+（推荐 3.8+）
- 无需安装任何第三方包，无需联网。

## 使用方法

### 命令行

```bash
# 1) 输入为 .md 文件（绝对路径或相对路径均可），生成同名 .html
python md2print.py /绝对/路径/文档.md

# 指定输出路径与标题
python md2print.py 文档.md -o 输出.html -t "我的文档"

# 2) 输入为 Markdown 字符串（换行用 \n 表示）
python md2print.py "# 标题\n\n正文 **加粗**，公式 $x^2$" -o out.html

# 3) 从标准输入读取
type 文档.md | python md2print.py - -o out.html
```

### 作为库使用

```python
from md2print import markdown_to_html, convert

# 得到 HTML 字符串
html_str = markdown_to_html("# 标题\n\n正文", title="示例")

# 直接生成 HTML 文件（自动识别：文件路径 or 字符串）
convert("/path/to/文档.md")          # -> 生成 文档.html
convert("**字符串** 内容", "out.html")

# 传入配置表（只覆盖想改的部分即可，未提供的字段沿用默认值）
convert("/path/to/文档.md", config={
    "colors": {"h1": "#c0392b", "list_marker": "#c0392b"},
    "preview": {"max_width": "760px"},
})
```

## 支持的语法

- 标题（ATX `#` 与 Setext `===` / `---`）、段落、水平线 `---`
- 加粗 `**x**` / `__x__`、斜体 `*x*` / `_x_`、粗斜体 `***x***`、删除线 `~~x~~`
- 行内代码 `` `code` ``、围栏代码块 ```` ```lang ````（高亮）
- 链接 `[文本](url "标题")`、图片 `![alt](url)`、自动链接 `<https://...>`
- 无序/有序/嵌套列表、引用 `>`、GFM 表格
- **数学公式**：
  - 行内：`$...$` 或 `\(...\)`
  - 块级：`$$...$$` 或 `\[...\]`
  - 多行环境：`\begin{aligned} ... \end{aligned}`（行首书写）

## 代码高亮语言

python / javascript(js,ts,tsx) / java / c / c++ / csharp / go / rust / ruby /
php / sql / bash(shell) / json / yaml / html(xml) / css 等；
未识别的语言会回退到通用高亮。纯文本可用 `text` / `plain` 关闭高亮。

## 配置

配色与布局都可通过一个**配置表**（Python `dict`，或命令行 `--config` 指定的 JSON 文件）覆盖，
只写想改的字段即可，其余自动沿用默认值。

查看默认配置（可据此复制一份再改）：

```bash
python md2print.py x --print-default-config > my_config.json
```

命令行使用配置文件：

```bash
python md2print.py 文档.md --config my_config.json -o 输出.html
```

配置结构如下（节选）：

```json
{
  "page": {
    "size": "A4",
    "margin": { "top": "18mm", "right": "16mm", "bottom": "18mm", "left": "25mm" }
  },
  "preview": {
    "max_width": "860px",
    "canvas": "#f3f4f6",
    "page_padding": "40px 48px"
  },
  "colors": {
    "text": "#1f2328", "background": "#ffffff", "link": "#2c5f8a",
    "h1": "#1e3a5f", "h2": "#24496e", "h3": "#2c547d",
    "h4": "#35608c", "h5": "#3f6d9c", "h6": "#4a7aab",
    "strong": "#b03a2e", "em": "#57606a",
    "blockquote_text": "#4b5563", "blockquote_border": "#9aa4b2",
    "hr": "#d4dbe2", "list_marker": "#2c5f8a",
    "inline_code_bg": "#eef0f2", "code_bg": "#f6f8fa", "code_border": "#d0d7de",
    "table_border": "#c8cdd3", "table_header_bg": "#eef1f5",
    "code_keyword": "#0000ff", "code_string": "#a31515", "code_comment": "#008000",
    "code_number": "#098658", "code_builtin": "#267f99", "code_function": "#795e26",
    "code_constant": "#0000ff", "code_variable": "#a626a4"
  }
}
```

> 说明：`page.margin` 左侧默认 25mm，是给装订留出的边距；`preview` 只影响浏览器屏幕预览，
> 不影响打印（打印时内容铺满 A4，边距由 `page.margin` 控制）。

## 打印提示

用浏览器打开生成的 HTML 后按 `Ctrl+P` 打印，请**开启「背景图形」**（Chrome 打印对话框中
“更多设置 → 背景图形”），否则代码块底色、表格表头底色、公式颜色可能不显示。
纸张选择 **A4**，边距使用“默认”即可。

## 目录结构

```
GoTooGood/
├── md2print.py              # 主程序（单文件，纯标准库）
├── example.md               # 示例输入
├── example.html             # 示例输出（由 example.md 生成）
├── README.md
└── static/
    └── katex/               # 本地打包的 KaTeX 0.16.47（MIT）
        ├── katex.min.css
        ├── katex.min.js
        ├── contrib/auto-render.min.js
        └── fonts/           # 全部字体文件
```

## 实现说明

- 数学公式先被替换为占位符，Markdown 解析后再还原，因此 `\` 反斜杠不会被 Markdown 破坏。
- 代码块、行内代码也在解析前被保护，代码里的 `$`、`*` 等符号不会被误判为公式/强调。
- 最终 HTML 把 KaTeX 的 CSS、JS、字体（base64）全部内联，生成的文件约 1.7 MB，
  换取「单文件即可离线渲染公式」的鲁棒性。

## 许可证

- 本项目代码：可自由使用/修改。
- 内置 KaTeX 0.16.47：MIT 协议，版权归 KaTeX 项目及其作者所有。
