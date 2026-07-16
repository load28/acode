"""TASK-0010: duplicate-literal-union analyzer.

A literal union repeated inline (same member set, order-insensitive)
flags each non-alias occurrence; ``type X = ...`` alias declarations are
never flagged — an existing alias is suggested by name, otherwise the
message asks to extract one.
"""

from pathlib import Path

from acode.astcore.rules import Rule, RuleEngine
from acode.rag.store import ConventionStore

REPO_ROOT = Path(__file__).resolve().parent.parent

RULE = Rule(
    id="ts-no-duplicate-literal-union",
    language="typescript",
    type="analysis",
    query="",
    message="extract the repeated literal union into a named alias",
    analyzer="duplicate-literal-union",
)

ENGINE = RuleEngine()


def check(code: str, language: str = "typescript"):
    return ENGINE.check(code, language, [RULE])


def test_repeated_inline_union_flags_each_occurrence():
    code = (
        "function badge(size: 'sm' | 'md' | 'lg'): string {\n"
        "  return size;\n"
        "}\n"
        "function icon(size: 'sm' | 'md' | 'lg'): string {\n"
        "  return size;\n"
        "}\n"
    )
    report = check(code)
    assert len(report.violations) == 2
    assert all("extract a named type alias" in v.message for v in report.violations)


def test_existing_alias_is_suggested_by_name():
    code = (
        "type Size = 'sm' | 'md';\n"
        "function badge(size: 'sm' | 'md'): string {\n"
        "  return size;\n"
        "}\n"
    )
    report = check(code)
    assert len(report.violations) == 1
    assert "'Size'" in report.violations[0].message


def test_matching_is_order_insensitive():
    code = (
        "let a: 'on' | 'off';\n"
        "let b: 'off' | 'on';\n"
    )
    assert len(check(code).violations) == 2


def test_single_occurrence_is_fine():
    code = "function badge(size: 'sm' | 'md'): string {\n  return size;\n}\n"
    assert check(code).passed


def test_different_sets_are_fine():
    code = (
        "let a: 'sm' | 'md';\n"
        "let b: 'sm' | 'lg';\n"
    )
    assert check(code).passed


def test_non_literal_members_are_ignored():
    code = (
        "let a: 'sm' | string;\n"
        "let b: 'sm' | string;\n"
    )
    assert check(code).passed


def test_two_aliases_of_the_same_set_are_fine():
    code = (
        "type Size = 'sm' | 'md';\n"
        "type Scale = 'sm' | 'md';\n"
    )
    assert check(code).passed


def test_runs_on_tsx_via_dialect():
    code = (
        "function chip(size: 'sm' | 'md'): string {\n"
        "  return size;\n"
        "}\n"
        "function pill(size: 'sm' | 'md'): string {\n"
        "  return size;\n"
        "}\n"
        "export function Tag() {\n"
        "  return <i>{chip('sm')}</i>;\n"
        "}\n"
    )
    report = check(code, language="typescript")
    assert report.language == "tsx"
    assert len(report.violations) == 2


def test_seed_convention_self_verifies_on_import():
    store = ConventionStore(":memory:")
    added = store.import_file(REPO_ROOT / "conventions" / "typescript.json")
    assert "ts-no-duplicate-literal-union" in added
    store.close()
