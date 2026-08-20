"use strict";

/* ================= 常量 ================= */
const DEFAULT_HTML_PROMPT = "不错，请把你的回答输出为一份html文件，使用你所拥有的FileTools，自拟文件名。";
const EMB_OPTIONS = ["BAAI/bge-m3", "BAAI/bge-large-zh-v1.5", "BAAI/bge-small-zh-v1.5"];

const HF_OPTIONS = [
  { v: "", label: "官方 huggingface.co" },
  { v: "https://hf-mirror.com", label: "hf-mirror（国内镜像）" },
];
const SSL_OPTIONS = [
  { v: "", label: "无字符串（视数据库设置启用/关闭 SSL）" },
  { v: "?sslmode=prefer", label: "prefer（有 SSL 就启用，无则明文）" },
  { v: "?sslmode=require", label: "require（必须 SSL，不校验证书）" },
  { v: "?sslmode=verify-ca", label: "verify-ca（校验 CA 证书）" },
  { v: "?sslmode=verify-full", label: "verify-full（校验 CA + 主机名）" },
];

// 已内置集成（requirements 已安装对应 langchain 包）的 provider 前缀
const OFFICIAL_PROVIDERS = ["openai", "anthropic", "deepseek", "google_genai"];
// 可选采样参数：int 标记是否取整（top_k / max_tokens 为整数）；desc/range 为提示文案
const EXTRA_PARAMS = [
  {
    key: "temperature", int: false, min: "0", step: "0.1",
    desc: "采样温度：值越高输出越随机、越发散，越低越确定、越保守。",
    range: "小数，常见范围 0.0 ~ 2.0",
  },
  {
    key: "top_k", int: true, min: "1", step: "1",
    desc: "Top-K 采样：仅从概率最高的 K 个 token 中抽样。",
    range: "正整数，常见范围 1 ~ 100",
  },
  {
    key: "top_p", int: false, min: "0", step: "0.01",
    desc: "Top-P（核）采样：取累计概率达到 p 的最小 token 集合。",
    range: "小数，范围 0.0 ~ 1.0",
  },
  {
    key: "max_tokens", int: true, min: "1", step: "1",
    desc: "单次回复的最大输出 token 数。",
    range: "正整数",
  },
  {
    key: "repetition_penalty", int: false, min: "0", step: "0.1",
    desc: "重复惩罚：大于 1 时抑制已出现内容的重复。",
    range: "小数，常见范围 0.0 ~ 2.0（1.0 为不惩罚）",
  },
];

/* ================= i18n ================= */
let lang = localStorage.getItem("lang") || "zh";
const I18N_EN = {
  "<数据库名>": "<database name>",
  "Agent 回答中…": "Agent is answering…",
  "Agent 思考中…": "Agent is thinking…",
  "Embedding 模型离线模式": "Embedding offline mode",
  "HTML 报告生成 prompt": "HTML report generation prompt",
  "MCP 服务器": "MCP server",
  "Multi-Agent 配置": "Multi-Agent configurations",
  "SSL 模式": "SSL mode",
  "WebSocket 连接失败": "WebSocket connection failed",
  "agent 回复完成时播放的提示音，切换即试听。": "Chime played when the agent finishes replying; switch to preview.",
  "ber（下滑音）": "ber (descending)",
  "checkpoint 库": "checkpoint database",
  "checkpoint 数据库（会话历史绑定）": "checkpoint database (bound to conversation history)",
  "chime（双音上行）": "chime (rising two-tone)",
  "ding（清脆单音）": "ding (crisp single tone)",
  "embedding 模型": "embedding model",
  "embedding 维度": "embedding dimension",
  "embedding 缓存目录": "embedding cache directory",
  "embedding 镜像": "embedding mirror",
  "hf-mirror（国内镜像）": "hf-mirror (China mirror)",
  "prefer（有 SSL 就启用，无则明文）": "prefer (SSL if available, plaintext otherwise)",
  "require（必须 SSL，不校验证书）": "require (SSL required, no certificate verification)",
  "store 数据库": "store database",
  "store_namespace（逗号分隔）": "store_namespace (comma-separated)",
  "verify-ca（校验 CA 证书）": "verify-ca (verify CA certificate)",
  "verify-full（校验 CA + 主机名）": "verify-full (verify CA + hostname)",
  "✅ 回复已完成": "✅ Reply completed",
  "一栏填库名（与上面填的 checkpoint 库名保持一致）。": "Enter the database name here (keep it consistent with the checkpoint database above).",
  "万": "×10,000",
  "下载 embedding 模型时使用的 HuggingFace 镜像源；国内推荐 hf-mirror。": "HuggingFace mirror used when downloading the embedding model; hf-mirror is recommended in China.",
  "不会删除数据库里的会话历史": "Conversation history in the database will not be deleted",
  "个": "",
  "主 agent": "main agent",
  "主 agent 文件工具读写文件的根目录。": "Root directory for the main agent's file tool.",
  "主 agent 的系统提示词，定义其角色与行为。": "System prompt for the main agent, defining its role and behavior.",
  "主 agent 输出完后，询问是否将结果生成 HTML 报告。": "After the main agent finishes, ask whether to generate an HTML report from the result.",
  "主会话": "Main conversation",
  "主机:port": "host:port",
  "主模型": "Main model",
  "云端/企业库请选择 SSL 模式": "For cloud/enterprise databases, choose an SSL mode",
  "仅鼠标（Enter 只换行）": "Mouse only (Enter adds a newline)",
  "会话": "Conversations",
  "会话历史归属的库名，需先在 pgAdmin 建库，创建后不可改。": "Database that holds the conversation history; create it in pgAdmin first. Cannot be changed after creation.",
  "例如 C:\\Agent_WorkPlace": "e.g. C:\\Agent_WorkPlace",
  "例如 my-agent": "e.g. my-agent",
  "例如 my_agent_checkpoints": "e.g. my_agent_checkpoints",
  "例如 postgresql://user:passwd@localhost:5432/": "e.g. postgresql://user:passwd@localhost:5432/",
  "例如 sk-...": "e.g. sk-...",
  "例如 web_search_agent": "e.g. web_search_agent",
  "例如 研究助理": "e.g. Research Assistant",
  "例如：你是一名研究助理，负责理解用户问题，将复杂任务拆解并委派给合适的子 agent，汇总后给出结构化、准确的回答。请使用中文。": "e.g. You are a research assistant who understands user questions, breaks down complex tasks and delegates them to suitable sub-agents, then summarizes into structured, accurate answers. Please respond in Chinese.",
  "例如：你是一名联网搜索子 agent，负责为主 agent 检索网页信息，最终输出检索到的原文或相关片段。": "e.g. You are a web search sub-agent that retrieves web information for the main agent and outputs the original text or relevant excerpts.",
  "例如：负责联网搜索，将需要查询的内容与注意事项告知它，它会返回检索到的原文片段。": "e.g. Handles web search; tell it what to search and any caveats, and it returns the retrieved excerpts.",
  "保存": "Save",
  "保留轮数": "Keep turns",
  "修改未保存时提醒": "Warn on unsaved changes",
  "停止": "Stop",
  "共": "Total",
  "创建后不可增删改名": "Sub-agents cannot be added/removed/renamed after creation",
  "创建后不可改": "Cannot be changed after creation",
  "删除": "Delete",
  "加载中…": "Loading…",
  "加载历史中…": "Loading history…",
  "千": "×1,000",
  "协议": "Protocol",
  "参数，逗号分隔（stdio）": "Arguments, comma-separated (stdio)",
  "发送": "Send",
  "发送失败，消息已保留在输入框": "Send failed; message kept in the input box",
  "发送键": "Send key",
  "发送，": "to send, ",
  "取消": "Cancel",
  "可增删": "Can add/remove",
  "右键": "Right-click",
  "名称": "Name",
  "向量维度；bge-m3 为 1024，换模型需对应调整。": "Vector dimension; 1024 for bge-m3, adjust if you change models.",
  "否": "No",
  "启用 HTML 报告": "Enable HTML report",
  "吸附记忆条数": "Memories to attach",
  "命令（stdio）": "Command (stdio)",
  "唯一标识，用作配置文件名 configs/&lt;id&gt;.json，创建后不可改。": "Unique ID, used as the config filename configs/&lt;id&gt;.json. Cannot be changed after creation.",
  "回到本页，点「保存」。": "Come back here and click 'Save'.",
  "回复完成提示音": "Reply completion chime",
  "在": "In the",
  "基本信息": "Basic info",
  "子 agent": "sub-agent",
  "子 agent 标识，会作为工具名呈现给主 agent，创建后不可改。": "Sub-agent ID, shown to the main agent as a tool name. Cannot be changed after creation.",
  "完整连接串示例": "Full connection string example",
  "官方 huggingface.co": "Official huggingface.co",
  "工具结果": "Tool result",
  "工具调用": "Tool call",
  "左侧展开": "Expand on the left",
  "已保存": "Saved",
  "已关闭 Embedding 模型离线模式：每次打开会联网校验。若已获取模型缓存，建议重新开启以跳过校验。": "Embedding offline mode disabled: it verifies online every time it opens. If you already have the model cached, re-enable it to skip verification.",
  "已创建": "Created",
  "已删除": "Deleted",
  "已设为默认配置": "Set as default configuration",
  "库名": "Database name",
  "开启后，主 agent 收到用户消息时会先从记忆库语义检索 N 条相关记忆，附在用户消息里一起传入。此设置影响图编译，进入某个 multi-agent 后不可改动，需退回主界面。": "When enabled, the main agent retrieves N relevant memories from the memory store and attaches them to the user message. This affects graph compilation and cannot be changed after entering a multi-agent; go back to the main screen.",
  "恢复默认": "Restore default",
  "打开": "Open",
  "换行键": "Newline key",
  "换行）": "to break)",
  "描述该子 agent 能力，作为工具描述呈现给主 agent。": "Describes the sub-agent's capability, shown to the main agent as a tool description.",
  "数据库名": "Database name",
  "数据库地址": "Database address",
  "文件工具根目录": "File tool root directory",
  "新会话名称：": "New conversation name:",
  "新建 / 编辑 multi-agent 时，空输入框显示灰色示例文字，帮助快速上手。": "When creating/editing a multi-agent, empty inputs show gray example text to help you get started.",
  "新建 multi-agent": "New multi-agent",
  "新建会话": "New conversation",
  "无": "None",
  "无字符串（视数据库设置启用/关闭 SSL）": "No string (SSL enabled/disabled by database settings)",
  "无需提前下载，首次配置会自动下载（需连接 Hugging Face Hub，国内网络可能连不上）。若已离线缓存过，可在下方缓存目录直接使用。": "No need to download in advance; it downloads automatically on first setup (requires connecting to Hugging Face Hub, which may be blocked in China). If already cached, use the cache directory below.",
  "是": "Yes",
  "显示名称，创建后不可改（与历史会话绑定）。": "Display name, cannot be changed after creation (bound to conversation history).",
  "显示输入示例（灰色占位字）": "Show input examples (gray placeholders)",
  "暂无会话": "No conversations yet",
  "所有会话均已隐藏": "All conversations are hidden",
  "隐藏": "Hide",
  "取消隐藏": "Unhide",
  "已隐藏": "Hidden",
  "显示隐藏对话": "Show hidden conversations",
  "暂无用户消息": "No user messages yet",
  "有未保存的修改，确定离开？": "You have unsaved changes. Leave anyway?",
  "本地 postgres（localhost/127.0.0.1）自动用 sslmode=disable，省一次 SSL 握手。<br/><br/>云端或企业级 postgres 请选择对应 SSL 模式；「无字符串」表示交由数据库设置决定。": "Local postgres (localhost/127.0.0.1) automatically uses sslmode=disable to skip an SSL handshake.<br/><br/>For cloud or enterprise postgres, choose the corresponding SSL mode; 'No string' leaves it to the database settings.",
  "本地模型缓存路径，留空用 Hugging Face 默认缓存。": "Local model cache path; leave empty to use Hugging Face's default cache.",
  "本地连接，自动使用 sslmode=disable": "Local connection, auto sslmode=disable",
  "格式：postgresql://用户名:密码@主机:端口/<br/>例如 postgresql://user:passwd@localhost:5432/<br/><br/>下面会实时显示完整连接串。程序会根据主机自动判断是否本地回环。": "Format: postgresql://user:passwd@host:port/<br/>e.g. postgresql://user:passwd@localhost:5432/<br/><br/>The full connection string is shown below in real time. The program auto-detects loopback hosts.",
  "模型 provider": "Model provider",
  "模型来源": "Model source",
  "思考过程": "Thinking process",
  "思考字号（相对正文）": "Reasoning font size (relative to body)",
  "思考字号减小": "Decrease reasoning font size",
  "思考字号增大": "Increase reasoning font size",
  "正文字号": "Body font size",
  "正文字号减小": "Decrease body font size",
  "正文字号增大": "Increase body font size",
  "进阶设置": "Advanced settings",
  "历史浏览时各板块默认展开 / 折叠": "Default expand/collapse of sections when browsing history",
  "历史浏览时，思考过程板块默认展开还是折叠。": "Whether the reasoning section is expanded or collapsed by default when browsing history.",
  "历史浏览时，工具调用板块默认展开还是折叠。": "Whether the tool call section is expanded or collapsed by default when browsing history.",
  "历史浏览时，工具结果板块默认展开还是折叠。": "Whether the tool result section is expanded or collapsed by default when browsing history.",
  "开启后默认展开": "Expanded by default when enabled",
  "字体颜色设置": "Font color settings",
  "正文": "Body text",
  "调整各板块文字颜色，仅本机生效。": "Adjust the text color of each section; applies on this machine only.",
  "内置模型": "Built-in models",
  "任意第三方模型（OpenAI 协议）": "Any third-party model (OpenAI protocol)",
  "模型名": "Model name",
  "格式「供应商:模型名」，如 deepseek:deepseek-v4-pro。已内置集成：openai / anthropic / deepseek / google_genai。": "Format 'provider:model', e.g. deepseek:deepseek-v4-pro. Built-in providers: openai / anthropic / deepseek / google_genai.",
  "填纯模型名，不要带供应商前缀（如 deepseek:）。第三方模型的 llm-provider 固定为 openai，不能是其他。": "Bare model name only, without a provider prefix (e.g. no \"deepseek:\"). Third-party models always use \"openai\" as the llm-provider.",
  "应指向 /v1 根路径，LangChain 会自动拼接 /chat/completions。不要把 /chat/completions 写进 base_url，否则会 404。": "Point to the /v1 root path; LangChain appends /chat/completions automatically. Do not include /chat/completions, or you'll get a 404.",
  "额外选项": "Extra options",
  "请确认你的模型 API 提供商支持 {param} 参数": "Please confirm your model's API provider supports the {param} parameter",
  "该 provider 未内置集成，建议切换为「任意第三方模型（OpenAI 协议）」": "This provider is not built-in; switch to 'Any third-party model (OpenAI protocol)' instead.",
  "采样温度：值越高输出越随机、越发散，越低越确定、越保守。": "Sampling temperature: higher = more random/creative, lower = more deterministic/conservative.",
  "Top-K 采样：仅从概率最高的 K 个 token 中抽样。": "Top-K sampling: sample only from the K highest-probability tokens.",
  "Top-P（核）采样：取累计概率达到 p 的最小 token 集合。": "Top-P (nucleus) sampling: take the smallest token set whose cumulative probability reaches p.",
  "单次回复的最大输出 token 数。": "Maximum output tokens per reply.",
  "重复惩罚：大于 1 时抑制已出现内容的重复。": "Repetition penalty: values > 1 discourage repeating already-generated content.",
  "小数，常见范围 0.0 ~ 2.0": "Decimal, typical range 0.0 ~ 2.0",
  "正整数，常见范围 1 ~ 100": "Positive integer, typical range 1 ~ 100",
  "小数，范围 0.0 ~ 1.0": "Decimal, range 0.0 ~ 1.0",
  "正整数": "Positive integer",
  "小数，常见范围 0.0 ~ 2.0（1.0 为不惩罚）": "Decimal, typical range 0.0 ~ 2.0 (1.0 = no penalty)",
  "正在下载 embedding 模型": "Downloading embedding model",
  "正在准备 / 校验缓存…": "Preparing / verifying cache…",
  "此操作不可撤销。": "This action cannot be undone.",
  "没有缓存时请勿开启；已有缓存时建议开启，跳过每次联网校验。": "Do not enable without a cache; if cached, enable it to skip online verification every time.",
  "消息目录": "Message directory",
  "添加 MCP": "Add MCP",
  "添加子 agent": "Add sub-agent",
  "清空历史时保留最近几轮对话。": "Keep the most recent turns when clearing history.",
  "清空历史阈值": "Clear-history threshold",
  "清空时保留轮数": "Turns to keep when clearing",
  "点": "Click",
  "点击检测健康状态": "Click to check health",
  "用户": "User",
  "用户名:passwd": "user:passwd",
  "留空则使用默认缓存目录": "Leave empty to use the default cache directory",
  "登录凭据": "Credentials",
  "确定删除会话": "Delete conversation?",
  "确定删除配置": "Delete configuration?",
  "确认": "Confirm",
  "确认生成后注入给主 agent 的提示词（可恢复默认）。": "Prompt injected into the main agent after confirmation (can restore default).",
  "离线模式 = 只从本地缓存加载 embedding 模型、不联网校验。没有缓存时请勿开启（会报错）；已有缓存时建议开启，跳过每次联网校验。": "Offline mode = load the embedding model only from the local cache without online verification. Do not enable without a cache (it will error); if cached, enable it to skip verification each time.",
  "移除": "Remove",
  "系统设置": "Settings",
  "URL（http）": "URL (http)",
  "组成：": "Consists of:",
  "编辑": "Edit",
  "编辑 multi-agent 配置时，若做了改动但未保存就离开，弹窗确认。": "When editing a multi-agent config, prompt on leaving with unsaved changes.",
  "编辑默认配置": "Edit default configuration",
  "聊天输入框里，按该键发送消息。": "Press this key in the chat input to send.",
  "自定义…": "Custom…",
  "自定义模型名": "Custom model name",
  "自定义镜像地址": "Custom mirror URL",
  "表单已按默认配置预填，请修改差异项。创建后子 agent 不可增删改名。": "The form is pre-filled from the default configuration; modify the differences. Sub-agents cannot be added/removed/renamed after creation.",
  "记忆吸附": "Memory attachment",
  "裸公式识别": "Auto-detect bare formulas",
  "模型偶尔不带 $ 或 \\( 分隔符直接输出公式（如 s_{t+1}=f(s_t,a_t)）。开启后自动识别并渲染，适合科研 / 数理场景；日常场景建议关闭，以免误判普通文本。": "Models sometimes output formulas without $ or \\( delimiters (e.g. s_{t+1}=f(s_t,a_t)). When enabled, they are detected and rendered automatically—useful for research/math scenarios. Keep it off in everyday use to avoid misinterpreting plain text.",
  "记忆存储命名空间，逗号分隔多个层级。": "Memory store namespace, comma-separated for multiple levels.",
  "记忆库（store 数据库）还需启用 pgvector：选中刚建的库 → 点上方「Query Tool」图标 → 粘贴下面这句 → 点执行（或按 F5）：": "The memory store (store database) also needs pgvector: select the database you just created → click the 'Query Tool' icon → paste the line below → click Execute (or press F5):",
  "设为默认": "Set as default",
  "设置已保存": "Settings saved",
  "该子 agent 可用的工具来源；程序不负责启动，需外部自行启动。": "Tool sources available to this sub-agent; the program does not start them—start them externally.",
  "该子 agent 所用模型的 API 密钥（明文存本地配置）。": "API key for this sub-agent's model (stored in plain text locally).",
  "该子 agent 的系统提示词。": "System prompt for this sub-agent.",
  "该子 agent 累计 token 达到该值时清空历史（只保留最近几轮）。": "Clear this sub-agent's history when accumulated tokens reach this value (keep recent turns).",
  "该模型供应商的 API 密钥（明文存本地配置）。": "API key for this model provider (stored in plain text locally).",
  "请填写 checkpoint 数据库名": "Please fill in the checkpoint database name",
  "请填写名称": "Please fill in the name",
  "子 agent 名称不能为空": "Sub-agent name cannot be empty",
  "请确认": "Please confirm",
  "跟随最新输出 · 按住可拖动": "Follow latest output · hold to drag",
  "输入消息…": "Type a message…",
  "输入消息（": "Type a message (",
  "输入消息（Enter 发送，Shift+Enter 换行）": "Type a message (Enter to send, Shift+Enter for newline)",
  "输入消息（点击发送，Enter 换行）": "Type a message (click to send, Enter for newline)",
  "返回会话": "Back to conversation",
  "还没有任何 multi-agent 配置": "No multi-agent configurations yet",
  "连接前缀": "Connection prefix",
  "连接后缀": "Connection suffix",
  "长期记忆存储的库名，可与 checkpoint 库相同或不同。": "Database for long-term memory; can be the same as or different from the checkpoint database.",
  "阶段性总结阈值": "Stage summary threshold",
  "除发送键外，其余 Enter 组合均换行。": "All Enter combos except the send key insert a newline.",
  "需先在 pgAdmin 建库": "Create the database in pgAdmin first",
  "默认配置已保存": "Default configuration saved",
  "（在开始菜单里搜索「pgAdmin」）。": "(search for 'pgAdmin' in the Start menu).",
  "（无历史）": "(no history)",
  "（空消息）": "(empty message)",
  "，双击连接，输入安装 PostgreSQL 时设置的密码。": ", double-click to connect and enter the password set when installing PostgreSQL.",
  "📖 不会建数据库？点这里看步骤": "📖 Don't know how to create a database? Click for steps",
  "、": ", ",
  "。": ".",
};
const t = (s) => (lang === "zh" || !I18N_EN[s]) ? s : I18N_EN[s];
function setLang(l) {
  lang = l;
  localStorage.setItem("lang", l);
  render();
}

/* ================= 工具 ================= */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) {}
    throw new Error(detail);
  }
  return res.json();
}

function toast(msg, isError = false) {
  let wrap = $(".toast-wrap");
  if (!wrap) { wrap = document.createElement("div"); wrap.className = "toast-wrap"; document.body.appendChild(wrap); }
  const el = document.createElement("div");
  el.className = "toast" + (isError ? " error" : "");
  el.textContent = msg;
  wrap.appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

function esc(s) { return String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

/* token 单位换算 */
function tokToUnit(t) {
  t = +t || 0;
  if (t && t % 10000 === 0) return { v: t / 10000, u: "万" };
  if (t && t % 1000 === 0) return { v: t / 1000, u: "千" };
  return { v: t, u: "千" };
}
function unitToTok(v, u) { return Math.round((+v || 0) * (u === "万" ? 10000 : 1000)); }

/* 连接前缀 host 解析 */
function hostOfPrefix(prefix) {
  const m = String(prefix || "").match(/@([^:/\s]+)/);
  return m ? m[1] : "";
}
function isLocalPrefix(prefix) {
  const h = hostOfPrefix(prefix).replace(/^\[|\]$/g, "");
  return h === "localhost" || h === "127.0.0.1" || h === "::1";
}

/* 确认弹窗 */
function askConfirm(prompt) {
  return new Promise(resolve => {
    const mask = document.createElement("div");
    mask.className = "modal-mask";
    mask.innerHTML = `
      <div class="modal">
        <h3>${t("确认")}</h3>
        <div class="modal-body">${esc(prompt)}</div>
        <div class="modal-actions">
          <button class="btn" data-v="no">${t("否")}</button>
          <button class="btn primary" data-v="yes">${t("是")}</button>
        </div>
      </div>`;
    document.body.appendChild(mask);
    mask.querySelectorAll("[data-v]").forEach(b => b.onclick = () => { resolve(b.dataset.v); mask.remove(); });
  });
}

async function checkMcp(transport, url, command) {
  try {
    const r = await api("/api/mcp-check", { method: "POST", body: JSON.stringify({ transport, url: url || null, command: command || null }) });
    return r.ok;
  } catch (e) { return false; }
}

/* 系统设置 */
async function getSettings() {
  if (!settingsCache) settingsCache = await api("/api/settings");
  return settingsCache;
}
async function saveSettings(s) {
  settingsCache = await api("/api/settings", { method: "PUT", body: JSON.stringify(s) });
  return settingsCache;
}

/* 提示音：Web Audio 合成，无需音频文件 */
function playSound(type) {
  if (!type || type === "none") return;
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const t = ctx.currentTime;
    const tone = (freq, start, dur, vol, endFreq) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.setValueAtTime(freq, t + start);
      if (endFreq) osc.frequency.exponentialRampToValueAtTime(endFreq, t + start + dur);
      gain.gain.setValueAtTime(vol, t + start);
      gain.gain.exponentialRampToValueAtTime(0.001, t + start + dur);
      osc.connect(gain).connect(ctx.destination);
      osc.start(t + start);
      osc.stop(t + start + dur + 0.02);
    };
    if (type === "ber") tone(1200, 0, 0.28, 0.25, 520);
    else if (type === "ding") tone(880, 0, 0.25, 0.25);
    else if (type === "chime") { tone(660, 0, 0.2, 0.2); tone(880, 0.12, 0.25, 0.2); }
  } catch (e) {}
}

/* 缩放 */
let zoomPct = parseInt(localStorage.getItem("md-zoom") || "100", 10);
function applyZoom() {
  document.documentElement.style.setProperty("--md-font-size", (15 * zoomPct / 100).toFixed(1) + "px");
}

/* 缩放锚点：记录缩放前视口顶端的文本/元素位置，缩放后回滚使其仍停留在视口顶部，避免长内容乱跳 */
function captureZoomAnchor() {
  const pane = $("#historyPane");
  if (!pane) return null;
  const rect = pane.getBoundingClientRect();
  if (rect.height < 8) return null;
  const x = rect.left + 1;
  const y = rect.top + 1;
  let range = null;
  if (document.caretRangeFromPoint) range = document.caretRangeFromPoint(x, y);
  else if (document.caretPositionFromPoint) {
    const pos = document.caretPositionFromPoint(x, y);
    if (pos) { range = document.createRange(); range.setStart(pos.offsetNode, pos.offset); range.collapse(true); }
  }
  if (range && range.startContainer && pane.contains(range.startContainer)) {
    return { range: range, beforeTop: range.getBoundingClientRect().top };
  }
  let el = document.elementFromPoint(x, y);
  if (!el || !pane.contains(el)) el = pane;
  return { el: el, beforeTop: el.getBoundingClientRect().top };
}
function restoreZoomAnchor(anchor) {
  if (!anchor) return;
  const pane = $("#historyPane");
  if (!pane) return;
  const afterTop = anchor.range ? anchor.range.getBoundingClientRect().top : anchor.el.getBoundingClientRect().top;
  pane.scrollTop += (afterTop - anchor.beforeTop);
}

function changeZoom(delta) {
  const anchor = captureZoomAnchor();
  zoomPct = Math.min(200, Math.max(60, zoomPct + delta));
  localStorage.setItem("md-zoom", String(zoomPct));
  applyZoom();
  restoreZoomAnchor(anchor);
}

/* 思考字号（相对正文的百分比） */
let reasoningZoomPct = parseInt(localStorage.getItem("reasoning-zoom") || "100", 10);
function applyReasoningZoom() {
  document.documentElement.style.setProperty("--reasoning-font-scale", (reasoningZoomPct / 100).toFixed(2));
}
function changeReasoningZoom(delta) {
  const anchor = captureZoomAnchor();
  reasoningZoomPct = Math.min(200, Math.max(50, reasoningZoomPct + delta));
  localStorage.setItem("reasoning-zoom", String(reasoningZoomPct));
  applyReasoningZoom();
  restoreZoomAnchor(anchor);
}

/* 板块字体颜色 */
const COLOR_FIELDS = [
  { key: "color-reasoning", css: "--reasoning-color", def: "#6b7280" },
  { key: "color-content", css: "--content-color", def: "#1f2328" },
  { key: "color-tool-result", css: "--tool-result-color", def: "#475569" },
];
function getColor(key, def) { return localStorage.getItem(key) || def; }
function applyColors() {
  const root = document.documentElement;
  for (const f of COLOR_FIELDS) {
    const v = localStorage.getItem(f.key);
    if (v) root.style.setProperty(f.css, v);
    else root.style.removeProperty(f.css);
  }
}

/* 未保存提醒 */
function bindDirty(container) {
  editorDirty = false;
  container.addEventListener("input", () => { editorDirty = true; });
  container.addEventListener("change", () => { editorDirty = true; });
}
async function confirmLeave() {
  if (!editorDirty) return true;
  try { const s = await getSettings(); if (!s.warn_unsaved_changes) return true; } catch (e) {}
  return confirm(t("有未保存的修改，确定离开？"));
}
function maskedPrefix(p) {
  return String(p || "")
    .replace(/\/\/([^:@/]+):[^@/]+@/, "//$1:passwd@")
    .replace(/(@[^:/]+):\d+\//, "$1:port/");
}

/* ================= Markdown 渲染器 ================= */
const md = window.markdownit ? (() => {
  const inst = window.markdownit({
    html: true,
    linkify: true,
    breaks: true,
    highlight(code, lang) {
      if (lang && window.hljs && window.hljs.getLanguage(lang)) {
        try { return '<pre class="hljs"><code>' + window.hljs.highlight(code, { language: lang }).value + "</code></pre>"; } catch (e) {}
      }
      return '<pre class="hljs"><code>' + inst.utils.escapeHtml(code) + "</code></pre>";
    },
  });
  if (window.texmath && window.katex) {
    try {
      inst.use(window.texmath, {
        engine: window.katex,
        delimiters: ["dollars", "brackets"],
        katexOptions: { throwOnError: false },
      });
    } catch (e) {}
  }
  if (window.katex) {
    // 让独占一行的 \[...\] / $$...$$ 成为 paragraph 的终止符：
    // 否则 paragraph 会连行吞掉它们，texmath 的 block 规则没机会在行首生效。
    inst.block.ruler.before("paragraph", "math_block_line", function (state, startLine, endLine, silent) {
      const start = state.bMarks[startLine] + state.tShift[startLine];
      const max = state.eMarks[startLine];
      const line = state.src.slice(start, max);
      const isBracketStart = line.length >= 2 && line.charCodeAt(0) === 92 && line[1] === "[";
      const isDollarStart = line.startsWith("$$");
      if (!isBracketStart && !isDollarStart) return false;
      // 行首是公式开始（单行或多行）就终止 paragraph，交给 texmath 的 block 规则跨行匹配
      if (silent) return true;
      let content = null;
      if (isBracketStart && line.endsWith("\\]") && line.length >= 4) {
        content = line.slice(2, -2);
      } else if (isDollarStart && line.endsWith("$$") && line.length >= 4) {
        content = line.slice(2, -2);
      }
      if (content === null) return false;
      const token = state.push("math_block_line", "math", 0);
      token.content = content;
      token.block = true;
      token.map = [startLine, startLine + 1];
      state.line = startLine + 1;
      return true;
    }, { alt: ["paragraph", "reference", "blockquote", "list"] });
    inst.renderer.rules.math_block_line = function (tokens, idx) {
      try {
        return window.katex.renderToString(tokens[idx].content, { displayMode: true, throwOnError: false });
      } catch (e) {
        return esc(tokens[idx].content);
      }
    };
    // 行内 \[...\] 兜底：texmath 的 brackets 只有 \(...\) 行内规则，没有 \[...\] 行内规则，
    // 导致 \[...\] 出现在段落中间（非独占一行）时被 escape 规则吃掉反斜杠、按原文显示。
    inst.inline.ruler.before("escape", "math_inline_bracket", function (state, silent) {
      const pos = state.pos;
      const src = state.src;
      if (src.charCodeAt(pos) !== 92 || src[pos + 1] !== "[") return false;
      const close = src.indexOf("\\]", pos + 2);
      if (close === -1) return false;
      const nl = src.indexOf("\n", pos);
      if (nl !== -1 && close > nl) return false;
      const content = src.slice(pos + 2, close).trim();
      if (!silent) {
        const token = state.push("math_inline_bracket", "math", 0);
        token.content = content;
        token.markup = "\\[";
      }
      state.pos = close + 2;
      return true;
    });
    inst.renderer.rules.math_inline_bracket = function (tokens, idx) {
      try {
        return window.katex.renderToString(tokens[idx].content, { displayMode: false, throwOnError: false });
      } catch (e) {
        return esc(tokens[idx].content);
      }
    };
  }
  return inst;
})() : null;
/* 裸公式识别：模型偶尔不带 $/\( 分隔符直接输出 LaTeX（如 s_{t+1}=f(s_t,a_t)）。
   按启发式把「强信号」片段包裹成 \(...\) 交给 KaTeX；强信号 = \命令 或 花括号/数字上下标。 */
const isBareMathChar = (c) => !!c && /[A-Za-z0-9_^{}().,=+\-*/<>|'~]/.test(c);
const isBareMathOp = (c) => !!c && /[=+\-*/<>|,^_]/.test(c);
function bareMathSeedEnd(s, i) {
  const c = s[i];
  if (c === undefined) return -1;
  if (c === "\\") {
    const m = /\\[A-Za-z]+/.exec(s.slice(i));
    return m ? i + m[0].length : -1;
  }
  if (!/[A-Za-z0-9)\]}]/.test(c)) return -1;
  const nxt = s[i + 1];
  if (nxt !== "_" && nxt !== "^") return -1;
  const after = s[i + 2];
  if (after === "{") {
    let j = i + 3, depth = 1;
    while (j < s.length && depth > 0) {
      if (s[j] === "{") depth++;
      else if (s[j] === "}") depth--;
      j++;
    }
    return depth === 0 ? j : -1;
  }
  return /[0-9]/.test(after || "") ? i + 3 : -1;
}
function bareMathExpandLeft(s, start) {
  while (start > 0) {
    const c = s[start - 1];
    if (isBareMathChar(c)) { start--; continue; }
    if (c === " " || c === "\t") {
      let k = start - 1;
      while (k > 0 && (s[k] === " " || s[k] === "\t")) k--;
      const leftCh = s[k];
      if (!isBareMathChar(leftCh)) break;
      if (isBareMathOp(leftCh) || s[start] === "\\") { start = k; continue; }
    }
    break;
  }
  return start;
}
function bareMathExpandRight(s, end) {
  while (end < s.length) {
    const c = s[end];
    if (isBareMathChar(c)) { end++; continue; }
    if (c === "\\" && /[A-Za-z]/.test(s[end + 1] || "")) {
      end += 2;
      while (end < s.length && /[A-Za-z]/.test(s[end])) end++;
      continue;
    }
    if (c === " " || c === "\t") {
      let k = end;
      while (k < s.length && (s[k] === " " || s[k] === "\t")) k++;
      const prev = s[end - 1];
      const nxt = s[k];
      const prevOk = isBareMathOp(prev);
      const nxtOk = isBareMathOp(nxt) || (nxt === "\\" && /[A-Za-z]/.test(s[k + 1] || ""));
      if (prevOk || nxtOk) { end = k; continue; }
    }
    break;
  }
  while (end > 0 && (s[end - 1] === " " || s[end - 1] === "\t")) end--;
  return end;
}
function autodetectMath(text) {
  const stash = [];
  const P = "\u0001";
  const protect = () => (m) => { stash.push(m); return P + (stash.length - 1) + P; };
  let s = String(text == null ? "" : text);
  s = s.replace(/```[\s\S]*?```/g, protect());
  s = s.replace(/~~~[\s\S]*?~~~/g, protect());
  s = s.replace(/`[^`\n]*`/g, protect());
  s = s.replace(/\$\$[\s\S]*?\$\$/g, protect());
  s = s.replace(/\\\[[\s\S]*?\\\]/g, protect());
  s = s.replace(/\\\([^\n]*?\\\)/g, protect());
  s = s.replace(/\$[^$\n]*?\$/g, protect());

  const spans = [];
  for (let i = 0; i < s.length; i++) {
    const se = bareMathSeedEnd(s, i);
    if (se < 0) continue;
    const start = bareMathExpandLeft(s, i);
    const end = bareMathExpandRight(s, se);
    if (s.slice(start, end).indexOf(P) !== -1) { i = se - 1; continue; }
    spans.push({ start, end });
    i = end - 1;
  }
  spans.sort((a, b) => a.start - b.start);
  const merged = [];
  for (const sp of spans) {
    const last = merged[merged.length - 1];
    if (last && sp.start <= last.end) last.end = Math.max(last.end, sp.end);
    else merged.push({ start: sp.start, end: sp.end });
  }
  let out = "", pos = 0;
  for (const sp of merged) {
    if (s.slice(sp.start, sp.end).trim() === "") continue;
    out += s.slice(pos, sp.start) + "\\(" + s.slice(sp.start, sp.end) + "\\)";
    pos = sp.end;
  }
  out += s.slice(pos);
  return out.replace(new RegExp(P + "(\\d+)" + P, "g"), (m, idx) => stash[+idx]);
}
function bareMathEnabled() { return !!(settingsCache && settingsCache.bare_math_detect); }
function renderMd(text) {
  const t = text || "";
  if (!md) return esc(t);
  return md.render(bareMathEnabled() ? autodetectMath(t) : t);
}

/* ================= 状态 ================= */
const S = { view: "agents", agentId: null, agentName: null, threadId: null, editingDefault: false };
const app = $("#app");
let ws = null;
let isRunning = false;
let currentReplyEl = null;
let editorDirty = false;
let settingsCache = null;
let pinned = localStorage.getItem("pin-follow") === "1";

/* ================= 顶栏 ================= */
function topbar(backLabel, backAction) {
  const back = backAction ? `<button class="btn small" id="backBtn">← ${t(backLabel || "返回")}</button>` : "";
  return `<div class="topbar">
    <div class="logo">Multi-Agent Studio</div>
    <div class="crumb">${S.agentName || ""}</div>
    <div class="spacer"></div>${back}
  </div>`;
}
function bindBack(cb) { const b = $("#backBtn"); if (b) b.onclick = cb; }

/* ================= 视图调度 ================= */
function render() {
  app.innerHTML = "";
  isRunning = false; ws = null; currentReplyEl = null; editorDirty = false;
  if (S.view === "agents") renderAgents();
  else if (S.view === "editor") renderEditorView();
  else if (S.view === "threads") renderThreadsView();
  else if (S.view === "chat") renderChatView();
}
function goAgents() { S.view = "agents"; S.agentId = null; S.agentName = null; S.threadId = null; render(); }
function goThreads(agentId, agentName) { S.view = "threads"; S.agentId = agentId; S.agentName = agentName; S.threadId = null; render(); }
function goChat(agentId, agentName, threadId) { S.view = "chat"; S.agentId = agentId; S.agentName = agentName; S.threadId = threadId; render(); }

/* ================= 视图1：Agent 列表 ================= */
async function renderAgents() {
  app.innerHTML = topbar();
  const view = document.createElement("div");
  view.className = "view";
  app.appendChild(view);
  view.innerHTML = `
    <div style="display:flex;align-items:center;margin-bottom:20px;gap:12px;">
      <h2 class="section-title" style="margin:0;">${t("Multi-Agent 配置")}</h2>
      <div class="spacer" style="flex:1;"></div>
      <button class="btn small" id="langBtn" title="切换语言 / Switch language">${lang === "zh" ? "EN" : "中文"}</button>
      <button class="gear-btn" id="gearBtn" title="${t("系统设置")}">⚙️</button>
      <button class="btn small" id="editDefaultBtn">${t("编辑默认配置")}</button>
      <button class="btn primary" id="newBtn">+ ${t("新建 multi-agent")}</button>
    </div>
    <div id="agentGrid" class="card-grid"><div class="muted">${t("加载中…")}</div></div>`;

  $("#newBtn").onclick = () => { S.editingDefault = false; S.agentId = null; S.view = "editor"; render(); };
  $("#editDefaultBtn").onclick = () => { S.editingDefault = true; S.agentId = null; S.view = "editor"; render(); };
  $("#gearBtn").onclick = () => openSettings();
  $("#langBtn").onclick = () => setLang(lang === "zh" ? "en" : "zh");

  let agents;
  try { agents = await api("/api/agents"); }
  catch (e) { $("#agentGrid").innerHTML = `<div class="empty"><div class="big">⚠️</div>${esc(e.message)}</div>`; return; }

  if (!agents.length) {
    $("#agentGrid").innerHTML = `<div class="empty"><div class="big">📦</div>${t("还没有任何 multi-agent 配置")}<br/><br/><button class="btn primary" id="newBtn2">+ ${t("新建 multi-agent")}</button></div>`;
    const n = $("#newBtn2"); if (n) n.onclick = () => { S.editingDefault = false; S.agentId = null; S.view = "editor"; render(); };
    return;
  }

  $("#agentGrid").innerHTML = agents.map(a => `
    <div class="agent-card" data-id="${esc(a.agent_id)}">
      <h3>${esc(a.name)}</h3>
      <div class="meta">
        ${t("checkpoint 库")}：<code>${esc(a.postgres.checkpoint_database)}</code><br/>
        ${t("子 agent")}：${a.sub_agents.length} ${t("个")}（${a.sub_agents.map(s => esc(s.name)).join(t("、")) || t("无")}）<br/>
        ${t("主模型")}：<code>${esc(a.main_agent.llm_provider_name)}</code>
      </div>
      <div class="actions">
        <button class="btn primary small" data-act="open">${t("打开")}</button>
        <button class="btn small" data-act="edit">${t("编辑")}</button>
        <button class="btn small" data-act="default">${t("设为默认")}</button>
        <button class="btn danger small" data-act="del">${t("删除")}</button>
      </div>
    </div>`).join("");

  $$(".agent-card").forEach(card => {
    const id = card.dataset.id;
    const name = agents.find(a => a.agent_id === id).name;
    card.addEventListener("click", e => { if (e.target.closest("button")) return; goThreads(id, name); });
    $$("button", card).forEach(btn => {
      btn.onclick = (e) => {
        e.stopPropagation();
        const act = btn.dataset.act;
        if (act === "open") goThreads(id, name);
        else if (act === "edit") { S.editingDefault = false; S.agentId = id; S.view = "editor"; render(); }
        else if (act === "default") setDefault(id);
        else if (act === "del") deleteAgent(id, name);
      };
    });
  });
}

async function openSettings() {
  let s;
  try { s = await getSettings(); } catch (e) { toast(e.message, true); return; }
  const mask = document.createElement("div");
  mask.className = "modal-mask";
  mask.innerHTML = `
    <div class="modal" style="width:480px;">
      <h3>⚙️ ${t("系统设置")}</h3>
      <div class="switch-row">
        <span class="sw-label">${t("修改未保存时提醒")} <i class="info-icon">!<span class="tip">${t("编辑 multi-agent 配置时，若做了改动但未保存就离开，弹窗确认。")}</span></i></span>
        <label class="toggle"><input type="checkbox" id="set_warn" ${s.warn_unsaved_changes ? "checked" : ""}><span class="track"></span></label>
      </div>
      <div class="switch-row">
        <span class="sw-label">${t("显示输入示例（灰色占位字）")} <i class="info-icon">!<span class="tip">${t("新建 / 编辑 multi-agent 时，空输入框显示灰色示例文字，帮助快速上手。")}</span></i></span>
        <label class="toggle"><input type="checkbox" id="set_ph" ${s.show_placeholders ? "checked" : ""}><span class="track"></span></label>
      </div>
      <div class="switch-row">
        <span class="sw-label">${t("记忆吸附")} <i class="info-icon">!<span class="tip">${t("开启后，主 agent 收到用户消息时会先从记忆库语义检索 N 条相关记忆，附在用户消息里一起传入。此设置影响图编译，进入某个 multi-agent 后不可改动，需退回主界面。")}</span></i></span>
        <label class="toggle"><input type="checkbox" id="set_mem" ${s.memory_attach ? "checked" : ""}><span class="track"></span></label>
      </div>
      <div class="switch-row">
        <span class="sw-label">${t("吸附记忆条数")}</span>
        <input type="number" id="set_memnum" value="${s.num_memories_attached}" min="1" max="20" ${s.memory_attach ? "" : "disabled"}>
      </div>
      <div class="switch-row">
        <span class="sw-label">${t("回复完成提示音")} <i class="info-icon">!<span class="tip">${t("agent 回复完成时播放的提示音，切换即试听。")}</span></i></span>
        <select id="set_sound">
          <option value="none" ${s.notification_sound === "none" ? "selected" : ""}>${t("无")}</option>
          <option value="ber" ${s.notification_sound === "ber" ? "selected" : ""}>${t("ber（下滑音）")}</option>
          <option value="ding" ${s.notification_sound === "ding" ? "selected" : ""}>${t("ding（清脆单音）")}</option>
          <option value="chime" ${s.notification_sound === "chime" ? "selected" : ""}>${t("chime（双音上行）")}</option>
        </select>
      </div>
      <div class="switch-row">
        <span class="sw-label">${t("发送键")} <i class="info-icon">!<span class="tip">${t("聊天输入框里，按该键发送消息。")}</span></i></span>
        <select id="set_send">
          <option value="enter" ${s.send_key === "enter" ? "selected" : ""}>Enter</option>
          <option value="shift_enter" ${s.send_key === "shift_enter" ? "selected" : ""}>Shift + Enter</option>
          <option value="ctrl_enter" ${s.send_key === "ctrl_enter" ? "selected" : ""}>Ctrl + Enter</option>
          <option value="mouse_only" ${s.send_key === "mouse_only" ? "selected" : ""}>${t("仅鼠标（Enter 只换行）")}</option>
        </select>
      </div>
      <div class="switch-row">
        <span class="sw-label">${t("换行键")} <i class="info-icon">!<span class="tip">${t("除发送键外，其余 Enter 组合均换行。")}</span></i></span>
        <select id="set_newline">
          <option value="enter" ${s.newline_key === "enter" ? "selected" : ""}>Enter</option>
          <option value="shift_enter" ${s.newline_key === "shift_enter" ? "selected" : ""}>Shift + Enter</option>
          <option value="ctrl_enter" ${s.newline_key === "ctrl_enter" ? "selected" : ""}>Ctrl + Enter</option>
        </select>
      </div>
      <div class="switch-row">
        <span class="sw-label">${t("裸公式识别")} <i class="info-icon">!<span class="tip">${t("模型偶尔不带 $ 或 \\( 分隔符直接输出公式（如 s_{t+1}=f(s_t,a_t)）。开启后自动识别并渲染，适合科研 / 数理场景；日常场景建议关闭，以免误判普通文本。")}</span></i></span>
        <label class="toggle"><input type="checkbox" id="set_baremath" ${s.bare_math_detect ? "checked" : ""}><span class="track"></span></label>
      </div>
      <div class="modal-actions">
        <button class="btn" id="setAdvanced">${t("进阶设置")}</button>
        <button class="btn" id="setColors">${t("字体颜色设置")}</button>
        <div class="spacer" style="flex:1;"></div>
        <button class="btn" id="setCancel">${t("取消")}</button>
        <button class="btn primary" id="setSave">${t("保存")}</button>
      </div>
    </div>`;
  document.body.appendChild(mask);
  mask.querySelector("#set_mem").addEventListener("change", e => { mask.querySelector("#set_memnum").disabled = !e.target.checked; });
  mask.querySelector("#set_sound").addEventListener("change", e => playSound(e.target.value));

  const sendSel = mask.querySelector("#set_send");
  const newlineSel = mask.querySelector("#set_newline");
  const updateNewline = () => {
    const sk = sendSel.value;
    newlineSel.querySelectorAll("option").forEach(o => { o.disabled = (o.value === sk && sk !== "mouse_only"); });
    if (newlineSel.selectedOptions[0] && newlineSel.selectedOptions[0].disabled) {
      newlineSel.value = newlineSel.querySelector("option:not([disabled])").value;
    }
  };
  sendSel.addEventListener("change", updateNewline);
  updateNewline();

  mask.querySelector("#setCancel").onclick = () => mask.remove();
  mask.querySelector("#setAdvanced").onclick = () => { mask.remove(); openAdvancedSettings(); };
  mask.querySelector("#setColors").onclick = () => { mask.remove(); openColorSettings(); };
  mask.querySelector("#setSave").onclick = async () => {
    try {
      await saveSettings({
        ...s,
        warn_unsaved_changes: mask.querySelector("#set_warn").checked,
        memory_attach: mask.querySelector("#set_mem").checked,
        num_memories_attached: +mask.querySelector("#set_memnum").value || 3,
        notification_sound: mask.querySelector("#set_sound").value,
        send_key: sendSel.value,
        newline_key: newlineSel.value,
        show_placeholders: mask.querySelector("#set_ph").checked,
        bare_math_detect: mask.querySelector("#set_baremath").checked,
      });
      mask.remove();
      toast(t("设置已保存"));
    } catch (e) { toast(e.message, true); }
  };
}

async function openAdvancedSettings() {
  let s;
  try { s = await getSettings(); } catch (e) { toast(e.message, true); return; }
  const mask = document.createElement("div");
  mask.className = "modal-mask";
  mask.innerHTML = `
    <div class="modal" style="width:480px;">
      <h3>🧩 ${t("进阶设置")}</h3>
      <div class="muted" style="margin-bottom:4px;">${t("历史浏览时各板块默认展开 / 折叠")}（${t("开启后默认展开")}）</div>
      <div class="switch-row">
        <span class="sw-label">🧠 ${t("思考过程")} <i class="info-icon">!<span class="tip">${t("历史浏览时，思考过程板块默认展开还是折叠。")}</span></i></span>
        <label class="toggle"><input type="checkbox" id="adv_reasoning" ${s.reasoning_expanded ? "checked" : ""}><span class="track"></span></label>
      </div>
      <div class="switch-row">
        <span class="sw-label">🔧 ${t("工具调用")} <i class="info-icon">!<span class="tip">${t("历史浏览时，工具调用板块默认展开还是折叠。")}</span></i></span>
        <label class="toggle"><input type="checkbox" id="adv_tool_call" ${s.tool_call_expanded ? "checked" : ""}><span class="track"></span></label>
      </div>
      <div class="switch-row">
        <span class="sw-label">✅ ${t("工具结果")} <i class="info-icon">!<span class="tip">${t("历史浏览时，工具结果板块默认展开还是折叠。")}</span></i></span>
        <label class="toggle"><input type="checkbox" id="adv_tool_result" ${s.tool_result_expanded ? "checked" : ""}><span class="track"></span></label>
      </div>
      <div class="modal-actions">
        <button class="btn" id="advCancel">${t("取消")}</button>
        <button class="btn primary" id="advSave">${t("保存")}</button>
      </div>
    </div>`;
  document.body.appendChild(mask);
  mask.querySelector("#advCancel").onclick = () => mask.remove();
  mask.querySelector("#advSave").onclick = async () => {
    try {
      await saveSettings({
        ...s,
        reasoning_expanded: mask.querySelector("#adv_reasoning").checked,
        tool_call_expanded: mask.querySelector("#adv_tool_call").checked,
        tool_result_expanded: mask.querySelector("#adv_tool_result").checked,
      });
      mask.remove();
      toast(t("设置已保存"));
    } catch (e) { toast(e.message, true); }
  };
}

async function openColorSettings() {
  const mask = document.createElement("div");
  mask.className = "modal-mask";
  const row = (f, label) => `
    <div class="switch-row">
      <span class="sw-label">${label}</span>
      <input type="color" id="${f.key}" value="${getColor(f.key, f.def)}">
    </div>`;
  mask.innerHTML = `
    <div class="modal" style="width:480px;">
      <h3>🎨 ${t("字体颜色设置")}</h3>
      <div class="muted" style="margin-bottom:4px;">${t("调整各板块文字颜色，仅本机生效。")}</div>
      ${row(COLOR_FIELDS[0], `🧠 ${t("思考过程")}`)}
      ${row(COLOR_FIELDS[1], `📝 ${t("正文")}`)}
      ${row(COLOR_FIELDS[2], `✅ ${t("工具结果")}`)}
      <div class="modal-actions">
        <button class="btn" id="colorReset">${t("恢复默认")}</button>
        <div class="spacer" style="flex:1;"></div>
        <button class="btn" id="colorCancel">${t("取消")}</button>
        <button class="btn primary" id="colorSave">${t("保存")}</button>
      </div>
    </div>`;
  document.body.appendChild(mask);

  mask.querySelector("#colorCancel").onclick = () => mask.remove();
  mask.querySelector("#colorReset").onclick = () => {
    for (const f of COLOR_FIELDS) {
      localStorage.removeItem(f.key);
      const el = mask.querySelector("#" + f.key);
      if (el) el.value = f.def;
    }
    applyColors();
  };
  mask.querySelector("#colorSave").onclick = () => {
    for (const f of COLOR_FIELDS) {
      const el = mask.querySelector("#" + f.key);
      if (el) localStorage.setItem(f.key, el.value);
    }
    applyColors();
    mask.remove();
    toast(t("设置已保存"));
  };
}

async function setDefault(id) {
  try { const cfg = await api("/api/agents/" + encodeURIComponent(id)); await api("/api/default", { method: "PUT", body: JSON.stringify(cfg) }); toast(t("已设为默认配置")); }
  catch (e) { toast(e.message, true); }
}
async function deleteAgent(id, name) {
  if (!confirm(`${t("确定删除配置")}「${name}」？\n（${t("不会删除数据库里的会话历史")}）`)) return;
  try { await api("/api/agents/" + encodeURIComponent(id), { method: "DELETE" }); toast(t("已删除")); renderAgents(); }
  catch (e) { toast(e.message, true); }
}

/* ================= 视图2：配置编辑器 ================= */
async function renderEditorView() {
  const leave = async () => { if (await confirmLeave()) goAgents(); };
  app.innerHTML = topbar("返回", () => leave());
  const view = document.createElement("div");
  view.className = "view";
  app.appendChild(view);
  bindBack(() => leave());

  const isNew = !S.agentId && !S.editingDefault;
  const isDefault = S.editingDefault;
  let cfg;
  if (S.editingDefault) cfg = await api("/api/default");
  else if (S.agentId) cfg = await api("/api/agents/" + encodeURIComponent(S.agentId));
  else cfg = await api("/api/default");

  const canEditSubs = isNew || isDefault;
  const title = isDefault ? t("编辑默认配置") : (isNew ? t("新建 multi-agent") : `${t("编辑")}「${cfg.name}」`);

  view.innerHTML = `
    <h2 class="section-title">${esc(title)}</h2>
    ${isNew ? `<div class="muted" style="margin-bottom:16px;">${t("表单已按默认配置预填，请修改差异项。创建后子 agent 不可增删改名。")}</div>` : ""}
    <div id="editorForm"></div>
    <div style="margin-top:20px;display:flex;gap:12px;justify-content:flex-end;">
      <button class="btn" id="cancelBtn">${t("取消")}</button>
      <button class="btn primary" id="saveBtn">${t("保存")}</button>
    </div>`;
  $("#cancelBtn").onclick = () => leave();
  $("#saveBtn").onclick = () => saveConfig(cfg, isNew, isDefault);
  await getSettings().catch(() => {});
  buildForm(cfg, canEditSubs);
  bindDirty(view);
}

const info = (tip) => `<i class="info-icon">!<span class="tip">${t(tip)}</span></i>`;

const PH = {
  agent_id: "例如 my-agent",
  name: "例如 研究助理",
  checkpoint_database: "例如 my_agent_checkpoints",
  prefix: "例如 postgresql://user:passwd@localhost:5432/",
  main_system_prompt: "例如：你是一名研究助理，负责理解用户问题，将复杂任务拆解并委派给合适的子 agent，汇总后给出结构化、准确的回答。请使用中文。",
  main_api_key: "例如 sk-...",
  root_dir: "例如 C:\\Agent_WorkPlace",
  emb_cache: "留空则使用默认缓存目录",
  sub_name: "例如 web_search_agent",
  sub_description: "例如：负责联网搜索，将需要查询的内容与注意事项告知它，它会返回检索到的原文片段。",
  sub_system_prompt: "例如：你是一名联网搜索子 agent，负责为主 agent 检索网页信息，最终输出检索到的原文或相关片段。",
  sub_api_key: "例如 sk-...",
};
const ph = (key) => (settingsCache && settingsCache.show_placeholders !== false) ? ` placeholder="${esc(t(PH[key]))}"` : "";
const slugify = (s) => (s || "").trim().replace(/[^0-9a-zA-Z\u4e00-\u9fa5_-]+/g, "-").replace(/^-+|-+$/g, "").toLowerCase() || "agent";

// tooltip 自动感知边界：右侧放不下翻到左侧（事件委托，覆盖动态创建的 info-icon）
document.addEventListener("mouseover", (e) => {
  const icon = e.target.closest && e.target.closest(".info-icon");
  if (!icon) return;
  const tip = icon.querySelector(".tip");
  if (!tip) return;
  tip.classList.remove("tip-left");
  tip.style.visibility = "hidden";
  tip.style.display = "block";
  const rect = icon.getBoundingClientRect();
  const tw = tip.offsetWidth;
  if (rect.right + 16 + tw > window.innerWidth) tip.classList.add("tip-left");
  tip.style.display = "";
  tip.style.visibility = "";
  icon.classList.add("show");
});
document.addEventListener("mouseout", (e) => {
  const icon = e.target.closest && e.target.closest(".info-icon");
  if (icon) icon.classList.remove("show");
});

function unitField(id, val, unit, extraLabel, tip) {
  return `<div class="field"><label>${esc(extraLabel || "")}${tip ? info(tip) : ""}</label>
    <div class="unit-row">
      <input id="${id}" value="${esc(val)}" type="number">
      <select id="${id}_unit"><option value="万" ${unit === "万" ? "selected" : ""}>${t("万")}</option><option value="千" ${unit === "千" ? "selected" : ""}>${t("千")}</option></select>
    </div></div>`;
}

/* 模型配置块：入口分支（内置/第三方）+ base_url + 额外采样参数 */
function modelBlockHTML(llmName, mc, group) {
  mc = mc || {};
  const isCompat = mc.provider_mode === "openai_compatible";
  const v = (x) => esc(x == null ? "" : x);
  const optWarn = (label) => t("请确认你的模型 API 提供商支持 {param} 参数").replace("{param}", label);

  const optChecks = EXTRA_PARAMS.map(p =>
    `<label><input type="checkbox" data-opt="${p.key}" ${mc[p.key] != null ? "checked" : ""}> ${p.key}</label>`).join("");

  const optFields = EXTRA_PARAMS.map(p =>
    `<div class="field opt-field" data-optfield="${p.key}" style="${mc[p.key] != null ? "" : "display:none;"}">
      <label>${p.key} <span class="opt-warn">⚠ ${optWarn(p.key)}</span></label>
      <input data-mf="${p.key}" value="${v(mc[p.key])}" type="number" step="${p.step}" min="${p.min}">
      <span class="hint">${t(p.desc)}</span>
      <span class="hint">${t(p.range)}</span>
    </div>`).join("");

  return `
    <div class="model-block" data-model-block>
      <div class="model-source-card">
        <div class="model-source-title">${t("模型来源")}</div>
        <div class="radio-list">
          <label class="radio-item"><input type="radio" name="${group}" value="official" ${!isCompat ? "checked" : ""}> ${t("内置模型")}</label>
          <label class="radio-item"><input type="radio" name="${group}" value="openai_compatible" ${isCompat ? "checked" : ""}> ${t("任意第三方模型（OpenAI 协议）")}</label>
        </div>
      </div>
      <div class="field">
        <label><span data-model-label>${isCompat ? t("模型名") : t("模型 provider")}</span> <i class="info-icon">!<span class="tip" data-model-tip>${isCompat ? t("填纯模型名，不要带供应商前缀（如 deepseek:）。第三方模型的 llm-provider 固定为 openai，不能是其他。") : t("格式「供应商:模型名」，如 deepseek:deepseek-v4-pro。已内置集成：openai / anthropic / deepseek / google_genai。")}</span></i></label>
        <input data-mf="llm_provider_name" value="${v(llmName)}">
        <span class="warn-hint" data-official-warn style="display:none;">⚠ ${t("该 provider 未内置集成，建议切换为「任意第三方模型（OpenAI 协议）」")}</span>
      </div>
      <div class="field" data-baseurl-field style="${isCompat ? "" : "display:none;"}">
        <label>base_url <i class="info-icon">!<span class="tip">${t("应指向 /v1 根路径，LangChain 会自动拼接 /chat/completions。不要把 /chat/completions 写进 base_url，否则会 404。")}</span></i></label>
        <input data-mf="base_url" value="${v(mc.base_url)}" placeholder="https://api.openai.com/v1">
      </div>
      <div class="field full">
        <details class="db-help">
          <summary>${t("额外选项")}</summary>
          <div class="opt-checks">${optChecks}</div>
          ${optFields}
        </details>
      </div>
    </div>`;
}

function bindModelBlock(root) {
  const block = root.querySelector("[data-model-block]");
  if (!block) return;
  const llmInput = block.querySelector('[data-mf="llm_provider_name"]');
  const baseUrlField = block.querySelector("[data-baseurl-field]");
  const label = block.querySelector("[data-model-label]");
  const tip = block.querySelector("[data-model-tip]");
  const warn = block.querySelector("[data-official-warn]");
  let mode = block.querySelector('input[type="radio"]:checked').value;

  const checkOfficial = () => {
    const raw = (llmInput.value || "").trim();
    const prov = (raw.match(/^([^:\s]+):/) || [])[1] || "";
    const ok = !prov || OFFICIAL_PROVIDERS.includes(prov.toLowerCase());
    warn.style.display = ok ? "none" : "";
  };

  const setMode = (m) => {
    mode = m;
    const isCompat = m === "openai_compatible";
    baseUrlField.style.display = isCompat ? "" : "none";
    label.textContent = isCompat ? t("模型名") : t("模型 provider");
    tip.textContent = isCompat
      ? t("填纯模型名，不要带供应商前缀（如 deepseek:）。第三方模型的 llm-provider 固定为 openai，不能是其他。")
      : t("格式「供应商:模型名」，如 deepseek:deepseek-v4-pro。已内置集成：openai / anthropic / deepseek / google_genai。");
    if (isCompat) warn.style.display = "none";
    else checkOfficial();
  };

  block.querySelectorAll('input[type="radio"]').forEach(r => r.addEventListener("change", () => setMode(r.value)));
  llmInput.addEventListener("input", () => { if (mode === "official") checkOfficial(); });
  llmInput.addEventListener("blur", () => { if (mode === "official") checkOfficial(); });
  block.querySelectorAll("[data-opt]").forEach(cb => cb.addEventListener("change", () => {
    const f = block.querySelector(`[data-optfield="${cb.dataset.opt}"]`);
    if (f) f.style.display = cb.checked ? "" : "none";
  }));
}

function collectModelCfg(root) {
  const block = root.querySelector("[data-model-block]");
  if (!block) return { provider_mode: "official", base_url: "" };
  const mode = block.querySelector('input[type="radio"]:checked').value;
  const out = {
    provider_mode: mode,
    base_url: mode === "openai_compatible" ? (block.querySelector('[data-mf="base_url"]').value || "").trim() : "",
  };
  for (const p of EXTRA_PARAMS) {
    const cb = block.querySelector(`[data-opt="${p.key}"]`);
    if (cb && cb.checked) {
      const raw = (block.querySelector(`[data-mf="${p.key}"]`).value || "").trim();
      const num = parseFloat(raw);
      if (raw !== "" && !Number.isNaN(num)) out[p.key] = p.int ? Math.round(num) : num;
    }
  }
  return out;
}

function buildForm(cfg, canEditSubs) {
  const form = $("#editorForm");
  const pg = cfg.postgres || {};
  const main = cfg.main_agent || {};
  const emb = main.embedding || {};
  const sum = main.summary || {};
  const ft = main.file_tools || {};
  const ns = (pg.store_namespace || []).join(",");
  const isLocal = isLocalPrefix(pg.prefix);
  const gap = tokToUnit(sum.summarize_gap_tokenwise);
  const flush = tokToUnit(sum.flush_history_tokenwise);

  const suffixCtrl = isLocal
    ? `<input id="f_suffix" value="?sslmode=disable" disabled><span class="hint">${t("本地连接，自动使用 sslmode=disable")}</span>`
    : `<select id="f_suffix">${SSL_OPTIONS.map(o => `<option value="${esc(o.v)}" ${(pg.suffix || "") === o.v ? "selected" : ""}>${esc(t(o.label))}</option>`).join("")}</select><span class="hint">${t("云端/企业库请选择 SSL 模式")}</span>`;

  const embInOptions = EMB_OPTIONS.includes(emb.model_name);
  const embSelect = `
    <select id="f_emb_model_sel" style="width:100%;">
      ${EMB_OPTIONS.map(m => `<option value="${esc(m)}" ${m === emb.model_name ? "selected" : ""}>${esc(m)}</option>`).join("")}
      <option value="__custom__" ${!embInOptions ? "selected" : ""}>${t("自定义…")}</option>
    </select>
    <input id="f_emb_model_custom" placeholder="${t("自定义模型名")}" value="${embInOptions ? "" : esc(emb.model_name)}" style="${embInOptions ? "display:none;" : ""} margin-top:6px;">`;

  const hfEndpoint = emb.hf_endpoint || "";
  const hfInOptions = HF_OPTIONS.some(o => o.v === hfEndpoint);
  const hfSelect = `
    <select id="f_hf_endpoint_sel" style="width:100%;">
      ${HF_OPTIONS.map(o => `<option value="${esc(o.v)}" ${hfEndpoint === o.v ? "selected" : ""}>${esc(t(o.label))}</option>`).join("")}
      <option value="__custom__" ${!hfInOptions ? "selected" : ""}>${t("自定义…")}</option>
    </select>
    <input id="f_hf_endpoint_custom" placeholder="${t("自定义镜像地址")}" value="${hfInOptions ? "" : esc(hfEndpoint)}" style="${hfInOptions ? "display:none;" : ""} margin-top:6px;">`;

  const htmlReport = main.html_report;
  const htmlPrompt = main.html_report_prompt || DEFAULT_HTML_PROMPT;

  form.innerHTML = `
    <div class="form-card"><h4>${t("基本信息")}</h4><div class="form-grid">
      <div class="field"><label>agent_id ${info("唯一标识，用作配置文件名 configs/&lt;id&gt;.json，创建后不可改。")}</label><input id="f_agent_id" value="${esc(cfg.agent_id)}" ${canEditSubs ? "" : "disabled"}${ph("agent_id")}></div>
      <div class="field"><label>${t("名称")} name ${info("显示名称，创建后不可改（与历史会话绑定）。")}</label><input id="f_name" value="${esc(cfg.name)}" ${canEditSubs ? "" : "disabled"}${ph("name")}><span class="lock-hint">${canEditSubs ? "" : t("创建后不可改")}</span></div>
      <div class="field"><label>${t("checkpoint 数据库（会话历史绑定）")} ${info("会话历史归属的库名，需先在 pgAdmin 建库，创建后不可改。")}</label><input id="f_cpdb" value="${esc(pg.checkpoint_database)}" ${canEditSubs ? "" : "disabled"}${ph("checkpoint_database")}><span class="lock-hint">${canEditSubs ? t("需先在 pgAdmin 建库") : t("创建后不可改")}</span></div>
      <div class="field full"><details class="db-help"><summary>${t("📖 不会建数据库？点这里看步骤")}</summary><ol><li>${t("打开")} <b>pgAdmin 4</b>${t("（在开始菜单里搜索「pgAdmin」）。")}</li><li>${t("左侧展开")} <b>Servers → PostgreSQL</b>${t("，双击连接，输入安装 PostgreSQL 时设置的密码。")}</li><li>${t("右键")} <b>Databases → Create → Database…</b>${t("。")}</li><li>${t("在")} <b>Database</b> ${t("一栏填库名（与上面填的 checkpoint 库名保持一致）。")}</li><li>${t("点")} <b>Save</b>${t("。")}</li><li>${t("记忆库（store 数据库）还需启用 pgvector：选中刚建的库 → 点上方「Query Tool」图标 → 粘贴下面这句 → 点执行（或按 F5）：")}<pre>CREATE EXTENSION IF NOT EXISTS vector;</pre></li><li>${t("回到本页，点「保存」。")}</li></ol></details></div>
      <div class="field"><label>${t("store 数据库")} ${info("长期记忆存储的库名，可与 checkpoint 库相同或不同。")}</label><input id="f_sdb" value="${esc(pg.store_database)}"></div>
      <div class="field"><label>${t("store_namespace（逗号分隔）")} ${info("记忆存储命名空间，逗号分隔多个层级。")}</label><input id="f_ns" value="${esc(ns)}"></div>
      <div class="field full"><label>${t("连接前缀")} prefix <i class="info-icon">!<span class="tip">${t("格式：postgresql://用户名:密码@主机:端口/<br/>例如 postgresql://user:passwd@localhost:5432/<br/><br/>下面会实时显示完整连接串。程序会根据主机自动判断是否本地回环。")}</span></i></label><input id="f_prefix" value="${esc(pg.prefix)}"${ph("prefix")}></div>
      <div class="field"><label>${t("连接后缀")} suffix <i class="info-icon">!<span class="tip">${t("本地 postgres（localhost/127.0.0.1）自动用 sslmode=disable，省一次 SSL 握手。<br/><br/>云端或企业级 postgres 请选择对应 SSL 模式；「无字符串」表示交由数据库设置决定。")}</span></i></label>${suffixCtrl}</div>
      <div class="field full"><label>${t("完整连接串示例")}</label><div class="conn-example" id="connExample"></div></div>
    </div></div>

    <div class="form-card"><h4>${t("主 agent")}</h4><div class="form-grid">
      ${modelBlockHTML(main.llm_provider_name, main.model, "main_mode")}
      <div class="field"><label>API Key ${info("该模型供应商的 API 密钥（明文存本地配置）。")}</label><input id="f_apikey" value="${esc(main.api_key)}" type="password"${ph("main_api_key")}></div>
      <div class="field"><label>${t("文件工具根目录")} ${info("主 agent 文件工具读写文件的根目录。")}</label><input id="f_rootdir" value="${esc(ft.root_dir)}"${ph("root_dir")}></div>
      <div class="field"><label>${t("embedding 模型")} <i class="info-icon">!<span class="tip">${t("无需提前下载，首次配置会自动下载（需连接 Hugging Face Hub，国内网络可能连不上）。若已离线缓存过，可在下方缓存目录直接使用。")}</span></i></label>${embSelect}</div>
      <div class="field"><label>${t("embedding 缓存目录")} ${info("本地模型缓存路径，留空用 Hugging Face 默认缓存。")}</label><input id="f_emb_cache" value="${esc(emb.cache_folder)}"${ph("emb_cache")}></div>
      <div class="field"><label>${t("embedding 维度")} ${info("向量维度；bge-m3 为 1024，换模型需对应调整。")}</label><input id="f_emb_dims" value="${esc(emb.dims)}" type="number"></div>
      <div class="field"><label>${t("embedding 镜像")} ${info("下载 embedding 模型时使用的 HuggingFace 镜像源；国内推荐 hf-mirror。")}</label>${hfSelect}</div>
      <div class="field full">
        <label style="flex-direction:row;align-items:center;gap:8px;"><input type="checkbox" id="f_emb_offline" ${emb.local_files_only ? "checked" : ""}> ${t("Embedding 模型离线模式")} ${info("离线模式 = 只从本地缓存加载 embedding 模型、不联网校验。没有缓存时请勿开启（会报错）；已有缓存时建议开启，跳过每次联网校验。")}</label>
        <span class="hint">${t("没有缓存时请勿开启；已有缓存时建议开启，跳过每次联网校验。")}</span>
      </div>
      ${unitField("f_sum_gap", gap.v, gap.u, t("阶段性总结阈值"), "累计 token 达到该值时触发一次阶段性总结。")}
      ${unitField("f_sum_flush", flush.v, flush.u, t("清空历史阈值"), "累计 token 达到该值时清空历史（只保留最近几轮）。")}
      <div class="field"><label>${t("清空时保留轮数")} ${info("清空历史时保留最近几轮对话。")}</label><input id="f_sum_reserve" value="${esc(sum.reserve_message_round)}" type="number"></div>
      <div class="field full"><label>System Prompt ${info("主 agent 的系统提示词，定义其角色与行为。")}</label><textarea id="f_prompt" rows="8"${ph("main_system_prompt")}>${esc(main.system_prompt)}</textarea></div>
      <div class="field full" style="border-top:1px solid var(--border);padding-top:14px;">
        <label style="flex-direction:row;align-items:center;gap:8px;"><input type="checkbox" id="f_html_report" ${htmlReport ? "checked" : ""}> ${t("启用 HTML 报告")} ${info("主 agent 输出完后，询问是否将结果生成 HTML 报告。")}</label>
      </div>
      <div class="field full"><label>${t("HTML 报告生成 prompt")} ${info("确认生成后注入给主 agent 的提示词（可恢复默认）。")} <button class="btn small" id="resetHtmlBtn" type="button">${t("恢复默认")}</button></label><textarea id="f_html_prompt" rows="3">${esc(htmlPrompt)}</textarea></div>
    </div></div>

    <div class="form-card"><h4>${t("子 agent")}（${canEditSubs ? t("可增删") : t("创建后不可增删改名")}）</h4>
      <div id="subs"></div>
      ${canEditSubs ? `<button class="btn small" id="addSubBtn" type="button">+ ${t("添加子 agent")}</button>` : ""}
    </div>`;

  // 连接示例实时更新（脱敏 + 释义）
  const refreshExample = () => {
    const p = maskedPrefix($("#f_prefix").value);
    const db = $("#f_cpdb").value || t("<数据库名>");
    const suffix = $("#f_suffix").value || "";
    $("#connExample").innerHTML = esc(p + db + suffix) +
      `<div class="muted" style="margin-top:6px;font-size:11px;">${t("组成：")}<code>postgresql://</code> ${t("协议")} · <code>${t("用户名:passwd")}</code> ${t("登录凭据")} · <code>@${t("主机:port")}</code> ${t("数据库地址")} · <code>/${t("数据库名")}</code> ${t("库名")} · <code>?sslmode</code> ${t("SSL 模式")}</div>`;
  };
  ["f_prefix", "f_cpdb", "f_suffix", "f_sdb"].forEach(id => { const el = $("#" + id); if (el) el.addEventListener("input", refreshExample); });
  refreshExample();

  // embedding 下拉 ↔ 自定义
  const embSel = $("#f_emb_model_sel"), embCustom = $("#f_emb_model_custom");
  embSel.onchange = () => { embCustom.style.display = embSel.value === "__custom__" ? "" : "none"; };

  // 镜像下拉 ↔ 自定义
  const hfSel = $("#f_hf_endpoint_sel"), hfCustom = $("#f_hf_endpoint_custom");
  hfSel.onchange = () => { hfCustom.style.display = hfSel.value === "__custom__" ? "" : "none"; };

  // 离线模式开关：关闭时提醒
  const embOffline = $("#f_emb_offline");
  embOffline.onchange = (e) => {
    if (!e.target.checked) {
      toast(t("已关闭 Embedding 模型离线模式：每次打开会联网校验。若已获取模型缓存，建议重新开启以跳过校验。"), true);
    }
  };

  $("#resetHtmlBtn").onclick = () => { $("#f_html_prompt").value = DEFAULT_HTML_PROMPT; };

  const subsBox = $("#subs");
  (cfg.sub_agents || []).forEach((s, i) => subsBox.appendChild(subAgentBox(s, i, canEditSubs)));
  const addBtn = $("#addSubBtn");
  if (addBtn) addBtn.onclick = () => subsBox.appendChild(subAgentBox(null, Date.now(), true));

  bindModelBlock(form);
}

function subAgentBox(s, key, canEdit) {
  s = s || { name: "", description: "", system_prompt: "", api_key: "", llm_provider_name: "deepseek:deepseek-v4-pro", mcp_servers: [], summary: { flush_history_tokenwise: 200000, reserve_message_round: 4 } };
  const flush = tokToUnit((s.summary || {}).flush_history_tokenwise);
  const div = document.createElement("div");
  div.className = "subagent-box";
  div.dataset.key = key;
  div.innerHTML = `
    <div class="sub-head"><span class="name">🧩 ${t("子 agent")}</span>${canEdit ? `<button class="btn danger small" data-act="remove" type="button">${t("移除")}</button>` : ""}</div>
    <div class="form-grid">
      <div class="field"><label>${t("名称")} name ${info("子 agent 标识，会作为工具名呈现给主 agent，创建后不可改。")}</label><input data-f="name" value="${esc(s.name)}" ${canEdit ? "" : "disabled"}${ph("sub_name")}></div>
      ${modelBlockHTML(s.llm_provider_name, s.model, `sub_${key}_mode`)}
      <div class="field"><label>API Key ${info("该子 agent 所用模型的 API 密钥（明文存本地配置）。")}</label><input data-f="api_key" value="${esc(s.api_key)}" type="password"${ph("sub_api_key")}></div>
      <div class="field"><label>${t("清空历史阈值")} ${info("该子 agent 累计 token 达到该值时清空历史（只保留最近几轮）。")}</label><div class="unit-row"><input data-f="flush" value="${esc(flush.v)}" type="number"><select data-f="flush_unit"><option value="万" ${flush.u === "万" ? "selected" : ""}>${t("万")}</option><option value="千" ${flush.u === "千" ? "selected" : ""}>${t("千")}</option></select></div></div>
      <div class="field"><label>${t("保留轮数")} ${info("清空历史时保留最近几轮对话。")}</label><input data-f="reserve" value="${esc((s.summary || {}).reserve_message_round)}" type="number"></div>
      <div class="field full"><label>Description ${info("描述该子 agent 能力，作为工具描述呈现给主 agent。")}</label><textarea data-f="description" rows="3"${ph("sub_description")}>${esc(s.description)}</textarea></div>
      <div class="field full"><label>System Prompt ${info("该子 agent 的系统提示词。")}</label><textarea data-f="system_prompt" rows="6"${ph("sub_system_prompt")}>${esc(s.system_prompt)}</textarea></div>
      <div class="field full"><label>${t("MCP 服务器")} ${info("该子 agent 可用的工具来源；程序不负责启动，需外部自行启动。")}</label><div data-f="mcp"></div></div>
    </div>`;

  div.querySelector('[data-act="remove"]')?.addEventListener("click", () => div.remove());

  const mcpBox = div.querySelector('[data-f="mcp"]');
  (s.mcp_servers || []).forEach((m, j) => mcpBox.appendChild(mcpRow(m, j)));
  const addM = document.createElement("button");
  addM.className = "btn small add-mcp"; addM.type = "button"; addM.textContent = "+ " + t("添加 MCP");
  addM.onclick = () => mcpBox.insertBefore(mcpRow(null, Date.now()), addM);
  mcpBox.appendChild(addM);

  bindModelBlock(div);
  return div;
}

function mcpRow(m, key) {
  m = m || { name: "", transport: "http", url: "", command: "", args: [] };
  const div = document.createElement("div");
  div.className = "mcp-row";
  div.innerHTML = `
    <span class="dot gray" data-dot title="${t("点击检测健康状态")}"></span>
    <input data-m="name" placeholder="${t("名称")}" value="${esc(m.name)}" style="flex:0.8">
    <select data-m="transport" style="flex:0.6">
      <option value="http" ${m.transport === "http" ? "selected" : ""}>http</option>
      <option value="stdio" ${m.transport === "stdio" ? "selected" : ""}>stdio</option>
    </select>
    <input data-m="url" placeholder="${t("URL（http）")}" value="${esc(m.url || "")}">
    <input data-m="command" placeholder="${t("命令（stdio）")}" value="${esc(m.command || "")}">
    <input data-m="args" placeholder="${t("参数，逗号分隔（stdio）")}" value="${esc((m.args || []).join(","))}">
    <button class="btn danger small" data-act="rm" type="button">×</button>`;
  div.querySelector('[data-act="rm"]').onclick = () => div.remove();

  const dot = div.querySelector("[data-dot]");
  const refresh = async () => {
    dot.className = "dot gray";
    const t = div.querySelector('[data-m="transport"]').value;
    const ok = await checkMcp(t, div.querySelector('[data-m="url"]').value, div.querySelector('[data-m="command"]').value);
    dot.className = "dot " + (ok ? "green" : "red");
  };
  dot.onclick = refresh;
  refresh();
  return div;
}

function collectSubAgent(box) {
  const get = f => box.querySelector(`[data-f="${f}"]`).value;
  const mcp = $$(".mcp-row", box).map(r => ({
    name: r.querySelector('[data-m="name"]').value,
    transport: r.querySelector('[data-m="transport"]').value,
    url: r.querySelector('[data-m="url"]').value || null,
    command: r.querySelector('[data-m="command"]').value || null,
    args: r.querySelector('[data-m="args"]').value ? r.querySelector('[data-m="args"]').value.split(",").map(x => x.trim()).filter(Boolean) : [],
  }));
  return {
    name: get("name"),
    description: get("description"),
    system_prompt: get("system_prompt"),
    api_key: get("api_key"),
    llm_provider_name: box.querySelector('[data-mf="llm_provider_name"]').value,
    model: collectModelCfg(box),
    mcp_servers: mcp,
    summary: {
      flush_history_tokenwise: unitToTok(box.querySelector('[data-f="flush"]').value, box.querySelector('[data-f="flush_unit"]').value),
      reserve_message_round: +get("reserve"),
    },
  };
}

function embModelValue() {
  const sel = $("#f_emb_model_sel").value;
  return sel === "__custom__" ? $("#f_emb_model_custom").value : sel;
}

function hfEndpointValue() {
  const sel = $("#f_hf_endpoint_sel").value;
  return sel === "__custom__" ? $("#f_hf_endpoint_custom").value : sel;
}

function buildPayload(cfg) {
  const val = id => $(`#${id}`).value;
  return {
    agent_id: val("f_agent_id") || slugify(val("f_name")),
    name: val("f_name"),
    postgres: {
      prefix: val("f_prefix"),
      suffix: val("f_suffix"),
      store_database: val("f_sdb"),
      checkpoint_database: val("f_cpdb"),
      store_namespace: val("f_ns").split(",").map(x => x.trim()).filter(Boolean),
    },
    main_agent: {
      system_prompt: val("f_prompt"),
      api_key: val("f_apikey"),
      llm_provider_name: $("#editorForm").querySelector('[data-mf="llm_provider_name"]').value,
      model: collectModelCfg($("#editorForm")),
      file_tools: { root_dir: val("f_rootdir") },
      embedding: {
        model_name: embModelValue(),
        cache_folder: val("f_emb_cache"),
        dims: +val("f_emb_dims"),
        device: (cfg.main_agent.embedding || {}).device || "cpu",
        encode_normalize: (cfg.main_agent.embedding || {}).encode_normalize ?? true,
        local_files_only: $("#f_emb_offline").checked,
        hf_endpoint: hfEndpointValue(),
      },
      summary: {
        summarize_gap_tokenwise: unitToTok(val("f_sum_gap"), val("f_sum_gap_unit")),
        flush_history_tokenwise: unitToTok(val("f_sum_flush"), val("f_sum_flush_unit")),
        reserve_message_round: +val("f_sum_reserve"),
      },
      html_report: $("#f_html_report").checked,
      html_report_prompt: val("f_html_prompt"),
    },
    sub_agents: $$(".subagent-box").map(collectSubAgent),
    output: cfg.output || { stream_output_dir: "" },
  };
}

async function saveConfig(cfg, isNew, isDefault) {
  try {
    const payload = buildPayload(cfg);
    if (!payload.name) return toast(t("请填写名称"), true);
    if (!payload.postgres.checkpoint_database) return toast(t("请填写 checkpoint 数据库名"), true);
    if (payload.sub_agents.some(s => !(s.name || "").trim())) return toast(t("子 agent 名称不能为空"), true);

    if (isDefault) { await api("/api/default", { method: "PUT", body: JSON.stringify(payload) }); toast(t("默认配置已保存")); goAgents(); }
    else if (isNew) { await api("/api/agents", { method: "POST", body: JSON.stringify(payload) }); toast(t("已创建")); goAgents(); }
    else { payload.agent_id = S.agentId; await api("/api/agents/" + encodeURIComponent(S.agentId), { method: "PUT", body: JSON.stringify(payload) }); toast(t("已保存")); goAgents(); }
  } catch (e) { toast(e.message, true); }
}

/* ================= 视图3：Thread 列表 ================= */
async function renderThreadsView() {
  app.innerHTML = topbar("返回", () => goAgents());
  const view = document.createElement("div");
  view.className = "view";
  view.style.display = "flex";
  view.style.flexDirection = "column";
  app.appendChild(view);
  bindBack(() => goAgents());
  view.innerHTML = `
    <div style="display:flex;align-items:center;margin-bottom:20px;gap:12px;">
      <h2 class="section-title" style="margin:0;">${t("会话")} — ${esc(S.agentName)}</h2>
      <div class="spacer" style="flex:1;"></div>
      <button class="btn primary" id="newThreadBtn">+ ${t("新建会话")}</button>
    </div>
    <div id="threadsBox" style="flex:1;overflow:auto;min-height:0;"><div class="muted">${t("加载中…")}</div></div>
    <div class="threads-footer" id="threadsFooter" style="display:none;">
      <label class="toggle"><input type="checkbox" id="showHiddenChk"><span class="track"></span></label>
      <span class="sw-label" id="showHiddenLabel"></span>
    </div>`;
  $("#newThreadBtn").onclick = () => { const n = prompt(t("新会话名称：")); if (n && n.trim()) goChat(S.agentId, S.agentName, n.trim()); };

  let threads;
  try { threads = await api(`/api/agents/${encodeURIComponent(S.agentId)}/threads`); }
  catch (e) { $("#threadsBox").innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }

  const hiddenKey = "hidden_threads_" + S.agentId;
  const showKey = "show_hidden_" + S.agentId;
  const hiddenSet = new Set(JSON.parse(localStorage.getItem(hiddenKey) || "[]"));
  const showHidden = localStorage.getItem(showKey) === "1";
  const hiddenCount = threads.filter(t => hiddenSet.has(t.thread_id)).length;
  const visible = showHidden ? threads : threads.filter(t => !hiddenSet.has(t.thread_id));

  // footer：有会话时始终显示（即使全部隐藏），让用户能切换「显示隐藏对话」
  if (threads.length) {
    $("#showHiddenChk").checked = showHidden;
    $("#showHiddenLabel").textContent = t("显示隐藏对话") + (hiddenCount ? `（${hiddenCount}）` : "");
    $("#threadsFooter").style.display = "flex";
  }

  if (!visible.length) {
    const msg = hiddenCount ? t("所有会话均已隐藏") : t("暂无会话");
    $("#threadsBox").innerHTML = `<div class="empty"><div class="big">🧵</div>${msg}<br/><br/><button class="btn primary" id="nt2">+ ${t("新建会话")}</button></div>`;
    const n = $("#nt2"); if (n) n.onclick = () => { const nm = prompt(t("新会话名称：")); if (nm && nm.trim()) goChat(S.agentId, S.agentName, nm.trim()); };
  } else {
    $("#threadsBox").innerHTML = `
      <div style="background:var(--surface);border-radius:var(--radius);overflow:hidden;box-shadow:var(--shadow);">
        ${visible.map(th => {
          const hidden = hiddenSet.has(th.thread_id);
          return `
          <div class="thread-row ${hidden ? "thread-hidden" : ""}" data-tid="${esc(th.thread_id)}">
            <span class="tid">${esc(th.thread_id)}</span>
            <span class="meta"><small>${th.checkpoints} checkpoints</small><small>${esc(th.last_updated || "")}</small>${hidden ? `<small class="hidden-tag">${t("已隐藏")}</small>` : ""}</span>
            <button class="btn hide small" data-hide="${esc(th.thread_id)}">${hidden ? t("取消隐藏") : t("隐藏")}</button>
            <button class="btn danger small" data-del="${esc(th.thread_id)}">${t("删除")}</button>
          </div>`;
        }).join("")}
      </div>`;

    $$("#threadsBox .thread-row").forEach(row => {
      const tid = row.dataset.tid;
      row.onclick = () => goChat(S.agentId, S.agentName, tid);
      row.querySelector("[data-del]").onclick = (e) => { e.stopPropagation(); deleteThread(tid); };
      row.querySelector("[data-hide]").onclick = (e) => {
        e.stopPropagation();
        if (hiddenSet.has(tid)) hiddenSet.delete(tid);
        else hiddenSet.add(tid);
        localStorage.setItem(hiddenKey, JSON.stringify([...hiddenSet]));
        renderThreadsView();
      };
    });
  }

  $("#showHiddenChk").onchange = (e) => { localStorage.setItem(showKey, e.target.checked ? "1" : "0"); renderThreadsView(); };
}
async function deleteThread(tid) {
  if (!confirm(`${t("确定删除会话")}「${tid}」？${t("此操作不可撤销。")}`)) return;
  try { await api(`/api/agents/${encodeURIComponent(S.agentId)}/threads/${encodeURIComponent(tid)}`, { method: "DELETE" }); toast(t("已删除")); renderThreadsView(); }
  catch (e) { toast(e.message, true); }
}

/* ================= 视图4：聊天 ================= */
function setStatusIndicator(mode) {
  const ind = $("#statusInd");
  if (!ind) return;
  if (mode === "thinking") { ind.className = "status-indicator thinking"; ind.innerHTML = `<span class="status-dot thinking"></span>${t("Agent 思考中…")}`; }
  else if (mode === "answering") { ind.className = "status-indicator answering"; ind.innerHTML = `<span class="status-dot answering"></span>${t("Agent 回答中…")}`; }
  else { ind.className = "status-indicator"; ind.innerHTML = ""; }
}

function toggleMsgDrawer(e) {
  const existing = $(".msg-drawer-panel");
  if (existing) { existing.remove(); return; }
  const btn = e.currentTarget;
  const panel = document.createElement("div");
  panel.className = "msg-drawer-panel";
  const blocks = $$("#historyMd .user-msg-block");
  const aiBlocks = $$("#historyMd .ai-msg-block");
  if (!blocks.length) {
    panel.innerHTML = `<div class="muted" style="padding:8px;">${t("暂无用户消息")}</div>`;
  } else {
    // 每个 AI 回复块归属到它前面的那个用户消息
    const aiOwner = new Array(aiBlocks.length).fill(-1);
    let ui = -1;
    aiBlocks.forEach((ai, k) => {
      while (ui + 1 < blocks.length && (blocks[ui + 1].compareDocumentPosition(ai) & Node.DOCUMENT_POSITION_FOLLOWING)) ui++;
      aiOwner[k] = ui;
    });

    const headingMap = {};
    const items = blocks.map((b, i) => {
      const summary = b.dataset.summary || "";
      const raw = summary || (b.querySelector(".user-msg-quote")?.innerText || "");
      const first = raw.split("\n").map(s => s.trim()).filter(Boolean)[0] || "";
      const label = first.length > 24 ? first.slice(0, 24) + "…" : first;

      const headings = [];
      aiBlocks.forEach((ai, k) => {
        if (aiOwner[k] !== i) return;
        ai.querySelectorAll("h1, h2, h3, h4, h5, h6").forEach(h => headings.push(h));
      });
      let topLevel = 0;
      headings.forEach(h => {
        const lv = parseInt(h.tagName.slice(1), 10);
        if (!topLevel || lv < topLevel) topLevel = lv;
      });
      const topHeadings = topLevel ? headings.filter(h => parseInt(h.tagName.slice(1), 10) === topLevel) : [];
      if (topHeadings.length) headingMap[i] = topHeadings;
      return { i, label: label || t("（空消息）"), expandable: topHeadings.length > 0 };
    });

    panel.innerHTML = items.map(({ i, label, expandable }) => {
      const caret = expandable
        ? `<span class="msg-drawer-caret" data-caret="${i}">▸</span>`
        : `<span class="msg-drawer-caret msg-drawer-caret-empty"></span>`;
      const labelHtml = `<span class="msg-drawer-label">${esc(label)}</span>`;
      const subHtml = expandable
        ? `<div class="msg-drawer-sub" data-sub="${i}" style="display:none;">${headingMap[i].map((h, j) => {
            const txt = (h.innerText || "").split("\n").map(s => s.trim()).filter(Boolean)[0] || "";
            const short = txt.length > 28 ? txt.slice(0, 28) + "…" : txt;
            return `<div class="msg-drawer-sub-item" data-i="${i}" data-h="${j}">${esc(short)}</div>`;
          }).join("")}</div>`
        : "";
      return `<div class="msg-drawer-item" data-i="${i}">${caret}${labelHtml}</div>${subHtml}`;
    }).join("");

    panel.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const caret = ev.target.closest(".msg-drawer-caret");
      if (caret && caret.dataset.caret != null) {
        const sub = panel.querySelector(`.msg-drawer-sub[data-sub="${caret.dataset.caret}"]`);
        if (sub) {
          const open = sub.style.display !== "none";
          sub.style.display = open ? "none" : "";
          caret.textContent = open ? "▸" : "▾";
        }
        return;
      }
      const subItem = ev.target.closest(".msg-drawer-sub-item");
      if (subItem) {
        const el = headingMap[+subItem.dataset.i]?.[+subItem.dataset.h];
        if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
        panel.remove();
        return;
      }
      const item = ev.target.closest(".msg-drawer-item");
      if (item) {
        const el = blocks[+item.dataset.i];
        if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
        panel.remove();
      }
    });
  }
  btn.appendChild(panel);
}

async function renderChatView() {
  app.innerHTML = topbar(t("返回会话"), () => goThreads(S.agentId, S.agentName));
  const view = document.createElement("div");
  view.className = "chat-view";
  app.appendChild(view);
  bindBack(() => goThreads(S.agentId, S.agentName));

  view.innerHTML = `
    <div class="chat-head">
      <span class="thread-name">🧵 ${esc(S.threadId)}</span>
      <span class="muted">（${esc(S.agentName)}）</span>
      <div class="spacer" style="flex:1;"></div>
      <span class="status-indicator" id="statusInd"></span>
      <div class="zoom-controls">
        <span class="zoom-btn" id="rZoomOut" title="${t("思考字号减小")}">−</span>
        <span class="zoom-label" id="rZoomLabel" title="${t("思考字号（相对正文）")}">🧠</span>
        <span class="zoom-btn" id="rZoomIn" title="${t("思考字号增大")}">+</span>
        <span class="zoom-pct" id="rZoomPct"></span>
      </div>
      <div class="zoom-controls">
        <span class="zoom-btn" id="zoomOut" title="${t("正文字号减小")}">−</span>
        <span class="zoom-label" id="zoomLabel" title="${t("正文字号")}">Aa</span>
        <span class="zoom-btn" id="zoomIn" title="${t("正文字号增大")}">+</span>
        <span class="zoom-pct" id="zoomPct"></span>
      </div>
      <button class="btn small" id="msgDirBtn" style="position:relative;">☰ ${t("消息目录")}</button>
      <select id="subgraphSel" class="btn small" style="max-width:220px;"><option value="">${t("主会话")}</option></select>
    </div>
    <div class="chat-body" id="chatBody">
      <div class="history-pane" id="historyPane"><div class="md-body markdown-body" id="historyMd"><div class="muted">${t("加载历史中…")}</div></div></div>
      <button id="pinBtn" class="pin-btn" title="${t("跟随最新输出 · 按住可拖动")}">📌</button>
      <div class="input-pane" id="inputPane">
        <div class="input-toolbar"><span class="muted" id="inputHint">${t("输入消息（Enter 发送，Shift+Enter 换行）")}</span><div class="spacer" style="flex:1;"></div></div>
        <textarea id="msgInput" placeholder="${t("输入消息…")}"></textarea>
        <div class="input-actions"><button class="btn" id="stopBtn" style="display:none;">${t("停止")}</button><button class="btn primary" id="sendBtn">${t("发送")}</button></div>
      </div>
      <div id="doneBubble" class="done-bubble done-bubble-float" style="display:none;"></div>
    </div>`;

  const chatBody = $("#chatBody");
  const pinBtn = $("#pinBtn");

  const defaultPinPos = () => {
    const inputPane = $("#inputPane");
    if (!chatBody || !pinBtn) return { left: 0, top: 0 };
    const bw = pinBtn.offsetWidth || 34, bh = pinBtn.offsetHeight || 34;
    const inp = inputPane ? inputPane.offsetHeight : 0;
    return { left: chatBody.clientWidth - bw - 28, top: chatBody.clientHeight - inp - bh - 24 };
  };
  const applyPinPos = (pos) => {
    pinBtn.style.left = pos.left + "px";
    pinBtn.style.top = pos.top + "px";
    pinBtn.style.right = "auto";
    pinBtn.style.bottom = "auto";
  };

  let customPos = null;
  try { customPos = JSON.parse(localStorage.getItem("pin-pos") || "null"); } catch (e) {}
  if (customPos && typeof customPos.left === "number") applyPinPos(customPos);
  else applyPinPos(defaultPinPos());

  if (window.Split) {
    Split(["#historyPane", "#inputPane"], {
      direction: "vertical", sizes: [72, 28], minSize: [100, 80], gutterSize: 8, cursor: "row-resize",
      onDragEnd: () => { if (!customPos) applyPinPos(defaultPinPos()); },
    });
  }

  applyZoom();
  applyReasoningZoom();
  let settings = null;
  try { settings = await getSettings(); } catch (e) {}
  if (settings) {
    const keyLabel = k => ({ enter: "Enter", shift_enter: "Shift+Enter", ctrl_enter: "Ctrl+Enter" })[k] || k;
    const hint = $("#inputHint");
    if (hint) {
      hint.textContent = settings.send_key === "mouse_only"
        ? t("输入消息（点击发送，Enter 换行）")
        : `${t("输入消息（")}${keyLabel(settings.send_key)} ${t("发送，")}${keyLabel(settings.newline_key)} ${t("换行）")}`;
    }
  }
  const zoomPctEl = $("#zoomPct");
  const updateZoomLabel = () => { if (zoomPctEl) zoomPctEl.textContent = zoomPct + "%"; };
  updateZoomLabel();
  $("#zoomOut").onclick = () => { changeZoom(-10); updateZoomLabel(); };
  $("#zoomIn").onclick = () => { changeZoom(10); updateZoomLabel(); };
  const rZoomPctEl = $("#rZoomPct");
  const updateReasoningZoomLabel = () => { if (rZoomPctEl) rZoomPctEl.textContent = reasoningZoomPct + "%"; };
  updateReasoningZoomLabel();
  $("#rZoomOut").onclick = () => { changeReasoningZoom(-10); updateReasoningZoomLabel(); };
  $("#rZoomIn").onclick = () => { changeReasoningZoom(10); updateReasoningZoomLabel(); };
  $("#msgDirBtn").onclick = (e) => toggleMsgDrawer(e);

  const updatePinBtn = () => { pinBtn.classList.toggle("active", pinned); };
  updatePinBtn();

  let dragState = null, dragged = false;

  pinBtn.onclick = () => {
    if (dragged) { dragged = false; return; }
    pinned = !pinned;
    localStorage.setItem("pin-follow", pinned ? "1" : "0");
    updatePinBtn();
    if (pinned) { const pane = $("#historyPane"); if (pane) pane.scrollTop = pane.scrollHeight; }
  };

  // 按住拖动移动按钮
  pinBtn.addEventListener("mousedown", (e) => {
    dragState = { x: e.clientX, y: e.clientY, left: pinBtn.offsetLeft, top: pinBtn.offsetTop };
    dragged = false;
    e.preventDefault();
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragState) return;
    const dx = e.clientX - dragState.x, dy = e.clientY - dragState.y;
    if (!dragged && (Math.abs(dx) > 3 || Math.abs(dy) > 3)) dragged = true;
    if (!dragged) return;
    const bw = pinBtn.offsetWidth, bh = pinBtn.offsetHeight;
    pinBtn.style.left = Math.min(Math.max(0, dragState.left + dx), chatBody.clientWidth - bw) + "px";
    pinBtn.style.top = Math.min(Math.max(0, dragState.top + dy), chatBody.clientHeight - bh) + "px";
  });
  window.addEventListener("mouseup", () => {
    if (dragState) {
      if (dragged) {
        customPos = { left: pinBtn.offsetLeft, top: pinBtn.offsetTop };
        localStorage.setItem("pin-pos", JSON.stringify(customPos));
      }
      dragState = null;
    }
  });

  const historyEl = $("#historyMd");
  const scrollToLastUserMsg = () => {
    const blocks = historyEl.querySelectorAll(".user-msg-block");
    if (blocks.length) {
      blocks[blocks.length - 1].scrollIntoView({ block: "start" });
      return;
    }
    const pane = $("#historyPane");
    const stats = historyEl.querySelector("#stats-anchor");
    if (!stats || !pane) return;
    const pr = pane.getBoundingClientRect();
    const sr = stats.getBoundingClientRect();
    pane.scrollTop = Math.max(0, pane.scrollTop + (sr.top - pr.top) - pane.clientHeight);
  };
  try {
    const h = await api(`/api/agents/${encodeURIComponent(S.agentId)}/threads/${encodeURIComponent(S.threadId)}/history`);
    historyEl.innerHTML = renderMd(h.markdown);
    scrollToLastUserMsg();
  } catch (e) { historyEl.innerHTML = `<div class="muted">${t("（无历史）")}</div>`; }

  const sel = $("#subgraphSel");
  try {
    const subs = await api(`/api/agents/${encodeURIComponent(S.agentId)}/threads/${encodeURIComponent(S.threadId)}/subgraphs`);
    subs.forEach(s => { const o = document.createElement("option"); o.value = s.node_name; o.textContent = s.node_name; sel.appendChild(o); });
  } catch (e) { /* 无子图 */ }

  const input = $("#msgInput"), sendBtn = $("#sendBtn"), stopBtn = $("#stopBtn");

  function updateSendState() {
    sendBtn.disabled = isRunning || !!sel.value;
  }

  sel.onchange = async () => {
    updateSendState();
    if (!sel.value) { renderChatView(); return; }
    try {
      const h = await api(`/api/agents/${encodeURIComponent(S.agentId)}/threads/${encodeURIComponent(S.threadId)}/subgraphs/${encodeURIComponent(sel.value)}/history`);
      $("#historyMd").innerHTML = renderMd(h.markdown);
    } catch (e) { toast(e.message, true); }
  };

  function setRunning(r) {
    isRunning = r;
    sel.disabled = r;
    updateSendState();
    if (r) setStatusIndicator("thinking"); else setStatusIndicator("idle");
    if (r) stopBtn.style.display = ""; else stopBtn.style.display = "none";
  }

  function showDoneBubble() {
    const b = $("#doneBubble");
    if (!b) return;
    const pane = $("#inputPane");
    b.textContent = t("✅ 回复已完成");
    b.style.bottom = (pane ? pane.offsetHeight + 10 : 160) + "px";
    b.style.display = "inline-flex";
    clearTimeout(b._t);
    b._t = setTimeout(() => { b.style.display = "none"; }, 4000);
    getSettings().then(s => playSound(s.notification_sound)).catch(() => playSound("ber"));
  }

  async function send() {
    const raw = input.value;
    if (!raw.trim() || isRunning) return;
    const content = raw.replace(/\s+$/, "");
    setRunning(true);
    appendReplyHeader();
    const ok = await openChatWs(content);
    setRunning(false);
    if (ok) {
      if (input.value.replace(/\s+$/, "") === content) input.value = "";
      showDoneBubble();
    } else {
      toast(t("发送失败，消息已保留在输入框"), true);
    }
  }

  sendBtn.onclick = send;
  input.onkeydown = e => {
    if (e.key !== "Enter") return;
    const combo = e.ctrlKey ? "ctrl_enter" : (e.shiftKey ? "shift_enter" : "enter");
    const sendKey = (settingsCache && settingsCache.send_key) || "enter";
    if (sendKey !== "mouse_only" && combo === sendKey) {
      e.preventDefault();
      send();
      return;
    }
    // 非发送键一律换行（手动插入，兼容 Ctrl+Enter）
    e.preventDefault();
    const start = input.selectionStart, end = input.selectionEnd;
    input.value = input.value.slice(0, start) + "\n" + input.value.slice(end);
    input.selectionStart = input.selectionEnd = start + 1;
  };
  stopBtn.onclick = () => { if (ws) ws.send(JSON.stringify({ type: "stop" })); };
  updateSendState();
}

function appendReplyHeader() {
  const historyEl = $("#historyMd");
  if (!currentReplyEl || currentReplyEl.parentElement !== historyEl) {
    currentReplyEl = document.createElement("div");
    currentReplyEl.className = "current-reply md-body markdown-body";
    historyEl.appendChild(currentReplyEl);
  }
}

function openChatWs(content) {
  return new Promise((resolve) => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/api/agents/${encodeURIComponent(S.agentId)}/threads/${encodeURIComponent(S.threadId)}/chat`);

    const buffers = { main_user: "", main: [], sub: {} };
    let rafPending = false;
    let answered = false;
    const flush = () => { rafPending = false; renderStream(); };
    const schedule = () => { if (!rafPending) { rafPending = true; requestAnimationFrame(flush); } };

    const pushBlock = (list, type, content) => {
      const last = list[list.length - 1];
      if (last && last.type === type) last.content += content;
      else list.push({ type, content });
    };

    const reasoningBlock = (txt) => txt
      ? `<details class="reasoning-block" open><summary>🧠 ${t("思考过程")}</summary><div class="reasoning-body">${esc(txt)}</div></details>`
      : "";

    function renderStream() {
      if (!currentReplyEl) return;
      const pane = $("#historyPane");
      const atBottom = pane && (pane.scrollHeight - pane.scrollTop - pane.clientHeight < 48);
      let html = "";
      if (buffers.main_user) html += `<div class="user-msg-block"><div class="user-msg-head">🧑 <strong>${t("用户")}</strong></div><blockquote class="user-msg-quote">${esc(buffers.main_user).replace(/\n/g, "<br>")}</blockquote></div>`;
      const renderBlocks = (blocks) => blocks.map(b =>
        b.type === "reasoning"
          ? reasoningBlock(b.content)
          : b.type === "tool_result"
            ? `<div class="tool-result-body">${renderMd(b.content)}</div>`
            : `<div class="ai-msg-block">${renderMd(b.content)}</div>`
      ).join("");
      html += renderBlocks(buffers.main);
      for (const [name, blocks] of Object.entries(buffers.sub)) {
        if (!blocks || !blocks.length) continue;
        html += `<div style="margin-top:12px;"><span class="sub-tag">🧩 ${t("子 agent")} · ${esc(name)}</span>${renderBlocks(blocks)}</div>`;
      }
      currentReplyEl.innerHTML = html;
      if (pane && (pinned || atBottom)) pane.scrollTop = pane.scrollHeight;
    }

    ws.onopen = () => ws.send(JSON.stringify({ type: "send", content }));

    ws.onmessage = async (ev) => {
      const msg = JSON.parse(ev.data);
      switch (msg.type) {
        case "text":
          if (msg.source === "main" && !answered) { answered = true; setStatusIndicator("answering"); }
          if (msg.source === "main_user") buffers.main_user += msg.text;
          else if (msg.source === "main") pushBlock(buffers.main, "text", msg.text);
          else { const n = msg.source.replace(/^sub:/, ""); (buffers.sub[n] = buffers.sub[n] || []); pushBlock(buffers.sub[n], "text", msg.text); }
          schedule();
          break;
        case "reasoning":
          if (msg.source === "main") pushBlock(buffers.main, "reasoning", msg.text);
          else { const n = msg.source.replace(/^sub:/, ""); (buffers.sub[n] = buffers.sub[n] || []); pushBlock(buffers.sub[n], "reasoning", msg.text); }
          schedule();
          break;
        case "tool_call":
          { let tcHtml = `\n\n🔧 **${t("工具调用")}**: \`${esc(msg.name)}\`\n\n`;
          if (msg.args && Object.keys(msg.args).length) tcHtml += "```json\n" + JSON.stringify(msg.args, null, 2) + "\n```\n\n";
          pushBlock(buffers.main, "tool", tcHtml);
          schedule(); }
          break;
        case "tool_result":
          pushBlock(buffers.main, "tool_result", `\n✅ **${t("工具结果")}** (\`${esc(msg.name)}\`):\n\n${esc(msg.content)}\n\n`);
          schedule();
          break;
        case "subgraph_start":
          buffers.sub[msg.name] = buffers.sub[msg.name] || [];
          break;
        case "interrupt": {
          const ans = await askConfirm(msg.prompt || t("请确认"));
          ws.send(JSON.stringify({ type: "resume", value: ans }));
          break;
        }
        case "done":
          resolve(true);
          break;
        case "error":
          pushBlock(buffers.main, "tool", `\n\n> ⚠️ ${esc(msg.message)}\n\n`);
          schedule();
          resolve(false);
          break;
      }
    };
    ws.onerror = () => { toast(t("WebSocket 连接失败"), true); resolve(false); };
    ws.onclose = () => resolve(false);
  });
}

/* ================= 模型下载进度横幅 ================= */
function formatSize(bytes) {
  if (!bytes || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, n = bytes;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return n.toFixed(i === 0 ? 0 : 1) + " " + units[i];
}

function initDlBanner() {
  const banner = document.createElement("div");
  banner.id = "dlBanner";
  banner.className = "dl-banner";
  banner.style.display = "none";
  banner.innerHTML = `
    <div class="dl-text">⬇ ${t("正在下载 embedding 模型")}：<span id="dlModel"></span></div>
    <div class="dl-bar"><div class="dl-bar-fill" id="dlFill"></div></div>
    <div class="dl-info"><span id="dlSize"></span><span id="dlSpeed"></span></div>`;
  document.body.appendChild(banner);
}

async function pollDownload() {
  try {
    const r = await api("/api/download-status");
    const cur = r && r.current;
    const banner = $("#dlBanner");
    if (!banner) return;
    if (cur && (cur.status === "preparing" || cur.status === "downloading")) {
      $("#dlModel").textContent = cur.model || "";
      if (cur.status === "preparing") {
        $("#dlFill").style.width = "0%";
        $("#dlSize").textContent = cur.total ? `${t("共")} ${formatSize(cur.total)}` : "";
        $("#dlSpeed").textContent = t("正在准备 / 校验缓存…");
      } else {
        const pct = cur.total ? Math.round((cur.downloaded / cur.total) * 100) : 0;
        $("#dlFill").style.width = pct + "%";
        $("#dlSize").textContent = `${formatSize(cur.downloaded)} / ${formatSize(cur.total)}`;
        $("#dlSpeed").textContent = formatSize(cur.speed) + "/s";
      }
      banner.style.display = "block";
    } else {
      banner.style.display = "none";
    }
  } catch (e) {}
}

initDlBanner();
setInterval(pollDownload, 1000);

/* ================= 启动 ================= */
applyColors();
render();
