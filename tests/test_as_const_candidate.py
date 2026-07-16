"""TASK-0010: as-const-candidate analyzer.

A module-level, unannotated ``const`` object literal whose values are
all primitive literals and which the file never mutates (reassignment,
property write/delete, Object.assign) should be frozen with ``as const``.
Any counter-evidence -> silence.
"""

from pathlib import Path

from acode.astcore.rules import Rule, RuleEngine
from acode.rag.store import ConventionStore

REPO_ROOT = Path(__file__).resolve().parent.parent

RULE = Rule(
    id="ts-prefer-as-const-object",
    language="typescript",
    type="analysis",
    query="",
    message="freeze the literal constant with as const",
    analyzer="as-const-candidate",
)

ENGINE = RuleEngine()


def check(code: str, language: str = "typescript"):
    return ENGINE.check(code, language, [RULE])


LITERAL_CONST = "const TYPE_COLORS = { fire: '#EE8130', water: '#6390F0' };\n"


def test_never_mutated_literal_const_is_flagged():
    report = check(LITERAL_CONST)
    assert len(report.violations) == 1
    message = report.violations[0].message
    assert "'TYPE_COLORS'" in message
    assert "as const" in message


def test_exported_const_is_also_flagged():
    assert len(check("export " + LITERAL_CONST).violations) == 1


def test_mixed_primitive_values_are_flagged():
    code = "const DEFAULTS = { retries: 3, verbose: false, mode: 'fast' };\n"
    assert len(check(code).violations) == 1


def test_already_as_const_is_fine():
    code = "const C = { fire: '#EE8130' } as const;\n"
    assert check(code).passed


def test_annotated_const_is_ignored():
    code = "const C: Palette = { fire: '#EE8130', water: '#6390F0' };\n"
    assert check(code).passed


def test_property_write_is_counter_evidence():
    assert check(LITERAL_CONST + "TYPE_COLORS.fire = '#f11';\n").passed


def test_subscript_write_is_counter_evidence():
    assert check(LITERAL_CONST + "TYPE_COLORS['fire'] = '#f11';\n").passed


def test_delete_is_counter_evidence():
    assert check(LITERAL_CONST + "delete TYPE_COLORS.fire;\n").passed


def test_object_assign_is_counter_evidence():
    assert check(LITERAL_CONST + "Object.assign(TYPE_COLORS, { grass: '#7AC74C' });\n").passed


def test_non_literal_value_is_ignored():
    code = "const M = { now: Date.now(), label: 'x' };\n"
    assert check(code).passed


def test_empty_object_is_ignored():
    assert check("const cache = {};\n").passed


def test_spread_is_ignored():
    assert check("const merged = { ...base, fire: '#f00' };\n").passed


def test_let_declaration_is_ignored():
    assert check("let colors = { fire: '#f00' };\n").passed


def test_function_local_const_is_ignored():
    code = (
        "function palette() {\n"
        "  const local = { fire: '#f00' };\n"
        "  return local;\n"
        "}\n"
    )
    assert check(code).passed


def test_runs_on_tsx_via_dialect():
    code = (
        "const LABELS = { ok: 'OK', bad: 'Bad' };\n"
        "export function Tag({ kind }: { kind: 'ok' | 'bad' }) {\n"
        "  return <span>{LABELS[kind]}</span>;\n"
        "}\n"
    )
    report = check(code, language="typescript")
    assert report.language == "tsx"
    assert len(report.violations) == 1


def test_seed_convention_self_verifies_on_import():
    store = ConventionStore(":memory:")
    added = store.import_file(REPO_ROOT / "conventions" / "typescript.json")
    assert "ts-prefer-as-const-object" in added
    store.close()
