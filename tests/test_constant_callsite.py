"""TASK-0013: constant-callsite analyzer.

A raw string literal passed where the parameter is typed by a union
derived from an ``as const`` object (``type T = typeof X[keyof typeof
X]``) is flagged with the exact member to reference (``X.Member``).
The full evidence chain — object, derived alias, annotated parameter,
matching literal — must be visible in the file; anything less -> silence.
"""

from pathlib import Path

from acode.astcore.rules import Rule, RuleEngine
from acode.rag.store import ConventionStore

REPO_ROOT = Path(__file__).resolve().parent.parent

RULE = Rule(
    id="ts-prefer-constant-callsite",
    language="typescript",
    type="analysis",
    query="",
    message="reference the as const member instead of the raw literal",
    analyzer="constant-callsite",
)

ENGINE = RuleEngine()


def check(code: str, language: str = "typescript"):
    return ENGINE.check(code, language, [RULE])


SETUP = (
    "const Align = {\n"
    "  Left: 'left',\n"
    "  Right: 'right',\n"
    "} as const;\n"
    "type Align = typeof Align[keyof typeof Align];\n"
    "function alignLabel(align: Align): string {\n"
    "  return align;\n"
    "}\n"
)


def test_raw_literal_argument_is_flagged():
    report = check(SETUP + "alignLabel('left');\n")
    assert len(report.violations) == 1
    message = report.violations[0].message
    assert "`Align.Left`" in message
    assert "'Align'" in message


def test_each_raw_call_is_flagged():
    report = check(SETUP + "alignLabel('left');\nalignLabel('right');\n")
    assert len(report.violations) == 2
    assert "`Align.Right`" in report.violations[1].message


def test_member_reference_is_fine():
    assert check(SETUP + "alignLabel(Align.Left);\n").passed


def test_literal_outside_the_set_is_the_compilers_job():
    assert check(SETUP + "alignLabel('center');\n").passed


def test_variable_argument_is_fine():
    assert check(SETUP + "alignLabel(current);\n").passed


def test_object_name_may_differ_from_type_name():
    code = (
        "const MODES = { Fast: 'fast', Slow: 'slow' } as const;\n"
        "type Mode = typeof MODES[keyof typeof MODES];\n"
        "function run(mode: Mode): string {\n"
        "  return mode;\n"
        "}\n"
        "run('fast');\n"
    )
    report = check(code)
    assert len(report.violations) == 1
    assert "`MODES.Fast`" in report.violations[0].message


def test_plain_union_alias_has_no_constant_to_reference():
    code = (
        "type Align = 'left' | 'right';\n"
        "function alignLabel(align: Align): string {\n"
        "  return align;\n"
        "}\n"
        "alignLabel('left');\n"
    )
    assert check(code).passed


def test_object_without_as_const_is_ignored():
    code = (
        "const Align = { Left: 'left', Right: 'right' };\n"
        "type Align = typeof Align[keyof typeof Align];\n"
        "function alignLabel(align: Align): string {\n"
        "  return align;\n"
        "}\n"
        "alignLabel('left');\n"
    )
    assert check(code).passed


def test_mismatched_typeof_targets_are_ignored():
    code = (
        "const A = { Left: 'left' } as const;\n"
        "const B = { Right: 'right' } as const;\n"
        "type Align = typeof A[keyof typeof B];\n"
        "function alignLabel(align: Align): string {\n"
        "  return align;\n"
        "}\n"
        "alignLabel('left');\n"
    )
    assert check(code).passed


def test_unknown_function_is_ignored():
    code = (
        "const Align = { Left: 'left' } as const;\n"
        "type Align = typeof Align[keyof typeof Align];\n"
        "importedLabel('left');\n"
    )
    assert check(code).passed


def test_multiple_derived_params_are_checked_independently():
    code = (
        "const Align = { Left: 'left', Right: 'right' } as const;\n"
        "type Align = typeof Align[keyof typeof Align];\n"
        "const Tone = { Ok: 'ok', Warn: 'warn' } as const;\n"
        "type Tone = typeof Tone[keyof typeof Tone];\n"
        "function paint(align: Align, tone: Tone): void {}\n"
        "paint('left', 'warn');\n"
    )
    report = check(code)
    assert len(report.violations) == 2
    messages = [v.message for v in report.violations]
    assert any("`Align.Left`" in m for m in messages)
    assert any("`Tone.Warn`" in m for m in messages)


def test_typed_variable_init_is_flagged():
    report = check(SETUP + "const fallback: Align = 'right';\n")
    assert len(report.violations) == 1
    message = report.violations[0].message
    assert "`Align.Right`" in message
    assert "variable" in message


def test_let_variable_init_is_also_flagged():
    assert len(check(SETUP + "let current: Align = 'left';\n").violations) == 1


def test_variable_member_init_is_fine():
    assert check(SETUP + "const fallback: Align = Align.Right;\n").passed


def test_untyped_variable_init_is_silent():
    assert check(SETUP + "const fallback = 'left';\n").passed


def test_parameter_default_is_flagged():
    code = (
        "const Align = { Left: 'left', Right: 'right' } as const;\n"
        "type Align = typeof Align[keyof typeof Align];\n"
        "function alignLabel(align: Align = 'left'): string {\n"
        "  return align;\n"
        "}\n"
    )
    report = check(code)
    assert len(report.violations) == 1
    message = report.violations[0].message
    assert "`Align.Left`" in message
    assert "parameter default" in message


def test_member_parameter_default_is_fine():
    code = (
        "const Align = { Left: 'left', Right: 'right' } as const;\n"
        "type Align = typeof Align[keyof typeof Align];\n"
        "function alignLabel(align: Align = Align.Left): string {\n"
        "  return align;\n"
        "}\n"
    )
    assert check(code).passed


def test_runs_on_tsx_via_dialect():
    code = (
        SETUP
        + "export function Label() {\n"
        + "  return <b>{alignLabel('left')}</b>;\n"
        + "}\n"
    )
    report = check(code, language="typescript")
    assert report.language == "tsx"
    assert len(report.violations) == 1


def test_seed_convention_self_verifies_on_import():
    store = ConventionStore(":memory:")
    added = store.import_file(REPO_ROOT / "conventions" / "typescript.json")
    assert "ts-prefer-constant-callsite" in added
    store.close()
