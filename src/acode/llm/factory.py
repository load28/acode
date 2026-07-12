from __future__ import annotations

from ..config import AcodeConfig
from .base import LlmError, LlmProvider
from .claude_code import ClaudeCodeProvider
from .http_providers import AnthropicProvider, LiteLlmProvider, OpenAICompatProvider


def create_provider(config: AcodeConfig | None = None) -> LlmProvider:
    """Resolve the LLM provider from configuration.

    Resolution order when ACODE_LLM_PROVIDER is unset:
      1. local Claude Code CLI, if installed (the intended default)
      2. anthropic API, if an API key is configured
      3. openai-compatible API, if a model is configured
    """
    config = config or AcodeConfig()
    provider = (config.llm_provider or "").strip().lower()

    if provider in ("claude-code", "claude_code", "claude"):
        return ClaudeCodeProvider(config.claude_bin, config.llm_model, config.llm_timeout)
    if provider == "anthropic":
        return AnthropicProvider(config.llm_model or "", config.llm_api_key or "",
                                 config.llm_base_url, config.llm_timeout)
    if provider in ("openai", "openai-compat", "openai_compat"):
        return OpenAICompatProvider(config.llm_model or "", config.llm_api_key,
                                    config.llm_base_url, config.llm_timeout)
    if provider == "litellm":
        return LiteLlmProvider(config.llm_model or "", config.llm_api_key,
                               config.llm_base_url, config.llm_timeout)
    if provider:
        raise LlmError(
            f"unknown ACODE_LLM_PROVIDER {config.llm_provider!r}; "
            "expected claude-code | anthropic | openai | litellm"
        )

    # auto-detection
    if ClaudeCodeProvider.available(config.claude_bin):
        return ClaudeCodeProvider(config.claude_bin, config.llm_model, config.llm_timeout)
    if config.llm_api_key and (config.llm_model or "").startswith("claude"):
        return AnthropicProvider(config.llm_model or "", config.llm_api_key,
                                 config.llm_base_url, config.llm_timeout)
    if config.llm_model:
        return OpenAICompatProvider(config.llm_model, config.llm_api_key,
                                    config.llm_base_url, config.llm_timeout)
    raise LlmError(
        "no LLM available: install Claude Code (claude CLI), or set "
        "ACODE_LLM_PROVIDER + ACODE_LLM_MODEL + ACODE_LLM_API_KEY "
        "(+ ACODE_LLM_BASE_URL for self-hosted/OpenAI-compatible servers)"
    )
