"""TASK-0020: three strict-type query rules (core picks of TASK-0019 proposal A).

ts-no-angle-bracket-assertion, ts-no-wrapper-object-types,
ts-no-implicit-any-param — all plain tree-sitter query rules, chosen because
they close bypass routes around the existing ts-no-any / ts-no-type-assertion
rules.
"""

from pathlib import Path

from acode.agent import steps

REPO_ROOT = Path(__file__).resolve().parent.parent

NEW_RULE_IDS = {
    "ts-no-angle-bracket-assertion",
    "ts-no-wrapper-object-types",
    "ts-no-implicit-any-param",
}


def _check(seeded_store, code: str, language: str = "typescript"):
    rules = steps.applicable_rules(seeded_store, language, None)
    return steps.check(code, language, rules)


def _violated(report) -> set[str]:
    return {v.rule_id for v in report.violations}


def _hits(report, rule_id: str) -> int:
    return sum(1 for v in report.violations if v.rule_id == rule_id)


def test_seed_file_self_verifies_on_import(store):
    added = store.import_file(REPO_ROOT / "conventions" / "typescript.json")
    assert NEW_RULE_IDS <= set(added)


class TestNoAngleBracketAssertion:
    def test_angle_assertion_is_flagged(self, seeded_store):
        code = "const el = <HTMLInputElement>document.getElementById('x');\n"
        assert "ts-no-angle-bracket-assertion" in _violated(_check(seeded_store, code))

    def test_angle_const_assertion_is_flagged_too(self, seeded_store):
        # the repository spelling for const assertions is `as const`
        code = 'const sizes = <const>["sm", "md"];\n'
        assert "ts-no-angle-bracket-assertion" in _violated(_check(seeded_store, code))

    def test_generic_call_passes(self, seeded_store):
        code = "const ids = new Set<number>([1, 2]);\n"
        assert "ts-no-angle-bracket-assertion" not in _violated(_check(seeded_store, code))

    def test_narrowing_passes(self, seeded_store):
        code = (
            "const el = document.getElementById('x');\n"
            "if (el instanceof HTMLInputElement) {\n"
            "  el.focus();\n"
            "}\n"
        )
        assert "ts-no-angle-bracket-assertion" not in _violated(_check(seeded_store, code))


class TestNoWrapperObjectTypes:
    def test_each_wrapper_position_is_flagged(self, seeded_store):
        code = "function f(s: String, n: Number, cb: Function): Object {\n  return { s, n, cb };\n}\n"
        assert _hits(_check(seeded_store, code), "ts-no-wrapper-object-types") == 4

    def test_wrapper_inside_generic_argument_is_flagged(self, seeded_store):
        code = "const fns: Array<Function> = [];\n"
        assert "ts-no-wrapper-object-types" in _violated(_check(seeded_store, code))

    def test_value_position_conversion_passes(self, seeded_store):
        # String(42) is the conversion function in value position, not a type
        code = "const label = String(42);\nconst big = BigInt(1);\n"
        assert "ts-no-wrapper-object-types" not in _violated(_check(seeded_store, code))

    def test_lowercase_primitives_pass(self, seeded_store):
        code = (
            "function f(s: string, n: number, cb: () => void): { s: string } {\n"
            "  cb();\n"
            "  return { s };\n"
            "}\n"
        )
        assert "ts-no-wrapper-object-types" not in _violated(_check(seeded_store, code))


class TestNoImplicitAnyParam:
    def test_function_params_are_flagged_each(self, seeded_store):
        code = "function add(a, b?) {\n  return a + (b ?? 0);\n}\n"
        assert _hits(_check(seeded_store, code), "ts-no-implicit-any-param") == 2

    def test_method_param_is_flagged(self, seeded_store):
        code = "class Store {\n  set(key) {\n    return key;\n  }\n}\n"
        assert "ts-no-implicit-any-param" in _violated(_check(seeded_store, code))

    def test_constructor_property_is_flagged(self, seeded_store):
        # `private x` without a type is implicit any too
        code = "class Store {\n  constructor(private x) {}\n}\n"
        assert "ts-no-implicit-any-param" in _violated(_check(seeded_store, code))

    def test_annotated_params_pass(self, seeded_store):
        code = "function add(a: number, b?: number): number {\n  return a + (b ?? 0);\n}\n"
        assert "ts-no-implicit-any-param" not in _violated(_check(seeded_store, code))

    def test_arrow_callback_params_are_exempt(self, seeded_store):
        # contextual typing already types callback parameters precisely
        code = "const doubled = [1, 2].map((n) => n * 2);\n"
        assert "ts-no-implicit-any-param" not in _violated(_check(seeded_store, code))

    def test_destructured_param_is_silent(self, seeded_store):
        # pattern: (identifier) restricts the rule to plain identifiers
        code = "function pick({ a }) {\n  return a;\n}\n"
        assert "ts-no-implicit-any-param" not in _violated(_check(seeded_store, code))


class TestTsxDialect:
    def test_angle_rule_is_skipped_on_tsx_trees(self, seeded_store):
        # tsx grammar has no type_assertion node (the syntax collides with
        # JSX), so the rule is skipped there — and nothing is lost, because
        # the forbidden syntax cannot be written in tsx at all
        code = (
            "export function Badge({ tone }: { tone: string }) {\n"
            "  return <span>{tone}</span>;\n"
            "}\n"
        )
        report = _check(seeded_store, code, "typescript")
        assert report.language == "tsx"
        assert "ts-no-angle-bracket-assertion" in report.skipped_rules

    def test_other_two_rules_fire_on_tsx_trees(self, seeded_store):
        code = (
            "export function Badge(props) {\n"
            "  const cb: Function = () => null;\n"
            "  return <span onClick={cb}>{props.tone}</span>;\n"
            "}\n"
        )
        report = _check(seeded_store, code, "typescript")
        assert report.language == "tsx"
        violated = _violated(report)
        assert "ts-no-implicit-any-param" in violated
        assert "ts-no-wrapper-object-types" in violated
