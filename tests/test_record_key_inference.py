"""TASK-0009: record-key-inference analyzer.

A variable annotated ``Record<string, V>`` / ``{ [k: string]: V }`` but
initialized with a closed set of literal keys is flagged; genuinely open
maps (empty init, spread/computed keys, dynamic-key writes) stay silent.
"""

from pathlib import Path

from acode.astcore.rules import Rule, RuleEngine
from acode.rag.store import ConventionStore

REPO_ROOT = Path(__file__).resolve().parent.parent

RULE = Rule(
    id="ts-no-wide-record-key",
    language="typescript",
    type="analysis",
    query="",
    message="derive the key type",
    analyzer="record-key-inference",
)

ENGINE = RuleEngine()


def check(code: str, language: str = "typescript"):
    return ENGINE.check(code, language, [RULE])


def test_record_string_with_literal_keys_is_flagged():
    code = (
        "const TYPE_COLORS: Record<string, string> = {\n"
        "  fire: '#EE8130',\n"
        "  water: '#6390F0',\n"
        "};\n"
    )
    report = check(code)
    assert len(report.violations) == 1
    message = report.violations[0].message
    assert "'TYPE_COLORS'" in message
    assert "fire, water" in message


def test_string_literal_keys_also_count_as_closed():
    code = (
        "const HEADERS: Record<string, string> = {\n"
        "  'Content-Type': 'application/json',\n"
        "  'X-Retry': '3',\n"
        "};\n"
    )
    assert len(check(code).violations) == 1


def test_index_signature_annotation_is_flagged():
    code = (
        "const SCORES: { [name: string]: number } = { koga: 1, sabrina: 2 };\n"
    )
    assert len(check(code).violations) == 1


def test_empty_literal_is_a_dynamic_accumulator():
    assert check("const cache: Record<string, string> = {};\n").passed


def test_spread_means_possibly_open_keys():
    code = (
        "const merged: Record<string, string> = { ...base, fire: '#f00' };\n"
    )
    assert check(code).passed


def test_computed_key_means_possibly_open_keys():
    code = (
        "const m: Record<string, string> = { [dynamicKey]: '#f00' };\n"
    )
    assert check(code).passed


def test_dynamic_key_write_marks_map_as_open():
    code = (
        "const cache: Record<string, string> = { seed: 'v' };\n"
        "cache[userInput] = 'w';\n"
    )
    assert check(code).passed


def test_literal_key_write_does_not_excuse_the_annotation():
    code = (
        "const colors: Record<string, string> = { fire: '#f00' };\n"
        "colors['fire'] = '#f11';\n"
    )
    assert len(check(code).violations) == 1


def test_narrow_key_type_is_fine():
    code = (
        "const TYPE_LABEL: Record<PokemonType, string> = {\n"
        "  fire: 'Fire',\n"
        "  water: 'Water',\n"
        "};\n"
    )
    assert check(code).passed


def test_unannotated_or_uninitialized_is_ignored():
    code = (
        "const inferred = { fire: '#f00' } as const;\n"
        "let pending: Record<string, string>;\n"
    )
    assert check(code).passed


def test_runs_on_tsx_via_dialect():
    code = (
        "const LABELS: Record<string, string> = { ok: 'OK', bad: 'Bad' };\n"
        "export function Tag({ kind }: { kind: string }) {\n"
        "  return <span>{LABELS[kind]}</span>;\n"
        "}\n"
    )
    report = check(code, language="typescript")
    assert report.language == "tsx"
    assert len(report.violations) == 1


def test_seed_convention_self_verifies_on_import():
    store = ConventionStore(":memory:")
    added = store.import_file(REPO_ROOT / "conventions" / "typescript.json")
    assert "ts-no-wide-record-key" in added
    assert "ts-pattern-derived-record-keys" in added
    store.close()
