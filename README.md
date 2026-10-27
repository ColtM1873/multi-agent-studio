[中文](#multi-agent-studio) | [English](#multi-agent-studio-english)

# Multi-Agent Studio

> 基于 [LangGraph General-Use Multi-Agent Framework](https://github.com/ColtM1873/LangGraph-General-Use-Multi-Agent-Framework) 构建的**本地多智能体工作台**：Supervisor–Worker 编排 + 现代 Web GUI，Windows 单机运行。

## 目录

- [快速开始](#快速开始)
- [版本迁移（升级到新版本）](#版本迁移升级到新版本)
- [特性](#特性)
- [界面预览](#界面预览)
- [架构](#架构)
- [本地 MCP](#本地-mcp)
- [许可证](#许可证)

## 快速开始

### 第一步：前置软件

1. **PostgreSQL**：如果使用Lite版本的sqlite子版本，则不需要

### 第二步：下载

1. 打开本仓库页面，点右侧 **Releases**，在最新版本下点击 **Source code (zip)** 下载（或者直接git clone）。

### 第三步：解压

1. 把 zip 解压，得到一个 `multi-agent-studio-*` 文件夹。

### 第四步：进入项目文件夹

1. 双击点进去，一直点到能看到 `setup.bat`、`build_exe.bat` 的那一层。

### 第五步：一键安装 + 打包

1. 双击 `setup.bat`。
2. 会先跑一个cmd黑色窗口，然后可能会再跑一个蓝色背景的提权powershell窗口。请不要在安装完成前关闭黑色窗口（或者蓝色窗口）。
3. 安装过程中可能会要求提权，说明因为没有开启长路径支持，导致支持库安装失败。此时请授权。
4. 如果没有python，会提示要不要一键自动安装python（本项目需要Python才能跑）
5. 轻量版分为postgres版本和sqlite版本，推荐sqlite版本，不需要额外安装约1GB存储占用的PostgreSQL
6. 对于非轻量版，会提示一键安装 pgvector ，可以考虑一键安装。
7. 安装完成后，可以在桌面看见Studio的快捷方式，双击即可开始使用

### 第六步：使用说明

1. （如果是postgres版本）安装PostgreSQL时，会让填写密码，以及端口号，请不要乱填。
   程序新建 multi-agent配置时，需要填入 安装PostgreSQL时的密码和端口号（以及用户名）（需要这些信息来连接数据库）
2. （如果是postgres版本）postgres连接设置中，连接串模板  postgresql://用户名:密码@localhost:端口号/?sslmode=disable  （默认本地数据库情景）
3. 每个选项旁边的 小圆圈，一个感叹号，里面都有比较详细的解释，可以把鼠标移过去查看


## 版本迁移（升级到新版本）

1. 下载最新版本release的源码（或者直接克隆库），解压后，点到有“setup.bat”那一层，全选，复制
2. 到你实际使用的Studio的本地文件，也点到有“setup.bat”那一层，粘贴，相同的文件选择全部覆盖（替换）
3. 通常情况下升级就完成了，可以直接点桌面的快捷方式进行运行
4. 如果跑起来有问题，再考虑到实际使用的Studio的本地文件夹里，把exe文件（MultiAgentStudio.exe 或者 MultiAgentStudio-Lite.exe）删掉，然后双击setup.bat重新生成

### 用户配置名录

| 文件 / 目录 | 说明 |
|---|---|
| `configs/` | 所有 multi-agent 配置（`<agent_id>.json`、`default.json`）与全局设置（`settings.json`）。含 API key、system prompt、数据库连接、阈值等。 |
| `snapshots/` | 全量总结前自动保存的会话快照（若生成过）。 |
| `.chrome-profile/` | 浏览器接管使用的自动化 Chrome profile（用过浏览器接管才有）。保留可复用网站登录态；不保留则需重新登录。 |

## 特性

### 多智能体编排

- **可视化配置** —— 在浏览器里创建 / 编辑 / 删除 multi-agent 配置；每份配置是一个 JSON 文件，并与一个 PostgreSQL checkpoint 库绑定。常用配置可保存为默认模板。
- **Supervisor–Worker 架构** —— 主 agent 负责理解、拆解与汇总，把任务委派给子 agent；每个子 agent 都是带 checkpointer 的子图，在线程内跨调用保持记忆。
- **子 agent 即工具** —— 每个子 agent 以「工具」的形式暴露给主 agent（名字 + 描述）；一次可并行调度多个子 agent，并回收它们的报告。
- **MCP 工具接入** —— 子 agent 可挂载 http / stdio 两种 MCP 工具源，配置页提供连通性检测。

### 浏览器接管

- **让主 agent 直接操作浏览器** —— 内置 `browser_agent`，主 agent 可驱动本机 Chrome/Edge 完成导航、点击、输入、拖动、滚动与多标签页管理；面向**纯文本 LLM**（无需多模态 / 视觉）。
- **省 token 的 DOM 呈现** —— 首帧给完整 DOM 基线，之后每次互动只回传新增 / 变化的内容；以地标 + 缩进树 + 语义标签（`<可点击元素 eN>` / `<可输入元素 eN>` / `<可拖动元素 eN>` / `<可滚动元素 eN>`）呈现，标题保留层级。
- **整页与双向滚动** —— 文档级滚动条暴露为可滚动元素；滚动用 `scroll_delta` 指定方向（正数向下、负数向上，单位为步）。
- **拟人交互** —— 真实鼠标 / 键盘事件轨迹（非直接调用元素接口），React / Vue 受控输入有原生 setter 兜底。
- **提示接管开关** —— 输入区可实时预览当前聚焦标签页的**只读** DOM，发送消息时自动附着，发送后自动关闭。
- **全局急停** —— 桌面级始终置顶的「停止 / 继续」悬浮球（跨应用可见），浏览器关闭后自动隐藏并复位。
- **敏感信息脱敏** —— LLM 填写时用 `<名称>` 占位，工具调用时按全局「敏感信息表单」替换为真实值，真实隐私不进入模型上下文。

### 模型与提供商

- **模型供应商 + 模型名称** —— 分别填写供应商与模型名称（例如 `deepseek` + `deepseek-v4-pro`），程序自动拼接为 `provider:model`。
- **自动识别接入方式** —— 内置供应商（openai / anthropic / deepseek / google_genai）直接使用官方端点，不显示 `base_url`；其他供应商自动按 OpenAI 兼容协议接入，并显示 `base_url`。
- **精细采样参数** —— temperature / top_k / top_p / max_tokens / repetition_penalty 逐项可选。
- **DeepSeek 思考模式修复** —— 检测到 DeepSeek 自动启用 reasoning_content 回填，无需手动勾选，规避思考模式 + 工具调用下的间歇性 400。

### 长期记忆

- **语义记忆** —— BGE-M3 embedding + PostgreSQL（pgvector）向量检索。
- **自动记忆吸附** —— 每条用户消息发送前自动检索相关记忆并附加上下文（条数可配）。
- **记忆读写工具** —— 主 agent 可主动写入 / 检索长期记忆。
- **模型下载进度** —— 首次使用自动下载 embedding 模型，界面与托盘实时显示进度。

### 对话与流式体验

- **逐 token 流式输出** —— 基于 WebSocket 实时渲染主 / 子 agent 输出。
- **思考过程** —— 推理内容独立成块，与正文按真实顺序交错显示。
- **工具调用可视化** —— 工具调用与结果内联展示并自动去重。
- **并行子 agent 分区** —— 多个子 agent 并行时按轮次分区，可下拉切换查看各自输出。
- **细粒度状态栏** —— 实时显示「谁 · 在做什么」（思考中 / 回答中 / 调取工具 / 等待工具 / 加载模型）。
- **Markdown + 公式** —— markdown-it 渲染、代码高亮、KaTeX 公式，另可选「裸公式识别 / 激进公式渲染」。
- **阅读友好** —— 正文与思考独立缩放、发送后自动跟随最新输出、可拖动的固定到底部按钮、回复完成提示音、发送 / 换行键可自定义。
- **图中途确认** —— 需要用户拍板时弹出应用内确认框（子 agent 使用区分样式）。
- **未发送草稿持久化** —— 输入框内容按 agent 落盘，退出会话 / 关闭浏览器 / 退出托盘程序后仍可恢复；跨 agent 流转可配。

### 历史、会话与快照

- **会话持久化** —— 列出 / 继续 / 删除会话；只建了名字、尚未发言的空会话也会保留。
- **历史 Markdown 浏览** —— 主 / 子 agent 历史以 Markdown 只读呈现，并统计消息数与 token 用量。
- **消息目录** —— 下拉目录按用户消息展开，点击标题即可跳转。
- **历史消息编辑** —— 进入编辑模式后可按行修改历史消息（可选任意时间、任意类型），逐条提交并即时生效。
- **编辑失败可读报错** —— 编辑提交按「加载模型 / 写入」分阶段超时，写入失败时以右上角持久化提示条给出可关闭的原因，且失败即停。
- **快照** —— 每次全量清空前自动保存主 / 子 agent 历史快照，可只读回看或删除。
- **隐藏会话** —— 按 agent 独立隐藏 / 显示会话。

### 总结与清空

- **阶段性总结** —— token 达到阈值时自动总结，不打断对话。
- **自动全量清空** —— 达到更高阈值时先快照、再总结、再清空（可保留最近若干轮）。
- **主动全量总结** —— 手动触发主 agent 或指定子 agent 的全量总结。

### 文件工具与导出

- **自研文件工具** —— read（分页 + 行号）/ edit（字符串级中段修改）/ write / search，以及目录、复制、移动、删除；所有操作限定在根目录内。
- **富格式读取** —— PDF / Word / PPT / Excel / EPUB / HTML 等自动转 Markdown；PDF 额外用 pdfminer 抽正文、pdfplumber 按框线抽表格；读取时在同目录生成可编辑的 `.md` 副本。
- **HTML 报告** —— 可选在回复后生成 HTML 报告并自动打开。
- **导出** —— 一键把单条回复导出为可打印 HTML（A4 优化）或 Markdown 文件。

### 设置与安装

- **全局设置** —— 记忆吸附、导出路径、历史折叠、编辑模式、公式识别等集中配置，支持中 / 英双语切换。
- **一键部署** —— `setup.bat` 自动创建虚拟环境、安装依赖并打包 `MultiAgentStudio.exe`；Windows 托盘常驻，浏览器自动打开。
- **pgvector 一键安装** —— 自动识别 PostgreSQL 与编译环境，优先从官方源码编译，失败回退预编译包。
- **连接串输入** —— PostgreSQL 连接支持「分步填写」或直接粘贴完整连接串。
- **卡片外观与隐藏** —— multi-agent 卡片列表的按钮配色 / 样式、角落绶带、卡片背景可配（本机偏好）；支持隐藏配置并用「显示隐藏」开关查看。

## 界面预览

### 主界面 · 设置菜单

![主界面设置菜单](show_case_pics/zh-main-settings-menu.png)

### 选择会话

![选择会话](show_case_pics/zh-session-selection.png)

### 子 agent 流式输出
![子 agent 流式输出](show_case_pics/zh-subagent-streaming_one.png)
![子 agent 流式输出](show_case_pics/zh-subagent-streaming_two.png)

## 架构

```
Supervisor（主 agent）                    Workers（子 agent）
  ├─ 文件工具                              ├─ MCP 工具（http / stdio）
  ├─ 记忆工具（写 / 读）                    └─ checkpointer=True 的子图
  ├─ 子 agent 工具（作为 tool 呈现）
  ├─ 浏览器工具（接管 Chrome/Edge）
  └─ 总结 / 清空历史
```

- 主 agent 是负责调度规划的 Supervisor；每个子 agent 是一个 `checkpointer=True` 编译的**子图**，作为节点挂载，在线程内跨调用保持记忆。
- 主图与各子图共享 `subagents_reports_submit` / `instructions_for_subagents`（故意同名）用于报告 / 指令穿透，而各自的消息通道键必须唯一。


## 许可证

[GPL-3.0](LICENSE)

---

[中文](#multi-agent-studio) | [English](#multi-agent-studio-english)

# Multi-Agent Studio (English)

> A **local multi-agent workbench** built on the [LangGraph General-Use Multi-Agent Framework](https://github.com/ColtM1873/LangGraph-General-Use-Multi-Agent-Framework): Supervisor–Worker orchestration with a modern web GUI, running locally on Windows.

## Table of Contents

- [Quick Start](#quick-start)
- [Version migration (upgrading)](#version-migration-upgrading)
- [Features](#features)
- [Screenshots](#screenshots)
- [Architecture](#architecture)
- [Local MCP servers](#local-mcp-servers)
- [License](#license)

## Quick Start

### 1. Prerequisites

1. **PostgreSQL**: not needed if you use the Lite edition's sqlite sub-variant.

### 2. Download

1. Open this repository page, click **Releases** on the right, and download **Source code (zip)** under the latest release (or `git clone` the repository).

### 3. Extract

1. Extract the zip; you will get a `multi-agent-studio-*` folder.

### 4. Enter the project folder

1. Open the folder and drill down until you see `setup.bat` and `build_exe.bat`.

### 5. One-click install + build

1. Double-click `setup.bat`.
2. A black cmd window opens first, and then possibly a blue elevated PowerShell window. Do not close either window before installation finishes.
3. Installation may ask for elevation. This means Windows long-path support is not enabled, so dependency installation failed; approve the prompt.
4. If Python is not installed, you will be asked whether to install it automatically in one click (this project needs Python to run).
5. The Lite edition comes in a postgres version and a sqlite version. The sqlite version is recommended, since it does not require installing PostgreSQL and its ~1 GB of disk usage.
6. For the non-Lite edition, you will be asked whether to install pgvector. Installing it in one click is recommended.
7. When installation finishes, a Studio shortcut appears on the desktop. Double-click it to start.

### 6. Usage notes

1. (For the postgres version) When installing PostgreSQL, you will be asked for a password and a port. Do not enter arbitrary values. When creating a multi-agent config, you must enter the PostgreSQL username, password, and port (the app needs them to connect to the database).
2. (For the postgres version) In the PostgreSQL connection settings, the connection string template is `postgresql://username:password@localhost:port/?sslmode=disable` (the typical local setup).
3. Each option has a small circled exclamation mark with a detailed explanation; hover over it to read.

## Version migration (upgrading)

1. Download the source code of the latest release (or clone the repository). After extracting, open the folder containing `setup.bat`, select everything, and copy it.
2. Open your actual Studio folder (also the one containing `setup.bat`), paste, and choose to overwrite/replace all matching files.
3. This normally completes the upgrade. You can start the app from the desktop shortcut.
4. If something breaks, delete the exe file (`MultiAgentStudio.exe` or `MultiAgentStudio-Lite.exe`) in your Studio folder, then double-click `setup.bat` to rebuild it.

### User configuration files

| File / directory | Notes |
|---|---|
| `configs/` | All multi-agent configs (`<agent_id>.json`, `default.json`) and global settings (`settings.json`). Contains API keys, system prompts, database connections, thresholds, etc. |
| `snapshots/` | Conversation snapshots saved automatically before a full summary (if any were generated). |
| `.chrome-profile/` | The automation Chrome profile used by browser takeover (present only if you used browser takeover). Keep it to reuse website logins; otherwise you will need to sign in again. |

## Features

### Multi-agent orchestration

- **Visual configuration** — create / edit / delete multi-agent configs in the browser; each config is a JSON file bound to a PostgreSQL checkpoint database. Save a commonly used config as the default template.
- **Supervisor–Worker architecture** — the main agent understands, decomposes, and summarizes tasks, delegating them to sub-agents; each sub-agent is a subgraph with a checkpointer and keeps memory across calls within a thread.
- **Sub-agents as tools** — each sub-agent is exposed to the main agent as a "tool" (name + description); multiple sub-agents can be dispatched in parallel in one turn, and their reports are collected.
- **MCP tool integration** — sub-agents can attach http / stdio MCP tool sources; the config page provides connectivity checks.

### Browser takeover

- **Let the main agent operate the browser directly** — the built-in `browser_agent` lets the main agent drive local Chrome/Edge to navigate, click, type, drag, scroll, and manage multiple tabs; designed for **text-only LLMs** (no multimodality / vision required).
- **Token-efficient DOM** — the first frame sends a full DOM baseline; later interactions send only new / changed content, presented as landmarks + an indented tree + semantic tags (`<可点击元素 eN>` / `<可输入元素 eN>` / `<可拖动元素 eN>` / `<可滚动元素 eN>`), with heading hierarchy preserved.
- **Whole-page and bidirectional scrolling** — the document-level scrollbar is exposed as a scrollable element; scrolling uses `scroll_delta` for direction (positive = down, negative = up, unit = steps).
- **Human-like interaction** — real mouse / keyboard event trajectories (not direct element API calls); controlled React / Vue inputs fall back to the native setter.
- **Takeover hint toggle** — the input area can preview the focused tab's **read-only** DOM in real time; the preview is attached automatically when sending and closed after a successful send.
- **Global emergency stop** — a desktop-level always-on-top "Stop / Continue" floating ball (visible across apps); it auto-hides and resets when the browser closes.
- **Sensitive-info masking** — the LLM uses `<name>` placeholders when filling; on tool calls they are replaced with real values from the global "sensitive info form", so real private data never enters the model context.

### Models & providers

- **Model provider + model name** — enter the provider and model name separately (e.g. `deepseek` + `deepseek-v4-pro`); the app joins them into `provider:model` automatically.
- **Automatic endpoint handling** — built-in providers (openai / anthropic / deepseek / google_genai) use their official endpoints and do not show a `base_url` field; other providers are automatically connected via the OpenAI-compatible protocol and show a `base_url` field.
- **Fine-grained sampling** — opt in per parameter: temperature / top_k / top_p / max_tokens / repetition_penalty.
- **DeepSeek thinking-mode fix** — automatically enabled when a DeepSeek model is detected; it back-fills `reasoning_content` to avoid intermittent 400s in thinking mode with tool calls.

### Long-term memory

- **Semantic memory** — BGE-M3 embeddings + PostgreSQL (pgvector) vector retrieval.
- **Automatic memory attachment** — before each user message, relevant memories are retrieved and attached as context (count configurable).
- **Memory read/write tools** — the main agent can actively write / search long-term memory.
- **Model download progress** — the embedding model is downloaded automatically on first use, with live progress in the UI and tray.

### Chat & streaming experience

- **Token-by-token streaming** — main / sub-agent output is rendered live over WebSocket.
- **Thinking process** — reasoning content is shown in its own block and interleaved with the body in true order.
- **Tool-call visualization** — tool calls and results are shown inline and de-duplicated automatically.
- **Parallel sub-agent partitions** — when multiple sub-agents run in parallel, output is partitioned by round with a dropdown to switch between them.
- **Fine-grained status bar** — shows who is doing what in real time (thinking / answering / fetching tools / waiting for tools / loading model).
- **Markdown + math** — markdown-it rendering, syntax highlighting, and KaTeX math, plus optional bare-formula detection / aggressive math rendering.
- **Reading comfort** — independent body / reasoning zoom, auto-follow after sending, a draggable pin-to-bottom button, a reply-completed chime, and customizable send / newline keys.
- **In-graph confirmation** — when the graph needs a decision, an in-app confirm dialog appears (sub-agent dialogs use a distinct style).
- **Unsent draft persistence** — input content is persisted per agent and survives leaving a thread, closing the browser, or quitting the tray app; cross-agent syncing is configurable.

### History, threads & snapshots

- **Thread persistence** — list / resume / delete conversations; named but unused empty threads are kept as well.
- **Markdown history browsing** — main / sub-agent history is shown as read-only Markdown, with message counts and token usage.
- **Message directory** — a dropdown directory expands by user message; click a heading to jump to the corresponding reply.
- **History message editing** — after entering edit mode, edit historical messages line by line (optionally any time, any type), submit them one by one, and apply changes immediately.
- **Readable edit errors** — edit submission times out by stage (loading model / writing); on failure, a persistent dismissible reason bar appears in the top-right, and submission stops.
- **Snapshots** — before every full flush, main / sub-agent history snapshots are saved automatically; browse them read-only or delete them.
- **Hide threads** — hide / show conversations independently per agent.

### Summary & history flush

- **Staged summarization** — automatic summarization when a token threshold is reached, without interrupting the conversation.
- **Automatic full flush** — at a higher threshold: snapshot, summarize, then clear (keeping a configurable number of recent turns).
- **Proactive full summary** — manually trigger a full summary for the main agent or a specified sub-agent.

### File tools & export

- **Custom file tools** — read (pagination + line numbers) / edit (precise mid-string edits) / write / search, plus directory, copy, move, and delete; all operations are confined to the root directory.
- **Rich-format reading** — PDF / Word / PPT / Excel / EPUB / HTML are automatically converted to Markdown; PDFs additionally use pdfminer for text and pdfplumber for ruled-line tables, and an editable `.md` copy is created in the same directory on read.
- **HTML report** — optionally generate an HTML report after a reply and open it automatically.
- **Export** — one-click export of a single reply to printable HTML (A4-optimized) or a Markdown file.

### Settings & installation

- **Global settings** — memory attachment, export paths, history folding, edit mode, formula detection, and more, with a zh / EN language switch.
- **One-click deployment** — `setup.bat` automatically creates the virtual environment, installs dependencies, and packages `MultiAgentStudio.exe`; the app stays in the Windows tray and opens the browser automatically.
- **One-click pgvector install** — automatically detects PostgreSQL and the build environment, prefers building from official source, and falls back to a prebuilt package on failure.
- **Connection-string input** — fill in PostgreSQL step by step or paste a complete connection string.
- **Card appearance & hiding** — configure the multi-agent card list's button colors / styles, corner ribbon, and card background (local preference); configs can be hidden and revealed via a "show hidden" toggle.

## Screenshots

### Main UI · Settings menu

![Main UI settings menu](show_case_pics/en-main-settings-menu.png)

### Thread selection

![Thread selection](show_case_pics/en-session-selection.png)

### Sub-agent streaming output

![Sub-agent streaming output](show_case_pics/en-subagent-streaming.png)

## Architecture

```
Supervisor (main agent)                 Workers (sub-agents)
  ├─ file tools                          ├─ MCP tools (http / stdio)
  ├─ memory tools (write/read)           └─ subgraph with checkpointer=True
  ├─ sub-agent tools (as tools)
  ├─ browser tools (take over Chrome/Edge)
  └─ summarization / history flush
```

- The main agent is a Supervisor that plans and delegates; each sub-agent is a **subgraph** compiled with `checkpointer=True` and mounted as a node, keeping memory across calls within a thread.
- The main graph and each subgraph share the `subagents_reports_submit` / `instructions_for_subagents` channels (intentionally same-named) for passing reports / instructions, while their message channels must be unique.

## Local MCP servers

`folder_of_MCPs/` contains standalone FastMCP servers (Caiyun weather, AMap). Start them separately and reference them in a sub-agent's `mcp_servers` via `http` or `stdio` transport. Their tokens are read from environment variables — see `.env.example`.

## License

[GPL-3.0](LICENSE)
