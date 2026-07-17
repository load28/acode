"""TASK-0010: boolean-variant-bag analyzer.

An interface with >= 2 boolean properties is flagged only on usage
evidence that the flags model exclusive states: no usage ever sets two
flags true together, and >= 2 distinct flags each appear as the sole
true flag of some usage. Dynamic flag values or co-occurring true flags
make exclusivity unprovable -> silence.
"""

from pathlib import Path

from acode.astcore.rules import Rule, RuleEngine
from acode.rag.store import ConventionStore

REPO_ROOT = Path(__file__).resolve().parent.parent

RULE = Rule(
    id="ts-no-boolean-variant-bag",
    language="typescript",
    type="analysis",
    query="",
    message="replace exclusive boolean flags with a status union",
    analyzer="boolean-variant-bag",
)

ENGINE = RuleEngine()


def check(code: str, language: str = "typescript"):
    return ENGINE.check(code, language, [RULE])


INTERFACE = (
    "interface FetchState {\n"
    "  isLoading: boolean;\n"
    "  isError: boolean;\n"
    "  data?: string;\n"
    "}\n"
)


def test_exclusive_flags_are_flagged():
    code = (
        INTERFACE
        + "const loading: FetchState = { isLoading: true, isError: false };\n"
        + "const failed: FetchState = { isLoading: false, isError: true };\n"
    )
    report = check(code)
    assert len(report.violations) == 1
    message = report.violations[0].message
    assert "FetchState" in message
    assert "isLoading" in message and "isError" in message


def test_satisfies_and_as_usages_count():
    code = (
        INTERFACE
        + "const a = { isLoading: true, isError: false } satisfies FetchState;\n"
        + "const b = { isLoading: false, isError: true } as FetchState;\n"
    )
    assert len(check(code).violations) == 1


def test_co_occurring_true_flags_are_independent():
    code = (
        INTERFACE
        + "const both: FetchState = { isLoading: true, isError: true };\n"
        + "const one: FetchState = { isLoading: true, isError: false };\n"
    )
    assert check(code).passed


def test_dynamic_flag_value_is_unprovable():
    code = (
        INTERFACE
        + "const a: FetchState = { isLoading: pending, isError: false };\n"
        + "const b: FetchState = { isLoading: false, isError: true };\n"
    )
    assert check(code).passed


def test_shorthand_flag_is_unprovable():
    code = (
        INTERFACE
        + "const a: FetchState = { isLoading, isError: false };\n"
        + "const b: FetchState = { isLoading: false, isError: true };\n"
    )
    assert check(code).passed


def test_single_sole_true_flag_is_not_enough():
    code = (
        INTERFACE
        + "const a: FetchState = { isLoading: true, isError: false };\n"
        + "const b: FetchState = { isLoading: true, isError: false };\n"
    )
    assert check(code).passed


def test_no_usages_is_silent():
    assert check(INTERFACE).passed


def test_single_boolean_property_is_fine():
    code = (
        "interface Options {\n"
        "  verbose: boolean;\n"
        "  retries?: number;\n"
        "}\n"
        "const a: Options = { verbose: true };\n"
        "const b: Options = { verbose: false, retries: 1 };\n"
    )
    assert check(code).passed


def test_runs_on_tsx_via_dialect():
    code = (
        "interface ToggleProps {\n"
        "  isOn: boolean;\n"
        "  isOff: boolean;\n"
        "}\n"
        "const on: ToggleProps = { isOn: true, isOff: false };\n"
        "const off: ToggleProps = { isOn: false, isOff: true };\n"
        "export function Toggle(props: ToggleProps) {\n"
        "  return <span>{String(props.isOn)}</span>;\n"
        "}\n"
    )
    report = check(code, language="typescript")
    assert report.language == "tsx"
    assert len(report.violations) == 1


def test_seed_convention_self_verifies_on_import():
    store = ConventionStore(":memory:")
    added = store.import_file(REPO_ROOT / "conventions" / "typescript.json")
    assert "ts-no-boolean-variant-bag" in added
    store.close()
