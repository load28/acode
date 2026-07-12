import pytest

pytest.importorskip("google.adk")

from acode.agent.adk import AdkCodingAgent  # noqa: E402
from acode.config import AcodeConfig  # noqa: E402
from tests.test_pipeline import BAD_CODE, GOOD_CODE  # noqa: E402


def _agent(seeded_store, provider):
    config = AcodeConfig()
    config.max_repairs = 3
    return AdkCodingAgent(seeded_store, provider, config)


class TestAdkGenerate:
    async def test_clean_first_try(self, seeded_store, fake_provider_factory):
        provider = fake_provider_factory([GOOD_CODE])
        result = await _agent(seeded_store, provider).generate(
            "write a greet function", "python")
        assert result.verified
        assert "logger.info" in result.code

    async def test_repair_loop(self, seeded_store, fake_provider_factory):
        provider = fake_provider_factory([BAD_CODE, GOOD_CODE])
        result = await _agent(seeded_store, provider).generate(
            "write a greet function", "python")
        assert result.verified
        assert result.iterations == 1

    async def test_gives_up_and_reports(self, seeded_store, fake_provider_factory):
        provider = fake_provider_factory([BAD_CODE] * 5)
        result = await _agent(seeded_store, provider).generate(
            "write a greet function", "python")
        assert not result.verified
        assert result.report["violations"]


class TestAdkReview:
    async def test_review_flow(self, seeded_store, fake_provider_factory):
        provider = fake_provider_factory([
            "Violates py-no-print.\n" + GOOD_CODE])
        result = await _agent(seeded_store, provider).review(
            "def greet(name):\n    print(name)\n", "python")
        assert any(v["rule_id"] == "py-no-print" for v in result.violations)
        assert result.fix_verified


class TestClaudeCodeLlm:
    async def test_provider_llm_adapts_requests(self, fake_provider_factory):
        from google.adk.models.llm_request import LlmRequest
        from google.genai import types

        from acode.agent.adk import ProviderLlm

        provider = fake_provider_factory(["hello from fake"])
        llm = ProviderLlm(model="fake-model", provider=provider)
        request = LlmRequest(
            contents=[types.Content(role="user", parts=[types.Part(text="hi")])],
            config=types.GenerateContentConfig(system_instruction="be brief"),
        )
        responses = [r async for r in llm.generate_content_async(request)]
        assert responses[0].content.parts[0].text == "hello from fake"
        system, prompt = provider.calls[0]
        assert system == "be brief"
        assert "hi" in prompt
