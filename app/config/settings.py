"""全局系统设置（区别于每个 multi-agent 的配置）。

持久化在 configs/settings.json。记忆吸附（memory_attach）属于全局设置：
它影响图编译，因此在进入某个 agent 后（图已编译）不允许改动，需退回主界面。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

SETTINGS_FILE_NAME = "settings.json"
# 长期记忆统一存放的 store 数据库默认名称。所有 multi-agent 共用这一份长期记忆，
# 不再为每个 multi-agent 单独指定 store 数据库（是否共用由 store_namespace 决定）。
DEFAULT_STORE_DATABASE = "Long_Term_Memory"

# 浏览器配色：边框主色 / 字体（标签与地址栏选中）主题色。
DEFAULT_BROWSER_BORDER_COLOR = "#4f46e5"
DEFAULT_BROWSER_TEXT_COLOR = "#ffffff"
_HEX_COLOR_RE = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def normalize_hex_color(value: object, fallback: str) -> str:
    """把用户/旧配置里的颜色统一成小写 ``#rrggbb``；非法值回退到默认色。"""
    text = str(value or "").strip()
    if not text:
        return fallback
    if not text.startswith("#"):
        text = "#" + text
    if not _HEX_COLOR_RE.match(text):
        return fallback
    digits = text[1:].lower()
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    return "#" + digits


class BrowserColorPreset(BaseModel):
    """一条浏览器配色：边框主色 + 字体主题色。

    用户按列表顺序为会话浏览器取色；列表用完后由后端生成不重复的随机配色。
    """

    border_color: str = DEFAULT_BROWSER_BORDER_COLOR
    text_color: str = DEFAULT_BROWSER_TEXT_COLOR

    @field_validator("border_color", "text_color", mode="before")
    @classmethod
    def _normalize_color(cls, value, info):
        fallback = (
            DEFAULT_BROWSER_BORDER_COLOR
            if info.field_name == "border_color"
            else DEFAULT_BROWSER_TEXT_COLOR
        )
        return normalize_hex_color(value, fallback)


def _coerce_browser_color_presets(value: object) -> list:
    """兼容 null / 非列表 / 混入脏条目，避免一条坏数据让整份 settings 回退默认。"""
    if not value or not isinstance(value, (list, tuple)):
        return []
    return [item for item in value if isinstance(item, (dict, BrowserColorPreset))]


class SensitiveEntry(BaseModel):
    """敏感信息表单的一行：name = 表单里 LLM 需要填的「名称」，value = 真实内容。"""

    name: str = ""
    value: str = ""


class CommonPrompt(BaseModel):
    """全局常用 prompt 库的一条。

    - `id`：稳定标识。每个会话保存的是「指向某条 id 的指针」，所以在这里改内容，
      所有指向它的会话会一起生效（见 `app/services/common_prompts.py`）。
    - `text`：内容（可多行）。
    """

    id: str = ""
    text: str = ""


def make_common_prompt_id(text: str, taken: set[str] | None = None) -> str:
    """给「还没有 id」的条目生成确定性的稳定 id（老数据从 `list[str]` 升级时用）。

    按内容哈希生成，因此**尚未落盘的旧库反复读取也会得到同一个 id**，
    不会让已经保存的会话指针时有时无。内容相同的条目用 `taken` 去重。
    """
    digest = hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:10]
    base = f"cp{digest}"
    if not taken:
        return base
    cand, n = base, 2
    while cand in taken:
        cand = f"{base}-{n}"
        n += 1
    return cand


class Settings(BaseModel):
    memory_attach: bool = True
    num_memories_attached: int = 5
    warn_unsaved_changes: bool = True
    notification_sound: str = "ber"
    send_key: str = "enter"
    newline_key: str = "shift_enter"
    show_placeholders: bool = True
    # 聊天输入框是否显示「注入当前日期」按钮（开启后可在会话里选择把当前日期拼接到用户消息前）
    show_date_inject_button: bool = True
    # 聊天输入框是否显示「注入当前时间」按钮（开启后可在会话里选择把当前时间拼接到用户消息前）。
    # 纯前端显示开关，不参与图编译，无需 invalidate_all。默认开启。
    show_time_inject_button: bool = True
    show_code_input_button: bool = True
    # 常用 prompt 注入功能总开关：关闭后，会话页的「注入常用prompt」「编辑会话常用prompt」
    # 与主界面的「编辑全局常用prompt」三个按钮都不再显示；**已保存的数据一律保留**
    # （全局库 common_prompts 与会话选择都存在后端，不因开关而删除）。
    # 纯前端显示开关，不参与图编译，无需 invalidate_all。默认开启。
    common_prompt_enabled: bool = True
    # 全局常用 prompt 库（多条，每条可多行）；每条有稳定 id，会话页保存的是
    # 「指向某条 id 的指针」——在这里改了内容，指向它的会话会一起变。
    # 兼容老数据：旧版是 list[str]（只有文本），加载时会自动补上确定性 id。
    common_prompts: list[CommonPrompt] = Field(default_factory=list)
    # 裸公式识别：无分隔符公式的启发式渲染，默认关闭以避开日常场景误判
    bare_math_detect: bool = False
    # 激进公式渲染：在裸公式识别基础上支持单字符上下标/希腊字母/数学符号，
    # 并把「看起来是公式」的代码块与行内代码也渲染成公式，默认关闭
    aggressive_math_detect: bool = False
    # 历史浏览时各板块默认展开/折叠
    reasoning_expanded: bool = True
    tool_call_expanded: bool = False
    tool_result_expanded: bool = False
    # 流式输出时是否显示子 agent 的工具调用 / 工具结果（默认开启，仅影响前端流式渲染）
    show_sub_agent_tools: bool = True
    # HTML 导出（md2print）：默认开启；输出路径为空时需先在设置里填写
    export_html: bool = True
    export_html_path: str = ""
    export_html_config: dict = {}
    # 会话页是否显示「将本地md转换为HTML」按钮（用系统文件选择窗口挑一个本地 md，
    # 套用上面的 export_html_config 转成 HTML，写在该 md 文件同目录下）。
    # 纯前端显示开关，不参与图编译，无需 invalidate_all。默认开启。
    show_local_md_convert_button: bool = True
    # MD 导出：默认开启；输出路径为空时需先在设置里填写
    export_md: bool = True
    export_md_path: str = ""
    # 历史消息编辑：总开关（默认开启）
    edit_mode_enabled: bool = True
    # 历史消息编辑：是否允许编辑任意久远的历史消息（默认只编辑最新一轮对话）
    edit_any_history: bool = False
    # 历史消息编辑：是否允许编辑所有消息类型（默认只编辑 AI 回复正文 text）
    edit_all_message_types: bool = False
    # 发送消息后自动跳到最新输出并跟随流式（鼠标上滑可停止跟随），默认开启
    auto_scroll_on_send: bool = True
    # 全量总结默认压缩比例（百分比）：目标 summary 占历史 token 的比例。
    # 默认 20%（即原来的 history_to_summary = 5，五分之一），越小越省 token。
    summary_token_percent: int = 20
    # 主动全量总结时是否弹窗让用户为本次总结单独设置压缩百分比，默认关闭。
    proactive_summary_custom_percent: bool = False
    # 主动搜索记忆（read_memory 工具）的语义相似度门槛，取值范围 0~1，越大越严格。
    search_memory_threshold: float = 0.5
    # 自动吸附记忆的相似度门槛，取值范围 0~1，越大越严格。
    attach_memory_threshold: float = 0.7
    # 读取 PDF 时是否用「基于框线」的表格提取（pdfplumber），并把表格嵌回正文。
    # 关闭后 PDF 只返回正文文本。影响图编译（file tools 闭包），默认开启。
    pdf_table_extraction: bool = True
    # 已输入未发送消息跨 multi-agent 流转：开启后，在任一 multi-agent 输入框输入的内容
    # 会同步更新所有 multi-agent 的未发送消息缓存（等效共用一个缓存）；关闭则各自独立。
    # 纯前端同步逻辑，不参与图编译，无需 invalidate_all。默认开启。
    cross_agent_draft_flow: bool = True
    # 浏览器接管：每次打开浏览器时是否弹出说明弹窗（介绍红色浮动按钮的停止/继续作用）。
    # 纯前端展示，不参与图编译。默认开启。
    browser_takeover_intro_popup: bool = True
    # 浏览器接管：debug 模式。开启后，LLM 每次调用浏览器工具都会在程序根目录的
    # browser_takeover_debug_logs/ 下按小时落盘一份人类可读日志（原始 DOM /
    # 处理后的 DOM / LLM 输入 / 工具返回的 content）。工具调用时实时读取，
    # 不参与图编译，无需 invalidate_all。默认关闭。
    browser_takeover_debug: bool = False
    # 浏览器接管：是否把网页里的图片解析成 [图片] 标签返回给 LLM。
    # 默认关闭（纯文本口径）：关闭时，页面解析之后、BID079 的层级压缩之前，
    # 会把 [图片] 行及其「独子父 [..] 结构标签链」整条删除（含 alt 文本）。
    # 工具调用时实时读取，不参与图编译，无需 invalidate_all。
    browser_image_tags: bool = False  # BID080
    # 浏览器接管：浏览器工具返回内容是否携带 url（BID088）。默认关闭：关闭时，
    # 除专门返回「tab → url 映射表」的 tool_40_return_urls_of_opened_tags 之外，
    # 其余工具的返回内容都不带页面地址——全量返回的 `[document]` 首行只保留标题，
    # 增量里的「页面地址已变化」提示不再附地址本体。动机：页面地址常带一长串没有
    # 语义的随机字符（推广/追踪参数），既挤占上下文又干扰推理。
    # 工具调用时实时读取，不参与图编译，无需 invalidate_all。
    browser_url_in_content: bool = False  # BID088
    # 浏览器接管：可交互元素整合（BID084）。开启后，返回给 LLM 的
    # ``<可点击元素 eN>内容</可点击元素 eN>`` 会压成 ``<可点击元素 eN : 内容>``
    # （可滚动元素除外），取消闭合标签，显著节省上下文长度。默认开启。
    # 工具调用时实时读取；但它同时改变工具描述，故保存时需 invalidate_all。
    browser_interactive_merge: bool = True  # BID084
    # 整合分隔符（BID084）：连接元素编号与内容的符号，默认英文冒号。
    browser_interactive_merge_separator: str = ":"  # BID084
    # 浏览器接管：可交互元素使用英文渲染（BID084）。开启后标签改用
    # ``<Click elem 23>`` / ``<Scroll elem 1>``，工具入参直接传数字（如 23）。
    # 默认关闭；同样会改变工具描述，保存时需 invalidate_all。
    browser_interactive_english: bool = False  # BID084
    # 敏感信息表单：浏览器接管填写「可填入元素」时，LLM 用 <名称> 占位，
    # 后台按本表把 <名称> 替换为真实值（表单里没有的名称原样保留）。
    # 工具调用时实时读取，不参与图编译，无需 invalidate_all。
    sensitive_info: list[SensitiveEntry] = Field(default_factory=list)
    # 隐私遮蔽模式：开启后，浏览器工具返回给 LLM 的一切网页内容（DOM/diff/tabs/error 等，
    # 以及「提示接管」预览块）在交给 LLM 之前，会把敏感信息表单里的「真实内容」反向替换回
    # 对应的 <名称>，从而完全不向 LLM 暴露真实值。工具调用时实时读取，不参与图编译。
    privacy_mask_mode: bool = False
    # 浏览器交互延迟（导航宽限、加载超时、判稳静默窗、轮询间隔、CDP 命令超时、拟人停顿等）。
    # 结构见 browser_agent/timing.py，工具调用时实时读取并覆盖，故改完即生效、无需 invalidate_all。
    browser_timing: dict = Field(default_factory=dict)
    # 浏览器颜色自定义（多实例会话）：按列表顺序给每个会话的自动化浏览器分配
    # 「边框主色 + 字体主题色」。列表为空或已用尽时，后端随机生成不重复的颜色。
    # 只影响下一次启动浏览器（Chrome 写 profile Preferences / Edge 写主题扩展），
    # 不参与图编译，无需 invalidate_all。
    browser_color_presets: list[BrowserColorPreset] = Field(default_factory=list)
    # 工具调用结果显示设置（流式输出）：
    # full=True 展示完整结果；full=False 时按 max_lines / max_chars 截断（0 表示不限，
    # 两个上限都填则触发任意一个就截断）。单行 JSON 会先结构化再按上限截断。
    tool_result_stream_full: bool = True
    tool_result_stream_max_lines: int = 0
    tool_result_stream_max_chars: int = 0
    # 工具调用结果显示设置（查看历史消息），默认按 50 行截断。
    tool_result_history_full: bool = False
    tool_result_history_max_lines: int = 50
    tool_result_history_max_chars: int = 0
    # 全局 PostgreSQL 连接设置：只存「连接前缀 + 连接后缀」（主机/凭据/SSL），
    # 库名仍每个 multi-agent 各自填写。它只是配置界面的一个「显示捷径」：
    # 勾选「使用全局连接」的 multi-agent 在保存时把当前全局值写回自己的配置，
    # 因此运行时后端完全不用感知全局设置（每个 agent 仍只用自己那份 prefix/suffix）。
    global_postgres_prefix: str = ""
    global_postgres_suffix: str = ""
    # 是否已经初始化过全局连接（首次启动从已有 agent 提取，或用户在设置里保存/清空过）。
    # 用它区分「从未配置」与「用户主动清空」，避免清空后被再次自动提取。
    global_postgres_configured: bool = False
    # 全局 store 数据库名称：所有 multi-agent 的长期记忆统一存放在这个库里。
    # 默认 Long_Term_Memory；用户可在主界面「设置 → 全局配置设置」里改名。
    # 改名的后端行为是直接 ALTER DATABASE ... RENAME（不会新建再迁移）。
    global_store_database: str = DEFAULT_STORE_DATABASE
    # 全局 embedding 模型设置：与「全局 postgres 连接设置」同构——同样只是配置界面的
    # 「显示捷径」，勾选「使用全局」的 multi-agent 在保存时把全局值写回自己的配置，
    # 运行时后端只读 agent 自己那份。存 model_name/cache_folder/dims/hf_endpoint/
    # local_files_only/device/encode_normalize 等字段的字典；空字典表示未设置。
    global_embedding: dict = Field(default_factory=dict)
    # 是否已经初始化过全局 embedding（首次启动从已有 agent 提取，或用户保存/清空过）。
    global_embedding_configured: bool = False
    # 全局自动（及主动）总结设置：同样是配置界面的「显示捷径」。主 agent 与子 agent
    # 各保留一套（子 agent 只有 flush_history_tokenwise / reserve_message_round）；
    # 空字典表示未设置。这些阈值影响图编译，但每个 agent 仍只读自己那份 summary。
    global_summary: dict = Field(default_factory=dict)
    global_summary_configured: bool = False
    global_sub_summary: dict = Field(default_factory=dict)
    global_sub_summary_configured: bool = False

    def effective_store_database(self) -> str:
        """当前生效的 store 数据库名（空值兜底为默认值）。"""
        name = (self.global_store_database or "").strip()
        return name or DEFAULT_STORE_DATABASE

    @field_validator("browser_color_presets", mode="before")
    @classmethod
    def _coerce_browser_colors(cls, value):
        return _coerce_browser_color_presets(value)

    @field_validator("common_prompts", mode="before")
    @classmethod
    def _coerce_common_prompts(cls, value):
        """兼容老数据并补齐 id：接受 `list[str]` / `[{id,text}]` 两种形态，空内容条目丢弃。"""
        if not value:
            return []
        out: list[dict] = []
        taken: set[str] = set()
        for item in value:
            if isinstance(item, CommonPrompt):
                pid, text = item.id, item.text
            elif isinstance(item, dict):
                pid = str(item.get("id") or "")
                raw = item.get("text")
                text = "" if raw is None else str(raw)
            else:
                pid, text = "", str(item)
            if not text.strip():
                continue
            # 缺 id（老数据 / 前端新增）或 id 撞车：按内容生成确定性 id
            if not pid or pid in taken:
                pid = make_common_prompt_id(text, taken)
            taken.add(pid)
            out.append({"id": pid, "text": text})
        return out


def settings_path(config_dir: Path | str) -> Path:
    return Path(config_dir) / SETTINGS_FILE_NAME


def load_settings(config_dir: Path | str) -> Settings:
    path = settings_path(config_dir)
    if not path.exists():
        return Settings()
    try:
        return Settings.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return Settings()


def save_settings(config_dir: Path | str, settings: Settings) -> None:
    d = Path(config_dir)
    d.mkdir(parents=True, exist_ok=True)
    settings_path(d).write_text(
        json.dumps(settings.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def seed_global_postgres(config_dir: Path | str, configs: list) -> Settings:
    """首次运行时，把已有 multi-agent 中第一个非空连接前缀的配置提取为全局连接设置。

    - 只在「从未初始化」时执行一次；用户主动清空后不会被再次提取。
    - 没有任何带前缀的 agent 时不写入，留待下次（例如新建首个 agent）再提取。
    - 仅作为配置界面的默认值来源，不改动任何 agent 自己的配置。
    """
    settings = load_settings(config_dir)
    if settings.global_postgres_configured:
        return settings
    for cfg in configs or []:
        try:
            prefix = (cfg.postgres.prefix or "").strip()
        except Exception:  # noqa: BLE001
            continue
        if not prefix:
            continue
        settings.global_postgres_prefix = cfg.postgres.prefix
        settings.global_postgres_suffix = cfg.postgres.suffix
        settings.global_postgres_configured = True
        save_settings(config_dir, settings)
        return settings
    return settings


def seed_global_embedding(config_dir: Path | str, configs: list) -> Settings:
    """首次运行时，把已有 multi-agent 中第一个的 embedding 配置提取为全局 embedding 设置。

    - 只在「从未初始化」时执行一次；用户主动清空后不会被再次提取。
    - 没有任何 agent 时不写入，留待下次（例如新建首个 agent）再提取。
    - 仅作为配置界面的默认值来源，不改动任何 agent 自己的配置。
    """
    settings = load_settings(config_dir)
    if settings.global_embedding_configured:
        return settings
    for cfg in configs or []:
        try:
            emb = cfg.main_agent.embedding
            settings.global_embedding = {
                "model_name": emb.model_name,
                "cache_folder": emb.cache_folder,
                "dims": emb.dims,
                "hf_endpoint": emb.hf_endpoint,
                "local_files_only": emb.local_files_only,
                "device": emb.device,
                "encode_normalize": emb.encode_normalize,
            }
        except Exception:  # noqa: BLE001
            continue
        settings.global_embedding_configured = True
        save_settings(config_dir, settings)
        return settings
    return settings


def seed_global_summary(config_dir: Path | str, configs: list) -> Settings:
    """首次运行时，把已有 multi-agent 中第一个的总结设置提取为全局总结设置。

    - 只在「从未初始化」时执行一次；用户主动清空后不会被再次提取。
    - 没有任何 agent 时不写入，留待下次（例如新建首个 agent）再提取。
    - 仅作为配置界面的默认值来源，不改动任何 agent 自己的配置。
    """
    settings = load_settings(config_dir)
    if settings.global_summary_configured:
        return settings
    for cfg in configs or []:
        try:
            summ = cfg.main_agent.summary
            settings.global_summary = {
                "summarize_gap_tokenwise": summ.summarize_gap_tokenwise,
                "flush_history_tokenwise": summ.flush_history_tokenwise,
                "reserve_message_round": summ.reserve_message_round,
            }
        except Exception:  # noqa: BLE001
            continue
        settings.global_summary_configured = True
        save_settings(config_dir, settings)
        return settings
    return settings


def seed_global_sub_summary(config_dir: Path | str, configs: list) -> Settings:
    """首次运行时，把第一个含子 agent 的 multi-agent 的子 agent 总结设置提取为全局子 agent 总结设置。

    - 只在「从未初始化」时执行一次；用户主动清空后不会被再次提取。
    - 没有任何子 agent 时不写入，留待下次（例如新建首个子 agent）再提取。
    - 仅作为配置界面的默认值来源，不改动任何 agent 自己的配置。
    """
    settings = load_settings(config_dir)
    if settings.global_sub_summary_configured:
        return settings
    for cfg in configs or []:
        subs = getattr(cfg, "sub_agents", None) or []
        if not subs:
            continue
        try:
            summ = subs[0].summary
            settings.global_sub_summary = {
                "flush_history_tokenwise": summ.flush_history_tokenwise,
                "reserve_message_round": summ.reserve_message_round,
            }
        except Exception:  # noqa: BLE001
            continue
        settings.global_sub_summary_configured = True
        save_settings(config_dir, settings)
        return settings
    return settings
