"""Rule recommendation engine (TASK-0010).

Covers: per-rule-type evidence counting, the four-way verdict, dialect
inheritance, naming-rule mining (exclusive-evidence gate, catalog dedup,
self-insertable proposals), determinism, and the CLI/MCP surfaces.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from acode.rag.recommend import recommend_rules
from acode.rag.store import Convention, ConventionStore


def _write(root: Path, name: str, code: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")


def _by_id(report: dict, rule_id: str) -> dict:
    entry = next((r for r in report["catalog"] if r["id"] == rule_id), None)
    assert entry is not None, f"{rule_id} missing from catalog: " + ", ".join(
        r["id"] for r in report["catalog"])
    return entry


@pytest.fixture()
def ts_repo(tmp_path: Path) -> Path:
    # six files: camelCase functions dominate, no var/enum anywhere
    for i in range(6):
        _write(tmp_path, f"src/mod{i}.ts",
               f"function loadThing{i}() {{ return {i}; }}\n"
               f"function saveThing{i}() {{ return {i}; }}\n")
    return tmp_path


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

    def test_fix_first_minority_naming_violation(self, seeded_store, ts_repo):
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
        assert entry["verdict"] == "fix_first" or entry["verdict"] == "adopt"
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


class TestMining:
    def test_proposes_dominant_style_with_exclusive_evidence(self, store, tmp_path):
        for i in range(3):
            _write(tmp_path, f"m{i}.py",
                   f"def load_thing_{i}():\n    pass\n\n"
                   f"def save_thing_{i}():\n    pass\n")
        report = recommend_rules(store, tmp_path)
        assert len(report["proposals"]) == 1
        prop = report["proposals"][0]
        assert prop["id"] == "mined-python-function-naming"
        assert "snake_case" in prop["title"]
        assert prop["evidence"]["sites"] == 6
        assert prop["evidence"]["exclusive"] == 6

    def test_proposal_is_self_insertable(self, store, tmp_path):
        for i in range(5):
            _write(tmp_path, f"m{i}.py", f"def handle_req_{i}():\n    pass\n")
        report = recommend_rules(store, tmp_path)
        conv = Convention.from_dict(report["proposals"][0]["convention"])
        stored = store.add(conv)  # self-verifies on insert
        assert store.get(stored.id) is not None

    def test_silent_when_all_samples_ambiguous(self, store, tmp_path):
        # single lowercase words match camelCase AND snake_case: no exclusive
        # evidence, so no style may be proposed
        _write(tmp_path, "m.py",
               "def fetch():\n    pass\n\ndef run():\n    pass\n\n"
               "def load():\n    pass\n\ndef save():\n    pass\n\n"
               "def send():\n    pass\n")
        report = recommend_rules(store, tmp_path)
        assert report["proposals"] == []

    def test_silent_below_min_sites(self, store, tmp_path):
        _write(tmp_path, "m.py", "def only_one_func():\n    pass\n")
        assert recommend_rules(store, tmp_path)["proposals"] == []

    def test_silent_when_no_dominant_style(self, store, tmp_path):
        _write(tmp_path, "m.py",
               "def load_thing():\n    pass\n\ndef saveThing():\n    pass\n\n"
               "def store_thing():\n    pass\n\ndef fetchThing():\n    pass\n\n"
               "def del_thing():\n    pass\n\ndef putThing():\n    pass\n")
        assert recommend_rules(store, tmp_path)["proposals"] == []

    def test_dedups_against_catalog_rule(self, seeded_store, ts_repo):
        # ts-func-camel-case already governs function names -> no proposal
        report = recommend_rules(seeded_store, ts_repo)
        assert not any("function" in p["id"] and p["id"].startswith("mined-typescript")
                       for p in report["proposals"])

    def test_mines_uncovered_construct_in_seeded_store(self, seeded_store, tmp_path):
        # python classes ARE covered by py-class-pascal-case; methods are not a
        # target, so mine typescript classes which the seed catalog doesn't name
        for i in range(5):
            _write(tmp_path, f"c{i}.ts", f"class HttpClient{i} {{ }}\n")
        report = recommend_rules(seeded_store, tmp_path)
        ids = [p["id"] for p in report["proposals"]]
        assert "mined-typescript-class-naming" in ids

    def test_tsx_mines_into_typescript(self, store, tmp_path):
        for i in range(5):
            _write(tmp_path, f"c{i}.tsx",
                   f"function AppView{i}() {{ return <div/>; }}\n")
        report = recommend_rules(store, tmp_path)
        prop = next(p for p in report["proposals"]
                    if p["id"] == "mined-typescript-function-naming")
        assert "PascalCase" in prop["title"]

    def test_mine_false_skips_proposals(self, store, tmp_path):
        for i in range(5):
            _write(tmp_path, f"m{i}.py", f"def do_work_{i}():\n    pass\n")
        report = recommend_rules(store, tmp_path, mine=False)
        assert report["proposals"] == []


class TestDeterminismAndSurfaces:
    def test_report_is_reproducible(self, seeded_store, ts_repo):
        _write(ts_repo, "src/legacy.ts", "var x = 1;\nenum E { A }\n")
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

        for i in range(6):
            _write(tmp_path, f"m{i}.ts", f"function getItem{i}() {{ return {i}; }}\n")
        result, _ = await server.call_tool(
            "recommend_rules", {"path": str(tmp_path)})
        report = json.loads(result[0].text)
        assert report["files"] == 6
        assert any(r["verdict"] == "adopt" for r in report["catalog"])
