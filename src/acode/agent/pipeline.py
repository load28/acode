"""Plain-asyncio coding pipeline.

Flow (generation):
    retrieve (metadata)         -> deterministic
    synthesize (LLM)            -> the only stochastic step
    verify (AST rule engine)    -> deterministic
    repair loop (LLM + verify)  -> bounded, each verdict deterministic

Flow (review/modify):
    retrieve (metadata + AST similarity of the input code)
    verify input mechanically
    synthesize review + fix (LLM, fed the mechanical report as ground truth)
    verify the suggested fix mechanically

The final answer always carries the mechanical report, so a consumer
(e.g. Claude Code over MCP) can trust `verified: true` without trusting
the LLM's self-assessment.

This module is framework-free; `acode.agent.adk` wraps the same steps
in Google ADK agents and is used when google-adk is installed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ..astcore.parser import normalize_language, resolve_dialect
from ..astcore.rules import CheckReport
from ..config import AcodeConfig
from ..llm.base import LlmProvider
from ..rag.store import ConventionStore, SearchHit
from . import steps

logger = logging.getLogger("acode.pipeline")


def _emit(trace: list[dict[str, Any]], stage: str, message: str,
          **data: Any) -> None:
    """Record a pipeline event: structured (trace) + human-readable (log)."""
    trace.append({"stage": stage, "message": message, **data})
    logger.info("[%s] %s", stage, message)


def _verdict_line(report: CheckReport) -> str:
    if report.passed:
        return f"PASS — {len(report.checked_rules)} rule(s) checked, 0 violations"
    parts: list[str] = []
    if not report.syntax_ok:
        parts.append("SYNTAX ERROR (code does not parse)")
    parts.extend(
        f"line {v.start_line} [{v.rule_id}] {v.message}" for v in report.violations
    )
    return f"FAIL — {len(report.violations)} violation(s): " + "; ".join(parts)


def _emit_verify(trace: list[dict[str, Any]], report: CheckReport,
                 iteration: int) -> None:
    _emit(trace, f"verify#{iteration}", _verdict_line(report),
          iteration=iteration, report=report.to_dict())


@dataclass
class GenerationResult:
    code: str
    verified: bool
    report: dict[str, Any]
    conventions: list[dict[str, Any]]
    iterations: int
    notes: str = ""
    trace: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "verified": self.verified,
            "mechanical_report": self.report,
            "conventions_applied": self.conventions,
            "repair_iterations": self.iterations,
            "notes": self.notes,
            "trace": self.trace,
        }


@dataclass
class ReviewResult:
    review: str
    violations: list[dict[str, Any]]
    suggested_fix: str | None
    fix_verified: bool
    fix_report: dict[str, Any] | None
    conventions: list[dict[str, Any]] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "review": self.review,
            "mechanical_violations": self.violations,
            "suggested_fix": self.suggested_fix,
            "fix_verified": self.fix_verified,
            "fix_mechanical_report": self.fix_report,
            "conventions_applied": self.conventions,
            "trace": self.trace,
        }


def _hit_summaries(hits: list[SearchHit]) -> list[dict[str, Any]]:
    return [
        {
            "id": h.convention.id,
            "kind": h.convention.kind,
            "title": h.convention.title,
            "score": round(h.score, 4),
            "match_reason": h.reason,
        }
        for h in hits
    ]


class CodingPipeline:
    def __init__(self, store: ConventionStore, provider: LlmProvider | None,
                 config: AcodeConfig | None = None):
        self.store = store
        self.provider = provider
        self.config = config or AcodeConfig()

    def _require_provider(self) -> LlmProvider:
        if self.provider is None:
            from ..llm.factory import create_provider

            self.provider = create_provider(self.config)
        return self.provider

    async def generate(
        self,
        task: str,
        language: str,
        metadata: dict[str, Any] | None = None,
        context_code: str | None = None,
    ) -> GenerationResult:
        language = normalize_language(language)
        provider = self._require_provider()
        trace: list[dict[str, Any]] = []

        # 1. deterministic retrieval (metadata; plus AST if context given)
        hits = steps.retrieve(self.store, language, metadata, context_code,
                              self.config.retrieval_top_k)
        _emit(trace, "retrieve",
              f"{len(hits)} convention(s) for {language}: "
              + ", ".join(f"{h.convention.id}({h.score:.2f})" for h in hits),
              hits=_hit_summaries(hits))
        rules = steps.applicable_rules(self.store, language, metadata)
        _emit(trace, "rules",
              f"{len(rules)} mechanical rule(s) will be enforced: "
              + ", ".join(r.id for r in rules),
              rule_ids=[r.id for r in rules])

        # 2. LLM synthesis
        prompt = steps.build_generate_prompt(task, language, hits, context_code)
        reply = await provider.complete(steps.SYSTEM_PROMPT, prompt)
        code = steps.extract_code_block(reply) or reply
        _emit(trace, "synthesize",
              f"LLM ({provider.name}) produced {len(code.splitlines())} line(s) of code",
              provider=provider.name, iteration=0, code=code)

        # 3. mechanical verification + bounded repair loop
        report = steps.check(code, language, rules)
        _emit_verify(trace, report, iteration=0)
        iterations = 0
        while not report.passed and iterations < self.config.max_repairs:
            iterations += 1
            _emit(trace, f"repair#{iterations}",
                  f"re-prompting LLM with {len(report.violations)} violation(s) "
                  f"(repair {iterations}/{self.config.max_repairs})")
            repair_prompt = steps.build_repair_prompt(task, language, code, report, hits)
            reply = await provider.complete(steps.SYSTEM_PROMPT, repair_prompt)
            candidate = steps.extract_code_block(reply)
            if candidate is None:
                _emit(trace, f"repair#{iterations}",
                      "LLM reply contained no code block; aborting repair loop")
                break
            code = candidate
            _emit(trace, "synthesize",
                  f"LLM ({provider.name}) produced {len(code.splitlines())} line(s) of code",
                  provider=provider.name, iteration=iterations, code=code)
            report = steps.check(code, language, rules)
            _emit_verify(trace, report, iteration=iterations)

        _emit(trace, "done",
              f"verified={report.passed} after {iterations} repair iteration(s)")
        return GenerationResult(
            code=code,
            verified=report.passed,
            report=report.to_dict(),
            conventions=_hit_summaries(hits),
            iterations=iterations,
            notes="" if report.passed else (
                "mechanical verification still failing after "
                f"{iterations} repair iteration(s); violations listed in mechanical_report"
            ),
            trace=trace,
        )

    async def review(
        self,
        code: str,
        language: str,
        metadata: dict[str, Any] | None = None,
        instruction: str | None = None,
    ) -> ReviewResult:
        # the code may be a dialect of the declared language (JSX in "typescript")
        language = resolve_dialect(code, language)
        provider = self._require_provider()
        trace: list[dict[str, Any]] = []

        # 1. deterministic retrieval: metadata + AST similarity of the input
        hits = steps.retrieve(self.store, language, metadata, code,
                              self.config.retrieval_top_k)
        _emit(trace, "retrieve",
              f"{len(hits)} convention(s) for {language}: "
              + ", ".join(f"{h.convention.id}({h.score:.2f})" for h in hits),
              hits=_hit_summaries(hits))
        rules = steps.applicable_rules(self.store, language, metadata)
        _emit(trace, "rules",
              f"{len(rules)} mechanical rule(s) will be enforced: "
              + ", ".join(r.id for r in rules),
              rule_ids=[r.id for r in rules])

        # 2. mechanical verdict on the input (ground truth for the LLM)
        report = steps.check(code, language, rules)
        _emit(trace, "verify-input", _verdict_line(report),
              report=report.to_dict())

        # 3. LLM synthesis of review + fix
        prompt = steps.build_review_prompt(code, language, report, hits, instruction)
        reply = await provider.complete(steps.SYSTEM_PROMPT, prompt)
        fix = steps.extract_code_block(reply)
        _emit(trace, "synthesize",
              f"LLM ({provider.name}) wrote a review"
              + (f" and a {len(fix.splitlines())}-line fix" if fix else "; no fix code block"),
              provider=provider.name, fix=fix)

        # 4. mechanical verdict on the suggested fix
        fix_report = steps.check(fix, language, rules) if fix else None
        if fix_report is not None:
            _emit(trace, "verify-fix", _verdict_line(fix_report),
                  report=fix_report.to_dict())

        _emit(trace, "done",
              f"input_violations={len(report.violations)} "
              f"fix_verified={bool(fix_report and fix_report.passed)}")
        return ReviewResult(
            review=reply,
            violations=[v.to_dict() for v in report.violations],
            suggested_fix=fix,
            fix_verified=bool(fix_report and fix_report.passed),
            fix_report=fix_report.to_dict() if fix_report else None,
            conventions=_hit_summaries(hits),
            trace=trace,
        )
