"""TASK-0008: analysis rule type + optional-variant-bag analyzer.

An interface with >= 2 optional properties is flagged only on evidence
that the optionals hide variants: a literal-union discriminant key in the
declaration (signal A) or usages filling the optional keys in disjoint
groups (signal B). No evidence -> optionals are legitimate -> silence.
"""

from pathlib import Path

import pytest

from acode.astcore.rules import Rule, RuleEngine, RuleError, validate_rule
from acode.rag.store import ConventionStore

REPO_ROOT = Path(__file__).resolve().parent.parent

RULE = Rule(
    id="ts-no-optional-variant-bag",
    language="typescript",
    type="analysis",
    query="",
    message="split into a discriminated union",
    analyzer="optional-variant-bag",
)

ENGINE = RuleEngine()


def check(code: str, language: str = "typescript"):
    return ENGINE.check(code, language, [RULE])


# -------------------------------------------------------------- signal A


def test_discriminant_key_with_optionals_is_flagged():
    code = (
        "interface CardView {\n"
        "  status: 'loading' | 'loaded' | 'failed';\n"
        "  title: string;\n"
        "  imageUrl?: string;\n"
        "  error?: string;\n"
        "}\n"
    )
    report = check(code)
    assert len(report.violations) == 1
    message = report.violations[0].message
    assert "'status'" in message
    assert "CardView" in message


def test_single_optional_with_discriminant_is_fine():
    code = (
        "interface CardView {\n"
        "  status: 'loading' | 'loaded';\n"
        "  title: string;\n"
        "  imageUrl?: string;\n"
        "}\n"
    )
    assert check(code).passed


def test_non_literal_union_is_not_a_discriminant():
    code = (
        "interface CardView {\n"
        "  status: string | number;\n"
        "  imageUrl?: string;\n"
        "  error?: string;\n"
        "}\n"
    )
    assert check(code).passed


# -------------------------------------------------------------- signal B


DISJOINT_USAGE = (
    "interface CardView {\n"
    "  title: string;\n"
    "  imageUrl?: string;\n"
    "  error?: string;\n"
    "  retryCount?: number;\n"
    "}\n"
    "const loaded: CardView = { title: 't', imageUrl: 'u' };\n"
    "const failed = { title: 't', error: 'e', retryCount: 1 } satisfies CardView;\n"
)


def test_disjoint_usage_groups_are_flagged():
    report = check(DISJOINT_USAGE)
    assert len(report.violations) == 1
    message = report.violations[0].message
    assert "{imageUrl}" in message
    assert "{error, retryCount}" in message


def test_as_expression_counts_as_usage():
    code = DISJOINT_USAGE.replace(
        "} satisfies CardView", "} as CardView"
    )
    assert len(check(code).violations) == 1


def test_overlapping_usage_groups_are_fine():
    # both usages fill imageUrl -> correlated optionality, not variants
    code = (
        "interface CardView {\n"
        "  title: string;\n"
        "  imageUrl?: string;\n"
        "  error?: string;\n"
        "}\n"
        "const a: CardView = { title: 't', imageUrl: 'u' };\n"
        "const b: CardView = { title: 't', imageUrl: 'u', error: 'e' };\n"
    )
    assert check(code).passed


def test_no_evidence_is_silent():
    code = (
        "interface Options {\n"
        "  timeoutMs?: number;\n"
        "  retries?: number;\n"
        "}\n"
        "const defaults: Options = { timeoutMs: 500, retries: 3 };\n"
    )
    assert check(code).passed


# ------------------------------------------------------------- dialects


def test_analyzer_runs_on_tsx_via_dialect():
    code = (
        "interface BannerProps {\n"
        "  status: 'ok' | 'error';\n"
        "  text?: string;\n"
        "  detail?: string;\n"
        "}\n"
        "export function Banner(props: BannerProps) {\n"
        "  return <div>{props.text}</div>;\n"
        "}\n"
    )
    report = check(code, language="typescript")
    assert report.language == "tsx"
    assert len(report.violations) == 1


# ------------------------------------------------------------ validation


def test_analysis_rule_requires_known_analyzer():
    with pytest.raises(RuleError):
        validate_rule(Rule(id="x", language="typescript", type="analysis",
                           query="", message="m", analyzer="no-such-analyzer"))
    with pytest.raises(RuleError):
        validate_rule(Rule(id="x", language="typescript", type="analysis",
                           query="", message="m"))


def test_seed_convention_self_verifies_on_import():
    store = ConventionStore(":memory:")
    added = store.import_file(REPO_ROOT / "conventions" / "typescript.json")
    assert "ts-no-optional-variant-bag" in added
    assert "ts-pattern-discriminated-view-model" in added
    store.close()
