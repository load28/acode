"""TASK-0007: JSX/TSX dialect support.

tsx is modeled as a dialect of typescript: code declared as "typescript"
that only parses under the tsx grammar is upgraded automatically, tsx
checks inherit the typescript ruleset, and a tsx rule can override a base
rule via metadata["overrides"].
"""

from pathlib import Path

import pytest

from acode.agent import steps
from acode.astcore.parser import resolve_dialect, rule_languages
from acode.astcore.rules import Rule, RuleEngine
from acode.rag.store import ConventionStore

REPO_ROOT = Path(__file__).resolve().parent.parent

COMPONENT = (
    "interface CardProps {\n"
    "  title: string;\n"
    "}\n\n"
    "export function PokemonCard({ title }: CardProps) {\n"
    "  return <article className=\"card\">{title}</article>;\n"
    "}\n"
)

ENUM_COMPONENT = (
    "enum Status {\n"
    "  Idle = \"IDLE\",\n"
    "}\n\n"
    "export function Badge() {\n"
    "  return <span>{Status.Idle}</span>;\n"
    "}\n"
)


@pytest.fixture()
def tsx_store() -> ConventionStore:
    s = ConventionStore(":memory:")
    s.import_file(REPO_ROOT / "conventions" / "typescript.json")
    s.import_file(REPO_ROOT / "conventions" / "tsx.json")
    yield s
    s.close()


# ---------------------------------------------------------------- parser


def test_rule_languages_dialect_includes_base():
    assert rule_languages("tsx") == ("tsx", "typescript")
    assert rule_languages("typescript") == ("typescript",)
    assert rule_languages("python") == ("python",)


def test_resolve_dialect_keeps_plain_typescript():
    assert resolve_dialect("const n: number = 1;\n", "typescript") == "typescript"


def test_resolve_dialect_upgrades_jsx_to_tsx():
    assert resolve_dialect(COMPONENT, "typescript") == "tsx"


def test_resolve_dialect_keeps_broken_code_as_declared():
    assert resolve_dialect("const = = 1;;;(\n", "typescript") == "typescript"


def test_resolve_dialect_angle_bracket_assertion_stays_typescript():
    # <T>expr parses as typescript but not as tsx — must NOT be upgraded
    code = "const n = <number>getValue();\n"
    assert resolve_dialect(code, "typescript") == "typescript"


# ---------------------------------------------------------------- engine


def test_typescript_rules_apply_to_tsx_tree():
    rule = Rule(id="ts-no-enum", language="typescript", type="forbid",
                query="(enum_declaration) @bad", capture="bad",
                message="enum is forbidden")
    report = RuleEngine().check(ENUM_COMPONENT, "typescript", [rule])
    assert report.language == "tsx"
    assert report.syntax_ok
    assert [v.rule_id for v in report.violations] == ["ts-no-enum"]


def test_dialect_incompatible_rule_is_reported_not_silently_dropped():
    # type_assertion exists in the typescript grammar only
    rule = Rule(id="ts-no-angle-assert", language="typescript", type="forbid",
                query="(type_assertion) @bad", capture="bad", message="no <T>x")
    report = RuleEngine().check(COMPONENT, "typescript", [rule])
    assert report.language == "tsx"
    assert report.skipped_rules == ["ts-no-angle-assert"]
    assert report.checked_rules == []


# ----------------------------------------------------------------- store


def test_store_list_tsx_inherits_typescript_conventions(tsx_store):
    ids = {c.id for c in tsx_store.list(language="tsx")}
    assert "tsx-no-inline-style" in ids
    assert "ts-no-enum" in ids  # inherited from the base language


def test_store_list_typescript_excludes_tsx_conventions(tsx_store):
    ids = {c.id for c in tsx_store.list(language="typescript")}
    assert "ts-no-enum" in ids
    assert not any(i.startswith("tsx-") for i in ids)


# ----------------------------------------------------------------- steps


def test_applicable_rules_override_replaces_base_naming_rule(tsx_store):
    tsx_ids = {r.id for r in steps.applicable_rules(tsx_store, "tsx", None)}
    assert "tsx-func-component-naming" in tsx_ids
    assert "ts-func-camel-case" not in tsx_ids  # overridden by the tsx rule

    ts_ids = {r.id for r in steps.applicable_rules(tsx_store, "typescript", None)}
    assert "ts-func-camel-case" in ts_ids
    assert "tsx-func-component-naming" not in ts_ids


def test_pascal_case_component_passes_snake_case_fails(tsx_store):
    rules = steps.applicable_rules(tsx_store, "tsx", None)

    report = steps.check(COMPONENT, "tsx", rules)
    assert report.passed

    snake = COMPONENT.replace("PokemonCard", "pokemon_card")
    report = steps.check(snake, "tsx", rules)
    assert [v.rule_id for v in report.violations] == ["tsx-func-component-naming"]


def test_tsx_specific_rules_fire(tsx_store):
    rules = steps.applicable_rules(tsx_store, "tsx", None)
    bad = (
        "export const Card: React.FC<{ html: string }> = ({ html }) => {\n"
        "  return <div style={{ color: \"red\" }}"
        " dangerouslySetInnerHTML={{ __html: html }} />;\n"
        "};\n"
    )
    report = steps.check(bad, "tsx", rules)
    fired = {v.rule_id for v in report.violations}
    assert {"tsx-no-react-fc", "tsx-no-inline-style",
            "tsx-no-dangerously-set-inner-html"} <= fired


def test_check_flow_with_language_declared_as_typescript(tsx_store):
    # the exact failure seen in the field: JSX handed over as "typescript"
    lang = resolve_dialect(ENUM_COMPONENT, "typescript")
    rules = steps.applicable_rules(tsx_store, lang, None)
    report = steps.check(ENUM_COMPONENT, lang, rules)
    assert report.language == "tsx"
    assert report.syntax_ok
    assert "ts-no-enum" in {v.rule_id for v in report.violations}


def test_rules_from_hits_respects_dialect_and_override(tsx_store):
    hits = tsx_store.search(language="tsx", code=COMPONENT, top_k=20)
    ids = {r.id for r in steps.rules_from_hits(hits, "tsx")}
    assert ids  # typescript rules retrieved for a tsx query
    assert "ts-func-camel-case" not in ids
