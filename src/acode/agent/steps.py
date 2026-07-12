"""Deterministic pipeline steps shared by the plain pipeline and the ADK
agents.

Everything here is mechanical: retrieval (metadata + AST fingerprint),
rule checking, and prompt construction are pure functions of their
inputs. The only non-deterministic component in the whole system is the
single LLM synthesis call that consumes these outputs.
"""

from __future__ import annotations

import re
from typing import Any

from ..astcore.rules import CheckReport, Rule, RuleEngine
from ..rag.store import ConventionStore, SearchHit

_ENGINE = RuleEngine()

SYSTEM_PROMPT = (
    "You are a coding agent that must follow the project's conventions exactly. "
    "Conventions are enforced mechanically by an AST rule engine after you answer; "
    "any rule violation will be sent back to you for repair, so follow the listed "
    "rules literally. Always return the complete code inside a single fenced code "
    "block. Outside the code block, be brief."
)


def retrieve(
    store: ConventionStore,
    language: str,
    metadata: dict[str, Any] | None,
    code: str | None,
    top_k: int,
) -> list[SearchHit]:
    """Generation path: metadata only. Modification path: metadata + AST."""
    return store.search(
        language=language, metadata=metadata, code=code, top_k=top_k
    )


def rules_from_hits(hits: list[SearchHit], language: str) -> list[Rule]:
    rules = []
    for hit in hits:
        conv = hit.convention
        if conv.kind == "rule" and conv.rule is not None and conv.language == language:
            rules.append(conv.rule)
    return rules


def applicable_rules(store: ConventionStore, language: str,
                     metadata: dict[str, Any] | None) -> list[Rule]:
    """All stored rules for a language that pass the metadata filter."""
    from ..rag.store import _metadata_matches

    rules = []
    for conv in store.list(language=language, kind="rule"):
        if conv.rule is None:
            continue
        if metadata and not _metadata_matches(conv.metadata, metadata):
            continue
        rules.append(conv.rule)
    return rules


def check(code: str, language: str, rules: list[Rule]) -> CheckReport:
    return _ENGINE.check(code, language, rules)


def extract_code_block(text: str) -> str | None:
    """First fenced code block in an LLM reply, else None."""
    match = re.search(r"```[^\n]*\n(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).rstrip("\n") + "\n"
    return None


# ---------------------------------------------------------------- prompts


def _conventions_section(hits: list[SearchHit]) -> str:
    lines: list[str] = []
    rules = [h for h in hits if h.convention.kind == "rule"]
    patterns = [h for h in hits if h.convention.kind == "pattern"]
    if rules:
        lines.append("## Conventions (mechanically enforced — MUST follow)")
        for hit in rules:
            conv = hit.convention
            lines.append(f"- [{conv.id}] {conv.title}")
            if conv.guideline:
                lines.append(f"  {conv.guideline}")
            if conv.rule and conv.rule.message:
                lines.append(f"  rule: {conv.rule.message}")
            if conv.good_example:
                lines.append(f"  good example:\n```{conv.language}\n{conv.good_example}\n```")
    if patterns:
        lines.append("\n## Existing code patterns (match this project's style)")
        for hit in patterns:
            conv = hit.convention
            lines.append(f"- {conv.title} (similarity {hit.score:.2f})")
            lines.append(f"```{conv.language}\n{conv.good_example}\n```")
    return "\n".join(lines)


def _violations_section(report: CheckReport) -> str:
    lines = ["## Mechanical AST check result (deterministic, not an opinion)"]
    if not report.syntax_ok:
        lines.append("- SYNTAX ERROR: the code does not parse")
    for v in report.violations:
        lines.append(
            f"- line {v.start_line}: [{v.rule_id}] {v.message}"
            + (f" -> `{v.snippet}`" if v.snippet else "")
        )
    if report.passed:
        lines.append("- all checks passed")
    return "\n".join(lines)


def build_generate_prompt(task: str, language: str, hits: list[SearchHit],
                          context_code: str | None) -> str:
    parts = [
        f"Write {language} code for the following task.",
        f"\n## Task\n{task}",
    ]
    if context_code:
        parts.append(f"\n## Surrounding code (for context)\n```{language}\n{context_code}\n```")
    section = _conventions_section(hits)
    if section:
        parts.append("\n" + section)
    parts.append(
        "\nReturn the complete code in one fenced code block."
    )
    return "\n".join(parts)


def build_repair_prompt(task: str, language: str, code: str,
                        report: CheckReport, hits: list[SearchHit]) -> str:
    return "\n".join([
        f"Your previous {language} code failed the mechanical convention check.",
        f"\n## Task\n{task}",
        f"\n## Previous code\n```{language}\n{code}\n```",
        "\n" + _violations_section(report),
        "\n" + _conventions_section([h for h in hits if h.convention.kind == "rule"]),
        "\nFix ONLY the reported violations while keeping the code correct. "
        "Return the complete fixed code in one fenced code block.",
    ])


def build_review_prompt(code: str, language: str, report: CheckReport,
                        hits: list[SearchHit], instruction: str | None) -> str:
    parts = [
        f"Review the following {language} code against the project's conventions.",
    ]
    if instruction:
        parts.append(f"\n## Requested change\n{instruction}")
    parts.append(f"\n## Code under review\n```{language}\n{code}\n```")
    parts.append("\n" + _violations_section(report))
    section = _conventions_section(hits)
    if section:
        parts.append("\n" + section)
    parts.append(
        "\nThe mechanical check result above is ground truth — do not re-litigate it, "
        "do not invent violations it did not report. Synthesize: (1) a short review "
        "explaining each reported violation and how the similar patterns apply, and "
        "(2) the corrected code in one fenced code block. If there are no violations "
        "and no requested change, say the code conforms and return the original code "
        "in the code block."
    )
    return "\n".join(parts)
