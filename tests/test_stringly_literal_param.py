"""TASK-0010: stringly-literal-param analyzer.

A non-exported function's ``string`` parameter is flagged when >= 2
direct calls in the file all pass string literals with >= 2 distinct
values — the domain is a closed union the type should carry. Exported
functions, indirect references, non-literal or missing arguments make
the set unprovably closed -> silence.
"""

from pathlib import Path

from acode.astcore.rules import Rule, RuleEngine
from acode.rag.store import ConventionStore

REPO_ROOT = Path(__file__).resolve().parent.parent

RULE = Rule(
    id="ts-prefer-literal-union-param",
    language="typescript",
    type="analysis",
    query="",
    message="narrow the string parameter to a literal union",
    analyzer="stringly-literal-param",
)

ENGINE = RuleEngine()


def check(code: str, language: str = "typescript"):
    return ENGINE.check(code, language, [RULE])


def test_closed_literal_call_set_is_flagged():
    code = (
        "function alignLabel(align: string): string {\n"
        "  return align;\n"
        "}\n"
        "alignLabel('left');\n"
        "alignLabel('right');\n"
    )
    report = check(code)
    assert len(report.violations) == 1
    message = report.violations[0].message
    assert "'align'" in message
    assert "'left' | 'right'" in message
    # the suggested fix is the as const + derived-union shape
    assert "as const" in message
    assert "keyof typeof" in message


def test_multiple_string_params_are_checked_independently():
    code = (
        "function paint(shape: string, color: string): void {}\n"
        "paint('circle', 'red');\n"
        "paint('square', 'blue');\n"
    )
    report = check(code)
    assert len(report.violations) == 2


def test_single_distinct_value_is_not_enough():
    code = (
        "function draw(shape: string): void {}\n"
        "draw('circle');\n"
        "draw('circle');\n"
    )
    assert check(code).passed


def test_non_literal_argument_opens_the_set():
    code = (
        "function draw(shape: string): void {}\n"
        "draw('circle');\n"
        "draw(kind);\n"
    )
    assert check(code).passed


def test_missing_argument_opens_the_set():
    code = (
        "function greet(name?: string): string {\n"
        "  return name ?? 'anon';\n"
        "}\n"
        "greet('ash');\n"
        "greet();\n"
    )
    assert check(code).passed


def test_exported_function_is_silent():
    code = (
        "export function draw(shape: string): void {}\n"
        "draw('circle');\n"
        "draw('square');\n"
    )
    assert check(code).passed


def test_indirect_reference_is_silent():
    code = (
        "function shout(word: string): string {\n"
        "  return word;\n"
        "}\n"
        "shout('hey');\n"
        "shout('yo');\n"
        "const handlers = [shout];\n"
    )
    assert check(code).passed


def test_fewer_than_two_calls_is_silent():
    code = (
        "function draw(shape: string): void {}\n"
        "draw('circle');\n"
    )
    assert check(code).passed


def test_non_string_param_is_ignored():
    code = (
        "function repeat(n: number): void {}\n"
        "repeat(1);\n"
        "repeat(2);\n"
    )
    assert check(code).passed


def test_runs_on_tsx_via_dialect():
    code = (
        "function tone(kind: string): string {\n"
        "  return kind;\n"
        "}\n"
        "tone('ok');\n"
        "tone('warn');\n"
        "export function Badge() {\n"
        "  return <em>{tone('ok')}</em>;\n"
        "}\n"
    )
    report = check(code, language="typescript")
    assert report.language == "tsx"
    assert len(report.violations) == 1


def test_seed_convention_self_verifies_on_import():
    store = ConventionStore(":memory:")
    added = store.import_file(REPO_ROOT / "conventions" / "typescript.json")
    assert "ts-prefer-literal-union-param" in added
    store.close()
