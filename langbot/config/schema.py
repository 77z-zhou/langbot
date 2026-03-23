"""Configuration schema using Pydantic.

Compatible with nanobot configuration format for easy migration.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from pydantic_settings import BaseSettings


class Base(BaseModel):
    """Base model that accepts both camelCase and snake_case keys."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class HITLConfig(Base):
    """Human-in-the-loop (HITL) configuration for tool approval."""

    mode: Literal["all", "none", "custom"] = "custom"
    # HITL 模式：
    # - "all": 所有工具调用前都需要人工确认
    # - "none": 所有工具调用都不需要人工确认
    # - "custom": 根据 tools 配置决定哪些工具需要确认

    tools: dict[str, bool] = Field(
        default_factory=lambda: {
            # 默认需要确认的工具（危险操作）
            "execute": True,
            "write_file": True,
            "edit_file": True,
            # 默认不需要确认的工具（安全操作）
            "ls": False,
            "read_file": False,
            "glob": False,
            "grep": False,
            "task": False,
            "write_todos": False,
        }
    )

    exclude: list[str] = Field(
        default_factory=list
    )  # 排除的工具列表，这些工具不受 HITL 控制

    @property
    def is_all_mode(self) -> bool:
        """是否为全部确认模式."""
        return self.mode == "all"

    @property
    def is_none_mode(self) -> bool:
        """是否为无需确认模式."""
        return self.mode == "none"

    def needs_approval(self, tool_name: str) -> bool:
        """
        判断指定工具是否需要人工确认.

        Args:
            tool_name: 工具名称

        Returns:
            True 如果需要确认，False 否则
        """
        # 如果在排除列表中，直接返回 False
        if tool_name in self.exclude:
            return False

        # 根据模式判断
        if self.mode == "all":
            return True
        if self.mode == "none":
            return False

        # custom 模式：查看 tools 配置
        return self.tools.get(tool_name, False)


class ChannelsConfig(Base):
    """Configuration for chat channels.

    Built-in and plugin channel configs are stored as extra fields (dicts).
    Each channel parses its own config in __init__.
    """

    model_config = ConfigDict(extra="allow")

    send_progress: bool = True     # 显示模型中间输出（有工具调用时的回复）
    send_tool_hints: bool = True  # 显示工具调用提示

class AgentDefaults(Base):
    """Default agent configuration."""

    workspace: str = "~/.langbot/workspace"
    model: str = "deepseek-chat"
    provider: str = (
        "deepseek"  # Provider name (e.g. "anthropic", "deepseek") or "auto" for auto-detection
    )
    max_tokens: int = 8192
    context_window_tokens: int = 200_000  # 保留：nanobot memory.py 使用，langbot 未来可能需要
    temperature: float = 0.1
    restrict_to_workspace: bool = True  # 限制文件和命令访问在工作目录内（安全模式）
    hitl: "HITLConfig" = Field(default_factory=lambda: HITLConfig())  # 人工干预配置
    skills: list[str] = Field(
        default_factory=lambda: ["/skills/"]
    )  # 技能源路径列表（相对于 workspace）


class AgentsConfig(Base):
    """Agent configuration."""

    defaults: AgentDefaults = Field(default_factory=AgentDefaults)


class ProviderConfig(Base):
    """LLM provider configuration."""

    api_key: str = ""
    api_base: str = ""
    extra_headers: dict[str, str] | None = None


class ProvidersConfig(Base):
    """Configuration for LLM providers."""

    custom: ProviderConfig = Field(default_factory=ProviderConfig)  # Any OpenAI-compatible endpoint
    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)
    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    deepseek: ProviderConfig = Field(default_factory=ProviderConfig)
    groq: ProviderConfig = Field(default_factory=ProviderConfig)
    zhipu: ProviderConfig = Field(default_factory=ProviderConfig)
    ollama: ProviderConfig = Field(default_factory=ProviderConfig)  # Ollama local models
    gemini: ProviderConfig = Field(default_factory=ProviderConfig)
    moonshot: ProviderConfig = Field(default_factory=ProviderConfig)
    minimax: ProviderConfig = Field(default_factory=ProviderConfig)


class GatewayConfig(Base):
    """Gateway/server configuration."""

    host: str = "0.0.0.0"
    port: int = 18790


class WebSearchConfig(Base):
    """Web search tool configuration."""

    provider: str = "tavily"  # tavily, brave, duckduckgo, searxng, jina
    api_key: str = ""
    base_url: str = ""  # SearXNG base URL
    max_results: int = 5


class WebToolsConfig(Base):
    """Web tools configuration."""

    proxy: str | None = (
        None  # HTTP/SOCKS5 proxy URL, e.g. "http://127.0.0.1:7890" or "socks5://127.0.0.1:1080"
    )
    search: WebSearchConfig = Field(default_factory=WebSearchConfig)


class ExecToolConfig(Base):
    """Shell exec tool configuration."""

    timeout: int = 60
    path_append: str = ""


class MCPServerConfig(Base):
    """MCP server connection configuration (stdio or HTTP)."""

    type: Literal["stdio", "sse", "streamableHttp"] | None = None  # auto-detected if omitted
    command: str = ""  # Stdio: command to run (e.g. "npx")
    args: list[str] = Field(default_factory=list)  # Stdio: command arguments
    env: dict[str, str] = Field(default_factory=dict)  # Stdio: extra env vars
    url: str = ""  # HTTP/SSE: endpoint URL
    headers: dict[str, str] = Field(default_factory=dict)  # HTTP/SSE: custom headers
    tool_timeout: int = 30  # seconds before a tool call is cancelled
    enabled_tools: list[str] = Field(default_factory=lambda: ["*"])  # Only register these tools
    enabled: bool = False  # Whether this MCP server is enabled


class ToolsConfig(Base):
    """Tools configuration."""

    web: WebToolsConfig = Field(default_factory=WebToolsConfig)
    exec: ExecToolConfig = Field(default_factory=ExecToolConfig)
    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)


class Config(BaseSettings):
    """Root configuration for langbot."""

    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)

    @property
    def workspace_path(self) -> Path:
        """Get expanded workspace path."""
        return Path(self.agents.defaults.workspace).expanduser()

    def get_model_init_params(
        self, model: str | None = None
    ) -> dict[str, str | int | float | None]:
        """
        Get all parameters needed to initialize a LangChain chat model.

        Directly reads provider config from the configured provider name.
        LangChain's init_chat_model handles provider detection automatically.

        Returns a dict with:
        - model: Model name (e.g. "deepseek-chat")
        - api_key: API key for the provider (if configured)
        - base_url: API base URL (if configured)
        - temperature: Temperature setting
        - max_tokens: Max tokens setting
        """
        # Get provider name from config
        provider_name = self.agents.defaults.provider

        # Get provider config directly
        provider_config: ProviderConfig = getattr(self.providers, provider_name, None)

        # Use provided model or default
        model_name = model or self.agents.defaults.model

        params = {
            "model": model_name,
            "temperature": self.agents.defaults.temperature,
            "max_tokens": self.agents.defaults.max_tokens,
        }

        # Add API key if available
        if provider_config and provider_config.api_key:
            params["api_key"] = provider_config.api_key

        # Add base URL if available (LangChain uses base_url)
        if provider_config and provider_config.api_base:
            params["base_url"] = provider_config.api_base

        return params

    model_config = ConfigDict(env_prefix="LANGBOT_", env_nested_delimiter="__")
