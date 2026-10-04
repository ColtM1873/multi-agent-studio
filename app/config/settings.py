"""全局系统设置（区别于每个 multi-agent 的配置）。

持久化在 configs/settings.json。记忆吸附（memory_attach）属于全局设置：
它影响图编译，因此在进入某个 agent 后（图已编译）不允许改动，需退回主界面。
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

SETTINGS_FILE_NAME = "settings.json"


class SensitiveEntry(BaseModel):
    """敏感信息表单的一行：name = 表单里 LLM 需要填的「名称」，value = 真实内容。"""

    name: str = ""
    value: str = ""


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
    # 常用 prompt 注入功能总开关：关闭后，会话页的「注入常用prompt」「编辑会话常用prompt」
    # 与主界面的「编辑全局常用prompt」三个按钮都不再显示；**已保存的数据一律保留**
    # （全局库 common_prompts 与会话选择都存在后端，不因开关而删除）。
    # 纯前端显示开关，不参与图编译，无需 invalidate_all。默认开启。
    common_prompt_enabled: bool = True
    # 全局常用 prompt 库（多条，每条可多行）；会话页可从中选取一条作为本会话的常用 prompt，
    # 也可「直接输入」（保存时自动追加到这里）。
    common_prompts: list[str] = Field(default_factory=list)
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
