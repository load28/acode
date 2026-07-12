"""Runtime configuration, resolved from environment variables.

Environment variables:
    ACODE_DB              path to the convention SQLite database
                          (default: ~/.acode/conventions.db)
    ACODE_LLM_PROVIDER    claude-code | anthropic | openai | litellm
                          (default: claude-code if the `claude` binary exists)
    ACODE_LLM_MODEL       model id for the chosen provider
    ACODE_LLM_API_KEY     API key for anthropic/openai/litellm providers
    ACODE_LLM_BASE_URL    base URL override (OpenAI-compatible servers,
                          Ollama, vLLM, OpenRouter, ...)
    ACODE_CLAUDE_BIN      claude CLI binary (default: claude)
    ACODE_MAX_REPAIRS     max LLM repair iterations after mechanical
                          verification fails (default: 3)
    ACODE_LLM_TIMEOUT     seconds to wait for one LLM call (default: 300)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _default_db_path() -> str:
    return os.environ.get(
        "ACODE_DB", str(Path.home() / ".acode" / "conventions.db")
    )


@dataclass
class AcodeConfig:
    db_path: str = field(default_factory=_default_db_path)
    llm_provider: str | None = field(
        default_factory=lambda: os.environ.get("ACODE_LLM_PROVIDER")
    )
    llm_model: str | None = field(
        default_factory=lambda: os.environ.get("ACODE_LLM_MODEL")
    )
    llm_api_key: str | None = field(
        default_factory=lambda: os.environ.get("ACODE_LLM_API_KEY")
    )
    llm_base_url: str | None = field(
        default_factory=lambda: os.environ.get("ACODE_LLM_BASE_URL")
    )
    claude_bin: str = field(
        default_factory=lambda: os.environ.get("ACODE_CLAUDE_BIN", "claude")
    )
    max_repairs: int = field(
        default_factory=lambda: int(os.environ.get("ACODE_MAX_REPAIRS", "3"))
    )
    llm_timeout: float = field(
        default_factory=lambda: float(os.environ.get("ACODE_LLM_TIMEOUT", "300"))
    )
    retrieval_top_k: int = field(
        default_factory=lambda: int(os.environ.get("ACODE_TOP_K", "8"))
    )
