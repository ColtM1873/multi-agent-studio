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
  "主 agent": "Main agent",
  "{actor} 思考中": "{actor} is thinking",
  "{actor} 回答中": "{actor} is answering",
  "{actor} 编辑工具调取指令": "{actor} is composing a tool call",
  "{actor} 给子 agent 发布任务": "{actor} is dispatching a task to a sub-agent",
  "{actor} 等待工具执行完毕": "{actor} is waiting for the tool to finish",
  "未调用": "Not called",
  "等待输出…": "Waiting for output…",
  "选择子 agent": "Select sub-agent",
  "Embedding 模型离线模式": "Embedding offline mode",
  "HTML 报告生成 prompt": "HTML report generation prompt",
  "MCP 服务器": "MCP server",
  "Multi-Agent 配置": "Multi-Agent configurations",
  "SSL 模式": "SSL mode",
  "WebSocket 连接失败": "WebSocket connection failed",
  "agent 回复完成时播放的提示音，切换即试听。": "Chime played when the agent finishes replying; switch to preview.",
  "ber（下滑音）": "ber (descending)",
  "checkpoint 库": "checkpoint database",
  "chime（双音上行）": "chime (rising two-tone)",
  "ding（清脆单音）": "ding (crisp single tone)",
  "embedding 模型": "embedding model",
  "embedding 维度": "embedding dimension",
  "embedding 缓存目录": "embedding cache directory",
  "embedding 镜像": "embedding mirror",
  "hf-mirror（国内镜像）": "hf-mirror (China mirror)",
  "prefer（有 SSL 就启用，无则明文）": "prefer (SSL if available, plaintext otherwise)",
  "require（必须 SSL，不校验证书）": "require (SSL required, no certificate verification)",
  "verify-ca（校验 CA 证书）": "verify-ca (verify CA certificate)",
  "verify-full（校验 CA + 主机名）": "verify-full (verify CA + hostname)",
  "DeepSeek 思考模式 + 工具调用时，官方 LangChain 会丢弃历史思考内容、可能偶发 400。勾选后在本项目内运行时回填该内容（不改动官方库）。": "In DeepSeek thinking mode with tool calls, official LangChain drops historical reasoning content, which can cause intermittent 400s. When enabled, this project restores it at runtime (the official library is not modified).",
  "启用 DeepSeek reasoning_content 保留修复": "Enable DeepSeek reasoning_content preservation fix",
  "✅ 回复已完成": "✅ Reply completed",
  "万": "×10,000",
  "下载 embedding 模型时使用的 HuggingFace 镜像源；国内推荐 hf-mirror。": "HuggingFace mirror used when downloading the embedding model; hf-mirror is recommended in China.",
  "不会删除数据库里的会话历史": "Conversation history in the database will not be deleted",
  "个": "",
  "主 agent": "main agent",
  "主Agent读写本地文件的根目录": "Root directory where the main agent reads/writes local files.",
  "主 agent 的系统提示词，定义其角色与行为。": "System prompt for the main agent, defining its role and behavior.",
  "主 agent 输出完后，询问是否将结果生成 HTML 报告。": "After the main agent finishes, ask whether to generate an HTML report from the result.",
  "主会话": "Main conversation",
  "主机:port": "host:port",
  "主模型": "Main model",
  "云端/企业库请选择 SSL 模式": "For cloud/enterprise databases, choose an SSL mode",
  "仅鼠标（Enter 只换行）": "Mouse only (Enter adds a newline)",
  "会话": "Conversations",
  "会话历史归属的库名，即连接串里最后一个 / 后面的那一段（如 postgresql://…:5432/这里）。保存时如果该库不存在，程序会自动创建；创建后不可更改。": "Database that holds the conversation history, i.e. the segment after the last / in the connection string (e.g. postgresql://…:5432/this-part). If it does not exist, the app creates it automatically when saving. Cannot be changed after creation.",
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
  "修改未保存时提醒": "Warn on unsaved changes",
  "停止": "Stop",
  "停止中": "Stopping…",
  "共": "Total",
  "创建后不可增删改名": "Sub-agents cannot be added/removed/renamed after creation",
  "创建后不可更改": "Cannot be changed after creation",
  "删除": "Delete",
  "加载Embedding模型中": "Loading embedding model…",
  "加载中…": "Loading…",
  "加载历史中…": "Loading history…",
  "千": "×1,000",
  "协议": "Protocol",
  "参数，逗号分隔（stdio）": "Arguments, comma-separated (stdio)",
  "发送": "Send",
  "发送失败，消息已保留在输入框": "Send failed; message kept in the input box",
  "发送消息后自动跟随最新输出": "Auto-follow the latest output after sending",
  "发送新消息后，聊天区自动下滑到最新消息并跟随流式输出；鼠标上滑即可停止跟随，方便回看历史，不会像图钉那样锁死。": "After you send a message, the chat area scrolls to the latest message and follows the streaming output. Scroll up to stop following and review history — unlike the pin button, it does not lock.",
  "发送键": "Send key",
  "发送，": "to send, ",
  "取消": "Cancel",
  "返回": "Back",
  "本次Agent循环失败/被中断，是否将 原发送信息回填？": "This agent run failed or was interrupted. Restore the original sent message?",
  "回填": "Restore",
  "丢弃": "Discard",
  "可增删": "Can add/remove",
  "名称": "Name",
  "向量维度；bge-m3 为 1024，换模型需对应调整。": "Vector dimension; 1024 for bge-m3, adjust if you change models.",
  "否": "No",
  "启用 HTML 报告": "Enable HTML report",
  "吸附记忆条数": "Memories to attach",
  "命令（stdio）": "Command (stdio)",
  "唯一标识，用作配置文件名 configs/&lt;id&gt;.json，创建后不可更改。": "Unique ID, used as the config filename configs/&lt;id&gt;.json. Cannot be changed after creation.",
  "回复完成提示音": "Reply completion chime",
  "基本信息": "Basic info",
  "子 agent": "sub-agent",
  "子 agent 标识，会作为工具名呈现给主 agent，创建后不可更改。": "Sub-agent ID, shown to the main agent as a tool name. Cannot be changed after creation.",
  "完整连接串示例": "Full connection string example",
  "官方 huggingface.co": "Official huggingface.co",
  "工具结果": "Tool result",
  "工具调用": "Tool call",
  "已保存": "Saved",
  "已关闭 Embedding 模型离线模式：每次打开会联网校验。若已获取模型缓存，建议重新开启以跳过校验。": "Embedding offline mode disabled: it verifies online every time it opens. If you already have the model cached, re-enable it to skip verification.",
  "已创建": "Created",
  "已删除": "Deleted",
  "已设为默认配置": "Set as default configuration",
  "库名": "Database name",
  "开启后，主 agent 收到用户消息时会先从记忆库语义检索 N 条相关记忆，附在用户消息里一起传入。此设置影响图编译，进入某个 multi-agent 后不可改动，需退回主界面。": "When enabled, the main agent retrieves N relevant memories from the memory store and attaches them to the user message. This affects graph compilation and cannot be changed after entering a multi-agent; go back to the main screen.",
  "开启 ReAct Prompt": "Enable ReAct Prompt",
  "用于长程多工具调用的严肃任务处理场景；日常聊天可关闭以节省 token 并加快响应。": "For serious task scenarios involving long-range, multi-tool calls; disable for everyday chat to save tokens and speed up responses.",
  "恢复默认": "Restore default",
  "换行键": "Newline key",
  "换行）": "to break)",
  "描述该子 agent 能力，作为工具描述呈现给主 agent。": "Describes the sub-agent's capability, shown to the main agent as a tool description.",
  "数据库名": "Database name",
  "数据库地址": "Database address",
  "主Agent工作目录": "Main agent working directory",
  "文件工具": "File tools",
  "PDF 表格提取": "PDF table extraction",
  "新会话名称：": "New conversation name:",
  "新建 / 编辑 multi-agent 时，空输入框显示灰色示例文字，帮助快速上手。": "When creating/editing a multi-agent, empty inputs show gray example text to help you get started.",
  "新建 multi-agent": "New multi-agent",
  "新建会话": "New conversation",
  "无": "None",
  "未发送消息": "No messages sent yet",
  "无字符串（视数据库设置启用/关闭 SSL）": "No string (SSL enabled/disabled by database settings)",
  "无需提前下载，首次配置会自动下载（需连接 Hugging Face Hub，国内网络可能连不上）。若已离线缓存过，可在下方缓存目录直接使用。": "No need to download in advance; it downloads automatically on first setup (requires connecting to Hugging Face Hub, which may be blocked in China). If already cached, use the cache directory below.",
  "是": "Yes",
  "显示名称，创建后不可更改（与历史会话绑定）。": "Display name, cannot be changed after creation (bound to conversation history).",
  "显示输入示例（灰色占位字）": "Show input examples (gray placeholders)",
  "显示「注入当前日期」按钮": "Show the \"Inject current date\" button",
  "开启后，聊天输入框右侧会出现「注入当前日期」按钮；开启该按钮后，发送消息时会在用户消息前面拼接当前日期。": "When enabled, an \"Inject current date\" button appears next to the chat input. When that button is on, the current date is prepended to your message when sending.",
  "注入当前日期": "Inject current date",
  "开启后，发送消息时会在用户消息前面拼接当前日期。": "When enabled, the current date is prepended to your message when sending.",
  "显示「注入当前时间」按钮": "Show the \"Inject current time\" button",
  "开启后，聊天输入框右侧会出现「注入当前时间」按钮；开启该按钮后，发送消息时会在用户消息前面拼接当前时间。": "When enabled, an \"Inject current time\" button appears next to the chat input. When that button is on, the current time is prepended to your message when sending.",
  "注入当前时间": "Inject current time",
  "开启后，发送消息时会在用户消息前面拼接当前时间。": "When enabled, the current time is prepended to your message when sending.",
  "显示「代码输入模式」按钮": "Show the \"Code input mode\" button",
  "开启后，聊天输入框右侧会出现「代码输入模式」按钮；开启该按钮后，在输入框内按 Tab 会插入 4 个空格（而不是切换焦点）。": "When enabled, a \"Code input mode\" button appears next to the chat input. When that button is on, pressing Tab inside the input inserts 4 spaces instead of moving focus.",
  "代码输入模式": "Code input mode",
  "开启后，在输入框内按 Tab 会插入 4 个空格，而不是把焦点切换到下一个按钮。": "When enabled, pressing Tab inside the input inserts 4 spaces instead of moving focus to the next button.",
  "发送框设置": "Message input settings",
  "用于集中管理聊天输入框相关的显示与注入开关。": "Manage display and injection switches related to the chat input box.",
  "开启常用prompt注入功能": "Enable common-prompt injection",
  "开启后，会话页会出现「注入常用prompt」「编辑会话常用prompt」按钮，主界面会出现「编辑全局常用prompt」按钮；关闭后这三个按钮都不再显示，但已保存的全局常用 prompt 库与每个会话的设置都会一直保留。": "When enabled, the chat view shows the \"Inject common prompt\" and \"Edit session common prompt\" buttons, and the main screen shows \"Edit global common prompts\". When disabled these three buttons are hidden, but the saved global prompt library and each session's setting are always kept.",
  "注入常用prompt": "Inject common prompt",
  "编辑全局常用prompt": "Edit global common prompts",
  "编辑会话常用prompt": "Edit session common prompt",
  "会话常用prompt": "Session common prompt",
  "点击开启：发送时把当前会话的常用 prompt 拼接到消息中（日期行下方、你输入的内容上方）": "Click to enable: this session's common prompt is inserted into your message when sending (below the date line, above your own input).",
  "已开启：发送时会把当前会话的常用 prompt 拼接到消息中（日期行下方、你输入的内容上方；发送后自动关闭）": "On: this session's common prompt will be inserted into your message when sending (below the date line, above your own input; turns off automatically after sending).",
  "当前会话还没有设置常用 prompt，请先点「编辑会话常用prompt」设置。": "This session has no common prompt yet. Click \"Edit session common prompt\" to set one first.",
  "用于当前会话：开启「注入常用prompt」后，发送消息时会把下方内容拼接到消息中（日期行下方、你输入的内容上方）。内容取自全局常用prompt库里本会话指向的那一条，改了会一起变。": "For this session: when \"Inject common prompt\" is on, the content below is inserted into your message when sending (below the date line, above your own input). The content comes from the entry this session points to in the global library, so edits there apply here too.",
  "每条常用 prompt 显示为一个预览框（最多三行，超出部分不显示）；点框内内容会打开一个独立的编辑窗口，在编辑窗口里点「保存」即生效。": "Each common prompt is shown as a preview box (at most three lines; anything beyond is hidden). Click the content to open a separate editor window; click Save there to apply the change.",
  "新增一条": "Add one",
  "全局常用prompt库为空：点下方「+ 新增一条」添加。": "The global common-prompt library is empty. Click \"+ Add one\" below to add an entry.",
  "点击编辑": "Click to edit",
  "编辑常用prompt": "Edit common prompt",
  "新增常用prompt": "Add common prompt",
  "已保存常用 prompt": "Common prompt saved",
  "确定删除这条常用 prompt？删除后，指向它的会话会回到「未设置」。": "Delete this common prompt? Sessions pointing to it will go back to \"not set\".",
  "确定删除这条常用 prompt（当前会话正在使用）？删除后，本会话和所有指向它的会话都会回到「未设置」。": "Delete this common prompt (currently used by this session)? This session and every session pointing to it will go back to \"not set\".",
  "已删除该条常用 prompt": "Common prompt deleted",
  "已删除该条常用 prompt，当前会话已回到「未设置」": "Common prompt deleted; this session is back to \"not set\"",
  "这条常用 prompt 已不存在，请关闭后重新打开": "This common prompt no longer exists. Close and reopen this dialog.",
  "常用 prompt 内容": "Common prompt content",
  "例如：请用简体中文回答，先给结论再给理由。": "e.g. Answer in Simplified Chinese; give the conclusion first, then the reasoning.",
  "直接输入": "Enter manually",
  "从下方全局库中选取一条，作为当前会话的常用 prompt。保存的是「指向该条的指针」：以后在主界面改了这一条，本会话会跟着一起变（该条被删除则本会话回到「未设置」）。每条右侧的「编辑」「删除」会直接修改全局库，删除后所有指向它的会话都会回到「未设置」。": "Pick one entry from the global library below as this session's common prompt. What is saved is a pointer to that entry: editing it on the main screen updates this session too (if that entry is deleted, this session goes back to \"not set\"). The \"Edit\" and \"Delete\" buttons on each row modify the global library directly; once an entry is deleted, every session pointing to it goes back to \"not set\".",
  "直接输入常用prompt": "Type a common prompt",
  "新增常用 prompt 失败": "Failed to add the common prompt",
  "全局常用prompt库为空：可点「直接输入」新建一条，或先到主界面「编辑全局常用prompt」新增条目。": "The global common-prompt library is empty. Click \"Enter manually\" to create one, or add entries via \"Edit global common prompts\" on the main screen first.",
  "请先选取一条常用 prompt": "Please pick a common prompt first",
  "常用 prompt 不能为空": "Common prompt cannot be empty",
  "清空（不设置）": "Clear (none)",
  "已保存当前会话的常用 prompt": "Saved this session's common prompt",
  "已清空当前会话的常用 prompt": "Cleared this session's common prompt",
  "点击查看完整内容": "Click to view the full content",
  "开启浏览器接管功能": "Enable browser takeover",
  "开启后，会向主 agent 注入一组浏览器接管工具（占用上下文）。只有需要让 agent 操作浏览器时才开启。": "When enabled, a set of browser-takeover tools is injected into the main agent (consuming context). Only turn it on when the agent needs to operate a browser.",
  "允许 Agent保留的最大标签页数": "Max tabs the agent may keep",
  "Agent 主动关闭标签页要花掉上下文，实际上很少这么做；于是「目前打开的标签页有哪些」会越列越长、白烧 token。这里设一个上限，超出的标签页按 LRU（最久未使用的先关）自动关闭，并在返回给模型的内容里注明关了哪些。<br/><br/>计数按浏览器进程算：同一个浏览器里的所有标签页都算在内（包括你自己手动打开的），当前聚焦的标签页永不关闭。两个 multi-agent 共用同一个浏览器时，按最近一次配置的 multi-agent 的值为准。<br/><br/>默认 4，最小 3（聚焦页不能关，1~2 会逼出「新页和聚焦页只能留一个」的死结），最大 50。": "Closing tabs costs the agent context, so it rarely does it — and the list of currently open tabs keeps growing, burning tokens for nothing. Set a cap here: tabs beyond it are closed automatically by LRU (least recently used first), and the reply to the model notes which ones were closed.<br/><br/>Tabs are counted per browser process: every tab in that browser counts (including ones you opened yourself), and the focused tab is never closed. If two multi-agents share one browser, the value configured most recently applies.<br/><br/>Default 4, minimum 3 (the focused tab cannot be closed; 1–2 would force a dead end where the new tab and the focused tab cannot coexist), maximum 50.",
  "开启记忆读取/写入功能": "Enable memory read/write",
  "开启后，会向主 agent 注入记忆读写工具（write_memory / read_memory），让 agent 能长期存取与检索信息；关闭后主 agent 不再拥有记忆读写工具。此设置影响图编译；「记忆吸附」是另一项功能，不受本开关影响。": "When enabled, memory read/write tools (write_memory / read_memory) are injected into the main agent so it can store and retrieve information long-term; when disabled the main agent no longer has memory read/write tools. This affects graph compilation; \"memory attachment\" is a separate feature and is not affected by this switch.",
  "显示浏览器接管说明弹窗": "Show the browser-takeover intro popup",
  "每次打开浏览器时，弹窗说明红色浮动按钮（停止/继续）的作用。关闭后不再弹出。": "Each time the browser is opened, show a popup explaining the red floating button (Stop/Continue). Turn off to suppress it.",
  "提示Agent开始接管浏览器": "Ask the agent to start taking over the browser",
  "打开浏览器": "Open browser",
  "浏览器已打开（当前只支持接管一个浏览器）": "Browser already open (only one browser can be taken over)",
  "继续": "Continue",
  "拖动": "Drag",
  "拖动调整大小": "Drag to resize",
  "拖动移动": "Drag to move",
  "浏览器接管说明": "Browser takeover guide",
  "知道了": "Got it",
  "这是「浏览器接管」控制按钮。红色表示 Agent 可以操作浏览器；点击它会把状态切到绿色「继续」，此时 Agent 的所有浏览器操作会被立即暂停，等待你的后续指示。你可以在它旁边的 ✥ 把手处拖动它，避免遮挡页面。再次点击可恢复。": "This is the browser-takeover control. Red means the agent may operate the browser; click it to switch to green \"Continue\", which immediately pauses all browser actions until you give further instructions. Drag the ✥ handle beside it to move it out of the way. Click again to resume.",
  "已在桌面显示一个始终置顶的「停止」悬浮按钮（无论切到哪个应用或页面都能看到）。红色表示 Agent 可以操作浏览器；点击它变成绿色「继续」，此时 Agent 的所有浏览器操作会被立即暂停，等待你的后续指示。可拖动它旁边的 ✥ 把手移动位置，再次点击可恢复。": "A desktop-level, always-on-top \"Stop\" button has been shown (visible no matter which app or page you switch to). Red means the agent may operate the browser; click it to turn green \"Continue\", which immediately pauses all browser actions until you give further instructions. Drag the ✥ handle beside it to move it. Click again to resume.",
  "请先点击「继续」按钮，再提示 Agent 接管浏览器。": "Please click \"Continue\" first, then ask the agent to take over.",
  "打开浏览器失败": "Failed to open the browser",
  "获取当前页面内容失败": "Failed to fetch the current page content",
  "本对话已注入过接管提示（每个对话仅注入一次）": "This conversation already has the takeover hint (only once per conversation).",
  "已开启：发送时会把当前聚焦标签页内容追加到消息之后（发送后自动关闭）": "On: the focused tab's content will be appended after your message when sending (turns off automatically after sending).",
  "正在获取当前页面内容…": "Fetching the current page content…",
  "📋 当前页面内容 注入块": "📋 Current page content injection block",
  "（发送时会自动附加到消息）": "(automatically appended to the message when sending)",
  "编辑敏感信息表单": "Edit sensitive info form",
  "用于浏览器接管：Agent 遇到表单类「可填入元素」时填 <名称>，后台会替换成右侧的真实内容；左侧填「名称」，右侧填「真实内容」。表单里没有的名称会按 Agent 原文原样填入。": "For browser takeover: when the agent fills a form-like \"fillable element\", it writes <name>; the backend replaces it with the real content on the right. Put the name on the left and the real content on the right. Names not in the form are filled exactly as the agent wrote them.",
  "信息名称（表项）": "Info name (field)",
  "实际填入内容": "Actual content to fill",
  "例如 姓名": "e.g. Name",
  "例如 张三": "e.g. John Doe",
  "删除本行": "Delete this row",
  "在下方新增一行": "Add a new row below",
  "确定删除这一行敏感信息吗？": "Delete this sensitive-info row?",
  "注入 敏感信息替换列表": "Inject sensitive-info replacement list",
  "敏感信息替换列表：": "Sensitive-info replacement list:",
  "📋 敏感信息替换列表 注入块": "📋 Sensitive-info replacement list injection block",
  "已开启：发送时会把敏感信息替换列表附加到消息之后（发送后自动关闭）": "On: the sensitive-info replacement list will be appended after your message when sending (turns off automatically after sending).",
  "点击开启：发送时把敏感信息替换列表附加到消息之后，供后台把 <名称> 替换为真实值": "Click to enable: append the sensitive-info replacement list after your message when sending, so the backend can replace <name> with real values.",
  "请遇到如下需要填写的内容时，直接将下述列表中的名称原文加上<>包裹后填入。比如，若下述列表中有「姓名」，那么在遇到「请输入你姓名」之后的可填入元素时，直接填写为<姓名>即可。CDP 在后台实现时，会将下列列表中的名称原文（带<>包裹）替换为真实值；若列表中没有对应名称，则按原文原样填入。": "When you encounter a field to fill, write the name from the list below wrapped in <> and fill that in. For example, if the list contains \"Name\", then for a fillable element after \"Please enter your name\", fill it with <Name>. The CDP backend will replace the wrapped name with the real value; if a name is not in the list, fill it exactly as written.",
  "敏感信息表单为空，请先在主界面「编辑敏感信息表单」中添加": "The sensitive-info form is empty. Please add entries via \"Edit sensitive info form\" on the main screen first.",
  "暂无会话": "No conversations yet",
  "所有会话均已隐藏": "All conversations are hidden",
  "隐藏": "Hide",
  "取消隐藏": "Unhide",
  "已隐藏": "Hidden",
  "显示隐藏对话": "Show hidden conversations",
  "显示隐藏multi-agent配置": "Show hidden multi-agent configs",
  "所有 multi-agent 配置均已隐藏": "All multi-agent configs are hidden",
  "Multi-Agent配置卡片 设置": "Multi-agent config card settings",
  "导入本机设置": "Import local settings",
  "已导入本机设置": "Local settings imported",
  "导入失败：设置桥接未加载": "Import failed: settings bridge not loaded",
  "将当前界面的本机设置（隐藏列表、卡片外观等）上传到后端保存，用于从旧浏览器迁移。确定继续？": "Upload this interface's local settings (hidden lists, card appearance, etc.) to the backend, for migrating from an old browser. Continue?",
  "调整主界面 multi-agent 卡片的按钮配色 / 样式、绶带与背景，仅本机生效。": "Adjust button colors/styles, ribbon and background of the home-screen multi-agent cards. Applies to this machine only.",
  "显示绶带": "Show ribbon",
  "按钮颜色": "Button colors",
  "按钮样式": "Button style",
  "开启 = 颜色底白字，关闭 = 白底框线": "On = solid color with white text, off = white background with outline",
  "绶带": "Ribbon",
  "颜色": "Color",
  "条数": "Count",
  "卡片背景": "Card background",
  "背景颜色": "Background color",
  "暂无用户消息": "No user messages yet",
  "有未保存的修改，确定离开？": "You have unsaved changes. Leave anyway?",
  "编辑模式中有未保存的修改，请先退出编辑模式并提交或撤销改动后再发送。": "You have unsaved changes in edit mode. Exit edit mode (submit or discard) before sending.",
  "本地模型缓存路径，留空用 Hugging Face 默认缓存。": "Local model cache path; leave empty to use Hugging Face's default cache.",
  "本地连接，自动使用 sslmode=disable": "Local connection, auto sslmode=disable",
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
  "全局配置设置": "Global configuration settings",
  "历史浏览时各板块默认展开 / 折叠": "Default expand/collapse of sections when browsing history",
  "历史浏览时，思考过程板块默认展开还是折叠。": "Whether the reasoning section is expanded or collapsed by default when browsing history.",
  "历史浏览时，工具调用板块默认展开还是折叠。": "Whether the tool call section is expanded or collapsed by default when browsing history.",
  "历史浏览时，工具结果板块默认展开还是折叠。": "Whether the tool result section is expanded or collapsed by default when browsing history.",
  "开启后默认展开": "Expanded by default when enabled",
  "流式显示子 agent 工具": "Stream sub-agent tools",
  "流式输出时显示的内容": "Content shown during streaming",
  "输入消息缓存": "Input message cache",
  "其他": "Other",
  "已输入未发送消息跨multi-agent流转": "Sync unsent input across multi-agents",
  "开启后，你在任意 multi-agent 输入框中输入的内容，会同步更新所有 multi-agent 的未发送消息缓存（等效于共用一个缓存）；关闭后各 multi-agent 独立保存。未发送的消息会持久化到本地，退出程序后下次打开仍会保留。": "When enabled, text you type in any multi-agent input box is synced to all multi-agents' unsent-message caches (equivalent to sharing one cache); when disabled, each multi-agent keeps its own. Unsent text is persisted locally and restored the next time you open the app after quitting.",
  "开启后，流式输出时子 agent 的工具调用与工具结果会像主 agent 一样实时显示；关闭则只显示子 agent 的正文与思考。": "When enabled, a sub-agent's tool calls and tool results stream in real time like the main agent's; when disabled, only the sub-agent's text and reasoning are shown.",
  "浏览器接管": "Browser takeover",
  "浏览器工具debug模式": "Browser tool debug mode",
  "开启后，LLM 每次调用浏览器工具都会在程序根目录的 browser_tools_DEBUG_logs 文件夹下，按小时记录一份日志（含整个 viewport 的原始 DOM、处理后的 DOM、LLM 输入、工具返回的 content），便于排查。": "When enabled, every browser tool call by the LLM writes an hourly log under the browser_tools_DEBUG_logs folder in the program root (the whole viewport's raw DOM, processed DOM, LLM input and returned content) to help troubleshooting.",
  "浏览器工具解析图片标签": "Parse image tags in browser tool output", // BID080
  "开启后，浏览器工具会把网页里的图片解析成 [图片] 标签（有 alt 时附带一行文本）返回给 LLM；关闭时（默认），[图片] 行以及因此变空的 [..] 结构标签父链会被整条删除，不输出任何图片信息。": "When enabled, the browser tool renders images in web pages as [图片] tags (with one line of alt text when available) for the LLM; when disabled (the default), the [图片] line and any [..] structural parent chain left empty by its removal are deleted entirely, so no image information is returned.", // BID080
  // BID084：可交互元素整合 / 分隔符 / 英文渲染
  "浏览器可交互元素整合": "Merge interactive browser elements",
  "开启后（默认），返回给 LLM 的可交互元素会从 `<可点击元素 eN>内容</可点击元素 eN>` 压缩成 `<可点击元素 eN : 内容>`，取消闭合标签，可显著节省上下文长度。可滚动元素不参与整合。": "When enabled (the default), interactive elements returned to the LLM are compressed from `<可点击元素 eN>content</可点击元素 eN>` into `<可点击元素 eN : content>`, dropping the closing tag to save context. Scrollable elements are not merged.",
  "整合分隔符": "Merge separator",
  "连接元素编号与内容的符号，默认英文冒号 `:`。例如设为 `|` 时输出 `<可点击元素 eN | 内容>`。": "Symbol between the element id and its content; defaults to a colon `:`. For example, `|` produces `<可点击元素 eN | content>`.",
  "浏览器交互元素使用英文渲染": "Render interactive elements in English",
  "开启后，可交互元素标签改用英文渲染（如 `<Click elem 23>`、`<Scroll elem 1>`），工具调用的元素编号直接传数字（如 23）。只替换标签与术语，提示语正文仍为中文。": "When enabled, interactive element tags are rendered in English (e.g. `<Click elem 23>`, `<Scroll elem 1>`), and tool calls take the bare number (e.g. 23). Only tags/terms change; notice prose stays Chinese.",
  "隐私遮蔽模式": "Privacy masking mode",
  "开启隐私遮蔽模式": "Enable privacy masking mode",
  "暂不开启": "Not now",
  "隐私遮蔽模式已开启": "Privacy masking mode enabled",
  "已开启：浏览器工具返回给 LLM 的内容与预览块都会把真实敏感值替换为 <名称>；点击关闭": "On: browser tool output and the preview block replace real sensitive values with <name> before reaching the LLM; click to turn off",
  "点击开启隐私遮蔽模式：在网页内容返回给 LLM 前，把敏感信息真实值替换为 <名称>": "Click to enable privacy masking: replace real sensitive values with <name> before web content reaches the LLM",
  "建议开启隐私遮蔽模式": "Enabling privacy masking mode is recommended",
  "隐私遮蔽模式开启后，所有从浏览器返回给 LLM 的网页内容（DOM、diff 等）以及下方「提示接管」预览块，都会在交给 LLM 之前，把其中出现的、与敏感信息表单「真实内容」相同的文字全部替换为对应的 <名称>，从而完全不向 LLM 暴露这些真实信息。\n\n⚠️ 请慎重填写：不要加入过于常见的信息。例如表单里填了「年龄 → 17」，那么网页里独立的 17 都会被替换成 <年龄>（纯数字/纯英文会做边界判断以减少误伤，例如 2017 不会被替换，但 17 岁会被替换）。请尽量只填写足够独特的内容。": "When privacy masking is on, all web content returned from the browser to the LLM (DOM, diff, etc.) and the 'hint takeover' preview block below will have any text matching a sensitive-info 'real value' replaced with the corresponding <name> before it reaches the LLM, so the real values are never exposed to the LLM.\n\n⚠️ Fill with care: do not add overly common values. For example, if you set 'age → 17', every standalone 17 in web content becomes <age> (pure numbers/English get boundary checks to reduce false hits, e.g. 2017 is left alone but '17 years' is replaced). Prefer values that are unique enough.",
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
  "主动全量总结": "Proactive Full Summary",
  "主动全量总结失败": "Proactive summary failed",
  "默认总结百分比": "Default summary percentage",
  "全量总结时，提示模型把历史压缩到的目标比例（占历史 token 的百分比）。默认与推荐 20%（即五分之一），数值越小越省 token。": "When doing a full summary, the target ratio (as a percentage of history tokens) the model is told to compress to. Default and recommended: 20% (one fifth). Smaller values save more tokens.",
  "主动总结时手动设置总结百分比": "Set summary percentage manually on proactive summary",
  "开启后，点击「主动全量总结」会先弹出百分比设置框，可为本次总结单独指定压缩比例；关闭时直接使用上面的默认总结百分比。": "When enabled, clicking 'Proactive Full Summary' first opens a percentage dialog so you can set the compression ratio for this summary; when disabled, the default summary percentage above is used directly.",
  "请设置本次总结的目标百分比（占当前历史 token 的比例）。": "Set the target percentage for this summary (as a share of the current history tokens).",
  "将 {total} tokens 数量的历史消息压缩为 {target} tokens": "Compress {total} tokens of history into {target} tokens",
  "开始总结": "Start summary",
  "记忆检索相似度门槛": "Memory retrieval similarity thresholds",
  "取值范围 0~1": "Range 0~1",
  "主动搜索记忆门槛": "Proactive memory search threshold",
  "主 agent 主动调用搜索记忆工具时的语义相似度门槛，取值范围 0~1。分数低于该门槛的记忆不会被返回；数值越大越严格、返回的记忆越少。": "Semantic similarity threshold when the main agent proactively calls the memory search tool, range 0~1. Memories scoring below it are not returned; larger values are stricter and return fewer memories.",
  "自动吸附记忆门槛": "Automatic memory attach threshold",
  "收到用户消息时自动检索并吸附相关记忆的语义相似度门槛，取值范围 0~1。分数低于该门槛的记忆不会被吸附；数值越大越严格、吸附的记忆越少。": "Semantic similarity threshold for automatically retrieving and attaching relevant memories on an incoming user message, range 0~1. Memories scoring below it are not attached; larger values are stricter and attach fewer memories.",
  "添加 MCP": "Add MCP",
  "添加子 agent": "Add sub-agent",
  "清空历史时保留最近几轮对话。": "Keep the most recent turns when clearing history.",
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
  "请求头（http），如 Authorization: Bearer xxx；按第一个冒号切成 dict，多个用 ; 分隔，勿加引号": "Headers (http), e.g. Authorization: Bearer xxx; split at the first colon into a dict, separate multiple with ;, no quotes",
  "查看 JSON 格式示例": "View JSON example",
  "JSON 格式示例": "JSON example",
  "该 MCP 表项在配置文件（configs/*.json）中的存储形态：": "How this MCP entry is stored in the config file (configs/*.json):",
  "关闭": "Close",
  "查找": "Find",
  "上一个": "Previous",
  "下一个": "Next",
  "无结果": "No results",
  "打开链接失败": "Failed to open link",
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
  "记忆附着": "Attached memory",
  "查看本条消息自动附着的记忆": "View the memory attached to this message",
  "裸公式识别": "Auto-detect bare formulas",
  "模型偶尔不带 $ 或 \\( 分隔符直接输出公式（如 s_{t+1}=f(s_t,a_t)）。开启后自动识别并渲染，适合科研 / 数理场景；日常场景建议关闭，以免误判普通文本。": "Models sometimes output formulas without $ or \\( delimiters (e.g. s_{t+1}=f(s_t,a_t)). When enabled, they are detected and rendered automatically—useful for research/math scenarios. Keep it off in everyday use to avoid misinterpreting plain text.",
  "激进公式渲染": "Aggressive formula rendering",
  "在「裸公式识别」基础上更激进：支持单字符上下标（如 V_φ、V^π）、希腊字母与数学符号，并会把「看起来是公式」的代码块 / 行内代码也渲染成公式。适合数理场景，可能误判普通文本或代码，建议按需开启。": "More aggressive than auto-detecting bare formulas: also supports single-character sub/superscripts (e.g. V_φ, V^π), Greek letters and math symbols, and renders code blocks / inline code that look like formulas. Suited to math-heavy scenarios; may misinterpret plain text or code, so enable as needed.",
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
  "请先选择要总结的子 agent": "Please select a sub-agent to summarize first",
  "跟随最新输出 · 按住可拖动": "Follow latest output · hold to drag",
  "输入消息…": "Type a message…",
  "输入消息（": "Type a message (",
  "输入消息（Enter 发送，Shift+Enter 换行）": "Type a message (Enter to send, Shift+Enter for newline)",
  "输入消息（点击发送，Enter 换行）": "Type a message (click to send, Enter for newline)",
  "返回会话": "Back to conversation",
  "还没有任何 multi-agent 配置": "No multi-agent configurations yet",
  "连接前缀": "Connection prefix",
  "连接后缀": "Connection suffix",
  "（无）": "(none)",
  "数据库连接填写方式": "How to fill the database connection",
  "两种方式二选一：不熟悉「连接前缀/连接后缀」时，可直接粘贴一条完整的连接串，程序会自动拆分。": "Pick either way: if you're unfamiliar with the 'connection prefix/suffix', just paste a full connection string and it will be split automatically.",
  "分步填写（连接前缀 / 连接后缀）": "Step-by-step (connection prefix / suffix)",
  "直接填写完整连接串": "Paste a full connection string",
  "连接前缀 = 连接串里「数据库名」前面的全部内容：协议://用户名:密码@主机:端口/。<br/><br/>例如 postgresql://user:passwd@localhost:5432/<br/><br/>它回答「连到哪台数据库服务器、用哪个账号」。注意结尾的斜杠 / 不能少，数据库名填在上一栏「checkpoint 数据库」。": "Connection prefix = everything before the database name in the connection string: protocol://user:password@host:port/.<br/><br/>e.g. postgresql://user:passwd@localhost:5432/<br/><br/>It answers 'which database server, with which account'. Note the trailing slash / is required; the database name goes in the 'checkpoint database' field above.",
  "格式：postgresql://用户名:密码@主机:端口/（以斜杠 / 结尾）": "Format: postgresql://user:password@host:port/ (must end with a slash /)",
  "连接后缀 = 连接串里「数据库名」后面的部分，通常是 ? 开头的参数，最主要的是 SSL 模式。<br/><br/>例如 ?sslmode=disable、?sslmode=require。<br/><br/>本地数据库用 sslmode=disable 即可；云端/企业数据库按服务商要求选择。": "Connection suffix = everything after the database name, usually parameters starting with ?, most importantly the SSL mode.<br/><br/>e.g. ?sslmode=disable, ?sslmode=require.<br/><br/>For a local database, sslmode=disable is fine; for cloud/enterprise databases, choose per your provider's requirement.",
  "完整连接串": "Full connection string",
  "直接粘贴数据库服务商提供的整条连接串（PostgreSQL 官方 URI 格式）。<br/><br/>例如 postgresql://user:passwd@localhost:5432/mydb?sslmode=disable<br/><br/>程序会自动拆出连接前缀、数据库名、连接后缀；其中的数据库名会同步到上面的「checkpoint 数据库」。": "Paste the whole connection string provided by your database vendor (official PostgreSQL URI format).<br/><br/>e.g. postgresql://user:passwd@localhost:5432/mydb?sslmode=disable<br/><br/>The app splits it into connection prefix, database name and connection suffix; the database name is synced to the 'checkpoint database' field above.",
  "按当前填写内容实时拼出的完整连接串（密码已脱敏）。": "The full connection string composed in real time from what you filled in (password masked).",
  "已识别": "Recognized",
  "请粘贴完整连接串": "Please paste a full connection string",
  "连接串必须以 postgresql:// 或 postgres:// 开头": "The connection string must start with postgresql:// or postgres://",
  "连接串缺少数据库名，格式应为 …/<数据库名>": "The connection string is missing the database name; format should be …/<database name>",
  "连接串缺少主机地址": "The connection string is missing the host address",
  "连接串里的数据库名与已绑定的 checkpoint 库不一致，创建后不可更换。": "The database name in the connection string differs from the bound checkpoint database, which cannot be changed after creation.",
  "全局postgres连接设置": "Global PostgreSQL connection settings",
  "使用全局 postgres 连接设置": "Use global PostgreSQL connection settings",
  "勾选后，本 multi-agent 直接使用主界面「系统设置 → 全局postgres连接设置」里的连接前缀与连接后缀（主机 / 账号 / 密码 / SSL）；你仍可单独填写自己的 checkpoint 数据库名。取消勾选可为本 multi-agent 单独设置连接。": "When checked, this multi-agent uses the connection prefix and suffix (host / account / password / SSL) configured under Settings → Global PostgreSQL connection settings on the main screen; you can still fill in your own checkpoint database name. Uncheck to set the connection separately for this multi-agent.",
  "当前使用的全局连接（密码已脱敏）：": "Current global connection in use (password masked):",
  "如需修改全局连接，请到主界面齿轮设置 →「全局postgres连接设置」。": "To change the global connection, go to the main screen gear settings → 'Global PostgreSQL connection settings'.",
  "什么是「完整连接串」（conn string）：数据库厂商提供的一整条连接地址，长这样：": "What is a 'full connection string' (conn string): the complete connection address provided by the database vendor, like this:",
  "我们自造的「连接前缀 / 连接后缀」是什么意思：把上面这条连接串从「数据库名」处切成两半——数据库名前面的一段（协议、账号密码、主机端口）叫连接前缀，数据库名后面的一段（? 开头的 SSL 等参数）叫连接后缀。": "What our custom 'connection prefix / suffix' means: split the connection string above into two halves at the database name — the part before the database name (protocol, account/password, host/port) is the connection prefix, and the part after it (parameters starting with ?, such as SSL) is the connection suffix.",
  "填写方式": "Fill-in method",
  "预览（密码已脱敏）": "Preview (password masked)",
  "清除全局设置": "Clear global settings",
  "确定清除全局 postgres 连接设置吗？清除后各 multi-agent 将恢复为单独填写的界面。": "Clear the global PostgreSQL connection settings? After clearing, each multi-agent returns to the individually-filled interface.",
  "已清除全局连接设置": "Global connection settings cleared",
  "请填写连接前缀": "Please fill in the connection prefix",
  "全局连接设置已保存": "Global connection settings saved",
  "保存时自动创建": "Created automatically on save",
  "长期记忆分组": "Long-term memory group",
  "相同分组共用长期记忆，不同分组互相隔离": "Multi-agents in the same group share long-term memory; different groups are isolated from each other",
  "长期记忆（agent 用 write_memory 存下的知识、偏好、结论等）都放在同一个全局记忆库里，靠「分组」区分归属。<br/><br/>① 分组相同：这些 multi-agent 共用同一份长期记忆，能读到彼此写下的内容；<br/>② 分组不同：彼此完全隔离，互相检索不到对方的记忆；<br/>③ 写一层即可（例如 default）；需要更细的划分时，可写多层并用逗号分隔（例如 teamA,projectX）；<br/>④ 不能为空。修改本项后，本 multi-agent 会改用新分组读写记忆：旧分组里的记忆不会被删除，但本 multi-agent 不再读取它。<br/><br/>分组按字符串完全匹配：大小写不同、多一个空格都会被当作不同分组。想让几个 multi-agent 共享经验，就把这里写成完全相同的一串。": "Long-term memory (knowledge, preferences, conclusions saved by an agent via write_memory) all lives in one global memory database and is separated by 'group'.<br/><br/>① Same group: these multi-agents share the same long-term memory and can read what each other wrote;<br/>② Different groups: fully isolated — neither can retrieve the other's memories;<br/>③ One level is enough (e.g. default); for finer separation use several levels separated by commas (e.g. teamA,projectX);<br/>④ Cannot be empty. After changing this, the multi-agent reads and writes memory under the new group: memories in the old group are not deleted, but this multi-agent no longer reads them.<br/><br/>Groups match exactly as strings: different letter case or an extra space counts as a different group. To let several multi-agents share experience, enter exactly the same value here.",
  "用途：让所有 multi-agent 共用同一套数据库连接（主机、账号、密码、SSL）；每个 multi-agent 仍需填写自己的 checkpoint 数据库名（保存时程序会自动建库），长期记忆统一使用全局 store 数据库。": "Purpose: let all multi-agents share the same database connection (host, account, password, SSL). Each multi-agent still fills in its own checkpoint database name (the app creates it automatically on save), and long-term memory always uses the global store database.",
  "所以这里只需填「前缀 + 后缀」即可，checkpoint 数据库名由每个 multi-agent 各自填写；也可以直接粘贴整条连接串，程序会自动帮你切出前缀与后缀（其中的数据库名会被忽略）。": "So you only need to fill in 'prefix + suffix' here; the checkpoint database name is filled in by each multi-agent. Alternatively, paste the whole connection string and the app extracts the prefix and suffix automatically (the database name in it is ignored).",
  "这里粘贴的完整串只用于提取「连接前缀 / 连接后缀」，其中的数据库名会被忽略（checkpoint 库名由每个 multi-agent 各自填写）。": "The full string pasted here is only used to extract the connection prefix / suffix; its database name is ignored (each multi-agent fills in its own checkpoint database name).",
  "用于集中管理全局 postgres、store 数据库、embedding 与自动（及主动）总结设置。": "Manage the global PostgreSQL connection, store database, embedding, and automatic (& proactive) summary settings here.",
  "当前全局 store 数据库：": "Current global store database: ",
  "全局store数据库名称": "Global store database name",
  "迁移旧 store 数据库": "Migrate legacy store databases",
  "所有 multi-agent 的长期记忆都统一存放在这个 PostgreSQL 数据库里；不同 multi-agent 是否共用记忆只由内部的命名空间决定。": "All multi-agents store long-term memory in this PostgreSQL database. Whether different multi-agents share memories is determined only by the internal namespace.",
  "修改名称会直接把现有数据库改名（ALTER DATABASE RENAME），已有记忆会一并保留，不会新建再迁移。": "Changing the name renames the existing database in place (ALTER DATABASE RENAME). Existing memories are preserved; no new database or migration is involved.",
  "store 数据库名称": "Store database name",
  "默认 Long_Term_Memory；只能包含字母、数字、下划线或连字符。": "Defaults to Long_Term_Memory; only letters, digits, underscores or hyphens are allowed.",
  "默认 Long_Term_Memory。改名后，所有 multi-agent 的长期记忆会继续使用改名后的数据库。": "Defaults to Long_Term_Memory. After renaming, all multi-agents keep using the renamed database.",
  "store 数据库名称只能包含字母、数字、下划线或连字符，且必须以字母、数字或下划线开头": "The store database name may only contain letters, digits, underscores or hyphens, and must start with a letter, digit or underscore",
  "「postgres」等系统库名称不能作为 store 数据库": "System databases such as 'postgres' cannot be used as the store database",
  "全局 store 数据库已更新": "Global store database updated",
  "没有需要迁移的旧 store 数据库": "No legacy store databases need migration",
  "旧版本允许每个 multi-agent 使用不同的 store 数据库。现在长期记忆统一存放到：": "Older versions allowed each multi-agent to use a different store database. Long-term memory is now stored in: ",
  "迁移会把原数据库里的记忆复制到当前库，并允许修改「新数据库中的 namespace」。复制并校验成功后，程序会删除原数据库；系统库 postgres 永不删除。": "Migration copies memories from the original database into the current one and lets you edit the namespace in the new database. After copying and verification succeed, the original database is deleted; the system database postgres is never deleted.",
  "原数据库：": "Original database: ",
  "系统库，不删除": "System database; not deleted",
  "使用它的 multi-agent：": "Multi-agents using it: ",
  "没有可迁移的记忆条目。": "No memory entries to migrate.",
  "移除这条迁移记录": "Remove this migration record",
  "稍后再说": "Later",
  "开始迁移": "Start migration",
  "确定移除这条迁移记录吗？只会删除记录，不会删除数据库里的任何数据。": "Remove this migration record? Only the record is deleted; no data in the database is touched.",
  "没有可迁移的记忆条目": "No memory entries to migrate",
  "新的命名空间不能为空": "The new namespace cannot be empty",
  "迁移中…": "Migrating…",
  "即将把记忆复制到当前 store 数据库，并在校验成功后删除原数据库。确定开始迁移吗？": "Memories will be copied into the current store database, and the original database will be deleted after verification. Start migration?",
  "有迁移失败：": "Migration failed: ",
  "迁移完成": "Migration complete",
  "全局embedding模型设置": "Global embedding model settings",
  "使用全局 embedding 模型设置": "Use global embedding model settings",
  "勾选后，本 multi-agent 直接使用主界面「系统设置 → 全局embedding模型设置」里的 embedding 模型、维度、镜像、缓存目录与离线模式；取消勾选可为本 multi-agent 单独设置。": "When checked, this multi-agent uses the embedding model, dimension, mirror, cache directory and offline mode configured under Settings → Global embedding model settings on the main screen. Uncheck to set them separately for this multi-agent.",
  "当前使用的全局 embedding 配置：": "Current global embedding configuration in use:",
  "如需修改，请到主界面齿轮设置 →「全局embedding模型设置」。": "To change it, go to the main screen gear settings → 'Global embedding model settings'.",
  "用途：让所有 multi-agent 共用同一套 embedding（向量化）模型配置，multi-agent 配置页勾选「使用全局 embedding 模型设置」即可，无需每个都重复填写。": "Purpose: let all multi-agents share the same embedding (vectorization) model configuration; just check 'Use global embedding model settings' on each multi-agent page instead of filling it in repeatedly.",
  "embedding 模型用于把文字转成向量、做长期记忆的语义检索。换模型时必须同步修改「embedding 维度」，否则检索会出错；首次使用会自动下载模型。": "The embedding model turns text into vectors for semantic retrieval of long-term memory. When changing models you must update the 'embedding dimension' accordingly, otherwise retrieval will fail; the model is downloaded automatically on first use.",
  "确定清除全局 embedding 模型设置吗？清除后各 multi-agent 将恢复为单独填写的界面。": "Clear the global embedding model settings? After clearing, each multi-agent returns to the individually-filled interface.",
  "已清除全局 embedding 设置": "Global embedding settings cleared",
  "请填写 embedding 模型名": "Please fill in the embedding model name",
  "全局 embedding 设置已保存": "Global embedding settings saved",
  "展开 / 收起说明": "Expand / collapse description",
  "自动阶段性总结阈值": "Auto stage-summary threshold",
  "自动全量总结阈值": "Auto full-summary threshold",
  "自动以及主动全量总结后，需要保留的会话轮数": "Turns to keep after automatic and proactive full summaries",
  "累计 token 达到该值时触发一次阶段性总结。": "A stage summary is triggered once accumulated tokens reach this value.",
  "累计 token 达到该值时清空历史（只保留最近几轮）。": "History is cleared once accumulated tokens reach this value (only the most recent turns are kept).",
  "全局自动（及主动）总结设置": "Global automatic (& proactive) summary settings",
  "使用全局自动（及主动）总结设置": "Use global automatic (& proactive) summary settings",
  "勾选后，本 multi-agent 直接使用主界面「系统设置 → 全局自动（及主动）总结设置」里的自动阶段性总结阈值、自动全量总结阈值与保留轮数；取消勾选可为本 multi-agent 单独设置。": "When checked, this multi-agent uses the auto stage-summary threshold, auto full-summary threshold and turns-to-keep configured under Settings → Global automatic (& proactive) summary settings on the main screen. Uncheck to set them separately for this multi-agent.",
  "当前使用的全局总结配置：": "Current global summary configuration in use:",
  "如需修改，请到主界面齿轮设置 →「全局自动（及主动）总结设置」。": "To change it, go to the main screen gear settings → 'Global automatic (& proactive) summary settings'.",
  "用途：让所有 multi-agent 共用同一套「自动阶段性总结 / 自动全量总结」阈值，multi-agent 配置页勾选「使用全局自动（及主动）总结设置」即可，无需每个都重复填写。": "Purpose: let all multi-agents share the same 'auto stage-summary / auto full-summary' thresholds; just check 'Use global automatic (& proactive) summary settings' on each multi-agent page instead of filling them in repeatedly.",
  "注意：这些阈值会影响图编译；修改后请保存对应的 multi-agent 才会真正生效。": "Note: these thresholds affect graph compilation; after changing them, save the corresponding multi-agent for the change to take effect.",
  "确定清除全局总结设置吗？清除后各 multi-agent 将恢复为单独填写的界面。": "Clear the global summary settings? After clearing, each multi-agent returns to the individually-filled interface.",
  "已清除全局总结设置": "Global summary settings cleared",
  "全局总结设置已保存": "Global summary settings saved",
  "子 agent 累计 token 达到该值时清空历史（只保留最近几轮）。": "The sub-agent clears its history once accumulated tokens reach this value (only the most recent turns are kept).",
  "子 agent 清空历史时保留最近几轮对话。": "Keep the most recent turns when the sub-agent clears history.",
  "使用全局子 agent 总结设置": "Use global sub-agent summary settings",
  "勾选后，本子 agent 直接使用主界面「系统设置 → 全局自动（及主动）总结设置 → 子 agent」里的清空历史阈值与保留轮数；取消勾选可为本子 agent 单独设置。": "When checked, this sub-agent uses the clear-history threshold and turns-to-keep configured under Settings → Global automatic (& proactive) summary settings → Sub-agent on the main screen. Uncheck to set them separately for this sub-agent.",
  "当前使用的全局子 agent 总结配置：": "Current global sub-agent summary configuration in use:",
  "主 agent 与子 agent 各自保留一套：主 agent 用下面「主 agent」三个阈值，子 agent 用「子 agent」两个阈值。注意：这些阈值会影响图编译；修改后请保存对应的 multi-agent 才会真正生效。": "Main agent and sub-agent each keep their own set: the main agent uses the three thresholds under 'Main agent' below, and sub-agents use the two under 'Sub-agent'. Note: these thresholds affect graph compilation; after changing them, save the corresponding multi-agent for the change to take effect.",
  "读取 PDF 时，用基于框线的检测把表格转成 Markdown 并嵌回正文；关闭后只返回正文文本。": "When reading PDFs, use ruled-line detection to convert tables to Markdown and embed them back into the body; when disabled, only the body text is returned.",
  "行": "lines",
  "除发送键外，其余 Enter 组合均换行。": "All Enter combos except the send key insert a newline.",
  "默认配置已保存": "Default configuration saved",
  "（无历史）": "(no history)",
  "（空消息）": "(empty message)",
  "、": ", ",
  "。": ".",
  "转换 HTML": "Export HTML",
  "HTML 输出路径": "HTML output path",
  "HTML 转换配置表": "HTML export config",
  "已生成 HTML": "HTML generated",
  "开启后，浏览历史时每条助手回复下方会出现「转换 HTML」按钮，可将该回复转成可打印的 HTML 文件。": "When enabled, an 'Export HTML' button appears below each assistant reply in history to convert it into a printable HTML file.",
  "生成 HTML 文件的输出目录（绝对路径）。": "Output directory (absolute path) where generated HTML files are saved.",
  "导出 md 文件": "Export MD",
  "已生成 md 文件": "MD file generated",
  "md 输出路径": "MD output path",
  "开启后，浏览历史时每条助手回复下方会出现「导出 md 文件」按钮，可将该回复保存为 Markdown 文件。": "When enabled, an 'Export MD' button appears below each assistant reply in history to save it as a Markdown file.",
  "生成 md 文件的输出目录（绝对路径）。": "Output directory (absolute path) where generated MD files are saved.",
  "显示转换本地md按钮": "Show the \"Convert local md to HTML\" button",
  "开启后，会话页「编辑会话常用prompt」左侧会出现「将本地md转换为HTML」按钮：点它可从本机选择一个 .md 文件，用上面的「HTML 转换配置表」把它转成可打印 HTML，并保存在该 md 文件同目录下（同名 .html；已存在时另存为带时间戳的文件）。": "When enabled, a \"Convert local md to HTML\" button appears to the left of \"Edit session common prompt\" in the chat view. Click it to pick a local .md file; it is converted into a printable HTML with the \"HTML export config\" above and saved next to the md file (same name .html; a timestamped file is created instead if it already exists).",
  "将本地md转换为HTML": "Convert local md to HTML",
  "选择一个本地 .md 文件，用系统设置里的「HTML 转换配置表」把它转成可打印 HTML，保存在该 md 文件所在目录（同名 .html；已存在时另存为带时间戳的文件）。": "Pick a local .md file and convert it into a printable HTML with the \"HTML export config\" from system settings. The HTML is saved next to the md file (same name .html; a timestamped file is created instead if it already exists).",
  "选择要转换为 HTML 的 Markdown 文件": "Select a Markdown file to convert to HTML",
  "请选择 Markdown 文件…": "Select a Markdown file…",
  "已取消": "Cancelled",
  "调整导出 HTML 的纸张、预览与配色。": "Adjust page, preview and color settings of the exported HTML.",
  "纸张": "Page",
  "纸张尺寸": "Page size",
  "上边距": "Top margin",
  "右边距": "Right margin",
  "下边距": "Bottom margin",
  "左边距": "Left margin",
  "预览": "Preview",
  "最大宽度": "Max width",
  "画布背景色": "Canvas color",
  "内边距": "Page padding",
  "配色": "Colors",
  "页面背景": "Page background",
  "链接": "Link",
  "标题 1": "Heading 1",
  "标题 2": "Heading 2",
  "标题 3": "Heading 3",
  "标题 4": "Heading 4",
  "标题 5": "Heading 5",
  "标题 6": "Heading 6",
  "加粗": "Bold",
  "斜体": "Italic",
  "引用文字": "Blockquote text",
  "引用边框": "Blockquote border",
  "水平线": "Horizontal rule",
  "列表标记": "List marker",
  "行内代码背景": "Inline code background",
  "代码块背景": "Code block background",
  "代码块边框": "Code block border",
  "表格边框": "Table border",
  "表头背景": "Table header background",
  "代码·关键字": "Code keyword",
  "代码·字符串": "Code string",
  "代码·注释": "Code comment",
  "代码·数字": "Code number",
  "代码·内建": "Code builtin",
  "代码·函数": "Code function",
  "代码·常量": "Code constant",
  "代码·变量": "Code variable",
  "浏览器交互延迟设置": "Browser interaction delay settings",
  "工具调用结果显示设置": "Tool result display settings",
  "展示完整工具调用结果": "Show full tool result",
  "流式输出": "Streaming output",
  "查看历史消息": "Viewing history messages",
  "最多展示多少行": "Maximum lines to show",
  "最多展示多少字": "Maximum characters to show",
  "超过该行数即截断显示；填 0 表示不限。": "Truncate when the line count exceeds this; 0 means unlimited.",
  "超过该字数即截断显示；填 0 表示不限。与行数上限同时填写时，触发任意一个就截断。": "Truncate when the character count exceeds this; 0 means unlimited. When both limits are set, hitting either one truncates.",
  "设置工具调用结果在「流式输出」和「查看历史消息」中的展示方式：可展示完整结果，或按「最多行数 / 最多字数」截断（0 表示不限；两个上限都填时，触发任意一个就截断）。工具结果若是单行 JSON，会先结构化再展示。": "Configure how tool results are shown in streaming output and when viewing history: show the full result, or truncate by maximum lines / characters (0 means unlimited; when both limits are set, hitting either one truncates). A single-line JSON tool result is pretty-printed before display.",
  "查看 content": "View content",
  "查看本条工具结果 content 字段的完整内容": "View the full content of this tool result's content field",
  "工具结果 content": "Tool result content",
  "…（已按设置截断显示）": "… (truncated by settings)",
  "正在获取当前页面内容，请稍候…": "Fetching the current page content, please wait…",
  "调整 LLM 操作浏览器时各环节的等待时长，单位为秒。数值越小响应越快，但过小可能抓不到刚加载 / 刚跳转的页面；数值越大越稳妥但更慢。修改保存后，下一次浏览器工具调用即生效。": "Tune the wait times (in seconds) at each step of browser interaction. Smaller values respond faster but may miss pages that just loaded or navigated; larger values are safer but slower. Changes take effect on the next browser tool call.",
  "导航与稳定化（对延迟影响最大）": "Navigation & stabilization (biggest latency impact)",
  "页面就绪与标签页": "Page ready & tabs",
  "CDP 命令与探测超时": "CDP command & probe timeouts",
  "浏览器启动": "Browser launch",
  "拟人交互微延迟（每项为 [最小, 最大] 随机秒数）": "Human-like micro delays (each is a random [min, max] in seconds)",
  "导航宽限窗口": "Navigation grace window",
  "仅对『可能触发跳转』的元素（a[href] / 表单提交 / role=link 等）生效：点击后先等导航「开始」的最长时间。此类元素通常立刻开始跳转并提前结束等待，只有迟迟不跳转时才等满。默认 4.0 秒。": "Applies only to elements that may navigate (a[href] / form submit / role=link): the max time to first wait for the navigation to *start* after a click. Such elements usually start navigating at once and end the wait early; only a stalled navigation waits the full window. Default 4.0 s.",
  "非导航点击宽限": "Non-navigating click grace",
  "对『不会触发跳转』的元素（普通按钮 / 图标 / 光标手势等）点击后，等待导航「开始」的短宽限。这类元素几乎不会导航，短等待 + 判稳即可，避免每次点击都空等数秒。默认 0.6 秒。": "A short wait for a navigation to *start* after clicking an element that cannot navigate (plain buttons / icons / cursor:pointer shapes). These almost never navigate, so a short wait plus settling suffices and avoids wasting seconds on every click. Default 0.6 s.",
  "导航提交等待超时": "Navigation commit timeout",
  "确认导航「已开始」后，等待新文档提交（Page.frameNavigated）的最长时间。正常情况下提交是瞬时的，此值只在导航中途失败 / 取消时兜底。默认 2.0 秒。": "After a navigation has *begun*, the max time to wait for the new document to commit (Page.frameNavigated). Commit is normally instant; this only backstops a navigation that fails / is cancelled midway. Default 2.0 s.",
  "导航提交后内容等待": "Content wait after commit",
  "新文档提交后，额外等待 load 事件的上限（若 load 已触发则立即结束）。用于给异步内容一点时间，但不再为『load 迟迟不来』死等。默认 1.5 秒。": "After the new document commits, the extra cap for waiting on the load event (ends immediately if load already fired). Gives async content a little time without blocking forever when load never arrives. Default 1.5 s.",
  "新标签页/空壳加载超时": "New-tab/blank-shell load timeout",
  "新标签页首次就绪（tool_8）以及页面为空壳时等待正文出现的最长时间。不再用于普通点击后的导航等待。默认 15.0 秒。": "The max time to wait for a new tab's first ready (tool_9) and for content to appear when a page is a bare shell. No longer used for navigation waits after ordinary clicks. Default 15.0 s.",
  "DOM 判稳静默窗": "DOM quiet window",
  "页面的 DOM 指纹（readyState + outerHTML 长度）连续保持不变达到这个时长，才认为页面渲染完成。异步渲染（SPA）常需要它兜底；太小会抓到中间态。默认 0.7 秒。": "The page is considered settled only once its DOM fingerprint (readyState + outerHTML length) stays unchanged for this long. Async rendering (SPAs) often needs it; too small may capture an intermediate state. Default 0.7 s.",
  "判稳总超时": "Max settle timeout",
  "等待 DOM 判稳的总上限；超过则不再等待，直接返回当前页面。默认 2.0 秒。": "The overall cap for waiting until the DOM settles; after this it returns the current page without waiting further. Default 2.0 s.",
  "判稳探测间隔": "Settle probe interval",
  "判稳过程中每隔多久探测一次 DOM 指纹。越小判定越及时，但探测命令更频繁。默认 0.15 秒。": "How often to probe the DOM fingerprint while settling. Smaller reacts sooner but sends probes more often. Default 0.15 s.",
  "导航宽限轮询间隔": "Navigation grace poll interval",
  "在「导航宽限窗口」内，每隔多久检查一次导航是否已经开始。默认 0.05 秒。": "How often to check whether a navigation has started, within the navigation grace window. Default 0.05 s.",
  "标签页就绪等待超时": "Tab-ready timeout",
  "切换标签页（tool_4）时，等待该页 readyState 变为 complete 的最长时间。默认 15.0 秒。": "When switching tabs (tool_5), the max time to wait for the tab's readyState to become complete. Default 15.0 s.",
  "标签页就绪轮询间隔": "Tab-ready poll interval",
  "切换标签页时轮询 readyState 的间隔。默认 0.15 秒。": "The readyState polling interval when switching tabs. Default 0.15 s.",
  "新标签页轮询间隔": "New-tab poll interval",
  "打开新标签页（tool_8）时，轮询 readyState 与 location.href 的间隔。默认 0.1 秒。": "When opening a new tab (tool_9), the polling interval for readyState and location.href. Default 0.1 s.",
  "关闭标签页等待超时": "Close-tab timeout",
  "关闭标签页后，等待该标签页真正从目标列表消失的最长时间。默认 3.0 秒。": "After closing a tab, the max time to wait for it to actually disappear from the target list. Default 3.0 s.",
  "关闭标签页轮询间隔": "Close-tab poll interval",
  "关闭标签页时的轮询间隔。默认 0.1 秒。": "The polling interval when closing a tab. Default 0.1 s.",
  "CDP 命令超时": "CDP command timeout",
  "单条 Chrome DevTools Protocol 命令的默认超时。抓取 DOM、注入点击等绝大多数命令都使用它；过小会在慢页面上误报超时。默认 30.0 秒。": "Default timeout for a single Chrome DevTools Protocol command. Used by most commands (DOM capture, click injection, etc.); too small reports false timeouts on slow pages. Default 30.0 s.",
  "短探测超时": "Short probe timeout",
  "轻量探测命令（读取 readyState、hasFocus、主帧树等）的超时，比默认命令超时短。默认 3.0 秒。": "Timeout for lightweight probe commands (reading readyState, hasFocus, main frame tree, etc.), shorter than the default command timeout. Default 3.0 s.",
  "输入事件等待超时": "Input event timeout",
  "鼠标 / 键盘输入事件的单次等待上限。输入是『发了就算数』的最佳努力操作；某台机器上个别事件确认很慢时，超时即继续，避免一次点击被拖成几十秒。默认 2.0 秒。": "Per-event wait cap for mouse / keyboard input. Input is fire-and-forget best-effort; if an individual event's ACK is slow on some machine, the wait times out and continues, so one click is never dragged into tens of seconds. Default 2.0 s.",
  "原始 HTML 读取超时": "Raw HTML read timeout",
  "debug 模式下读取整页原始 HTML 的超时，仅影响调试日志。默认 20.0 秒。": "Timeout for reading the full raw HTML in debug mode; only affects debug logs. Default 20.0 s.",
  "启动就绪总超时": "Launch-ready timeout",
  "启动浏览器后，等待 DevToolsActivePort 端口文件与 CDP 服务就绪的总超时。机器慢或安全软件拦截时可调大。默认 25.0 秒。": "After launching the browser, the overall timeout for the DevToolsActivePort file and CDP service to become ready. Increase on slow machines or when security software interferes. Default 25.0 s.",
  "端口文件轮询间隔": "Port-file poll interval",
  "启动时轮询 DevToolsActivePort 文件的间隔。默认 0.1 秒。": "The polling interval for the DevToolsActivePort file during launch. Default 0.1 s.",
  "CDP 服务轮询间隔": "CDP service poll interval",
  "启动时轮询 /json/version 等待 CDP 服务就绪的间隔。默认 0.2 秒。": "The polling interval for /json/version while waiting for the CDP service during launch. Default 0.2 s.",
  "复用浏览器就绪超时": "Reuse-ready timeout",
  "复用上一次已启动的浏览器时，等待其 CDP 就绪的超时；超时则改为重新启动。默认 2.0 秒。": "When reusing a previously launched browser, the timeout for its CDP to become ready; on timeout it launches a fresh one. Default 2.0 s.",
  "端口探测超时": "Port probe timeout",
  "检测浏览器端口是否存活（GET /json/version）的单次超时，也用于浮动按钮的连接状态轮询。默认 1.0 秒。": "Single-attempt timeout for checking whether the browser port is alive (GET /json/version); also used by the floating button's connection polling. Default 1.0 s.",
  "鼠标移动每步间隔": "Mouse move per-step interval",
  "拟人移动鼠标时，每个中间点的间隔范围（随机取值）。减小可加快点击，但过小可能被反爬识别。默认 0.008 ~ 0.025 秒。": "Interval range between each intermediate point when moving the mouse human-likely (random). Smaller speeds up clicks but may be detected by anti-bot. Default 0.008 ~ 0.025 s.",
  "元素定位后停顿": "Post-locate settle",
  "把元素滚动进视口后、获取其几何信息前的停顿。默认 0.05 ~ 0.12 秒。": "Pause after scrolling an element into view and before reading its geometry. Default 0.05 ~ 0.12 s.",
  "点击悬停停顿": "Click hover settle",
  "鼠标移到目标上后、按下鼠标之前的停顿。默认 0.04 ~ 0.12 秒。": "Pause after moving onto the target and before pressing. Default 0.04 ~ 0.12 s.",
  "按下到松开停顿": "Press-to-release settle",
  "鼠标按下与松开之间的停顿。默认 0.05 ~ 0.12 秒。": "Pause between mouse press and release. Default 0.05 ~ 0.12 s.",
  "输入聚焦停顿": "Input focus settle",
  "点击输入框聚焦后、开始输入前的停顿。默认 0.08 ~ 0.18 秒。": "Pause after clicking to focus an input and before typing. Default 0.08 ~ 0.18 s.",
  "清空输入框停顿": "Input clear settle",
  "清空输入框原有内容后的停顿。默认 0.05 ~ 0.10 秒。": "Pause after clearing an input's existing content. Default 0.05 ~ 0.10 s.",
  "逐字符输入间隔": "Per-character typing interval",
  "逐字符输入时，每个字符之间的间隔。长文本会明显累加耗时，是输入类操作最主要的延迟来源。默认 0.03 ~ 0.12 秒。": "Interval between characters when typing. Long text accumulates noticeably; this is the main latency source for input actions. Default 0.03 ~ 0.12 s.",
  "拖动按下停顿": "Drag press settle",
  "拖动时在起点按下鼠标前后的停顿。默认 0.05 ~ 0.12 秒。": "Pause around pressing the mouse at the drag start point. Default 0.05 ~ 0.12 s.",
  "拖动移动每步间隔": "Drag move per-step interval",
  "拖动过程中鼠标移动每个中间点的间隔。默认 0.01 ~ 0.03 秒。": "Interval between each intermediate point while dragging. Default 0.01 ~ 0.03 s.",
  "滚动步间停顿": "Scroll step settle",
  "每次滚动（滚轮 / 整页）后等待页面响应的停顿；滚动类操作可能连续多步，累加后较可观。默认 0.15 ~ 0.35 秒。": "Pause after each scroll (wheel / full page) to let the page respond; scrolling may run several steps, so it adds up. Default 0.15 ~ 0.35 s.",
  "快照": "Snapshots",
  "快照列表": "Snapshot list",
  "暂无快照": "No snapshots yet",
  "条消息": "messages",
  "确定删除快照": "Delete snapshot?",
  "查看已删除multi-agent的快照": "View snapshots of deleted multi-agents",
  "暂无已删除的multi-agent": "No deleted multi-agents",
  "个快照": "snapshots",
  "删除全部快照": "Delete all snapshots",
  "确定删除该 multi-agent 的全部快照": "Delete all snapshots of this multi-agent?",
  "等待确认…": "Awaiting confirmation…",
  "历史消息任意时间可编辑": "Edit any historical message",
  "开启编辑模式": "Enable edit mode",
  "开启后，会话界面右上角会出现「进入编辑模式」按钮，可编辑历史消息。": "When enabled, an 'Enter Edit Mode' button appears at the top-right of the chat view to edit historical messages.",
  "开启后，无论多久远的历史消息都能进入编辑模式编辑；关闭时只编辑最近一轮对话。": "When enabled, messages of any age can be edited in edit mode; when disabled, only the latest turn is editable.",
  "历史消息所有类型可编辑": "Edit all message types",
  "开启后，用户消息、思考过程、工具调用、工具结果都能编辑；关闭时只编辑 AI 回复正文。": "When enabled, user messages, reasoning, tool calls and tool results can all be edited; when disabled, only AI reply text is editable.",
  "进入编辑模式": "Enter Edit Mode",
  "退出编辑模式": "Exit Edit Mode",
  "编辑模式": "Edit Mode",
  "确认改动": "Apply",
  "撤销改动": "Undo",
  "是否提交对历史消息的更改": "Submit changes to the historical messages?",
  "更改已生效": "Changes applied",
  "编辑历史消息失败": "Failed to edit history message",
  "写入中": "Writing…",
  "加载模型中": "Loading model…",
  "写入失败，失败原因：": "Write failed. Reason: ",
  "写入超时（超过 30 秒未完成），请重试。": "Write timed out (over 30 seconds). Please retry.",
  "无法连接到服务端，请确认程序仍在运行后重试。": "Cannot connect to the server. Please make sure the app is still running and retry.",
  "连接已断开，写入未完成。": "Connection closed; the write was not completed.",
  "（已停止提交剩余 {n} 处改动）": " (stopped submitting the remaining {n} change(s))",
  "关闭": "Close",
  "未知错误": "Unknown error",
  "当前处于编辑模式，请先退出编辑模式后再离开。是否退出编辑模式？": "You are in edit mode. Exit edit mode before leaving?",
  "查看/编辑记忆库": "View / Edit Memory Store",
  "刷新": "Refresh",
  "重试": "Retry",
  "数据库服务正在启动，请稍候…": "The database service is starting. Please wait…",
  "程序正在为你加载数据库服务，通常很快；电脑配置较低时可能需要 1～2 分钟。就绪后会自动进入，请不要关闭窗口。": "The app is loading the database service. This is usually quick; on slower computers it may take 1–2 minutes. The interface will open automatically once ready — please don't close this window.",
  "已等待 {sec} 秒": "Waited {sec}s",
  "暂无记忆": "No memories yet",
  "更新于": "Updated at",
  "编辑记忆": "Edit Memory",
  "主题": "Subject",
  "内容": "Content",
  "主题不能为空": "Subject cannot be empty",
  "确定删除该条记忆？此操作不可撤销。": "Delete this memory? This cannot be undone.",
  "删除全部历史消息": "Delete all history messages",
  "删除全部历史消息后不可恢复。可先归档为快照再删除，或直接删除。": "Deleted history cannot be restored. You can archive it as a snapshot first and then delete, or delete directly.",
  "归档为快照后删除": "Archive as snapshot, then delete",
  "直接删除": "Delete directly",
  "已归档为快照（{n} 条消息）": "Archived as snapshot ({n} messages)",
  "归档快照失败，已取消删除": "Failed to archive snapshot; deletion cancelled",
  "删除全部历史消息失败": "Failed to delete all history messages",
  "namespace": "namespace",
  "唯一标识：同时用作 agent_id（配置文件名 configs/&lt;名称&gt;.json）与 checkpoint 数据库名。创建后不可更改；不能与其他 multi-agent 重名。": "Unique identity: used both as agent_id (config file configs/<name>.json) and as the checkpoint database name. Cannot be changed after creation; must be unique across multi-agents.",
  "该 multi-agent 的内部标识（agent_id / checkpoint 数据库）仍与名称不一致：程序下次启动会自动改名；如果一直不变，请查看 logs/server.log 或 configs/identity_migration.json。": "This multi-agent's internal identifiers (agent_id / checkpoint database) still differ from its name. The app will try to rename them on the next start; if this persists, check logs/server.log or configs/identity_migration.json.",
  "请填写长期记忆分组": "Please fill in the long-term memory group",
  "长期记忆分组的单个层级不能包含点号，请用逗号分隔层级（例如 teamA,projectX）": "A single memory group level cannot contain a dot; separate levels with commas (e.g. teamA,projectX)",
  "名称不能是「.」或「..」": "The name cannot be '.' or '..'",
  "名称不能以点开头（会变成隐藏文件）": "The name cannot start with a dot (it would become a hidden file)",
  "「postgres」等系统库名称不能作为 multi-agent 名称": "System database names such as 'postgres' cannot be used as a multi-agent name",
  "「settings」等程序内部配置文件名不能作为 multi-agent 名称": "Internal config file names such as 'settings' cannot be used as a multi-agent name",
  "名称不能以点或空格结尾（Windows 文件名限制）": "The name cannot end with a dot or space (Windows filename restriction)",
  "名称不能包含这些字符：": "The name cannot contain these characters: ",
  "（名称会同时用作文件名与数据库名）": " (the name is used as both the file name and the database name)",
  "名称过长：最多 63 字节（约 21 个汉字或 63 个英文字符），因为名称会直接作为 checkpoint 数据库名。": "Name too long: at most 63 bytes (about 21 Chinese characters or 63 English characters), because the name is used directly as the checkpoint database name.",
  "服务器：": "Server: ",
  "数据库：": "Database: ",
  "（未填写）": "(not filled in)",
  "（填写名称后自动生成）": "(generated after you enter a name)",
  "服务器只由连接前缀里的 host:port 决定；同一 host:port 才是同一台 PostgreSQL。pgAdmin 里的「服务器」名字只是连接配置的显示名，不改变数据位置。": "The server is determined only by host:port in the connection prefix; only the same host:port is the same PostgreSQL instance. The 'server' names in pgAdmin are just display names for connection profiles and do not change where data lives.",
  "连接串里的数据库名": "The database name in the connection string",
  "会被忽略：实际库名由名称自动生成。": " is ignored: the actual database name is generated from the name.",
  "按当前填写内容实时拼出的完整连接串（密码已脱敏）。数据库名一栏只是示例，实际由名称自动生成。": "Connection string composed live from the current inputs (password masked). The database name shown is only a sample; the actual one is generated from the name.",
  "连接前缀 = 连接串里「数据库名」前面的全部内容：协议://用户名:密码@主机:端口/。<br/><br/>例如 postgresql://user:passwd@localhost:5432/<br/><br/>它回答「连到哪台数据库服务器、用哪个账号」。注意结尾的斜杠 / 不能少；数据库名由名称自动生成，不需要在这里填。": "Connection prefix = everything before the database name in the connection string: protocol://user:password@host:port/.<br/><br/>e.g. postgresql://user:passwd@localhost:5432/<br/><br/>It answers 'which database server, with which account'. The trailing slash / is required; the database name is generated from the name, so it is not needed here.",
  "直接粘贴数据库服务商提供的整条连接串（PostgreSQL 官方 URI 格式）。<br/><br/>例如 postgresql://user:passwd@localhost:5432/mydb?sslmode=disable<br/><br/>程序只从中拆出连接前缀、连接后缀；其中的数据库名会被忽略，实际库名由名称自动生成。": "Paste the whole connection string provided by your database vendor (official PostgreSQL URI format).<br/><br/>e.g. postgresql://user:passwd@localhost:5432/mydb?sslmode=disable<br/><br/>The app only extracts the connection prefix and suffix; the database name in it is ignored and the actual database is derived from the name.",
  "勾选后，本 multi-agent 直接使用主界面「系统设置 → 全局postgres连接设置」里的连接前缀与连接后缀（主机 / 账号 / 密码 / SSL）；checkpoint 数据库名仍由名称自动生成。取消勾选可为本 multi-agent 单独设置连接。": "When checked, this multi-agent uses the connection prefix and suffix (host / account / password / SSL) configured under Settings → Global PostgreSQL connection settings on the main screen; the checkpoint database name is still derived from the name. Uncheck to set the connection separately for this multi-agent.",
  "用途：让所有 multi-agent 共用同一套数据库连接（主机、账号、密码、SSL）；checkpoint 数据库名由每个 multi-agent 的名称自动生成（保存时程序会自动建库），长期记忆统一使用全局 store 数据库。": "Purpose: let all multi-agents share the same database connection (host, account, password, SSL). The checkpoint database name is derived from each multi-agent's name (the app creates it automatically on save), and long-term memory always uses the global store database.",
  "所以这里只需填「前缀 + 后缀」即可，checkpoint 数据库名由每个 multi-agent 的名称自动生成；也可以直接粘贴整条连接串，程序会自动帮你切出前缀与后缀（其中的数据库名会被忽略）。": "So you only need to fill in 'prefix + suffix' here; each multi-agent's checkpoint database name is derived from its name. Alternatively, paste the whole connection string and the app extracts the prefix and suffix automatically (the database name in it is ignored).",
  "迁移前会先按连接前缀识别服务器（host:port）：同一 host:port 才是同一台 PostgreSQL；pgAdmin 里的「服务器」名字只是连接配置的显示名，不改变数据位置。": "Before migrating, the app identifies the server from the connection prefix (host:port): only the same host:port is the same PostgreSQL instance. The 'server' names in pgAdmin are just display names for connection profiles and do not change where data lives.",
  "（未知）": "(unknown)",
  "正在进行的对话": "Ongoing conversations",
  "查看正在进行的对话": "View ongoing conversations",
  "当前有 {a} 个对话正在进行": "{a} conversation(s) in progress",
  "当前有 {a} 个对话正在进行，{b} 个已经完工": "{a} conversation(s) in progress, {b} completed",
  "正在进行": "In progress",
  "已经完工": "Completed",
  "任务完成": "Completed",
  "该会话正在进行": "This conversation is in progress",
  "该会话正在运行或等待验收，暂时不能隐藏或删除": "This conversation is running or awaiting acceptance and cannot be hidden or deleted yet.",
  "该会话的浏览器已打开（每个会话使用独立浏览器实例）": "This conversation's browser is open (each conversation uses an independent browser instance)",
  "该会话正在运行或等待验收，暂时不能删除。请等它完成并验收后再删除。": "This conversation is running or awaiting acceptance and cannot be deleted yet. Please wait until it is completed and accepted.",
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
    let body = null;
    try { body = await res.json(); } catch (e) {}
    const err = new Error((body && body.detail) || res.statusText);
    err.status = res.status;
    if (body) { err.code = body.code; err.retryable = body.retryable; }
    throw err;
  }
  return res.json();
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function brk(s) { return esc(s).replace(/\n/g, "<br>"); }

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

/* ================= 浮层（弹窗）可拖动调整大小 / 移动 / 位置尺寸落盘 =================
   需求：所有可弹出的浮层都能拖动边缘改变大小——左上角拖动处管“上 / 左”，
   右下角拖动处管“右 / 下”；也可拖动标题移动位置。尺寸与位置持久记忆
   （存 localStorage，由 ui_prefs_bridge 落盘到后端），下次打开保持。
   位置以“占可移动范围（视口尺寸 - 浮层尺寸）的比例”存储，因此整个窗口放大/缩小时，
   浮层上下左右边距的比例保持不变、位置稳定，且始终被钳制在视口内、不会显示不全。 */
const MODAL_GEOM_KEY = "modal-geom";
const MODAL_MIN_W = 220, MODAL_MIN_H = 120, MODAL_MARGIN = 8;

function loadModalGeomMap() {
  try {
    const v = JSON.parse(localStorage.getItem(MODAL_GEOM_KEY) || "{}");
    return (v && typeof v === "object") ? v : {};
  } catch (e) { return {}; }
}
function saveModalGeom(key, geom) {
  if (!key) return;
  try {
    const map = loadModalGeomMap();
    map[key] = geom;
    localStorage.setItem(MODAL_GEOM_KEY, JSON.stringify(map));
  } catch (e) { /* ignore */ }
}

const _floatingPanels = [];

function enableFloatingResize(el, key, opts) {
  if (!el || el.dataset.floatingResize === "1") return;
  el.dataset.floatingResize = "1";
  opts = opts || {};
  const minW = opts.minW || MODAL_MIN_W;
  const minH = opts.minH || MODAL_MIN_H;
  const margin = MODAL_MARGIN;
  const vw = () => document.documentElement.clientWidth;
  const vh = () => document.documentElement.clientHeight;

  const stored = loadModalGeomMap()[key];
  let geom = (stored && typeof stored === "object") ? Object.assign({}, stored) : null;
  let curW = 0, curH = 0, curLeft = 0, curTop = 0;
  let materialized = false;

  const tl = document.createElement("div");
  tl.className = "float-resize-handle float-resize-tl";
  tl.title = t("拖动调整大小");
  const br = document.createElement("div");
  br.className = "float-resize-handle float-resize-br";
  br.title = t("拖动调整大小");
  el.appendChild(tl);
  el.appendChild(br);

  const syncHandles = () => {
    let left, top, right, bottom;
    if (materialized) { left = curLeft; top = curTop; right = curLeft + curW; bottom = curTop + curH; }
    else {
      const r = el.getBoundingClientRect();
      left = r.left; top = r.top; right = r.right; bottom = r.bottom;
    }
    tl.style.left = Math.round(left) + "px";
    tl.style.top = Math.round(top) + "px";
    br.style.left = Math.round(right) + "px";
    br.style.top = Math.round(bottom) + "px";
  };

  const apply = () => {
    el.style.position = "fixed";
    el.style.margin = "0";
    el.style.maxWidth = "none";
    el.style.maxHeight = "none";
    el.style.right = "auto";
    el.style.bottom = "auto";
    el.style.width = Math.round(curW) + "px";
    el.style.height = Math.round(curH) + "px";
    el.style.left = Math.round(curLeft) + "px";
    el.style.top = Math.round(curTop) + "px";
    syncHandles();
  };

  const layoutFromGeom = () => {
    const VW = vw(), VH = vh();
    const maxW = Math.max(minW, VW - 2 * margin);
    const maxH = Math.max(minH, VH - 2 * margin);
    curW = Math.max(minW, Math.min(geom.w || maxW, maxW));
    curH = Math.max(minH, Math.min(geom.h || maxH, maxH));
    const freeX = Math.max(0, VW - curW);
    const freeY = Math.max(0, VH - curH);
    const fx = typeof geom.fx === "number" ? geom.fx : 0.5;
    const fy = typeof geom.fy === "number" ? geom.fy : 0.5;
    curLeft = fx * freeX;
    curTop = fy * freeY;
    curLeft = Math.max(margin, Math.min(curLeft, Math.max(margin, VW - curW - margin)));
    curTop = Math.max(margin, Math.min(curTop, Math.max(margin, VH - curH - margin)));
    materialized = true;
    apply();
  };

  const capture = () => {
    const VW = vw(), VH = vh();
    const freeX = Math.max(0, VW - curW);
    const freeY = Math.max(0, VH - curH);
    geom = {
      w: Math.round(curW), h: Math.round(curH),
      fx: freeX ? Math.min(Math.max(0, curLeft / freeX), 1) : 0,
      fy: freeY ? Math.min(Math.max(0, curTop / freeY), 1) : 0,
    };
  };

  const materialize = () => {
    if (materialized) return;
    const r = el.getBoundingClientRect();
    const VW = vw(), VH = vh();
    curW = Math.max(minW, Math.min(r.width || minW, VW - 2 * margin));
    curH = Math.max(minH, Math.min(r.height || minH, VH - 2 * margin));
    curLeft = Math.min(Math.max(margin, r.left), Math.max(margin, VW - curW - margin));
    curTop = Math.min(Math.max(margin, r.top), Math.max(margin, VH - curH - margin));
    capture();
    materialized = true;
    apply();
  };

  const startDrag = (e, mode) => {
    if (e.button != null && e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    materialize();
    const sx = e.clientX, sy = e.clientY;
    const s = { left: curLeft, top: curTop, w: curW, h: curH };
    const onMove = (ev) => {
      const dx = ev.clientX - sx, dy = ev.clientY - sy;
      const VW = vw(), VH = vh();
      if (mode === "move") {
        curW = s.w; curH = s.h;
        curLeft = Math.min(Math.max(margin, s.left + dx), Math.max(margin, VW - s.w - margin));
        curTop = Math.min(Math.max(margin, s.top + dy), Math.max(margin, VH - s.h - margin));
      } else if (mode === "tl") {
        const right = s.left + s.w, bottom = s.top + s.h;
        curLeft = Math.min(Math.max(margin, s.left + dx), Math.max(margin, right - minW));
        curTop = Math.min(Math.max(margin, s.top + dy), Math.max(margin, bottom - minH));
        curW = right - curLeft;
        curH = bottom - curTop;
      } else { // br
        curLeft = s.left; curTop = s.top;
        curW = Math.min(Math.max(minW, s.w + dx), Math.max(minW, VW - margin - s.left));
        curH = Math.min(Math.max(minH, s.h + dy), Math.max(minH, VH - margin - s.top));
      }
      apply();
    };
    const onUp = () => {
      document.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerup", onUp);
      document.removeEventListener("pointercancel", onUp);
      document.body.classList.remove("floating-dragging");
      capture();
      saveModalGeom(key, geom);
    };
    document.addEventListener("pointermove", onMove);
    document.addEventListener("pointerup", onUp);
    document.addEventListener("pointercancel", onUp);
    document.body.classList.add("floating-dragging");
  };

  tl.addEventListener("pointerdown", (e) => startDrag(e, "tl"));
  br.addEventListener("pointerdown", (e) => startDrag(e, "br"));

  const dragBy = opts.dragBy || "h3";
  const title = dragBy ? el.querySelector(dragBy) : null;
  if (title) {
    title.classList.add("float-drag-handle");
    if (!title.title) title.title = t("拖动移动");
    title.addEventListener("pointerdown", (e) => {
      if (e.target.closest("button, a, input, select, textarea, .info-icon")) return;
      startDrag(e, "move");
    });
  }

  const onWindowResize = () => {
    if (!el.isConnected) return;
    if (materialized && geom) layoutFromGeom();
    else syncHandles();
  };
  _floatingPanels.push({ el, onWindowResize });

  if (window.ResizeObserver) {
    try {
      new ResizeObserver(() => { if (!materialized && el.isConnected) syncHandles(); }).observe(el);
    } catch (e) { /* ignore */ }
  }

  if (geom && geom.w > 0 && geom.h > 0) {
    layoutFromGeom();
  } else if (opts.materialize) {
    materialize();
    saveModalGeom(key, geom);
  } else {
    syncHandles();
  }
}

window.addEventListener("resize", () => {
  for (let i = _floatingPanels.length - 1; i >= 0; i--) {
    const p = _floatingPanels[i];
    if (!p.el.isConnected) { _floatingPanels.splice(i, 1); continue; }
    p.onWindowResize();
  }
});

/* 自动为任何插入到 body 的 .modal-mask > .modal 启用上述能力（无需在每个弹窗里手动接线）。
   每个弹窗用 .modal 上的 data-modal-key 区分，以便分别记忆尺寸与位置。 */
const _floatingModalObserver = new MutationObserver((muts) => {
  for (const mut of muts) {
    if (!mut.addedNodes) continue;
    mut.addedNodes.forEach((n) => {
      if (n.nodeType !== 1 || !n.classList || !n.classList.contains("modal-mask")) return;
      const el = n.querySelector(".modal");
      if (!el) return;
      const key = el.dataset.modalKey || ("modal:" + (el.className || "").trim());
      enableFloatingResize(el, key, {});
    });
  }
});
if (document.body) _floatingModalObserver.observe(document.body, { childList: true });

/* 通用只读内容查看弹窗：在当前视窗内弹出，展示消息注入块 / 记忆附着等全文。
   bodyHtml 需为已转义好的 HTML；onClose 用于复位触发按钮的高亮态。 */
function showContentModal(title, bodyHtml, onClose) {
  const mask = document.createElement("div");
  mask.className = "modal-mask";
  mask.innerHTML = `<div class="modal content-view-modal" data-modal-key="contentView">
    <h3>${title || ""}</h3>
    <div class="modal-body content-view-body">${bodyHtml || ""}</div>
    <div class="modal-actions"><button class="btn primary" data-close>${t("关闭")}</button></div>
  </div>`;
  document.body.appendChild(mask);
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    mask.remove();
    if (onClose) onClose();
  };
  mask.querySelector("[data-close]").onclick = close;
  mask.addEventListener("click", (e) => { if (e.target === mask) close(); });
  return mask;
}

/* ================= 记忆库（查看 / 编辑） ================= */

function fmtMemoryTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return String(iso);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

async function openMemoryStoreModal() {
  if (!S.agentId) return;
  const mask = document.createElement("div");
  mask.className = "modal-mask";
  mask.innerHTML = `<div class="modal memory-modal" data-modal-key="memoryStore">
    <h3>🧠 ${t("查看/编辑记忆库")}</h3>
    <div class="modal-body memory-list" id="memoryList"><div class="muted">${t("加载中…")}</div></div>
    <div class="modal-actions">
      <button class="btn" id="memoryRefresh">${t("刷新")}</button>
      <button class="btn primary" data-close>${t("关闭")}</button>
    </div>
  </div>`;
  document.body.appendChild(mask);
  const close = () => mask.remove();
  mask.querySelector("[data-close]").onclick = close;
  mask.addEventListener("click", (e) => { if (e.target === mask) close(); });

  const listEl = mask.querySelector("#memoryList");
  const load = async () => {
    listEl.innerHTML = `<div class="muted">${t("加载中…")}</div>`;
    try {
      const items = await api(`/api/agents/${encodeURIComponent(S.agentId)}/memories`);
      renderMemoryList(items, listEl, load);
    } catch (e) {
      listEl.innerHTML = `<div class="muted">${brk(e.message)}</div>`;
    }
  };
  mask.querySelector("#memoryRefresh").onclick = load;
  await load();
}

function renderMemoryList(items, root, reload) {
  root.innerHTML = "";
  if (!items || !items.length) {
    root.innerHTML = `<div class="muted">${t("暂无记忆")}</div>`;
    return;
  }
  const list = document.createElement("div");
  list.className = "memory-items";
  items.forEach((it) => {
    const entries = Object.entries(it.value || {});
    const subject = entries.length ? entries[0][0] : "";
    const content = entries.length ? String(entries[0][1]) : "";
    const row = document.createElement("div");
    row.className = "memory-item";
    row.innerHTML = `
      <div class="memory-item-main">
        <div class="memory-subject">${esc(subject)}</div>
        <div class="memory-content">${esc(content)}</div>
        <div class="memory-meta">${t("更新于")} ${esc(fmtMemoryTime(it.updated_at))}</div>
      </div>
      <div class="memory-item-actions">
        <button class="btn small" data-act="edit">${t("编辑")}</button>
        <button class="btn small danger" data-act="del">${t("删除")}</button>
      </div>`;
    row.querySelector('[data-act="del"]').onclick = async () => {
      const ans = await askConfirm(t("确定删除该条记忆？此操作不可撤销。"));
      if (ans !== "yes") return;
      try {
        await api(`/api/agents/${encodeURIComponent(S.agentId)}/memories/${encodeURIComponent(it.key)}`, { method: "DELETE" });
        toast(t("已删除"));
        await reload();
      } catch (e) { toast(e.message, true); }
    };
    row.querySelector('[data-act="edit"]').onclick = () => {
      openMemoryEditModal(subject, content, async (newSubject, newContent) => {
        try {
          await api(`/api/agents/${encodeURIComponent(S.agentId)}/memories/${encodeURIComponent(it.key)}`, {
            method: "PUT",
            body: JSON.stringify({ value: { [newSubject]: newContent } }),
          });
          toast(t("已保存"));
          await reload();
        } catch (e) { toast(e.message, true); }
      });
    };
    list.appendChild(row);
  });
  root.appendChild(list);
}

function openMemoryEditModal(subject, content, onSave) {
  const mask = document.createElement("div");
  mask.className = "modal-mask";
  mask.innerHTML = `<div class="modal memory-edit-modal" data-modal-key="memoryEdit">
    <h3>${t("编辑记忆")}</h3>
    <label class="memory-field-label">${t("主题")}</label>
    <input class="memory-edit-subject" type="text" value="${esc(subject)}">
    <label class="memory-field-label">${t("内容")}</label>
    <textarea class="memory-edit-content" rows="7">${esc(content)}</textarea>
    <div class="modal-actions">
      <button class="btn" data-cancel>${t("取消")}</button>
      <button class="btn primary" data-save>${t("保存")}</button>
    </div>
  </div>`;
  document.body.appendChild(mask);
  let closed = false;
  const close = () => { if (!closed) { closed = true; mask.remove(); } };
  mask.querySelector("[data-cancel]").onclick = close;
  mask.addEventListener("click", (e) => { if (e.target === mask) close(); });
  mask.querySelector("[data-save]").onclick = async () => {
    const newSubject = mask.querySelector(".memory-edit-subject").value.trim();
    const newContent = mask.querySelector(".memory-edit-content").value;
    if (!newSubject) { toast(t("主题不能为空"), true); return; }
    await onSave(newSubject, newContent);
    close();
  };
}


/* 记忆附着包装标记（与后端 app/runtime/prompts.py 保持一致）：流式回放的用户消息
 * 可能带这些包装，前端拆出真实原文与附着记忆，给用户消息框加「记忆附着」按钮。 */
const USER_MSG_PREFIX = "用户此次发送的信息如下：\n";
const MEMORY_ATTACH_MARKER = "\n与用户此次发送信息相关联的记忆如下：";
function splitUserMemory(raw) {
  const s = String(raw ?? "");
  if (!s.startsWith(USER_MSG_PREFIX)) return { text: s, memory: "" };
  const rest = s.slice(USER_MSG_PREFIX.length);
  const i = rest.indexOf(MEMORY_ATTACH_MARKER);
  if (i < 0) return { text: rest, memory: "" };
  return { text: rest.slice(0, i), memory: rest.slice(i + MEMORY_ATTACH_MARKER.length).trim() };
}

/* 工具调用结果显示（与后端 app/services/history_render.py 的行为保持一致）：
 * - 单行 JSON → 先结构化（tab 缩进）再展示；
 * - 按「工具调用结果显示设置」截断（full / 最多行数 / 最多字数）；
 * - 浏览器工具结果若含 content 字段，附「查看 content」按钮（base64 携带，点击弹窗）。 */
const BROWSER_TOOL_NAME_RE = /^tool_\d+_/;
function toolResultDisplayCfg(kind) {
  const s = settingsCache || {};
  if (kind === "stream") {
    return {
      full: s.tool_result_stream_full !== false,
      maxLines: (+s.tool_result_stream_max_lines) || 0,
      maxChars: (+s.tool_result_stream_max_chars) || 0,
    };
  }
  return {
    full: s.tool_result_history_full === true,
    maxLines: (+s.tool_result_history_max_lines) || 0,
    maxChars: (+s.tool_result_history_max_chars) || 0,
  };
}
function tryParseSingleLineJson(text) {
  const s = String(text ?? "").trim();
  if (!s || s.indexOf("\n") >= 0) return null;
  if (!(s.startsWith("{") || s.startsWith("["))) return null;
  try { return JSON.parse(s); } catch (e) { return null; }
}
function formatToolResultText(raw, cfg) {
  let display = String(raw ?? "");
  const obj = tryParseSingleLineJson(display);
  if (obj !== null) { try { display = JSON.stringify(obj, null, "\t"); } catch (e) {} }
  let truncated = false;
  if (!cfg.full) {
    if (cfg.maxChars > 0 && display.length > cfg.maxChars) { display = display.slice(0, cfg.maxChars); truncated = true; }
    if (cfg.maxLines > 0) {
      const lines = display.split("\n");
      if (lines.length > cfg.maxLines) { display = lines.slice(0, cfg.maxLines).join("\n"); truncated = true; }
    }
  }
  return { display, truncated, obj };
}
function buildToolResultHtml(name, raw) {
  const cfg = toolResultDisplayCfg("stream");
  const { display, truncated, obj } = formatToolResultText(raw, cfg);
  let btn = "";
  if (obj && typeof obj === "object" && BROWSER_TOOL_NAME_RE.test(String(name)) && typeof obj.content === "string") {
    const human = obj.content.replace(/\\r\\n/g, "\n").replace(/\\n/g, "\n").replace(/\\t/g, "\t");
    btn = `<button type="button" class="tool-content-btn" data-tool-content-b64="${utf8ToB64(human)}" title="${t("查看本条工具结果 content 字段的完整内容")}">📄 ${t("查看 content")}</button>`;
  }
  const body = esc(display).replace(/\r\n/g, "\n").replace(/\n/g, "<br>");
  const truncNote = truncated ? `<div class="tool-result-trunc">${t("…（已按设置截断显示）")}</div>` : "";
  const head = `✅ ${t("工具结果")} (<code>${esc(name)}</code>)`;
  return `<div class="tool-result-wrap"><div class="tool-result-body">${btn}<div class="tool-result-head">${head}</div><div class="tool-result-text">${body}</div>${truncNote}</div></div>`;
}

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

/* 确认弹窗
   opts.buttons 可自定义按钮（按数组顺序从左到右渲染）：
     primary = 蓝底白字主按钮；soft = 浅蓝底蓝字按钮；danger = 红色文字危险按钮；
     都不传 = 普通按钮。
     [{ v: "no", label: t("否") }, { v: "archive", label: t("归档为快照后删除"), soft: true },
      { v: "delete", label: t("直接删除"), primary: true }]
   不传时保持原样「否 / 是」，返回值是对应按钮的 v。 */
function askConfirm(prompt, opts) {
  return new Promise(resolve => {
    const pink = opts && opts.pink;
    const buttons = (opts && opts.buttons) || [
      { v: "no", label: t("否") },
      { v: "yes", label: t("是"), primary: true },
    ];
    const btnHtml = buttons.map(b =>
      `<button class="btn${b.primary ? " primary" : ""}${b.soft ? " soft" : ""}${b.danger ? " danger" : ""}" data-v="${esc(b.v)}">${esc(b.label)}</button>`
    ).join("");
    const mask = document.createElement("div");
    mask.className = "modal-mask";
    mask.innerHTML = `
      <div class="modal${pink ? " modal-pink" : ""}" data-modal-key="confirm">
        <h3>${t("确认")}</h3>
        <div class="modal-body">${esc(prompt)}</div>
        <div class="modal-actions">
          ${btnHtml}
        </div>
      </div>`;
    document.body.appendChild(mask);
    mask.querySelectorAll("[data-v]").forEach(b => b.onclick = () => { resolve(b.dataset.v); mask.remove(); });
  });
}

/* 主动总结：百分比设置弹窗（输入框 + 滑条 + 压缩前后 token 预览）
   返回用户选定的百分比；取消返回 null。 */
function askSummaryPercent(defaultPercent, totalTokens) {
  return new Promise(resolve => {
    const min = 1, max = 100;
    const clamp = v => Math.min(max, Math.max(min, Math.round(+v || 0)));
    let pct = clamp(defaultPercent || 20);
    const fmt = n => Number(n || 0).toLocaleString();
    const targetOf = p => Math.round((+totalTokens || 0) * p / 100);
    const mask = document.createElement("div");
    mask.className = "modal-mask";
    mask.innerHTML = `
      <div class="modal" style="width:440px;" data-modal-key="summaryPercent">
        <h3>📝 ${t("主动全量总结")}</h3>
        <div class="modal-body">${t("请设置本次总结的目标百分比（占当前历史 token 的比例）。")}</div>
        <div class="summary-percent-row">
          <input type="number" id="sumPctInput" min="${min}" max="${max}" step="1" value="${pct}"><span class="pct-suffix">%</span>
        </div>
        <input type="range" id="sumPctRange" min="${min}" max="${max}" step="1" value="${pct}" class="summary-percent-range">
        <div class="summary-token-preview" id="sumTokenPreview"></div>
        <div class="modal-actions">
          <button class="btn" id="sumPctCancel">${t("取消")}</button>
          <button class="btn primary" id="sumPctStart">${t("开始总结")}</button>
        </div>
      </div>`;
    document.body.appendChild(mask);
    const input = mask.querySelector("#sumPctInput");
    const range = mask.querySelector("#sumPctRange");
    const preview = mask.querySelector("#sumTokenPreview");
    const updatePreview = () => {
      preview.textContent = t("将 {total} tokens 数量的历史消息压缩为 {target} tokens")
        .replace("{total}", fmt(+totalTokens || 0))
        .replace("{target}", fmt(targetOf(pct)));
    };
    input.oninput = () => {
      const v = +input.value;
      if (isFinite(v)) { pct = clamp(v); range.value = pct; updatePreview(); }
    };
    input.onchange = () => { pct = clamp(input.value); input.value = pct; range.value = pct; updatePreview(); };
    range.oninput = () => { pct = clamp(range.value); input.value = pct; updatePreview(); };
    mask.querySelector("#sumPctCancel").onclick = () => { mask.remove(); resolve(null); };
    mask.querySelector("#sumPctStart").onclick = () => { mask.remove(); resolve(pct); };
    updatePreview();
  });
}

function parseHeaders(str) {
  const out = {};
  (str || "").split(/[;\n]/).forEach(part => {
    const i = part.indexOf(":");
    if (i <= 0) return;
    const k = part.slice(0, i).trim();
    const v = part.slice(i + 1).trim();
    if (k) out[k] = v;
  });
  return out;
}

function headersToStr(h) {
  return Object.entries(h || {}).map(([k, v]) => `${k}: ${v}`).join("; ");
}

async function checkMcp(transport, url, command, headers) {
  try {
    return await api("/api/mcp-check", { method: "POST", body: JSON.stringify({ transport, url: url || null, headers: headers || {}, command: command || null }) });
  } catch (e) { return { ok: false, error: String((e && e.message) || e) }; }
}

/* 展示某个 MCP 表项保存到配置文件后的 JSON 形态 */
function showMcpJsonExample(obj) {
  const mask = document.createElement("div");
  mask.className = "modal-mask";
  mask.innerHTML = `
    <div class="modal" style="width:560px;max-height:82vh;overflow:auto;" data-modal-key="mcpJsonExample">
      <h3>${t("JSON 格式示例")}</h3>
      <div class="muted" style="margin-bottom:8px;">${t("该 MCP 表项在配置文件（configs/*.json）中的存储形态：")}</div>
      <pre class="json-preview">${esc(JSON.stringify(obj, null, 2))}</pre>
      <div class="modal-actions">
        <button class="btn primary" id="mcpJsonClose">${t("关闭")}</button>
      </div>
    </div>`;
  document.body.appendChild(mask);
  mask.querySelector("#mcpJsonClose").onclick = () => mask.remove();
  mask.onclick = e => { if (e.target === mask) mask.remove(); };
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

/* Multi-Agent 配置卡片外观（纯 localStorage，仅本机生效） */
const CARD_THEME_BLUE = "#4f46e5";
const CARD_BG_DEF = "#ffffff";
const CARD_BTN_COLORS = [
  { key: "card-btn-edit-color", label: "编辑", def: CARD_THEME_BLUE },
  { key: "card-btn-default-color", label: "设为默认", def: CARD_THEME_BLUE },
  { key: "card-btn-hide-color", label: "隐藏", def: CARD_THEME_BLUE },
  { key: "card-btn-danger-color", label: "删除", def: "#dc2626" },
];
const CARD_BTN_STYLES = [
  { key: "card-btn-edit-style", label: "编辑", def: "solid" },
  { key: "card-btn-default-style", label: "设为默认", def: "outline" },
  { key: "card-btn-hide-style", label: "隐藏", def: "outline" },
  { key: "card-btn-danger-style", label: "删除", def: "outline" },
];
function cardGet(key, def) { const v = localStorage.getItem(key); return v == null ? def : v; }
function cardSetOrClear(key, val, def) {
  if (String(val).toLowerCase() === String(def).toLowerCase()) localStorage.removeItem(key);
  else localStorage.setItem(key, val);
}
function cardRibbonCount() {
  const n = parseInt(cardGet("card-ribbon-count", "2"), 10);
  return Math.min(5, Math.max(1, isNaN(n) ? 2 : n));
}
function ribbonWidth(n) { return Math.round((18 + 9 * (n - 1)) * 2.828) + 12; }
function agentRibbonsHTML() {
  const n = cardRibbonCount();
  let inner = "";
  for (let i = 0; i < n; i++) inner += `<i style="--i:${i}"></i>`;
  return `<div class="agent-ribbons" data-n="${n}" style="--card-ribbon-w:${ribbonWidth(n)}px;">${inner}</div>`;
}
function isDarkColor(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || "").trim());
  if (!m) return false;
  const v = parseInt(m[1], 16);
  const r = (v >> 16) & 255, g = (v >> 8) & 255, b = v & 255;
  return (0.299 * r + 0.587 * g + 0.114 * b) < 140;
}
function applyCardSettings() {
  const root = document.documentElement;
  const setVar = (name, val) => root.style.setProperty(name, val);
  setVar("--card-btn-edit", cardGet("card-btn-edit-color", CARD_THEME_BLUE));
  setVar("--card-btn-default", cardGet("card-btn-default-color", CARD_THEME_BLUE));
  setVar("--card-btn-hide", cardGet("card-btn-hide-color", CARD_THEME_BLUE));
  setVar("--card-btn-danger", cardGet("card-btn-danger-color", "#dc2626"));
  root.dataset.styleEdit = cardGet("card-btn-edit-style", "solid");
  root.dataset.styleDefault = cardGet("card-btn-default-style", "outline");
  root.dataset.styleHide = cardGet("card-btn-hide-style", "outline");
  root.dataset.styleDanger = cardGet("card-btn-danger-style", "outline");
  setVar("--card-ribbon-color", cardGet("card-ribbon-color", CARD_THEME_BLUE));
  root.dataset.cardRibbon = cardGet("card-ribbon-show", "1") === "0" ? "0" : "1";
  const bg = cardGet("card-bg", "");
  if (bg) {
    setVar("--card-bg", bg);
    const dark = isDarkColor(bg);
    setVar("--card-fg", dark ? "#ffffff" : "#1f2328");
    setVar("--card-meta", dark ? "rgba(255,255,255,.78)" : "#6b7280");
    setVar("--card-code-bg", dark ? "rgba(255,255,255,.16)" : "#f1f5f9");
  } else {
    root.style.removeProperty("--card-bg");
    root.style.removeProperty("--card-fg");
    root.style.removeProperty("--card-meta");
    root.style.removeProperty("--card-code-bg");
  }
  const n = cardRibbonCount();
  const w = ribbonWidth(n) + "px";
  $$(".agent-ribbons").forEach(el => {
    el.style.setProperty("--card-ribbon-w", w);
    if (+el.dataset.n !== n) {
      el.dataset.n = n;
      el.innerHTML = Array.from({ length: n }, (_, i) => `<i style="--i:${i}"></i>`).join("");
    }
  });
}

/* HTML 导出（md2print） */
const HTML_CFG_SCHEMA = [
  { section: "纸张", items: [
    { p: ["page", "size"], label: "纸张尺寸", type: "text" },
    { p: ["page", "margin", "top"], label: "上边距", type: "text" },
    { p: ["page", "margin", "right"], label: "右边距", type: "text" },
    { p: ["page", "margin", "bottom"], label: "下边距", type: "text" },
    { p: ["page", "margin", "left"], label: "左边距", type: "text" },
  ]},
  { section: "预览", items: [
    { p: ["preview", "max_width"], label: "最大宽度", type: "text" },
    { p: ["preview", "canvas"], label: "画布背景色", type: "color" },
    { p: ["preview", "page_padding"], label: "内边距", type: "text" },
  ]},
  { section: "配色", items: [
    { p: ["colors", "text"], label: "正文", type: "color" },
    { p: ["colors", "background"], label: "页面背景", type: "color" },
    { p: ["colors", "link"], label: "链接", type: "color" },
    { p: ["colors", "h1"], label: "标题 1", type: "color" },
    { p: ["colors", "h2"], label: "标题 2", type: "color" },
    { p: ["colors", "h3"], label: "标题 3", type: "color" },
    { p: ["colors", "h4"], label: "标题 4", type: "color" },
    { p: ["colors", "h5"], label: "标题 5", type: "color" },
    { p: ["colors", "h6"], label: "标题 6", type: "color" },
    { p: ["colors", "strong"], label: "加粗", type: "color" },
    { p: ["colors", "em"], label: "斜体", type: "color" },
    { p: ["colors", "blockquote_text"], label: "引用文字", type: "color" },
    { p: ["colors", "blockquote_border"], label: "引用边框", type: "color" },
    { p: ["colors", "hr"], label: "水平线", type: "color" },
    { p: ["colors", "list_marker"], label: "列表标记", type: "color" },
    { p: ["colors", "inline_code_bg"], label: "行内代码背景", type: "color" },
    { p: ["colors", "code_bg"], label: "代码块背景", type: "color" },
    { p: ["colors", "code_border"], label: "代码块边框", type: "color" },
    { p: ["colors", "table_border"], label: "表格边框", type: "color" },
    { p: ["colors", "table_header_bg"], label: "表头背景", type: "color" },
    { p: ["colors", "code_keyword"], label: "代码·关键字", type: "color" },
    { p: ["colors", "code_string"], label: "代码·字符串", type: "color" },
    { p: ["colors", "code_comment"], label: "代码·注释", type: "color" },
    { p: ["colors", "code_number"], label: "代码·数字", type: "color" },
    { p: ["colors", "code_builtin"], label: "代码·内建", type: "color" },
    { p: ["colors", "code_function"], label: "代码·函数", type: "color" },
    { p: ["colors", "code_constant"], label: "代码·常量", type: "color" },
    { p: ["colors", "code_variable"], label: "代码·变量", type: "color" },
  ]},
];

/* 浏览器交互延迟设置（browser_agent/timing.py） */
const BROWSER_DELAY_SCHEMA = [
  { section: "导航与稳定化（对延迟影响最大）", items: [
    { p: ["nav", "grace_seconds"], label: "导航宽限窗口", def: 4.0, step: 0.1,
      desc: "仅对『可能触发跳转』的元素（a[href] / 表单提交 / role=link 等）生效：点击后先等导航「开始」的最长时间。此类元素通常立刻开始跳转并提前结束等待，只有迟迟不跳转时才等满。默认 4.0 秒。" },
    { p: ["nav", "short_grace_seconds"], label: "非导航点击宽限", def: 0.6, step: 0.1,
      desc: "对『不会触发跳转』的元素（普通按钮 / 图标 / 光标手势等）点击后，等待导航「开始」的短宽限。这类元素几乎不会导航，短等待 + 判稳即可，避免每次点击都空等数秒。默认 0.6 秒。" },
    { p: ["nav", "commit_timeout"], label: "导航提交等待超时", def: 2.0, step: 0.5,
      desc: "确认导航「已开始」后，等待新文档提交（Page.frameNavigated）的最长时间。正常情况下提交是瞬时的，此值只在导航中途失败 / 取消时兜底。默认 2.0 秒。" },
    { p: ["nav", "load_grace_seconds"], label: "导航提交后内容等待", def: 1.5, step: 0.5,
      desc: "新文档提交后，额外等待 load 事件的上限（若 load 已触发则立即结束）。用于给异步内容一点时间，但不再为『load 迟迟不来』死等。默认 1.5 秒。" },
    { p: ["nav", "load_timeout"], label: "新标签页/空壳加载超时", def: 15.0, step: 0.5,
      desc: "新标签页首次就绪（tool_8）以及页面为空壳时等待正文出现的最长时间。不再用于普通点击后的导航等待。默认 15.0 秒。" },
    { p: ["nav", "quiet_seconds"], label: "DOM 判稳静默窗", def: 0.7, step: 0.1,
      desc: "页面的 DOM 指纹（readyState + outerHTML 长度）连续保持不变达到这个时长，才认为页面渲染完成。异步渲染（SPA）常需要它兜底；太小会抓到中间态。默认 0.7 秒。" },
    { p: ["nav", "quiet_timeout"], label: "判稳总超时", def: 2.0, step: 0.5,
      desc: "等待 DOM 判稳的总上限；超过则不再等待，直接返回当前页面。默认 2.0 秒。" },
    { p: ["nav", "probe_interval"], label: "判稳探测间隔", def: 0.15, step: 0.01,
      desc: "判稳过程中每隔多久探测一次 DOM 指纹。越小判定越及时，但探测命令更频繁。默认 0.15 秒。" },
    { p: ["nav", "settle_poll_interval"], label: "导航宽限轮询间隔", def: 0.05, step: 0.01,
      desc: "在「导航宽限窗口」内，每隔多久检查一次导航是否已经开始。默认 0.05 秒。" },
  ]},
  { section: "页面就绪与标签页", items: [
    { p: ["ready", "timeout"], label: "标签页就绪等待超时", def: 15.0, step: 0.5,
      desc: "切换标签页（tool_4）时，等待该页 readyState 变为 complete 的最长时间。默认 15.0 秒。" },
    { p: ["ready", "poll_interval"], label: "标签页就绪轮询间隔", def: 0.15, step: 0.01,
      desc: "切换标签页时轮询 readyState 的间隔。默认 0.15 秒。" },
    { p: ["ready", "new_tab_poll_interval"], label: "新标签页轮询间隔", def: 0.1, step: 0.01,
      desc: "打开新标签页（tool_8）时，轮询 readyState 与 location.href 的间隔。默认 0.1 秒。" },
    { p: ["ready", "close_timeout"], label: "关闭标签页等待超时", def: 3.0, step: 0.5,
      desc: "关闭标签页后，等待该标签页真正从目标列表消失的最长时间。默认 3.0 秒。" },
    { p: ["ready", "close_poll_interval"], label: "关闭标签页轮询间隔", def: 0.1, step: 0.01,
      desc: "关闭标签页时的轮询间隔。默认 0.1 秒。" },
  ]},
  { section: "CDP 命令与探测超时", items: [
    { p: ["cdp", "command_timeout"], label: "CDP 命令超时", def: 30.0, step: 1,
      desc: "单条 Chrome DevTools Protocol 命令的默认超时。抓取 DOM、注入点击等绝大多数命令都使用它；过小会在慢页面上误报超时。默认 30.0 秒。" },
    { p: ["cdp", "probe_timeout"], label: "短探测超时", def: 3.0, step: 0.5,
      desc: "轻量探测命令（读取 readyState、hasFocus、主帧树等）的超时，比默认命令超时短。默认 3.0 秒。" },
    { p: ["cdp", "input_timeout"], label: "输入事件等待超时", def: 2.0, step: 0.5,
      desc: "鼠标 / 键盘输入事件的单次等待上限。输入是『发了就算数』的最佳努力操作；某台机器上个别事件确认很慢时，超时即继续，避免一次点击被拖成几十秒。默认 2.0 秒。" },
    { p: ["cdp", "outer_html_timeout"], label: "原始 HTML 读取超时", def: 20.0, step: 1,
      desc: "debug 模式下读取整页原始 HTML 的超时，仅影响调试日志。默认 20.0 秒。" },
  ]},
  { section: "浏览器启动", items: [
    { p: ["launch", "ready_timeout"], label: "启动就绪总超时", def: 25.0, step: 1,
      desc: "启动浏览器后，等待 DevToolsActivePort 端口文件与 CDP 服务就绪的总超时。机器慢或安全软件拦截时可调大。默认 25.0 秒。" },
    { p: ["launch", "devtools_poll_interval"], label: "端口文件轮询间隔", def: 0.1, step: 0.01,
      desc: "启动时轮询 DevToolsActivePort 文件的间隔。默认 0.1 秒。" },
    { p: ["launch", "cdp_poll_interval"], label: "CDP 服务轮询间隔", def: 0.2, step: 0.01,
      desc: "启动时轮询 /json/version 等待 CDP 服务就绪的间隔。默认 0.2 秒。" },
    { p: ["launch", "reuse_timeout"], label: "复用浏览器就绪超时", def: 2.0, step: 0.5,
      desc: "复用上一次已启动的浏览器时，等待其 CDP 就绪的超时；超时则改为重新启动。默认 2.0 秒。" },
    { p: ["launch", "port_probe_timeout"], label: "端口探测超时", def: 1.0, step: 0.1,
      desc: "检测浏览器端口是否存活（GET /json/version）的单次超时，也用于浮动按钮的连接状态轮询。默认 1.0 秒。" },
  ]},
  { section: "拟人交互微延迟（每项为 [最小, 最大] 随机秒数）", items: [
    { p: ["actions", "move_step"], label: "鼠标移动每步间隔", def: [0.008, 0.025], pair: true, step: 0.005,
      desc: "拟人移动鼠标时，每个中间点的间隔范围（随机取值）。减小可加快点击，但过小可能被反爬识别。默认 0.008 ~ 0.025 秒。" },
    { p: ["actions", "node_box"], label: "元素定位后停顿", def: [0.05, 0.12], pair: true, step: 0.01,
      desc: "把元素滚动进视口后、获取其几何信息前的停顿。默认 0.05 ~ 0.12 秒。" },
    { p: ["actions", "click_hover"], label: "点击悬停停顿", def: [0.04, 0.12], pair: true, step: 0.01,
      desc: "鼠标移到目标上后、按下鼠标之前的停顿。默认 0.04 ~ 0.12 秒。" },
    { p: ["actions", "click_press"], label: "按下到松开停顿", def: [0.05, 0.12], pair: true, step: 0.01,
      desc: "鼠标按下与松开之间的停顿。默认 0.05 ~ 0.12 秒。" },
    { p: ["actions", "input_focus"], label: "输入聚焦停顿", def: [0.08, 0.18], pair: true, step: 0.01,
      desc: "点击输入框聚焦后、开始输入前的停顿。默认 0.08 ~ 0.18 秒。" },
    { p: ["actions", "input_clear"], label: "清空输入框停顿", def: [0.05, 0.10], pair: true, step: 0.01,
      desc: "清空输入框原有内容后的停顿。默认 0.05 ~ 0.10 秒。" },
    { p: ["actions", "type_char"], label: "逐字符输入间隔", def: [0.03, 0.12], pair: true, step: 0.01,
      desc: "逐字符输入时，每个字符之间的间隔。长文本会明显累加耗时，是输入类操作最主要的延迟来源。默认 0.03 ~ 0.12 秒。" },
    { p: ["actions", "drag_press"], label: "拖动按下停顿", def: [0.05, 0.12], pair: true, step: 0.01,
      desc: "拖动时在起点按下鼠标前后的停顿。默认 0.05 ~ 0.12 秒。" },
    { p: ["actions", "drag_move"], label: "拖动移动每步间隔", def: [0.01, 0.03], pair: true, step: 0.005,
      desc: "拖动过程中鼠标移动每个中间点的间隔。默认 0.01 ~ 0.03 秒。" },
    { p: ["actions", "scroll_settle"], label: "滚动步间停顿", def: [0.15, 0.35], pair: true, step: 0.01,
      desc: "每次滚动（滚轮 / 整页）后等待页面响应的停顿；滚动类操作可能连续多步，累加后较可观。默认 0.15 ~ 0.35 秒。" },
  ]},
];

function cfgGet(obj, path) {
  let c = obj;
  for (const k of path) { if (c == null) return ""; c = c[k]; }
  return c == null ? "" : c;
}
function cfgSet(obj, path, val) {
  let c = obj;
  for (let i = 0; i < path.length - 1; i++) {
    if (c[path[i]] == null) c[path[i]] = {};
    c = c[path[i]];
  }
  c[path[path.length - 1]] = val;
}

function b64ToUtf8(b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new TextDecoder("utf-8").decode(bytes);
}
function utf8ToB64(s) {
  const bytes = new TextEncoder().encode(String(s));
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin);
}

async function exportHtml(markdown) {
  try {
    const r = await api(`/api/agents/${encodeURIComponent(S.agentId)}/threads/${encodeURIComponent(S.threadId)}/export-html`, {
      method: "POST", body: JSON.stringify({ markdown }),
    });
    toast(`${t("已生成 HTML")}：${r.path}`);
  } catch (e) { toast(e.message, true); }
}

async function exportMd(markdown) {
  try {
    const r = await api(`/api/agents/${encodeURIComponent(S.agentId)}/threads/${encodeURIComponent(S.threadId)}/export-md`, {
      method: "POST", body: JSON.stringify({ markdown }),
    });
    toast(`${t("已生成 md 文件")}：${r.path}`);
  } catch (e) { toast(e.message, true); }
}

// 「将本地md转换为HTML」：后端弹 Windows 文件选择窗口，选中后把该 md 转成可打印 HTML，
// 写在其同目录下（同名 .html；已存在时另存为带时间戳的文件，不覆盖）。配置复用
// 系统设置里的「HTML 转换配置表」。对话框是阻塞的，等待期间按钮置灰并给出提示。
async function convertLocalMd(btn) {
  const original = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = `⏳ ${t("请选择 Markdown 文件…")}`; }
  try {
    const r = await api("/api/convert-local-md", {
      method: "POST",
      body: JSON.stringify({ title: t("选择要转换为 HTML 的 Markdown 文件") }),
    });
    if (r && r.canceled) { toast(t("已取消")); return; }
    toast(`${t("已生成 HTML")}：${r.path}`);
  } catch (e) {
    toast(e.message, true);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = original; }
  }
}

function injectExportButtons(rootEl) {
  if (!settingsCache) return;
  const showHtml = settingsCache.export_html;
  const showMd = settingsCache.export_md;
  if (!showHtml && !showMd) return;
  $$(".ai-msg-block[data-md-b64]", rootEl).forEach(block => {
    if (block.querySelector(".export-html-row")) return;
    const wrap = document.createElement("div");
    wrap.className = "export-html-row";
    if (showHtml) {
      const btn = document.createElement("button");
      btn.className = "btn small export-html-btn";
      btn.type = "button";
      btn.textContent = `🖨 ${t("转换 HTML")}`;
      btn.onclick = () => exportHtml(b64ToUtf8(block.dataset.mdB64));
      wrap.appendChild(btn);
    }
    if (showMd) {
      const btn = document.createElement("button");
      btn.className = "btn small export-md-btn";
      btn.type = "button";
      btn.textContent = `📄 ${t("导出 md 文件")}`;
      btn.onclick = () => exportMd(b64ToUtf8(block.dataset.mdB64));
      wrap.appendChild(btn);
    }
    block.appendChild(wrap);
  });
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
/* 从连接前缀里提取「服务器」= host:port。这是后端唯一认识的服务器身份：
 * 同一 host:port 才是同一台 PostgreSQL；pgAdmin 里的服务器名字只是显示名。 */
function connServerLabel(prefix) {
  const p = String(prefix || "").trim();
  if (!p) return "";
  const rest = p.replace(/^[a-z][a-z0-9+.-]*:\/\//i, "");
  const at = rest.lastIndexOf("@");
  let hp = at >= 0 ? rest.slice(at + 1) : rest;
  hp = hp.split("/")[0].split("?")[0];
  return hp;
}

/* 完整连接串 ⇄ 前缀 + 数据库名 + 后缀
 * PostgreSQL URI 规范：postgresql://用户名:密码@主机:端口/数据库名?参数
 * prefix = 数据库名之前（含结尾斜杠），database = 库名，suffix = ? 开头的参数。 */
function parseConnString(raw) {
  const s = String(raw || "").trim();
  if (!s) return { ok: false, empty: true, error: t("请粘贴完整连接串") };
  const scheme = s.match(/^([a-z][a-z0-9+.-]*):\/\//i);
  if (!scheme) return { ok: false, error: t("连接串必须以 postgresql:// 或 postgres:// 开头") };
  const rest = s.slice(scheme[0].length);
  const at = rest.lastIndexOf("@");
  const slash = rest.indexOf("/", at >= 0 ? at : 0);
  if (slash < 0) return { ok: false, error: t("连接串缺少数据库名，格式应为 …/<数据库名>") };
  const authority = rest.slice(0, slash);
  if (!authority) return { ok: false, error: t("连接串缺少主机地址") };
  let tail = rest.slice(slash + 1);
  let database = tail, suffix = "";
  const q = tail.indexOf("?");
  if (q >= 0) { database = tail.slice(0, q); suffix = tail.slice(q); }
  database = database.replace(/\/+$/, "");
  if (!database) return { ok: false, error: t("连接串缺少数据库名，格式应为 …/<数据库名>") };
  return { ok: true, prefix: scheme[0] + authority + "/", database, suffix };
}
function composeConnString(prefix, database, suffix) {
  return String(prefix || "") + String(database || "") + String(suffix || "");
}
/* 连接后缀控件（本地 → readonly 固定 sslmode=disable；远端 → SSL 下拉）。
 * 抽成顶层函数，供 multi-agent 编辑器与「全局连接设置」弹窗共用。
 * suffixId 默认 "f_suffix"（编排器），全局弹窗传自己的 id 以免冲突。 */
function pgSuffixCtrlHTML(local, value, suffixId) {
  const id = suffixId || "f_suffix";
  return local
    ? `<input id="${id}" value="${esc(value || "?sslmode=disable")}" readonly><span class="hint">${t("本地连接，自动使用 sslmode=disable")}</span>`
    : `<select id="${id}">${SSL_OPTIONS.map(o => `<option value="${esc(o.v)}" ${(value || "") === o.v ? "selected" : ""}>${esc(t(o.label))}</option>`).join("")}${SSL_OPTIONS.some(o => o.v === (value || "")) ? "" : `<option value="${esc(value || "")}" selected>${esc(value || t("（无）"))}</option>`}</select><span class="hint">${t("云端/企业库请选择 SSL 模式")}</span>`;
}
function currentPgMode() {
  const r = document.querySelector('input[name="pg_conn_mode"]:checked');
  return r ? r.value : "split";
}
/* 收集当前表单的连接信息（两种填写方式归一化成 prefix/suffix）。
 * 数据库名不再由用户填写：它由「名称」自动生成，所以这里不返回 database。 */
function collectPgConn() {
  if (currentPgMode() === "full") {
    const parsed = parseConnString($("#f_conn_full").value);
    if (parsed.ok) {
      let suffix = parsed.suffix;
      if (!suffix && isLocalPrefix(parsed.prefix)) suffix = "?sslmode=disable";
      return { ok: true, prefix: parsed.prefix, suffix };
    }
    return { ok: false, error: parsed.error, prefix: "", suffix: "" };
  }
  const s = $("#f_suffix");
  return { ok: true, prefix: $("#f_prefix").value, suffix: s ? s.value || "" : "" };
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
  // CJK 强调补丁：CommonMark 的侧翼规则在中文里会误伤——
  // `这是**"引用"**` 中 `**` 左侧是汉字（非空白/标点）、右侧是引号（标点），
  // 会被判为「不能开启」，导致加粗失效。这里放宽：只要一侧是 CJK 字符、另一侧是标点，
  // 就允许该 `*`/`**`/`_` 开启或闭合（纯 ASCII 场景不受影响，仍按 CommonMark 处理）。
  if (inst.inline && inst.inline.State) {
    const isCjkChar = (cp) => cp > 0 && (
      (cp >= 0x2E80 && cp <= 0x303F) || (cp >= 0x3040 && cp <= 0x30FF) ||
      (cp >= 0x3400 && cp <= 0x4DBF) || (cp >= 0x4E00 && cp <= 0x9FFF) ||
      (cp >= 0xAC00 && cp <= 0xD7AF) || (cp >= 0xF900 && cp <= 0xFAFF) ||
      (cp >= 0xFE10 && cp <= 0xFE4F) || (cp >= 0xFF00 && cp <= 0xFFEF) ||
      (cp >= 0x20000 && cp <= 0x2FA1F) ||
      cp === 0x2018 || cp === 0x2019 || cp === 0x201C || cp === 0x201D ||
      cp === 0x2013 || cp === 0x2014 || cp === 0x2026 || cp === 0x00B7
    );
    const isPunctCp = (cp) => cp > 0 && /\p{P}/u.test(String.fromCodePoint(cp));
    const cpBefore = (s, i) => {
      if (i <= 0) return -1;
      const c = s.charCodeAt(i - 1);
      if (c >= 0xDC00 && c <= 0xDFFF && i >= 2) {
        const p = s.charCodeAt(i - 2);
        if (p >= 0xD800 && p <= 0xDBFF) return (p - 0xD800) * 0x400 + (c - 0xDC00) + 0x10000;
      }
      return c;
    };
    const cpAfter = (s, i) => {
      if (i >= s.length) return -1;
      const c = s.charCodeAt(i);
      if (c >= 0xD800 && c <= 0xDBFF && i + 1 < s.length) {
        const n = s.charCodeAt(i + 1);
        if (n >= 0xDC00 && n <= 0xDFFF) return (c - 0xD800) * 0x400 + (n - 0xDC00) + 0x10000;
      }
      return c;
    };
    const origScanDelims = inst.inline.State.prototype.scanDelims;
    inst.inline.State.prototype.scanDelims = function (start, canSplitWord) {
      const res = origScanDelims.call(this, start, canSplitWord);
      if (res.can_open && res.can_close) return res;
      const before = cpBefore(this.src, start);
      const after = cpAfter(this.src, start + res.length);
      let canOpen = res.can_open, canClose = res.can_close;
      if (!canOpen && isCjkChar(before) && isPunctCp(after)) canOpen = true;
      if (!canClose && isPunctCp(before) && isCjkChar(after)) canClose = true;
      if (canOpen !== res.can_open || canClose !== res.can_close) {
        return { can_open: canOpen, can_close: canClose, length: res.length };
      }
      return res;
    };
  }
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
   按启发式把「强信号」片段包裹成 \(...\) 交给 KaTeX；强信号 = \命令 或 花括号/数字上下标。
   激进模式（aggressive=true）：额外支持单字符上下标（V_φ、V^π）、希腊字母与 Unicode 数学符号，
   并把「看起来是公式」的代码块 / 行内代码去掉代码标记后一并识别。 */
function isBareMathChar(c, aggressive) {
  if (!c) return false;
  if (/[A-Za-z0-9_^{}().,=+\-*/<>|'~]/.test(c)) return true;
  if (aggressive && /[\u00c0-\u024f\u0370-\u03ff\u1f00-\u1fff\u2200-\u22ff≈≠≤≥±−×÷⋅∞∑∫√∂∇∈∉⊂⊃∪∩→←↔∅∀∃∝∼≃≅≡≪≫]/.test(c)) return true;
  return false;
}
function isBareMathOp(c, aggressive) {
  if (!c) return false;
  // 注意：不把表格分隔符 | 当作可跨空格的运算符，否则会吞掉 markdown 表格的竖线
  if (/[=+\-*/<>,^_]/.test(c)) return true;
  if (aggressive && /[−≈≠≤≥±×÷⋅Σ∑∫∏∐]/.test(c)) return true;
  return false;
}
function bareMathSeedEnd(s, i, aggressive) {
  const c = s[i];
  if (c === undefined) return -1;
  if (c === "\\") {
    const m = /\\[A-Za-z]+/.exec(s.slice(i));
    return m ? i + m[0].length : -1;
  }
  const seedStart = aggressive
    ? /[A-Za-z0-9\u00c0-\u024f\u0370-\u03ff)\]}]/
    : /[A-Za-z0-9)\]}]/;
  if (!seedStart.test(c)) return -1;
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
  if (/[0-9]/.test(after || "")) return i + 3;
  if (aggressive && after && !/\s/.test(after)) {
    // 单字符上下标（V_φ、s_t）；若下划线后是整词（user_name）则视为 snake_case，不当公式
    const afterAfter = s[i + 3];
    if (!/[A-Za-z0-9]/.test(after) || !/[A-Za-z0-9]/.test(afterAfter || "")) return i + 3;
  }
  return -1;
}
function matchOpenBraceLeft(s, closeIdx) {
  let depth = 0;
  for (let j = closeIdx; j >= 0; j--) {
    const c = s[j];
    if (c === "\n") return -1;
    if (c === "}") depth++;
    else if (c === "{") { depth--; if (depth === 0) return j; }
  }
  return -1;
}
function bareMathExpandLeft(s, start, aggressive) {
  while (start > 0) {
    const c = s[start - 1];
    if (c === "}") {
      // 左侧是花括号组：整组纳入，避免公式从组中间开始导致括号不配平
      const open = matchOpenBraceLeft(s, start - 1);
      if (open >= 0) { start = open; continue; }
    }
    if (isBareMathChar(c, aggressive)) { start--; continue; }
    if (c === " " || c === "\t") {
      let k = start - 1;
      while (k > 0 && (s[k] === " " || s[k] === "\t")) k--;
      const leftCh = s[k];
      if (!isBareMathChar(leftCh, aggressive)) break;
      if (isBareMathOp(leftCh, aggressive) || s[start] === "\\") { start = k; continue; }
    }
    break;
  }
  return start;
}
function bareMathExpandRight(s, end, aggressive) {
  let depth = 0;
  while (end < s.length) {
    const c = s[end];
    if (depth > 0) {
      // 已在未闭合的 {} 内：一路吞到配对为止（允许中文/空格等任意非换行字符），保证花括号配平
      if (c === "\n") break;
      if (c === "{") depth++;
      else if (c === "}") depth--;
      end++;
      continue;
    }
    if (c === "{") { depth++; end++; continue; }
    if (isBareMathChar(c, aggressive)) { end++; continue; }
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
      const prevOk = isBareMathOp(prev, aggressive);
      const nxtOk = isBareMathOp(nxt, aggressive) || (nxt === "\\" && /[A-Za-z]/.test(s[k + 1] || ""));
      if (prevOk || nxtOk) { end = k; continue; }
    }
    break;
  }
  while (end > 0 && (s[end - 1] === " " || s[end - 1] === "\t")) end--;
  return end;
}
/* 把公式片段里的中文/CJK 连续段包成 \text{...}，让 KaTeX 以正体排版（纯 CJK 段不含 LaTeX 特殊字符，无需转义） */
function cjkToLatexText(s) {
  return s.replace(/[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]+/g, (m) => "\\text{" + m + "}");
}
/* 判断代码（代码块 / 行内代码）内容是否「像公式」：只有含明确数学信号才去掉代码标记，
   避免把普通代码当公式。仅激进模式使用。 */
const MATH_LIKE_RE = /\\(?:frac|sqrt|sum|prod|int|iint|oint|alpha|beta|gamma|delta|epsilon|varepsilon|zeta|eta|theta|vartheta|iota|kappa|lambda|mu|nu|xi|pi|rho|sigma|tau|upsilon|phi|varphi|chi|psi|omega|Gamma|Delta|Theta|Lambda|Xi|Pi|Sigma|Phi|Psi|Omega|mathbb|mathcal|mathbf|mathrm|text|cdot|times|div|pm|mp|leq|geq|neq|approx|equiv|in|notin|subset|supset|cup|cap|infty|partial|nabla|forall|exists|to|rightarrow|leftarrow|leftrightarrow|begin|end)\b/;
function isMathLikeText(t) {
  const core = String(t == null ? "" : t);
  if (!core.trim()) return false;
  if (/[\u0370-\u03ff\u1f00-\u1fff\u2200-\u22ff]/.test(core)) return true;
  if (/[≈≠≤≥±−×÷⋅∞∑∫√∂∇∈∉⊂⊃∪∩→←↔∅∀∃∝∼≃≅≡≪≫]/.test(core)) return true;
  if (/[\^_]\{/.test(core)) return true;
  if (MATH_LIKE_RE.test(core)) return true;
  return false;
}
/* 按 CommonMark 围栏规则把字符串切成「普通文本 / 围栏代码块」片段：
   开启围栏必须在行首（最多 3 个前导空格），闭合围栏必须是同种字符且不短于开启围栏。
   绝不能再用全局正则 ```` ```...``` ```` 盲配——正文 / HTML 块里偶然出现的单个 ```
   （例如工具结果里转义后仍保留的说明文字）会与真正的围栏错配，配对整体错位后
   激进模式会把「真围栏 + 假围栏」之间的普通文本误判为公式代码块并删掉标记，
   遗留一个孤立围栏；markdown-it 会把该孤立围栏当作代码块开头，把其后所有内容
   （含后续消息的 HTML）全部吞进代码块。 */
function splitFencedCode(s) {
  const lines = s.split("\n");
  const parts = [];
  let segStart = 0;
  let i = 0;
  while (i < lines.length) {
    const open = /^( {0,3})(`{3,}|~{3,})(.*)$/.exec(lines[i]);
    if (open && !(open[2][0] === "`" && open[3].indexOf("`") !== -1)) {
      const ch = open[2][0];
      const len = open[2].length;
      let j = i + 1;
      for (; j < lines.length; j++) {
        const close = /^( {0,3})(`{3,}|~{3,})[ \t]*$/.exec(lines[j]);
        if (close && close[2][0] === ch && close[2].length >= len) break;
      }
      if (j < lines.length) {
        if (i > segStart) parts.push({ type: "text", text: lines.slice(segStart, i).join("\n") });
        parts.push({ type: "code", raw: lines.slice(i, j + 1).join("\n"), inner: lines.slice(i + 1, j).join("\n") });
        i = j + 1;
        segStart = i;
        continue;
      }
    }
    i++;
  }
  if (segStart < lines.length) parts.push({ type: "text", text: lines.slice(segStart).join("\n") });
  return parts;
}
function autodetectMath(text, aggressive) {
  const stash = [];
  const P = "\u0001";
  const keep = (m) => { stash.push(m); return P + (stash.length - 1) + P; };
  let s = String(text == null ? "" : text).replace(/\r\n?/g, "\n");
  if (aggressive) {
    // 代码块 / 行内代码：内容像公式就去掉代码标记让公式参与识别，否则按原样保护
    s = splitFencedCode(s)
      .map((p) => (p.type === "code" ? (isMathLikeText(p.inner) ? p.inner : keep(p.raw)) : p.text))
      .join("\n");
    s = s.replace(/`([^`\n]*)`/g, (m, inner) => (isMathLikeText(inner) ? inner : keep(m)));
  } else {
    s = splitFencedCode(s)
      .map((p) => (p.type === "code" ? keep(p.raw) : p.text))
      .join("\n");
    s = s.replace(/`[^`\n]*`/g, keep);
  }
  s = s.replace(/\$\$[\s\S]*?\$\$/g, keep);
  s = s.replace(/\\\[[\s\S]*?\\\]/g, keep);
  s = s.replace(/\\\([^\n]*?\\\)/g, keep);
  s = s.replace(/\$[^$\n]*?\$/g, keep);
  // 保护 markdown 强调标记的连续运行（** / __ / ~~），避免被裸公式识别吞掉而破坏加粗/斜体；
  // 必须在代码块保护之后执行，否则代码块内的占位符无法被最终还原。单字符 * / _ 仍作数学字符。
  s = s.replace(/\*{2,}/g, keep);
  s = s.replace(/_{2,}/g, keep);
  s = s.replace(/~{2,}/g, keep);

  const spans = [];
  for (let i = 0; i < s.length; i++) {
    const se = bareMathSeedEnd(s, i, aggressive);
    if (se < 0) continue;
    const start = bareMathExpandLeft(s, i, aggressive);
    const end = bareMathExpandRight(s, se, aggressive);
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
    const raw = s.slice(sp.start, sp.end);
    if (raw.trim() === "") continue;
    // 花括号配平校验：不平衡（被截断/未闭合）就不包裹，避免 KaTeX 红色错误框
    const bare = raw.replace(/\\[{}]/g, "");
    if ((bare.match(/\{/g) || []).length !== (bare.match(/\}/g) || []).length) continue;
    out += s.slice(pos, sp.start) + "\\(" + cjkToLatexText(raw) + "\\)";
    pos = sp.end;
  }
  out += s.slice(pos);
  return out.replace(new RegExp(P + "(\\d+)" + P, "g"), (m, idx) => stash[+idx]);
}
function bareMathEnabled() { return !!(settingsCache && settingsCache.bare_math_detect); }
function aggressiveMathEnabled() { return !!(settingsCache && settingsCache.aggressive_math_detect); }
function renderMd(text) {
  const t = text || "";
  if (!md) return esc(t);
  if (aggressiveMathEnabled()) return md.render(autodetectMath(t, true));
  if (bareMathEnabled()) return md.render(autodetectMath(t, false));
  return md.render(t);
}

/* ================= 状态 ================= */
const S = { view: "agents", agentId: null, agentName: null, threadId: null, snapshotId: null, snapshotFromDeleted: false, editingDefault: false };
const app = $("#app");
let ws = null;
let isRunning = false;
let statusMode = "idle";
let currentReplyEl = null;
let editorDirty = false;
let settingsCache = null;
let pgConnMode = "split";
let pinned = localStorage.getItem("pin-follow") === "1";

/* ================= 多实例会话（ID91） ================= */
// 每个 (agent_id, thread_id) 一个 SessionStream；切视图只解绑 DOM，不丢 WebSocket/
// 缓冲；页面刷新后靠后端 run_id+seq 回放重建。
const sessionStreams = new Map();
let activeSessionStream = null;
let sessionSnapshot = { sessions: [], counts: { running: 0, completed: 0 } };
let sessionPollTimer = null;
let sessionBallOpen = false;
let threadsStatusTimer = null;

function sessionKeyOf(agentId, threadId) { return String(agentId) + "\u0000" + String(threadId); }
function getSessionStream(agentId, agentName, threadId) {
  const key = sessionKeyOf(agentId, threadId);
  let stream = sessionStreams.get(key);
  if (!stream) {
    stream = new SessionStream(agentId, agentName, threadId);
    sessionStreams.set(key, stream);
  } else if (agentName) {
    stream.agentName = agentName;
  }
  return stream;
}
function sessionInfoOf(agentId, threadId) {
  return (sessionSnapshot.sessions || []).find(
    (s) => s.agent_id === agentId && s.thread_id === threadId
  ) || null;
}
function isSessionPending(info) {
  if (!info) return false;
  return ["starting", "running", "stopping", "waiting"].includes(info.status)
    || (info.status === "completed" && !info.accepted);
}

async function refreshSessions() {
  try {
    sessionSnapshot = await api("/api/sessions");
  } catch (e) {
    return sessionSnapshot;
  }
  updateSessionBall();
  return sessionSnapshot;
}

async function acceptSession(agentId, threadId) {
  try {
    const r = await api(`/api/sessions/${encodeURIComponent(agentId)}/threads/${encodeURIComponent(threadId)}/accept`, { method: "POST" });
    await refreshSessions();
    return !!r.ok;
  } catch (e) {
    return false;
  }
}

function reportActiveView(agentId, threadId) {
  api("/api/sessions/active", {
    method: "POST",
    body: JSON.stringify({
      agent_id: agentId || null,
      thread_id: threadId || null,
      visible: document.visibilityState === "visible",
    }),
  }).catch(() => {});
}

/* ---------- 应用内悬浮球（WebView2 DOM，非 OS 窗口） ---------- */
function clampSessionBallPos(pos) {
  const w = 400, h = 58, m = 8;
  return {
    x: Math.min(Math.max(m, pos.x), Math.max(m, window.innerWidth - w - m)),
    y: Math.min(Math.max(m, pos.y), Math.max(m, window.innerHeight - h - m)),
  };
}
function loadSessionBallPos() {
  try {
    const p = JSON.parse(localStorage.getItem("session-ball-pos") || "null");
    if (p && typeof p.x === "number" && typeof p.y === "number") return clampSessionBallPos(p);
  } catch (e) {}
  return clampSessionBallPos({ x: window.innerWidth - 400, y: Math.round(window.innerHeight * 0.55) });
}
function applySessionBallPos(pos) {
  const wrap = $("#sessionBallWrap");
  if (!wrap) return;
  wrap.style.left = pos.x + "px";
  wrap.style.top = pos.y + "px";
}
function ensureSessionBall() {
  if ($("#sessionBallWrap")) return;
  const wrap = document.createElement("div");
  wrap.id = "sessionBallWrap";
  wrap.className = "session-ball-wrap";
  wrap.style.display = "none";
  wrap.innerHTML = `
    <div class="session-ball-panel" id="sessionBallPanel" style="display:none;"></div>
    <button type="button" class="session-ball" id="sessionBallBtn" title="${t("查看正在进行的对话")}">
      <span class="session-ball-glow"></span>
      <span class="session-ball-text" id="sessionBallText"></span>
    </button>`;
  document.body.appendChild(wrap);
  applySessionBallPos(loadSessionBallPos());

  const btn = $("#sessionBallBtn");
  const panel = $("#sessionBallPanel");
  btn.onclick = (e) => {
    e.stopPropagation();
    if (btn.dataset.dragged === "1") { btn.dataset.dragged = "0"; return; }
    sessionBallOpen = !sessionBallOpen;
    panel.style.display = sessionBallOpen ? "" : "none";
    updateSessionBallPanel();
    if (sessionBallOpen) requestAnimationFrame(positionSessionBallPanel);
  };
  panel.onclick = (e) => e.stopPropagation();
  document.addEventListener("pointerdown", (e) => {
    if (!sessionBallOpen) return;
    if (wrap.contains(e.target)) return;
    sessionBallOpen = false;
    panel.style.display = "none";
  });

  let drag = null;
  btn.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    const r = wrap.getBoundingClientRect();
    drag = { sx: e.clientX, sy: e.clientY, x: r.left, y: r.top, moved: false };
    btn.dataset.dragged = "0";
    btn.setPointerCapture(e.pointerId);
  });
  btn.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.sx, dy = e.clientY - drag.sy;
    if (!drag.moved && (Math.abs(dx) > 4 || Math.abs(dy) > 4)) drag.moved = true;
    if (!drag.moved) return;
    const pos = clampSessionBallPos({ x: drag.x + dx, y: drag.y + dy });
    applySessionBallPos(pos);
    drag.pos = pos;
  });
  btn.addEventListener("pointerup", (e) => {
    if (!drag) return;
    if (drag.moved && drag.pos) {
      localStorage.setItem("session-ball-pos", JSON.stringify(drag.pos));
      btn.dataset.dragged = "1";
      setTimeout(() => { btn.dataset.dragged = "0"; }, 30);
    }
    try { btn.releasePointerCapture(e.pointerId); } catch (err) {}
    drag = null;
  });
  window.addEventListener("resize", () => {
    const wrap2 = $("#sessionBallWrap");
    if (!wrap2 || wrap2.style.display === "none") return;
    const r = wrap2.getBoundingClientRect();
    applySessionBallPos(clampSessionBallPos({ x: r.left, y: r.top }));
  });
}

function positionSessionBallPanel() {
  const panel = $("#sessionBallPanel");
  if (!panel || panel.style.display === "none") return;
  panel.style.bottom = "0";
  panel.style.top = "auto";
  const r = panel.getBoundingClientRect();
  if (r.top < 8) {
    panel.style.bottom = "auto";
    panel.style.top = "0";
  }
}

function updateSessionBallPanel() {
  const panel = $("#sessionBallPanel");
  if (!panel) return;
  const list = sessionSnapshot.sessions || [];
  if (!list.length) { panel.innerHTML = ""; return; }
  panel.innerHTML = list.map((s) => {
    const done = s.status === "completed";
    const statusText = done ? t("已经完工") : t("正在进行");
    return `<div class="session-ball-row ${done ? "done" : "running"}" data-a="${esc(s.agent_id)}" data-t="${esc(s.thread_id)}">
      <span class="session-ball-row-name">@${esc(s.agent_name || s.agent_id)} - ${esc(s.thread_id)}</span>
      <span class="session-ball-row-state">${statusText}</span>
    </div>`;
  }).join("");
  $$("#sessionBallPanel .session-ball-row").forEach((row) => {
    row.onclick = () => {
      sessionBallOpen = false;
      panel.style.display = "none";
      goChat(row.dataset.a, (list.find((x) => x.agent_id === row.dataset.a) || {}).agent_name || row.dataset.a, row.dataset.t);
    };
  });
}

function updateSessionBall() {
  ensureSessionBall();
  const wrap = $("#sessionBallWrap");
  const panel = $("#sessionBallPanel");
  const textEl = $("#sessionBallText");
  if (!wrap || !textEl) return;
  const running = Number(sessionSnapshot.counts && sessionSnapshot.counts.running) || 0;
  const completed = Number(sessionSnapshot.counts && sessionSnapshot.counts.completed) || 0;
  const list = sessionSnapshot.sessions || [];
  // 用户就在唯一的那个“正在进行”会话里：悬浮球按需求保持隐藏；点返回后 render()
  // 会立即重算并显示。若还有其它待验收/进行中的会话，则仍显示以便跳转。
  const onlyCurrent = list.length === 1
    && S.view === "chat"
    && S.agentId === list[0].agent_id
    && S.threadId === list[0].thread_id;
  if (!list.length || onlyCurrent) {
    wrap.style.display = "none";
    sessionBallOpen = false;
    if (panel) panel.style.display = "none";
    return;
  }
  wrap.style.display = "";
  const label = completed > 0
    ? t("当前有 {a} 个对话正在进行，{b} 个已经完工").replace("{a}", running).replace("{b}", completed)
    : t("当前有 {a} 个对话正在进行").replace("{a}", running);
  textEl.textContent = label;
  const btn = $("#sessionBallBtn");
  if (btn) {
    btn.title = label;
    btn.classList.toggle("has-done", completed > 0);
  }
  const r = wrap.getBoundingClientRect();
  const onLeft = r.left < window.innerWidth / 2;
  panel.classList.toggle("panel-right", onLeft);
  panel.classList.toggle("panel-left", !onLeft);
  if (sessionBallOpen) {
    panel.style.display = "";
    updateSessionBallPanel();
    requestAnimationFrame(positionSessionBallPanel);
  }
}

function startSessionPolling() {
  if (sessionPollTimer) return;
  refreshSessions();
  sessionPollTimer = setInterval(refreshSessions, 2000);
}

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
  flushCurrentDraft();
  if (findState.open) findClose();
  // 切视图只解绑当前会话的 DOM；后台 SessionStream / 浏览器运行继续。
  if (activeSessionStream) {
    activeSessionStream.detachView();
    activeSessionStream = null;
  }
  if (S.view !== "chat") reportActiveView(null, null);
  app.innerHTML = "";
  $$(".edit-popup").forEach(p => p.remove());
  const errWrap = $(".edit-error-wrap");
  if (errWrap && (S.view !== "chat" || errWrap.dataset.thread !== String(S.threadId))) errWrap.remove();
  isRunning = false; ws = null; currentReplyEl = null; editorDirty = false; statusMode = "idle";
  if (S.view === "agents") renderAgents();
  else if (S.view === "editor") renderEditorView();
  else if (S.view === "threads") renderThreadsView();
  else if (S.view === "chat") renderChatView();
  else if (S.view === "snapshot") renderSnapshotView();
  ensureSessionBall();
  updateSessionBall();
  // 离开聊天视图时立刻补一次状态，避免等 2s 轮询才看到悬浮球/线程状态。
  if (S.view !== "chat") refreshSessions();
}
function goAgents() { S.view = "agents"; S.agentId = null; S.agentName = null; S.threadId = null; S.snapshotId = null; S.snapshotFromDeleted = false; render(); }
function goThreads(agentId, agentName) { S.view = "threads"; S.agentId = agentId; S.agentName = agentName; S.threadId = null; S.snapshotId = null; S.snapshotFromDeleted = false; render(); }
function goChat(agentId, agentName, threadId) { S.view = "chat"; S.agentId = agentId; S.agentName = agentName; S.threadId = threadId; S.snapshotId = null; S.snapshotFromDeleted = false; render(); }
function goSnapshot(agentId, agentName, snapshotId, fromDeleted = false) { S.view = "snapshot"; S.agentId = agentId; S.agentName = agentName; S.snapshotId = snapshotId; S.snapshotFromDeleted = !!fromDeleted; render(); }

/* ================= 视图1：Agent 列表 ================= */
async function renderAgents() {
  app.innerHTML = topbar();
  const view = document.createElement("div");
  view.className = "view";
  view.style.display = "flex";
  view.style.flexDirection = "column";
  app.appendChild(view);
  view.innerHTML = `
    <div style="display:flex;align-items:center;margin-bottom:20px;gap:12px;">
      <h2 class="section-title" style="margin:0;">${t("Multi-Agent 配置")}</h2>
      <div class="spacer" style="flex:1;"></div>
      <button class="btn small" id="langBtn" title="切换语言 / Switch language">${lang === "zh" ? "EN" : "中文"}</button>
      <button class="gear-btn" id="gearBtn" title="${t("系统设置")}">⚙️</button>
      <button class="btn small" id="editSensitiveBtn">${t("编辑敏感信息表单")}</button>
      <button class="btn small" id="editGlobalPromptBtn" style="display:none;">${t("编辑全局常用prompt")}</button>
      <button class="btn small" id="editDefaultBtn">${t("编辑默认配置")}</button>
      <button class="btn primary" id="newBtn">+ ${t("新建 multi-agent")}</button>
    </div>
    <div id="agentGridWrap" style="flex:1;overflow:auto;min-height:0;">
      <div id="agentGrid" class="card-grid"><div class="muted">${t("加载中…")}</div></div>
    </div>
    <div class="snapshot-drawer" id="agentsFooter">
      <button class="snapshot-handle" id="deletedAgentsHandle" type="button" style="display:none;">🗑️ ${t("查看已删除multi-agent的快照")}<span class="sw-label" id="deletedAgentsCount"></span></button>
      <button class="showhidden-zone" id="showHiddenAgentsZone" type="button" style="display:none;border-left:none;">
        <span class="sw-label" id="showHiddenAgentsLabel"></span>
        <span class="toggle"><input type="checkbox" id="showHiddenAgentsChk"><span class="track"></span></span>
      </button>
      <div class="snapshot-panel" id="deletedAgentsPanel" style="display:none;">
        <div class="snapshot-panel-head">
          <span class="snapshot-panel-title">🗑️ ${t("查看已删除multi-agent的快照")}</span>
          <button class="btn small" id="deletedAgentsClose">×</button>
        </div>
        <div class="snapshot-list" id="deletedAgentsList"><div class="muted">${t("加载中…")}</div></div>
      </div>
    </div>`;

  $("#newBtn").onclick = () => { S.editingDefault = false; S.agentId = null; S.view = "editor"; render(); };
  $("#editDefaultBtn").onclick = () => { S.editingDefault = true; S.agentId = null; S.view = "editor"; render(); };
  $("#editSensitiveBtn").onclick = () => openSensitiveForm();
  // 「编辑全局常用prompt」：显示与否由系统设置「开启常用prompt注入功能」决定（关闭时隐藏，数据保留）
  const globalPromptBtn = $("#editGlobalPromptBtn");
  if (globalPromptBtn) {
    globalPromptBtn.onclick = () => openGlobalPromptModal();
    try {
      const st = await getSettings();
      globalPromptBtn.style.display = st.common_prompt_enabled === false ? "none" : "";
    } catch (e) { globalPromptBtn.style.display = "none"; }
  }
  $("#gearBtn").onclick = () => openSettings();
  $("#langBtn").onclick = () => setLang(lang === "zh" ? "en" : "zh");

  const hiddenKey = "hidden_agents";
  const showKey = "show_hidden_agents";
  const hiddenSet = new Set(JSON.parse(localStorage.getItem(hiddenKey) || "[]"));

  let agents;
  try { agents = await api("/api/agents"); }
  catch (e) { $("#agentGrid").innerHTML = `<div class="empty"><div class="big">⚠️</div>${esc(e.message)}</div>`; return; }

  // 复用已加载的 agents，仅原地重绘列表，避免整页 flash /「加载中」闪屏
  function drawAgentsBox() {
    const showNow = localStorage.getItem(showKey) === "1";
    const vis = showNow ? agents : agents.filter(a => !hiddenSet.has(a.agent_id));
    const hiddenCnt = agents.filter(a => hiddenSet.has(a.agent_id)).length;

    // 「显示隐藏multi-agent配置」仅在有隐藏项时才显示（与「显示隐藏对话」一致）
    if (hiddenCnt > 0) {
      $("#showHiddenAgentsChk").checked = showNow;
      $("#showHiddenAgentsLabel").textContent = t("显示隐藏multi-agent配置") + `（${hiddenCnt}）`;
      $("#showHiddenAgentsZone").style.display = "flex";
    } else {
      $("#showHiddenAgentsZone").style.display = "none";
    }
    updateAgentsFooter();

    if (!agents.length) {
      $("#agentGrid").innerHTML = `<div class="empty"><div class="big">📦</div>${t("还没有任何 multi-agent 配置")}<br/><br/><button class="btn primary" id="newBtn2">+ ${t("新建 multi-agent")}</button></div>`;
      const n = $("#newBtn2"); if (n) n.onclick = () => { S.editingDefault = false; S.agentId = null; S.view = "editor"; render(); };
      return;
    }

    if (!vis.length) {
      $("#agentGrid").innerHTML = `<div class="empty"><div class="big">📦</div>${t("所有 multi-agent 配置均已隐藏")}</div>`;
      return;
    }

    $("#agentGrid").innerHTML = vis.map(a => {
      const hidden = hiddenSet.has(a.agent_id);
      return `
      <div class="agent-card ${hidden ? "agent-hidden" : ""}" data-id="${esc(a.agent_id)}">
        ${agentRibbonsHTML()}
        <h3>${esc(a.name)}${hidden ? ` <span class="hidden-tag">${t("已隐藏")}</span>` : ""}</h3>
        <div class="meta">
          ${t("checkpoint 库")}：<code>${esc(a.postgres.checkpoint_database)}</code><br/>
          ${t("namespace")}：<code>${esc((a.postgres.store_namespace || []).join(",") || "—")}</code><br/>
          ${t("子 agent")}：${a.sub_agents.length} ${t("个")}（${a.sub_agents.map(s => esc(s.name)).join(t("、")) || t("无")}）<br/>
          ${t("主模型")}：<code>${esc(a.main_agent.llm_provider_name)}</code>
        </div>
        <div class="actions">
          <button class="btn card-btn card-btn-edit small" data-act="edit">${t("编辑")}</button>
          <button class="btn card-btn card-btn-default small" data-act="default">${t("设为默认")}</button>
          <button class="btn card-btn card-btn-hide small" data-act="hide">${hidden ? t("取消隐藏") : t("隐藏")}</button>
          <button class="btn card-btn card-btn-danger small" data-act="del">${t("删除")}</button>
        </div>
      </div>`;
    }).join("");

    $$(".agent-card").forEach(card => {
      const id = card.dataset.id;
      const name = agents.find(a => a.agent_id === id).name;
      card.addEventListener("click", e => { if (e.target.closest("button")) return; goThreads(id, name); });
      $$("button", card).forEach(btn => {
        btn.onclick = (e) => {
          e.stopPropagation();
          const act = btn.dataset.act;
          if (act === "edit") { S.editingDefault = false; S.agentId = id; S.view = "editor"; render(); }
          else if (act === "default") setDefault(id);
          else if (act === "hide") {
            if (hiddenSet.has(id)) hiddenSet.delete(id);
            else hiddenSet.add(id);
            localStorage.setItem(hiddenKey, JSON.stringify([...hiddenSet]));
            drawAgentsBox();
          }
          else if (act === "del") deleteAgent(id, name);
        };
      });
    });
  }
  drawAgentsBox();

  $("#showHiddenAgentsChk").onchange = (e) => { localStorage.setItem(showKey, e.target.checked ? "1" : "0"); drawAgentsBox(); };
  $("#showHiddenAgentsZone").onclick = (e) => {
    e.preventDefault();
    const chk = $("#showHiddenAgentsChk");
    chk.checked = !chk.checked;
    chk.dispatchEvent(new Event("change"));
  };

  // ── 已删除 multi-agent 的快照抽屉 ─────────
  // 配置已被删除（或无法从 app 访问）的 agent，其快照仍留在 snapshots/ 下。
  // 这里把它们列出来，可就地展开看快照、删单个、或删掉该 agent 的全部快照。
  const delHandle = $("#deletedAgentsHandle");
  const delPanel = $("#deletedAgentsPanel");
  const delListEl = $("#deletedAgentsList");
  let deletedAgents = [];
  const expandedDirs = new Set();

  // 底部栏可见性：两个按钮都不可见时整条底栏隐藏；分隔线只在两者都可见时出现
  function updateAgentsFooter() {
    const sh = $("#showHiddenAgentsZone");
    const dh = $("#deletedAgentsHandle");
    const shVisible = sh.style.display !== "none";
    const dhVisible = dh.style.display !== "none";
    sh.style.borderLeft = (shVisible && dhVisible) ? "" : "none";
    $("#agentsFooter").style.display = (shVisible || dhVisible) ? "" : "none";
  }

  function renderDeletedAgentSnaps(dir, aName, container) {
    container.innerHTML = `<div class="muted" style="padding:8px 12px;">${t("加载中…")}</div>`;
    api(`/api/agents/${encodeURIComponent(dir)}/snapshots`).then(snaps => {
      if (!snaps.length) {
        container.innerHTML = `<div class="muted" style="padding:8px 12px;">${t("暂无快照")}</div>`;
        return;
      }
      container.innerHTML = snaps.map(s => `
        <div class="snapshot-row" data-sid="${esc(s.id)}">
          <div class="snapshot-info">
            <span class="snapshot-name">${esc(s.thread_id)}</span>
            <span class="snapshot-meta">${esc(s.created_at)} · ${s.total_messages} ${t("条消息")}</span>
          </div>
          <button class="btn card-btn card-btn-danger small" data-snap-del="${esc(s.id)}">${t("删除")}</button>
        </div>`).join("");
      $$(".snapshot-row", container).forEach(row => {
        const sid = row.dataset.sid;
        row.onclick = (e) => { if (e.target.closest("button")) return; goSnapshot(dir, aName, sid, true); };
        row.querySelector("[data-snap-del]").onclick = async (e) => {
          e.stopPropagation();
          if (!confirm(`${t("确定删除快照")}「${sid}」？${t("此操作不可撤销。")}`)) return;
          try {
            await api(`/api/agents/${encodeURIComponent(dir)}/snapshots/${encodeURIComponent(sid)}`, { method: "DELETE" });
            toast(t("已删除"));
            await refreshDeletedAgents();
          } catch (err) { toast(err.message, true); }
        };
      });
    }).catch(e => { container.innerHTML = `<div class="empty">${esc(e.message)}</div>`; });
  }

  function renderDeletedAgentsList() {
    if (!deletedAgents.length) {
      delListEl.innerHTML = `<div class="muted" style="padding:12px;">${t("暂无已删除的multi-agent")}</div>`;
      return;
    }
    delListEl.innerHTML = deletedAgents.map(a => `
      <div class="deleted-agent-item" data-dir="${esc(a.dir)}" data-name="${esc(a.name)}">
        <div class="deleted-agent-head">
          <button class="deleted-agent-toggle" type="button">
            <span class="snapshot-name">${esc(a.name)}</span>
            <span class="snapshot-meta">${a.count} ${t("个快照")}</span>
          </button>
          <button class="btn card-btn card-btn-danger small" data-del-all="${esc(a.dir)}">${t("删除全部快照")}</button>
        </div>
        <div class="deleted-agent-snaps" style="display:none;"></div>
      </div>`).join("");

    $$(".deleted-agent-item", delListEl).forEach(item => {
      const dir = item.dataset.dir;
      const aName = item.dataset.name;
      const snapsEl = item.querySelector(".deleted-agent-snaps");
      item.querySelector(".deleted-agent-toggle").onclick = () => {
        const open = snapsEl.style.display !== "none";
        if (open) { snapsEl.style.display = "none"; expandedDirs.delete(dir); return; }
        snapsEl.style.display = "";
        expandedDirs.add(dir);
        renderDeletedAgentSnaps(dir, aName, snapsEl);
      };
      item.querySelector("[data-del-all]").onclick = async (e) => {
        e.stopPropagation();
        if (!confirm(`${t("确定删除该 multi-agent 的全部快照")}「${aName}」？${t("此操作不可撤销。")}`)) return;
        try {
          await api(`/api/deleted-agents/${encodeURIComponent(dir)}/snapshots`, { method: "DELETE" });
          toast(t("已删除"));
          await refreshDeletedAgents();
        } catch (err) { toast(err.message, true); }
      };
    });

    // 刷新后恢复仍在的展开项
    $$(".deleted-agent-item", delListEl).forEach(item => {
      const dir = item.dataset.dir;
      if (expandedDirs.has(dir)) {
        const snapsEl = item.querySelector(".deleted-agent-snaps");
        snapsEl.style.display = "";
        renderDeletedAgentSnaps(dir, item.dataset.name, snapsEl);
      }
    });
  }

  async function refreshDeletedAgents() {
    try { deletedAgents = await api("/api/deleted-agents"); }
    catch (e) { deletedAgents = []; }
    const dirs = new Set(deletedAgents.map(a => a.dir));
    [...expandedDirs].forEach(d => { if (!dirs.has(d)) expandedDirs.delete(d); });
    delHandle.style.display = deletedAgents.length ? "" : "none";
    $("#deletedAgentsCount").textContent = deletedAgents.length ? `（${deletedAgents.length}）` : "";
    updateAgentsFooter();
    renderDeletedAgentsList();
  }

  delHandle.onclick = () => {
    const open = delPanel.style.display !== "none";
    delPanel.style.display = open ? "none" : "";
    if (!open) refreshDeletedAgents();
  };
  $("#deletedAgentsClose").onclick = () => { delPanel.style.display = "none"; };

  refreshDeletedAgents();
}

async function openSettings() {
  let s;
  try { s = await getSettings(); } catch (e) { toast(e.message, true); return; }
  const mask = document.createElement("div");
  mask.className = "modal-mask";
  mask.innerHTML = `
    <div class="modal settings-modal" data-modal-key="settings">
      <h3>⚙️ ${t("系统设置")}</h3>
      <div class="settings-cols">
      <div class="switch-row">
        <span class="sw-label">${t("修改未保存时提醒")} <i class="info-icon">!<span class="tip">${t("编辑 multi-agent 配置时，若做了改动但未保存就离开，弹窗确认。")}</span></i></span>
        <label class="toggle"><input type="checkbox" id="set_warn" ${s.warn_unsaved_changes ? "checked" : ""}><span class="track"></span></label>
      </div>
      <div class="switch-row">
        <span class="sw-label">${t("显示输入示例（灰色占位字）")} <i class="info-icon">!<span class="tip">${t("新建 / 编辑 multi-agent 时，空输入框显示灰色示例文字，帮助快速上手。")}</span></i></span>
        <label class="toggle"><input type="checkbox" id="set_ph" ${s.show_placeholders ? "checked" : ""}><span class="track"></span></label>
      </div>
      <div class="settings-group">
        <div class="switch-row">
          <span class="sw-label">${t("记忆吸附")} <i class="info-icon">!<span class="tip">${t("开启后，主 agent 收到用户消息时会先从记忆库语义检索 N 条相关记忆，附在用户消息里一起传入。此设置影响图编译，进入某个 multi-agent 后不可改动，需退回主界面。")}</span></i></span>
          <label class="toggle"><input type="checkbox" id="set_mem" ${s.memory_attach ? "checked" : ""}><span class="track"></span></label>
        </div>
        <div class="switch-row">
          <span class="sw-label">${t("吸附记忆条数")}</span>
          <input type="number" id="set_memnum" value="${s.num_memories_attached}" min="1" max="20" ${s.memory_attach ? "" : "disabled"}>
        </div>
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
        <span class="sw-label">${t("发送消息后自动跟随最新输出")} <i class="info-icon">!<span class="tip">${t("发送新消息后，聊天区自动下滑到最新消息并跟随流式输出；鼠标上滑即可停止跟随，方便回看历史，不会像图钉那样锁死。")}</span></i></span>
        <label class="toggle"><input type="checkbox" id="set_autoscroll" ${s.auto_scroll_on_send !== false ? "checked" : ""}><span class="track"></span></label>
      </div>
      <div class="switch-row">
        <span class="sw-label">${t("显示浏览器接管说明弹窗")} <i class="info-icon">!<span class="tip">${t("每次打开浏览器时，弹窗说明红色浮动按钮（停止/继续）的作用。关闭后不再弹出。")}</span></i></span>
        <label class="toggle"><input type="checkbox" id="set_browser_popup" ${s.browser_takeover_intro_popup !== false ? "checked" : ""}><span class="track"></span></label>
      </div>
      <div class="settings-group">
        <div class="switch-row">
          <span class="sw-label">${t("默认总结百分比")} <i class="info-icon">!<span class="tip">${t("全量总结时，提示模型把历史压缩到的目标比例（占历史 token 的百分比）。默认与推荐 20%（即五分之一），数值越小越省 token。")}</span></i></span>
          <span class="sw-inline"><input type="number" id="set_summary_pct" min="1" max="100" value="${s.summary_token_percent ?? 20}"><span class="pct-suffix">%</span></span>
        </div>
        <div class="switch-row">
          <span class="sw-label">${t("主动总结时手动设置总结百分比")} <i class="info-icon">!<span class="tip">${t("开启后，点击「主动全量总结」会先弹出百分比设置框，可为本次总结单独指定压缩比例；关闭时直接使用上面的默认总结百分比。")}</span></i></span>
          <label class="toggle"><input type="checkbox" id="set_summary_custom" ${s.proactive_summary_custom_percent ? "checked" : ""}><span class="track"></span></label>
        </div>
      </div>
      <div class="settings-group">
        <div class="switch-row">
          <span class="sw-label">${t("裸公式识别")} <i class="info-icon">!<span class="tip">${t("模型偶尔不带 $ 或 \\( 分隔符直接输出公式（如 s_{t+1}=f(s_t,a_t)）。开启后自动识别并渲染，适合科研 / 数理场景；日常场景建议关闭，以免误判普通文本。")}</span></i></span>
          <label class="toggle"><input type="checkbox" id="set_baremath" ${s.bare_math_detect ? "checked" : ""}><span class="track"></span></label>
        </div>
        <div class="switch-row">
          <span class="sw-label">${t("激进公式渲染")} <i class="info-icon">!<span class="tip">${t("在「裸公式识别」基础上更激进：支持单字符上下标（如 V_φ、V^π）、希腊字母与数学符号，并会把「看起来是公式」的代码块 / 行内代码也渲染成公式。适合数理场景，可能误判普通文本或代码，建议按需开启。")}</span></i></span>
          <label class="toggle"><input type="checkbox" id="set_aggressive_math" ${s.aggressive_math_detect ? "checked" : ""}><span class="track"></span></label>
        </div>
      </div>
      <div class="settings-group">
        <div class="switch-row">
          <span class="sw-label">🖨 ${t("转换 HTML")} <i class="info-icon">!<span class="tip">${t("开启后，浏览历史时每条助手回复下方会出现「转换 HTML」按钮，可将该回复转成可打印的 HTML 文件。")}</span></i></span>
          <label class="toggle"><input type="checkbox" id="set_export_html" ${s.export_html ? "checked" : ""}><span class="track"></span></label>
        </div>
        <div id="exportHtmlFields" style="${s.export_html ? "" : "display:none;"}">
          <div class="switch-row">
            <span class="sw-label">${t("HTML 输出路径")} <i class="info-icon">!<span class="tip">${t("生成 HTML 文件的输出目录（绝对路径）。")}</span></i></span>
            <input type="text" id="set_export_html_path" value="${esc(s.export_html_path || "")}" placeholder="例如 C:\\Agent_WorkPlace\\html">
          </div>
        </div>
        <div class="switch-row">
          <span class="sw-label">📄 ${t("显示转换本地md按钮")} <i class="info-icon">!<span class="tip">${t("开启后，会话页「编辑会话常用prompt」左侧会出现「将本地md转换为HTML」按钮：点它可从本机选择一个 .md 文件，用上面的「HTML 转换配置表」把它转成可打印 HTML，并保存在该 md 文件同目录下（同名 .html；已存在时另存为带时间戳的文件）。")}</span></i></span>
          <label class="toggle"><input type="checkbox" id="set_local_md_convert" ${s.show_local_md_convert_button !== false ? "checked" : ""}><span class="track"></span></label>
        </div>
      </div>
      <div class="settings-group">
        <div class="switch-row">
          <span class="sw-label">📄 ${t("导出 md 文件")} <i class="info-icon">!<span class="tip">${t("开启后，浏览历史时每条助手回复下方会出现「导出 md 文件」按钮，可将该回复保存为 Markdown 文件。")}</span></i></span>
          <label class="toggle"><input type="checkbox" id="set_export_md" ${s.export_md ? "checked" : ""}><span class="track"></span></label>
        </div>
        <div id="exportMdFields" style="${s.export_md ? "" : "display:none;"}">
          <div class="switch-row">
            <span class="sw-label">${t("md 输出路径")} <i class="info-icon">!<span class="tip">${t("生成 md 文件的输出目录（绝对路径）。")}</span></i></span>
            <input type="text" id="set_export_md_path" value="${esc(s.export_md_path || "")}" placeholder="例如 C:\\Agent_WorkPlace\\md">
          </div>
        </div>
      </div>
      <div class="settings-group">
        <div class="switch-row">
          <span class="sw-label">✏️ ${t("开启编辑模式")} <i class="info-icon">!<span class="tip">${t("开启后，会话界面右上角会出现「进入编辑模式」按钮，可编辑历史消息。")}</span></i></span>
          <label class="toggle"><input type="checkbox" id="set_edit_enabled" ${s.edit_mode_enabled !== false ? "checked" : ""}><span class="track"></span></label>
        </div>
        <div id="editFields" style="${s.edit_mode_enabled !== false ? "" : "display:none;"}">
          <div class="switch-row">
            <span class="sw-label">🕰 ${t("历史消息任意时间可编辑")} <i class="info-icon">!<span class="tip">${t("开启后，无论多久远的历史消息都能进入编辑模式编辑；关闭时只编辑最近一轮对话。")}</span></i></span>
            <label class="toggle"><input type="checkbox" id="set_edit_any" ${s.edit_any_history ? "checked" : ""}><span class="track"></span></label>
          </div>
          <div class="switch-row">
            <span class="sw-label">🧩 ${t("历史消息所有类型可编辑")} <i class="info-icon">!<span class="tip">${t("开启后，用户消息、思考过程、工具调用、工具结果都能编辑；关闭时只编辑 AI 回复正文。")}</span></i></span>
            <label class="toggle"><input type="checkbox" id="set_edit_types" ${s.edit_all_message_types ? "checked" : ""}><span class="track"></span></label>
          </div>
        </div>
      </div>
      </div>
      <div class="modal-actions" style="justify-content:flex-start; flex-wrap:wrap;">
        <button class="btn small" id="setInputBox">${t("发送框设置")}</button>
        <button class="btn small" id="setGlobalConfig">${t("全局配置设置")}</button>
        <button class="btn small" id="setAdvanced">${t("进阶设置")}</button>
        <button class="btn small" id="setColors">${t("字体颜色设置")}</button>
        <button class="btn small" id="setCard">${t("Multi-Agent配置卡片 设置")}</button>
        <button class="btn small" id="setHtmlConfig" style="${(s.export_html || s.show_local_md_convert_button !== false) ? "" : "display:none;"}">${t("HTML 转换配置表")}</button>
        <button class="btn small" id="setToolResultDisplay">${t("工具调用结果显示设置")}</button>
      </div>
      <div class="modal-actions" style="margin-top:10px;">
        <div class="spacer" style="flex:1;"></div>
        <button class="btn" id="setCancel">${t("取消")}</button>
        <button class="btn primary" id="setSave">${t("保存")}</button>
      </div>
    </div>`;
  document.body.appendChild(mask);
  mask.querySelector("#set_mem").addEventListener("change", e => { mask.querySelector("#set_memnum").disabled = !e.target.checked; });
  mask.querySelector("#set_sound").addEventListener("change", e => playSound(e.target.value));
  // 「HTML 转换配置表」同时服务「转换 HTML」（历史消息按钮）与「将本地md转换为HTML」，任一开启即可见
  const syncHtmlConfigBtn = () => {
    const on = mask.querySelector("#set_export_html").checked
      || mask.querySelector("#set_local_md_convert").checked;
    mask.querySelector("#setHtmlConfig").style.display = on ? "" : "none";
  };
  mask.querySelector("#set_export_html").addEventListener("change", e => {
    const on = e.target.checked;
    mask.querySelector("#exportHtmlFields").style.display = on ? "" : "none";
    syncHtmlConfigBtn();
  });
  mask.querySelector("#set_local_md_convert").addEventListener("change", syncHtmlConfigBtn);
  mask.querySelector("#set_export_md").addEventListener("change", e => {
    mask.querySelector("#exportMdFields").style.display = e.target.checked ? "" : "none";
  });
  mask.querySelector("#set_edit_enabled").addEventListener("change", e => {
    mask.querySelector("#editFields").style.display = e.target.checked ? "" : "none";
  });

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
  mask.querySelector("#setInputBox").onclick = () => { mask.remove(); openInputBoxSettings(); };
  mask.querySelector("#setGlobalConfig").onclick = () => { mask.remove(); openGlobalConfigSettings(); };
  mask.querySelector("#setAdvanced").onclick = () => { mask.remove(); openAdvancedSettings(); };
  mask.querySelector("#setColors").onclick = () => { mask.remove(); openColorSettings(); };
  mask.querySelector("#setCard").onclick = () => { mask.remove(); openCardSettings(); };
  mask.querySelector("#setHtmlConfig").onclick = () => { mask.remove(); openHtmlConfigSettings(); };
  mask.querySelector("#setToolResultDisplay").onclick = () => { mask.remove(); openToolResultDisplaySettings(); };
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
        aggressive_math_detect: mask.querySelector("#set_aggressive_math").checked,
        export_html: mask.querySelector("#set_export_html").checked,
        export_html_path: mask.querySelector("#set_export_html_path").value,
        show_local_md_convert_button: mask.querySelector("#set_local_md_convert").checked,
        export_md: mask.querySelector("#set_export_md").checked,
        export_md_path: mask.querySelector("#set_export_md_path").value,
        edit_mode_enabled: mask.querySelector("#set_edit_enabled").checked,
        edit_any_history: mask.querySelector("#set_edit_any").checked,
        edit_all_message_types: mask.querySelector("#set_edit_types").checked,
        auto_scroll_on_send: mask.querySelector("#set_autoscroll").checked,
        browser_takeover_intro_popup: mask.querySelector("#set_browser_popup").checked,
        summary_token_percent: Math.min(100, Math.max(1, +mask.querySelector("#set_summary_pct").value || 20)),
        proactive_summary_custom_percent: mask.querySelector("#set_summary_custom").checked,
      });
      mask.remove();
      toast(t("设置已保存"));
    } catch (e) { toast(e.message, true); }
  };
}

async function openInputBoxSettings() {
  let s;
  try { s = await getSettings(); } catch (e) { toast(e.message, true); return; }
  const mask = document.createElement("div");
  mask.className = "modal-mask";
  mask.innerHTML = `
    <div class="modal" style="width:520px;" data-modal-key="inputBox">
      <h3>💬 ${t("发送框设置")}</h3>
      <div class="muted" style="margin-bottom:10px;">${t("用于集中管理聊天输入框相关的显示与注入开关。")}</div>
      <div class="switch-row">
        <span class="sw-label">${t("显示「注入当前日期」按钮")} <i class="info-icon">!<span class="tip">${t("开启后，聊天输入框右侧会出现「注入当前日期」按钮；开启该按钮后，发送消息时会在用户消息前面拼接当前日期。")}</span></i></span>
        <label class="toggle"><input type="checkbox" id="set_date_inject" ${s.show_date_inject_button !== false ? "checked" : ""}><span class="track"></span></label>
      </div>
      <div class="switch-row">
        <span class="sw-label">${t("显示「注入当前时间」按钮")} <i class="info-icon">!<span class="tip">${t("开启后，聊天输入框右侧会出现「注入当前时间」按钮；开启该按钮后，发送消息时会在用户消息前面拼接当前时间。")}</span></i></span>
        <label class="toggle"><input type="checkbox" id="set_time_inject" ${s.show_time_inject_button !== false ? "checked" : ""}><span class="track"></span></label>
      </div>
      <div class="switch-row">
        <span class="sw-label">${t("显示「代码输入模式」按钮")} <i class="info-icon">!<span class="tip">${t("开启后，聊天输入框右侧会出现「代码输入模式」按钮；开启该按钮后，在输入框内按 Tab 会插入 4 个空格（而不是切换焦点）。")}</span></i></span>
        <label class="toggle"><input type="checkbox" id="set_code_input" ${s.show_code_input_button !== false ? "checked" : ""}><span class="track"></span></label>
      </div>
      <div class="switch-row">
        <span class="sw-label">${t("开启常用prompt注入功能")} <i class="info-icon">!<span class="tip">${t("开启后，会话页会出现「注入常用prompt」「编辑会话常用prompt」按钮，主界面会出现「编辑全局常用prompt」按钮；关闭后这三个按钮都不再显示，但已保存的全局常用 prompt 库与每个会话的设置都会一直保留。")}</span></i></span>
        <label class="toggle"><input type="checkbox" id="set_common_prompt" ${s.common_prompt_enabled !== false ? "checked" : ""}><span class="track"></span></label>
      </div>
      <div class="modal-actions">
        <button class="btn" id="ibCancel">${t("返回")}</button>
        <button class="btn primary" id="ibSave">${t("保存")}</button>
      </div>
    </div>`;
  document.body.appendChild(mask);
  mask.querySelector("#ibCancel").onclick = () => { mask.remove(); openSettings(); };
  mask.querySelector("#ibSave").onclick = async () => {
    try {
      await saveSettings({
        ...s,
        show_date_inject_button: mask.querySelector("#set_date_inject").checked,
        show_time_inject_button: mask.querySelector("#set_time_inject").checked,
        show_code_input_button: mask.querySelector("#set_code_input").checked,
        common_prompt_enabled: mask.querySelector("#set_common_prompt").checked,
      });
      mask.remove();
      toast(t("设置已保存"));
    } catch (e) { toast(e.message, true); }
  };
}

async function openGlobalConfigSettings() {
  let migration = null;
  try { migration = await api("/api/store-migrations?probe=0"); } catch (e) { /* 迁移状态拿不到不影响其它设置 */ }
  const migrationCount = migration && migration.sources ? migration.sources.length : 0;
  const currentStoreDb = (migration && migration.current_database) || "Long_Term_Memory";

  const mask = document.createElement("div");
  mask.className = "modal-mask";
  mask.innerHTML = `
    <div class="modal" style="width:560px;" data-modal-key="globalConfig">
      <h3>🌍 ${t("全局配置设置")}</h3>
      <div class="muted" style="margin-bottom:10px;line-height:1.65;">${t("用于集中管理全局 postgres、store 数据库、embedding 与自动（及主动）总结设置。")}</div>
      <div class="conn-example" style="margin-bottom:12px;">🧠 ${t("当前全局 store 数据库：")}<code>${esc(currentStoreDb)}</code></div>
      <div class="modal-actions" style="justify-content:flex-start; flex-wrap:wrap;">
        <button class="btn small" id="gcPg">${t("全局postgres连接设置")}</button>
        <button class="btn small" id="gcStoreDb">${t("全局store数据库名称")}</button>
        <button class="btn small" id="gcEmb">${t("全局embedding模型设置")}</button>
        <button class="btn small" id="gcSum">${t("全局自动（及主动）总结设置")}</button>
        ${migrationCount ? `<button class="btn small" id="gcMigrate">${t("迁移旧 store 数据库")}（${migrationCount}）</button>` : ""}
      </div>
      <div class="modal-actions">
        <button class="btn" id="gcCancel">${t("返回")}</button>
      </div>
    </div>`;
  document.body.appendChild(mask);
  mask.querySelector("#gcCancel").onclick = () => { mask.remove(); openSettings(); };
  mask.querySelector("#gcPg").onclick = () => { mask.remove(); openGlobalPostgresSettings(() => openGlobalConfigSettings()); };
  mask.querySelector("#gcStoreDb").onclick = () => { mask.remove(); openGlobalStoreDatabaseSettings(() => openGlobalConfigSettings()); };
  mask.querySelector("#gcEmb").onclick = () => { mask.remove(); openGlobalEmbeddingSettings(() => openGlobalConfigSettings()); };
  mask.querySelector("#gcSum").onclick = () => { mask.remove(); openGlobalSummarySettings(() => openGlobalConfigSettings()); };
  if (migrationCount) {
    mask.querySelector("#gcMigrate").onclick = () => { mask.remove(); openStoreMigrationModal(() => openGlobalConfigSettings()); };
  }
}

async function openGlobalStoreDatabaseSettings(back) {
  let s;
  try { s = await getSettings(); } catch (e) { toast(e.message, true); return; }
  const current = s.global_store_database || "Long_Term_Memory";

  const mask = document.createElement("div");
  mask.className = "modal-mask";
  mask.innerHTML = `
    <div class="modal" style="width:540px;" data-modal-key="globalStoreDatabase">
      <h3>🧠 ${t("全局store数据库名称")}</h3>
      <div class="muted" style="margin-bottom:10px;line-height:1.7;">
        <p style="margin:0 0 6px;">${t("所有 multi-agent 的长期记忆都统一存放在这个 PostgreSQL 数据库里；不同 multi-agent 是否共用记忆只由内部的命名空间决定。")}</p>
        <p style="margin:0;">${t("修改名称会直接把现有数据库改名（ALTER DATABASE RENAME），已有记忆会一并保留，不会新建再迁移。")}</p>
      </div>
      <div class="field full">
        <label>${t("store 数据库名称")} ${info("默认 Long_Term_Memory；只能包含字母、数字、下划线或连字符。")}</label>
        <input id="gsdb_name" value="${esc(current)}" placeholder="Long_Term_Memory">
        <span class="hint">${t("默认 Long_Term_Memory。改名后，所有 multi-agent 的长期记忆会继续使用改名后的数据库。")}</span>
      </div>
      <div class="modal-actions">
        <button class="btn" id="gsdbCancel">${t("返回")}</button>
        <button class="btn primary" id="gsdbSave">${t("保存")}</button>
      </div>
    </div>`;
  document.body.appendChild(mask);

  const close = () => mask.remove();
  mask.querySelector("#gsdbCancel").onclick = () => { close(); (back || openSettings)(); };
  mask.addEventListener("click", (e) => { if (e.target === mask) close(); });

  mask.querySelector("#gsdbSave").onclick = async () => {
    const name = (mask.querySelector("#gsdb_name").value || "").trim();
    if (!/^[A-Za-z0-9_][A-Za-z0-9_-]{0,62}$/.test(name)) {
      toast(t("store 数据库名称只能包含字母、数字、下划线或连字符，且必须以字母、数字或下划线开头"), true);
      return;
    }
    if (["postgres", "template0", "template1"].includes(name.toLowerCase())) {
      toast(t("「postgres」等系统库名称不能作为 store 数据库"), true);
      return;
    }
    if (name === current) { close(); (back || openSettings)(); return; }
    try {
      await saveSettings({ ...s, global_store_database: name });
      close();
      toast(t("全局 store 数据库已更新"));
      (back || openSettings)();
    } catch (e) { toast(e.message, true); }
  };
}

async function openStoreMigrationModal(back, auto = false) {
  let data;
  try { data = await api("/api/store-migrations"); } catch (e) { toast(e.message, true); return; }
  if (!data.sources || !data.sources.length) {
    if (!auto) {
      toast(t("没有需要迁移的旧 store 数据库"));
      (back || openSettings)();
    }
    return;
  }

  const mask = document.createElement("div");
  mask.className = "modal-mask";
  mask.innerHTML = `
    <div class="modal" style="width:640px;max-height:84vh;overflow:auto;" data-modal-key="storeMigration">
      <h3>📦 ${t("迁移旧 store 数据库")}</h3>
      <div class="muted" style="margin-bottom:10px;line-height:1.7;">
        <p style="margin:0 0 6px;">${t("旧版本允许每个 multi-agent 使用不同的 store 数据库。现在长期记忆统一存放到：")}<code>${esc(data.current_database)}</code>${t("。")}</p>
        <p style="margin:0 0 6px;">${t("迁移会把原数据库里的记忆复制到当前库，并允许修改「新数据库中的 namespace」。复制并校验成功后，程序会删除原数据库；系统库 postgres 永不删除。")}</p>
        <p style="margin:0;">${t("迁移前会先按连接前缀识别服务器（host:port）：同一 host:port 才是同一台 PostgreSQL；pgAdmin 里的「服务器」名字只是连接配置的显示名，不改变数据位置。")}</p>
      </div>
      <div id="migrationList">
        ${data.sources.map(src => {
          const nsRows = (src.namespaces || []).map(n => `
            <div class="migration-row" data-source-ns="${esc(n.source)}">
              <code>${esc(n.source)}</code>
              <span class="migration-arrow">→</span>
              <input data-migration-target value="${esc(n.target)}" placeholder="${esc(n.source)}">
            </div>`).join("");
          return `
            <div class="migration-source" data-source-id="${esc(src.id)}">
              <div class="migration-source-head">
                <b>${t("服务器：")}</b><code>${esc(src.server || t("（未知）"))}</code>
                <b>${t("原数据库：")}</b><code>${esc(src.database)}</code>
                ${src.is_system ? `<span class="migration-tag">${t("系统库，不删除")}</span>` : ""}
                ${src.agents && src.agents.length ? `<span class="muted">（${t("使用它的 multi-agent：")}${esc(src.agents.join("、"))}）</span>` : ""}
              </div>
              ${src.error ? `<div class="err-hint">⚠ ${esc(src.error)}</div>` : ""}
              ${nsRows ? `<div class="migration-rows">${nsRows}</div>` : (src.error ? "" : `<div class="muted">${t("没有可迁移的记忆条目。")}</div>`)}
              <div class="migration-source-actions">
                <button class="btn small" data-remove-source="${esc(src.id)}">${t("移除这条迁移记录")}</button>
              </div>
            </div>`;
        }).join("")}
      </div>
      <div class="modal-actions">
        <button class="btn" id="migrationCancel">${auto ? t("稍后再说") : t("返回")}</button>
        <button class="btn primary" id="migrationRun">${t("开始迁移")}</button>
      </div>
    </div>`;
  document.body.appendChild(mask);

  const close = () => mask.remove();
  const leave = () => { close(); if (back) back(); };
  mask.querySelector("#migrationCancel").onclick = leave;
  mask.addEventListener("click", (e) => { if (e.target === mask) close(); });

  mask.querySelectorAll("[data-remove-source]").forEach(btn => {
    btn.onclick = async () => {
      if ((await askConfirm(t("确定移除这条迁移记录吗？只会删除记录，不会删除数据库里的任何数据。"))) !== "yes") return;
      try {
        await api(`/api/store-migrations/${encodeURIComponent(btn.dataset.removeSource)}`, { method: "DELETE" });
        close();
        openStoreMigrationModal(back, auto);
      } catch (e) { toast(e.message, true); }
    };
  });

  mask.querySelector("#migrationRun").onclick = async () => {
    const sources = $$(".migration-source", mask).map(box => ({
      id: box.dataset.sourceId,
      namespaces: $$(".migration-row", box).map(row => ({
        source: row.dataset.sourceNs,
        target: (row.querySelector("[data-migration-target]").value || "").trim(),
      })),
    })).filter(s => s.namespaces.length);
    if (!sources.length) { toast(t("没有可迁移的记忆条目"), true); return; }
    for (const s of sources) {
      for (const n of s.namespaces) {
        if (!n.target) { toast(t("新的命名空间不能为空"), true); return; }
      }
    }
    if ((await askConfirm(t("即将把记忆复制到当前 store 数据库，并在校验成功后删除原数据库。确定开始迁移吗？"))) !== "yes") return;
    const btn = mask.querySelector("#migrationRun");
    btn.disabled = true;
    btn.textContent = t("迁移中…");
    try {
      const res = await api("/api/store-migrations/run", { method: "POST", body: JSON.stringify({ sources }) });
      const failed = (res.results || []).filter(r => r.status === "failed");
      if (failed.length) {
        toast(`${t("有迁移失败：")}${failed[0].message}`, true);
        close();
        openStoreMigrationModal(back, auto);
        return;
      }
      toast(t("迁移完成"));
      close();
      if (res.remaining > 0) openStoreMigrationModal(back, auto);
      else if (back) back();
    } catch (e) {
      btn.disabled = false;
      btn.textContent = t("开始迁移");
      toast(e.message, true);
    }
  };
}

async function maybePromptStoreMigration() {
  // 启动时最多探测 3 次：数据库服务/源库可能还在启动，避免一上来就弹「无法连接」。
  for (let i = 0; i < 3; i++) {
    try {
      const data = await api("/api/store-migrations");
      if (!data.sources || !data.sources.length || data.prompted) return;
      const reachable = data.sources.some(s => s.exists || (s.namespaces && s.namespaces.length));
      if (!reachable) {
        if (i < 2) { await sleep(2500); continue; }
        return;
      }
      await openStoreMigrationModal(null, true);
      await api("/api/store-migrations/prompted", { method: "POST" });
      return;
    } catch (e) {
      return;
    }
  }
}


async function importLocalPrefsFlow() {
  if (typeof window.__importLocalPrefs !== "function") { toast(t("导入失败：设置桥接未加载"), true); return; }
  if ((await askConfirm(t("将当前界面的本机设置（隐藏列表、卡片外观等）上传到后端保存，用于从旧浏览器迁移。确定继续？"))) !== "yes") return;
  try { await window.__importLocalPrefs(); toast(t("已导入本机设置")); } catch (e) { toast(e.message, true); }
}

async function openSensitiveForm() {
  let s;
  try { s = await getSettings(); } catch (e) { toast(e.message, true); return; }
  const mask = document.createElement("div");
  mask.className = "modal-mask";
  mask.innerHTML = `
    <div class="modal sensitive-modal" data-modal-key="sensitive">
      <h3>🔐 ${t("编辑敏感信息表单")}</h3>
      <div class="muted" style="margin-bottom:10px;">${esc(t("用于浏览器接管：Agent 遇到表单类「可填入元素」时填 <名称>，后台会替换成右侧的真实内容；左侧填「名称」，右侧填「真实内容」。表单里没有的名称会按 Agent 原文原样填入。"))}</div>
      <div class="sensitive-head"><span>${t("信息名称（表项）")}</span><span>${t("实际填入内容")}</span><span></span><span></span></div>
      <div id="sensitiveRows"></div>
      <div class="modal-actions">
        <button class="btn" id="sensCancel">${t("取消")}</button>
        <button class="btn primary" id="sensSave">${t("保存")}</button>
      </div>
    </div>`;
  document.body.appendChild(mask);

  const rowsEl = mask.querySelector("#sensitiveRows");

  function addRow(name = "", value = "", afterEl = null) {
    const row = document.createElement("div");
    row.className = "sensitive-row";
    row.innerHTML = `
      <input class="sens-name" placeholder="${t("例如 姓名")}" value="${esc(name)}">
      <input class="sens-value" placeholder="${t("例如 张三")}" value="${esc(value)}">
      <button class="btn small sens-minus" type="button" title="${t("删除本行")}">−</button>
      <button class="btn small sens-plus" type="button" title="${t("在下方新增一行")}">+</button>`;
    row.querySelector(".sens-minus").onclick = async () => {
      // 破坏性操作：删行也要再确认一次（确认后才从表单移除；点「保存」才真正落盘）
      const ans = await askConfirm(t("确定删除这一行敏感信息吗？"), {
        buttons: [{ v: "no", label: t("取消") }, { v: "yes", label: t("删除"), danger: true }],
      });
      if (ans !== "yes") return;
      row.remove();
    };
    row.querySelector(".sens-plus").onclick = () => addRow("", "", row);
    if (afterEl && afterEl.parentNode === rowsEl) afterEl.after(row);
    else rowsEl.appendChild(row);
    return row;
  }

  const initial = (s.sensitive_info || []);
  if (initial.length) initial.forEach(e => addRow(e.name || "", e.value || ""));
  else addRow();

  mask.querySelector("#sensCancel").onclick = () => mask.remove();
  mask.querySelector("#sensSave").onclick = async () => {
    try {
      const sensitive_info = $$(".sensitive-row", rowsEl)
        .map(r => ({
          name: r.querySelector(".sens-name").value.trim(),
          value: r.querySelector(".sens-value").value,
        }))
        .filter(e => e.name);
      await saveSettings({ ...s, sensitive_info });
      mask.remove();
      toast(t("设置已保存"));
    } catch (e) { toast(e.message, true); }
  };
}

/* ================= 常用 prompt 编辑弹窗（全局库 / 会话「直接输入」共用） =================
   独立浮层：自带「保存」按钮；尺寸/位置由通用浮层系统按 data-modal-key 单独记忆
   （左上/右下角把手改大小、拖标题移动，下次打开保持）。 */
function openCommonPromptEditor({ title, initialText, onSave }) {
  return new Promise((resolve) => {
    const mask = document.createElement("div");
    mask.className = "modal-mask";
    mask.innerHTML = `
      <div class="modal common-prompt-editor" data-modal-key="commonPromptEditor"
           style="width:min(640px, calc(100vw - 32px)); height:min(460px, calc(100vh - 32px));">
        <h3>${esc(title)}</h3>
        <textarea id="cpEditorText" spellcheck="false" placeholder="${t("例如：请用简体中文回答，先给结论再给理由。")}"></textarea>
        <div class="modal-actions">
          <button class="btn" id="cpEditorCancel">${t("取消")}</button>
          <button class="btn primary" id="cpEditorSave">${t("保存")}</button>
        </div>
      </div>`;
    document.body.appendChild(mask);

    const ta = mask.querySelector("#cpEditorText");
    const saveBtn = mask.querySelector("#cpEditorSave");
    ta.value = initialText || "";   // 用 value 赋值，避免多行文本被 HTML 解析吞掉首行
    let closed = false;
    const close = () => { if (closed) return; closed = true; mask.remove(); };
    mask.querySelector("#cpEditorCancel").onclick = () => { close(); resolve(null); };
    mask.addEventListener("click", (e) => { if (e.target === mask) { close(); resolve(null); } });
    saveBtn.onclick = async () => {
      const text = ta.value.trim();
      if (!text) { toast(t("常用 prompt 不能为空"), true); ta.focus(); return; }
      saveBtn.disabled = true;
      try {
        await onSave(text);
        close();
        resolve(text);
      } catch (e) {
        toast(e.message, true);
      } finally { saveBtn.disabled = false; }
    };
    ta.focus();
    try { ta.setSelectionRange(ta.value.length, ta.value.length); } catch (e) {}
  });
}

/* ================= 全局常用 prompt 库（每条一个预览框 + 独立编辑弹窗） ================= */

async function openGlobalPromptModal() {
  let s;
  try { s = await getSettings(); } catch (e) { toast(e.message, true); return; }
  const mask = document.createElement("div");
  mask.className = "modal-mask";
  mask.innerHTML = `
    <div class="modal common-prompt-modal" data-modal-key="globalPrompt">
      <h3>📝 ${t("编辑全局常用prompt")}</h3>
      <div class="muted" style="margin-bottom:10px;">${esc(t("每条常用 prompt 显示为一个预览框（最多三行，超出部分不显示）；点框内内容会打开一个独立的编辑窗口，在编辑窗口里点「保存」即生效。"))}</div>
      <div class="common-prompt-head"><span>${t("常用 prompt 内容")}</span><span></span></div>
      <div id="globalPromptRows"></div>
      <div class="modal-actions">
        <button class="btn small" id="gpAdd">+ ${t("新增一条")}</button>
        <div class="spacer" style="flex:1;"></div>
        <button class="btn" id="gpClose">${t("关闭")}</button>
      </div>
    </div>`;
  document.body.appendChild(mask);

  const rowsEl = mask.querySelector("#globalPromptRows");
  const list = () => (Array.isArray(s.common_prompts) ? s.common_prompts : []);

  // 预览框只读（最多三行）；点击框 -> 打开独立编辑弹窗改这一条
  function paint() {
    rowsEl.innerHTML = "";
    const items = list();
    if (!items.length) {
      rowsEl.innerHTML = `<div class="muted" style="padding:6px 2px;">${esc(t("全局常用prompt库为空：点下方「+ 新增一条」添加。"))}</div>`;
      return;
    }
    items.forEach((entry) => {
      const row = document.createElement("div");
      row.className = "common-prompt-row";

      const box = document.createElement("div");
      box.className = "cp-box";
      box.title = t("点击编辑");
      box.tabIndex = 0;
      box.setAttribute("role", "button");
      // 三行裁切放在内层（内层没有 padding，裁切边界正好=内容盒），否则 Chromium 会把
      // 第 4 行露进外层底部内边距里（看起来像半行被切断的残字）。
      const txt = document.createElement("span");
      txt.className = "cp-box-text";
      txt.textContent = String(entry.text || "");
      box.appendChild(txt);
      box.onclick = () => editEntry(entry);
      box.onkeydown = (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); editEntry(entry); }
      };

      // 删除：文字按钮（不再用「−」图标）；新增统一走底部「+ 新增一条」。
      const del = document.createElement("button");
      del.className = "btn small danger cp-del";
      del.type = "button";
      del.textContent = t("删除");
      del.onclick = () => removeEntry(entry);

      row.append(box, del);
      rowsEl.appendChild(row);
    });
  }

  function editEntry(entry) {
    openCommonPromptEditor({
      title: t("编辑常用prompt"),
      initialText: entry.text,
      onSave: async (text) => {
        const next = list().map(p => (p.id === entry.id ? { ...p, text } : p));
        s = await saveSettings({ ...s, common_prompts: next });
        paint();
        toast(t("已保存常用 prompt"));
      },
    });
  }

  // 新增：打开空编辑框，保存时才真正插入（取消则什么也不写）
  function addEntry(pos) {
    openCommonPromptEditor({
      title: t("新增常用prompt"),
      initialText: "",
      onSave: async (text) => {
        const next = list().slice();
        const at = Math.min(Math.max(0, pos | 0), next.length);
        next.splice(at, 0, { id: "", text });   // id 由后端按内容生成稳定标识
        s = await saveSettings({ ...s, common_prompts: next });
        paint();
        toast(t("已保存常用 prompt"));
      },
    });
  }

  // 删除：多弹一次确认；删除后指向它的会话自动回到「未设置」（后端解析不到即空）
  async function removeEntry(entry) {
    const ans = await askConfirm(t("确定删除这条常用 prompt？删除后，指向它的会话会回到「未设置」。"), {
      buttons: [{ v: "no", label: t("取消") }, { v: "yes", label: t("删除"), danger: true }],
    });
    if (ans !== "yes") return;
    try {
      const next = list().filter(p => p.id !== entry.id);
      s = await saveSettings({ ...s, common_prompts: next });
      paint();
      toast(t("已删除该条常用 prompt"));
    } catch (e) { toast(e.message, true); }
  }

  const close = () => mask.remove();
  mask.querySelector("#gpClose").onclick = close;
  mask.querySelector("#gpAdd").onclick = () => addEntry(list().length);
  mask.addEventListener("click", (e) => { if (e.target === mask) close(); });
  paint();
}

/* ================= 会话常用 prompt（从全局库选取 / 行内编辑·删除 / 直接输入新建一条） ================= */

async function openSessionPromptModal(currentRef, onSaved) {
  if (!S.agentId || !S.threadId) return;
  let s;
  try { s = await getSettings(); } catch (e) { toast(e.message, true); return; }
  // 全局库每次都从 s 现算：行内「编辑」「删除」直接改全局库，会话侧只存「指向哪一条」的指针。
  const lib = () => (Array.isArray(s.common_prompts) ? s.common_prompts : [])
    .filter(p => p && String(p.text || "").trim());
  const curRef = String(currentRef || "");          // 打开时会话指向哪一条（只有点「保存」才改指针）
  let selectedId = lib().some(p => p.id === curRef) ? curRef : "";

  const mask = document.createElement("div");
  mask.className = "modal-mask";
  mask.innerHTML = `
    <div class="modal common-prompt-modal" data-modal-key="sessionPrompt">
      <h3>📝 ${t("编辑会话常用prompt")}</h3>
      <div class="muted" style="margin-bottom:8px;">${esc(t("从下方全局库中选取一条，作为当前会话的常用 prompt。保存的是「指向该条的指针」：以后在主界面改了这一条，本会话会跟着一起变（该条被删除则本会话回到「未设置」）。每条右侧的「编辑」「删除」会直接修改全局库，删除后所有指向它的会话都会回到「未设置」。"))}</div>
      <div class="common-prompt-list" id="cpList"></div>
      <div class="modal-actions">
        <button class="btn" id="cpDirect">✏️ ${t("直接输入")}</button>
        <button class="btn" id="cpClear">${t("清空（不设置）")}</button>
        <div class="spacer" style="flex:1;"></div>
        <button class="btn" id="cpCancel">${t("取消")}</button>
        <button class="btn primary" id="cpSave">${t("保存")}</button>
      </div>
    </div>`;
  document.body.appendChild(mask);

  const listEl = mask.querySelector("#cpList");

  function paintList() {
    listEl.innerHTML = "";
    const items = lib();
    if (!items.length) {
      listEl.innerHTML = `<div class="muted">${esc(t("全局常用prompt库为空：可点「直接输入」新建一条，或先到主界面「编辑全局常用prompt」新增条目。"))}</div>`;
      return;
    }
    items.forEach((entry) => {
      const item = document.createElement("div");
      item.className = "common-prompt-item" + (entry.id === selectedId ? " sel" : "");

      // 点文本 = 选取（与原逻辑一致）；行内按钮 stopPropagation，不会误触选取
      const txt = document.createElement("div");
      txt.className = "cp-item-text";
      txt.textContent = String(entry.text || "");

      const edit = document.createElement("button");
      edit.className = "btn small cp-item-edit";
      edit.type = "button";
      edit.textContent = t("编辑");
      edit.onclick = (e) => { e.stopPropagation(); editEntry(entry); };

      const del = document.createElement("button");
      del.className = "btn small danger cp-item-del";
      del.type = "button";
      del.textContent = t("删除");
      del.onclick = (e) => { e.stopPropagation(); removeEntry(entry); };

      const acts = document.createElement("div");
      acts.className = "cp-item-actions";
      acts.append(edit, del);

      item.append(txt, acts);
      item.onclick = () => { selectedId = entry.id; paintList(); };
      listEl.appendChild(item);
    });
  }
  paintList();

  // 行内「编辑」：直接改全局库里的这一条（指针不变，所有指向它的会话一起变）
  function editEntry(entry) {
    openCommonPromptEditor({
      title: t("编辑常用prompt"),
      initialText: entry.text,
      onSave: async (text) => {
        const cur = await getSettings();     // 取最新整份设置，spread 全量保存（ID06/ID87）
        const all = Array.isArray(cur.common_prompts) ? cur.common_prompts.slice() : [];
        const idx = all.findIndex(p => p.id === entry.id);
        if (idx < 0) throw new Error(t("这条常用 prompt 已不存在，请关闭后重新打开"));
        all[idx] = { ...all[idx], text };
        s = await saveSettings({ ...cur, common_prompts: all });
        paintList();
        // 本会话正指向这一条：指针没变，只是内容变了 —— 同步聊天页的灰字提示
        if (entry.id === curRef && onSaved) onSaved(entry.id, text);
        toast(t("已保存常用 prompt"));
      },
    });
  }

  // 行内「删除」：从全局库删除（多弹一次确认）；当前会话若正指向它，回到「未设置」
  async function removeEntry(entry) {
    const isCur = entry.id === curRef;
    const ans = await askConfirm(
      isCur
        ? t("确定删除这条常用 prompt（当前会话正在使用）？删除后，本会话和所有指向它的会话都会回到「未设置」。")
        : t("确定删除这条常用 prompt？删除后，指向它的会话会回到「未设置」。"),
      { buttons: [{ v: "no", label: t("取消") }, { v: "yes", label: t("删除"), danger: true }] },
    );
    if (ans !== "yes") return;
    try {
      const cur = await getSettings();
      const all = Array.isArray(cur.common_prompts) ? cur.common_prompts : [];
      s = await saveSettings({ ...cur, common_prompts: all.filter(p => p.id !== entry.id) });
      if (selectedId === entry.id) selectedId = "";
      paintList();
      if (isCur && onSaved) onSaved("", "");   // 指针解析不到 = 未设置（不需要清理任何其它会话）
      toast(isCur ? t("已删除该条常用 prompt，当前会话已回到「未设置」") : t("已删除该条常用 prompt"));
    } catch (e) { toast(e.message, true); }
  }

  const close = () => mask.remove();
  mask.querySelector("#cpCancel").onclick = close;
  mask.addEventListener("click", (e) => { if (e.target === mask) close(); });

  // 写入「本会话指向哪一条」的指针（空串 = 清空/未设置）
  const putRef = (ref) => api(
    `/api/agents/${encodeURIComponent(S.agentId)}/threads/${encodeURIComponent(S.threadId)}/common-prompt`,
    { method: "PUT", body: JSON.stringify({ ref }) },
  );

  // 「清空（不设置）」：该会话回到「未设置」，注入按钮变灰不可点
  mask.querySelector("#cpClear").onclick = async () => {
    try {
      await putRef("");
      close();
      if (onSaved) onSaved("", "");
      toast(t("已清空当前会话的常用 prompt"));
    } catch (e) { toast(e.message, true); }
  };

  // 「直接输入」：按钮（不再是蓝点单选）→ 打开独立编辑弹窗；保存后这是**新建的一条全局
  // 常用 prompt**，并让当前会话指向它（指针语义：以后在全局库里改，这里一起变）。
  mask.querySelector("#cpDirect").onclick = () => {
    openCommonPromptEditor({
      title: t("直接输入常用prompt"),
      initialText: "",
      onSave: async (text) => {
        const curSettings = await getSettings();
        const next = (Array.isArray(curSettings.common_prompts) ? curSettings.common_prompts.slice() : []);
        next.push({ id: "", text });   // id 由后端按内容生成稳定标识
        const saved = await saveSettings({ ...curSettings, common_prompts: next });
        const all = Array.isArray(saved.common_prompts) ? saved.common_prompts : [];
        const added = all[all.length - 1];   // 新条目追加在末尾
        if (!added || !added.id) throw new Error(t("新增常用 prompt 失败"));
        await putRef(added.id);
        close();
        if (onSaved) onSaved(added.id, added.text);
        toast(t("已保存当前会话的常用 prompt"));
      },
    });
  };

  mask.querySelector("#cpSave").onclick = async () => {
    const entry = lib().find(p => p.id === selectedId);
    if (!entry) { toast(t("请先选取一条常用 prompt"), true); return; }
    try {
      await putRef(entry.id);
      close();
      if (onSaved) onSaved(entry.id, entry.text);
      toast(t("已保存当前会话的常用 prompt"));
    } catch (e) { toast(e.message, true); }
  };
}

async function openAdvancedSettings() {
  let s;
  try { s = await getSettings(); } catch (e) { toast(e.message, true); return; }
  const mask = document.createElement("div");
  mask.className = "modal-mask";
  // BID080：进阶设置新增「浏览器工具解析图片标签」开关（默认关闭）；模板字符串内不能写注释，故在此标注。
  // BID084：进阶设置新增「浏览器可交互元素整合」开关（默认开启）、整合分隔符、英文渲染开关。
  mask.innerHTML = `
    <div class="modal" style="width:480px;" data-modal-key="advanced">
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
      <div class="muted" style="margin:10px 0 2px;">${t("流式输出时显示的内容")}</div>
      <div class="switch-row">
        <span class="sw-label">🧩 ${t("流式显示子 agent 工具")} <i class="info-icon">!<span class="tip">${t("开启后，流式输出时子 agent 的工具调用与工具结果会像主 agent 一样实时显示；关闭则只显示子 agent 的正文与思考。")}</span></i></span>
        <label class="toggle"><input type="checkbox" id="adv_sub_tools" ${s.show_sub_agent_tools !== false ? "checked" : ""}><span class="track"></span></label>
      </div>
      <div class="muted" style="margin:10px 0 2px;">${t("文件工具")}</div>
      <div class="switch-row">
        <span class="sw-label">📄 ${t("PDF 表格提取")} <i class="info-icon">!<span class="tip">${t("读取 PDF 时，用基于框线的检测把表格转成 Markdown 并嵌回正文；关闭后只返回正文文本。")}</span></i></span>
        <label class="toggle"><input type="checkbox" id="adv_pdf_tables" ${s.pdf_table_extraction !== false ? "checked" : ""}><span class="track"></span></label>
      </div>
      <div class="muted" style="margin:10px 0 2px;">${t("输入消息缓存")}</div>
      <div class="switch-row">
        <span class="sw-label">💾 ${t("已输入未发送消息跨multi-agent流转")} <i class="info-icon">!<span class="tip">${t("开启后，你在任意 multi-agent 输入框中输入的内容，会同步更新所有 multi-agent 的未发送消息缓存（等效于共用一个缓存）；关闭后各 multi-agent 独立保存。未发送的消息会持久化到本地，退出程序后下次打开仍会保留。")}</span></i></span>
        <label class="toggle"><input type="checkbox" id="adv_cross_draft" ${s.cross_agent_draft_flow !== false ? "checked" : ""}><span class="track"></span></label>
      </div>
      <div class="muted" style="margin:10px 0 2px;">${t("浏览器接管")}</div>
      <div class="switch-row">
        <span class="sw-label">🐞 ${t("浏览器工具debug模式")} <i class="info-icon">!<span class="tip">${t("开启后，LLM 每次调用浏览器工具都会在程序根目录的 browser_tools_DEBUG_logs 文件夹下，按小时记录一份日志（含整个 viewport 的原始 DOM、处理后的 DOM、LLM 输入、工具返回的 content），便于排查。")}</span></i></span>
        <label class="toggle"><input type="checkbox" id="adv_browser_debug" ${s.browser_takeover_debug ? "checked" : ""}><span class="track"></span></label>
      </div>
      <div class="switch-row">
        <span class="sw-label">🖼️ ${t("浏览器工具解析图片标签")} <i class="info-icon">!<span class="tip">${t("开启后，浏览器工具会把网页里的图片解析成 [图片] 标签（有 alt 时附带一行文本）返回给 LLM；关闭时（默认），[图片] 行以及因此变空的 [..] 结构标签父链会被整条删除，不输出任何图片信息。")}</span></i></span>
        <label class="toggle"><input type="checkbox" id="adv_browser_image_tags" ${s.browser_image_tags ? "checked" : ""}><span class="track"></span></label>
      </div>
      <div class="switch-row">
        <span class="sw-label">🧩 ${t("浏览器可交互元素整合")} <i class="info-icon">!<span class="tip">${esc(t("开启后（默认），返回给 LLM 的可交互元素会从 `<可点击元素 eN>内容</可点击元素 eN>` 压缩成 `<可点击元素 eN : 内容>`，取消闭合标签，可显著节省上下文长度。可滚动元素不参与整合。"))}</span></i></span>
        <label class="toggle"><input type="checkbox" id="adv_browser_interactive_merge" ${s.browser_interactive_merge !== false ? "checked" : ""}><span class="track"></span></label>
      </div>
      <div class="switch-row">
        <span class="sw-label">🔗 ${t("整合分隔符")} <i class="info-icon">!<span class="tip">${esc(t("连接元素编号与内容的符号，默认英文冒号 `:`。例如设为 `|` 时输出 `<可点击元素 eN | 内容>`。"))}</span></i></span>
        <span class="sw-inline"><input type="text" id="adv_browser_interactive_sep" maxlength="8" value="${esc(s.browser_interactive_merge_separator || ":")}" style="width:70px;"></span>
      </div>
      <div class="switch-row">
        <span class="sw-label">🔤 ${t("浏览器交互元素使用英文渲染")} <i class="info-icon">!<span class="tip">${esc(t("开启后，可交互元素标签改用英文渲染（如 `<Click elem 23>`、`<Scroll elem 1>`），工具调用的元素编号直接传数字（如 23）。只替换标签与术语，提示语正文仍为中文。"))}</span></i></span>
        <label class="toggle"><input type="checkbox" id="adv_browser_interactive_english" ${s.browser_interactive_english ? "checked" : ""}><span class="track"></span></label>
      </div>
      <div class="muted" style="margin:10px 0 2px;">${t("记忆检索相似度门槛")}（${t("取值范围 0~1")}）</div>
      <div class="switch-row">
        <span class="sw-label">🔎 ${t("主动搜索记忆门槛")} <i class="info-icon">!<span class="tip">${t("主 agent 主动调用搜索记忆工具时的语义相似度门槛，取值范围 0~1。分数低于该门槛的记忆不会被返回；数值越大越严格、返回的记忆越少。")}</span></i></span>
        <span class="sw-inline"><input type="number" id="adv_search_threshold" min="0" max="1" step="0.05" value="${s.search_memory_threshold ?? 0.5}"></span>
      </div>
      <div class="switch-row">
        <span class="sw-label">📎 ${t("自动吸附记忆门槛")} <i class="info-icon">!<span class="tip">${t("收到用户消息时自动检索并吸附相关记忆的语义相似度门槛，取值范围 0~1。分数低于该门槛的记忆不会被吸附；数值越大越严格、吸附的记忆越少。")}</span></i></span>
        <span class="sw-inline"><input type="number" id="adv_attach_threshold" min="0" max="1" step="0.05" value="${s.attach_memory_threshold ?? 0.7}"></span>
      </div>
      <div class="muted" style="margin:10px 0 2px;">${t("其他")}</div>
      <div class="modal-actions" style="justify-content:flex-start; flex-wrap:wrap;">
        <button class="btn small" id="advBrowserDelay">${t("浏览器交互延迟设置")}</button>
        <button class="btn small" id="advImportPrefs">${t("导入本机设置")}</button>
      </div>
      <div class="modal-actions">
        <button class="btn" id="advCancel">${t("返回")}</button>
        <button class="btn primary" id="advSave">${t("保存")}</button>
      </div>
    </div>`;
  document.body.appendChild(mask);
  mask.querySelector("#advCancel").onclick = () => { mask.remove(); openSettings(); };
  mask.querySelector("#advBrowserDelay").onclick = () => { mask.remove(); openBrowserDelaySettings(() => openAdvancedSettings()); };
  mask.querySelector("#advImportPrefs").onclick = async () => { mask.remove(); await importLocalPrefsFlow(); openAdvancedSettings(); };
  mask.querySelector("#advSave").onclick = async () => {
    try {
      const clamp01 = (v, def) => { const n = parseFloat(v); return Number.isFinite(n) ? Math.min(1, Math.max(0, n)) : def; };
      await saveSettings({
        ...s,
        reasoning_expanded: mask.querySelector("#adv_reasoning").checked,
        tool_call_expanded: mask.querySelector("#adv_tool_call").checked,
        tool_result_expanded: mask.querySelector("#adv_tool_result").checked,
        show_sub_agent_tools: mask.querySelector("#adv_sub_tools").checked,
        pdf_table_extraction: mask.querySelector("#adv_pdf_tables").checked,
        cross_agent_draft_flow: mask.querySelector("#adv_cross_draft").checked,
        browser_takeover_debug: mask.querySelector("#adv_browser_debug").checked,
        browser_image_tags: mask.querySelector("#adv_browser_image_tags").checked, // BID080
        browser_interactive_merge: mask.querySelector("#adv_browser_interactive_merge").checked, // BID084
        browser_interactive_merge_separator: (mask.querySelector("#adv_browser_interactive_sep").value || ":").trim() || ":", // BID084
        browser_interactive_english: mask.querySelector("#adv_browser_interactive_english").checked, // BID084
        search_memory_threshold: clamp01(mask.querySelector("#adv_search_threshold").value, 0.5),
        attach_memory_threshold: clamp01(mask.querySelector("#adv_attach_threshold").value, 0.7),
      });
      mask.remove();
      toast(t("设置已保存"));
    } catch (e) { toast(e.message, true); }
  };
}

async function openGlobalPostgresSettings(back) {
  let s;
  try { s = await getSettings(); } catch (e) { toast(e.message, true); return; }
  const gpPrefix = s.global_postgres_prefix || "";
  const gpSuffix = s.global_postgres_suffix || "";
  let suffixLocal = isLocalPrefix(gpPrefix);

  const mask = document.createElement("div");
  mask.className = "modal-mask";
  mask.innerHTML = `
    <div class="modal" style="width:580px;max-height:84vh;overflow:auto;" data-modal-key="globalPostgres">
      <h3>🌐 ${t("全局postgres连接设置")}</h3>
      <div class="muted" style="margin-bottom:10px;line-height:1.65;">
        <p style="margin:0 0 6px;">${t("用途：让所有 multi-agent 共用同一套数据库连接（主机、账号、密码、SSL）；checkpoint 数据库名由每个 multi-agent 的名称自动生成（保存时程序会自动建库），长期记忆统一使用全局 store 数据库。")}</p>
        <p style="margin:0 0 6px;">${t("什么是「完整连接串」（conn string）：数据库厂商提供的一整条连接地址，长这样：")}<code>postgresql://user:passwd@localhost:5432/mydb?sslmode=disable</code></p>
        <p style="margin:0 0 6px;">${t("我们自造的「连接前缀 / 连接后缀」是什么意思：把上面这条连接串从「数据库名」处切成两半——数据库名前面的一段（协议、账号密码、主机端口）叫连接前缀，数据库名后面的一段（? 开头的 SSL 等参数）叫连接后缀。")}</p>
        <p style="margin:0;">${t("所以这里只需填「前缀 + 后缀」即可，checkpoint 数据库名由每个 multi-agent 的名称自动生成；也可以直接粘贴整条连接串，程序会自动帮你切出前缀与后缀（其中的数据库名会被忽略）。")}</p>
      </div>
      <div class="field full">
        <label>${t("填写方式")}</label>
        <div class="radio-list" style="flex-direction:row;gap:22px;flex-wrap:wrap;">
          <label class="radio-item"><input type="radio" name="gp_conn_mode" value="split" checked> ${t("分步填写（连接前缀 / 连接后缀）")}</label>
          <label class="radio-item"><input type="radio" name="gp_conn_mode" value="full"> ${t("直接填写完整连接串")}</label>
        </div>
      </div>
      <div id="gpSplitBlock">
        <div class="field full"><label>${t("连接前缀")} prefix</label><input id="gp_prefix" value="${esc(gpPrefix)}" placeholder="postgresql://user:passwd@localhost:5432/"><span class="hint">${t("格式：postgresql://用户名:密码@主机:端口/（以斜杠 / 结尾）")}</span></div>
        <div class="field"><label>${t("连接后缀")} suffix</label><div id="gpSuffixWrap" style="display:flex;flex-direction:column;gap:5px;">${pgSuffixCtrlHTML(suffixLocal, gpSuffix, "gp_suffix")}</div></div>
      </div>
      <div id="gpFullBlock" style="display:none;">
        <div class="field full"><label>${t("完整连接串")}</label><textarea id="gp_conn_full" rows="3" placeholder="postgresql://user:passwd@localhost:5432/mydb?sslmode=disable"></textarea><span class="hint" id="gpFullHint"></span><span class="hint">${t("这里粘贴的完整串只用于提取「连接前缀 / 连接后缀」，其中的数据库名会被忽略（checkpoint 库名由每个 multi-agent 各自填写）。")}</span></div>
      </div>
      <div class="field full"><label>${t("预览（密码已脱敏）")}</label><div class="conn-example" id="gpExample"></div></div>
      <div class="modal-actions">
        <button class="btn" id="gpClear">${t("清除全局设置")}</button>
        <div class="spacer" style="flex:1;"></div>
        <button class="btn" id="gpCancel">${t("返回")}</button>
        <button class="btn primary" id="gpSave">${t("保存")}</button>
      </div>
    </div>`;
  document.body.appendChild(mask);

  const splitBlock = mask.querySelector("#gpSplitBlock");
  const fullBlock = mask.querySelector("#gpFullBlock");
  const prefixInput = mask.querySelector("#gp_prefix");
  const suffixWrap = mask.querySelector("#gpSuffixWrap");
  const fullInput = mask.querySelector("#gp_conn_full");
  const fullHint = mask.querySelector("#gpFullHint");
  const exampleEl = mask.querySelector("#gpExample");
  let gpMode = "split";

  const bindSuffix = () => {
    const el = mask.querySelector("#gp_suffix");
    if (el) el.addEventListener("input", refreshGpExample);
  };
  const setGpSuffix = (value) => {
    const el = mask.querySelector("#gp_suffix");
    if (!el) return;
    if (el.tagName === "SELECT") {
      if (!Array.from(el.options).some(o => o.value === value)) {
        const opt = document.createElement("option");
        opt.value = value;
        opt.textContent = value || t("（无）");
        el.appendChild(opt);
      }
      el.value = value;
    } else {
      el.value = value;
    }
  };
  const currentGp = () => {
    if (gpMode === "full") {
      const parsed = parseConnString(fullInput.value);
      if (parsed.ok) {
        let suffix = parsed.suffix;
        if (!suffix && isLocalPrefix(parsed.prefix)) suffix = "?sslmode=disable";
        return { ok: true, prefix: parsed.prefix, suffix };
      }
      return { ok: false, error: parsed.error };
    }
    const el = mask.querySelector("#gp_suffix");
    let suffix = el ? (el.value || "") : "";
    if (!suffix && isLocalPrefix(prefixInput.value)) suffix = "?sslmode=disable";
    return { ok: true, prefix: prefixInput.value, suffix };
  };
  const refreshGpExample = () => {
    const cur = currentGp();
    const p = cur.ok ? (cur.prefix || "") : "";
    const sfx = cur.ok ? (cur.suffix || "") : "";
    exampleEl.innerHTML = `<code>${esc(maskedPrefix(p))}${esc(p && !p.endsWith("/") ? "/" : "")}<span class="muted">${t("<数据库名>")}</span>${esc(sfx)}</code>`;
  };
  const updateGpFullHint = () => {
    const parsed = parseConnString(fullInput.value);
    if (parsed.empty) { fullHint.innerHTML = ""; return; }
    if (!parsed.ok) { fullHint.innerHTML = `<span class="err-hint">⚠ ${esc(parsed.error)}</span>`; return; }
    let suffix = parsed.suffix;
    if (!suffix && isLocalPrefix(parsed.prefix)) suffix = "?sslmode=disable";
    fullHint.innerHTML = `<span class="ok-hint">✓ ${t("已识别")}</span> ${t("连接前缀")} <code>${esc(maskedPrefix(parsed.prefix))}</code> · ${t("连接后缀")} <code>${esc(suffix || t("（无）"))}</code>`;
  };
  const setGpMode = (mode) => {
    gpMode = mode === "full" ? "full" : "split";
    splitBlock.style.display = gpMode === "split" ? "" : "none";
    fullBlock.style.display = gpMode === "full" ? "" : "none";
    if (gpMode === "full") {
      const el = mask.querySelector("#gp_suffix");
      const sfx = el ? (el.value || "") : "";
      if ((prefixInput.value || "").trim()) fullInput.value = composeConnString(prefixInput.value, "mydb", sfx);
      updateGpFullHint();
    } else {
      const parsed = parseConnString(fullInput.value);
      if (parsed.ok) {
        prefixInput.value = parsed.prefix;
        let suffix = parsed.suffix;
        if (!suffix && isLocalPrefix(parsed.prefix)) suffix = "?sslmode=disable";
        if (isLocalPrefix(parsed.prefix) !== suffixLocal) {
          suffixLocal = isLocalPrefix(parsed.prefix);
          suffixWrap.innerHTML = pgSuffixCtrlHTML(suffixLocal, suffix, "gp_suffix");
          bindSuffix();
        } else {
          setGpSuffix(suffix);
        }
      }
    }
    refreshGpExample();
  };

  mask.querySelectorAll('input[name="gp_conn_mode"]').forEach(r =>
    r.addEventListener("change", () => setGpMode(r.value)));
  prefixInput.addEventListener("input", refreshGpExample);
  const suffixEl0 = mask.querySelector("#gp_suffix");
  if (suffixEl0) suffixEl0.addEventListener("input", refreshGpExample);
  fullInput.addEventListener("input", () => { updateGpFullHint(); refreshGpExample(); });
  refreshGpExample();

  const close = () => mask.remove();
  mask.querySelector("#gpCancel").onclick = () => { mask.remove(); (back || openSettings)(); };
  mask.addEventListener("click", (e) => { if (e.target === mask) close(); });

  mask.querySelector("#gpClear").onclick = async () => {
    if ((await askConfirm(t("确定清除全局 postgres 连接设置吗？清除后各 multi-agent 将恢复为单独填写的界面。"))) !== "yes") return;
    try {
      await saveSettings({ ...s, global_postgres_prefix: "", global_postgres_suffix: "", global_postgres_configured: true });
      close();
      toast(t("已清除全局连接设置"));
    } catch (e) { toast(e.message, true); }
  };

  mask.querySelector("#gpSave").onclick = async () => {
    let prefix, suffix;
    if (gpMode === "full") {
      const parsed = parseConnString(fullInput.value);
      if (!parsed.ok) { toast(parsed.error || t("请粘贴完整连接串"), true); return; }
      prefix = parsed.prefix;
      suffix = parsed.suffix;
      if (!suffix && isLocalPrefix(prefix)) suffix = "?sslmode=disable";
    } else {
      const el = mask.querySelector("#gp_suffix");
      prefix = prefixInput.value;
      suffix = el ? (el.value || "") : "";
      if (!suffix && isLocalPrefix(prefix)) suffix = "?sslmode=disable";
    }
    if (!String(prefix || "").trim()) { toast(t("请填写连接前缀"), true); return; }
    try {
      await saveSettings({ ...s, global_postgres_prefix: prefix, global_postgres_suffix: suffix, global_postgres_configured: true });
      close();
      toast(t("全局连接设置已保存"));
    } catch (e) { toast(e.message, true); }
  };
}

async function openGlobalEmbeddingSettings(back) {
  let s;
  try { s = await getSettings(); } catch (e) { toast(e.message, true); return; }
  const g = s.global_embedding || {};
  const curModel = String(g.model_name || EMB_OPTIONS[0]);
  const curCache = String(g.cache_folder || "");
  const curDims = (g.dims != null && g.dims !== "") ? g.dims : 1024;
  const curHf = String(g.hf_endpoint != null ? g.hf_endpoint : "");
  const curOffline = !!g.local_files_only;
  const modelInOptions = EMB_OPTIONS.includes(curModel);
  const hfInOptions = HF_OPTIONS.some(o => o.v === curHf);

  const mask = document.createElement("div");
  mask.className = "modal-mask";
  mask.innerHTML = `
    <div class="modal" style="width:560px;max-height:84vh;overflow:auto;" data-modal-key="globalEmbedding">
      <h3>🧬 ${t("全局embedding模型设置")}</h3>
      <div class="muted" style="margin-bottom:10px;line-height:1.65;">
        <p style="margin:0 0 6px;">${t("用途：让所有 multi-agent 共用同一套 embedding（向量化）模型配置，multi-agent 配置页勾选「使用全局 embedding 模型设置」即可，无需每个都重复填写。")}</p>
        <p style="margin:0;">${t("embedding 模型用于把文字转成向量、做长期记忆的语义检索。换模型时必须同步修改「embedding 维度」，否则检索会出错；首次使用会自动下载模型。")}</p>
      </div>
      <div class="field full"><label>${t("embedding 模型")} <i class="info-icon">!<span class="tip">${t("无需提前下载，首次配置会自动下载（需连接 Hugging Face Hub，国内网络可能连不上）。若已离线缓存过，可在下方缓存目录直接使用。")}</span></i></label>
        <select id="gemb_model_sel" style="width:100%;">
          ${EMB_OPTIONS.map(m => `<option value="${esc(m)}" ${m === curModel ? "selected" : ""}>${esc(m)}</option>`).join("")}
          <option value="__custom__" ${!modelInOptions ? "selected" : ""}>${t("自定义…")}</option>
        </select>
        <input id="gemb_model_custom" placeholder="${t("自定义模型名")}" value="${modelInOptions ? "" : esc(curModel)}" style="${modelInOptions ? "display:none;" : ""} margin-top:6px;">
      </div>
      <div class="field"><label>${t("embedding 缓存目录")} ${info("本地模型缓存路径，留空用 Hugging Face 默认缓存。")}</label><input id="gemb_cache" value="${esc(curCache)}"></div>
      <div class="field"><label>${t("embedding 维度")} ${info("向量维度；bge-m3 为 1024，换模型需对应调整。")}</label><input id="gemb_dims" value="${esc(curDims)}" type="number"></div>
      <div class="field full"><label>${t("embedding 镜像")} ${info("下载 embedding 模型时使用的 HuggingFace 镜像源；国内推荐 hf-mirror。")}</label>
        <select id="gemb_hf_sel" style="width:100%;">
          ${HF_OPTIONS.map(o => `<option value="${esc(o.v)}" ${curHf === o.v ? "selected" : ""}>${esc(t(o.label))}</option>`).join("")}
          <option value="__custom__" ${!hfInOptions ? "selected" : ""}>${t("自定义…")}</option>
        </select>
        <input id="gemb_hf_custom" placeholder="${t("自定义镜像地址")}" value="${hfInOptions ? "" : esc(curHf)}" style="${hfInOptions ? "display:none;" : ""} margin-top:6px;">
      </div>
      <div class="field full">
        <label style="flex-direction:row;align-items:center;gap:8px;"><input type="checkbox" id="gemb_offline" ${curOffline ? "checked" : ""}> ${t("Embedding 模型离线模式")} ${info("离线模式 = 只从本地缓存加载 embedding 模型、不联网校验。没有缓存时请勿开启（会报错）；已有缓存时建议开启，跳过每次联网校验。")}</label>
        <span class="hint">${t("没有缓存时请勿开启；已有缓存时建议开启，跳过每次联网校验。")}</span>
      </div>
      <div class="modal-actions">
        <button class="btn" id="gembClear">${t("清除全局设置")}</button>
        <div class="spacer" style="flex:1;"></div>
        <button class="btn" id="gembCancel">${t("返回")}</button>
        <button class="btn primary" id="gembSave">${t("保存")}</button>
      </div>
    </div>`;
  document.body.appendChild(mask);

  const modelSel = mask.querySelector("#gemb_model_sel"), modelCustom = mask.querySelector("#gemb_model_custom");
  modelSel.onchange = () => { modelCustom.style.display = modelSel.value === "__custom__" ? "" : "none"; };
  const hfSel = mask.querySelector("#gemb_hf_sel"), hfCustom = mask.querySelector("#gemb_hf_custom");
  hfSel.onchange = () => { hfCustom.style.display = hfSel.value === "__custom__" ? "" : "none"; };

  const close = () => mask.remove();
  mask.querySelector("#gembCancel").onclick = () => { mask.remove(); (back || openSettings)(); };
  mask.addEventListener("click", (e) => { if (e.target === mask) close(); });

  const collect = () => {
    const model_name = modelSel.value === "__custom__" ? modelCustom.value.trim() : modelSel.value;
    const hf_endpoint = hfSel.value === "__custom__" ? hfCustom.value.trim() : hfSel.value;
    return {
      model_name,
      cache_folder: mask.querySelector("#gemb_cache").value.trim(),
      dims: +mask.querySelector("#gemb_dims").value || 1024,
      hf_endpoint,
      local_files_only: mask.querySelector("#gemb_offline").checked,
      device: (g.device != null ? g.device : "cpu"),
      encode_normalize: (g.encode_normalize != null ? g.encode_normalize : true),
    };
  };

  mask.querySelector("#gembClear").onclick = async () => {
    if ((await askConfirm(t("确定清除全局 embedding 模型设置吗？清除后各 multi-agent 将恢复为单独填写的界面。"))) !== "yes") return;
    try {
      await saveSettings({ ...s, global_embedding: {}, global_embedding_configured: true });
      close();
      toast(t("已清除全局 embedding 设置"));
    } catch (e) { toast(e.message, true); }
  };

  mask.querySelector("#gembSave").onclick = async () => {
    const emb = collect();
    if (!emb.model_name) { toast(t("请填写 embedding 模型名"), true); return; }
    try {
      await saveSettings({ ...s, global_embedding: emb, global_embedding_configured: true });
      close();
      toast(t("全局 embedding 设置已保存"));
    } catch (e) { toast(e.message, true); }
  };
}

async function openGlobalSummarySettings(back) {
  let s;
  try { s = await getSettings(); } catch (e) { toast(e.message, true); return; }
  const g = s.global_summary || {};
  const gap = tokToUnit(g.summarize_gap_tokenwise != null ? g.summarize_gap_tokenwise : 80000);
  const flush = tokToUnit(g.flush_history_tokenwise != null ? g.flush_history_tokenwise : 600000);
  const reserve = (g.reserve_message_round != null ? g.reserve_message_round : 0);
  const gs = s.global_sub_summary || {};
  const subFlush = tokToUnit(gs.flush_history_tokenwise != null ? gs.flush_history_tokenwise : 200000);
  const subReserve = (gs.reserve_message_round != null ? gs.reserve_message_round : 4);

  const unitRow = (id, v, u) => `
    <div class="unit-row">
      <input id="${id}" value="${esc(v)}" type="number">
      <select id="${id}_unit"><option value="万" ${u === "万" ? "selected" : ""}>${t("万")}</option><option value="千" ${u === "千" ? "selected" : ""}>${t("千")}</option></select>
    </div>`;

  const mask = document.createElement("div");
  mask.className = "modal-mask";
  mask.innerHTML = `
    <div class="modal" style="width:540px;max-height:84vh;overflow:auto;" data-modal-key="globalSummary">
      <h3>📉 ${t("全局自动（及主动）总结设置")}</h3>
      <div class="muted" style="margin-bottom:10px;line-height:1.65;">
        <p style="margin:0 0 6px;">${t("用途：让所有 multi-agent 共用同一套「自动阶段性总结 / 自动全量总结」阈值，multi-agent 配置页勾选「使用全局自动（及主动）总结设置」即可，无需每个都重复填写。")}</p>
        <p style="margin:0;">${t("主 agent 与子 agent 各自保留一套：主 agent 用下面「主 agent」三个阈值，子 agent 用「子 agent」两个阈值。注意：这些阈值会影响图编译；修改后请保存对应的 multi-agent 才会真正生效。")}</p>
      </div>
      <div class="muted" style="margin:12px 0 2px;font-weight:700;">${t("主 agent")}</div>
      <div class="field"><label>${t("自动阶段性总结阈值")} ${info("累计 token 达到该值时触发一次阶段性总结。")}</label>${unitRow("gsum_gap", gap.v, gap.u)}</div>
      <div class="field"><label>${t("自动全量总结阈值")} ${info("累计 token 达到该值时清空历史（只保留最近几轮）。")}</label>${unitRow("gsum_flush", flush.v, flush.u)}</div>
      <div class="field"><label>${t("自动以及主动全量总结后，需要保留的会话轮数")} ${info("清空历史时保留最近几轮对话。")}</label><input id="gsum_reserve" value="${esc(reserve)}" type="number"></div>
      <div class="muted" style="margin:12px 0 2px;font-weight:700;">${t("子 agent")}</div>
      <div class="field"><label>${t("自动全量总结阈值")} ${info("子 agent 累计 token 达到该值时清空历史（只保留最近几轮）。")}</label>${unitRow("gsum_sub_flush", subFlush.v, subFlush.u)}</div>
      <div class="field"><label>${t("自动以及主动全量总结后，需要保留的会话轮数")} ${info("子 agent 清空历史时保留最近几轮对话。")}</label><input id="gsum_sub_reserve" value="${esc(subReserve)}" type="number"></div>
      <div class="modal-actions">
        <button class="btn" id="gsumClear">${t("清除全局设置")}</button>
        <div class="spacer" style="flex:1;"></div>
        <button class="btn" id="gsumCancel">${t("返回")}</button>
        <button class="btn primary" id="gsumSave">${t("保存")}</button>
      </div>
    </div>`;
  document.body.appendChild(mask);

  const close = () => mask.remove();
  mask.querySelector("#gsumCancel").onclick = () => { mask.remove(); (back || openSettings)(); };
  mask.addEventListener("click", (e) => { if (e.target === mask) close(); });

  mask.querySelector("#gsumClear").onclick = async () => {
    if ((await askConfirm(t("确定清除全局总结设置吗？清除后各 multi-agent 将恢复为单独填写的界面。"))) !== "yes") return;
    try {
      await saveSettings({
        ...s,
        global_summary: {}, global_summary_configured: true,
        global_sub_summary: {}, global_sub_summary_configured: true,
      });
      close();
      toast(t("已清除全局总结设置"));
    } catch (e) { toast(e.message, true); }
  };

  mask.querySelector("#gsumSave").onclick = async () => {
    try {
      const summary = {
        summarize_gap_tokenwise: unitToTok(mask.querySelector("#gsum_gap").value, mask.querySelector("#gsum_gap_unit").value),
        flush_history_tokenwise: unitToTok(mask.querySelector("#gsum_flush").value, mask.querySelector("#gsum_flush_unit").value),
        reserve_message_round: +mask.querySelector("#gsum_reserve").value || 0,
      };
      const subSummary = {
        flush_history_tokenwise: unitToTok(mask.querySelector("#gsum_sub_flush").value, mask.querySelector("#gsum_sub_flush_unit").value),
        reserve_message_round: +mask.querySelector("#gsum_sub_reserve").value || 0,
      };
      await saveSettings({
        ...s,
        global_summary: summary, global_summary_configured: true,
        global_sub_summary: subSummary, global_sub_summary_configured: true,
      });
      close();
      toast(t("全局总结设置已保存"));
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
    <div class="modal" style="width:480px;" data-modal-key="color">
      <h3>🎨 ${t("字体颜色设置")}</h3>
      <div class="muted" style="margin-bottom:4px;">${t("调整各板块文字颜色，仅本机生效。")}</div>
      ${row(COLOR_FIELDS[0], `🧠 ${t("思考过程")}`)}
      ${row(COLOR_FIELDS[1], `📝 ${t("正文")}`)}
      ${row(COLOR_FIELDS[2], `✅ ${t("工具结果")}`)}
      <div class="modal-actions">
        <button class="btn" id="colorReset">${t("恢复默认")}</button>
        <div class="spacer" style="flex:1;"></div>
        <button class="btn" id="colorCancel">${t("返回")}</button>
        <button class="btn primary" id="colorSave">${t("保存")}</button>
      </div>
    </div>`;
  document.body.appendChild(mask);

  mask.querySelector("#colorCancel").onclick = () => { mask.remove(); openSettings(); };
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

async function openCardSettings() {
  const mask = document.createElement("div");
  mask.className = "modal-mask";
  const colorRow = (f) => `
    <div class="switch-row">
      <span class="sw-label">${t(f.label)}</span>
      <span class="sw-inline">
        <input type="color" id="${f.key}" value="${cardGet(f.key, f.def)}">
        <button class="btn small" type="button" data-reset="${f.key}" data-def="${f.def}">${t("恢复默认")}</button>
      </span>
    </div>`;
  const styleRow = (f) => `
    <div class="switch-row">
      <span class="sw-label">${t(f.label)}</span>
      <label class="toggle"><input type="checkbox" id="${f.key}" ${cardGet(f.key, f.def) === "solid" ? "checked" : ""}><span class="track"></span></label>
    </div>`;
  mask.innerHTML = `
    <div class="modal card-set-modal" style="width:560px;max-height:82vh;overflow:auto;" data-modal-key="card">
      <h3>🃏 ${t("Multi-Agent配置卡片 设置")}</h3>
      <div class="muted" style="margin-bottom:4px;">${t("调整主界面 multi-agent 卡片的按钮配色 / 样式、绶带与背景，仅本机生效。")}</div>
      <div class="card-set-section">
        <div class="card-set-title">${t("按钮颜色")}</div>
        <div class="card-set-body">${CARD_BTN_COLORS.map(colorRow).join("")}</div>
      </div>
      <div class="card-set-section">
        <div class="card-set-title">${t("按钮样式")}（${t("开启 = 颜色底白字，关闭 = 白底框线")}）</div>
        <div class="card-set-body">${CARD_BTN_STYLES.map(styleRow).join("")}</div>
      </div>
      <div class="card-set-section">
        <div class="card-set-title">${t("绶带")}</div>
        <div class="card-set-body">
          <div class="switch-row">
            <span class="sw-label">🎗 ${t("显示绶带")}</span>
            <label class="toggle"><input type="checkbox" id="card-ribbon-show" ${cardGet("card-ribbon-show", "1") !== "0" ? "checked" : ""}><span class="track"></span></label>
          </div>
          <div class="switch-row">
            <span class="sw-label">${t("颜色")}</span>
            <span class="sw-inline">
              <input type="color" id="card-ribbon-color" value="${cardGet("card-ribbon-color", CARD_THEME_BLUE)}">
              <button class="btn small" type="button" data-reset="card-ribbon-color" data-def="${CARD_THEME_BLUE}">${t("恢复默认")}</button>
            </span>
          </div>
          <div class="switch-row">
            <span class="sw-label">${t("条数")}</span>
            <input type="number" id="card-ribbon-count" min="1" max="5" value="${cardRibbonCount()}">
          </div>
        </div>
      </div>
      <div class="card-set-section">
        <div class="card-set-title">${t("卡片背景")}</div>
        <div class="card-set-body">
          <div class="switch-row">
            <span class="sw-label">${t("背景颜色")}</span>
            <span class="sw-inline">
              <input type="color" id="card-bg" value="${cardGet("card-bg", CARD_BG_DEF)}">
              <button class="btn small" type="button" data-reset="card-bg" data-def="${CARD_BG_DEF}">${t("恢复默认")}</button>
            </span>
          </div>
        </div>
      </div>
      <div class="modal-actions">
        <button class="btn" id="cardCancel">${t("返回")}</button>
        <button class="btn primary" id="cardSave">${t("保存")}</button>
      </div>
    </div>`;
  document.body.appendChild(mask);
  mask.querySelector("#cardCancel").onclick = () => { mask.remove(); openSettings(); };
  mask.querySelectorAll("[data-reset]").forEach(btn => {
    btn.onclick = () => {
      const el = mask.querySelector("#" + btn.dataset.reset);
      if (el) el.value = btn.dataset.def;
    };
  });
  mask.querySelector("#cardSave").onclick = () => {
    CARD_BTN_COLORS.forEach(f => cardSetOrClear(f.key, mask.querySelector("#" + f.key).value, f.def));
    CARD_BTN_STYLES.forEach(f => cardSetOrClear(f.key, mask.querySelector("#" + f.key).checked ? "solid" : "outline", f.def));
    cardSetOrClear("card-ribbon-color", mask.querySelector("#card-ribbon-color").value, CARD_THEME_BLUE);
    cardSetOrClear("card-bg", mask.querySelector("#card-bg").value, CARD_BG_DEF);
    const cnt = Math.min(5, Math.max(1, parseInt(mask.querySelector("#card-ribbon-count").value, 10) || 2));
    cardSetOrClear("card-ribbon-count", String(cnt), "2");
    if (mask.querySelector("#card-ribbon-show").checked) localStorage.removeItem("card-ribbon-show");
    else localStorage.setItem("card-ribbon-show", "0");
    applyCardSettings();
    mask.remove();
    toast(t("设置已保存"));
  };
}

async function openHtmlConfigSettings() {
  let cfg, defaults, s;
  try {
    const r = await api("/api/export-html-config");
    cfg = r.config; defaults = r.defaults;
  } catch (e) { toast(e.message, true); return; }
  try { s = await getSettings(); } catch (e) { s = {}; }
  const mask = document.createElement("div");
  mask.className = "modal-mask";

  const fid = (path) => "cfg-" + path.join("_");
  const rows = HTML_CFG_SCHEMA.map(sec => {
    const items = sec.items.map(it => {
      const id = fid(it.p);
      const val = cfgGet(cfg, it.p);
      const input = it.type === "color"
        ? `<input type="color" id="${id}" value="${esc(String(val))}">`
        : `<input type="text" id="${id}" value="${esc(String(val))}">`;
      return `<div class="switch-row"><span class="sw-label">${t(it.label)}</span>${input}</div>`;
    }).join("");
    return `<div class="muted" style="margin:10px 0 2px;">${t(sec.section)}</div>${items}`;
  }).join("");

  mask.innerHTML = `
    <div class="modal" style="width:520px;max-height:82vh;overflow:auto;" data-modal-key="htmlConfig">
      <h3>🖨 ${t("HTML 转换配置表")}</h3>
      <div class="muted" style="margin-bottom:4px;">${t("调整导出 HTML 的纸张、预览与配色。")}</div>
      ${rows}
      <div class="modal-actions">
        <button class="btn" id="cfgReset">${t("恢复默认")}</button>
        <div class="spacer" style="flex:1;"></div>
        <button class="btn" id="cfgCancel">${t("返回")}</button>
        <button class="btn primary" id="cfgSave">${t("保存")}</button>
      </div>
    </div>`;
  document.body.appendChild(mask);

  const collect = () => {
    const out = {};
    HTML_CFG_SCHEMA.forEach(sec => sec.items.forEach(it => {
      const el = mask.querySelector("#" + fid(it.p));
      if (el) cfgSet(out, it.p, el.value);
    }));
    return out;
  };

  mask.querySelector("#cfgCancel").onclick = () => { mask.remove(); openSettings(); };
  mask.querySelector("#cfgReset").onclick = () => {
    HTML_CFG_SCHEMA.forEach(sec => sec.items.forEach(it => {
      const el = mask.querySelector("#" + fid(it.p));
      if (el) el.value = cfgGet(defaults, it.p);
    }));
  };
  mask.querySelector("#cfgSave").onclick = async () => {
    try {
      await saveSettings({ ...s, export_html_config: collect() });
      mask.remove();
      toast(t("设置已保存"));
    } catch (e) { toast(e.message, true); }
  };
}

async function openBrowserDelaySettings(back) {
  let defaults = {}, s;
  try {
    defaults = (await api("/api/browser-timing")).defaults || {};
  } catch (e) { toast(e.message, true); return; }
  try { s = await getSettings(); } catch (e) { s = {}; }
  const cfg = s.browser_timing || {};
  const mask = document.createElement("div");
  mask.className = "modal-mask";

  const fid = (path) => "bt-" + path.join("_");
  const getVal = (p) => {
    const v = cfgGet(cfg, p);
    return (v === "" || v == null) ? cfgGet(defaults, p) : v;
  };
  const asPair = (v, fallback) => {
    if (Array.isArray(v)) return v;
    if (v !== "" && v != null) return [v, v];
    return Array.isArray(fallback) ? fallback : [0, 0];
  };

  const sections = BROWSER_DELAY_SCHEMA.map(sec => {
    const rows = sec.items.map(it => {
      const id = fid(it.p);
      const step = it.step || 0.01;
      let input;
      if (it.pair) {
        const arr = asPair(getVal(it.p), it.def);
        input = `<span class="sw-inline"><input type="number" id="${id}_min" min="0" step="${step}" value="${esc(String(arr[0]))}"><span class="muted">~</span><input type="number" id="${id}_max" min="0" step="${step}" value="${esc(String(arr[1]))}"></span>`;
      } else {
        input = `<input type="number" id="${id}" min="0" step="${step}" value="${esc(String(getVal(it.p)))}">`;
      }
      return `<div class="delay-item"><div class="switch-row"><span class="sw-label">${t(it.label)}</span>${input}</div><div class="muted delay-desc">${t(it.desc)}</div></div>`;
    }).join("");
    return `<div class="delay-section-title">${t(sec.section)}</div>${rows}`;
  }).join("");

  mask.innerHTML = `
    <div class="modal" style="width:640px;max-height:84vh;overflow:auto;" data-modal-key="browserDelay">
      <h3>⏱ ${t("浏览器交互延迟设置")}</h3>
      <div class="muted" style="margin-bottom:6px;">${t("调整 LLM 操作浏览器时各环节的等待时长，单位为秒。数值越小响应越快，但过小可能抓不到刚加载 / 刚跳转的页面；数值越大越稳妥但更慢。修改保存后，下一次浏览器工具调用即生效。")}</div>
      ${sections}
      <div class="modal-actions">
        <button class="btn" id="btReset">${t("恢复默认")}</button>
        <div class="spacer" style="flex:1;"></div>
        <button class="btn" id="btCancel">${t("返回")}</button>
        <button class="btn primary" id="btSave">${t("保存")}</button>
      </div>
    </div>`;
  document.body.appendChild(mask);

  const collect = () => {
    const out = {};
    BROWSER_DELAY_SCHEMA.forEach(sec => sec.items.forEach(it => {
      const id = fid(it.p);
      if (it.pair) {
        let mn = parseFloat(mask.querySelector("#" + id + "_min").value);
        let mx = parseFloat(mask.querySelector("#" + id + "_max").value);
        if (!Number.isFinite(mn)) mn = 0;
        if (!Number.isFinite(mx)) mx = 0;
        cfgSet(out, it.p, [mn, mx]);
      } else {
        let v = parseFloat(mask.querySelector("#" + id).value);
        if (!Number.isFinite(v)) v = 0;
        cfgSet(out, it.p, v);
      }
    }));
    return out;
  };

  mask.querySelector("#btCancel").onclick = () => { mask.remove(); (back || openSettings)(); };
  mask.querySelector("#btReset").onclick = () => {
    BROWSER_DELAY_SCHEMA.forEach(sec => sec.items.forEach(it => {
      const id = fid(it.p);
      if (it.pair) {
        const arr = asPair(cfgGet(defaults, it.p), it.def);
        mask.querySelector("#" + id + "_min").value = arr[0];
        mask.querySelector("#" + id + "_max").value = arr[1];
      } else {
        mask.querySelector("#" + id).value = cfgGet(defaults, it.p);
      }
    }));
  };
  mask.querySelector("#btSave").onclick = async () => {
    try {
      await saveSettings({ ...s, browser_timing: collect() });
      mask.remove();
      toast(t("设置已保存"));
    } catch (e) { toast(e.message, true); }
  };
}

async function openToolResultDisplaySettings() {
  let s;
  try { s = await getSettings(); } catch (e) { toast(e.message, true); return; }
  const mask = document.createElement("div");
  mask.className = "modal-mask";

  const section = (title, fullKey, linesKey, charsKey, defFull, defLines, defChars) => {
    const full = (s[fullKey] === undefined) ? defFull : !!s[fullKey];
    const lines = (s[linesKey] === undefined) ? defLines : s[linesKey];
    const chars = (s[charsKey] === undefined) ? defChars : s[charsKey];
    return `
      <div class="delay-section-title">${t(title)}</div>
      <div class="switch-row">
        <span class="sw-label">${t("展示完整工具调用结果")}</span>
        <label class="toggle"><input type="checkbox" id="${fullKey}" ${full ? "checked" : ""}><span class="track"></span></label>
      </div>
      <div id="${fullKey}_fields" style="${full ? "display:none;" : ""}">
        <div class="switch-row">
          <span class="sw-label">${t("最多展示多少行")} <i class="info-icon">!<span class="tip">${t("超过该行数即截断显示；填 0 表示不限。")}</span></i></span>
          <input type="number" min="0" step="1" id="${linesKey}" value="${esc(String(lines))}">
        </div>
        <div class="switch-row">
          <span class="sw-label">${t("最多展示多少字")} <i class="info-icon">!<span class="tip">${t("超过该字数即截断显示；填 0 表示不限。与行数上限同时填写时，触发任意一个就截断。")}</span></i></span>
          <input type="number" min="0" step="1" id="${charsKey}" value="${esc(String(chars))}">
        </div>
      </div>`;
  };

  mask.innerHTML = `
    <div class="modal" style="width:540px;" data-modal-key="toolResultDisplay">
      <h3>📄 ${t("工具调用结果显示设置")}</h3>
      <div class="muted" style="margin-bottom:6px;">${t("设置工具调用结果在「流式输出」和「查看历史消息」中的展示方式：可展示完整结果，或按「最多行数 / 最多字数」截断（0 表示不限；两个上限都填时，触发任意一个就截断）。工具结果若是单行 JSON，会先结构化再展示。")}</div>
      ${section("流式输出", "tool_result_stream_full", "tool_result_stream_max_lines", "tool_result_stream_max_chars", true, 0, 0)}
      ${section("查看历史消息", "tool_result_history_full", "tool_result_history_max_lines", "tool_result_history_max_chars", false, 50, 0)}
      <div class="modal-actions">
        <button class="btn" id="trCancel">${t("返回")}</button>
        <button class="btn primary" id="trSave">${t("保存")}</button>
      </div>
    </div>`;
  document.body.appendChild(mask);

  const bindToggle = (fullKey) => {
    const cb = mask.querySelector("#" + fullKey);
    const fields = mask.querySelector("#" + fullKey + "_fields");
    if (!cb || !fields) return;
    const upd = () => { fields.style.display = cb.checked ? "none" : ""; };
    cb.addEventListener("change", upd);
    upd();
  };
  bindToggle("tool_result_stream_full");
  bindToggle("tool_result_history_full");

  const readInt = (id) => {
    const n = parseInt(mask.querySelector("#" + id).value, 10);
    return Number.isFinite(n) && n > 0 ? n : 0;
  };

  mask.querySelector("#trCancel").onclick = () => { mask.remove(); openSettings(); };
  mask.querySelector("#trSave").onclick = async () => {
    try {
      await saveSettings({
        ...s,
        tool_result_stream_full: mask.querySelector("#tool_result_stream_full").checked,
        tool_result_stream_max_lines: readInt("tool_result_stream_max_lines"),
        tool_result_stream_max_chars: readInt("tool_result_stream_max_chars"),
        tool_result_history_full: mask.querySelector("#tool_result_history_full").checked,
        tool_result_history_max_lines: readInt("tool_result_history_max_lines"),
        tool_result_history_max_chars: readInt("tool_result_history_max_chars"),
      });
      mask.remove();
      toast(t("设置已保存"));
    } catch (e) { toast(e.message, true); }
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
  name: "例如 研究助理",
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

// tooltip 智能定位：优先右侧，空间不足翻左侧，两侧都不足则收窄宽度自动换行；
// 水平/垂直均钳制在视口内，永不越界。用 position:fixed 避免被滚动容器裁切或撑出滚动条。
const TIP_GAP = 16, TIP_MARGIN = 8, TIP_MIN_W = 140;
document.addEventListener("mouseover", (e) => {
  const icon = e.target.closest && e.target.closest(".info-icon");
  if (!icon) return;
  const tip = icon.querySelector(".tip");
  if (!tip) return;
  tip.style.visibility = "hidden";
  tip.style.display = "block";
  tip.style.maxWidth = "";
  tip.style.left = "0px";
  tip.style.top = "0px";
  const vw = document.documentElement.clientWidth;
  const vh = document.documentElement.clientHeight;
  const rect = icon.getBoundingClientRect();
  let tw = tip.offsetWidth;
  let th = tip.offsetHeight;
  const spaceRight = vw - (rect.right + TIP_GAP) - TIP_MARGIN;
  const spaceLeft = rect.left - TIP_GAP - TIP_MARGIN;
  let left;
  if (tw <= spaceRight) {
    left = rect.right + TIP_GAP;
  } else if (tw <= spaceLeft) {
    left = rect.left - TIP_GAP - tw;
  } else {
    const useRight = spaceRight >= spaceLeft;
    tip.style.maxWidth = Math.min(Math.max(useRight ? spaceRight : spaceLeft, TIP_MIN_W), vw - TIP_MARGIN * 2) + "px";
    tw = tip.offsetWidth;
    th = tip.offsetHeight;
    left = useRight ? rect.right + TIP_GAP : rect.left - TIP_GAP - tw;
  }
  left = Math.min(Math.max(left, TIP_MARGIN), vw - TIP_MARGIN - tw);
  let top = rect.top + rect.height / 2 - th / 2;
  top = Math.min(Math.max(top, TIP_MARGIN), vh - TIP_MARGIN - th);
  tip.style.left = Math.round(left) + "px";
  tip.style.top = Math.round(top) + "px";
  tip.style.display = "";
  tip.style.visibility = "";
  icon.classList.add("show");
});
document.addEventListener("mouseout", (e) => {
  const icon = e.target.closest && e.target.closest(".info-icon");
  if (icon) icon.classList.remove("show");
});

// 记忆附着按钮：点击在当前视窗内弹出本条用户消息附着的记忆（事件委托，覆盖历史/流式动态创建的按钮）
document.addEventListener("click", (e) => {
  const btn = e.target && e.target.closest && e.target.closest(".mem-attach-btn");
  if (!btn) return;
  const block = btn.closest(".user-msg-block");
  const panel = block && block.querySelector(".mem-attach-panel");
  if (!panel) return;
  btn.classList.add("on");
  showContentModal(`🔖 ${t("记忆附着")}`, panel.innerHTML, () => btn.classList.remove("on"));
});

// 浏览器工具结果的「查看 content」按钮：点击弹窗展示去掉引号、\n/\t 已还原为真实换行/tab 的 content。
document.addEventListener("click", (e) => {
  const btn = e.target && e.target.closest && e.target.closest(".tool-content-btn");
  if (!btn) return;
  let text = "";
  try { text = b64ToUtf8(btn.getAttribute("data-tool-content-b64") || ""); } catch (err) { text = ""; }
  showContentModal(`📄 ${t("工具结果 content")}`, `<pre class="tool-content-pre">${esc(text)}</pre>`);
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
      <div class="field" data-reasoning-fix-field style="display:none;">
        <label style="flex-direction:row;align-items:center;gap:8px;"><input type="checkbox" data-mf="preserve_reasoning_content" ${mc.preserve_reasoning_content ? "checked" : ""}> ${t("启用 DeepSeek reasoning_content 保留修复")}</label>
        <span class="hint">${t("DeepSeek 思考模式 + 工具调用时，官方 LangChain 会丢弃历史思考内容、可能偶发 400。勾选后在本项目内运行时回填该内容（不改动官方库）。")}</span>
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
  const baseUrlInput = block.querySelector('[data-mf="base_url"]');
  const reasoningFixField = block.querySelector("[data-reasoning-fix-field]");
  let mode = block.querySelector('input[type="radio"]:checked').value;

  const isDeepseek = () => {
    if (mode === "openai_compatible") {
      return ((baseUrlInput && baseUrlInput.value) || "").toLowerCase().includes("deepseek");
    }
    return (llmInput.value || "").trim().toLowerCase().startsWith("deepseek:");
  };
  const updateDeepseekFix = () => {
    if (reasoningFixField) reasoningFixField.style.display = isDeepseek() ? "" : "none";
  };

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
    updateDeepseekFix();
  };

  block.querySelectorAll('input[type="radio"]').forEach(r => r.addEventListener("change", () => setMode(r.value)));
  llmInput.addEventListener("input", () => { if (mode === "official") checkOfficial(); updateDeepseekFix(); });
  llmInput.addEventListener("blur", () => { if (mode === "official") checkOfficial(); updateDeepseekFix(); });
  if (baseUrlInput) baseUrlInput.addEventListener("input", updateDeepseekFix);
  block.querySelectorAll("[data-opt]").forEach(cb => cb.addEventListener("change", () => {
    const f = block.querySelector(`[data-optfield="${cb.dataset.opt}"]`);
    if (f) f.style.display = cb.checked ? "" : "none";
  }));

  updateDeepseekFix();
}

function collectModelCfg(root) {
  const block = root.querySelector("[data-model-block]");
  if (!block) return { provider_mode: "official", base_url: "" };
  const mode = block.querySelector('input[type="radio"]:checked').value;
  const out = {
    provider_mode: mode,
    base_url: mode === "openai_compatible" ? (block.querySelector('[data-mf="base_url"]').value || "").trim() : "",
  };
  const reasoningFix = block.querySelector('[data-mf="preserve_reasoning_content"]');
  out.preserve_reasoning_content = !!(reasoningFix && reasoningFix.checked);
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
  const isLocal = isLocalPrefix(pg.prefix);
  const gap = tokToUnit(sum.summarize_gap_tokenwise);
  const flush = tokToUnit(sum.flush_history_tokenwise);

  const suffixCtrl = pgSuffixCtrlHTML(isLocal, pg.suffix);

  // 全局连接设置（只存前缀+后缀）：全局非空时，把「连接前缀/后缀」折叠成
  // 一个「使用全局设置」开关；是否默认勾选 = 本 agent 的连接是否与全局一致。
  const gpPrefix = (settingsCache && settingsCache.global_postgres_prefix) || "";
  const gpSuffix = (settingsCache && settingsCache.global_postgres_suffix) || "";
  const globalSet = !!String(gpPrefix).trim();
  const ns = (pg.store_namespace || []).join(",");
  const isNewCfg = !cfg.agent_id;
  // 只有「编辑已有 agent」才可能出现身份未统一（迁移失败）；新建 / 默认模板不算。
  const identityMismatch = !canEditSubs && (
    String(cfg.agent_id || "") !== String(cfg.name || "") ||
    String(pg.checkpoint_database || "") !== String(cfg.name || "")
  );
  const useGlobalInit = globalSet && (
    isNewCfg || (String(pg.prefix || "") === gpPrefix && String(pg.suffix || "") === gpSuffix)
  );

  // 全局 embedding 设置（与 postgres 同构：显示捷径；与全局一致则默认勾选）。
  const gEmb = (settingsCache && settingsCache.global_embedding) || {};
  const globalEmbSet = !!(settingsCache && settingsCache.global_embedding_configured) && Object.keys(gEmb).length > 0;
  const embEqual = globalEmbSet && (
    String(emb.model_name || "") === String(gEmb.model_name || "") &&
    String(emb.cache_folder || "") === String(gEmb.cache_folder || "") &&
    String(emb.hf_endpoint || "") === String(gEmb.hf_endpoint || "") &&
    (!!emb.local_files_only) === (!!gEmb.local_files_only) &&
    Number(emb.dims || 0) === Number(gEmb.dims || 0)
  );
  const useGlobalEmbInit = globalEmbSet && (isNewCfg || embEqual);

  // 全局自动（及主动）总结设置（与 postgres / embedding 同构：显示捷径；与全局一致则默认勾选）。
  const gSum = (settingsCache && settingsCache.global_summary) || {};
  const globalSumSet = !!(settingsCache && settingsCache.global_summary_configured) && Object.keys(gSum).length > 0;
  const sumEqual = globalSumSet && (
    Number(sum.summarize_gap_tokenwise || 0) === Number(gSum.summarize_gap_tokenwise || 0) &&
    Number(sum.flush_history_tokenwise || 0) === Number(gSum.flush_history_tokenwise || 0) &&
    Number(sum.reserve_message_round || 0) === Number(gSum.reserve_message_round || 0)
  );
  const useGlobalSumInit = globalSumSet && (isNewCfg || sumEqual);
  const gGap = tokToUnit(gSum.summarize_gap_tokenwise != null ? gSum.summarize_gap_tokenwise : 0);
  const gFlush = tokToUnit(gSum.flush_history_tokenwise != null ? gSum.flush_history_tokenwise : 0);

  const pgMode = pgConnMode === "full" ? "full" : "split";
  // 数据库名不再手填：完整连接串里只借用「前缀 / 后缀」，库名由名称自动生成。
  const fullConnInit = (pg.prefix || "").trim()
    ? composeConnString(pg.prefix, "mydb", pg.suffix)
    : "";

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
  const reactPrompt = main.react_prompt !== false;
  const browserTakeover = !!main.browser_takeover;
  const browserTabMax = Math.min(50, Math.max(3, parseInt(main.browser_tab_max_number, 10) || 4));
  const memoryTools = main.memory_tools_enabled !== false;

  form.innerHTML = `
    <div class="form-card"><h4>${t("基本信息")}</h4><div class="form-grid">
      <div class="basic-grid">
      <div class="field"><label>${t("名称")} name ${info("唯一标识：同时用作 agent_id（配置文件名 configs/&lt;名称&gt;.json）与 checkpoint 数据库名。创建后不可更改；不能与其他 multi-agent 重名。")}</label><input id="f_name" value="${esc(cfg.name)}" ${canEditSubs ? "" : "readonly"}${ph("name")}><span class="lock-hint">${canEditSubs ? "&nbsp;" : t("创建后不可更改")}</span></div>
      <div class="field"><label>${t("长期记忆分组")} ${info("长期记忆（agent 用 write_memory 存下的知识、偏好、结论等）都放在同一个全局记忆库里，靠「分组」区分归属。<br/><br/>① 分组相同：这些 multi-agent 共用同一份长期记忆，能读到彼此写下的内容；<br/>② 分组不同：彼此完全隔离，互相检索不到对方的记忆；<br/>③ 写一层即可（例如 default）；需要更细的划分时，可写多层并用逗号分隔（例如 teamA,projectX）；<br/>④ 不能为空。修改本项后，本 multi-agent 会改用新分组读写记忆：旧分组里的记忆不会被删除，但本 multi-agent 不再读取它。<br/><br/>分组按字符串完全匹配：大小写不同、多一个空格都会被当作不同分组。想让几个 multi-agent 共享经验，就把这里写成完全相同的一串。")}</label><input id="f_ns" value="${esc(ns)}"><span class="lock-hint">${t("相同分组共用长期记忆，不同分组互相隔离")}</span></div>
      </div>
      ${identityMismatch ? `<div class="field full"><div class="err-hint">⚠ ${t("该 multi-agent 的内部标识（agent_id / checkpoint 数据库）仍与名称不一致：程序下次启动会自动改名；如果一直不变，请查看 logs/server.log 或 configs/identity_migration.json。")}</div></div>` : ""}
      ${globalSet ? `
      <div class="field full">
        <div class="global-toggle-row">
          <label style="display:flex;flex-direction:row;align-items:center;gap:8px;margin:0;"><input type="checkbox" id="f_pg_use_global" ${useGlobalInit ? "checked" : ""}> ${t("使用全局 postgres 连接设置")}</label>
          ${info("勾选后，本 multi-agent 直接使用主界面「系统设置 → 全局postgres连接设置」里的连接前缀与连接后缀（主机 / 账号 / 密码 / SSL）；checkpoint 数据库名仍由名称自动生成。取消勾选可为本 multi-agent 单独设置连接。")}
          <span class="exp-tri" id="pgExpTri" title="${t("展开 / 收起说明")}"></span>
        </div>
      </div>
      <div class="field full" id="pgGlobalBlock" style="display:none;">
        <div class="conn-example">
          <div>${t("当前使用的全局连接（密码已脱敏）：")}</div>
          <div style="margin-top:4px;"><code>${esc(maskedPrefix(gpPrefix))}${esc(gpPrefix.endsWith("/") ? "" : "/")}<span class="muted">${t("<数据库名>")}</span>${esc(gpSuffix || "")}</code></div>
          <div class="muted" style="margin-top:6px;font-size:11px;">${t("如需修改全局连接，请到主界面齿轮设置 →「全局postgres连接设置」。")}</div>
        </div>
      </div>` : ""}
      <div id="pgManualBlock" style="${globalSet && useGlobalInit ? "display:none;" : "display:contents;"}">
      <div class="field full">
        <label>${t("数据库连接填写方式")} ${info("两种方式二选一：不熟悉「连接前缀/连接后缀」时，可直接粘贴一条完整的连接串，程序会自动拆分。")}</label>
        <div class="radio-list" style="flex-direction:row;gap:22px;flex-wrap:wrap;">
          <label class="radio-item"><input type="radio" name="pg_conn_mode" value="split" ${pgMode === "split" ? "checked" : ""}> ${t("分步填写（连接前缀 / 连接后缀）")}</label>
          <label class="radio-item"><input type="radio" name="pg_conn_mode" value="full" ${pgMode === "full" ? "checked" : ""}> ${t("直接填写完整连接串")}</label>
        </div>
      </div>
      <div id="pgSplitBlock" style="${pgMode === "split" ? "display:contents;" : "display:none;"}">
        <div class="field full"><label>${t("连接前缀")} prefix <i class="info-icon">!<span class="tip">${t("连接前缀 = 连接串里「数据库名」前面的全部内容：协议://用户名:密码@主机:端口/。<br/><br/>例如 postgresql://user:passwd@localhost:5432/<br/><br/>它回答「连到哪台数据库服务器、用哪个账号」。注意结尾的斜杠 / 不能少；数据库名由名称自动生成，不需要在这里填。")}</span></i></label><input id="f_prefix" value="${esc(pg.prefix)}"${ph("prefix")}><span class="hint">${t("格式：postgresql://用户名:密码@主机:端口/（以斜杠 / 结尾）")}</span></div>
        <div class="field"><label>${t("连接后缀")} suffix <i class="info-icon">!<span class="tip">${t("连接后缀 = 连接串里「数据库名」后面的部分，通常是 ? 开头的参数，最主要的是 SSL 模式。<br/><br/>例如 ?sslmode=disable、?sslmode=require。<br/><br/>本地数据库用 sslmode=disable 即可；云端/企业数据库按服务商要求选择。")}</span></i></label><div id="suffixCtrlWrap" style="display:flex;flex-direction:column;gap:5px;">${suffixCtrl}</div></div>
      </div>
      <div class="field full" id="pgFullBlock" style="${pgMode === "full" ? "" : "display:none;"}">
        <label>${t("完整连接串")} <i class="info-icon">!<span class="tip">${t("直接粘贴数据库服务商提供的整条连接串（PostgreSQL 官方 URI 格式）。<br/><br/>例如 postgresql://user:passwd@localhost:5432/mydb?sslmode=disable<br/><br/>程序只从中拆出连接前缀、连接后缀；其中的数据库名会被忽略，实际库名由名称自动生成。")}</span></i></label>
        <textarea id="f_conn_full" rows="3" placeholder="postgresql://user:passwd@localhost:5432/mydb?sslmode=disable">${esc(fullConnInit)}</textarea>
        <span class="hint" id="connFullHint"></span>
      </div>
      <div class="field full"><label>${t("完整连接串示例")} ${info("按当前填写内容实时拼出的完整连接串（密码已脱敏）。数据库名一栏只是示例，实际由名称自动生成。")}</label><div class="conn-example" id="connExample"></div></div>
      </div>
    </div></div>

    <div class="form-card"><h4>${t("主 agent")}</h4><div class="form-grid">
      <div class="field full takeover-switch-field">
        <label style="flex-direction:row;align-items:center;gap:8px;"><input type="checkbox" id="f_browser_takeover" ${browserTakeover ? "checked" : ""}> 🌐 ${t("开启浏览器接管功能")} ${info("开启后，会向主 agent 注入一组浏览器接管工具（占用上下文）。只有需要让 agent 操作浏览器时才开启。")}</label>
      </div>
      <div class="field full" id="browserTabMaxField" style="${browserTakeover ? "" : "display:none;"}">
        <label>${t("允许 Agent保留的最大标签页数")} ${info("Agent 主动关闭标签页要花掉上下文，实际上很少这么做；于是「目前打开的标签页有哪些」会越列越长、白烧 token。这里设一个上限，超出的标签页按 LRU（最久未使用的先关）自动关闭，并在返回给模型的内容里注明关了哪些。<br/><br/>计数按浏览器进程算：同一个浏览器里的所有标签页都算在内（包括你自己手动打开的），当前聚焦的标签页永不关闭。两个 multi-agent 共用同一个浏览器时，按最近一次配置的 multi-agent 的值为准。<br/><br/>默认 4，最小 3（聚焦页不能关，1~2 会逼出「新页和聚焦页只能留一个」的死结），最大 50。")}</label>
        <input id="f_browser_tab_max" type="number" min="3" max="50" step="1" value="${esc(String(browserTabMax))}">
      </div>
      <div class="field full">
        <label style="flex-direction:row;align-items:center;gap:8px;"><input type="checkbox" id="f_memory_tools" ${memoryTools ? "checked" : ""}> 🧠 ${t("开启记忆读取/写入功能")} ${info("开启后，会向主 agent 注入记忆读写工具（write_memory / read_memory），让 agent 能长期存取与检索信息；关闭后主 agent 不再拥有记忆读写工具。此设置影响图编译；「记忆吸附」是另一项功能，不受本开关影响。")}</label>
      </div>
      ${modelBlockHTML(main.llm_provider_name, main.model, "main_mode")}
      <div class="field"><label>API Key ${info("该模型供应商的 API 密钥（明文存本地配置）。")}</label><input id="f_apikey" value="${esc(main.api_key)}" type="password"${ph("main_api_key")}></div>
      <div class="field"><label>${t("主Agent工作目录")} ${info("主Agent读写本地文件的根目录")}</label><input id="f_rootdir" value="${esc(ft.root_dir)}"${ph("root_dir")}></div>
      ${globalEmbSet ? `
      <div class="field full">
        <div class="global-toggle-row">
          <label style="display:flex;flex-direction:row;align-items:center;gap:8px;margin:0;"><input type="checkbox" id="f_emb_use_global" ${useGlobalEmbInit ? "checked" : ""}> ${t("使用全局 embedding 模型设置")}</label>
          ${info("勾选后，本 multi-agent 直接使用主界面「系统设置 → 全局embedding模型设置」里的 embedding 模型、维度、镜像、缓存目录与离线模式；取消勾选可为本 multi-agent 单独设置。")}
          <span class="exp-tri" id="embExpTri" title="${t("展开 / 收起说明")}"></span>
        </div>
      </div>
      <div class="field full" id="embGlobalBlock" style="display:none;">
        <div class="conn-example">
          <div>${t("当前使用的全局 embedding 配置：")}</div>
          <div style="margin-top:4px;"><code>${esc(gEmb.model_name || "")}</code> · ${t("embedding 维度")} <code>${esc(String(gEmb.dims || ""))}</code> · ${t("embedding 镜像")} <code>${esc(gEmb.hf_endpoint || t("官方 huggingface.co"))}</code> · ${t("Embedding 模型离线模式")} <code>${gEmb.local_files_only ? t("是") : t("否")}</code></div>
          <div class="muted" style="margin-top:6px;font-size:11px;">${t("如需修改，请到主界面齿轮设置 →「全局embedding模型设置」。")}</div>
        </div>
      </div>` : ""}
      <div id="embManualBlock" style="${globalEmbSet && useGlobalEmbInit ? "display:none;" : "display:contents;"}">
      <div class="field"><label>${t("embedding 模型")} <i class="info-icon">!<span class="tip">${t("无需提前下载，首次配置会自动下载（需连接 Hugging Face Hub，国内网络可能连不上）。若已离线缓存过，可在下方缓存目录直接使用。")}</span></i></label>${embSelect}</div>
      <div class="field"><label>${t("embedding 缓存目录")} ${info("本地模型缓存路径，留空用 Hugging Face 默认缓存。")}</label><input id="f_emb_cache" value="${esc(emb.cache_folder)}"${ph("emb_cache")}></div>
      <div class="field"><label>${t("embedding 维度")} ${info("向量维度；bge-m3 为 1024，换模型需对应调整。")}</label><input id="f_emb_dims" value="${esc(emb.dims)}" type="number"></div>
      <div class="field"><label>${t("embedding 镜像")} ${info("下载 embedding 模型时使用的 HuggingFace 镜像源；国内推荐 hf-mirror。")}</label>${hfSelect}</div>
      <div class="field full">
        <label style="flex-direction:row;align-items:center;gap:8px;"><input type="checkbox" id="f_emb_offline" ${emb.local_files_only ? "checked" : ""}> ${t("Embedding 模型离线模式")} ${info("离线模式 = 只从本地缓存加载 embedding 模型、不联网校验。没有缓存时请勿开启（会报错）；已有缓存时建议开启，跳过每次联网校验。")}</label>
        <span class="hint">${t("没有缓存时请勿开启；已有缓存时建议开启，跳过每次联网校验。")}</span>
      </div>
      </div>
      ${globalSumSet ? `
      <div class="field full">
        <div class="global-toggle-row">
          <label style="display:flex;flex-direction:row;align-items:center;gap:8px;margin:0;"><input type="checkbox" id="f_sum_use_global" ${useGlobalSumInit ? "checked" : ""}> ${t("使用全局自动（及主动）总结设置")}</label>
          ${info("勾选后，本 multi-agent 直接使用主界面「系统设置 → 全局自动（及主动）总结设置」里的自动阶段性总结阈值、自动全量总结阈值与保留轮数；取消勾选可为本 multi-agent 单独设置。")}
          <span class="exp-tri" id="sumExpTri" title="${t("展开 / 收起说明")}"></span>
        </div>
      </div>
      <div class="field full" id="sumGlobalBlock" style="display:none;">
        <div class="conn-example">
          <div>${t("当前使用的全局总结配置：")}</div>
          <div style="margin-top:4px;">${t("自动阶段性总结阈值")} <code>${esc(String(gGap.v))}${esc(t(gGap.u))}</code> · ${t("自动全量总结阈值")} <code>${esc(String(gFlush.v))}${esc(t(gFlush.u))}</code> · ${t("自动以及主动全量总结后，需要保留的会话轮数")} <code>${esc(String(gSum.reserve_message_round != null ? gSum.reserve_message_round : 0))}</code></div>
          <div class="muted" style="margin-top:6px;font-size:11px;">${t("如需修改，请到主界面齿轮设置 →「全局自动（及主动）总结设置」。")}</div>
        </div>
      </div>` : ""}
      <div id="sumManualBlock" style="${globalSumSet && useGlobalSumInit ? "display:none;" : "display:contents;"}">
      ${unitField("f_sum_gap", gap.v, gap.u, t("自动阶段性总结阈值"), "累计 token 达到该值时触发一次阶段性总结。")}
      ${unitField("f_sum_flush", flush.v, flush.u, t("自动全量总结阈值"), "累计 token 达到该值时清空历史（只保留最近几轮）。")}
      <div class="field"><label>${t("自动以及主动全量总结后，需要保留的会话轮数")} ${info("清空历史时保留最近几轮对话。")}</label><input id="f_sum_reserve" value="${esc(sum.reserve_message_round)}" type="number"></div>
      </div>
      <div class="field full"><label>System Prompt ${info("主 agent 的系统提示词，定义其角色与行为。")}</label><textarea id="f_prompt" rows="8"${ph("main_system_prompt")}>${esc(main.system_prompt)}</textarea></div>
      <div class="field full">
        <label style="flex-direction:row;align-items:center;gap:8px;"><input type="checkbox" id="f_react_prompt" ${reactPrompt ? "checked" : ""}> ${t("开启 ReAct Prompt")} ${info("用于长程多工具调用的严肃任务处理场景；日常聊天可关闭以节省 token 并加快响应。")}</label>
      </div>
      <div class="field full" style="border-top:1px solid var(--border);padding-top:14px;">
        <label style="flex-direction:row;align-items:center;gap:8px;"><input type="checkbox" id="f_html_report" ${htmlReport ? "checked" : ""}> ${t("启用 HTML 报告")} ${info("主 agent 输出完后，询问是否将结果生成 HTML 报告。")}</label>
      </div>
      <div class="field full" id="htmlPromptField" style="${htmlReport ? "" : "display:none;"}"><label>${t("HTML 报告生成 prompt")} ${info("确认生成后注入给主 agent 的提示词（可恢复默认）。")} <button class="btn small" id="resetHtmlBtn" type="button">${t("恢复默认")}</button></label><textarea id="f_html_prompt" rows="3">${esc(htmlPrompt)}</textarea></div>
    </div></div>

    <div class="form-card"><h4>${t("子 agent")}（${canEditSubs ? t("可增删") : t("创建后不可增删改名")}）</h4>
      <div id="subs"></div>
      ${canEditSubs ? `<button class="btn small" id="addSubBtn" type="button">+ ${t("添加子 agent")}</button>` : ""}
    </div>`;

  // ── 数据库连接：分步填写 ⇄ 完整连接串 ──────────────────────
  const splitBlock = $("#pgSplitBlock");
  const fullBlock = $("#pgFullBlock");
  const fullInput = $("#f_conn_full");
  const fullHint = $("#connFullHint");
  const suffixWrap = $("#suffixCtrlWrap");
  const prefixInput = $("#f_prefix");
  const nameInput = $("#f_name");

  const bindSuffixEvents = () => {
    const el = $("#f_suffix");
    if (el) el.addEventListener("input", refreshExample);
  };
  const setSuffixValue = (value) => {
    const el = $("#f_suffix");
    if (!el) return;
    if (el.tagName === "SELECT") {
      if (!Array.from(el.options).some(o => o.value === value)) {
        const opt = document.createElement("option");
        opt.value = value;
        opt.textContent = value || t("（无）");
        el.appendChild(opt);
      }
      el.value = value;
    } else {
      el.value = value;
    }
  };

  const currentDbName = () => ((nameInput ? nameInput.value : "") || "").trim();

  const refreshExample = () => {
    let prefix, suffix;
    if (currentPgMode() === "full") {
      const parsed = parseConnString(fullInput.value);
      prefix = parsed.ok ? parsed.prefix : "";
      suffix = parsed.ok ? parsed.suffix : "";
    } else {
      prefix = prefixInput.value;
      const s = $("#f_suffix");
      suffix = s ? s.value || "" : "";
    }
    const db = currentDbName();
    const server = connServerLabel(prefix);
    $("#connExample").innerHTML =
      esc(maskedPrefix(prefix) + (db || t("<数据库名>")) + (suffix || "")) +
      `<div class="muted" style="margin-top:6px;font-size:11px;">🖥 ${t("服务器：")}<code>${esc(server || t("（未填写）"))}</code> · ${t("数据库：")}<code>${esc(db || t("（填写名称后自动生成）"))}</code></div>` +
      `<div class="muted" style="margin-top:4px;font-size:11px;">${t("服务器只由连接前缀里的 host:port 决定；同一 host:port 才是同一台 PostgreSQL。pgAdmin 里的「服务器」名字只是连接配置的显示名，不改变数据位置。")}</div>`;
  };

  const updateFullHint = () => {
    if (!fullHint) return;
    const parsed = parseConnString(fullInput.value);
    if (parsed.empty) { fullHint.innerHTML = ""; return; }
    if (!parsed.ok) { fullHint.innerHTML = `<span class="err-hint">⚠ ${esc(parsed.error)}</span>`; return; }
    let suffix = parsed.suffix;
    if (!suffix && isLocalPrefix(parsed.prefix)) suffix = "?sslmode=disable";
    fullHint.innerHTML = `<span class="ok-hint">✓ ${t("已识别")}</span> ` +
      `${t("连接前缀")} <code>${esc(maskedPrefix(parsed.prefix))}</code> · ` +
      `${t("连接后缀")} <code>${esc(suffix || t("（无）"))}</code>` +
      `<div class="muted" style="margin-top:4px;">${t("连接串里的数据库名")}<code>${esc(parsed.database)}</code>${t("会被忽略：实际库名由名称自动生成。")}</div>`;
  };

  const setPgMode = (mode) => {
    pgConnMode = mode === "full" ? "full" : "split";
    splitBlock.style.display = pgConnMode === "split" ? "contents" : "none";
    fullBlock.style.display = pgConnMode === "full" ? "" : "none";
    if (pgConnMode === "full") {
      if ((prefixInput.value || "").trim()) {
        const s = $("#f_suffix");
        fullInput.value = composeConnString(prefixInput.value, currentDbName() || "mydb", s ? s.value : "");
      }
      updateFullHint();
    } else {
      const parsed = parseConnString(fullInput.value);
      if (parsed.ok) {
        prefixInput.value = parsed.prefix;
        let suffix = parsed.suffix;
        if (!suffix && isLocalPrefix(parsed.prefix)) suffix = "?sslmode=disable";
        if (isLocalPrefix(parsed.prefix) !== isLocal) {
          suffixWrap.innerHTML = pgSuffixCtrlHTML(isLocalPrefix(parsed.prefix), suffix);
          bindSuffixEvents();
        } else {
          setSuffixValue(suffix);
        }
      }
    }
    refreshExample();
  };

  document.querySelectorAll('input[name="pg_conn_mode"]').forEach(r =>
    r.addEventListener("change", () => setPgMode(r.value)));
  ["f_prefix", "f_suffix"].forEach(id => { const el = $("#" + id); if (el) el.addEventListener("input", refreshExample); });
  if (nameInput) nameInput.addEventListener("input", refreshExample);
  fullInput.addEventListener("input", () => { updateFullHint(); refreshExample(); });
  updateFullHint();
  refreshExample();

  // 全局连接开关：勾选时隐藏手动填写区；全局摘要默认折叠，点小三角才展开。
  const pgUseGlobal = $("#f_pg_use_global");
  const pgManualBlock = $("#pgManualBlock");
  const pgGlobalBlock = $("#pgGlobalBlock");
  const pgExpTri = $("#pgExpTri");
  let pgExpanded = false;
  const applyPgUseGlobal = () => {
    if (!pgUseGlobal) return;
    const on = pgUseGlobal.checked;
    if (!on) pgExpanded = false;
    if (pgManualBlock) pgManualBlock.style.display = on ? "none" : "contents";
    if (pgGlobalBlock) pgGlobalBlock.style.display = (on && pgExpanded) ? "" : "none";
    if (pgExpTri) pgExpTri.classList.toggle("open", !!pgExpanded);
  };
  if (pgExpTri) pgExpTri.onclick = () => { pgExpanded = !pgExpanded; applyPgUseGlobal(); };
  if (pgUseGlobal) {
    pgUseGlobal.addEventListener("change", applyPgUseGlobal);
    applyPgUseGlobal();
  }

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

  // 全局 embedding 开关：勾选时隐藏手动填写区；全局摘要默认折叠，点小三角才展开。
  const embUseGlobal = $("#f_emb_use_global");
  const embManualBlock = $("#embManualBlock");
  const embGlobalBlock = $("#embGlobalBlock");
  const embExpTri = $("#embExpTri");
  let embExpanded = false;
  const applyEmbUseGlobal = () => {
    if (!embUseGlobal) return;
    const on = embUseGlobal.checked;
    if (!on) embExpanded = false;
    if (embManualBlock) embManualBlock.style.display = on ? "none" : "contents";
    if (embGlobalBlock) embGlobalBlock.style.display = (on && embExpanded) ? "" : "none";
    if (embExpTri) embExpTri.classList.toggle("open", !!embExpanded);
  };
  if (embExpTri) embExpTri.onclick = () => { embExpanded = !embExpanded; applyEmbUseGlobal(); };
  if (embUseGlobal) {
    embUseGlobal.addEventListener("change", applyEmbUseGlobal);
    applyEmbUseGlobal();
  }

  // 全局总结开关：勾选时隐藏手动填写区；全局摘要默认折叠，点小三角才展开。
  const sumUseGlobal = $("#f_sum_use_global");
  const sumManualBlock = $("#sumManualBlock");
  const sumGlobalBlock = $("#sumGlobalBlock");
  const sumExpTri = $("#sumExpTri");
  let sumExpanded = false;
  const applySumUseGlobal = () => {
    if (!sumUseGlobal) return;
    const on = sumUseGlobal.checked;
    if (!on) sumExpanded = false;
    if (sumManualBlock) sumManualBlock.style.display = on ? "none" : "contents";
    if (sumGlobalBlock) sumGlobalBlock.style.display = (on && sumExpanded) ? "" : "none";
    if (sumExpTri) sumExpTri.classList.toggle("open", !!sumExpanded);
  };
  if (sumExpTri) sumExpTri.onclick = () => { sumExpanded = !sumExpanded; applySumUseGlobal(); };
  if (sumUseGlobal) {
    sumUseGlobal.addEventListener("change", applySumUseGlobal);
    applySumUseGlobal();
  }

  $("#resetHtmlBtn").onclick = () => { $("#f_html_prompt").value = DEFAULT_HTML_PROMPT; };

  // 未启用 HTML 报告时，隐藏「HTML 报告生成 prompt」设置项。
  const htmlReportChk = $("#f_html_report");
  const htmlPromptField = $("#htmlPromptField");
  if (htmlReportChk && htmlPromptField) {
    htmlReportChk.addEventListener("change", () => {
      htmlPromptField.style.display = htmlReportChk.checked ? "" : "none";
    });
  }

  // 未启用浏览器接管时，隐藏「允许 Agent保留的最大标签页数」。
  const browserTakeoverChk = $("#f_browser_takeover");
  const browserTabMaxField = $("#browserTabMaxField");
  if (browserTakeoverChk && browserTabMaxField) {
    const applyBrowserTabMaxVisible = () => {
      browserTabMaxField.style.display = browserTakeoverChk.checked ? "" : "none";
    };
    browserTakeoverChk.addEventListener("change", applyBrowserTabMaxVisible);
    applyBrowserTabMaxVisible();
  }

  const subsBox = $("#subs");
  (cfg.sub_agents || []).forEach((s, i) => subsBox.appendChild(subAgentBox(s, i, canEditSubs)));
  const addBtn = $("#addSubBtn");
  if (addBtn) addBtn.onclick = () => subsBox.appendChild(subAgentBox(null, Date.now(), true));

  bindModelBlock(form);
}

function subAgentBox(s, key, canEdit) {
  const isNewSub = !s;
  s = s || { name: "", description: "", system_prompt: "", api_key: "", llm_provider_name: "deepseek:deepseek-v4-pro", mcp_servers: [], summary: { flush_history_tokenwise: 200000, reserve_message_round: 4 }, react_prompt: true };
  const flush = tokToUnit((s.summary || {}).flush_history_tokenwise);
  // 全局子 agent 总结设置（与主 agent 那套分开；同样是与全局一致则默认勾选）。
  const gSub = (settingsCache && settingsCache.global_sub_summary) || {};
  const globalSubSet = !!(settingsCache && settingsCache.global_sub_summary_configured) && Object.keys(gSub).length > 0;
  const subEqual = globalSubSet && (
    Number((s.summary || {}).flush_history_tokenwise || 0) === Number(gSub.flush_history_tokenwise || 0) &&
    Number((s.summary || {}).reserve_message_round || 0) === Number(gSub.reserve_message_round || 0)
  );
  const useGlobalSubInit = globalSubSet && (isNewSub || subEqual);
  const gSubFlush = tokToUnit(gSub.flush_history_tokenwise != null ? gSub.flush_history_tokenwise : 0);
  const div = document.createElement("div");
  div.className = "subagent-box";
  div.dataset.key = key;
  div.innerHTML = `
    <div class="sub-head"><span class="name">🧩 ${t("子 agent")}</span>${canEdit ? `<button class="btn danger small" data-act="remove" type="button">${t("移除")}</button>` : ""}</div>
    <div class="form-grid">
      <div class="field"><label>${t("名称")} name ${info("子 agent 标识，会作为工具名呈现给主 agent，创建后不可更改。")}</label><input data-f="name" value="${esc(s.name)}" ${canEdit ? "" : "readonly"}${ph("sub_name")}></div>
      ${modelBlockHTML(s.llm_provider_name, s.model, `sub_${key}_mode`)}
      <div class="field"><label>API Key ${info("该子 agent 所用模型的 API 密钥（明文存本地配置）。")}</label><input data-f="api_key" value="${esc(s.api_key)}" type="password"${ph("sub_api_key")}></div>
      ${globalSubSet ? `
      <div class="field full">
        <div class="global-toggle-row">
          <label style="display:flex;flex-direction:row;align-items:center;gap:8px;margin:0;"><input type="checkbox" data-f="sum_use_global" ${useGlobalSubInit ? "checked" : ""}> ${t("使用全局子 agent 总结设置")}</label>
          ${info("勾选后，本子 agent 直接使用主界面「系统设置 → 全局自动（及主动）总结设置 → 子 agent」里的清空历史阈值与保留轮数；取消勾选可为本子 agent 单独设置。")}
          <span class="exp-tri" data-f="sum_tri" title="${t("展开 / 收起说明")}"></span>
        </div>
      </div>
      <div class="field full" data-f="sum_global" style="display:none;">
        <div class="conn-example">
          <div>${t("当前使用的全局子 agent 总结配置：")}</div>
          <div style="margin-top:4px;">${t("自动全量总结阈值")} <code>${esc(String(gSubFlush.v))}${esc(t(gSubFlush.u))}</code> · ${t("自动以及主动全量总结后，需要保留的会话轮数")} <code>${esc(String(gSub.reserve_message_round != null ? gSub.reserve_message_round : 0))}</code></div>
          <div class="muted" style="margin-top:6px;font-size:11px;">${t("如需修改，请到主界面齿轮设置 →「全局自动（及主动）总结设置」。")}</div>
        </div>
      </div>` : ""}
      <div data-f="sum_manual" style="${globalSubSet && useGlobalSubInit ? "display:none;" : "display:contents;"}">
      <div class="field"><label>${t("自动全量总结阈值")} ${info("该子 agent 累计 token 达到该值时清空历史（只保留最近几轮）。")}</label><div class="unit-row"><input data-f="flush" value="${esc(flush.v)}" type="number"><select data-f="flush_unit"><option value="万" ${flush.u === "万" ? "selected" : ""}>${t("万")}</option><option value="千" ${flush.u === "千" ? "selected" : ""}>${t("千")}</option></select></div></div>
      <div class="field"><label>${t("自动以及主动全量总结后，需要保留的会话轮数")} ${info("清空历史时保留最近几轮对话。")}</label><input data-f="reserve" value="${esc((s.summary || {}).reserve_message_round)}" type="number"></div>
      </div>
      <div class="field full"><label>Description ${info("描述该子 agent 能力，作为工具描述呈现给主 agent。")}</label><textarea data-f="description" rows="3"${ph("sub_description")}>${esc(s.description)}</textarea></div>
      <div class="field full"><label>System Prompt ${info("该子 agent 的系统提示词。")}</label><textarea data-f="system_prompt" rows="6"${ph("sub_system_prompt")}>${esc(s.system_prompt)}</textarea></div>
      <div class="field full">
        <label style="flex-direction:row;align-items:center;gap:8px;"><input type="checkbox" data-f="react_prompt" ${s.react_prompt !== false ? "checked" : ""}> ${t("开启 ReAct Prompt")} ${info("用于长程多工具调用的严肃任务处理场景；日常聊天可关闭以节省 token 并加快响应。")}</label>
      </div>
      <div class="field full"><label>${t("MCP 服务器")} ${info("该子 agent 可用的工具来源；程序不负责启动，需外部自行启动。")}</label><div data-f="mcp"></div></div>
    </div>`;

  div.querySelector('[data-act="remove"]')?.addEventListener("click", () => div.remove());

  const mcpBox = div.querySelector('[data-f="mcp"]');
  (s.mcp_servers || []).forEach((m, j) => mcpBox.appendChild(mcpRow(m, j)));
  const addM = document.createElement("button");
  addM.className = "btn small add-mcp"; addM.type = "button"; addM.textContent = "+ " + t("添加 MCP");
  addM.onclick = () => mcpBox.insertBefore(mcpRow(null, Date.now()), addM);
  mcpBox.appendChild(addM);

  // 全局子 agent 总结开关：勾选时隐藏手动填写区；摘要默认折叠，点小三角才展开。
  const subSumUseGlobal = div.querySelector('[data-f="sum_use_global"]');
  if (subSumUseGlobal) {
    const subSumManual = div.querySelector('[data-f="sum_manual"]');
    const subSumGlobal = div.querySelector('[data-f="sum_global"]');
    const subSumTri = div.querySelector('[data-f="sum_tri"]');
    let subSumExpanded = false;
    const applySubSum = () => {
      const on = subSumUseGlobal.checked;
      if (!on) subSumExpanded = false;
      if (subSumManual) subSumManual.style.display = on ? "none" : "contents";
      if (subSumGlobal) subSumGlobal.style.display = (on && subSumExpanded) ? "" : "none";
      if (subSumTri) subSumTri.classList.toggle("open", !!subSumExpanded);
    };
    if (subSumTri) subSumTri.onclick = () => { subSumExpanded = !subSumExpanded; applySubSum(); };
    subSumUseGlobal.addEventListener("change", applySubSum);
    applySubSum();
  }

  bindModelBlock(div);
  return div;
}

function mcpRow(m, key) {
  m = m || { name: "", transport: "http", url: "", command: "", args: [], headers: {} };
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
    <input data-m="headers" placeholder="${t("请求头（http），如 Authorization: Bearer xxx；按第一个冒号切成 dict，多个用 ; 分隔，勿加引号")}" title="${t("请求头（http），如 Authorization: Bearer xxx；按第一个冒号切成 dict，多个用 ; 分隔，勿加引号")}" value="${esc(headersToStr(m.headers))}">
    <input data-m="command" placeholder="${t("命令（stdio）")}" value="${esc(m.command || "")}">
    <input data-m="args" placeholder="${t("参数，逗号分隔（stdio）")}" value="${esc((m.args || []).join(","))}">
    <button class="btn small" data-act="json" type="button" title="${t("查看 JSON 格式示例")}">{ }</button>
    <button class="btn danger small" data-act="rm" type="button">×</button>`;
  div.querySelector('[data-act="rm"]').onclick = () => div.remove();
  div.querySelector('[data-act="json"]').onclick = () => {
    const nameV = div.querySelector('[data-m="name"]').value;
    const trV = div.querySelector('[data-m="transport"]').value;
    const urlV = div.querySelector('[data-m="url"]').value;
    const cmdV = div.querySelector('[data-m="command"]').value;
    const argsV = div.querySelector('[data-m="args"]').value;
    showMcpJsonExample({
      name: nameV,
      transport: trV,
      url: urlV || null,
      headers: parseHeaders(div.querySelector('[data-m="headers"]').value),
      command: cmdV || null,
      args: argsV ? argsV.split(",").map(x => x.trim()).filter(Boolean) : [],
      env: {},
    });
  };

  const dot = div.querySelector("[data-dot]");
  const refresh = async () => {
    dot.className = "dot gray";
    dot.title = t("点击检测健康状态");
    const tr = div.querySelector('[data-m="transport"]').value;
    const res = await checkMcp(tr, div.querySelector('[data-m="url"]').value, div.querySelector('[data-m="command"]').value, parseHeaders(div.querySelector('[data-m="headers"]').value));
    dot.className = "dot " + (res.ok ? "green" : "red");
    if (!res.ok && res.error) dot.title = res.error;
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
    headers: parseHeaders(r.querySelector('[data-m="headers"]').value),
    command: r.querySelector('[data-m="command"]').value || null,
    args: r.querySelector('[data-m="args"]').value ? r.querySelector('[data-m="args"]').value.split(",").map(x => x.trim()).filter(Boolean) : [],
  }));
  const subUseGlobal = box.querySelector('[data-f="sum_use_global"]');
  let summary;
  if (subUseGlobal && subUseGlobal.checked) {
    // 使用全局子 agent 总结设置：把当前全局值写回本子 agent。
    const gs = (settingsCache && settingsCache.global_sub_summary) || {};
    summary = {
      flush_history_tokenwise: +(gs.flush_history_tokenwise != null ? gs.flush_history_tokenwise : unitToTok(box.querySelector('[data-f="flush"]').value, box.querySelector('[data-f="flush_unit"]').value)),
      reserve_message_round: +(gs.reserve_message_round != null ? gs.reserve_message_round : +get("reserve")),
    };
  } else {
    summary = {
      flush_history_tokenwise: unitToTok(box.querySelector('[data-f="flush"]').value, box.querySelector('[data-f="flush_unit"]').value),
      reserve_message_round: +get("reserve"),
    };
  }
  return {
    name: get("name"),
    description: get("description"),
    system_prompt: get("system_prompt"),
    api_key: get("api_key"),
    llm_provider_name: box.querySelector('[data-mf="llm_provider_name"]').value,
    model: collectModelCfg(box),
    mcp_servers: mcp,
    summary,
    react_prompt: box.querySelector('[data-f="react_prompt"]').checked,
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
  const name = (val("f_name") || "").trim();
  const namespace = (val("f_ns") || "")
    .split(/[,，]/)
    .map(x => x.trim())
    .filter(Boolean);
  const pgUseGlobal = $("#f_pg_use_global");
  let conn;
  if (pgUseGlobal && pgUseGlobal.checked) {
    // 使用全局连接设置：把当前全局前缀/后缀写回本 agent（运行时后端仍只读 agent 自己这份）。
    const gp = settingsCache || {};
    const prefix = gp.global_postgres_prefix || "";
    let suffix = gp.global_postgres_suffix || "";
    if (!suffix && isLocalPrefix(prefix)) suffix = "?sslmode=disable";
    conn = { ok: true, prefix, suffix };
  } else {
    conn = collectPgConn();
  }
  if (!conn.ok) throw new Error(conn.error);
  const embUseGlobalEl = $("#f_emb_use_global");
  let embedding;
  if (embUseGlobalEl && embUseGlobalEl.checked) {
    // 使用全局 embedding 设置：把当前全局值写回本 agent（运行时后端仍只读 agent 自己这份）。
    const ge = (settingsCache && settingsCache.global_embedding) || {};
    const cfgEmb = cfg.main_agent.embedding || {};
    embedding = {
      model_name: ge.model_name || embModelValue(),
      cache_folder: ge.cache_folder || "",
      dims: +(ge.dims != null ? ge.dims : val("f_emb_dims")),
      device: ge.device != null ? ge.device : (cfgEmb.device || "cpu"),
      encode_normalize: ge.encode_normalize != null ? ge.encode_normalize : (cfgEmb.encode_normalize ?? true),
      local_files_only: !!ge.local_files_only,
      hf_endpoint: ge.hf_endpoint || "",
    };
  } else {
    embedding = {
      model_name: embModelValue(),
      cache_folder: val("f_emb_cache"),
      dims: +val("f_emb_dims"),
      device: (cfg.main_agent.embedding || {}).device || "cpu",
      encode_normalize: (cfg.main_agent.embedding || {}).encode_normalize ?? true,
      local_files_only: $("#f_emb_offline").checked,
      hf_endpoint: hfEndpointValue(),
    };
  }
  const sumUseGlobalEl = $("#f_sum_use_global");
  let summary;
  if (sumUseGlobalEl && sumUseGlobalEl.checked) {
    // 使用全局总结设置：把当前全局值写回本 agent（运行时后端仍只读 agent 自己这份）。
    const gs = (settingsCache && settingsCache.global_summary) || {};
    summary = {
      summarize_gap_tokenwise: +(gs.summarize_gap_tokenwise != null ? gs.summarize_gap_tokenwise : unitToTok(val("f_sum_gap"), val("f_sum_gap_unit"))),
      flush_history_tokenwise: +(gs.flush_history_tokenwise != null ? gs.flush_history_tokenwise : unitToTok(val("f_sum_flush"), val("f_sum_flush_unit"))),
      reserve_message_round: +(gs.reserve_message_round != null ? gs.reserve_message_round : +val("f_sum_reserve")),
    };
  } else {
    summary = {
      summarize_gap_tokenwise: unitToTok(val("f_sum_gap"), val("f_sum_gap_unit")),
      flush_history_tokenwise: unitToTok(val("f_sum_flush"), val("f_sum_flush_unit")),
      reserve_message_round: +val("f_sum_reserve"),
    };
  }
  return {
    // 名称是唯一身份输入：agent_id 与 checkpoint 数据库都取同一个值（后端会再校验一次）。
    agent_id: name,
    name,
    postgres: {
      prefix: conn.prefix,
      suffix: conn.suffix,
      checkpoint_database: name,
      store_namespace: namespace,
    },
    main_agent: {
      system_prompt: val("f_prompt"),
      api_key: val("f_apikey"),
      llm_provider_name: $("#editorForm").querySelector('[data-mf="llm_provider_name"]').value,
      model: collectModelCfg($("#editorForm")),
      file_tools: { root_dir: val("f_rootdir") },
      embedding,
      summary,
      html_report: $("#f_html_report").checked,
      html_report_prompt: val("f_html_prompt"),
      react_prompt: $("#f_react_prompt").checked,
      browser_takeover: $("#f_browser_takeover").checked,
      browser_tab_max_number: Math.min(50, Math.max(3, parseInt(val("f_browser_tab_max"), 10) || 4)),
      memory_tools_enabled: $("#f_memory_tools").checked,
    },
    sub_agents: $$(".subagent-box").map(collectSubAgent),
    output: cfg.output || { stream_output_dir: "" },
  };
}

/* 名称同时是 agent_id（文件名）与 checkpoint 数据库名，前端先做同样的校验，
 * 后端仍会再校验一次（以前端为准的只是提示速度）。 */
function validateAgentName(raw) {
  const name = String(raw || "").trim();
  if (!name) return t("请填写名称");
  if (name === "." || name === "..") return t("名称不能是「.」或「..」");
  if (name.startsWith(".")) return t("名称不能以点开头（会变成隐藏文件）");
  if (/[. ]$/.test(name)) return t("名称不能以点或空格结尾（Windows 文件名限制）");
  if (["postgres", "template0", "template1"].includes(name.toLowerCase())) {
    return t("「postgres」等系统库名称不能作为 multi-agent 名称");
  }
  const reserved = ["default", "settings", "ui_prefs", "drafts", "common_prompts", "store_migrations", "identity_migration", "floating_stop_pos"];
  if (reserved.includes(name.toLowerCase())) {
    return t("「settings」等程序内部配置文件名不能作为 multi-agent 名称");
  }
  const bad = name.match(/[\\/:*?"<>|]/g);
  if (bad) return `${t("名称不能包含这些字符：")}${[...new Set(bad)].join(" ")}${t("（名称会同时用作文件名与数据库名）")}`;
  const bytes = window.TextEncoder ? new TextEncoder().encode(name).length : name.length;
  if (bytes > 63) return t("名称过长：最多 63 字节（约 21 个汉字或 63 个英文字符），因为名称会直接作为 checkpoint 数据库名。");
  return "";
}

async function saveConfig(cfg, isNew, isDefault) {
  try {
    const payload = buildPayload(cfg);
    // 默认配置模板允许不填名称（它只是新建时的预填内容）；真正的 multi-agent 必须填。
    if (!isDefault || payload.name) {
      const nameErr = validateAgentName(payload.name);
      if (nameErr) return toast(nameErr, true);
    }
    if (!payload.postgres.store_namespace.length) return toast(t("请填写长期记忆分组"), true);
    if (payload.postgres.store_namespace.some(x => x.includes("."))) {
      return toast(t("长期记忆分组的单个层级不能包含点号，请用逗号分隔层级（例如 teamA,projectX）"), true);
    }
    if (payload.sub_agents.some(s => !(s.name || "").trim())) return toast(t("子 agent 名称不能为空"), true);

    if (isDefault) { await api("/api/default", { method: "PUT", body: JSON.stringify(payload) }); toast(t("默认配置已保存")); goAgents(); }
    else if (isNew) {
      await api("/api/agents", { method: "POST", body: JSON.stringify(payload) });
      // 后端可能在首次创建 agent 时顺带初始化「全局 postgres 连接设置」，清掉前端缓存以便下次重新拉取。
      settingsCache = null;
      toast(t("已创建"));
      goAgents();
    }
    else { payload.agent_id = S.agentId; await api("/api/agents/" + encodeURIComponent(S.agentId), { method: "PUT", body: JSON.stringify(payload) }); toast(t("已保存")); goAgents(); }
  } catch (e) { toast(e.message, true); }
}

/* ================= 视图3：Thread 列表 ================= */
async function renderThreadsView() {
  if (threadsStatusTimer) { clearInterval(threadsStatusTimer); threadsStatusTimer = null; }
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
    <div class="snapshot-drawer" id="snapshotDrawer">
      <button class="snapshot-handle" id="snapshotHandle" type="button">📸 <span id="snapshotLabel">${t("快照")}</span></button>
      <button class="showhidden-zone" id="showHiddenZone" type="button" style="display:none;">
        <span class="sw-label" id="showHiddenLabel"></span>
        <span class="toggle"><input type="checkbox" id="showHiddenChk"><span class="track"></span></span>
      </button>
      <div class="snapshot-panel" id="snapshotPanel" style="display:none;">
        <div class="snapshot-panel-head">
          <span class="snapshot-panel-title">📸 ${t("快照列表")}</span>
          <button class="btn small" id="snapshotClose">×</button>
        </div>
        <div class="snapshot-list" id="snapshotList"><div class="muted">${t("加载中…")}</div></div>
      </div>
    </div>`;
  $("#newThreadBtn").onclick = () => createNewThread();

  // 加载会话列表。数据库服务可能还在启动（低配机器尤其慢），此时不要把一堆报错甩给用户，
  // 而是显示「正在启动，请稍候」并自动重试；就绪后无缝进入列表。
  let threads = null;
  const loadStartedAt = Date.now();
  const MAX_DB_WAIT_MS = 180000;
  while (threads === null) {
    try {
      threads = await api(`/api/agents/${encodeURIComponent(S.agentId)}/threads`);
    } catch (e) {
      const box = $("#threadsBox");
      if (!box) return; // 用户已离开本页
      const stillLoading = e.code === "db_unavailable" && e.retryable === true && (Date.now() - loadStartedAt) < MAX_DB_WAIT_MS;
      if (stillLoading) {
        const secs = Math.floor((Date.now() - loadStartedAt) / 1000);
        box.innerHTML = `<div class="empty" style="max-width:620px;margin:0 auto;">
          <div class="big">⏳</div>
          <div style="font-size:16px;">${t("数据库服务正在启动，请稍候…")}</div>
          <div class="muted" style="margin-top:10px;line-height:1.8;">${t("程序正在为你加载数据库服务，通常很快；电脑配置较低时可能需要 1～2 分钟。就绪后会自动进入，请不要关闭窗口。")}</div>
          <div class="muted" style="margin-top:8px;">${t("已等待 {sec} 秒").replace("{sec}", String(secs))}</div>
        </div>`;
        await sleep(2000);
        continue;
      }
      box.innerHTML = `<div class="empty" style="max-width:680px;margin:0 auto;text-align:left;">
        <div class="big" style="text-align:center;">⚠️</div>
        <div style="line-height:1.8;">${brk(e.message)}</div>
        <div style="text-align:center;margin-top:18px;"><button class="btn primary" id="threadsRetry">${t("重试")}</button></div>
      </div>`;
      const rb = $("#threadsRetry"); if (rb) rb.onclick = () => renderThreadsView();
      return;
    }
  }

  const hiddenKey = "hidden_threads_" + S.agentId;
  const showKey = "show_hidden_" + S.agentId;
  const hiddenSet = new Set(JSON.parse(localStorage.getItem(hiddenKey) || "[]"));
  await refreshSessions();

  // 快照列表：用于决定「快照」按钮是否显示（没有快照就不显示按钮）
  let snapshots = [];
  try { snapshots = await api(`/api/agents/${encodeURIComponent(S.agentId)}/snapshots`); }
  catch (e) { snapshots = []; }

  // 底部栏可见性（与主界面一致）：
  // - 没有快照 → 不显示「快照」按钮
  // - 没有隐藏会话 → 不显示「显示隐藏对话」
  // - 两者都没有 → 整条底栏都不显示
  function updateThreadsFooter() {
    const hiddenCnt = threads.filter(t => hiddenSet.has(t.thread_id)).length;
    const hasSnap = snapshots.length > 0;
    const zone = $("#showHiddenZone");
    if (hiddenCnt > 0) {
      $("#showHiddenChk").checked = localStorage.getItem(showKey) === "1";
      $("#showHiddenLabel").textContent = t("显示隐藏对话") + `（${hiddenCnt}）`;
      zone.style.display = "flex";
      zone.style.borderLeft = hasSnap ? "" : "none";
    } else {
      zone.style.display = "none";
    }
    // 快照按钮上显示现有快照数量（与「显示隐藏对话」一致）
    $("#snapshotLabel").textContent = t("快照") + (snapshots.length ? `（${snapshots.length}）` : "");
    $("#snapshotHandle").style.display = hasSnap ? "" : "none";
    if (!hasSnap) $("#snapshotPanel").style.display = "none";
    $("#snapshotDrawer").style.display = (hasSnap || hiddenCnt > 0) ? "" : "none";
  }

  // 复用已加载的 threads，仅原地重绘列表，避免整页 flash /「加载中」闪屏
  function threadStateOf(tid) {
    return (sessionSnapshot.sessions || []).find(
      (s) => s.agent_id === S.agentId && s.thread_id === tid
    ) || null;
  }

  function threadStateHtml(info) {
    if (!info) return "";
    if (info.status === "completed") {
      return `<span class="thread-run-state done">${t("任务完成")}</span>`;
    }
    return `<span class="thread-run-state running" title="${t("该会话正在进行")}">
      <span class="thread-run-viewport"><span class="thread-run-track"><span>${t("正在进行")}</span><span>${t("正在进行")}</span></span></span>
    </span>`;
  }

  function drawThreadsBox() {
    const showNow = localStorage.getItem(showKey) === "1";
    const vis = showNow ? threads : threads.filter(t => {
      const info = threadStateOf(t.thread_id);
      return !hiddenSet.has(t.thread_id) || isSessionPending(info);
    });
    const hiddenCnt = threads.filter(t => hiddenSet.has(t.thread_id)).length;
    updateThreadsFooter();
    if (!vis.length) {
      const msg = hiddenCnt ? t("所有会话均已隐藏") : t("暂无会话");
      $("#threadsBox").innerHTML = `<div class="empty"><div class="big">🧵</div>${msg}<br/><br/><button class="btn primary" id="nt2">+ ${t("新建会话")}</button></div>`;
      const n = $("#nt2"); if (n) n.onclick = () => createNewThread();
    } else {
      $("#threadsBox").innerHTML = `
        <div style="background:var(--surface);border-radius:var(--radius);overflow:hidden;box-shadow:var(--shadow);">
          ${vis.map(th => {
            const hidden = hiddenSet.has(th.thread_id);
            const cpText = th.checkpoints ? `${th.checkpoints} checkpoints` : t("未发送消息");
            const info = threadStateOf(th.thread_id);
            const locked = !!info;
            const lockTitle = locked ? t("该会话正在运行或等待验收，暂时不能隐藏或删除") : "";
            return `
            <div class="thread-row ${hidden ? "thread-hidden" : ""}" data-tid="${esc(th.thread_id)}">
              <span class="tid">${esc(th.thread_id)}</span>
              ${threadStateHtml(info)}
              <span class="meta"><small>${esc(cpText)}</small><small>${esc(th.last_updated || "")}</small>${hidden ? `<small class="hidden-tag">${t("已隐藏")}</small>` : ""}</span>
              <button class="btn card-btn card-btn-hide small" data-hide="${esc(th.thread_id)}" ${locked ? "disabled" : ""} title="${lockTitle}">${hidden ? t("取消隐藏") : t("隐藏")}</button>
              <button class="btn card-btn card-btn-danger small" data-del="${esc(th.thread_id)}" ${locked ? "disabled" : ""} title="${lockTitle}">${t("删除")}</button>
            </div>`;
          }).join("")}
        </div>`;

      $$("#threadsBox .thread-row").forEach(row => {
        const tid = row.dataset.tid;
        row.onclick = () => goChat(S.agentId, S.agentName, tid);
        const delBtn = row.querySelector("[data-del]");
        const hideBtn = row.querySelector("[data-hide]");
        if (delBtn && !delBtn.disabled) delBtn.onclick = (e) => { e.stopPropagation(); deleteThread(tid); };
        if (hideBtn && !hideBtn.disabled) hideBtn.onclick = (e) => {
          e.stopPropagation();
          if (hiddenSet.has(tid)) hiddenSet.delete(tid);
          else hiddenSet.add(tid);
          localStorage.setItem(hiddenKey, JSON.stringify([...hiddenSet]));
          drawThreadsBox();
        };
      });
    }
  }
  drawThreadsBox();
  // 正在运行 / 完工状态来自后端会话中枢；只在状态签名变化时原地重绘，避免闪烁。
  let lastThreadStatusSig = JSON.stringify((sessionSnapshot.sessions || [])
    .filter(s => s.agent_id === S.agentId)
    .map(s => [s.thread_id, s.status, s.accepted]));
  threadsStatusTimer = setInterval(async () => {
    if (!$("#threadsBox")) { clearInterval(threadsStatusTimer); threadsStatusTimer = null; return; }
    await refreshSessions();
    const sig = JSON.stringify((sessionSnapshot.sessions || [])
      .filter(s => s.agent_id === S.agentId)
      .map(s => [s.thread_id, s.status, s.accepted]));
    if (sig !== lastThreadStatusSig) {
      lastThreadStatusSig = sig;
      drawThreadsBox();
    }
  }, 2000);

  $("#showHiddenChk").onchange = (e) => { localStorage.setItem(showKey, e.target.checked ? "1" : "0"); drawThreadsBox(); };
  $("#showHiddenZone").onclick = (e) => {
    e.preventDefault();
    const chk = $("#showHiddenChk");
    chk.checked = !chk.checked;
    chk.dispatchEvent(new Event("change"));
  };

  // ── 快照抽屉 ─────────────────────────────
  const snapPanel = $("#snapshotPanel");
  const snapListEl = $("#snapshotList");
  async function loadSnapshots() {
    snapListEl.innerHTML = `<div class="muted">${t("加载中…")}</div>`;
    let snaps;
    try { snaps = await api(`/api/agents/${encodeURIComponent(S.agentId)}/snapshots`); }
    catch (e) { snapListEl.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
    snapshots = snaps;
    updateThreadsFooter();
    if (!snaps.length) {
      snapListEl.innerHTML = `<div class="muted" style="padding:12px;">${t("暂无快照")}</div>`;
      return;
    }
    snapListEl.innerHTML = snaps.map(s => `
      <div class="snapshot-row" data-sid="${esc(s.id)}">
        <div class="snapshot-info">
          <span class="snapshot-name">${esc(s.thread_id)}</span>
          <span class="snapshot-meta">${esc(s.created_at)} · ${s.total_messages} ${t("条消息")}</span>
        </div>
        <button class="btn card-btn card-btn-danger small" data-snap-del="${esc(s.id)}">${t("删除")}</button>
      </div>`).join("");

    $$("#snapshotList .snapshot-row").forEach(row => {
      const sid = row.dataset.sid;
      row.onclick = (e) => { if (e.target.closest("button")) return; goSnapshot(S.agentId, S.agentName, sid); };
      row.querySelector("[data-snap-del]").onclick = async (e) => {
        e.stopPropagation();
        if (!confirm(`${t("确定删除快照")}「${sid}」？${t("此操作不可撤销。")}`)) return;
        try {
          await api(`/api/agents/${encodeURIComponent(S.agentId)}/snapshots/${encodeURIComponent(sid)}`, { method: "DELETE" });
          toast(t("已删除"));
          loadSnapshots();
        } catch (err) { toast(err.message, true); }
      };
    });
  }
  $("#snapshotHandle").onclick = () => {
    const open = snapPanel.style.display !== "none";
    snapPanel.style.display = open ? "none" : "";
    if (!open) loadSnapshots();
  };
  $("#snapshotClose").onclick = () => { snapPanel.style.display = "none"; };
}
async function createNewThread() {
  const n = prompt(t("新会话名称："));
  if (!n || !n.trim()) return;
  const tid = n.trim();
  // 先在后端登记会话，确保即使不发消息，退出后也仍在会话列表中
  try {
    await api(`/api/agents/${encodeURIComponent(S.agentId)}/threads`, {
      method: "POST",
      body: JSON.stringify({ thread_id: tid }),
    });
  } catch (e) { toast(e.message, true); return; }
  goChat(S.agentId, S.agentName, tid);
}
async function deleteThread(tid) {
  if (!confirm(`${t("确定删除会话")}「${tid}」？${t("此操作不可撤销。")}`)) return;
  try { await api(`/api/agents/${encodeURIComponent(S.agentId)}/threads/${encodeURIComponent(tid)}`, { method: "DELETE" }); toast(t("已删除")); renderThreadsView(); }
  catch (e) { toast(e.message, true); }
}

/* ================= 视图4：聊天 ================= */
const PHASE_TEMPLATES = {
  thinking: "{actor} 思考中",
  answering: "{actor} 回答中",
  tool_edit: "{actor} 编辑工具调取指令",
  delegate: "{actor} 给子 agent 发布任务",
  tool_wait: "{actor} 等待工具执行完毕",
};

function setStatusIndicator(mode, actor) {
  statusMode = mode;
  const ind = $("#statusInd");
  if (!ind) return;
  if (PHASE_TEMPLATES[mode]) {
    ind.className = "status-indicator " + mode;
    const label = t(PHASE_TEMPLATES[mode]).replace("{actor}", esc(actor || t("主 agent")));
    ind.innerHTML = `<span class="status-dot ${mode}"></span>${label}`;
  }
  else if (mode === "loading") { ind.className = "status-indicator loading"; ind.innerHTML = `<span class="status-dot loading"></span>${t("加载Embedding模型中")}`; }
  else if (mode === "waiting") { ind.className = "status-indicator waiting"; ind.innerHTML = `<span class="status-dot waiting"></span>${t("等待确认…")}`; }
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
        // 排除思考过程（.reasoning-body）里的标题：那只是 LLM 的规划，不应混进消息目录
        ai.querySelectorAll("h1, h2, h3, h4, h5, h6").forEach(h => { if (!h.closest(".reasoning-body")) headings.push(h); });
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
  // 顶栏按钮换行到下一行时，面板可能横向超出视口；append 后按视口做一次校正
  const margin = 8;
  const br = btn.getBoundingClientRect();
  const pr = panel.getBoundingClientRect();
  if (pr.left < margin) {
    panel.style.right = "auto";
    panel.style.left = `${margin - br.left}px`;
  } else if (pr.right > window.innerWidth - margin) {
    panel.style.left = "auto";
    panel.style.right = `${br.right - (window.innerWidth - margin)}px`;
  }
}

/* ================= 未发送消息缓存（draft） ================= */
// 每个 multi-agent 一条、后端落盘。输入时防抖保存；退出会话 / 关闭浏览器 / 退出托盘程序时兜底落盘。
const DRAFT_DEBOUNCE_MS = 400;
let draftFlushFn = null; // 当前聊天视图的「立即落盘」函数，供卸载 / 退出视图时调用
let draftMemCache = {};  // 会话内内存缓存，避免频繁请求、并规避「落盘后立即读取」的竞态

function draftSyncAll() {
  return !(settingsCache && settingsCache.cross_agent_draft_flow === false);
}
// 后端始终按 per-agent 落盘；这里只是会话内缓存：跨 agent 流转时清空其它条目，强制回后端取同步值
function draftCacheStore(agentId, text) {
  if (draftSyncAll()) draftMemCache = {};
  draftMemCache[agentId] = text;
}
async function saveDraft(agentId, text) {
  draftCacheStore(agentId, text);
  try {
    await api(`/api/drafts/${encodeURIComponent(agentId)}`, {
      method: "PUT",
      body: JSON.stringify({ text, sync_all: draftSyncAll() }),
    });
  } catch (e) { /* 缓存写入失败不应阻断交互 */ }
}
function saveDraftKeepalive(agentId, text) {
  draftCacheStore(agentId, text);
  try {
    fetch(`/api/drafts/${encodeURIComponent(agentId)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, sync_all: draftSyncAll() }),
      keepalive: true,
    });
  } catch (e) {}
}
function flushCurrentDraft() {
  try { if (draftFlushFn) draftFlushFn(); } catch (e) {}
}
window.addEventListener("beforeunload", flushCurrentDraft);
window.addEventListener("pagehide", flushCurrentDraft);
window.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") flushCurrentDraft(); });

async function renderChatView() {
  app.innerHTML = topbar(t("返回会话"), () => leaveChatView());
  const view = document.createElement("div");
  view.className = "chat-view";
  app.appendChild(view);
  bindBack(() => leaveChatView());

  view.innerHTML = `
    <div class="chat-head">
      <div class="chat-head-left">
        <span class="thread-name">🧵 ${esc(S.threadId)}</span>
        <span class="muted">（${esc(S.agentName)}）</span>
      </div>
      <div class="chat-head-right">
      <span class="status-indicator" id="statusInd"></span>
      <span class="edit-mode-badge" id="editModeBadge" style="display:none;">✏️ ${t("编辑模式")}</span>
      <button class="btn small" id="localMdConvertBtn" style="display:none;" title="${t("选择一个本地 .md 文件，用系统设置里的「HTML 转换配置表」把它转成可打印 HTML，保存在该 md 文件所在目录（同名 .html；已存在时另存为带时间戳的文件）。")}">🖨 ${t("将本地md转换为HTML")}</button>
      <button class="btn small" id="editSessionPromptBtn" style="display:none;">📝 ${t("编辑会话常用prompt")}</button>
      <button class="btn small" id="memoryStoreBtn" title="${t("查看/编辑记忆库")}">🧠 ${t("查看/编辑记忆库")}</button>
      <button class="btn small" id="delAllMsgBtn" title="${t("删除全部历史消息")}">🗑️ ${t("删除全部历史消息")}</button>
      <button class="btn small" id="editModeBtn">✏️ ${t("进入编辑模式")}</button>
      <button class="btn small" id="proactiveSummaryBtn" title="${t("主动全量总结")}">📝 ${t("主动全量总结")}</button>
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
    </div>
    <div class="chat-body" id="chatBody">
      <div class="history-pane" id="historyPane"><div class="md-body markdown-body" id="historyMd"><div class="muted">${t("加载历史中…")}</div></div></div>
      <button id="pinBtn" class="pin-btn" title="${t("跟随最新输出 · 按住可拖动")}">📌</button>
      <div class="input-pane" id="inputPane">
        <div class="input-main">
          <div class="input-main-left">
            <div class="input-toolbar"><span class="muted" id="inputHint">${t("输入消息（Enter 发送，Shift+Enter 换行）")}</span><div class="spacer" style="flex:1;"></div></div>
            <div class="date-inject-hint" id="dateInjectHint" style="display:none;"></div>
            <div class="date-inject-hint" id="timeInjectHint" style="display:none;"></div>
            <div class="date-inject-hint common-prompt-hint" id="commonPromptHint" style="display:none;" title="${t("点击查看完整内容")}"></div>
            <textarea id="msgInput" placeholder="${t("输入消息…")}"></textarea>
            <div class="inject-blocks">
              <div class="inject-block" id="sensitiveInjectBlock" style="display:none;"></div>
              <div class="inject-block" id="takeoverInjectBlock" style="display:none;"></div>
            </div>
          </div>
          <div class="input-actions-col">
            <div class="input-actions-top">
              <button class="btn small" id="stopBtn" style="display:none;">${t("停止")}</button>
              <button class="btn small" id="timeInjectBtn" style="display:none;" title="${t("开启后，发送消息时会在用户消息前面拼接当前时间。")}"><span class="date-dot"></span>${t("注入当前时间")}</button>
              <button class="btn small" id="dateInjectBtn" style="display:none;" title="${t("开启后，发送消息时会在用户消息前面拼接当前日期。")}"><span class="date-dot"></span>${t("注入当前日期")}</button>
              <button class="btn small" id="commonPromptInjectBtn" style="display:none;"><span class="date-dot"></span>${t("注入常用prompt")}</button>
              <button class="btn small" id="codeInputModeBtn" style="display:none;" title="${t("开启后，在输入框内按 Tab 会插入 4 个空格，而不是把焦点切换到下一个按钮。")}"><span class="date-dot"></span>${t("代码输入模式")}</button>
            </div>
            <button class="btn small primary" id="sendBtn">${t("发送")}</button>
          </div>
          <div class="browser-takeover-actions" id="browserTakeoverActions" style="display:none;">
            <button class="btn small" id="openBrowserBtn"><span class="tk-dot"></span>${t("打开浏览器")}</button>
            <button class="btn small" id="browserHintBtn"><span class="tk-dot"></span>${t("提示Agent开始接管浏览器")}</button>
            <button class="btn small" id="privacyMaskBtn"><span class="tk-dot"></span>${t("隐私遮蔽模式")}</button>
            <button class="btn small" id="sensitiveInjectBtn"><span class="tk-dot"></span>${t("注入 敏感信息替换列表")}</button>
          </div>
        </div>
      </div>
      <div id="doneBubble" class="done-bubble done-bubble-float" style="display:none;"></div>
      <div id="failBubble" class="fail-bubble fail-bubble-float" style="display:none;">
        <div class="fail-bubble-text">${t("本次Agent循环失败/被中断，是否将 原发送信息回填？")}</div>
        <div class="fail-bubble-actions">
          <button type="button" class="fail-bubble-btn fail-bubble-cancel" id="failCancelBtn" title="${t("丢弃")}">✗</button>
          <button type="button" class="fail-bubble-btn fail-bubble-restore" id="failRestoreBtn" title="${t("回填")}">✓</button>
        </div>
      </div>
      <div id="editSendWarnBubble" class="edit-send-warn" style="display:none;"></div>
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
  // 把坐标钳制在 chatBody 可视区内，避免窗口变小后按钮被 overflow:hidden 裁掉
  const clampPinPos = (pos) => {
    const bw = pinBtn.offsetWidth || 34, bh = pinBtn.offsetHeight || 34;
    return {
      left: Math.min(Math.max(0, pos.left), Math.max(0, chatBody.clientWidth - bw)),
      top: Math.min(Math.max(0, pos.top), Math.max(0, chatBody.clientHeight - bh)),
    };
  };
  // 位置以「占 chatBody 可移动范围的比例」存储，窗口缩放前后保持相对位置不变
  const maxPinLeft = () => Math.max(0, chatBody.clientWidth - (pinBtn.offsetWidth || 34));
  const maxPinTop = () => Math.max(0, chatBody.clientHeight - (pinBtn.offsetHeight || 34));
  const posFromRatio = (r) => ({
    left: Math.round(Math.min(Math.max(0, r.rx), 1) * maxPinLeft()),
    top: Math.round(Math.min(Math.max(0, r.ry), 1) * maxPinTop()),
  });
  const ratioFromPos = (pos) => {
    const ml = maxPinLeft(), mt = maxPinTop();
    return {
      rx: ml ? Math.min(Math.max(0, pos.left / ml), 1) : 0,
      ry: mt ? Math.min(Math.max(0, pos.top / mt), 1) : 0,
    };
  };

  let customPos = null;
  try { customPos = JSON.parse(localStorage.getItem("pin-pos") || "null"); } catch (e) {}
  if (customPos && typeof customPos.rx === "number") {
    applyPinPos(posFromRatio(customPos));
  } else if (customPos && typeof customPos.left === "number") {
    // 兼容旧格式（绝对像素）：按当前尺寸换算成比例
    customPos = ratioFromPos(customPos);
    applyPinPos(posFromRatio(customPos));
  } else {
    customPos = null;
    applyPinPos(clampPinPos(defaultPinPos()));
  }

  // 窗口尺寸变化时按比例重算图钉位置（缩放前后相对位置不变）
  window.addEventListener("resize", () => {
    if (!pinBtn.isConnected) return;
    if (customPos) applyPinPos(posFromRatio(customPos));
    else applyPinPos(clampPinPos(defaultPinPos()));
  });

  if (window.Split) {
    Split(["#historyPane", "#inputPane"], {
      direction: "vertical", sizes: [72, 28], minSize: [100, 80], gutterSize: 8, cursor: "row-resize",
      onDragEnd: () => { if (!customPos) applyPinPos(clampPinPos(defaultPinPos())); },
    });
  }

  applyZoom();
  applyReasoningZoom();
  let settings = null;
  try { settings = await getSettings(); } catch (e) {}
  // 「将本地md转换为HTML」按钮（在「编辑会话常用prompt」左侧）是否显示由系统设置决定；
  // 这里尽早显隐，避免等历史加载完才「跳出来」。
  {
    const localMdBtnEl = $("#localMdConvertBtn");
    if (localMdBtnEl) {
      const showLocalMd = !(settings && settings.show_local_md_convert_button === false);
      localMdBtnEl.style.display = showLocalMd ? "" : "none";
    }
  }
  if (settings) {
    const keyLabel = k => ({ enter: "Enter", shift_enter: "Shift+Enter", ctrl_enter: "Ctrl+Enter" })[k] || k;
    const hint = $("#inputHint");
    if (hint) {
      hint.textContent = settings.send_key === "mouse_only"
        ? t("输入消息（点击发送，Enter 换行）")
        : `${t("输入消息（")}${keyLabel(settings.send_key)} ${t("发送，")}${keyLabel(settings.newline_key)} ${t("换行）")}`;
    }
    const editBtn = $("#editModeBtn");
    if (editBtn) editBtn.style.display = (settings.edit_mode_enabled !== false) ? "" : "none";
  }

  // 需求4：进入/切换会话即后台预热该 agent 的运行时（加载 embedding、建图），
  // 避免用户真正发送消息时才开始加载而白白等待。失败静默（发送时仍会走既有加载提示）。
  api(`/api/agents/${encodeURIComponent(S.agentId)}/warmup`, { method: "POST" }).catch(() => {});

  /* ================= 注入当前日期 ================= */
  const dateInjectAvailable = !(settings && settings.show_date_inject_button === false);
  let dateInjectOn = localStorage.getItem("date-inject-on") === "1";
  function datePromptText() {
    const now = new Date();
    const y = now.getFullYear(), m = now.getMonth(), d = now.getDate();
    if (lang === "zh") {
      return `今天是${y}年${String(m + 1).padStart(2, "0")}月${String(d).padStart(2, "0")}日。`;
    }
    const months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
    return `Today's date is ${months[m]} ${d}, ${y}.`;
  }
  function refreshDateInjectUI() {
    const btn = $("#dateInjectBtn"), hint = $("#dateInjectHint");
    if (btn) btn.classList.toggle("on", dateInjectOn);
    if (hint) {
      if (dateInjectOn) { hint.textContent = datePromptText(); hint.style.display = ""; }
      else { hint.style.display = "none"; }
    }
  }
  const dateInjectBtn = $("#dateInjectBtn");
  if (dateInjectBtn) {
    dateInjectBtn.style.display = dateInjectAvailable ? "" : "none";
    dateInjectBtn.onclick = () => {
      dateInjectOn = !dateInjectOn;
      localStorage.setItem("date-inject-on", dateInjectOn ? "1" : "0");
      refreshDateInjectUI();
    };
  }
  refreshDateInjectUI();

  /* ================= 注入当前时间 ================= */
  const timeInjectAvailable = !(settings && settings.show_time_inject_button === false);
  let timeInjectOn = localStorage.getItem("time-inject-on") === "1";
  function timePromptText() {
    const now = new Date();
    const hours24 = now.getHours();
    const minutes = String(now.getMinutes()).padStart(2, "0");
    const hours24Text = String(hours24).padStart(2, "0");
    let hour12 = hours24 % 12;
    if (hour12 === 0) hour12 = 12;
    if (lang === "zh") {
      return `现在是${hours24 < 12 ? "上午" : "下午"}${hour12}点${minutes}分（${hours24Text}:${minutes}）`;
    }
    return `It is now ${hour12}:${minutes} ${hours24 < 12 ? "AM" : "PM"} (${hours24Text}:${minutes}).`;
  }
  function refreshTimeInjectUI() {
    const btn = $("#timeInjectBtn"), hint = $("#timeInjectHint");
    if (btn) btn.classList.toggle("on", timeInjectOn);
    if (hint) {
      if (timeInjectOn) { hint.textContent = timePromptText(); hint.style.display = ""; }
      else { hint.style.display = "none"; }
    }
  }
  const timeInjectBtn = $("#timeInjectBtn");
  if (timeInjectBtn) {
    timeInjectBtn.style.display = timeInjectAvailable ? "" : "none";
    timeInjectBtn.onclick = () => {
      timeInjectOn = !timeInjectOn;
      localStorage.setItem("time-inject-on", timeInjectOn ? "1" : "0");
      refreshTimeInjectUI();
    };
  }
  refreshTimeInjectUI();

  const codeInputAvailable = !(settings && settings.show_code_input_button === false);
  let codeInputOn = localStorage.getItem("code-input-mode-on") === "1";
  function refreshCodeInputUI() {
    const btn = $("#codeInputModeBtn");
    if (btn) btn.classList.toggle("on", codeInputOn);
  }
  const codeInputBtn = $("#codeInputModeBtn");
  if (codeInputBtn) {
    codeInputBtn.style.display = codeInputAvailable ? "" : "none";
    codeInputBtn.onclick = () => {
      codeInputOn = !codeInputOn;
      localStorage.setItem("code-input-mode-on", codeInputOn ? "1" : "0");
      refreshCodeInputUI();
    };
  }
  refreshCodeInputUI();

  /* ============ 注入常用 prompt（每个会话一条，存的是指向全局库的指针） ============ */
  // 与「注入当前日期」同款显示方式：开启后，在日期提示行的下一行显示本会话的常用 prompt
  // （灰色只读文本；多行时限定高度，点击可看全文）；发送时按「日期 → 常用prompt → 用户输入」
  // 的顺序拼进消息。语义与「注入敏感信息替换列表」一致：**一次性**，发送成功后自动关闭。
  // 后端返回的是 {ref, text}：ref 为指向全局库条目的指针，text 是**实时解析**出来的内容，
  // 所以在主界面改了那条常用 prompt 后，这里拿到的一定是新版本；条目被删除则 text 为空（未设置）。
  // 按钮显示与否由系统设置「开启常用prompt注入功能」控制；本会话尚未设置时按钮置灰不可点。
  const commonPromptEnabled = !(settings && settings.common_prompt_enabled === false);
  let commonPromptRef = "";
  let commonPromptText = "";
  let commonPromptOn = false;
  if (commonPromptEnabled) {
    try {
      const r = await api(`/api/agents/${encodeURIComponent(S.agentId)}/threads/${encodeURIComponent(S.threadId)}/common-prompt`);
      commonPromptRef = (r && r.ref) || "";
      commonPromptText = (r && r.text) || "";
    } catch (e) { commonPromptRef = ""; commonPromptText = ""; }
  }
  function refreshCommonPromptUI() {
    const btn = $("#commonPromptInjectBtn"), hint = $("#commonPromptHint"), editBtn = $("#editSessionPromptBtn");
    if (editBtn) editBtn.style.display = commonPromptEnabled ? "" : "none";
    if (btn) {
      btn.style.display = commonPromptEnabled ? "" : "none";
      btn.disabled = !commonPromptText;
      btn.classList.toggle("on", commonPromptOn && !!commonPromptText);
      btn.title = !commonPromptText
        ? t("当前会话还没有设置常用 prompt，请先点「编辑会话常用prompt」设置。")
        : commonPromptOn
          ? t("已开启：发送时会把当前会话的常用 prompt 拼接到消息中（日期行下方、你输入的内容上方；发送后自动关闭）")
          : t("点击开启：发送时把当前会话的常用 prompt 拼接到消息中（日期行下方、你输入的内容上方）");
    }
    if (hint) {
      if (commonPromptOn && commonPromptText) { hint.textContent = commonPromptText; hint.style.display = ""; }
      else { hint.textContent = ""; hint.style.display = "none"; }
    }
  }
  const commonPromptBtn = $("#commonPromptInjectBtn");
  if (commonPromptBtn) {
    commonPromptBtn.onclick = () => {
      if (!commonPromptText) { toast(t("当前会话还没有设置常用 prompt，请先点「编辑会话常用prompt」设置。")); return; }
      commonPromptOn = !commonPromptOn;
      refreshCommonPromptUI();
    };
  }
  const commonPromptHintEl = $("#commonPromptHint");
  if (commonPromptHintEl) {
    commonPromptHintEl.onclick = () => {
      if (!commonPromptText) return;
      showContentModal(`📝 ${t("会话常用prompt")}`,
        `<div class="muted" style="margin-bottom:8px;">${esc(t("用于当前会话：开启「注入常用prompt」后，发送消息时会把下方内容拼接到消息中（日期行下方、你输入的内容上方）。内容取自全局常用prompt库里本会话指向的那一条，改了会一起变。"))}</div>` +
        esc(commonPromptText).replace(/\n/g, "<br>"));
    };
  }
  function resetCommonPrompt() { commonPromptOn = false; refreshCommonPromptUI(); }
  refreshCommonPromptUI();

  /* ============ 浏览器接管：提示注入开关状态（供 send() 读取） ============ */
  // 「提示Agent开始接管浏览器」/「注入 敏感信息替换列表」= 开关式状态：
  // 开启后发送时把对应内容拼到用户消息之后（不写进 textarea）；发送成功后自动关闭。
  // 输入框里只显示一个灰色只读「注入块」，点击可在当前视窗弹窗查看全文。
  let takeoverHintOn = false;
  let takeoverPreviewText = "";
  let takeoverHintReset = null;
  let sensitiveInjectOn = false;
  let sensitiveInjectText = "";
  let sensitiveInjectReset = null;

  /* ================= 浏览器接管 ================= */
  (async () => {
    const actionsEl = $("#browserTakeoverActions");
    const openBtn = $("#openBrowserBtn");
    const hintBtn = $("#browserHintBtn");
    const sensitiveBtn = $("#sensitiveInjectBtn");
    const privacyBtn = $("#privacyMaskBtn");
    const sensitiveBlockEl = $("#sensitiveInjectBlock");
    const takeoverBlockEl = $("#takeoverInjectBlock");
    if (!actionsEl || !openBtn || !hintBtn || !sensitiveBtn || !privacyBtn || !sensitiveBlockEl || !takeoverBlockEl) return;

    let takeoverOn = false;
    try {
      const cfg = await api(`/api/agents/${encodeURIComponent(S.agentId)}`);
      takeoverOn = !!(cfg.main_agent && cfg.main_agent.browser_takeover);
    } catch (e) { return; }
    if (!takeoverOn) return;
    // 视图可能已被重新渲染，元素已脱离文档：放弃绑定
    if (!actionsEl.isConnected) return;

    actionsEl.style.display = "flex";

    let paused = false;
    let lastConnected = false;
    let domTimer = null;
    let domFetching = false;

    // 隐私遮蔽模式：全局设置持久化，绿灯表示开启；开关即时生效（后端调用时读取）。
    let privacyOn = !!(settings && settings.privacy_mask_mode);

    function updatePrivacyUI() {
      privacyBtn.classList.toggle("on", privacyOn);
      privacyBtn.title = privacyOn
        ? t("已开启：浏览器工具返回给 LLM 的内容与预览块都会把真实敏感值替换为 <名称>；点击关闭")
        : t("点击开启隐私遮蔽模式：在网页内容返回给 LLM 前，把敏感信息真实值替换为 <名称>");
    }

    async function setPrivacyMode(on) {
      const prev = privacyOn;
      privacyOn = on;
      updatePrivacyUI();
      try {
        const cur = settingsCache || await getSettings();
        await saveSettings({ ...cur, privacy_mask_mode: on });
      } catch (e) {
        privacyOn = prev;
        updatePrivacyUI();
        toast(e.message, true);
      }
    }

    const wrapDom = dom => `<当前聚焦标签页的可见内容>\n${dom}\n</当前聚焦标签页的可见内容>`;

    function updateHintState() {
      // 正在获取页面内容（已开启但预览为空）时：橙灯提示 + 发送按钮置灰不可点，
      // 避免用户在内容就绪前发送出不含页面内容的消息。
      const fetching = takeoverHintOn && !takeoverPreviewText;
      hintBtn.classList.toggle("on", takeoverHintOn && !fetching);
      hintBtn.classList.toggle("fetching", fetching);
      hintBtn.classList.toggle("is-disabled", paused && !takeoverHintOn);
      hintBtn.title = fetching
        ? t("正在获取当前页面内容…")
        : takeoverHintOn
          ? t("已开启：发送时会把当前聚焦标签页内容追加到消息之后（发送后自动关闭）")
          : "";
      if (takeoverHintOn) {
        takeoverBlockEl.textContent = takeoverPreviewText
          ? t("📋 当前页面内容 注入块")
          : t("正在获取当前页面内容…");
        takeoverBlockEl.style.display = "";
      } else {
        takeoverBlockEl.textContent = "";
        takeoverBlockEl.style.display = "none";
      }
      // 同步发送按钮可用状态（函数在 renderChatView 作用域内声明，可安全调用）
      try { updateSendState(); } catch (e) {}
    }

    // 敏感信息替换列表：开关式状态（绿灯），发送时附加到消息之后，发送成功后自动关闭。
    // 输入框里只显示一个灰色只读块，点击弹窗看完整 JSON。
    function updateSensitiveUI() {
      sensitiveBtn.classList.toggle("on", sensitiveInjectOn);
      sensitiveBtn.title = sensitiveInjectOn
        ? t("已开启：发送时会把敏感信息替换列表附加到消息之后（发送后自动关闭）")
        : t("点击开启：发送时把敏感信息替换列表附加到消息之后，供后台把 <名称> 替换为真实值");
      if (sensitiveInjectOn) {
        sensitiveBlockEl.textContent = t("📋 敏感信息替换列表 注入块");
        sensitiveBlockEl.style.display = "";
      } else {
        sensitiveBlockEl.textContent = "";
        sensitiveBlockEl.style.display = "none";
      }
    }

    // 供 send() 在发送成功后自动关闭开关
    sensitiveInjectReset = () => {
      sensitiveInjectOn = false;
      sensitiveInjectText = "";
      updateSensitiveUI();
    };

    async function fetchPreview(allowOpen) {
      if (domFetching) return null;
      domFetching = true;
      try {
        const r = await api("/api/browser/viewport-dom", {
          method: "POST",
          body: JSON.stringify({ agent_id: S.agentId, thread_id: S.threadId, allow_open: !!allowOpen }),
        });
        if (takeoverHintOn && r && r.ok && r.content) {
          takeoverPreviewText = wrapDom(r.content);
          updateHintState();
        }
        return r;
      } catch (e) {
        return null;
      } finally {
        domFetching = false;
      }
    }

    function stopDomPolling() {
      if (domTimer) { clearInterval(domTimer); domTimer = null; }
    }

    function startDomPolling() {
      stopDomPolling();
      // 每 1 秒刷新一次预览，保证内容在「点击发送前一秒」仍是最新的。
      // 轮询用 allow_open=false：浏览器未连接时不触发自动启动，仅沿用最后一次内容。
      domTimer = setInterval(() => {
        if (!actionsEl.isConnected || !takeoverHintOn) { stopDomPolling(); return; }
        // agent 运行期间不轮询，避免与图内浏览器工具并发争用 CDP 连接
        if (paused || !lastConnected || isRunning) return;
        fetchPreview(false);
      }, 1000);
    }

    // 供 send() 在发送成功后自动关闭开关
    takeoverHintReset = () => {
      takeoverHintOn = false;
      takeoverPreviewText = "";
      stopDomPolling();
      updateHintState();
    };

    function showInfoModal(text) {
      const mask = document.createElement("div");
      mask.className = "modal-mask";
      mask.innerHTML = `<div class="modal" data-modal-key="browserTextInfo"><div class="modal-body">${esc(text)}</div><div class="modal-actions"><button class="btn primary" id="tkInfoOk">${t("知道了")}</button></div></div>`;
      document.body.appendChild(mask);
      mask.querySelector("#tkInfoOk").onclick = () => mask.remove();
    }

    function showIntroPopup() {
      if (settings && settings.browser_takeover_intro_popup === false) return;
      const mask = document.createElement("div");
      mask.className = "modal-mask";
      mask.innerHTML = `<div class="modal" data-modal-key="takeoverIntro"><h3>🌐 ${t("浏览器接管说明")}</h3><div class="modal-body">${esc(t("已在桌面显示一个始终置顶的「停止」悬浮按钮（无论切到哪个应用或页面都能看到）。红色表示 Agent 可以操作浏览器；点击它变成绿色「继续」，此时 Agent 的所有浏览器操作会被立即暂停，等待你的后续指示。可拖动它旁边的 ✥ 把手移动位置，再次点击可恢复。"))}</div><div class="modal-actions"><button class="btn primary" id="tkIntroOk">${t("知道了")}</button></div></div>`;
      document.body.appendChild(mask);
      mask.querySelector("#tkIntroOk").onclick = () => mask.remove();
    }

    // 用户注入敏感信息替换列表后：若隐私遮蔽模式未开启，弹窗提示并解释。
    function showPrivacyPrompt() {
      const mask = document.createElement("div");
      mask.className = "modal-mask";
      mask.innerHTML = `<div class="modal" data-modal-key="privacyMask"><h3>🕶️ ${t("建议开启隐私遮蔽模式")}</h3><div class="modal-body" style="white-space:pre-wrap;">${esc(t("隐私遮蔽模式开启后，所有从浏览器返回给 LLM 的网页内容（DOM、diff 等）以及下方「提示接管」预览块，都会在交给 LLM 之前，把其中出现的、与敏感信息表单「真实内容」相同的文字全部替换为对应的 <名称>，从而完全不向 LLM 暴露这些真实信息。\n\n⚠️ 请慎重填写：不要加入过于常见的信息。例如表单里填了「年龄 → 17」，那么网页里独立的 17 都会被替换成 <年龄>（纯数字/纯英文会做边界判断以减少误伤，例如 2017 不会被替换，但 17 岁会被替换）。请尽量只填写足够独特的内容。"))}</div><div class="modal-actions"><button class="btn" id="pmLater">${t("暂不开启")}</button><button class="btn primary" id="pmOn">${t("开启隐私遮蔽模式")}</button></div></div>`;
      document.body.appendChild(mask);
      mask.querySelector("#pmLater").onclick = () => mask.remove();
      mask.querySelector("#pmOn").onclick = async () => {
        mask.remove();
        await setPrivacyMode(true);
        if (privacyOn) toast(t("隐私遮蔽模式已开启"));
      };
    }

    privacyBtn.onclick = () => { setPrivacyMode(!privacyOn); };

    function showFloating() {
      return api("/api/browser/floating", {
        method: "POST",
        body: JSON.stringify({ agent_id: S.agentId, thread_id: S.threadId, show: true }),
      }).catch(() => {});
    }

    async function refreshBrowserUI() {
      let st = { connected: false, port: null, paused: false };
      try { st = await api(`/api/browser/status?agent_id=${encodeURIComponent(S.agentId)}&thread_id=${encodeURIComponent(S.threadId)}`); } catch (e) {}
      const wasConnected = lastConnected;
      lastConnected = !!st.connected;
      paused = !!st.paused;
      if (st.connected) {
        openBtn.classList.add("on");
        openBtn.classList.add("is-disabled");
        openBtn.title = t("该会话的浏览器已打开（每个会话使用独立浏览器实例）");
      } else {
        openBtn.classList.remove("on");
        openBtn.classList.remove("is-disabled");
        openBtn.title = "";
      }
      updateHintState();
      return { connected: !!st.connected, wasConnected };
    }

    function buildSensitiveText(names) {
      const usage = t("请遇到如下需要填写的内容时，直接将下述列表中的名称原文加上<>包裹后填入。\n比如，若下述列表中有「姓名」，那么在遇到以「请输入你姓名」为提示的可输入元素时，直接填写为<姓名>即可。\nCDP 在后台实现时，会识别使用<>包裹的通义替换词，并将其替换为具体的真实值。");
      const jsonText = JSON.stringify({
        "使用方式": usage,
        "直接填名称原文列表": names,
      }, null, 2);
      return `${t("敏感信息替换列表：")}\n${jsonText}`;
    }

    sensitiveBtn.onclick = async () => {
      if (sensitiveInjectOn) { sensitiveInjectReset(); return; }
      let names = [];
      try {
        const st = await api("/api/settings");
        names = (st.sensitive_info || []).map(e => (e.name || "").trim()).filter(Boolean);
      } catch (e) {}
      if (!names.length) { toast(t("敏感信息表单为空，请先在主界面「编辑敏感信息表单」中添加"), true); return; }
      sensitiveInjectOn = true;
      sensitiveInjectText = buildSensitiveText(names);
      updateSensitiveUI();
      // 注入后提示用户开启隐私遮蔽模式（未开启时才弹）
      if (!privacyOn) showPrivacyPrompt();
    };

    // 灰色只读注入块：点击在当前视窗弹窗查看全文，避免在输入框里挤一个窄滚动块
    sensitiveBlockEl.onclick = () => {
      if (!sensitiveInjectText) return;
      showContentModal(t("敏感信息替换列表：") + t("（发送时会自动附加到消息）"), esc(sensitiveInjectText).replace(/\n/g, "<br>"));
    };
    takeoverBlockEl.onclick = () => {
      if (!takeoverPreviewText) return;
      showContentModal(t("提示Agent开始接管浏览器") + t("（发送时会自动附加到消息）"), esc(takeoverPreviewText).replace(/\n/g, "<br>"));
    };

    openBtn.onclick = async () => {
      if (openBtn.classList.contains("is-disabled")) return;
      try {
        const r = await api("/api/browser/open", { method: "POST", body: JSON.stringify({ agent_id: S.agentId, thread_id: S.threadId }) });
        if (!r.ok) { toast(r.error || t("打开浏览器失败"), true); return; }
        await refreshBrowserUI();
        await showFloating();
        showIntroPopup();
      } catch (e) { toast(e.message, true); }
    };

    hintBtn.onclick = async () => {
      if (takeoverHintOn) { takeoverHintReset(); return; }
      if (paused) { showInfoModal(t("请先点击「继续」按钮，再提示 Agent 接管浏览器。")); return; }
      takeoverHintOn = true;
      takeoverPreviewText = "";
      updateHintState();  // 先显示「正在获取…」
      try {
        // 首次抓取允许自动打开浏览器（沿用原「提示接管」行为）
        const r = await fetchPreview(true);
        const { connected, wasConnected } = await refreshBrowserUI();
        if (connected) await showFloating();
        if (connected && !wasConnected) showIntroPopup();  // 仅当本次提示自动打开了浏览器才弹
        if (!r || !r.ok || !takeoverPreviewText) {
          takeoverHintReset();
          toast((r && r.error) || t("获取当前页面内容失败"), true);
          return;
        }
        startDomPolling();
      } catch (e) { takeoverHintReset(); toast(e.message, true); }
    };

    updateHintState();
    updateSensitiveUI();
    updatePrivacyUI();
    await refreshBrowserUI();
    if (lastConnected) await showFloating();

    // 定时同步暂停状态：系统级悬浮按钮可独立翻转暂停，前端据此更新提示按钮置灰
    const pollId = setInterval(async () => {
      if (!actionsEl.isConnected) { clearInterval(pollId); stopDomPolling(); return; }
      await refreshBrowserUI();
    }, 2000);
  })();

  /* ================= 未发送消息缓存：恢复与保存 ================= */
  const draftInput = $("#msgInput");
  let draftTimer = null;
  if (draftInput) {
    draftInput.addEventListener("input", () => {
      clearTimeout(draftTimer);
      draftTimer = setTimeout(() => { draftTimer = null; saveDraft(S.agentId, draftInput.value); }, DRAFT_DEBOUNCE_MS);
    });
    // 退出会话 / 关闭浏览器 / 退出托盘程序时兜底落盘（同步 keepalive，不依赖防抖）
    draftFlushFn = () => {
      if (draftTimer) { clearTimeout(draftTimer); draftTimer = null; }
      saveDraftKeepalive(S.agentId, draftInput.value);
    };
    if (draftMemCache[S.agentId] !== undefined) {
      if (draftMemCache[S.agentId] && !draftInput.value) draftInput.value = draftMemCache[S.agentId];
    } else {
      try {
        const d = await api(`/api/drafts/${encodeURIComponent(S.agentId)}`);
        const txt = (d && d.text) || "";
        draftMemCache[S.agentId] = txt;
        if (txt && !draftInput.value) draftInput.value = txt;
      } catch (e) { /* 无缓存或读取失败，忽略 */ }
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
  $("#proactiveSummaryBtn").onclick = () => openProactiveSummary();

  /* ================= 编辑模式 ================= */
  let editModeOn = false;
  let editRawMessages = null;
  let editSubName = null;
  let edits = [];

  const editModeBtn = $("#editModeBtn");
  const editModeBadge = $("#editModeBadge");

  function setEditBadge(mode) {
    if (editModeBtn) editModeBtn.textContent = (mode === "off") ? `✏️ ${t("进入编辑模式")}` : `✏️ ${t("退出编辑模式")}`;
    if (!editModeBadge) return;
    if (mode === "off") {
      editModeBadge.style.display = "none";
    } else {
      editModeBadge.style.display = "";
      if (mode === "loading") {
        editModeBadge.textContent = `⏳ ${t("加载模型中")}`;
        editModeBadge.className = "edit-mode-badge loading";
      } else if (mode === "writing") {
        editModeBadge.textContent = `✏️ ${t("写入中")}`;
        editModeBadge.className = "edit-mode-badge writing";
      } else {
        editModeBadge.textContent = `✏️ ${t("编辑模式")}`;
        editModeBadge.className = "edit-mode-badge";
      }
    }
  }

  /* 持久化报错：右上角常驻，用户点叉号才消失（区别于 4 秒自动消失的 toast） */
  function showEditError(reason) {
    let wrap = $(".edit-error-wrap");
    if (!wrap) {
      wrap = document.createElement("div");
      wrap.className = "edit-error-wrap";
      document.body.appendChild(wrap);
    }
    wrap.dataset.thread = String(S.threadId);
    wrap.innerHTML = "";  // 同一会话重复失败只保留最新一条，避免堆叠
    const el = document.createElement("div");
    el.className = "edit-error-banner";
    const text = document.createElement("div");
    text.className = "edit-error-text";
    text.textContent = `${t("写入失败，失败原因：")}${reason}`;
    const btn = document.createElement("button");
    btn.className = "edit-error-close";
    btn.type = "button";
    btn.setAttribute("aria-label", t("关闭"));
    btn.textContent = "✕";
    btn.onclick = () => {
      el.remove();
      if (!wrap.children.length) wrap.remove();
    };
    el.appendChild(text);
    el.appendChild(btn);
    wrap.appendChild(el);
  }

  // 把字段原文切成「可编辑段」：默认按行（自然段），但代码围栏 / 展示公式 / 表格
  // 这类跨行的 markdown 结构必须保持在同一段里，否则每行单独 renderMd 会渲染不完整
  //（`\[...\]`、``` 代码块、`|` 表格被按行拆开）。不变量：segs.join("\n") 等于原文（\r\n 归一为 \n）。
  function splitEditableSegments(rawText) {
    const lines = String(rawText == null ? "" : rawText).replace(/\r\n?/g, "\n").split("\n");
    const segs = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      const t = line.replace(/^\s+/, "");
      const fence = /^(`{3,}|~{3,})/.exec(line);
      if (fence) {
        const ch = fence[1][0];
        const len = fence[1].length;
        const closeRe = new RegExp("^\\s*" + (ch === "`" ? "`" : "~") + "{" + len + ",}\\s*$");
        let j = i + 1;
        while (j < lines.length && !closeRe.test(lines[j])) j++;
        const end = j < lines.length ? j + 1 : lines.length;
        segs.push(lines.slice(i, end).join("\n"));
        i = end;
        continue;
      }
      // 展示公式 $$...$$ / \[...\]（可能跨多行）
      const isDollar = t.startsWith("$$");
      const isBracket = t.length >= 2 && t.charCodeAt(0) === 92 && t[1] === "[";
      if (isDollar || isBracket) {
        const inlineClosed = isDollar ? t.indexOf("$$", 2) !== -1 : t.indexOf("\\]", 2) !== -1;
        if (inlineClosed) { segs.push(line); i++; continue; }
        let j = i + 1;
        while (j < lines.length) {
          const lj = lines[j];
          const hasClose = isDollar ? lj.indexOf("$$") !== -1 : lj.replace(/\s+$/, "").endsWith("\\]");
          j++;
          if (hasClose) break;
        }
        segs.push(lines.slice(i, j).join("\n"));
        i = j;
        continue;
      }
      // 表格：连续以 | 开头的行
      if (/^\s*\|/.test(line)) {
        let j = i;
        while (j < lines.length && /^\s*\|/.test(lines[j])) j++;
        segs.push(lines.slice(i, j).join("\n"));
        i = j;
        continue;
      }
      segs.push(line);
      i++;
    }
    return segs;
  }

  function editableLinesHTML(rawText, field, blockIndex, toolCallIndex, msgIndice) {
    const segs = splitEditableSegments(rawText);
    return segs.map((seg, pi) => {
      const b64 = utf8ToB64(seg);
      const inner = seg ? renderMd(seg) : "&nbsp;";
      return `<div class="edit-line" data-msg="${msgIndice}" data-field="${field}" data-block="${blockIndex == null ? "" : blockIndex}" data-toolcall="${toolCallIndex == null ? "" : toolCallIndex}" data-para="${pi}" data-raw-b64="${b64}">${inner}</div>`;
    }).join("");
  }

  function aiTokStr(msg) {
    const um = msg.usage_metadata || {};
    const i = um.input_tokens != null ? um.input_tokens : "?";
    const o = um.output_tokens != null ? um.output_tokens : "?";
    const cache = (um.input_token_details && um.input_token_details.cache_read) || 0;
    let s = `↑${i} ↓${o}`;
    if (cache) s += `  💾cache:${cache}`;
    return s;
  }

  function renderEditableMessage(msg, settings) {
    const allTypes = !!settings.edit_all_message_types;
    const idx = msg.msg_indice;
    if (msg.type === "HumanMessage") {
      const raw = String(msg.content ?? "");
      const body = allTypes
        ? editableLinesHTML(raw, "human_content", null, null, idx)
        : esc(raw).replace(/\n/g, "<br>");
      return `<div class="user-msg-block" data-msg-indice="${idx}"><div class="user-msg-head">🧑 <strong>${t("用户")}</strong></div><blockquote class="user-msg-quote">${body}</blockquote></div>`;
    }
    if (msg.type === "AIMessage") {
      let html = `<div class="ai-msg-block" data-msg-indice="${idx}" style="border-left: 3px solid #4CAF50; padding-left: 12px;">`;
      html += `<strong>🤖 Assistant</strong>  <sub>${aiTokStr(msg)}</sub>`;
      const content = msg.content;
      if (Array.isArray(content)) {
        content.forEach((blk, bi) => {
          if (!blk) return;
          if (blk.type === "reasoning") {
            const open = settings.reasoning_expanded !== false ? " open" : "";
            const r = String(blk.reasoning ?? "");
            // 思考过程始终渲染（公式/加粗/标题）；是否可点击编辑仍由「所有类型可编辑」开关决定。
            const inner = allTypes
              ? editableLinesHTML(r, "reasoning", bi, null, idx)
              : renderMd(r);
            html += `<details class="reasoning-block"${open}><summary>🧠 ${t("思考过程")}</summary><div class="reasoning-body reasoning-md">${inner}</div></details>`;
          } else if (blk.type === "text") {
            html += editableLinesHTML(String(blk.text ?? ""), "text", bi, null, idx);
          }
        });
      } else if (typeof content === "string") {
        html += editableLinesHTML(content, "text", null, null, idx);
      }
      if (msg.tool_calls && msg.tool_calls.length) {
        msg.tool_calls.forEach((tc, ti) => {
          const open = settings.tool_call_expanded ? " open" : "";
          const argsJson = JSON.stringify(tc.args || {}, null, 2);
          const body = allTypes
            ? editableLinesHTML(argsJson, "tool_call_args", null, ti, idx)
            : renderMd("```json\n" + argsJson + "\n```");
          html += `<details${open}><summary>🔧 \`${esc(tc.name || "?")}\`</summary>${body}</details>`;
        });
      }
      html += `</div>`;
      return html;
    }
    if (msg.type === "ToolMessage") {
      const name = msg.name || "unknown_tool";
      let c = msg.content;
      if (Array.isArray(c)) c = c.filter(b => b && b.type === "text").map(b => b.text).join("\n\n");
      const raw = String(c ?? "");
      const lineCount = raw.split("\n").length;
      const open = settings.tool_result_expanded ? " open" : "";
      const body = allTypes
        ? editableLinesHTML(raw, "tool_content", null, null, idx)
        : `<div class="tool-result-body">${esc(raw).replace(/\r\n/g, "\n").replace(/\n/g, "<br>")}</div>`;
      return `<details${open} data-msg-indice="${idx}"><summary>✅ ${t("工具结果")}: \`${esc(name)}\` (${lineCount} ${t("行")})</summary>${body}</details>`;
    }
    return "";
  }

  async function fetchRawMessagesForEdit() {
    const base = `/api/agents/${encodeURIComponent(S.agentId)}/threads/${encodeURIComponent(S.threadId)}`;
    const isSub = !!sel.value;
    const url = isSub ? `${base}/subgraphs/${encodeURIComponent(sel.value)}/messages` : `${base}/messages`;
    const r = await api(url);
    editSubName = isSub ? sel.value : null;
    editRawMessages = r.messages || [];
  }

  function computeEditableMessages(settings) {
    const msgs = editRawMessages || [];
    const allTime = !!settings.edit_any_history;
    const allTypes = !!settings.edit_all_message_types;
    let editableIdx = new Set();
    if (allTime) {
      msgs.forEach((m, i) => editableIdx.add(i));
    } else {
      let h = -1;
      msgs.forEach((m, i) => { if (m.type === "HumanMessage") h = i; });
      for (let i = h + 1; i < msgs.length; i++) editableIdx.add(i);
    }
    const editable = new Set();
    msgs.forEach((m, i) => {
      if (!editableIdx.has(i)) return;
      if (m.type === "AIMessage") editable.add(i);
      else if (allTypes && (m.type === "HumanMessage" || m.type === "ToolMessage")) editable.add(i);
    });
    return editable;
  }

  // ---- 编辑模式滚动锚点：结构化文本锚点（message + 模块 + 标题 + 顶部行文本） ----
  // 进入/退出编辑模式会改变块内行高（编辑态把段落拆成 .edit-line、去掉 margin），
  // 只保留旧的像素 scrollTop 会让视口里的文字整体错位。这里不再用「去空白字符计数」
  // 做位置映射（公式 \[...\] 源文多行、渲染成一行时计数会错位），而是记录：
  //   视口顶部第一行文本 + 它归属的 message(data-msg-indice) / 模块(reason/text/tool_calls)
  //   / 标题(##)
  // 重渲染后在同一作用域内按文本搜索匹配该行，把它拉回原来的视口纵向位置。
  // 旧实现见 app/static/js/obsolete.js。

  function normAnchorText(s) {
    return String(s == null ? "" : s).replace(/\s+/g, "");
  }

  // 跳过「对锚点不可见」的文本：
  // ① KaTeX 隐藏的 MathML（position:absolute+clip，getBoundingClientRect 恒为 (0,0)）；
  // ② 折叠 <details> 里的内容——Chrome 对折叠内容仍会报告布局矩形（甚至超大），但它并不显示，
  //    绝不能当锚点/匹配目标；<summary> 本身仍可见，故不跳过（只跳过非 summary 的折叠内容）。
  function isAnchorHidden(node) {
    const pe = node.parentElement;
    if (!pe) return false;
    if (pe.closest(".katex-mathml")) return true;
    // 注入型 UI（导出按钮 / 记忆附着按钮 / 工具 content 按钮等）在编辑态会消失，不能当锚点。
    if (pe.closest(".export-html-row, .mem-attach-btn, .tool-content-btn, .mem-attach-panel, .tool-result-trunc")) return true;
    const closed = pe.closest("details:not([open])");
    if (closed) {
      const sum = pe.closest("summary");
      if (!(sum && sum.closest("details") === closed)) return true;
    }
    return false;
  }

  // 取某文字节点第一段有高度的行矩形。
  function firstLineRect(node) {
    const range = document.createRange();
    range.selectNodeContents(node);
    const rects = range.getClientRects();
    for (let i = 0; i < rects.length; i++) {
      if (rects[i].height >= 0.5) return rects[i];
    }
    return null;
  }

  // 取某文字节点里第 i 个字符的矩形（用于定位「可见行的起始字符」）。
  function charRectAt(node, i) {
    const len = node.data.length;
    if (len === 0) return null;
    const k = Math.min(Math.max(0, i), len - 1);
    const range = document.createRange();
    range.setStart(node, k);
    range.setEnd(node, Math.min(k + 1, len));
    const rects = range.getClientRects();
    return rects.length ? rects[0] : null;
  }

  // 二分找出「落在 top≈targetTop 那一行」的起始字符索引。
  // 关键：顶部可见行常常是某个长段落（单个文字节点、软换行折成多行）的中间一行，
  // 必须从这一行的起始字符开始截取；否则会把「整段开头」当成锚点、对齐到错误位置，
  // 进/退出编辑模式就会出现视口跳变（这正是本轮修复的 bug）。
  function lineStartOffset(node, targetTop) {
    const len = node.data.length;
    let lo = 0, hi = len;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      const r = charRectAt(node, mid);
      if (r && (r.top + r.height / 2) < targetTop) lo = mid + 1;
      else hi = mid;
    }
    return Math.max(0, Math.min(lo, len));
  }

  // 取 root 内 node 之后的下一个文字节点（文档顺序）。
  function nextTextNodeWithin(root, node) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    let n, hit = false;
    while ((n = walker.nextNode())) {
      if (hit) return n;
      if (n === node) hit = true;
    }
    return null;
  }

  // 找到「视口顶部第一条可见文字行」：扫描文字节点的行矩形，取 top 最小的可见行。
  function findTopVisibleLine(pane) {
    const pr = pane.getBoundingClientRect();
    const paneTop = pr.top, paneBottom = pr.bottom;
    const walker = document.createTreeWalker(pane, NodeFilter.SHOW_TEXT, null);
    let n, best = null, bestTop = Infinity;
    while ((n = walker.nextNode())) {
      if (!n.data || !/\S/.test(n.data)) continue;
      if (isAnchorHidden(n)) continue;
      const block = n.parentElement && n.parentElement.closest("[data-msg-indice]");
      if (!block) continue;
      const pe = n.parentElement;
      if (pe) {
        const er = pe.getBoundingClientRect();
        if (er.bottom <= paneTop + 0.5) continue;   // 该节点整体在视口上方
        if (er.top >= paneBottom) {                 // 该节点整体在视口下方
          if (best) break;                          // 已找到顶部可见行：其后的只会更靠下
          continue;
        }
      }
      const range = document.createRange();
      range.selectNodeContents(n);
      const rects = range.getClientRects();
      for (let i = 0; i < rects.length; i++) {
        const r = rects[i];
        if (r.height < 0.5) continue;
        if (r.bottom <= paneTop + 0.5) continue;
        if (r.top >= paneBottom) continue;
        if (r.top < bestTop) { bestTop = r.top; best = { node: n, rect: r, block }; }
      }
    }
    return best;
  }

  // 判断节点属于哪个模块（重渲染后在同一模块内搜索）。
  function moduleOf(node, block) {
    const pe = node.parentElement;
    if (pe && pe.closest(".reasoning-body")) return "reasoning";
    if (pe && pe.closest(".tool-result-wrap")) return "tool_result";
    if (block && block.matches && block.matches("details[data-msg-indice]")) return "tool_result";
    if (pe && pe.closest("details")) return "tool_call";
    if (block && block.classList && block.classList.contains("user-msg-block")) return "human";
    return "text";
  }

  function moduleSummaryOf(node) {
    const pe = node.parentElement;
    const d = pe && pe.closest("details");
    if (!d) return "";
    const s = d.querySelector("summary");
    return s ? normAnchorText(s.textContent) : "";
  }

  // 模块容器（用于定位标题作用域）。
  function moduleContainer(node, block, module) {
    const pe = node.parentElement;
    if (module === "reasoning") return pe ? pe.closest(".reasoning-body") : null;
    if (module === "tool_result") return (pe && pe.closest(".tool-result-wrap")) || block;
    if (module === "tool_call") return pe ? pe.closest("details") : null;
    if (module === "human") return (pe && pe.closest(".user-msg-quote")) || block;
    return block;
  }

  // 捕获「顶部那一行」的文本：从**可见行起始字符**开始向后累积，遇到新的一行即止。
  // 至少累积一小段，便于唯一匹配。命中公式时由调用方改用整个公式文本。
  function collectLineText(pane, startNode, startRect) {
    const block = startNode.parentElement && startNode.parentElement.closest("[data-msg-indice]");
    const lineH = startRect.height || 20;
    const startOff = lineStartOffset(startNode, startRect.top);
    let text = isAnchorHidden(startNode) ? "" : startNode.data.slice(startOff);
    let node = startNode;
    while (normAnchorText(text).length < 64) {
      node = nextTextNodeWithin(pane, node);
      if (!node) break;
      const nb = node.parentElement && node.parentElement.closest("[data-msg-indice]");
      if (!nb || nb !== block) break;   // 只在本消息块内累积，避免跨块拼出匹配不到的串
      const r = firstLineRect(node);
      if (r && r.top > startRect.top + lineH * 0.7 && normAnchorText(text).length >= 20) break;
      if (!isAnchorHidden(node)) text += node.data;
    }
    return normAnchorText(text);
  }

  // 捕获锚点：顶部行文本 + message + 模块 + 标题（+ 视口纵向位置）。
  function captureMsgAnchor() {
    const pane = $("#historyPane");
    if (!pane) return null;
    const pr = pane.getBoundingClientRect();
    if (pr.height < 8) return null;
    const found = findTopVisibleLine(pane);
    if (!found) {
      // 回退：消息块级
      const blocks = pane.querySelectorAll("[data-msg-indice]");
      for (const b of blocks) {
        const r = b.getBoundingClientRect();
        if (r.bottom > pr.top + 8) return { msgIndice: b.getAttribute("data-msg-indice"), beforeTop: r.top, offsetRatio: 0 };
      }
      return { scrollTop: pane.scrollTop };
    }
    const block = found.block;
    const module = moduleOf(found.node, block);
    const container = moduleContainer(found.node, block, module);

    // 标题作用域：容器内、位于该行之前、文本非空的最近一个标题（##）。
    let headingText = null;
    if (container) {
      const heads = container.querySelectorAll("h1, h2, h3, h4, h5, h6");
      let chosen = null;
      for (const h of heads) {
        if (module === "text" && (h.closest(".reasoning-body") || h.closest("details"))) continue;
        if (h.compareDocumentPosition(found.node) & Node.DOCUMENT_POSITION_FOLLOWING) chosen = h;
      }
      if (chosen) headingText = normAnchorText(chosen.textContent);
    }

    // 顶部行文本：命中公式时按整个公式捕获（`\[...\]` 源文多行、渲染成一行）。
    let lineText, beforeTop;
    const katexEl = found.node.parentElement && found.node.parentElement.closest(".katex");
    if (katexEl) {
      const html = katexEl.querySelector(".katex-html") || katexEl;
      lineText = normAnchorText(html.textContent || "");
      beforeTop = katexEl.getBoundingClientRect().top;
    } else {
      lineText = collectLineText(pane, found.node, found.rect);
      beforeTop = found.rect.top;
    }

    const blockRect = block.getBoundingClientRect();
    return {
      msgIndice: block.getAttribute("data-msg-indice"),
      module,
      headingText,
      lineText,
      beforeTop,
      containerSummary: (module === "tool_call" || module === "tool_result") ? moduleSummaryOf(found.node) : "",
      offsetRatio: (beforeTop - blockRect.top) / (blockRect.height || 1),
    };
  }

  // 在 root 内按「忽略所有空白」查找 needle，返回所有出现处的 Range。
  function findRangesByText(root, needle) {
    const nNeedle = normAnchorText(needle);
    const out = [];
    if (!nNeedle) return out;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    const nodes = [];
    let full = "", n;
    while ((n = walker.nextNode())) {
      if (isAnchorHidden(n)) continue;
      nodes.push({ node: n, start: full.length });
      full += n.data;
    }
    let norm = "";
    const map = [];
    for (let i = 0; i < full.length; i++) {
      if (/\s/.test(full[i])) continue;
      norm += full[i];
      map.push(i);
    }
    const locate = (idx) => {
      for (let k = nodes.length - 1; k >= 0; k--) {
        if (idx >= nodes[k].start) return { node: nodes[k].node, offset: Math.min(idx - nodes[k].start, nodes[k].node.data.length) };
      }
      return nodes.length ? { node: nodes[0].node, offset: 0 } : null;
    };
    let from = 0;
    while (true) {
      const j = norm.indexOf(nNeedle, from);
      if (j < 0) break;
      const s = locate(map[j]);
      const e = locate(map[j + nNeedle.length - 1] + 1);
      if (s && e) {
        const r = document.createRange();
        r.setStart(s.node, s.offset);
        r.setEnd(e.node, e.offset);
        out.push(r);
      }
      from = j + 1;
    }
    return out;
  }

  // 匹配是否落在锚点所记录的模块作用域内。
  function matchesModule(node, block, anchor) {
    const pe = node.parentElement;
    if (!pe) return false;
    if (anchor.module === "reasoning") return !!pe.closest(".reasoning-body");
    if (anchor.module === "tool_result") return !!pe.closest(".tool-result-wrap") || !block.querySelector(".tool-result-wrap");
    if (anchor.module === "tool_call") {
      const d = pe.closest("details");
      if (!d || d === block) return false;
      if (!anchor.containerSummary) return true;
      const s = d.querySelector("summary");
      return !!(s && normAnchorText(s.textContent) === anchor.containerSummary);
    }
    if (anchor.module === "human") return !!pe.closest(".user-msg-quote");
    // text：排除思考块与工具 details
    return !pe.closest(".reasoning-body") && !pe.closest("details");
  }

  // 匹配是否位于锚点所记录的标题之后。
  function afterAnchorHeading(block, range, anchor) {
    if (!anchor.headingText) return true;
    const heads = block.querySelectorAll("h1, h2, h3, h4, h5, h6");
    for (const h of heads) {
      if (normAnchorText(h.textContent) !== anchor.headingText) continue;
      if (h.compareDocumentPosition(range.startContainer) & Node.DOCUMENT_POSITION_FOLLOWING) return true;
    }
    return false;
  }

  // 把「锚点行上方、但仍探入视口」的可见文字行推回视口之上，
  // 保证锚点行是视口显示的第一行（上方块间距/行高变化时的残余修正）。
  function pushLinesAbove(pane, anchorTopViewport) {
    const pr = pane.getBoundingClientRect();
    const walker = document.createTreeWalker(pane, NodeFilter.SHOW_TEXT, null);
    let n, maxBottom = -Infinity;
    while ((n = walker.nextNode())) {
      if (!n.data || !/\S/.test(n.data)) continue;
      if (isAnchorHidden(n)) continue;
      if (!n.parentElement || !n.parentElement.closest("[data-msg-indice]")) continue;
      const pe = n.parentElement;
      const er = pe.getBoundingClientRect();
      if (er.bottom <= pr.top + 0.5) continue;
      if (er.top >= pr.bottom) continue;
      const range = document.createRange();
      range.selectNodeContents(n);
      const rects = range.getClientRects();
      for (let i = 0; i < rects.length; i++) {
        const r = rects[i];
        if (r.height < 0.5) continue;
        if (r.bottom <= pr.top + 0.5) continue;
        if (r.top >= pr.bottom) continue;
        if (r.top >= anchorTopViewport - 0.5) continue;   // 不是锚点行上方的行
        if (r.bottom > maxBottom) maxBottom = r.bottom;
      }
    }
    if (maxBottom > pr.top + 0.5) pane.scrollTop += (maxBottom - pr.top);
  }

  // 回滚锚点：在同一 message / 模块 / 标题作用域内找到该行文本，拉回原视口位置。
  function restoreMsgAnchor(anchor) {
    if (!anchor) return;
    const pane = $("#historyPane");
    if (!pane) return;
    if (anchor.msgIndice == null) {
      if (anchor.scrollTop != null) pane.scrollTop = anchor.scrollTop;
      return;
    }
    const block = pane.querySelector(`[data-msg-indice="${anchor.msgIndice}"]`);
    if (!block) {
      if (anchor.scrollTop != null) pane.scrollTop = anchor.scrollTop;
      return;
    }

    let target = null;
    if (anchor.lineText) {
      const cands = findRangesByText(block, anchor.lineText);
      let scoped = cands.filter(r => r.startContainer && matchesModule(r.startContainer, block, anchor) && afterAnchorHeading(block, r, anchor));
      if (!scoped.length) scoped = cands.filter(r => r.startContainer && matchesModule(r.startContainer, block, anchor));
      if (!scoped.length) scoped = cands;
      if (scoped.length) {
        const blockRect = block.getBoundingClientRect();
        const wantTop = blockRect.top + (anchor.offsetRatio || 0) * (blockRect.height || 1);
        let bestDiff = Infinity;
        for (const r of scoped) {
          const rr = r.getBoundingClientRect();
          if (!rr || !rr.height) continue;
          const diff = Math.abs(rr.top - wantTop);
          if (diff < bestDiff) { bestDiff = diff; target = rr; }
        }
      }
    }
    if (!target) {
      const blockRect = block.getBoundingClientRect();
      target = { top: blockRect.top + (anchor.offsetRatio || 0) * (blockRect.height || 1) };
    }
    pane.scrollTop += (target.top - anchor.beforeTop);
    pushLinesAbove(pane, anchor.beforeTop);
  }

  function renderEditMode() {
    const anchor = captureMsgAnchor();
    const settings = settingsCache || {};
    const editable = computeEditableMessages(settings);
    (editRawMessages || []).forEach((m, i) => {
      if (!editable.has(i)) return;
      const el = historyEl.querySelector(`[data-msg-indice="${i}"]`);
      if (el) el.outerHTML = renderEditableMessage(m, settings);
    });
    restoreMsgAnchor(anchor);
  }

  function applyLineEdit(line, newText) {
    const oldRaw = b64ToUtf8(line.dataset.rawB64 || "");
    if (newText === oldRaw) return;
    line.innerHTML = newText ? renderMd(newText) : "&nbsp;";
    line.dataset.rawB64 = utf8ToB64(newText);
    const msgIndice = +line.dataset.msg;
    const field = line.dataset.field;
    const blockIndex = line.dataset.block === "" ? null : +line.dataset.block;
    const toolCallIndex = line.dataset.toolcall === "" ? null : +line.dataset.toolcall;
    const paraIndex = +line.dataset.para;
    const existing = edits.find(e =>
      e.msgIndice === msgIndice && e.field === field &&
      e.blockIndex === blockIndex && e.toolCallIndex === toolCallIndex &&
      e.paraIndex === paraIndex);
    if (existing) existing.newText = newText;
    else edits.push({ msgIndice, field, blockIndex, toolCallIndex, paraIndex, newText });
  }

  function closeEditPopup() {
    $$(".edit-popup").forEach(p => {
      rememberEditPopupSize(p.offsetWidth, p.offsetHeight);
      p.remove();
    });
  }

  // 编辑框尺寸全局记忆（本机偏好，localStorage 会被 ui_prefs_bridge 落盘到后端，
  // 换渲染外壳 / 清缓存都不丢）。只存宽高，位置每次按被点行重新定位。
  const EDIT_POPUP_SIZE_KEY = "edit-popup-size";
  function readEditPopupSize() {
    try {
      const v = JSON.parse(localStorage.getItem(EDIT_POPUP_SIZE_KEY) || "null");
      if (v && v.w > 0 && v.h > 0) return v;
    } catch (e) { /* ignore */ }
    return null;
  }
  function rememberEditPopupSize(w, h) {
    if (!(w > 0 && h > 0)) return;
    try { localStorage.setItem(EDIT_POPUP_SIZE_KEY, JSON.stringify({ w: Math.round(w), h: Math.round(h) })); } catch (e) { /* ignore */ }
  }

  // 编辑框挂在 #historyPane（滚动容器）内的滚动内容坐标系里，紧贴被编辑行、随内容滚动。
  // rect 是行的视口坐标；换算成「相对滚动内容」的坐标（加上 scrollLeft/scrollTop）。
  function positionEditPopup(popup, rect) {
    const pane = $("#historyPane");
    if (!pane) return;
    const pr = pane.getBoundingClientRect();
    const pad = 6;
    const pw = popup.offsetWidth, ph = popup.offsetHeight;
    let left = (rect.left - pr.left) + pane.scrollLeft;
    let top = (rect.bottom - pr.top) + pane.scrollTop + pad;
    // 下方放不下就翻到被编辑行的上方
    if (rect.bottom + pad + ph > pr.bottom) {
      top = (rect.top - pr.top) + pane.scrollTop - ph - pad;
    }
    // 水平方向不越出可视区，避免撑出横向滚动条
    const minLeft = pane.scrollLeft + 8;
    const maxLeft = pane.scrollLeft + pane.clientWidth - pw - 8;
    if (left > maxLeft) left = Math.max(minLeft, maxLeft);
    if (left < minLeft) left = minLeft;
    popup.style.left = Math.round(left) + "px";
    popup.style.top = Math.round(top) + "px";
  }

  function openEditPopup(line) {
    closeEditPopup();
    const raw = b64ToUtf8(line.dataset.rawB64 || "");
    const rect = line.getBoundingClientRect();
    const popup = document.createElement("div");
    popup.className = "edit-popup";
    popup.innerHTML = `
      <div class="edit-popup-toolbar">
        <button class="btn small primary" data-act="apply">${t("确认改动")}</button>
        <button class="btn small" data-act="cancel">${t("撤销改动")}</button>
      </div>
      <textarea></textarea>`;
    const ta = popup.querySelector("textarea");
    ta.value = raw;
    const pane = $("#historyPane") || document.body;
    pane.appendChild(popup);
    const stored = readEditPopupSize();
    if (stored) {
      // 记忆尺寸：宽度按聊天区宽度夹取（避免撑出横向滚动条），高度按视口夹取
      const availW = (pane.clientWidth || window.innerWidth) - 16;
      popup.style.width = Math.min(stored.w, Math.max(240, availW)) + "px";
      popup.style.height = Math.min(stored.h, window.innerHeight - 16) + "px";
    } else {
      // 首次：动态宽度 = 一行文本（正文渲染区）宽度的 0.75 倍
      const lineWidth = (historyEl && historyEl.clientWidth) || (window.innerWidth - 44);
      popup.style.width = Math.max(280, Math.round(lineWidth * 0.75)) + "px";
    }
    positionEditPopup(popup, rect);
    popup.querySelector('[data-act="apply"]').onclick = () => { applyLineEdit(line, ta.value); closeEditPopup(); };
    popup.querySelector('[data-act="cancel"]').onclick = () => closeEditPopup();
    ta.focus();
  }

  function bindEditLineEvents() {
    historyEl.addEventListener("mouseover", (e) => {
      if (!editModeOn) return;
      const line = e.target.closest(".edit-line");
      const prev = historyEl.querySelector(".edit-line.hover");
      if (prev && prev !== line) prev.classList.remove("hover");
      if (!line) return;
      if (line.closest("details:not([open])")) return;
      line.classList.add("hover");
    });
    historyEl.addEventListener("mouseleave", () => {
      historyEl.querySelectorAll(".edit-line.hover").forEach(el => el.classList.remove("hover"));
    });
    historyEl.addEventListener("click", (e) => {
      if (!editModeOn) return;
      const line = e.target.closest(".edit-line");
      if (!line) return;
      if (line.closest("details:not([open])")) return;
      e.preventDefault();
      openEditPopup(line);
    });
  }

  function applyParaEditsToText(rawText, paraEdits) {
    const segs = splitEditableSegments(rawText);
    paraEdits.forEach(pe => { segs[pe.paraIndex] = pe.newText; });
    return segs.join("\n");
  }

  function buildSubstituteMessage(original, itemEdits) {
    const clone = JSON.parse(JSON.stringify(original));
    const byField = {};
    itemEdits.forEach(e => {
      const fk = `${e.field}|${e.blockIndex}|${e.toolCallIndex}`;
      (byField[fk] = byField[fk] || []).push(e);
    });
    for (const fk of Object.keys(byField)) {
      const parts = fk.split("|");
      const field = parts[0];
      const bi = parts[1] === "" ? null : parts[1];
      const ti = parts[2] === "" ? null : parts[2];
      const paraEdits = byField[fk];
      if (field === "text" || field === "reasoning") {
        const blocks = Array.isArray(clone.content) ? clone.content : null;
        if (bi != null && blocks && blocks[bi]) {
          blocks[bi][field] = applyParaEditsToText(blocks[bi][field], paraEdits);
        } else if (field === "text" && !blocks && typeof clone.content === "string") {
          clone.content = applyParaEditsToText(clone.content, paraEdits);
        }
      } else if (field === "tool_call_args") {
        const tc = clone.tool_calls && clone.tool_calls[ti];
        if (tc) {
          const jsonStr = JSON.stringify(tc.args || {}, null, 2);
          const newJson = applyParaEditsToText(jsonStr, paraEdits);
          try { tc.args = JSON.parse(newJson); } catch (err) { /* 无效 JSON：保留原值 */ }
        }
      } else if (field === "tool_content" || field === "human_content") {
        let raw = clone.content;
        if (Array.isArray(raw)) raw = raw.filter(b => b && b.type === "text").map(b => b.text).join("\n\n");
        clone.content = applyParaEditsToText(String(raw), paraEdits);
      }
    }
    return clone;
  }

  /* 编辑写入阶段超时（不含「加载模型」阶段；加载阶段由状态徽标单独提示，不设超时）。 */
  const EDIT_WRITE_TIMEOUT_MS = 30000;

  function submitEditRequest(req, onStage) {
    return new Promise((resolve) => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const s = new WebSocket(`${proto}://${location.host}/api/agents/${encodeURIComponent(S.agentId)}/threads/${encodeURIComponent(S.threadId)}/chat`);
      let settled = false;
      let writeTimer = null;
      let editSent = false;
      const finish = (res) => {
        if (settled) return;
        settled = true;
        if (writeTimer) clearTimeout(writeTimer);
        try { s.close(); } catch (e) {}
        resolve(res);
      };
      // 超时只覆盖「写入」阶段：等后端发出 status:ready（运行时/模型就绪）后才开始计时。
      const startWriteTimer = () => {
        if (writeTimer) clearTimeout(writeTimer);
        writeTimer = setTimeout(
          () => finish({ ok: false, reason: t("写入超时（超过 30 秒未完成），请重试。") }),
          EDIT_WRITE_TIMEOUT_MS,
        );
      };
      s.onopen = () => s.send(JSON.stringify({ type: "attach", run_id: "", since: 0 }));
      s.onmessage = (ev) => {
        let m;
        try { m = JSON.parse(ev.data); } catch (e) { return; }
        // 编辑请求也走会话中枢：先 attach（拿到当前运行快照/回放），
        // replay_done 后再发 edit；若当前有运行，后端会返回「上一轮仍在运行」。
        if (m.type === "replay_done") {
          if (!editSent) {
            editSent = true;
            try { s.send(JSON.stringify({ type: "edit", request: req })); }
            catch (e) { finish({ ok: false, reason: t("连接已断开，写入未完成。") }); }
          }
          return;
        }
        if (m.type === "run_state" || m.type === "text" || m.type === "reasoning"
          || m.type === "phase" || m.type === "tool_call" || m.type === "tool_result"
          || m.type === "subgraph_start" || m.type === "subgraph_end"
          || m.type === "sub_agents" || m.type === "stopping" || m.type === "backfill") {
          return;
        }
        if (m.type === "status" && editSent && m.status === "loading") {
          if (onStage) onStage("loading");
        } else if (m.type === "status" && editSent && m.status === "ready") {
          if (onStage) onStage("writing");
          startWriteTimer();
        } else if (m.type === "done") {
          finish({ ok: true });
        } else if (m.type === "error") {
          finish({ ok: false, reason: m.message || t("未知错误") });
        }
      };
      s.onerror = () => finish({ ok: false, reason: t("无法连接到服务端，请确认程序仍在运行后重试。") });
      s.onclose = () => finish({ ok: false, reason: t("连接已断开，写入未完成。") });
    });
  }

  async function submitEdits() {
    const groups = {};
    edits.forEach(e => { (groups[e.msgIndice] = groups[e.msgIndice] || []).push(e); });
    const groupKeys = Object.keys(groups);
    for (let i = 0; i < groupKeys.length; i++) {
      const msgIndice = +groupKeys[i];
      const original = (editRawMessages || [])[msgIndice];
      if (!original) continue;
      const substitute = buildSubstituteMessage(original, groups[groupKeys[i]]);
      const isSub = !!editSubName;
      const res = await submitEditRequest({
        request_for_subagent: isSub,
        subagent_name: isSub ? editSubName : null,
        msg_indice: msgIndice,
        substitute_msg: substitute,
      }, (stage) => setEditBadge(stage));
      if (!res.ok) {
        const remaining = groupKeys.length - i - 1;
        let reason = res.reason || t("未知错误");
        if (remaining > 0) reason += t("（已停止提交剩余 {n} 处改动）").replace("{n}", remaining);
        return { ok: false, reason };
      }
    }
    return { ok: true };
  }

  async function refreshHistory() {
    const anchor = captureMsgAnchor();
    try {
      const base = `/api/agents/${encodeURIComponent(S.agentId)}/threads/${encodeURIComponent(S.threadId)}`;
      const h = sel.value
        ? await api(`${base}/subgraphs/${encodeURIComponent(sel.value)}/history`)
        : await api(`${base}/history`);
      historyEl.innerHTML = renderMd(h.markdown);
      injectExportButtons(historyEl);
      restoreMsgAnchor(anchor);
    } catch (e) {
      historyEl.innerHTML = `<div class="muted">${t("（无历史）")}</div>`;
    }
  }

  function leaveEditMode() {
    closeEditPopup();
    editModeOn = false;
    edits = [];
    editRawMessages = null;
    editSubName = null;
    setEditBadge("off");
  }

  async function enterEditMode() {
    editModeOn = true;
    edits = [];
    setEditBadge("edit");
    try {
      await fetchRawMessagesForEdit();
      renderEditMode();
    } catch (e) {
      toast(e.message, true);
      leaveEditMode();
    }
  }

  async function exitEditMode() {
    let shouldSubmit = false;
    if (edits.length) {
      const ans = await askConfirm(t("是否提交对历史消息的更改"));
      if (ans === "yes") shouldSubmit = true;
    }
    if (shouldSubmit) {
      const res = await submitEdits();
      if (res.ok) showAppliedBubble();
      else showEditError(res.reason);
    }
    leaveEditMode();
    await refreshHistory();
  }

  async function leaveChatView() {
    if (editModeOn) {
      const ans = await askConfirm(t("当前处于编辑模式，请先退出编辑模式后再离开。是否退出编辑模式？"));
      if (ans !== "yes") return;
      await exitEditMode();
    }
    flushCurrentDraft();
    draftFlushFn = null;
    goThreads(S.agentId, S.agentName);
  }

  async function refreshEditModeData() {
    try {
      await fetchRawMessagesForEdit();
      renderEditMode();
    } catch (e) { toast(e.message, true); }
  }

  function showAppliedBubble() {
    const el = document.createElement("div");
    el.className = "edit-applied-bubble";
    el.textContent = t("更改已生效");
    document.body.appendChild(el);
    setTimeout(() => particleDissolve(el, () => el.remove()), 1300);
  }

  function roundRectPath(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  function particleDissolve(el, done) {
    const rect = el.getBoundingClientRect();
    const w = Math.max(1, Math.round(rect.width));
    const h = Math.max(1, Math.round(rect.height));
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    canvas.style.cssText = `position:fixed;left:${rect.left}px;top:${rect.top}px;width:${w}px;height:${h}px;pointer-events:none;z-index:1101;`;
    const ctx = canvas.getContext("2d");
    document.body.appendChild(canvas);

    ctx.fillStyle = "#16a34a";
    roundRectPath(ctx, 0, 0, w, h, 12);
    ctx.fill();
    ctx.fillStyle = "#fff";
    ctx.font = "700 16px -apple-system, 'Segoe UI', sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(el.textContent, w / 2, h / 2);

    const data = ctx.getImageData(0, 0, w, h).data;
    const particles = [];
    const step = 2;
    for (let y = 0; y < h; y += step) {
      for (let x = 0; x < w; x += step) {
        const a = data[(y * w + x) * 4 + 3];
        if (a > 128) {
          particles.push({
            x, y,
            color: `rgb(${data[(y * w + x) * 4]},${data[(y * w + x) * 4 + 1]},${data[(y * w + x) * 4 + 2]})`,
            vx: -Math.random() * 2 - 1.5,
            vy: (Math.random() - 0.5) * 1.5,
            life: 1,
            decay: 0.01 + Math.random() * 0.03,
          });
        }
      }
    }
    el.style.opacity = "0";

    const tick = () => {
      ctx.clearRect(0, 0, w, h);
      let alive = 0;
      for (const p of particles) {
        p.x += p.vx;
        p.y += p.vy;
        p.life -= p.decay;
        if (p.life <= 0) continue;
        alive++;
        ctx.globalAlpha = Math.max(0, p.life);
        ctx.fillStyle = p.color;
        ctx.fillRect(p.x, p.y, step, step);
      }
      ctx.globalAlpha = 1;
      if (alive > 0) requestAnimationFrame(tick);
      else { canvas.remove(); done(); }
    };
    requestAnimationFrame(tick);
  }

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
        customPos = ratioFromPos({ left: pinBtn.offsetLeft, top: pinBtn.offsetTop });
        localStorage.setItem("pin-pos", JSON.stringify(customPos));
      }
      dragState = null;
    }
  });

  const historyEl = $("#historyMd");
  // 本会话的持久流：切出视图时不销毁，回来时复用同一份 DOM / 缓冲。
  const stream = getSessionStream(S.agentId, S.agentName, S.threadId);
  activeSessionStream = stream;
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
    if (stream.baseHtml != null) {
      // 同一页面内切走再回来：直接用离开时的静态基线 + 仍在持有的流式 DOM，
      // 避免把当前这一轮的流式内容再从 /history 渲染一遍。
      historyEl.innerHTML = stream.baseHtml;
      injectExportButtons(historyEl);
      if (stream.replyEl) historyEl.appendChild(stream.replyEl);
      stream.renderStream();
    } else {
      const h = await api(`/api/agents/${encodeURIComponent(S.agentId)}/threads/${encodeURIComponent(S.threadId)}/history`);
      historyEl.innerHTML = renderMd(h.markdown);
      injectExportButtons(historyEl);
      scrollToLastUserMsg();
    }
  } catch (e) { historyEl.innerHTML = `<div class="muted">${t("（无历史）")}</div>`; }

  const sel = $("#subgraphSel");
  try {
    const subs = await api(`/api/agents/${encodeURIComponent(S.agentId)}/threads/${encodeURIComponent(S.threadId)}/subgraphs`);
    subs.forEach(s => { const o = document.createElement("option"); o.value = s.node_name; o.textContent = s.node_name; sel.appendChild(o); });
  } catch (e) { /* 无子图 */ }

  const input = $("#msgInput"), sendBtn = $("#sendBtn"), stopBtn = $("#stopBtn");

  editModeBtn.onclick = () => { if (editModeOn) exitEditMode(); else enterEditMode(); };
  bindEditLineEvents();
  const memoryBtn = $("#memoryStoreBtn");
  if (memoryBtn) memoryBtn.onclick = () => openMemoryStoreModal();
  const sessionPromptBtn = $("#editSessionPromptBtn");
  if (sessionPromptBtn) {
    sessionPromptBtn.onclick = () => openSessionPromptModal(commonPromptRef, (ref, text) => {
      commonPromptRef = ref || "";
      commonPromptText = text || "";
      if (!commonPromptText) commonPromptOn = false;   // 清空/指向的条目被删：回到「未设置」，按钮变灰不可点
      refreshCommonPromptUI();
    });
  }
  // 「将本地md转换为HTML」按钮（位于「编辑会话常用prompt」左侧）；显隐已在上面按设置处理。
  const localMdBtn = $("#localMdConvertBtn");
  if (localMdBtn) localMdBtn.onclick = () => convertLocalMd(localMdBtn);
  const delAllMsgBtn = $("#delAllMsgBtn");
  if (delAllMsgBtn) delAllMsgBtn.onclick = () => triggerCleanAll();

  function updateSendState() {
    // 浏览器接管提示正在获取页面内容时，禁止发送（否则消息会缺少页面内容）。
    const takeoverFetching = takeoverHintOn && !takeoverPreviewText;
    sendBtn.disabled = isRunning || !!sel.value || takeoverFetching;
  }

  sel.onchange = async () => {
    updateSendState();
    if (!sel.value) { closeEditPopup(); renderChatView(); return; }
    try {
      const h = await api(`/api/agents/${encodeURIComponent(S.agentId)}/threads/${encodeURIComponent(S.threadId)}/subgraphs/${encodeURIComponent(sel.value)}/history`);
      $("#historyMd").innerHTML = renderMd(h.markdown);
      injectExportButtons($("#historyMd"));
      if (editModeOn) { edits = []; await refreshEditModeData(); }
    } catch (e) { toast(e.message, true); }
  };

  function setRunning(r, status = "thinking", actor = null) {
    isRunning = r;
    sel.disabled = r;
    updateSendState();
    if (r) setStatusIndicator(status, actor || t("主 agent")); else setStatusIndicator("idle");
    // 停止按钮：运行时显示；每次进入/退出都复位「停止」文案与可点状态，
    // 避免上一轮「停止中」的灰态残留到下一轮。
    stopBtn.style.display = r ? "" : "none";
    stopBtn.disabled = false;
    stopBtn.textContent = t("停止");
    stopBtn.classList.remove("stopping");
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

  // 本轮流程失败/被中断时，暂存「原发送信息」，供气泡上的「回填」按钮使用
  let pendingFailedMsg = null;
  function showFailBubble(msg) {
    const b = $("#failBubble");
    if (!b) return;
    pendingFailedMsg = msg || "";
    const pane = $("#inputPane");
    b.style.bottom = (pane ? pane.offsetHeight + 10 : 160) + "px";
    b.style.display = "flex";
  }
  function hideFailBubble() {
    const b = $("#failBubble");
    if (b) b.style.display = "none";
    pendingFailedMsg = null;
  }

  // 编辑模式中有未保存改动时点「发送」：不发送，仅在发送按钮上方提示。
  let editSendWarnTimer = null;
  function showEditSendWarn() {
    const b = $("#editSendWarnBubble");
    if (!b) return;
    b.textContent = t("编辑模式中有未保存的修改，请先退出编辑模式并提交或撤销改动后再发送。");
    b.style.display = "block";
    clearTimeout(editSendWarnTimer);
    editSendWarnTimer = setTimeout(() => { b.style.display = "none"; }, 4000);
  }

  let sendBusy = false;
  async function send() {
    const raw = input.value;
    if (sendBusy || isRunning) return;
    // 浏览器接管提示已开启但页面内容仍在获取：暂不允许发送（按钮此时也是置灰的，
    // 这里兜底 Enter 键路径），避免发出不含页面内容的消息让 LLM 摸不着头脑。
    if (takeoverHintOn && !takeoverPreviewText) { toast(t("正在获取当前页面内容，请稍候…")); return; }
    const userText = raw.replace(/\s+$/, "");
    const injectDate = dateInjectAvailable && dateInjectOn;
    const injectTime = timeInjectAvailable && timeInjectOn;
    const injectCommon = commonPromptEnabled && commonPromptOn && !!commonPromptText;
    const injectTakeover = takeoverHintOn && !!takeoverPreviewText;
    const injectSensitive = sensitiveInjectOn && !!sensitiveInjectText;
    // 开启日期/时间注入 / 常用 prompt 注入 / 浏览器接管提示 / 敏感信息注入后，允许「空输入」发送
    if (!userText && !injectDate && !injectTime && !injectCommon && !injectTakeover && !injectSensitive) return;
    // 编辑模式：有未保存改动则提示、不发送；无改动则先自动退出编辑模式（与「退出编辑模式」
    // 按钮同一套逻辑），再走正常发送流程，避免在编辑态 DOM 上发送导致流式回复不显示。
    if (editModeOn) {
      if (edits.length) { showEditSendWarn(); return; }
      sendBusy = true;
      try { await exitEditMode(); }
      catch (e) { sendBusy = false; toast(e.message, true); return; }
      finally { sendBusy = false; }
    }
    // 前缀按「日期 → 时间 → 常用prompt → 用户输入」拼接，与输入框上方灰色提示从上到下的顺序一致
    const parts = [];
    if (injectDate) parts.push(datePromptText());
    if (injectTime) parts.push(timePromptText());
    if (injectCommon) parts.push(commonPromptText);
    if (userText) parts.push(userText);
    let content = parts.join("\n");
    // 敏感信息替换列表：追加到用户消息之后（发送成功后自动关闭）
    if (injectSensitive) content = content ? content + "\n" + sensitiveInjectText : sensitiveInjectText;
    // 浏览器接管提示：把当前聚焦标签页内容（已包裹）追加到用户消息之后
    if (injectTakeover) content = content ? content + "\n" + takeoverPreviewText : takeoverPreviewText;
    // 发出后立即清空输入框与草稿：用户可在流式进行时输入下一条，两者互不影响
    input.value = "";
    if (draftTimer) { clearTimeout(draftTimer); draftTimer = null; }
    saveDraft(S.agentId, "");
    hideFailBubble();
    setRunning(true);
    stream.beginRun(historyEl);
    // 发送后自动下滑到最新消息并跟随流式输出；鼠标上滑后 atBottom 变 false，自动停止跟随
    if (!(settingsCache && settingsCache.auto_scroll_on_send === false)) {
      const pane = $("#historyPane");
      if (pane) pane.scrollTop = pane.scrollHeight;
    }
    const ok = await openChatWs(content);
    if (ok) {
      // 浏览器接管提示 / 敏感信息替换列表 / 常用 prompt 为「一次性状态」：发送成功后自动关闭
      if (takeoverHintOn && takeoverHintReset) takeoverHintReset();
      if (sensitiveInjectOn && sensitiveInjectReset) sensitiveInjectReset();
      if (injectCommon) resetCommonPrompt();
      // 软取消（停止中 → 本轮回 done）不播「回复已完成」气泡与提示音。
      // 正常完成的气泡由 SessionStream 的 done → view.onDone 统一触发（即使从别处触发也能提示）。
      // 停止后修复削掉了还没被消费的用户消息：提示是否回填（复用失败回填气泡）。
      if (ok.backfill) showFailBubble(userText);
    } else {
      // 真正的失败（连接断开 / 后端 error）：气泡提示是否回填原发送信息。
      // 软取消不再走这里（会正常收到 done）。
      showFailBubble(userText);
    }
  }

  async function openProactiveSummary() {
    const sub = sel.value;
    const custom = !!(settingsCache && settingsCache.proactive_summary_custom_percent);
    if (!custom) {
      if (sub) triggerSubAgentProactiveSummary(sub);
      else triggerProactiveSummary();
      return;
    }
    let total = 0;
    try {
      const q = sub ? `?sub_agent=${encodeURIComponent(sub)}` : "";
      const r = await api(`/api/agents/${encodeURIComponent(S.agentId)}/threads/${encodeURIComponent(S.threadId)}/token-usage${q}`);
      total = (r && r.total_tokens) || 0;
    } catch (e) { /* 取不到 token 数就按 0 估算，不阻断流程 */ }
    const pct = await askSummaryPercent((settingsCache && settingsCache.summary_token_percent) || 20, total);
    if (pct == null) return;
    if (sub) triggerSubAgentProactiveSummary(sub, pct);
    else triggerProactiveSummary(pct);
  }

  async function triggerProactiveSummary(summaryPercent = null) {
    if (isRunning) return;
    setRunning(true, "loading");
    stream.beginRun(historyEl);
    const ok = await openChatWs("", true, null, summaryPercent);
    if (ok) {
      // 原地刷新历史，避免整页 render() 造成闪屏
      stream.releaseReply();
      try {
        const h = await api(`/api/agents/${encodeURIComponent(S.agentId)}/threads/${encodeURIComponent(S.threadId)}/history`);
        historyEl.innerHTML = renderMd(h.markdown);
        injectExportButtons(historyEl);
        sel.value = "";
        while (sel.options.length > 1) sel.remove(1);
        const subs = await api(`/api/agents/${encodeURIComponent(S.agentId)}/threads/${encodeURIComponent(S.threadId)}/subgraphs`);
        subs.forEach(s => { const o = document.createElement("option"); o.value = s.node_name; o.textContent = s.node_name; sel.appendChild(o); });
        scrollToLastUserMsg();
      } catch (e) {
        historyEl.innerHTML = `<div class="muted">${t("（无历史）")}</div>`;
      }
      setRunning(false);
    } else {
      setRunning(false);
      toast(t("主动全量总结失败"), true);
    }
  }

  async function triggerSubAgentProactiveSummary(subAgent, summaryPercent = null) {
    if (isRunning) return;
    if (!subAgent) { toast(t("请先选择要总结的子 agent"), true); return; }
    setRunning(true, "loading");
    stream.beginRun(historyEl);
    const ok = await openChatWs("", false, subAgent, summaryPercent);
    if (ok) {
      stream.releaseReply();
      try {
        const h = await api(`/api/agents/${encodeURIComponent(S.agentId)}/threads/${encodeURIComponent(S.threadId)}/subgraphs/${encodeURIComponent(subAgent)}/history`);
        historyEl.innerHTML = renderMd(h.markdown);
        injectExportButtons(historyEl);
        scrollToLastUserMsg();
      } catch (e) {
        historyEl.innerHTML = `<div class="muted">${t("（无历史）")}</div>`;
      }
      setRunning(false);
    } else {
      setRunning(false);
      toast(t("主动全量总结失败"), true);
    }
  }

  async function triggerCleanAll() {
    if (isRunning) return;
    // 三种选择（从左到右）：否 / 归档为快照后删除 / 直接删除。
    const ans = await askConfirm(
      t("删除全部历史消息后不可恢复。可先归档为快照再删除，或直接删除。"),
      {
        buttons: [
          { v: "no", label: t("否") },
          { v: "archive", label: t("归档为快照后删除"), soft: true },
          { v: "delete", label: t("直接删除"), primary: true },
        ],
      }
    );
    if (ans !== "archive" && ans !== "delete") return;
    const archive = ans === "archive";
    // 待删除的历史中包含编辑态正在编辑的消息，直接退出编辑模式（放弃未提交的改动）。
    if (editModeOn) leaveEditMode();
    setRunning(true, "loading");
    stream.beginRun(historyEl);
    const ok = await openChatWs("", false, null, null, archive ? "archive" : true);
    if (ok) {
      // 原地刷新历史，避免整页 render() 造成闪屏
      stream.releaseReply();
      try {
        const h = await api(`/api/agents/${encodeURIComponent(S.agentId)}/threads/${encodeURIComponent(S.threadId)}/history`);
        historyEl.innerHTML = renderMd(h.markdown);
        injectExportButtons(historyEl);
        sel.value = "";
        while (sel.options.length > 1) sel.remove(1);
        const subs = await api(`/api/agents/${encodeURIComponent(S.agentId)}/threads/${encodeURIComponent(S.threadId)}/subgraphs`);
        subs.forEach(s => { const o = document.createElement("option"); o.value = s.node_name; o.textContent = s.node_name; sel.appendChild(o); });
        scrollToLastUserMsg();
      } catch (e) {
        historyEl.innerHTML = `<div class="muted">${t("（无历史）")}</div>`;
      }
      setRunning(false);
    } else {
      setRunning(false);
      toast(archive ? t("归档快照失败，已取消删除") : t("删除全部历史消息失败"), true);
    }
  }

  sendBtn.onclick = send;
  input.onkeydown = e => {
    if (e.key === "Tab") {
      if (!(codeInputAvailable && codeInputOn)) return;
      e.preventDefault();
      const start = input.selectionStart, end = input.selectionEnd;
      input.value = input.value.slice(0, start) + "    " + input.value.slice(end);
      input.selectionStart = input.selectionEnd = start + 4;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      return;
    }
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
  // 软取消：点一次后进入「停止中」（灰、不可点），等后端把当前节点跑完、
  // 图在 superstep 边界停下并回 done 后，再由 setRunning(false) 恢复「发送」。
  stopBtn.onclick = () => {
    if (stopBtn.disabled || !stream.isBusy()) return;
    stopBtn.disabled = true;
    stopBtn.textContent = t("停止中");
    stopBtn.classList.add("stopping");
    stream.stop();
  };
  const failRestoreBtn = $("#failRestoreBtn"), failCancelBtn = $("#failCancelBtn");
  if (failRestoreBtn) failRestoreBtn.onclick = () => {
    const msg = pendingFailedMsg;
    hideFailBubble();
    if (!msg) return;
    const cur = input.value.replace(/\s+$/, "");
    input.value = cur ? cur + "\n" + msg : msg;
    if (draftTimer) { clearTimeout(draftTimer); draftTimer = null; }
    saveDraft(S.agentId, input.value);
    input.focus();
  };
  if (failCancelBtn) failCancelBtn.onclick = () => hideFailBubble();

  // 把本会话的持久流绑定到当前 DOM：若它仍在后台运行，会在这里接回当前内容；
  // 若是页面刷新后的新流，会向后端 attach 并按 run_id+seq 回放。
  await stream.attachView({
    historyEl,
    setRunning: (r, status, actor) => setRunning(r, status, actor),
    markStopping: () => {
      stopBtn.disabled = true;
      stopBtn.textContent = t("停止中");
      stopBtn.classList.add("stopping");
    },
    updateSendState,
    onDone: (result) => { if (!result.stopped) showDoneBubble(); },
  });
  updateSendState();
}

/* ================= 视图5：快照浏览 ================= */
async function renderSnapshotView() {
  // 从「已删除 multi-agent 的快照」进入时，原 agent 已不存在，返回主界面；
  // 否则返回该 agent 的会话列表。
  const fromDeleted = !!S.snapshotFromDeleted;
  const backFn = () => (fromDeleted ? goAgents() : goThreads(S.agentId, S.agentName));
  app.innerHTML = topbar(fromDeleted ? t("返回") : t("返回会话"), backFn);
  const view = document.createElement("div");
  view.className = "chat-view";
  app.appendChild(view);
  bindBack(backFn);

  await getSettings().catch(() => {});

  let snap;
  try {
    snap = await api(`/api/agents/${encodeURIComponent(S.agentId)}/snapshots/${encodeURIComponent(S.snapshotId)}`);
  } catch (e) {
    view.innerHTML = `<div class="empty"><div class="big">⚠️</div>${esc(e.message)}</div>`;
    return;
  }

  const subMap = {};
  (snap.subgraphs || []).forEach(s => { subMap[s.name] = s.markdown; });
  const subNames = Object.keys(subMap);

  view.innerHTML = `
    <div class="chat-head">
      <div class="chat-head-left">
        <span class="thread-name">📸 ${esc(snap.thread_id)}</span>
        <span class="muted">${esc(snap.created_at || "")}</span>
      </div>
      <div class="chat-head-right">
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
    </div>
    <div class="chat-body" id="chatBody">
      <div class="history-pane" id="historyPane"><div class="md-body markdown-body" id="historyMd"><div class="muted">${t("加载历史中…")}</div></div></div>
    </div>`;

  applyZoom();
  applyReasoningZoom();
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

  const historyEl = $("#historyMd");
  historyEl.innerHTML = renderMd(snap.main || "");
  injectExportButtons(historyEl);

  const sel = $("#subgraphSel");
  subNames.forEach(n => { const o = document.createElement("option"); o.value = n; o.textContent = n; sel.appendChild(o); });
  sel.onchange = () => {
    const v = sel.value;
    historyEl.innerHTML = renderMd(v ? subMap[v] : (snap.main || ""));
    injectExportButtons(historyEl);
    const pane = $("#historyPane"); if (pane) pane.scrollTop = 0;
  };
}

class SessionStream {
  constructor(agentId, agentName, threadId) {
    this.agentId = agentId;
    this.agentName = agentName || agentId;
    this.threadId = threadId;
    this.ws = null;
    this.runId = "";
    this.lastSeq = 0;
    this.status = "idle";
    this.endReason = "";
    this.accepted = true;
    this.backfill = false;
    this.stopping = false;
    this.phase = "";
    this.historyMarkdown = "";
    this.baseHtml = null;
    this.replyEl = null;
    this.buffers = this._freshBuffers();
    this.view = null;
    this.viewState = { scrollTop: null, pinned: false };
    this.pendingInterrupt = null;
    this._interrupting = false;
    this._attachPromise = null;
    this._attachResolve = null;
    this._replayDone = false;
    this._commandPromise = null;
    this._commandResolve = null;
    this._startingCommand = false;
    this._accepting = false;
    this._historyPending = false;
    this._disposed = false;
    this._reconnectTimer = null;
    this._reconnectAttempts = 0;
    this._rafPending = false;
  }

  _freshBuffers() {
    return {
      main_user: "",
      segments: [],
      configuredSubs: [],
      featureEnabled: false,
      _domOwner: null,
    };
  }

  isBusy(status = this.status) {
    return ["starting", "running", "stopping", "waiting"].includes(status);
  }

  beginRun(historyEl) {
    if (this.view && this.view.historyEl) historyEl = this.view.historyEl;
    this.baseHtml = historyEl ? historyEl.innerHTML : this.baseHtml;
    // 旧 replyEl 已经显示在页面上，作为历史的一部分保留；只把引用换成新的。
    this.replyEl = null;
    this.buffers = this._freshBuffers();
    this.runId = "";
    this.lastSeq = 0;
    this.status = "starting";
    this.endReason = "";
    this.accepted = false;
    this.backfill = false;
    this.stopping = false;
    this.phase = "";
    this.historyMarkdown = "";
    this.pendingInterrupt = null;
    this._interrupting = false;
    this._historyPending = false;
    this._startingCommand = true;
    this._ensureReplyEl();
  }

  releaseReply() {
    this.replyEl = null;
    this.buffers = this._freshBuffers();
    this.status = "idle";
    this.stopping = false;
    this.phase = "";
    this.pendingInterrupt = null;
    this._startingCommand = false;
  }

  _ensureReplyEl() {
    if (!this.replyEl) {
      this.replyEl = document.createElement("div");
      this.replyEl.className = "current-reply md-body markdown-body";
    }
    if (this.view && this.view.historyEl && this.replyEl.parentElement !== this.view.historyEl) {
      this.view.historyEl.appendChild(this.replyEl);
    }
    if (this.view) currentReplyEl = this.replyEl;
    return this.replyEl;
  }

  attachView(view) {
    this.view = view;
    if (view.historyEl) {
      if (this.baseHtml != null) {
        view.historyEl.innerHTML = this.baseHtml;
        injectExportButtons(view.historyEl);
      } else if (this.historyMarkdown) {
        view.historyEl.innerHTML = renderMd(this.historyMarkdown);
        injectExportButtons(view.historyEl);
      }
      if (!this.replyEl && this.isBusy()) this._ensureReplyEl();
      if (this.replyEl) view.historyEl.appendChild(this.replyEl);
      this.renderStream();
    }
    this.reportActive();
    const attach = this.connect();
    attach.finally(() => {
      this.syncView();
      if (this.view && this.view.historyEl) {
        const pane = $("#historyPane");
        if (pane && this.viewState.scrollTop != null) pane.scrollTop = this.viewState.scrollTop;
      }
      if (this.pendingInterrupt) this._showInterrupt();
      if (this.status === "completed" && !this.accepted) this.maybeAccept();
    });
    return attach;
  }

  detachView() {
    if (!this.view) return;
    const pane = $("#historyPane");
    if (pane) {
      this.viewState.scrollTop = pane.scrollTop;
      this.viewState.pinned = pane.scrollHeight - pane.scrollTop - pane.clientHeight < 48;
    }
    if (!this.isBusy()) {
      // 已结束：把当前完整 DOM（含最后回复）作为静态基线；下次进入直接恢复，
      // 不再重复拉 /history，也不会把同一段回复渲染两遍。
      if (this.view.historyEl) this.baseHtml = this.view.historyEl.innerHTML;
      this.replyEl = null;
    } else if (this.replyEl) {
      this.replyEl.remove();
    }
    this.view = null;
    if (currentReplyEl) currentReplyEl = null;
  }

  dispose() {
    this._disposed = true;
    if (this._reconnectTimer) { clearTimeout(this._reconnectTimer); this._reconnectTimer = null; }
    try { if (this.ws) this.ws.close(); } catch (e) {}
    this.ws = null;
  }

  reportActive() {
    if (S.view === "chat" && S.agentId === this.agentId && S.threadId === this.threadId) {
      reportActiveView(this.agentId, this.threadId);
    }
  }

  connect() {
    if (this.ws && this.ws.readyState === WebSocket.OPEN && this._replayDone) {
      return Promise.resolve();
    }
    if (this._attachPromise) return this._attachPromise;
    this._disposed = false;
    this._attachPromise = new Promise((resolve) => { this._attachResolve = resolve; });
    const proto = location.protocol === "https:" ? "wss" : "ws";
    let sock;
    try {
      sock = new WebSocket(`${proto}://${location.host}/api/agents/${encodeURIComponent(this.agentId)}/threads/${encodeURIComponent(this.threadId)}/chat`);
    } catch (e) {
      this._resolveAttach();
      return Promise.resolve();
    }
    this.ws = sock;
    sock.onopen = () => {
      this._reconnectAttempts = 0;
      try {
        sock.send(JSON.stringify({ type: "attach", run_id: this.runId || "", since: this.lastSeq || 0 }));
      } catch (e) {
        this._resolveAttach();
      }
    };
    sock.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      this._handleMessage(msg).catch((err) => console.error(err));
    };
    sock.onerror = () => {};
    sock.onclose = () => {
      if (this.ws === sock) this.ws = null;
      this._replayDone = false;
      this._resolveAttach();
      if (this._disposed) return;
      if (this._reconnectTimer) return;
      this._reconnectAttempts += 1;
      const delay = Math.min(5000, 700 * this._reconnectAttempts);
      this._reconnectTimer = setTimeout(() => {
        this._reconnectTimer = null;
        if (!this._disposed) this.connect();
      }, delay);
    };
    return this._attachPromise;
  }

  _resolveAttach() {
    this._attachPromise = null;
    if (this._attachResolve) {
      const fn = this._attachResolve;
      this._attachResolve = null;
      fn();
    }
  }

  _resolveCommand(value) {
    if (this._commandResolve) {
      const fn = this._commandResolve;
      this._commandResolve = null;
      this._commandPromise = null;
      fn(value);
    }
    this._startingCommand = false;
    if (value === false) refreshSessions();
  }

  async sendCommand(command) {
    this._commandPromise = new Promise((resolve) => { this._commandResolve = resolve; });
    this._startingCommand = true;
    const attached = await this.connect();
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      this._resolveCommand(false);
      return false;
    }
    try { this.ws.send(JSON.stringify(command)); }
    catch (e) { this._resolveCommand(false); return false; }
    return await this._commandPromise;
  }

  async sendRaw(command) {
    await this.connect();
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      try { this.ws.send(JSON.stringify(command)); } catch (e) {}
    }
  }

  async stop() {
    this.stopping = true;
    this.status = "stopping";
    this.syncView();
    await this.sendRaw({ type: "stop" });
  }

  async _loadFinalHistory(runId) {
    try {
      const h = await api(`/api/agents/${encodeURIComponent(this.agentId)}/threads/${encodeURIComponent(this.threadId)}/history`);
      if (this.runId === runId && this.status === "completed") {
        this.historyMarkdown = h.markdown || "";
        this.baseHtml = null;
        this.replyEl = null;
        if (this.view && this.view.historyEl) {
          this.view.historyEl.innerHTML = renderMd(this.historyMarkdown);
          injectExportButtons(this.view.historyEl);
          this.syncView();
        }
      }
    } catch (e) { /* 历史拉取失败时保留当前 DOM，不影响验收 */ }
    finally {
      this._historyPending = false;
      this.maybeAccept();
    }
  }

  async maybeAccept() {
    if (this._historyPending) return;
    if (this.status !== "completed" || this.accepted || this._accepting) return;
    if (!(this.view && S.view === "chat" && S.agentId === this.agentId && S.threadId === this.threadId)) return;
    if (document.visibilityState !== "visible") return;
    this._accepting = true;
    const ok = await acceptSession(this.agentId, this.threadId);
    this._accepting = false;
    if (ok) {
      this.accepted = true;
      this.status = "idle";
      this.endReason = "";
      this.backfill = false;
      this.syncView();
    }
  }

  syncView() {
    if (!this.view) return;
    const busy = this.isBusy();
    const mode = this.status === "waiting" ? "waiting" : (this.phase || "thinking");
    this.view.setRunning(busy, mode);
    if (this.status === "stopping") this.view.markStopping();
    this.view.updateSendState();
  }

  async _handleMessage(msg) {
    if (msg.type === "run_state") {
      this._handleRunState(msg);
      return;
    }
    if (msg.type === "replay_done") {
      this.lastSeq = Math.max(this.lastSeq, Number(msg.seq) || 0);
      this._replayDone = true;
      this._resolveAttach();
      this.syncView();
      if (this.status === "completed" && this._commandPromise) {
        // 断线期间 run 已经完成：completed 快照不会再回放 done，必须在这里收尾，
        // 否则发送按钮会一直等一个永远不来的 done。
        const result = { stopped: this.endReason === "stopped", backfill: !!this.backfill };
        this._resolveCommand(result);
        if (this.view && !result.stopped && this.view.onDone) this.view.onDone(result);
        refreshSessions();
      } else if (!this.isBusy() && this.status === "idle" && this._commandPromise) {
        // 命令在断线时丢了：没有 run 可等，直接按失败处理。
        this._resolveCommand(false);
      }
      if (this.status === "completed" && !this.accepted) this.maybeAccept();
      return;
    }
    if (msg.run_id && typeof msg.seq === "number") {
      if (msg.run_id === this.runId && msg.seq <= this.lastSeq) return;
      if (msg.run_id === this.runId || !this.runId) {
        this.runId = this.runId || msg.run_id;
        this.lastSeq = Math.max(this.lastSeq, msg.seq);
      }
    }
    await this._handleEvent(msg);
  }

  _handleRunState(msg) {
    const wasStatus = this.status;
    const wasCompleted = wasStatus === "completed";
    if (msg.run_id && msg.run_id !== this.runId) {
      const ours = this._startingCommand;
      this.runId = msg.run_id;
      if (!ours) {
        if (this.view && this.view.historyEl) {
          this.baseHtml = this.view.historyEl.innerHTML;
          this.replyEl = null;
        }
        this.buffers = this._freshBuffers();
        this.lastSeq = 0;
        if (["starting", "running", "stopping", "waiting"].includes(msg.status)) {
          this._ensureReplyEl();
        }
      }
      this._startingCommand = false;
    }
    this.status = msg.status || this.status;
    this.endReason = msg.end_reason || "";
    this.accepted = !!msg.accepted;
    this.backfill = !!msg.backfill;
    this.stopping = this.status === "stopping";
    if (Array.isArray(msg.sub_agents) && msg.sub_agents.length) {
      this.buffers.configuredSubs = msg.sub_agents;
      this.buffers.featureEnabled = msg.sub_agents.length >= 2;
    }
    if (msg.snapshot) {
      this.lastSeq = 0;
      if (this.status === "completed") {
        this.lastSeq = Number(msg.seq) || 0;
        this.historyMarkdown = msg.history_markdown || "";
        this.baseHtml = null;
        if (this.view && this.view.historyEl && !wasCompleted) {
          if (this.historyMarkdown) {
            this.replyEl = null;
            this.view.historyEl.innerHTML = renderMd(this.historyMarkdown);
            injectExportButtons(this.view.historyEl);
          } else {
            // 断线期间完成、后端不回放事件：重新拉一次最终历史，否则只看到断线前
            // 的半截回复。拉完之前不要自动验收，避免把半截内容当成已读。
            this._historyPending = true;
            this._loadFinalHistory(msg.run_id);
          }
        }
      } else if (this.isBusy()) {
        this.historyMarkdown = msg.history_markdown || "";
        if (this.view && this.view.historyEl && this.historyMarkdown
          && (!this.replyEl || this.replyEl.childElementCount === 0)) {
          this.baseHtml = null;
          this.view.historyEl.innerHTML = renderMd(this.historyMarkdown);
          injectExportButtons(this.view.historyEl);
          this._ensureReplyEl();
          this.renderStream();
        } else if (this.view && this.view.historyEl && !this.replyEl) {
          // 运行时还在构建、基线尚未拿到：保留 renderChatView 已加载的 /history。
          this._ensureReplyEl();
        }
      } else {
        this.lastSeq = Number(msg.seq) || 0;
      }
      if (msg.interrupt_prompt) this.pendingInterrupt = msg.interrupt_prompt;
    }
    this.syncView();
    if (this.isBusy()) {
      // 让 2s 轮询之外也能立刻把“正在进行”写进悬浮球/线程列表的全局快照。
      refreshSessions();
    }
    if (
      !msg.snapshot
      && this.status === "completed"
      && this.endReason === "error"
      && !this.accepted
    ) {
      // 失败也属于“本轮结束”；用户正看着该会话且页面可见时按验收处理。
      this.maybeAccept();
    }
  }

  _handleEvent(msg) {
    switch (msg.type) {
      case "text":
        if (msg.source === "main" || msg.source.startsWith("sub:")) this._leaveLoading("answering", msg.source);
        if (msg.source === "main_user") this.buffers.main_user += msg.text;
        else if (msg.source === "main") this._pushMain("text", msg.text);
        else this._pushSub(msg.source.replace(/^sub:/, ""), "text", msg.text);
        this._schedule();
        break;
      case "reasoning":
        this._leaveLoading("thinking", msg.source);
        if (msg.source === "main") this._pushMain("reasoning", msg.text);
        else this._pushSub(msg.source.replace(/^sub:/, ""), "reasoning", msg.text);
        this._schedule();
        break;
      case "sub_agents":
        this.buffers.configuredSubs = msg.names || [];
        this.buffers.featureEnabled = this.buffers.configuredSubs.length >= 2;
        this._schedule();
        break;
      case "subgraph_start": {
        let seg = this._lastSeg();
        if (!seg || seg.kind !== "sub") {
          seg = { kind: "sub", subs: {}, order: [], marks: {}, selected: null };
          this.buffers.segments.push(seg);
        }
        if (!seg.subs[msg.name]) { seg.subs[msg.name] = []; seg.order.push(msg.name); }
        seg.marks[msg.name] = "running";
        if (!seg.selected) seg.selected = msg.name;
        this._schedule();
        break;
      }
      case "subgraph_end": {
        for (let i = this.buffers.segments.length - 1; i >= 0; i--) {
          const seg = this.buffers.segments[i];
          if (seg.kind === "sub" && seg.marks[msg.name] === "running") { seg.marks[msg.name] = "done"; break; }
        }
        this._schedule();
        break;
      }
      case "phase":
        this.phase = msg.phase || "";
        if (this.view) setStatusIndicator(msg.phase, this._actorOf(msg.source));
        break;
      case "tool_call": {
        const isSub = typeof msg.source === "string" && msg.source.startsWith("sub:");
        if (isSub && settingsCache && settingsCache.show_sub_agent_tools === false) break;
        this._leaveLoading("thinking", msg.source || "main");
        let tcHtml = `\n\n🔧 **${t("工具调用")}**: \`${esc(msg.name)}\`\n\n`;
        if (msg.args && Object.keys(msg.args).length) tcHtml += "```json\n" + JSON.stringify(msg.args, null, 2) + "\n```\n\n";
        if (isSub) this._pushSub(msg.source.replace(/^sub:/, ""), "tool", tcHtml);
        else this._pushMain("tool", tcHtml);
        this._schedule();
        break;
      }
      case "tool_result": {
        const isSub = typeof msg.source === "string" && msg.source.startsWith("sub:");
        if (isSub && settingsCache && settingsCache.show_sub_agent_tools === false) break;
        this._leaveLoading("thinking", msg.source || "main");
        const rHtml = buildToolResultHtml(msg.name, msg.content);
        if (isSub) this._pushSub(msg.source.replace(/^sub:/, ""), "tool_result", rHtml);
        else this._pushMain("tool_result", rHtml);
        this._schedule();
        break;
      }
      case "interrupt":
        this.pendingInterrupt = msg.prompt || "";
        if (this.view) this._showInterrupt();
        break;
      case "status":
        if (msg.status === "loading" && this.view) setStatusIndicator("loading");
        else if (msg.status === "ready") this._leaveLoading("thinking", "main");
        break;
      case "snapshot_saved":
        toast(t("已归档为快照（{n} 条消息）").replace("{n}", String(msg.total_messages || 0)));
        break;
      case "stopping":
        this.status = "stopping";
        this.stopping = true;
        this.syncView();
        break;
      case "backfill":
        this.backfill = true;
        break;
      case "done": {
        this.status = "completed";
        this.endReason = msg.stopped ? "stopped" : "done";
        this.stopping = false;
        this.phase = "";
        this.syncView();
        const result = { stopped: !!msg.stopped, backfill: !!this.backfill };
        this._resolveCommand(result);
        if (this.view) {
          if (!result.stopped && this.view.onDone) this.view.onDone(result);
        }
        refreshSessions();
        this.maybeAccept();
        break;
      }
      case "error":
        this._pushMain("tool", `\n\n> ⚠️ ${brk(msg.message)}\n\n`);
        this._schedule();
        this._resolveCommand(false);
        break;
    }
  }

  _actorOf(source) {
    return source === "main" ? t("主 agent") : String(source || "").replace(/^sub:/, "");
  }

  _leaveLoading(next, source) {
    if (this.view && statusMode === "loading") setStatusIndicator(next, this._actorOf(source));
  }

  _lastSeg() { return this.buffers.segments[this.buffers.segments.length - 1]; }

  _pushMain(type, content) {
    let seg = this._lastSeg();
    if (!seg || seg.kind !== "main") { seg = { kind: "main", blocks: [] }; this.buffers.segments.push(seg); }
    const last = seg.blocks[seg.blocks.length - 1];
    if (last && last.type === type) last.content += content;
    else seg.blocks.push({ type, content });
  }

  _pushSub(name, type, content) {
    let seg = this._lastSeg();
    if (!seg || seg.kind !== "sub") {
      seg = { kind: "sub", subs: {}, order: [], marks: {}, selected: null };
      this.buffers.segments.push(seg);
    }
    if (!seg.subs[name]) { seg.subs[name] = []; seg.order.push(name); if (!(name in seg.marks)) seg.marks[name] = "running"; }
    const arr = seg.subs[name];
    const last = arr[arr.length - 1];
    if (last && last.type === type) last.content += content;
    else arr.push({ type, content });
  }

  _schedule() {
    if (this._rafPending) return;
    this._rafPending = true;
    requestAnimationFrame(() => { this._rafPending = false; this.renderStream(); });
  }

  _reasoningBlock(txt) {
    return txt
      ? `<details class="reasoning-block" open><summary>🧠 ${t("思考过程")}</summary><div class="reasoning-body reasoning-md">${renderMd(txt)}</div></details>`
      : "";
  }

  _renderBlock(b) {
    if (b.type === "reasoning") return this._reasoningBlock(b.content);
    if (b.type === "tool_result") return b.content;
    return `<div class="ai-msg-block">${renderMd(b.content)}</div>`;
  }

  _renderBlocks(blocks) { return (blocks || []).map((b) => this._renderBlock(b)).join(""); }

  _subOptionLabel(seg, name) {
    return name + (seg.marks[name] === "running" ? " ●" : seg.marks[name] === "done" ? " ✓" : "");
  }

  _subContentHtml(seg, name) {
    if (!name) return "";
    if (!(name in seg.marks)) return `<div class="sub-placeholder">${t("未调用")}</div>`;
    const blocks = seg.subs[name];
    if (!blocks || !blocks.length) return `<div class="sub-placeholder">${t("等待输出…")}</div>`;
    return this._renderBlocks(blocks);
  }

  _buildSegmentEl(seg) {
    seg._el = document.createElement("div");
    if (seg.kind === "main") { seg._el.className = "seg-main"; return; }
    seg._el.className = "seg-sub";
    seg._el.innerHTML = `<div class="sub-stream-divider"><select class="sub-stream-select" title="${t("选择子 agent")}"></select></div><div class="sub-stream-content"></div>`;
    seg._dividerEl = seg._el.querySelector(".sub-stream-divider");
    seg._selectEl = seg._el.querySelector(".sub-stream-select");
    seg._contentEl = seg._el.querySelector(".sub-stream-content");
    seg._selectEl.onchange = () => { seg.selected = seg._selectEl.value; this.renderStream(); };
  }

  renderStream() {
    if (!this.view || !this.replyEl) return;
    const replyEl = this.replyEl;
    const pane = $("#historyPane");
    const atBottom = pane && (pane.scrollHeight - pane.scrollTop - pane.clientHeight < 48);
    if (this.buffers._domOwner !== replyEl) {
      this.buffers._domOwner = replyEl;
      for (const seg of this.buffers.segments) { seg._el = null; seg._dividerEl = null; seg._selectEl = null; seg._contentEl = null; }
    }
    if (this.buffers.main_user) {
      if (!replyEl._userEl || replyEl._userEl.parentElement !== replyEl) {
        replyEl._userEl = document.createElement("div");
        replyEl._userEl.className = "user-msg-block";
        replyEl.insertBefore(replyEl._userEl, replyEl.firstChild);
      }
      const { text: userText, memory: userMem } = splitUserMemory(this.buffers.main_user);
      const memBtn = userMem ? `<button type="button" class="mem-attach-btn" title="${t("查看本条消息自动附着的记忆")}">🔖 ${t("记忆附着")}</button>` : "";
      const memPanel = userMem ? `<div class="mem-attach-panel" hidden>${esc(userMem).replace(/\n/g, "<br>")}</div>` : "";
      replyEl._userEl.innerHTML = `<div class="user-msg-head">🧑 <strong>${t("用户")}</strong></div><blockquote class="user-msg-quote${userMem ? " has-mem" : ""}">${memBtn}${esc(userText).replace(/\n/g, "<br>")}</blockquote>${memPanel}`;
    }
    for (const seg of this.buffers.segments) {
      if (!seg._el || seg._el.parentElement !== replyEl) {
        this._buildSegmentEl(seg);
        replyEl.appendChild(seg._el);
      }
      if (seg.kind === "main") {
        seg._el.innerHTML = this._renderBlocks(seg.blocks);
        continue;
      }
      const showRegion = this.buffers.featureEnabled;
      seg._dividerEl.style.display = showRegion ? "" : "none";
      if (showRegion) {
        const opts = this.buffers.configuredSubs.length ? this.buffers.configuredSubs : seg.order;
        const sig = opts.join("\u0001");
        if (seg._selectEl._sig !== sig) {
          seg._selectEl._sig = sig;
          seg._selectEl.innerHTML = opts.map((n) => `<option value="${esc(n)}">${esc(this._subOptionLabel(seg, n))}</option>`).join("");
        }
        for (const o of Array.from(seg._selectEl.options)) {
          const label = this._subOptionLabel(seg, o.value);
          if (o.textContent !== label) o.textContent = label;
        }
        if (seg.selected == null) seg.selected = seg.order[0] || this.buffers.configuredSubs[0] || null;
        if (seg._selectEl.value !== (seg.selected || "")) seg._selectEl.value = seg.selected || "";
        seg._contentEl.innerHTML = this._subContentHtml(seg, seg.selected);
      } else {
        const tag = seg.order.length ? `<div class="sub-tag">🧩 ${t("子 agent")} · ${esc(seg.order.join("、"))}</div>` : "";
        seg._contentEl.innerHTML = tag + this._renderBlocks(seg.order.flatMap((n) => seg.subs[n] || []));
      }
    }
    if (pane && (pinned || atBottom)) pane.scrollTop = pane.scrollHeight;
  }

  async _showInterrupt() {
    if (this._interrupting || !this.pendingInterrupt) return;
    this._interrupting = true;
    const prompt = this.pendingInterrupt;
    this.pendingInterrupt = null;
    const ans = await askConfirm(prompt || t("请确认"), null);
    this._interrupting = false;
    if (ans === "yes" && this.view) setStatusIndicator("thinking", t("主 agent"));
    await this.sendRaw({ type: "resume", value: ans });
    this.pendingInterrupt = null;
  }
}

// 兼容旧调用点：直接使用当前聊天视图的流。
function openChatWs(content, proactive = false, subAgent = null, summaryPercent = null, cleanAll = false) {
  const stream = activeSessionStream;
  if (!stream) return Promise.resolve(false);
  const command = cleanAll
    ? { type: "clean_all", archive: cleanAll === "archive" }
    : subAgent
      ? { type: "proactive_summarize_sub", sub_agent: subAgent, percent: summaryPercent }
      : proactive
        ? { type: "proactive_summarize", percent: summaryPercent }
        : { type: "send", content };
  return stream.sendCommand(command);
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

/* ================= 链接跳转：绝不在应用窗口内渲染网页 =================
   应用窗口（WebView2）没有浏览器通用功能（返回、地址栏等），一旦在窗口内导航到
   外部网页，用户就回不来了（只能重启）。所以拦截所有 <a href> 的点击：
   - 有受控的 CDP 浏览器进程（浏览器工具绑定的那个）→ 在其中新开标签页打开；
   - 否则 → 用系统默认浏览器打开。
   后端 /api/browser/open-url 负责这个判断。 */
async function openExternalLink(url) {
  try {
    await api("/api/browser/open-url", {
      method: "POST",
      body: JSON.stringify({
        url,
        agent_id: S.view === "chat" ? S.agentId : null,
        thread_id: S.view === "chat" ? S.threadId : null,
      }),
    });
  } catch (e) {
    toast(t("打开链接失败") + "：" + (e && e.message ? e.message : e), true);
  }
}

document.addEventListener("click", (e) => {
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  const a = e.target && e.target.closest ? e.target.closest("a[href]") : null;
  if (!a) return;
  const href = a.getAttribute("href") || "";
  if (!href || href.startsWith("#") || /^javascript:/i.test(href)) return;
  e.preventDefault();
  openExternalLink(a.href || href);
}, true);

/* ================= Ctrl+F 页内查找（WebView2 无原生查找栏，自建） =================
   套进 WebView2 后浏览器自带的 Ctrl+F 查找栏没有了，这里用 CSS Custom Highlight
   API 实现页内查找：不修改 DOM（对 SPA/流式内容安全），只加高亮层。 */
const findState = { open: false, matches: [], idx: -1, input: null, count: null };

function findEnsureBar() {
  if (findState.input) return;
  const bar = document.createElement("div");
  bar.id = "findBar";
  bar.className = "find-bar";
  bar.style.display = "none";
  bar.innerHTML = `
    <input id="findInput" class="find-input" type="text" placeholder="${esc(t("查找"))}" />
    <span id="findCount" class="find-count"></span>
    <button id="findPrev" class="find-btn" title="${esc(t("上一个"))}" aria-label="${esc(t("上一个"))}">↑</button>
    <button id="findNext" class="find-btn" title="${esc(t("下一个"))}" aria-label="${esc(t("下一个"))}">↓</button>
    <button id="findClose" class="find-btn" title="${esc(t("关闭"))}" aria-label="${esc(t("关闭"))}">×</button>`;
  document.body.appendChild(bar);
  const input = bar.querySelector("#findInput");
  findState.input = input;
  findState.count = bar.querySelector("#findCount");
  bar.querySelector("#findPrev").onclick = () => findMove(-1);
  bar.querySelector("#findNext").onclick = () => findMove(1);
  bar.querySelector("#findClose").onclick = () => findClose();
  input.oninput = () => findSearchReset();
  input.onkeydown = (e) => {
    if (e.key === "Enter") { e.preventDefault(); findMove(e.shiftKey ? -1 : 1); }
    else if (e.key === "Escape") { e.preventDefault(); findClose(); }
  };
}

function findCollect(query) {
  const ranges = [];
  if (!query) return ranges;
  const q = query.toLowerCase();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const text = node.nodeValue;
      if (!text || !text.trim()) return NodeFilter.FILTER_REJECT;
      const p = node.parentElement;
      if (!p || p.closest("#findBar") || p.closest("script,style,noscript")) return NodeFilter.FILTER_REJECT;
      // 排除"存在但不绘制"的文本，否则会成幽灵匹配——计数里有，却既不跳转也无高亮：
      //  - KaTeX 的 .katex-mathml 是 clip:rect(1px…)+绝对定位的隐藏注解（含原始 LaTeX）；
      //  - display:none 的文本没有布局盒（getClientRects 为空）；
      //  - visibility:hidden 有布局但不绘制。
      // 注意：折叠 <details> 的内容不能靠 getClientRects 判断（Chrome 仍会报告矩形），
      // 它们在导航时由 findScrollToCurrent 先展开再定位，故此处保留。
      if (p.closest(".katex-mathml")) return NodeFilter.FILTER_REJECT;
      if (p.getClientRects().length === 0) return NodeFilter.FILTER_REJECT;
      if (window.getComputedStyle(p).visibility === "hidden") return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  let node;
  while ((node = walker.nextNode())) {
    const lower = node.nodeValue.toLowerCase();
    let from = 0, at;
    while ((at = lower.indexOf(q, from)) !== -1) {
      const r = document.createRange();
      r.setStart(node, at);
      r.setEnd(node, at + query.length);
      ranges.push(r);
      from = at + query.length;
    }
  }
  return ranges;
}

function findPaint() {
  if (typeof Highlight === "undefined" || !window.CSS || !CSS.highlights) return;
  const all = new Highlight();
  findState.matches.forEach((r) => all.add(r));
  CSS.highlights.set("studio-find-match", all);
  if (findState.idx >= 0 && findState.matches[findState.idx]) {
    const cur = new Highlight();
    cur.add(findState.matches[findState.idx]);
    CSS.highlights.set("studio-find-current", cur);
  } else if (CSS.highlights.has("studio-find-current")) {
    CSS.highlights.delete("studio-find-current");
  }
}

function findUpdateCount() {
  if (!findState.count) return;
  findState.count.textContent = findState.matches.length
    ? (findState.idx + 1) + "/" + findState.matches.length
    : (findState.input && findState.input.value ? t("无结果") : "");
}

function findScrollToCurrent() {
  const r = findState.matches[findState.idx];
  if (!r) return;
  try {
    const n = r.startContainer;
    const el = n.nodeType === 1 ? n : n.parentElement;
    if (!el) return;
    // 命中可能落在折叠的 <details>（思考过程 / 工具调用 / 工具结果）里。Chrome 对折叠内容
    // 仍会报告布局矩形（甚至超大），所以必须先展开祖先 details，再取矩形——否则会滚到
    // 完全错误的位置（这正是"大量折叠时跳转错位"的根因）。命中在 <summary> 行本身时它
    // 本就可见，不必展开那个 details。
    let guard = 0;
    let closed = el.closest ? el.closest("details:not([open])") : null;
    const inSummaryOf = (details) => {
      const sum = el.closest("summary");
      return !!(sum && sum.parentElement === details);
    };
    while (closed && guard++ < 20) {
      if (!inSummaryOf(closed)) closed.open = true;
      const parent = closed.parentElement;
      closed = parent && parent.closest ? parent.closest("details:not([open])") : null;
    }
    // 用 Range 自身的矩形定位（滚动整个父块会让大 markdown 块"看起来没动"）。
    // 逐层上溯，把每一层可滚动祖先里的命中滚动到容器可视区中部。
    const chain = [];
    for (let p = el; p; p = p.parentElement) {
      if (p === document.body) break;
      const st = window.getComputedStyle(p);
      if (/(auto|scroll|overlay)/.test(st.overflowY) && p.scrollHeight > p.clientHeight + 1) {
        chain.push(p);
      }
    }
    const scroller = document.scrollingElement || document.documentElement;
    chain.push(scroller);
    chain.forEach((c) => {
      const rect = r.getBoundingClientRect();
      if (!rect || (rect.width === 0 && rect.height === 0)) return;
      const cRect = c.getBoundingClientRect();
      const delta = (rect.top + rect.height / 2) - (cRect.top + c.clientHeight / 2);
      if (Math.abs(delta) > 1) c.scrollTop += delta;
    });
  } catch (e) {}
}

function findSearchReset() {
  findState.matches = findCollect(findState.input.value);
  findState.idx = findState.matches.length ? 0 : -1;
  findPaint();
  if (findState.idx >= 0) findScrollToCurrent();
  findUpdateCount();
}

function findMove(step) {
  findState.matches = findCollect(findState.input.value);
  if (!findState.matches.length) {
    findState.idx = -1;
    findPaint();
    findUpdateCount();
    return;
  }
  if (findState.idx < 0 || findState.idx >= findState.matches.length) {
    findState.idx = step > 0 ? 0 : findState.matches.length - 1;
  } else {
    findState.idx = (findState.idx + step + findState.matches.length) % findState.matches.length;
  }
  findPaint();
  findScrollToCurrent();
  findUpdateCount();
}

function findOpen() {
  findEnsureBar();
  const bar = document.getElementById("findBar");
  bar.style.display = "flex";
  findState.open = true;
  const sel = window.getSelection ? (window.getSelection().toString() || "") : "";
  if (sel && sel.length <= 100) findState.input.value = sel.trim();
  findState.input.focus();
  findState.input.select();
  findSearchReset();
}

function findClose() {
  findState.open = false;
  const bar = document.getElementById("findBar");
  if (bar) bar.style.display = "none";
  findState.matches = [];
  findState.idx = -1;
  if (window.CSS && CSS.highlights) {
    CSS.highlights.delete("studio-find-match");
    CSS.highlights.delete("studio-find-current");
  }
}

document.addEventListener("keydown", (e) => {
  const mod = e.ctrlKey || e.metaKey;
  if (mod && !e.altKey && (e.key === "f" || e.key === "F")) {
    e.preventDefault();
    findOpen();
    return;
  }
  if (e.key === "Escape" && findState.open) {
    e.preventDefault();
    findClose();
    return;
  }
  if (findState.open && findState.input && document.activeElement === findState.input && e.key === "F3") {
    e.preventDefault();
    findMove(e.shiftKey ? -1 : 1);
  }
});

/* ================= 启动 ================= */
applyColors();
applyCardSettings();
render();
startSessionPolling();
document.addEventListener("visibilitychange", () => {
  if (S.view === "chat" && activeSessionStream) {
    reportActiveView(S.agentId, S.threadId);
    if (document.visibilityState === "visible") activeSessionStream.maybeAccept();
  }
});
// 旧版 store 数据库迁移提示（只弹一次；数据库服务可能还在启动，函数内部会重试）
maybePromptStoreMigration();
