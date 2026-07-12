from __future__ import annotations

from abc import ABC, abstractmethod


class LlmError(RuntimeError):
    pass


class LlmProvider(ABC):
    """Minimal completion interface: system + user prompt -> text.

    The agent pipeline is deliberately deterministic-first — the LLM is
    only asked to synthesize, so a single-shot completion interface is
    all that is needed (no tool-calling protocol required).
    """

    name: str = "base"

    @abstractmethod
    async def complete(self, system: str, prompt: str) -> str:
        ...
