"""Rule recommendation engine (TASK-0010, reshaped by TASK-0011).

Covers: per-rule-type evidence counting across the complexity spectrum
(simple query rules and multi-signal analysis rules), the four-way
verdict, dialect inheritance, the governed-sites primitive, the
rule-applicability search signal, determinism, and the CLI/MCP surfaces.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from acode.astcore.analyzers import (
    optional_variant_bag_sites,
    record_key_inference_sites,
)
from acode.astcore.parser import parse
from acode.astcore.rules import Rule, governed_sites
from acode.rag.recommend import recommend_rules


def _write(root: Path, name: str, code: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")


def _by_id(report: dict, rule_id: str) -> dict:
    entry = next((r for r in report["catalog"] if r["id"] == rule_id), None)
    assert entry is not None, f"{rule_id} missing from catalog: " + ", ".join(
        r["id"] for r in report["catalog"])
    return entry


VARIANT_CANDIDATE = "interface Opts{n} {{ width?: number; height?: number; }}\n"
VARIANT_VIOLATION = (
    'interface Payment {\n  method: "card" | "bank";\n'
    "  cardNumber?: string;\n  iban?: string;\n}\n"
)


@pytest.fixture()
def ts_repo(tmp_path: Path) -> Path:
    # six files: camelCase functions dominate, no var/enum anywhere
    for i in range(6):
        _write(tmp_path, f"src/mod{i}.ts",
               f"function loadThing{i}() {{ return {i}; }}\n"
               f"function saveThing{i}() {{ return {i}; }}\n")
    return tmp_path


class TestGovernedSites:
    """The shared site-semantics primitive, per rule type."""

    def _root(self, code: str, language: str = "typescript"):
        return parse(code, language).root_node

    def test_naming_counts_captures(self):
        rule = Rule(id="r", language="typescript", type="naming",
                    query="(function_declaration name: (identifier) @name)",
                    capture="name", regex="[a-z].*", message="m")
        root = self._root("function a() {}\nfunction b() {}\nfunction c() {}\n")
        assert governed_sites(rule, root, "typescript") == 3

    def test_require_in_counts_scopes(self):
        rule = Rule(id="r", language="python", type="require_in",
                    scope_query="(function_definition) @scope", capture="scope",
                    query="(function_definition body: (block . (expression_statement (string))))",
                    message="m")
        root = self._root("def a():\n    pass\n\ndef b():\n    pass\n", "python")
        assert governed_sites(rule, root, "python") == 2

    def test_require_governs_the_file(self):
        rule = Rule(id="r", language="python", type="require",
                    query="(import_statement)", message="m")
        assert governed_sites(rule, self._root("x = 1\n", "python"), "python") == 1

    def test_forbid_counts_matches(self):
        rule = Rule(id="r", language="typescript", type="forbid",
                    query="(enum_declaration) @bad", capture="bad", message="m")
        root = self._root("enum A { X }\nenum B { Y }\n")
        assert governed_sites(rule, root, "typescript") == 2

    def test_analysis_counts_candidate_population(self):
        rule = Rule(id="r", language="typescript", type="analysis", query="",
                    analyzer="optional-variant-bag", message="m")
        # two candidates (>= 2 optionals), one non-candidate (1 optional)
        root = self._root(
            "interface A { x?: number; y?: number; }\n"
            "interface B { p?: string; q?: string; }\n"
            "interface C { only?: string; fixed: number; }\n")
        assert governed_sites(rule, root, "typescript") == 2

    def test_analyzer_site_counters(self):
        root = self._root(
            "const a: Record<string, number> = {};\n"
            "const b: { [k: string]: string } = { x: 'y' };\n"
            "const c: number = 1;\n")
        assert record_key_inference_sites(root) == 2
        assert optional_variant_bag_sites(root) == 0


class TestCatalogVerdicts:
    def test_adopt_forbid_zero_violations(self, seeded_store, ts_repo):
        report = recommend_rules(seeded_store, ts_repo)
        entry = _by_id(report, "ts-no-enum")
        assert entry["verdict"] == "adopt"
        assert entry["evidence"]["violations"] == 0
        assert entry["evidence"]["checked_files"] == 6
        assert entry["confidence"] > 0

    def test_adopt_naming_full_conformance(self, seeded_store, ts_repo):
        entry = _by_id(recommend_rules(seeded_store, ts_repo), "ts-func-camel-case")
        assert entry["verdict"] == "adopt"
        assert entry["evidence"]["sites"] == 12
        assert entry["confidence"] == pytest.approx(12 / 20)

    def test_adopt_lists_minority_naming_violation(self, seeded_store, ts_repo):
        _write(ts_repo, "src/legacy.ts", "function Legacy_Load() { return 1; }\n")
        entry = _by_id(recommend_rules(seeded_store, ts_repo), "ts-func-camel-case")
        # 12/13 conform (92%) -> still adopt; the violation is listed for cleanup
        assert entry["verdict"] == "adopt"
        assert entry["evidence"]["violations"] == 1
        assert entry["evidence"]["counter_examples"] == ["Legacy_Load"]
        assert entry["evidence"]["violating_files"] == ["src/legacy.ts"]

    def test_conflicts_when_codebase_leans_other_way(self, seeded_store, ts_repo):
        # enums in 3 of 9 files (>20% dispersion) -> conflicts
        for i in range(3):
            _write(ts_repo, f"src/enum{i}.ts", f"enum Color{i} {{ Red, Green }}\n")
        entry = _by_id(recommend_rules(seeded_store, ts_repo), "ts-no-enum")
        assert entry["verdict"] == "conflicts"
        assert entry["evidence"]["violations"] == 3

    def test_fix_first_contained_forbid_violations(self, seeded_store, ts_repo):
        _write(ts_repo, "src/dbg.ts", "function dbg() { console.log('x'); }\n")
        entry = _by_id(recommend_rules(seeded_store, ts_repo), "ts-no-console-log")
        # violations in 1/7 files (14% <= 20%) -> contained
        assert entry["verdict"] == "fix_first"

    def test_insufficient_evidence_below_min_sites(self, seeded_store, ts_repo):
        _write(ts_repo, "src/types.ts", "interface User { id: number; }\n")
        entry = _by_id(recommend_rules(seeded_store, ts_repo), "ts-interface-pascal-case")
        assert entry["verdict"] == "insufficient_evidence"
        assert entry["confidence"] == 0.0

    def test_require_in_counts_scopes(self, seeded_store, tmp_path):
        code_ok = 'def f{i}():\n    """doc."""\n    return {i}\n'
        for i in range(4):
            _write(tmp_path, f"m{i}.py", code_ok.replace("{i}", str(i)))
        _write(tmp_path, "bare.py", "def naked():\n    return 0\n")
        entry = _by_id(recommend_rules(seeded_store, tmp_path), "py-docstring-required")
        assert entry["evidence"]["sites"] == 5
        assert entry["evidence"]["violations"] == 1
        # 4/5 = 0.8 -> between 0.5 and 0.9 -> fix_first
        assert entry["verdict"] == "fix_first"

    def test_tsx_inherits_typescript_rules(self, seeded_store, tmp_path):
        for i in range(3):
            _write(tmp_path, f"c{i}.tsx",
                   f"enum E{i} {{ A }}\n"
                   f"export function View{i}() {{ return <div>{i}</div>; }}\n")
        report = recommend_rules(seeded_store, tmp_path)
        assert report["languages"] == {"tsx": 3}
        entry = _by_id(report, "ts-no-enum")
        assert entry["evidence"]["violations"] == 3

    def test_catalog_sorted_adopt_first(self, seeded_store, ts_repo):
        report = recommend_rules(seeded_store, ts_repo)
        verdict_order = {"adopt": 0, "fix_first": 1, "conflicts": 2,
                         "insufficient_evidence": 3}
        ranks = [verdict_order[r["verdict"]] for r in report["catalog"]]
        assert ranks == sorted(ranks)


class TestComplexRuleEvidence:
    """analysis rules get the same conformance-based verdicts as simple
    ones: their candidate populations are counted as governed sites."""

    def test_analysis_conformance_verdict(self, seeded_store, tmp_path):
        for i in range(5):
            _write(tmp_path, f"opts{i}.ts", VARIANT_CANDIDATE.format(n=i))
        _write(tmp_path, "payment.ts", VARIANT_VIOLATION)
        entry = _by_id(recommend_rules(seeded_store, tmp_path),
                       "ts-no-optional-variant-bag")
        assert entry["rule_type"] == "analysis"
        assert entry["evidence"]["sites"] == 6
        assert entry["evidence"]["violations"] == 1
        # 5/6 conform (83%) -> fix_first, judged by ratio not dispersion
        assert entry["verdict"] == "fix_first"
        assert "5/6 sites conform" in entry["reason"]

    def test_analysis_adopt_with_conforming_candidates(self, seeded_store, tmp_path):
        for i in range(5):
            _write(tmp_path, f"opts{i}.ts", VARIANT_CANDIDATE.format(n=i))
        entry = _by_id(recommend_rules(seeded_store, tmp_path),
                       "ts-no-optional-variant-bag")
        assert entry["verdict"] == "adopt"
        assert entry["evidence"]["sites"] == 5
        assert entry["evidence"]["violations"] == 0

    def test_analysis_no_candidates_is_insufficient_not_adopt(
            self, seeded_store, ts_repo):
        # no interfaces at all: "no violations" must not read as adoption
        entry = _by_id(recommend_rules(seeded_store, ts_repo),
                       "ts-no-optional-variant-bag")
        assert entry["verdict"] == "insufficient_evidence"
        assert entry["evidence"]["sites"] == 0

    def test_record_key_candidates_counted(self, seeded_store, tmp_path):
        for i in range(5):
            _write(tmp_path, f"open{i}.ts",
                   f"const cache{i}: Record<string, number> = {{}};\n")
        _write(tmp_path, "closed.ts",
               "const palette: Record<string, string> = { red: '#f00', blue: '#00f' };\n")
        entry = _by_id(recommend_rules(seeded_store, tmp_path),
                       "ts-no-wide-record-key")
        assert entry["evidence"]["sites"] == 6
        assert entry["evidence"]["violations"] == 1
        assert entry["verdict"] == "fix_first"


class TestApplicabilitySearch:
    """search(code=...) recommends rules by the structure they govern —
    complex analysis rules surface from their preconditions alone."""

    def test_complex_rule_recommended_before_any_violation(self, seeded_store):
        # three variant-bag candidates, zero violations, zero keywords
        code = "".join(VARIANT_CANDIDATE.format(n=i) for i in range(3))
        hits = seeded_store.search(language="typescript", code=code, kind="rule")
        top = [h.convention.id for h in hits[:3]]
        assert "ts-no-optional-variant-bag" in top
        hit = next(h for h in hits
                   if h.convention.id == "ts-no-optional-variant-bag")
        assert "rule_applicability=1.000" in hit.reason

    def test_record_rule_surfaces_from_annotation_shape(self, seeded_store):
        code = "const palette: Record<string, string> = { red: '#f00' };\n"
        hits = seeded_store.search(language="typescript", code=code, kind="rule")
        ids = [h.convention.id for h in hits]
        # governs this code -> must outrank rules that govern nothing here
        assert ids.index("ts-no-wide-record-key") < ids.index("ts-no-var")
        assert ids.index("ts-no-wide-record-key") < ids.index("ts-no-console-log")

    def test_inapplicable_rules_score_zero_on_applies(self, seeded_store):
        code = "const n: number = 1;\n"
        hits = seeded_store.search(language="typescript", code=code, kind="rule")
        hit = next(h for h in hits if h.convention.id == "ts-func-camel-case")
        assert "rule_applicability=0.000" in hit.reason

    def test_patterns_do_not_carry_the_applies_signal(self, seeded_store):
        code = "const palette: Record<string, string> = { red: '#f00' };\n"
        hits = seeded_store.search(language="typescript", code=code, kind="pattern")
        assert hits and all("rule_applicability" not in h.reason for h in hits)


class TestDeterminismAndSurfaces:
    def test_report_is_reproducible(self, seeded_store, ts_repo):
        _write(ts_repo, "src/legacy.ts", "var x = 1;\nenum E { A }\n")
        _write(ts_repo, "src/opts.ts", VARIANT_CANDIDATE.format(n=9))
        first = recommend_rules(seeded_store, ts_repo)
        second = recommend_rules(seeded_store, ts_repo)
        assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)

    def test_missing_root_raises(self, store, tmp_path):
        with pytest.raises(FileNotFoundError):
            recommend_rules(store, tmp_path / "nope")

    def test_language_filter(self, seeded_store, tmp_path):
        _write(tmp_path, "a.py", "def do_work():\n    pass\n")
        _write(tmp_path, "b.ts", "function doWork() { return 1; }\n")
        report = recommend_rules(seeded_store, tmp_path, language="python")
        assert report["languages"] == {"python": 1}
        assert all(r["language"] == "python" for r in report["catalog"])

    def test_single_file_root(self, seeded_store, tmp_path):
        _write(tmp_path, "one.ts", VARIANT_VIOLATION)
        report = recommend_rules(seeded_store, tmp_path / "one.ts")
        assert report["files"] == 1
        entry = _by_id(report, "ts-no-optional-variant-bag")
        assert entry["evidence"]["sites"] == 1
        assert entry["evidence"]["violations"] == 1

    def test_cli_recommend(self, tmp_path, capsys):
        from acode.cli import main

        db = tmp_path / "conv.db"
        repo_root = Path(__file__).resolve().parent.parent
        assert main(["--db", str(db), "import",
                     str(repo_root / "conventions" / "typescript.json")]) == 0
        src = tmp_path / "src"
        for i in range(6):
            _write(src, f"m{i}.ts", f"function getItem{i}() {{ return {i}; }}\n")
        capsys.readouterr()
        assert main(["--db", str(db), "recommend", str(src)]) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["files"] == 6
        assert "proposals" not in report
        assert any(r["id"] == "ts-func-camel-case" and r["verdict"] == "adopt"
                   for r in report["catalog"])


class TestMcpTool:
    async def test_recommend_rules_tool(self, seeded_store, tmp_path):
        from acode.config import AcodeConfig
        from acode.mcpserver.server import build_server

        config = AcodeConfig()
        config.db_path = ":memory:"
        server = build_server(config, seeded_store)
        tools = {t.name for t in await server.list_tools()}
        assert "recommend_rules" in tools

        for i in range(5):
            _write(tmp_path, f"opts{i}.ts", VARIANT_CANDIDATE.format(n=i))
        result, _ = await server.call_tool(
            "recommend_rules", {"path": str(tmp_path)})
        report = json.loads(result[0].text)
        assert report["files"] == 5
        analysis = next(r for r in report["catalog"]
                        if r["id"] == "ts-no-optional-variant-bag")
        assert analysis["verdict"] == "adopt"
        assert analysis["evidence"]["sites"] == 5
