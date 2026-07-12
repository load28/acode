from acode.agent.pipeline import CodingPipeline
from acode.config import AcodeConfig

BAD_CODE = "```python\ndef greet(name):\n    print(name)\n```"
GOOD_CODE = (
    "```python\n"
    "import logging\n\n"
    "logger = logging.getLogger(__name__)\n\n\n"
    "def greet(name):\n"
    '    """Log a greeting."""\n'
    "    logger.info(\"hello %s\", name)\n"
    "```"
)


def _pipeline(seeded_store, provider):
    config = AcodeConfig()
    config.max_repairs = 3
    return CodingPipeline(seeded_store, provider, config)


class TestGenerate:
    async def test_clean_first_try(self, seeded_store, fake_provider_factory):
        provider = fake_provider_factory([GOOD_CODE])
        result = await _pipeline(seeded_store, provider).generate(
            "write a greet function", "python")
        assert result.verified
        assert result.iterations == 0
        assert "logger.info" in result.code
        assert result.report["passed"]

    async def test_repair_loop_fixes_violations(self, seeded_store, fake_provider_factory):
        provider = fake_provider_factory([BAD_CODE, GOOD_CODE])
        result = await _pipeline(seeded_store, provider).generate(
            "write a greet function", "python")
        assert result.verified
        assert result.iterations == 1
        # the repair prompt must carry the mechanical verdict
        repair_prompt = provider.calls[1][1]
        assert "py-no-print" in repair_prompt
        assert "Mechanical AST check result" in repair_prompt

    async def test_gives_up_after_max_repairs(self, seeded_store, fake_provider_factory):
        provider = fake_provider_factory([BAD_CODE] * 4)
        result = await _pipeline(seeded_store, provider).generate(
            "write a greet function", "python")
        assert not result.verified
        assert result.iterations == 3
        assert result.report["violations"]
        assert "still failing" in result.notes

    async def test_conventions_reach_the_prompt(self, seeded_store, fake_provider_factory):
        provider = fake_provider_factory([GOOD_CODE])
        await _pipeline(seeded_store, provider).generate(
            "write a greet function", "python", metadata={"category": "logging"})
        prompt = provider.calls[0][1]
        assert "py-no-print" in prompt
        assert "MUST follow" in prompt


class TestReview:
    async def test_mechanical_verdict_precedes_llm(self, seeded_store, fake_provider_factory):
        provider = fake_provider_factory([
            "The code violates py-no-print.\n" + GOOD_CODE])
        result = await _pipeline(seeded_store, provider).review(
            "def greet(name):\n    print(name)\n", "python")
        rule_ids = {v["rule_id"] for v in result.violations}
        assert "py-no-print" in rule_ids
        # LLM prompt received the deterministic report as ground truth
        prompt = provider.calls[0][1]
        assert "ground truth" in prompt
        assert result.suggested_fix is not None
        assert result.fix_verified
        assert result.fix_report["passed"]

    async def test_unfixed_suggestion_is_flagged(self, seeded_store, fake_provider_factory):
        provider = fake_provider_factory(["Looks fine to me.\n" + BAD_CODE])
        result = await _pipeline(seeded_store, provider).review(
            "def greet(name):\n    print(name)\n", "python")
        # the LLM lied — mechanical re-check catches it
        assert not result.fix_verified
        assert result.fix_report["violations"]
