"""TASK-0018: five new TypeScript query rules (proposal A1-A5 of TASK-0017).

ts-no-type-assertion, ts-expect-error-needs-reason, ts-class-pascal-case,
ts-no-nested-ternary, ts-prefer-template-literal — all plain tree-sitter
query rules, no analyzers involved.
"""

from pathlib import Path

from acode.agent import steps

REPO_ROOT = Path(__file__).resolve().parent.parent

NEW_RULE_IDS = {
    "ts-no-type-assertion",
    "ts-expect-error-needs-reason",
    "ts-class-pascal-case",
    "ts-no-nested-ternary",
    "ts-prefer-template-literal",
}


def _check(seeded_store, code: str, language: str = "typescript"):
    rules = steps.applicable_rules(seeded_store, language, None)
    return steps.check(code, language, rules)


def _violated(report) -> set[str]:
    return {v.rule_id for v in report.violations}


def test_seed_file_self_verifies_on_import(store):
    added = store.import_file(REPO_ROOT / "conventions" / "typescript.json")
    assert NEW_RULE_IDS <= set(added)


class TestNoTypeAssertion:
    def test_as_assertion_is_flagged(self, seeded_store):
        code = 'const el = document.querySelector("#app") as HTMLElement;\n'
        assert "ts-no-type-assertion" in _violated(_check(seeded_store, code))

    def test_double_assertion_is_flagged_per_layer(self, seeded_store):
        code = "const n = value as unknown as number;\n"
        report = _check(seeded_store, code)
        hits = [v for v in report.violations if v.rule_id == "ts-no-type-assertion"]
        assert len(hits) == 2  # both stacked overrides are reported

    def test_as_const_is_exempt(self, seeded_store):
        code = 'const Palette = {\n  Red: "red",\n} as const;\n'
        assert "ts-no-type-assertion" not in _violated(_check(seeded_store, code))

    def test_narrowing_passes(self, seeded_store):
        code = (
            'const el = document.querySelector("#app");\n'
            "if (el instanceof HTMLElement) {\n"
            "  el.focus();\n"
            "}\n"
        )
        assert "ts-no-type-assertion" not in _violated(_check(seeded_store, code))


class TestExpectErrorNeedsReason:
    def test_bare_directive_is_flagged(self, seeded_store):
        code = "// @ts-expect-error\nconst x: number = compute();\n"
        assert "ts-expect-error-needs-reason" in _violated(_check(seeded_store, code))

    def test_directive_with_trailing_spaces_is_flagged(self, seeded_store):
        code = "// @ts-expect-error   \nconst x: number = compute();\n"
        assert "ts-expect-error-needs-reason" in _violated(_check(seeded_store, code))

    def test_directive_with_reason_passes(self, seeded_store):
        code = (
            "// @ts-expect-error TODO(#123): upstream types lag the runtime\n"
            "const x: number = compute();\n"
        )
        assert "ts-expect-error-needs-reason" not in _violated(_check(seeded_store, code))


class TestClassPascalCase:
    def test_snake_case_class_is_flagged(self, seeded_store):
        assert "ts-class-pascal-case" in _violated(
            _check(seeded_store, "class user_store {}\n"))

    def test_pascal_case_class_passes(self, seeded_store):
        assert "ts-class-pascal-case" not in _violated(
            _check(seeded_store, "class UserStore {}\n"))


class TestNoNestedTernary:
    def test_nested_in_alternative_is_flagged(self, seeded_store):
        code = 'const label = a ? "x" : b ? "y" : "z";\n'
        assert "ts-no-nested-ternary" in _violated(_check(seeded_store, code))

    def test_nested_in_consequence_is_flagged(self, seeded_store):
        code = 'const label = a ? (b ? "x" : "y") : "z";\n'
        assert "ts-no-nested-ternary" in _violated(_check(seeded_store, code))

    def test_single_ternary_passes(self, seeded_store):
        code = 'const label = a ? "x" : "y";\n'
        assert "ts-no-nested-ternary" not in _violated(_check(seeded_store, code))


class TestPreferTemplateLiteral:
    def test_literal_on_left_is_flagged(self, seeded_store):
        code = 'const msg = "hello " + name;\n'
        assert "ts-prefer-template-literal" in _violated(_check(seeded_store, code))

    def test_literal_on_right_is_flagged(self, seeded_store):
        code = 'const msg = name + "!";\n'
        assert "ts-prefer-template-literal" in _violated(_check(seeded_store, code))

    def test_numeric_addition_passes(self, seeded_store):
        code = "const sum = a + b;\n"
        assert "ts-prefer-template-literal" not in _violated(_check(seeded_store, code))

    def test_template_literal_passes(self, seeded_store):
        code = "const msg = `hello ${name}!`;\n"
        assert "ts-prefer-template-literal" not in _violated(_check(seeded_store, code))


class TestTsxDialect:
    def test_new_rules_run_on_tsx_trees(self, seeded_store):
        # a JSX component with a nested ternary and an `as` assertion:
        # the rules must execute (not be skipped) under the tsx grammar
        code = (
            "export function Badge({ tone }: { tone: string }) {\n"
            "  const el = document.querySelector(\"#root\") as HTMLElement;\n"
            '  return <span>{tone === "a" ? "A" : tone === "b" ? "B" : "C"}</span>;\n'
            "}\n"
        )
        report = _check(seeded_store, code, "typescript")
        assert report.language == "tsx"
        assert not set(report.skipped_rules) & NEW_RULE_IDS
        violated = _violated(report)
        assert "ts-no-type-assertion" in violated
        assert "ts-no-nested-ternary" in violated

    def test_clean_component_passes_new_rules(self, seeded_store):
        code = (
            "export function Card({ title }: { title: string }) {\n"
            '  return <article className="card">{title}</article>;\n'
            "}\n"
        )
        report = _check(seeded_store, code, "typescript")
        assert not _violated(report) & NEW_RULE_IDS
