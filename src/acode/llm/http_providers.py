"""API-key providers for machines without a local Claude Code install.

    AnthropicProvider     Anthropic Messages API (api.anthropic.com)
    OpenAICompatProvider  any /chat/completions server: OpenAI, OpenRouter,
                          Ollama, vLLM, LM Studio, Groq, ... via base_url
    LiteLlmProvider       optional, routes to 100+ providers if the
                          `litellm` package is installed

All are configured purely through provider/model/api_key/base_url — no
code changes needed to swap providers.
"""

from __future__ import annotations

import httpx

from .base import LlmError, LlmProvider

_TIMEOUT_CONNECT = 30.0


class AnthropicProvider(LlmProvider):
    name = "anthropic"

    def __init__(self, model: str, api_key: str, base_url: str | None = None,
                 timeout: float = 300.0, max_tokens: int = 8192):
        if not api_key:
            raise LlmError("anthropic provider needs ACODE_LLM_API_KEY")
        self.model = model or "claude-sonnet-5"
        self.api_key = api_key
        self.base_url = (base_url or "https://api.anthropic.com").rstrip("/")
        self.timeout = timeout
        self.max_tokens = max_tokens

    async def complete(self, system: str, prompt: str) -> str:
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            payload["system"] = system
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=_TIMEOUT_CONNECT)
        ) as client:
            resp = await client.post(
                f"{self.base_url}/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                },
                json=payload,
            )
        if resp.status_code != 200:
            raise LlmError(f"anthropic API {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
        return "".join(parts)


class OpenAICompatProvider(LlmProvider):
    name = "openai"

    def __init__(self, model: str, api_key: str | None, base_url: str | None = None,
                 timeout: float = 300.0):
        if not model:
            raise LlmError("openai-compatible provider needs ACODE_LLM_MODEL")
        self.model = model
        self.api_key = api_key
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.timeout = timeout

    async def complete(self, system: str, prompt: str) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=_TIMEOUT_CONNECT)
        ) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json={"model": self.model, "messages": messages},
            )
        if resp.status_code != 200:
            raise LlmError(f"openai-compatible API {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmError(f"unexpected completion payload: {data}") from exc


class LiteLlmProvider(LlmProvider):
    name = "litellm"

    def __init__(self, model: str, api_key: str | None = None,
                 base_url: str | None = None, timeout: float = 300.0):
        try:
            import litellm  # noqa: F401
        except ImportError as exc:
            raise LlmError(
                "litellm is not installed; pip install 'acode[litellm]'"
            ) from exc
        if not model:
            raise LlmError("litellm provider needs ACODE_LLM_MODEL (e.g. 'gemini/gemini-2.5-pro')")
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout

    async def complete(self, system: str, prompt: str) -> str:
        import litellm

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        response = await litellm.acompletion(
            model=self.model,
            messages=messages,
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
        )
        content = response.choices[0].message.content
        return content or ""
