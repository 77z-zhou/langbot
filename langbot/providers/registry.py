"""
Provider Registry — single source of truth for LLM provider metadata.

This registry maps provider names to their LangChain integration classes
and configuration requirements.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProviderSpec:
    """One LLM provider's metadata for LangChain integration."""

    # identity
    name: str  # config field name, e.g. "deepseek"
    keywords: tuple[str, ...]  # model-name keywords for matching (lowercase)
    env_key: str  # Environment variable for API key
    display_name: str = ""  # shown in status

    # LangChain integration
    langchain_class: str = ""  # Module path, e.g. "langchain_anthropic.ChatAnthropic"
    is_openai_compatible: bool = False  # Uses ChatOpenAI with custom base_url
    default_api_base: str = ""  # fallback base URL

    # Provider characteristics
    is_local: bool = False  # local deployment (Ollama, vLLM)
    supports_prompt_caching: bool = False
    supports_streaming: bool = True

    @property
    def label(self) -> str:
        return self.display_name or self.name.title()


# ---------------------------------------------------------------------------
# PROVIDERS — the registry
# ---------------------------------------------------------------------------

PROVIDERS: tuple[ProviderSpec, ...] = (
    # === Custom (OpenAI-compatible endpoint) ============================
    ProviderSpec(
        name="custom",
        keywords=(),
        env_key="",
        display_name="Custom",
        langchain_class="",
        is_openai_compatible=True,
    ),

    # === Anthropic ======================================================
    ProviderSpec(
        name="anthropic",
        keywords=("anthropic", "claude"),
        env_key="ANTHROPIC_API_KEY",
        display_name="Anthropic",
        langchain_class="langchain_anthropic.ChatAnthropic",
        supports_prompt_caching=True,
    ),

    # === OpenAI =========================================================
    ProviderSpec(
        name="openai",
        keywords=("openai", "gpt"),
        env_key="OPENAI_API_KEY",
        display_name="OpenAI",
        langchain_class="langchain_openai.ChatOpenAI",
    ),

    # === DeepSeek =======================================================
    ProviderSpec(
        name="deepseek",
        keywords=("deepseek",),
        env_key="DEEPSEEK_API_KEY",
        display_name="DeepSeek",
        langchain_class="langchain_deepseek.ChatDeepSeek",
        default_api_base="https://api.deepseek.com",
    ),

    # === Gemini =========================================================
    ProviderSpec(
        name="gemini",
        keywords=("gemini",),
        env_key="GEMINI_API_KEY",
        display_name="Gemini",
        langchain_class="langchain_google_genai.ChatGemini",
    ),

    # === Zhipu ==========================================================
    ProviderSpec(
        name="zhipu",
        keywords=("zhipu", "glm"),
        env_key="ZHIPUAI_API_KEY",
        display_name="Zhipu AI",
        langchain_class="langchain_community.chat_models.ChatZhipuAI",
    ),

    # === Moonshot =======================================================
    ProviderSpec(
        name="moonshot",
        keywords=("moonshot", "kimi"),
        env_key="MOONSHOT_API_KEY",
        display_name="Moonshot",
        langchain_class="langchain_openai.ChatOpenAI",
        is_openai_compatible=True,
        default_api_base="https://api.moonshot.cn/v1",
    ),

    # === MiniMax ========================================================
    ProviderSpec(
        name="minimax",
        keywords=("minimax",),
        env_key="MINIMAX_API_KEY",
        display_name="MiniMax",
        langchain_class="langchain_community.chat_models.MiniMaxChat",
    ),

    # === Groq ===========================================================
    ProviderSpec(
        name="groq",
        keywords=("groq",),
        env_key="GROQ_API_KEY",
        display_name="Groq",
        langchain_class="langchain_groq.ChatGroq",
    ),

    # === Local deployment ===============================================
    ProviderSpec(
        name="ollama",
        keywords=("ollama",),
        env_key="",
        display_name="Ollama",
        langchain_class="langchain_ollama.ChatOllama",
        is_local=True,
        default_api_base="http://localhost:11434",
    ),
)


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


def find_by_model(model: str) -> ProviderSpec | None:
    """Match a provider by model-name keyword (case-insensitive)."""
    model_lower = model.lower()
    model_normalized = model_lower.replace("-", "_")
    model_prefix = model_lower.split("/", 1)[0] if "/" in model_lower else ""
    normalized_prefix = model_prefix.replace("-", "_")

    # Prefer explicit provider prefix
    for spec in PROVIDERS:
        if model_prefix and normalized_prefix == spec.name:
            return spec

    # Match by keyword
    for spec in PROVIDERS:
        if any(
            kw in model_lower or kw.replace("-", "_") in model_normalized
            for kw in spec.keywords
        ):
            return spec
    return None


def find_by_name(name: str) -> ProviderSpec | None:
    """Find a provider spec by config field name, e.g. "deepseek"."""
    for spec in PROVIDERS:
        if spec.name == name:
            return spec
    return None


def get_model_for_provider(provider_name: str, model: str) -> str:
    """Get the properly prefixed model name for a provider.

    For example:
    - deepseek + deepseek-chat → deepseek-chat (no prefix needed with official package)
    - openai + gpt-4 → gpt-4 (no prefix needed)
    - gemini + gemini-pro → gemini/gemini-pro
    """
    spec = find_by_name(provider_name)
    if not spec:
        return model

    # Check if model already has provider prefix
    if "/" in model:
        prefix = model.split("/", 1)[0]
        if prefix == spec.name:
            return model

    # Add provider prefix if needed
    # langchain-deepseek handles prefixing automatically
    # other providers may need explicit prefix
    if spec.name in ("gemini", "zhipu", "moonshot", "minimax"):
        return f"{spec.name}/{model}"

    return model
