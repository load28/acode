"""Google ADK layer.

The coding agent is composed from ADK workflow agents so orchestration
is *structural*, not model-driven — the LLM cannot skip verification:

    GenerateAgent = Sequential(
        RetrievalAgent,                      # deterministic RAG lookup
        SynthesisAgent,                      # LLM writes code
        Loop(max_iterations=N)(
            MechanicalCheckAgent,            # AST rule engine verdict
            RepairAgent,                     # LLM fixes reported violations
        ),
        MechanicalCheckAgent,                # final verdict recorded
    )

    ReviewAgent = Sequential(
        RetrievalAgent,                      # metadata + AST-similarity RAG
        MechanicalCheckAgent,                # verdict on the input code
        ReviewSynthesisAgent,                # LLM explains + proposes a fix
        FixCheckAgent,                       # verdict on the proposed fix
    )

State flows through ``session.state`` under ``acode:*`` keys. A
``ClaudeCodeLlm``/``ProviderLlm`` BaseLlm adapter is also provided so
regular ADK ``LlmAgent``s can run on the local Claude Code CLI.

Requires the ``google-adk`` extra: pip install 'acode[adk]'.
"""

from __future__ import annotations

import uuid
from typing import Any, AsyncGenerator, ClassVar

from google.adk.agents import BaseAgent, InvocationContext, LoopAgent, SequentialAgent
from google.adk.events import Event, EventActions
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types
from pydantic import ConfigDict

from ..config import AcodeConfig
from ..llm.base import LlmProvider
from ..llm.claude_code import ClaudeCodeProvider
from ..rag.store import ConventionStore
from . import steps
from .pipeline import GenerationResult, ReviewResult, _hit_summaries

# session.state keys
K_REQUEST = "acode:request"          # input dict
K_HITS = "acode:hits"                # retrieved SearchHits (objects)
K_RULES = "acode:rules"              # applicable Rule objects
K_CODE = "acode:code"                # current candidate code
K_REPORT = "acode:report"            # last CheckReport dict
K_REVIEW = "acode:review"            # review text
K_FIX_REPORT = "acode:fix_report"
K_ITERATIONS = "acode:iterations"


def _event(ctx: InvocationContext, author: str, delta: dict[str, Any],
           escalate: bool = False, text: str | None = None) -> Event:
    return Event(
        invocation_id=ctx.invocation_id,
        author=author,
        actions=EventActions(state_delta=delta, escalate=escalate),
        content=types.Content(role="model", parts=[types.Part(text=text)]) if text else None,
    )


class _AcodeAgent(BaseAgent):
    """Base for deterministic pipeline agents holding shared services."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    store: ConventionStore
    provider: LlmProvider
    config: AcodeConfig


class RetrievalAgent(_AcodeAgent):
    """Deterministic RAG lookup: metadata filter + AST fingerprint ranking."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        req = ctx.session.state[K_REQUEST]
        code_for_similarity = req.get("code") or req.get("context_code")
        hits = steps.retrieve(self.store, req["language"], req.get("metadata"),
                              code_for_similarity, self.config.retrieval_top_k)
        rules = steps.applicable_rules(self.store, req["language"], req.get("metadata"))
        yield _event(
            ctx, self.name,
            {K_HITS: hits, K_RULES: rules, K_ITERATIONS: 0},
            text=f"retrieved {len(hits)} conventions, {len(rules)} enforceable rules",
        )


class SynthesisAgent(_AcodeAgent):
    """LLM writes the first candidate."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        req = ctx.session.state[K_REQUEST]
        hits = ctx.session.state[K_HITS]
        prompt = steps.build_generate_prompt(
            req["task"], req["language"], hits, req.get("context_code"))
        reply = await self.provider.complete(steps.SYSTEM_PROMPT, prompt)
        code = steps.extract_code_block(reply) or reply
        yield _event(ctx, self.name, {K_CODE: code}, text="synthesized candidate code")


class MechanicalCheckAgent(_AcodeAgent):
    """Deterministic AST verdict. Escalates (ends the repair loop) when clean."""

    escalate_when_clean: bool = False

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        req = ctx.session.state[K_REQUEST]
        code = ctx.session.state.get(K_CODE) or req.get("code", "")
        rules = ctx.session.state[K_RULES]
        report = steps.check(code, req["language"], rules)
        yield _event(
            ctx, self.name,
            {K_REPORT: report.to_dict()},
            escalate=self.escalate_when_clean and report.passed,
            text=f"mechanical check: {'passed' if report.passed else f'{len(report.violations)} violation(s)'}",
        )


class RepairAgent(_AcodeAgent):
    """LLM repairs the reported violations (only runs while the loop is alive)."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        from ..astcore.rules import CheckReport, RuleViolation

        req = ctx.session.state[K_REQUEST]
        report_dict = ctx.session.state[K_REPORT]
        if report_dict.get("passed"):
            yield _event(ctx, self.name, {})
            return
        hits = ctx.session.state[K_HITS]
        code = ctx.session.state[K_CODE]
        report = CheckReport(
            language=report_dict["language"],
            syntax_ok=report_dict["syntax_ok"],
            checked_rules=report_dict["checked_rules"],
            violations=[
                RuleViolation(**{k: v[k] for k in (
                    "rule_id", "message", "severity", "start_line", "start_col",
                    "end_line", "end_col", "snippet")})
                for v in report_dict["violations"]
            ],
        )
        prompt = steps.build_repair_prompt(req["task"], req["language"], code, report, hits)
        reply = await self.provider.complete(steps.SYSTEM_PROMPT, prompt)
        candidate = steps.extract_code_block(reply)
        delta: dict[str, Any] = {
            K_ITERATIONS: ctx.session.state.get(K_ITERATIONS, 0) + 1,
        }
        if candidate is not None:
            delta[K_CODE] = candidate
        yield _event(ctx, self.name, delta, text="applied LLM repair")


class ReviewSynthesisAgent(_AcodeAgent):
    """LLM synthesizes a review + fix from the mechanical report."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        from ..astcore.rules import CheckReport, RuleViolation

        req = ctx.session.state[K_REQUEST]
        hits = ctx.session.state[K_HITS]
        report_dict = ctx.session.state[K_REPORT]
        report = CheckReport(
            language=report_dict["language"],
            syntax_ok=report_dict["syntax_ok"],
            checked_rules=report_dict["checked_rules"],
            violations=[
                RuleViolation(**{k: v[k] for k in (
                    "rule_id", "message", "severity", "start_line", "start_col",
                    "end_line", "end_col", "snippet")})
                for v in report_dict["violations"]
            ],
        )
        prompt = steps.build_review_prompt(
            req["code"], req["language"], report, hits, req.get("instruction"))
        reply = await self.provider.complete(steps.SYSTEM_PROMPT, prompt)
        fix = steps.extract_code_block(reply)
        delta: dict[str, Any] = {K_REVIEW: reply}
        if fix is not None:
            delta[K_CODE] = fix
        yield _event(ctx, self.name, delta, text="synthesized review")


class FixCheckAgent(_AcodeAgent):
    """Deterministic verdict on the LLM's suggested fix."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        req = ctx.session.state[K_REQUEST]
        fix = ctx.session.state.get(K_CODE)
        if not fix:
            yield _event(ctx, self.name, {K_FIX_REPORT: None})
            return
        rules = ctx.session.state[K_RULES]
        report = steps.check(fix, req["language"], rules)
        yield _event(ctx, self.name, {K_FIX_REPORT: report.to_dict()})


# ------------------------------------------------------------- assembly


def build_generate_agent(store: ConventionStore, provider: LlmProvider,
                         config: AcodeConfig) -> BaseAgent:
    deps = {"store": store, "provider": provider, "config": config}
    return SequentialAgent(
        name="acode_generate",
        sub_agents=[
            RetrievalAgent(name="retrieve", **deps),
            SynthesisAgent(name="synthesize", **deps),
            LoopAgent(
                name="verify_repair_loop",
                max_iterations=max(1, config.max_repairs),
                sub_agents=[
                    MechanicalCheckAgent(name="verify", escalate_when_clean=True, **deps),
                    RepairAgent(name="repair", **deps),
                ],
            ),
            MechanicalCheckAgent(name="final_verify", **deps),
        ],
    )


def build_review_agent(store: ConventionStore, provider: LlmProvider,
                       config: AcodeConfig) -> BaseAgent:
    deps = {"store": store, "provider": provider, "config": config}
    return SequentialAgent(
        name="acode_review",
        sub_agents=[
            RetrievalAgent(name="retrieve", **deps),
            MechanicalCheckAgent(name="verify_input", **deps),
            ReviewSynthesisAgent(name="review", **deps),
            FixCheckAgent(name="verify_fix", **deps),
        ],
    )


# --------------------------------------------------------------- runner


class AdkCodingAgent:
    """Runs the ADK agent graphs and maps session state to result types."""

    def __init__(self, store: ConventionStore, provider: LlmProvider,
                 config: AcodeConfig | None = None):
        self.store = store
        self.provider = provider
        self.config = config or AcodeConfig()

    async def _run(self, agent: BaseAgent, request: dict[str, Any]) -> dict[str, Any]:
        runner = InMemoryRunner(agent=agent, app_name="acode")
        session = await runner.session_service.create_session(
            app_name="acode", user_id="acode",
            session_id=uuid.uuid4().hex,
            state={K_REQUEST: request},
        )
        message = types.Content(
            role="user", parts=[types.Part(text=request.get("task") or "review")])
        async for _ in runner.run_async(
            user_id="acode", session_id=session.id, new_message=message
        ):
            pass
        final = await runner.session_service.get_session(
            app_name="acode", user_id="acode", session_id=session.id)
        return dict(final.state)

    async def generate(self, task: str, language: str,
                       metadata: dict[str, Any] | None = None,
                       context_code: str | None = None) -> GenerationResult:
        from ..astcore.parser import normalize_language

        language = normalize_language(language)
        agent = build_generate_agent(self.store, self.provider, self.config)
        state = await self._run(agent, {
            "task": task, "language": language,
            "metadata": metadata, "context_code": context_code,
        })
        report = state.get(K_REPORT) or {"passed": False, "violations": []}
        iterations = state.get(K_ITERATIONS, 0)
        return GenerationResult(
            code=state.get(K_CODE, ""),
            verified=bool(report.get("passed")),
            report=report,
            conventions=_hit_summaries(state.get(K_HITS, [])),
            iterations=iterations,
            notes="" if report.get("passed") else (
                f"mechanical verification still failing after {iterations} repair iteration(s)"
            ),
        )

    async def review(self, code: str, language: str,
                     metadata: dict[str, Any] | None = None,
                     instruction: str | None = None) -> ReviewResult:
        from ..astcore.parser import normalize_language

        language = normalize_language(language)
        agent = build_review_agent(self.store, self.provider, self.config)
        state = await self._run(agent, {
            "code": code, "language": language,
            "metadata": metadata, "instruction": instruction,
        })
        report = state.get(K_REPORT) or {"violations": []}
        fix_report = state.get(K_FIX_REPORT)
        return ReviewResult(
            review=state.get(K_REVIEW, ""),
            violations=report.get("violations", []),
            suggested_fix=state.get(K_CODE),
            fix_verified=bool(fix_report and fix_report.get("passed")),
            fix_report=fix_report,
            conventions=_hit_summaries(state.get(K_HITS, [])),
        )


# ------------------------------------------------- BaseLlm adapters


def _flatten_request(llm_request: LlmRequest) -> tuple[str, str]:
    """Collapse an ADK LlmRequest into (system, prompt) text."""
    system = ""
    if llm_request.config and llm_request.config.system_instruction:
        si = llm_request.config.system_instruction
        system = si if isinstance(si, str) else "\n".join(
            p.text or "" for p in getattr(si, "parts", []) or [])
    chunks: list[str] = []
    for content in llm_request.contents or []:
        role = content.role or "user"
        for part in content.parts or []:
            if part.text:
                chunks.append(f"[{role}] {part.text}")
    return system, "\n\n".join(chunks)


class ProviderLlm(BaseLlm):
    """Lets any ADK LlmAgent run on an acode LlmProvider (single-shot)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    provider: LlmProvider

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        system, prompt = _flatten_request(llm_request)
        text = await self.provider.complete(system, prompt)
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=text)])
        )


class ClaudeCodeLlm(ProviderLlm):
    """ADK model id 'claude-code[/<model>]' backed by the local claude CLI."""

    supported_models_regex: ClassVar[str] = r"claude-code.*"

    @classmethod
    def supported_models(cls) -> list[str]:
        return [cls.supported_models_regex]

    @classmethod
    def from_model_id(cls, model: str = "claude-code") -> "ClaudeCodeLlm":
        _, _, cli_model = model.partition("/")
        provider = ClaudeCodeProvider(model=cli_model or None)
        return cls(model=model, provider=provider)
