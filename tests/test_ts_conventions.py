"""New TypeScript ruleset (enum -> as const, non-null, type alias naming)
and the pipeline step trace that makes generation/repair observable."""

import logging
from pathlib import Path

from acode.agent import steps
from acode.agent.pipeline import CodingPipeline
from acode.config import AcodeConfig

REPO_ROOT = Path(__file__).resolve().parent.parent

ENUM_CODE = (
    "export enum OrderStatus {\n"
    '  Pending = "PENDING",\n'
    '  Shipped = "SHIPPED",\n'
    "}\n\n"
    "export function isShipped(status: OrderStatus): boolean {\n"
    "  return status === OrderStatus.Shipped;\n"
    "}\n"
)

AS_CONST_CODE = (
    "export const OrderStatus = {\n"
    '  Pending: "PENDING",\n'
    '  Shipped: "SHIPPED",\n'
    "} as const;\n\n"
    "export type OrderStatus = typeof OrderStatus[keyof typeof OrderStatus];\n\n"
    "export function isShipped(status: OrderStatus): boolean {\n"
    "  return status === OrderStatus.Shipped;\n"
    "}\n"
)


def _ts_rules(seeded_store):
    return steps.applicable_rules(seeded_store, "typescript", None)


class TestNewTsRules:
    def test_seed_file_self_verifies_on_import(self, store):
        added = store.import_file(REPO_ROOT / "conventions" / "typescript.json")
        assert {
            "ts-no-enum",
            "ts-pattern-const-object-enum",
            "ts-no-non-null-assertion",
            "ts-type-alias-pascal-case",
        } <= set(added)

    def test_enum_is_flagged(self, seeded_store):
        report = steps.check(ENUM_CODE, "typescript", _ts_rules(seeded_store))
        assert not report.passed
        assert "ts-no-enum" in {v.rule_id for v in report.violations}

    def test_const_enum_is_flagged(self, seeded_store):
        code = "const enum Direction {\n  Up,\n  Down,\n}\n"
        report = steps.check(code, "typescript", _ts_rules(seeded_store))
        assert "ts-no-enum" in {v.rule_id for v in report.violations}

    def test_as_const_replacement_passes_all_rules(self, seeded_store):
        report = steps.check(AS_CONST_CODE, "typescript", _ts_rules(seeded_store))
        assert report.passed
        assert "ts-no-enum" in report.checked_rules

    def test_non_null_assertion_is_flagged(self, seeded_store):
        code = 'const el = document.getElementById("app")!;\n'
        report = steps.check(code, "typescript", _ts_rules(seeded_store))
        assert "ts-no-non-null-assertion" in {v.rule_id for v in report.violations}

    def test_snake_case_type_alias_is_flagged(self, seeded_store):
        code = "type user_id = string;\n"
        report = steps.check(code, "typescript", _ts_rules(seeded_store))
        assert "ts-type-alias-pascal-case" in {v.rule_id for v in report.violations}

    def test_no_enum_retrievable_by_text_query(self, seeded_store):
        hits = seeded_store.search(
            language="typescript", query="enum as const object type", top_k=5)
        assert hits
        assert hits[0].convention.id in ("ts-no-enum", "ts-pattern-const-object-enum")


class TestGenerationTrace:
    async def test_enum_repair_is_fully_traced(
        self, seeded_store, fake_provider_factory, caplog
    ):
        provider = fake_provider_factory([
            f"Here you go.\n```typescript\n{ENUM_CODE}```",
            f"Fixed.\n```typescript\n{AS_CONST_CODE}```",
        ])
        config = AcodeConfig()
        config.max_repairs = 3
        with caplog.at_level(logging.INFO, logger="acode.pipeline"):
            result = await CodingPipeline(seeded_store, provider, config).generate(
                "order status handling with a finite status set", "typescript")

        assert result.verified
        assert result.iterations == 1

        stages = [e["stage"] for e in result.trace]
        assert stages[0] == "retrieve"
        assert stages[1] == "rules"
        assert "verify#0" in stages
        assert "repair#1" in stages
        assert "verify#1" in stages
        assert stages[-1] == "done"

        # first verdict shows the enum violation, second is clean
        v0 = next(e for e in result.trace if e["stage"] == "verify#0")
        assert not v0["report"]["passed"]
        assert "ts-no-enum" in v0["message"]
        v1 = next(e for e in result.trace if e["stage"] == "verify#1")
        assert v1["report"]["passed"]

        # code snapshots show the evolution enum -> as const
        snapshots = [e["code"] for e in result.trace if e["stage"] == "synthesize"]
        assert "enum OrderStatus" in snapshots[0]
        assert "as const" in snapshots[1]

        # the repair prompt carried the mechanical verdict
        repair_prompt = provider.calls[1][1]
        assert "ts-no-enum" in repair_prompt

        # live log output mirrors the trace
        messages = [r.getMessage() for r in caplog.records]
        assert any(m.startswith("[verify#0] FAIL") for m in messages)
        assert any(m.startswith("[verify#1] PASS") for m in messages)

    async def test_trace_serialized_in_result_dict(
        self, seeded_store, fake_provider_factory
    ):
        provider = fake_provider_factory([f"```typescript\n{AS_CONST_CODE}```"])
        result = await CodingPipeline(seeded_store, provider, AcodeConfig()).generate(
            "order status handling", "typescript")
        payload = result.to_dict()
        assert payload["trace"]
        assert payload["trace"][-1]["stage"] == "done"


class TestReviewTrace:
    async def test_review_stages_are_traced(self, seeded_store, fake_provider_factory):
        provider = fake_provider_factory([
            f"The enum violates ts-no-enum.\n```typescript\n{AS_CONST_CODE}```",
        ])
        result = await CodingPipeline(seeded_store, provider, AcodeConfig()).review(
            ENUM_CODE, "typescript")

        stages = [e["stage"] for e in result.trace]
        assert stages == [
            "retrieve", "rules", "verify-input", "synthesize", "verify-fix", "done",
        ]
        verdict = next(e for e in result.trace if e["stage"] == "verify-input")
        assert "ts-no-enum" in verdict["message"]
        assert result.fix_verified
        assert result.to_dict()["trace"][-1]["message"].endswith("fix_verified=True")
