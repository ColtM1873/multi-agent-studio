# Multi-Agent Studio

一个基于 **LangGraph 的 Supervisor-Worker 多智能体框架**，配一套现代 Web 图形界面。

本 Studio 基于 [LangGraph General-Use Multi-Agent Framework](https://github.com/ColtM1873/LangGraph-General-Use-Multi-Agent-Framework) 框架构建。

## 特性

- **可视化多智能体配置** —— 在浏览器里创建 / 编辑 / 删除 multi-agent 配置；每份配置是一个 JSON 文件，与一个 PostgreSQL checkpoint 数据库绑定。
- **会话线程** —— 基于 LangGraph Postgres checkpointer 列出 / 删除 / 继续线程；历史以 Markdown 渲染。
- **流式对话** —— 通过 WebSocket 实时渲染主 / 子 agent 输出；子 agent 历史按子 agent 名聚合。
- **长期记忆** —— 用 BGE-M3 embedding + Postgres（pgvector）做语义记忆。
- **可配置的总结 / 清空历史** —— token 阈值控制阶段性总结与历史清空。
- **系统托盘** —— 后台常驻 + Windows 托盘图标。

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

## 快速开始

### 第一步：安装前置软件

1. **安装 Python 3.13**：到 https://www.python.org/downloads/ 下载并安装，安装时**请勾选「Add python.exe to PATH」**。
2. **安装 PostgreSQL**（需启用 `pgvector` 扩展）：程序用它保存会话历史和长期记忆。

### 第二步：下载

1. 打开本仓库页面，点右侧 **Releases**，在最新版本下点击 **Source code (zip)** 下载。

### 第三步：解压

1. 把 zip 解压到**任意位置**，得到一个 `multi-agent-studio-*` 文件夹。

### 第四步：进入项目文件夹

1. 双击点进去，一直点到能看到 `setup.bat`、`build_exe.bat` 的那一层。

### 第五步：一键安装 + 打包

1. 双击 `setup.bat`（或在文件夹空白处**右键** →「**在终端中打开**」→ 输入 `setup.bat` 回车）。
2. 首次会弹出「用户账户控制」，点「**是**」——这是为了启用 Windows 长路径支持，防止安装时报「路径过长」。
3. 脚本会自动：创建虚拟环境 → 安装全部依赖（首次约几分钟）→ 打包出 `MultiAgentStudio.exe`。
4. 看到「安装完成！已生成 MultiAgentStudio.exe」即可。

> 安装脚本会自动启用 Windows 长路径，因此项目解压到任意目录都能正常安装，不必特意放到 C 盘根目录。

### 第六步：启动

1. 双击项目文件夹里的 `MultiAgentStudio.exe`。
2. 浏览器会自动打开界面，同时右下角出现托盘图标（右键图标可「打开 / 查看状态 / 退出」）。

> 从源码运行（开发用）：`python run.py --console`（前台，自动开浏览器 + 控制台日志）。

### 第七步：开始使用

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

## 重新打包

改动代码后想重新生成 exe：

```bash
build_exe.bat               # 双击运行，或：python build_exe.py
```

生成 `dist/MultiAgentStudio.exe` 并复制到项目根目录。启动器相对自身目录定位 `run.py` 与 `venv`，不写死任何绝对路径。

## 版本迁移（升级到新版本）

升级时只需**保留少数「你的数据」，其余代码文件全部覆盖为新版本**即可。PostgreSQL 里的会话历史与长期记忆（checkpoint 库 / store 库）存在数据库里、不在此文件夹中，覆盖文件不会影响它们。

### 必须保留（覆盖前先备份，或复制到新版本目录）

| 文件 / 目录 | 说明 |
|---|---|
| `configs/` | 所有 multi-agent 配置（`<agent_id>.json`、`default.json`）与全局设置（`settings.json`）。含 API key、system prompt、数据库连接、阈值等。 |
| `snapshots/` | 全量总结前自动保存的会话快照（若生成过）。 |
| `.env` | 本地 MCP 服务器读取的 token 等环境变量。 |

### 可以直接覆盖（用新版本替换）

`app/`、`run.py`、`tray.py`、`launcher.py`、`scripts/`、`folder_of_MCPs/`、`md2print/`（源码）、`requirements.txt`、`setup.bat`、`setup.ps1`、`build_exe.py`、`build_exe.bat`、`icon.ico`、`.env.example`、`README.md`、`LICENSE` 等。

### 会自动重新生成（无需手动保留）

`venv/`（`setup.bat` 重建）、`MultiAgentStudio.exe` / `dist/` / `build/`（打包生成）、`logs/`（服务日志）、`__pycache__/` 等缓存。

### 推荐迁移步骤

1. 备份 `configs/`、`snapshots/`、`.env`。
2. 用新版本覆盖其余文件（或把上述三个复制进新解压的文件夹）。
3. 运行 `setup.bat` 重新安装依赖并打包。
4. 双击 `MultiAgentStudio.exe`，确认历史会话、记忆与快照都还在。

## 本地 MCP

`folder_of_MCPs/` 是独立的 FastMCP 服务器（彩云天气、高德地图）。单独启动后，在子 agent 的 `mcp_servers` 里以 `http` 或 `stdio` 方式引用；token 从环境变量读取，见 `.env.example`。

## 目录结构

```
run.py           入口（默认托盘，--console 前台）
tray.py          系统托盘
launcher.py      薄启动器（被 build_exe.py 打成 exe）
setup.bat        一键安装 + 打包
app/
  config/        models · store · edits · settings
  runtime/       state_factory · graph_builder · streaming · persistence · prompts
  services/      threads · chat · history_render
  api/           agents · threads · chat_ws · settings
  static/        index.html · css · js
configs/         multi-agent 配置（不入库）
snapshots/       会话快照（不入库）
scripts/         dev_server · verify_phase1
folder_of_MCPs/  本地 MCP 服务器
```

## 许可证

[GPL-3.0](LICENSE)

---

# Multi-Agent Studio

A **LangGraph-based Supervisor-Worker multi-agent framework** with a modern web GUI.

This studio is built on top of the [LangGraph General-Use Multi-Agent Framework](https://github.com/ColtM1873/LangGraph-General-Use-Multi-Agent-Framework).

## Features

- **Visual multi-agent configuration** — create / edit / delete multi-agent configs in the browser; each config is a JSON file bound to a PostgreSQL checkpoint database.
- **Conversation threads** — list / delete / resume threads backed by LangGraph's Postgres checkpointer; history is rendered as Markdown.
- **Streaming chat** — real-time Markdown rendering of agent / sub-agent output over WebSocket; sub-agent history is aggregated by sub-agent name.
- **Long-term memory** — semantic memory with BGE-M3 embeddings + Postgres (pgvector).
- **Configurable summaries / history flush** — token thresholds for periodic summarization and history flushing.
- **System tray** — run in the background with a Windows tray icon.

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

## Quick Start

### 1. Prerequisites

1. Install **Python 3.13** from https://www.python.org/downloads/ — please check **"Add python.exe to PATH"**.
2. Install **PostgreSQL** with the `pgvector` extension (for checkpoints and long-term memory).

### 2. Download

1. Open the **Releases** page and download the latest **Source code (zip)**.

### 3. Extract

1. Extract the zip anywhere — you'll get a `multi-agent-studio-*` folder.

### 4. Enter the project folder

1. Drill down until you see `setup.bat` and `build_exe.bat`.

### 5. One-click install + build

1. Double-click `setup.bat` (or right-click an empty area → **Open in Terminal** → run `setup.bat`).
2. On the first UAC prompt, click **Yes** — this enables Windows long-path support so torch installs without the "path too long" error.
3. The script creates the venv, installs all dependencies (a few minutes the first time), and builds `MultiAgentStudio.exe`.
4. Wait for "安装完成！已生成 MultiAgentStudio.exe".

> The installer enables Windows long paths automatically, so the project can be extracted anywhere.

### 6. Launch

1. Double-click `MultiAgentStudio.exe`.
2. The browser opens automatically, and a tray icon appears (right-click for Open / Status / Quit).

> Running from source (development): `python run.py --console` (foreground — auto-opens browser + console logs).

### 7. Use

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

## Rebuild

```bash
build_exe.bat               # double-click, or run: python build_exe.py
```

Produces `dist/MultiAgentStudio.exe` and copies it to the project root. The launcher locates `run.py` and `venv` relative to its own directory — no absolute paths are hard-coded.

## Version migration (upgrading)

When upgrading, **keep only a handful of "your data" items and overwrite everything else with the new version**. Conversation history and long-term memory (checkpoint / store databases) live in PostgreSQL, not in this folder, so overwriting files does not affect them.

### Must keep (back up first, or copy into the new version folder)

| File / directory | Notes |
|---|---|
| `configs/` | All multi-agent configs (`<agent_id>.json`, `default.json`) and global settings (`settings.json`). Contains API keys, system prompts, DB connections, thresholds, etc. |
| `snapshots/` | Conversation snapshots saved automatically before a full summary (if any were generated). |
| `.env` | Environment variables (e.g. tokens) read by the local MCP servers. |

### Safe to overwrite (replace with the new version)

`app/`, `run.py`, `tray.py`, `launcher.py`, `scripts/`, `folder_of_MCPs/`, `md2print/` (source), `requirements.txt`, `setup.bat`, `setup.ps1`, `build_exe.py`, `build_exe.bat`, `icon.ico`, `.env.example`, `README.md`, `LICENSE`, etc.

### Regenerated automatically (no need to keep)

`venv/` (rebuilt by `setup.bat`), `MultiAgentStudio.exe` / `dist/` / `build/` (built during packaging), `logs/` (server logs), `__pycache__/` and other caches.

### Recommended migration steps

1. Back up `configs/`, `snapshots/`, and `.env`.
2. Overwrite the remaining files with the new version (or copy the three items above into the freshly extracted folder).
3. Run `setup.bat` to reinstall dependencies and rebuild.
4. Double-click `MultiAgentStudio.exe` and confirm history, memory, and snapshots are all intact.

## Local MCP servers

`folder_of_MCPs/` contains standalone FastMCP servers (Caiyun weather, AMap). Run them separately and reference them in a sub-agent's `mcp_servers` via `http` or `stdio` transport. Their tokens are read from environment variables — see `.env.example`.

## Project structure

```
run.py           entry point (tray mode by default; --console for foreground)
tray.py          system tray
launcher.py      thin launcher (packaged into an exe by build_exe.py)
setup.bat        one-click install + build
app/
  config/        models · store · edits · settings
  runtime/       state_factory · graph_builder · streaming · persistence · prompts
  services/      threads · chat · history_render
  api/           agents · threads · chat_ws · settings
  static/        index.html · css · js
configs/         multi-agent configs (not committed)
snapshots/       conversation snapshots (not committed)
scripts/         dev_server · verify_phase1
folder_of_MCPs/  local MCP servers
```

## License

[GPL-3.0](LICENSE)
