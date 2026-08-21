"""多 agent 配置模型（pydantic v2）。

一份 MultiAgentConfig 就是"一个 multi-agent"的全部定义，
落盘为 configs/<agent_id>.json。用户通过 GUI 表单填写，不直接接触 JSON。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

DEFAULT_HTML_REPORT_PROMPT = (
    "不错，请把你的回答输出为一份html文件，使用你所拥有的FileTools，自拟文件名。"
)


class PostgresConfig(BaseModel):
    prefix: str = ""
    suffix: str = "?sslmode=disable"
    store_database: str = "postgres"
    # 身份绑定：会话历史归属哪个库。创建后不可改。
    checkpoint_database: str
    store_namespace: tuple[str, ...] = ("Li", "memories")

    def store_conn_string(self) -> str:
        return self.prefix + self.store_database + self.suffix

    def checkpoint_conn_string(self) -> str:
        return self.prefix + self.checkpoint_database + self.suffix


class EmbeddingConfig(BaseModel):
    model_name: str = "BAAI/bge-m3"
    device: str = "cpu"
    local_files_only: bool = False
    cache_folder: str = ""
    encode_normalize: bool = True
    dims: int = 1024
    hf_endpoint: str = "https://hf-mirror.com"


class SummaryConfig(BaseModel):
    """主 agent 的阶段性总结/清空历史阈值。"""

    summarize_gap_tokenwise: int = 8 * 10000
    flush_history_tokenwise: int = 60 * 10000
    reserve_message_round: int = 4


class SubSummaryConfig(BaseModel):
    """子 agent 的清空历史阈值。"""

    flush_history_tokenwise: int = 20 * 10000
    reserve_message_round: int = 4


class ModelConfig(BaseModel):
    """LLM 生成配置：模型来源分支 + 可选采样参数。

    provider_mode 决定 init_chat_model 的构造方式：
    - official: 直接用 ``provider:model`` 前缀走内置 provider。
    - openai_compatible: 固定 model_provider="openai" 并携带 base_url，
      适配任意符合 OpenAI Chat Completion 协议的第三方模型。
    采样参数为 None 表示不传（沿用模型默认值）。
    """

    provider_mode: Literal["official", "openai_compatible"] = "official"
    base_url: str = ""
    temperature: float | None = None
    top_k: int | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    repetition_penalty: float | None = None


class FileToolsConfig(BaseModel):
    root_dir: str = ""


class MCPServerConfig(BaseModel):
    name: str
    transport: Literal["http", "stdio"] = "http"
    url: str | None = None
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class MainAgentConfig(BaseModel):
    system_prompt: str
    api_key: str
    llm_provider_name: str = "deepseek:deepseek-v4-pro"
    model: ModelConfig = Field(default_factory=ModelConfig)
    file_tools: FileToolsConfig = Field(default_factory=FileToolsConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    summary: SummaryConfig = Field(default_factory=SummaryConfig)
    html_report: bool = False
    html_report_prompt: str = DEFAULT_HTML_REPORT_PROMPT
    react_prompt: bool = True


class SubAgentConfig(BaseModel):
    name: str
    description: str
    system_prompt: str
    api_key: str
    llm_provider_name: str = "deepseek:deepseek-v4-pro"
    model: ModelConfig = Field(default_factory=ModelConfig)
    mcp_servers: list[MCPServerConfig] = Field(default_factory=list)
    # 消息通道键名，由系统自动生成（父子消息键必须唯一），用户不填。
    state_messages_key: str | None = None
    summary: SubSummaryConfig = Field(default_factory=SubSummaryConfig)
    react_prompt: bool = True


class OutputConfig(BaseModel):
    stream_output_dir: str = ""


class MultiAgentConfig(BaseModel):
    agent_id: str
    name: str
    postgres: PostgresConfig
    main_agent: MainAgentConfig
    sub_agents: list[SubAgentConfig] = Field(default_factory=list)
    output: OutputConfig = Field(default_factory=OutputConfig)

    @property
    def checkpoint_conn_string(self) -> str:
        return self.postgres.checkpoint_conn_string()

    @property
    def store_conn_string(self) -> str:
        return self.postgres.store_conn_string()
