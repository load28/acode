"""LLM provider backed by a locally installed Claude Code CLI.

Runs ``claude -p`` headless with a plain prompt (no tools, single turn)
and reads the JSON result. This is the default provider: if the machine
already runs Claude Code, no API key or extra configuration is needed.
"""

from __future__ import annotations

import asyncio
import json
import shutil

from .base import LlmError, LlmProvider


class ClaudeCodeProvider(LlmProvider):
    name = "claude-code"

    def __init__(self, binary: str = "claude", model: str | None = None,
                 timeout: float = 300.0):
        self.binary = binary
        self.model = model
        self.timeout = timeout

    @classmethod
    def available(cls, binary: str = "claude") -> bool:
        return shutil.which(binary) is not None

    async def complete(self, system: str, prompt: str) -> str:
        cmd = [
            self.binary,
            "-p",
            "--output-format", "json",
            "--max-turns", "1",  # pure completion: no agentic tool loop
        ]
        if self.model:
            cmd += ["--model", self.model]
        if system:
            cmd += ["--append-system-prompt", system]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise LlmError(
                f"claude CLI not found ({self.binary!r}); install Claude Code "
                "or set ACODE_LLM_PROVIDER/ACODE_LLM_MODEL/ACODE_LLM_API_KEY"
            ) from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(prompt.encode("utf-8")), timeout=self.timeout
            )
        except asyncio.TimeoutError as exc:
            proc.kill()
            raise LlmError(f"claude CLI timed out after {self.timeout}s") from exc

        if proc.returncode != 0:
            raise LlmError(
                f"claude CLI exited with {proc.returncode}: "
                f"{stderr.decode('utf-8', errors='replace')[:500]}"
            )

        text = stdout.decode("utf-8", errors="replace").strip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return text  # older CLI or plain-text output
        if isinstance(payload, dict):
            if payload.get("is_error"):
                raise LlmError(f"claude CLI returned error: {payload.get('result')}")
            result = payload.get("result")
            if isinstance(result, str):
                return result
        return text
