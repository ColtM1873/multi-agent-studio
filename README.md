# Multi-Agent Studio

A **LangGraph-based Supervisor-Worker multi-agent framework** with a modern web GUI.

This studio is built on top of the [LangGraph General-Use Multi-Agent Framework](https://github.com/ColtM1873/LangGraph-General-Use-Multi-Agent-Framework).

## Features

- **Visual multi-agent configuration** — create / edit / delete multi-agent configs in the browser; each config is a JSON file bound to a PostgreSQL checkpoint database.
- **Conversation threads** — list / delete / resume threads backed by LangGraph's Postgres checkpointer; history is rendered as Markdown.
- **Streaming chat** — real-time Markdown rendering of agent / sub-agent output over WebSocket; sub-agent history is aggregated by sub-agent name.
- **Long-term memory** — semantic memory with BGE-M3 embeddings + Postgres (pgvector).
- **Configurable summaries / history flush** — token thresholds for periodic summarization and history flushing.
- **System tray** — run in the background with a Windows tray icon (optional packaged `.exe`).

## Architecture

```
Supervisor (main agent)                 Workers (sub-agents)
  ├─ file tools                          ├─ MCP tools (http / stdio)
  ├─ memory tools (write/read)           └─ subgraph with checkpointer=True
  ├─ sub-agent tools (as tools)
  └─ summarization / history flush
```

- The main agent is a Supervisor that plans and delegates; each sub-agent is a **subgraph** compiled with `checkpointer=True` and mounted as a node, keeping memory across calls within a thread.
- The main graph and each subgraph share `subagents_reports_submit` / `instructions_for_subagents` channels (intentionally same-named) for passing reports / instructions, while their message channels must be unique.

## Requirements

- Python 3.13+
- PostgreSQL with the `pgvector` extension (for the checkpoint saver and semantic memory store)

## Install

```bash
python -m venv venv
venv\Scripts\activate            # Windows
pip install -r requirements.txt
```

On first run the BGE-M3 embedding model is downloaded from Hugging Face (or point `embedding.cache_folder` at a local cache).

## Quick Start

The recommended way to run Multi-Agent Studio:

1. Double-click `build_exe.bat` to build the packaged app (produces `MultiAgentStudio.exe` in the project root).
2. Double-click `MultiAgentStudio.exe`.
3. Click the tray icon in the bottom-right system tray and choose **Open** to launch the web UI.

> Running from source (development): `python run.py` (tray mode) or `python run.py --console` (foreground — auto-opens browser + console logs).

Then in the browser:

1. Click **＋ New multi-agent** and fill in the form (API key, system prompts, sub-agents, MCP servers, PostgreSQL databases).
2. Create the checkpoint database in pgAdmin first (the form reminds you and pre-checks connectivity).
3. Open an agent → pick/create a thread → chat with streaming Markdown.

> Each multi-agent's identity is bound to `checkpoint_database`. After creation, sub-agents cannot be added / removed / renamed, but their prompts / description / MCP tools / models can still be edited.

## Configuration

Configs live in `configs/<agent_id>.json` (not committed). Use the GUI to create them; key fields:

- `postgres`: `prefix` / `suffix` / `store_database` / `checkpoint_database` / `store_namespace`
- `main_agent`: `system_prompt`, `api_key`, `llm_provider_name`, `file_tools.root_dir`, `embedding.*`, `summary.*`, `html_report`, `html_report_prompt`
- `sub_agents[]`: `name`, `description`, `system_prompt`, `api_key`, `llm_provider_name`, `mcp_servers[]`, `summary.*`
- Global settings (`configs/settings.json`): `memory_attach`, `num_memories_attached`, `notification_sound`, `warn_unsaved_changes`

## Packaging

```bash
build_exe.bat               # double-click, or run: python build_exe.py
```

Produces `dist/MultiAgentStudio.exe` and copies it to the project root. The launcher locates `run.py` and `venv` relative to its own directory — no absolute paths are hard-coded.

> If `build_exe.bat` fails with `The system cannot find the path specified`, the `venv` hasn't been created yet — run the [Install](#install) steps first.

## Local MCP servers

`folder_of_MCPs/` contains standalone FastMCP servers (Caiyun weather, AMap). Run them separately and reference them in a sub-agent's `mcp_servers` via `http` or `stdio` transport. Their tokens are read from environment variables — see `.env.example`.

## Project structure

```
run.py           entry point (tray mode by default; --console for foreground)
tray.py          system tray
launcher.py      thin launcher (packaged into an exe by build_exe.py)
app/
  config/        models · store · edits · settings
  runtime/       state_factory · graph_builder · streaming · persistence · prompts
  services/      threads · chat · history_render
  api/           agents · threads · chat_ws · settings
  static/        index.html · css · js
configs/         multi-agent configs (not committed)
scripts/         dev_server · verify_phase1
folder_of_MCPs/  local MCP servers
```

## License

[GPL-3.0](LICENSE)

---

# Multi-Agent Studio

一个基于 **LangGraph 的 Supervisor-Worker 多智能体框架**，配一套现代 Web 图形界面。

本 Studio 基于 [LangGraph General-Use Multi-Agent Framework](https://github.com/ColtM1873/LangGraph-General-Use-Multi-Agent-Framework) 框架构建。

## 特性

- **可视化多智能体配置** —— 在浏览器里创建 / 编辑 / 删除 multi-agent 配置；每份配置是一个 JSON 文件，与一个 PostgreSQL checkpoint 数据库绑定。
- **会话线程** —— 基于 LangGraph Postgres checkpointer 列出 / 删除 / 继续线程；历史以 Markdown 渲染。
- **流式对话** —— 通过 WebSocket 实时渲染主 / 子 agent 输出；子 agent 历史按子 agent 名聚合。
- **长期记忆** —— 用 BGE-M3 embedding + Postgres（pgvector）做语义记忆。
- **可配置的总结 / 清空历史** —— token 阈值控制阶段性总结与历史清空。
- **系统托盘** —— 后台常驻 + Windows 托盘图标（可选打包成 exe）。

## 架构

```
Supervisor（主 agent）                    Workers（子 agent）
  ├─ 文件工具                              ├─ MCP 工具（http / stdio）
  ├─ 记忆工具（写 / 读）                    └─ checkpointer=True 的子图
  ├─ 子 agent 工具（作为 tool 呈现）
  └─ 总结 / 清空历史
```

- 主 agent 是负责调度规划的 Supervisor；每个子 agent 是一个 `checkpointer=True` 编译的**子图**，作为节点挂载，在线程内跨调用保持记忆。
- 主图与各子图共享 `subagents_reports_submit` / `instructions_for_subagents`（故意同名）用于报告 / 指令穿透，而各自的消息通道键必须唯一。

## 依赖

- Python 3.13+
- 安装了 `pgvector` 扩展的 PostgreSQL（用于 checkpoint 与语义记忆）

## 安装

```bash
python -m venv venv
venv\Scripts\activate            # Windows
pip install -r requirements.txt
```

首次运行会从 Hugging Face 下载 BGE-M3 模型（或把 `embedding.cache_folder` 指向本地缓存）。

## 快速开始

推荐方式：

1. 双击 `build_exe.bat` 打包（生成项目根目录下的 `MultiAgentStudio.exe`）。
2. 双击 `MultiAgentStudio.exe`。
3. 单击右下角系统托盘图标，选择「打开」进入网页界面。

> 从源码运行（开发用）：`python run.py`（托盘模式）或 `python run.py --console`（前台模式，自动开浏览器 + 控制台日志）。

浏览器里：

1. 点「＋ 新建 multi-agent」填表单（API key、system prompt、子 agent、MCP、PostgreSQL 库）。
2. 先在 pgAdmin 建好 checkpoint 库（表单会提醒并做连通预检）。
3. 打开某个 agent → 选 / 建线程 → 开始流式对话。

> 每个 multi-agent 的身份绑定 `checkpoint_database`。创建后子 agent 不可增删 / 改名，但其 prompt / description / MCP 工具 / 模型仍可改。

## 配置

配置文件在 `configs/<agent_id>.json`（不入库）。用 GUI 创建，关键字段：

- `postgres`：`prefix` / `suffix` / `store_database` / `checkpoint_database` / `store_namespace`
- `main_agent`：`system_prompt`、`api_key`、`llm_provider_name`、`file_tools.root_dir`、`embedding.*`、`summary.*`、`html_report`、`html_report_prompt`
- `sub_agents[]`：`name`、`description`、`system_prompt`、`api_key`、`llm_provider_name`、`mcp_servers[]`、`summary.*`
- 全局设置（`configs/settings.json`）：记忆吸附、吸附条数、完成提示音、未保存提醒等。

## 打包

```bash
build_exe.bat               # 双击运行，或：python build_exe.py
```

生成 `dist/MultiAgentStudio.exe` 并复制到项目根目录。启动器相对自身目录定位 `run.py` 与 `venv`，不写死任何绝对路径。

> 若双击 `build_exe.bat` 报「系统找不到指定的路径」，说明尚未创建 `venv`，请先执行上面的[安装](#安装)步骤。

## 本地 MCP

`folder_of_MCPs/` 是独立的 FastMCP 服务器（彩云天气、高德地图）。单独启动后，在子 agent 的 `mcp_servers` 里以 `http` 或 `stdio` 方式引用；token 从环境变量读取，见 `.env.example`。

## 目录结构

```
run.py           入口（默认托盘，--console 前台）
tray.py          系统托盘
launcher.py      薄启动器（被 build_exe.py 打成 exe）
app/
  config/        models · store · edits · settings
  runtime/       state_factory · graph_builder · streaming · persistence · prompts
  services/      threads · chat · history_render
  api/           agents · threads · chat_ws · settings
  static/        index.html · css · js
configs/         multi-agent 配置（不入库）
scripts/         dev_server · verify_phase1
folder_of_MCPs/  本地 MCP 服务器
```

## 许可证

[GPL-3.0](LICENSE)
